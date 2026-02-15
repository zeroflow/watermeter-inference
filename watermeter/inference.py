import openvino as ov
import cv2
import numpy as np
from pathlib import Path
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import tempfile
import httpx
import yaml
import re
import logging
import threading

logger = logging.getLogger(__name__)


class Classifier:
    def __init__(self, model_path, classes, resolution, label_config_tag, device="GPU"):
        core = ov.Core()
        model = core.read_model(model_path)
        self.compiled = core.compile_model(model, device)
        self.classes = classes
        self.resolution = resolution
        self.label_config_tag = label_config_tag
        self.model_path = model_path

    def preprocess(self, image_path):
        img = cv2.imread(str(image_path))
        if img is None:
            raise ValueError(f"Failed to read image: {image_path}")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self.resolution, self.resolution))
        img = img.astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return img.transpose(2, 0, 1)[np.newaxis, ...]

    def predict(self, image_path):
        img = self.preprocess(image_path)
        result = self.compiled([img])[self.compiled.output(0)][0]
        # Numerically stable softmax (subtract max to prevent overflow)
        result = result - result.max()
        probs = np.exp(result) / np.exp(result).sum()
        idx = probs.argmax()
        return {"class": self.classes[idx], "confidence": float(probs[idx])}

    def predict_detailed(self, image_path, top_k=3):
        """Return top-K predictions with softmax probabilities."""
        img = self.preprocess(image_path)
        result = self.compiled([img])[self.compiled.output(0)][0]
        result = result - result.max()
        probs = np.exp(result) / np.exp(result).sum()

        k = min(top_k, len(self.classes))
        top_indices = probs.argsort()[::-1][:k]

        return [{"class": self.classes[idx], "confidence": float(probs[idx])} for idx in top_indices]


class Regressor:
    """
    Regression-based inference for continuous arrow models.

    Outputs a dial position (0.0-9.9) instead of a class label.
    """

    def __init__(self, model_path, resolution, label_config_tag, device="GPU"):
        core = ov.Core()
        model = core.read_model(model_path)
        self.compiled = core.compile_model(model, device)
        self.resolution = resolution
        self.label_config_tag = label_config_tag
        self.model_path = model_path

    def preprocess(self, image_path):
        """Same preprocessing as Classifier."""
        img = cv2.imread(str(image_path))
        if img is None:
            raise ValueError(f"Failed to read image: {image_path}")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self.resolution, self.resolution))
        img = img.astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return img.transpose(2, 0, 1)[np.newaxis, ...]

    def predict(self, image_path):
        """
        Run regression inference.

        Returns:
            dict with 'class' (string like "3.7") and 'confidence' (float 0.0-1.0)
        """
        img = self.preprocess(image_path)
        raw_output = self.compiled([img])[self.compiled.output(0)][0]

        # Apply sigmoid to get [0, 1], then scale to [0, 10)
        sigmoid_val = 1.0 / (1.0 + np.exp(-float(raw_output[0])))
        dial_position = sigmoid_val * 10.0

        # Clamp to valid range
        dial_position = max(0.0, min(9.9, dial_position))

        # Format as class string (same format as ground truth folder names)
        class_str = f"{dial_position:.1f}"

        # Confidence heuristic: how certain the model is.
        # Use distance from 0.5 sigmoid midpoint as confidence proxy.
        # Values near sigmoid midpoint (0.5) are uncertain; values near 0 or 1 are confident.
        # Map: |sigmoid - 0.5| * 2 gives 0.0 (uncertain) to 1.0 (confident).
        confidence = abs(sigmoid_val - 0.5) * 2.0

        return {"class": class_str, "confidence": float(confidence)}

    def predict_detailed(self, image_path, top_k=3):
        """
        For regression, return a single prediction (no top-K concept).

        Returns list with one entry for API compatibility with Classifier.predict_detailed().
        """
        result = self.predict(image_path)
        return [result]


def _detect_training_mode(model_path: str) -> str:
    """
    Detect whether a model is discrete (classification) or continuous (regression).

    Checks metadata.json in the model directory first, falls back to filename pattern.

    Returns:
        "discrete" or "continuous"
    """
    import json as _json

    model_dir = Path(model_path).parent
    metadata_file = model_dir / "metadata.json"

    if metadata_file.exists():
        try:
            with open(metadata_file, "r") as f:
                metadata = _json.load(f)
            return metadata.get("training_mode", "discrete")
        except Exception:
            pass

    # Fallback: check filename pattern
    filename = Path(model_path).stem
    if "_continuous_" in filename:
        return "continuous"

    return "discrete"


class InferenceService:
    """Thread-safe inference service with hot-reload support."""

    def __init__(self):
        self._lock = threading.RLock()
        self._digits_classifier = None
        self._arrows_classifier = None
        self._reloading = False

    def initialize(self, config: dict):
        """Initialize classifiers from config."""
        with self._lock:
            inference_config = config["inference"]

            # --- Digits (always classification) ---
            validate_model_config(
                inference_config["digits_model"],
                "digits",
                inference_config["digits_classes"],
                inference_config["digits_resolution"],
            )
            self._digits_classifier = Classifier(
                inference_config["digits_model"],
                inference_config["digits_classes"],
                inference_config["digits_resolution"],
                "digit",
                device=inference_config.get("device", "GPU"),
            )

            # --- Arrows (classification or regression) ---
            arrows_mode = _detect_training_mode(inference_config["arrows_model"])

            if arrows_mode == "continuous":
                # Regression model — no class list validation needed
                self._arrows_classifier = Regressor(
                    inference_config["arrows_model"],
                    inference_config["arrows_resolution"],
                    "arrow_value",
                    device=inference_config.get("device", "GPU"),
                )
                logger.info("Arrows model initialized in REGRESSION mode")
            else:
                validate_model_config(
                    inference_config["arrows_model"],
                    "arrows",
                    inference_config["arrows_classes"],
                    inference_config["arrows_resolution"],
                )
                self._arrows_classifier = Classifier(
                    inference_config["arrows_model"],
                    inference_config["arrows_classes"],
                    inference_config["arrows_resolution"],
                    "arrow_value",
                    device=inference_config.get("device", "GPU"),
                )
                logger.info("Arrows model initialized in CLASSIFICATION mode")

            logger.info("Inference service initialized")

    def reload_models(self, config: dict):
        """
        Hot-reload models from updated config.
        Blocks inference during reload.
        """
        with self._lock:
            self._reloading = True
            try:
                logger.info("Starting model hot-reload...")

                inference_config = config["inference"]

                # --- Digits (always classification) ---
                validate_model_config(
                    inference_config["digits_model"],
                    "digits",
                    inference_config["digits_classes"],
                    inference_config["digits_resolution"],
                )
                new_digits = Classifier(
                    inference_config["digits_model"],
                    inference_config["digits_classes"],
                    inference_config["digits_resolution"],
                    "digit",
                    device=inference_config.get("device", "GPU"),
                )

                # --- Arrows (classification or regression) ---
                arrows_mode = _detect_training_mode(inference_config["arrows_model"])

                if arrows_mode == "continuous":
                    new_arrows = Regressor(
                        inference_config["arrows_model"],
                        inference_config["arrows_resolution"],
                        "arrow_value",
                        device=inference_config.get("device", "GPU"),
                    )
                    logger.info("Arrows model reloaded in REGRESSION mode")
                else:
                    validate_model_config(
                        inference_config["arrows_model"],
                        "arrows",
                        inference_config["arrows_classes"],
                        inference_config["arrows_resolution"],
                    )
                    new_arrows = Classifier(
                        inference_config["arrows_model"],
                        inference_config["arrows_classes"],
                        inference_config["arrows_resolution"],
                        "arrow_value",
                        device=inference_config.get("device", "GPU"),
                    )
                    logger.info("Arrows model reloaded in CLASSIFICATION mode")

                # Atomic swap
                self._digits_classifier = new_digits
                self._arrows_classifier = new_arrows

                logger.info("Model hot-reload completed successfully")
                return True

            except Exception as e:
                logger.error(f"Model hot-reload failed: {e}")
                raise
            finally:
                self._reloading = False

    def predict(self, model_type: str, image_path: str) -> dict:
        """
        Run inference. Waits if model is being reloaded.

        Args:
            model_type: "digits" or "arrows"
            image_path: Path to image file

        Returns:
            Prediction dict with 'class' and 'confidence'
        """
        with self._lock:
            if model_type == "digits":
                return self._digits_classifier.predict(image_path)
            elif model_type == "arrows":
                return self._arrows_classifier.predict(image_path)
            else:
                raise ValueError(f"Unknown model type: {model_type}")

    def predict_detailed(self, model_type: str, image_path: str, top_k: int = 3) -> list:
        """Run inference and return top-K predictions."""
        with self._lock:
            if model_type == "digits":
                return self._digits_classifier.predict_detailed(image_path, top_k)
            elif model_type == "arrows":
                return self._arrows_classifier.predict_detailed(image_path, top_k)
            else:
                raise ValueError(f"Unknown model type: {model_type}")

    def get_classifier(self, model_type: str) -> Classifier:
        """Get classifier (for backward compatibility)."""
        with self._lock:
            if model_type == "digits":
                return self._digits_classifier
            elif model_type == "arrows":
                return self._arrows_classifier
            else:
                raise ValueError(f"Unknown model type: {model_type}")

    def is_reloading(self) -> bool:
        """Check if models are currently being reloaded."""
        return self._reloading

    @property
    def digits_classifier(self) -> Classifier:
        """Get digits classifier."""
        return self._digits_classifier

    @property
    def arrows_classifier(self) -> Classifier:
        """Get arrows classifier."""
        return self._arrows_classifier


def validate_model_config(model_path: str, model_type: str, classes: list, resolution: int) -> None:
    """
    Validate model filename against config values.

    Model filename patterns:
      - arrows: model_arrows_<model>_c<num_classes>_r<resolution>
      - digits: model_digits_<model>_r<resolution>

    If filename conforms to pattern, validates that embedded values match config.
    Raises ValueError if mismatch found.
    """
    filename = Path(model_path).stem

    if model_type == "arrows":
        # Check continuous pattern first (with optional _s{seed} suffix)
        continuous_pattern = r"^model_arrows_(.+)_continuous_r(\d+)(?:_s\d+)?$"
        continuous_match = re.match(continuous_pattern, filename)
        if continuous_match:
            file_resolution = int(continuous_match.group(2))
            if file_resolution != resolution:
                msg = f"arrows resolution mismatch: filename has r{file_resolution}, config has {resolution}"
                logger.error(msg)
                raise ValueError(msg)
            logger.info(f"Arrows continuous model validated: {filename} (resolution={resolution})")
            return

        # Check discrete pattern: model_arrows_<model>_c<num_classes>_r<resolution> (with optional _s{seed} suffix)
        pattern = r"^model_arrows_(.+)_c(\d+)_r(\d+)(?:_s\d+)?$"
        match = re.match(pattern, filename)
        if match:
            file_num_classes = int(match.group(2))
            file_resolution = int(match.group(3))
            config_num_classes = len(classes)

            errors = []
            if file_num_classes != config_num_classes:
                errors.append(
                    f"arrows class count mismatch: filename has c{file_num_classes}, config has {config_num_classes} classes"
                )
            if file_resolution != resolution:
                errors.append(f"arrows resolution mismatch: filename has r{file_resolution}, config has {resolution}")

            if errors:
                for error in errors:
                    logger.error(error)
                raise ValueError("; ".join(errors))

            logger.info(f"Arrows model validated: {filename} (classes={config_num_classes}, resolution={resolution})")

    elif model_type == "digits":
        # Pattern: model_digits_<model>_r<resolution> (with optional _s{seed} suffix)
        pattern = r"^model_digits_(.+)_r(\d+)(?:_s\d+)?$"
        match = re.match(pattern, filename)
        if match:
            file_resolution = int(match.group(2))

            if file_resolution != resolution:
                msg = f"digits resolution mismatch: filename has r{file_resolution}, config has {resolution}"
                logger.error(msg)
                raise ValueError(msg)

            logger.info(f"Digits model validated: {filename} (resolution={resolution})")


# Load configuration and initialize service
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

app = FastAPI()

# Create global inference service
_inference_service = InferenceService()
_inference_service.initialize(config)


def get_inference_service() -> InferenceService:
    """Get the global InferenceService instance."""
    return _inference_service


classifiers = None  # Deprecated: use get_inference_service().predict() instead

# Label Studio ML Backend models


class SetupRequest(BaseModel):
    project: Optional[str] = None
    label_schema: Optional[str] = Field(None, alias="schema")
    hostname: Optional[str] = None
    access_token: Optional[str] = None


class PredictRequest(BaseModel):
    tasks: list
    project: Optional[str] = None
    label_config: Optional[str] = None
    params: Optional[dict] = None


async def download_image(url: str) -> str:
    """Download image from URL to temp file."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(response.content)
            return tmp.name


# Setup endpoints
@app.post("/predict/{model_type}/setup")
async def setup(model_type: str, request: SetupRequest):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)

    clf = classifiers[model_type]
    return {"model_version": f"{model_type}-v1", "labels": clf.classes}


# Predict endpoints for Label Studio
@app.post("/predict/{model_type}/predict")
async def predict_ls(model_type: str, request: PredictRequest):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)

    clf = classifiers[model_type]
    results = []

    for task in request.tasks:
        image_url = task.get("data", {}).get("image")
        if not image_url:
            results.append({"result": [], "score": 0.0})
            continue

        try:
            tmp_path = await download_image(image_url)
            pred = clf.predict(tmp_path)
            Path(tmp_path).unlink()

            # Different format for Number vs Choices
            if model_type == "arrows":
                result_item = {
                    "from_name": clf.label_config_tag,
                    "to_name": "image",
                    "type": "number",
                    "value": {"number": float(pred["class"])},
                }
            else:
                result_item = {
                    "from_name": clf.label_config_tag,
                    "to_name": "image",
                    "type": "choices",
                    "value": {"choices": [pred["class"]]},
                }

            results.append({"result": [result_item], "score": pred["confidence"]})
        except Exception as e:
            results.append({"result": [], "score": 0.0, "error": str(e)})

    return {"results": results}


# Health endpoints
@app.get("/health")
def health():
    return {"status": "ok", "models": list(classifiers.keys()), "device": "GPU"}


@app.get("/predict/{model_type}/health")
def model_health(model_type: str):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)
    return {"status": "ok", "model": model_type, "device": "GPU"}


# Direct prediction endpoint (your original)
@app.post("/predict/{model_type}")
async def predict_direct(model_type: str, file: UploadFile = File(...)):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    result = classifiers[model_type].predict(tmp_path)
    Path(tmp_path).unlink()

    return JSONResponse({**result, "model": model_type})
