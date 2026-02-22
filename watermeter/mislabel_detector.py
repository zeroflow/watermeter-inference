"""Mislabel detection and confirmation for ground truth training data."""

import base64
import logging
import shutil
from pathlib import Path

from .inference import get_inference_service
from .training_core import circular_error

logger = logging.getLogger(__name__)


def _make_thumbnail_base64(image_path: Path, max_size: int = 128) -> str:
    """Read an image file and return a base64-encoded representation of its raw bytes.

    Returns an empty string if the file cannot be read.
    """
    try:
        raw = image_path.read_bytes()
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


def scan_mislabeled(model_type: str, training_path: Path) -> dict:
    """Run the active model on all ground truth images and collect suspects.

    A *suspect* is an image whose model prediction disagrees with its
    folder label.

    For digits (classification): prediction must exactly match folder name.
    For arrows with a regression model: prediction must be within 0.5 dial
    positions of the folder label to be considered correct.
    For arrows with a classification model: prediction must exactly match
    the folder label (after rounding to the model's class set).

    Returns a dict with keys:
        suspects  -- list of suspect dicts
        total_scanned -- number of images processed
        total_suspects -- len(suspects)
    """
    gt_path = training_path / model_type / "ground_truth"
    if not gt_path.exists():
        return {"suspects": [], "total_scanned": 0, "total_suspects": 0}

    inference_svc = get_inference_service()
    classifier = inference_svc.get_classifier(model_type)
    if classifier is None:
        raise RuntimeError(f"No active {model_type} model loaded")

    # Detect whether this is a regression model
    is_regression = hasattr(classifier, "predict") and not hasattr(classifier, "classes")

    suspects = []
    total_scanned = 0

    for class_dir in sorted(gt_path.iterdir()):
        if not class_dir.is_dir():
            continue

        folder_label = class_dir.name

        for img_path in sorted(class_dir.glob("*.jpg")):
            total_scanned += 1

            try:
                prediction = classifier.predict(str(img_path))
            except Exception as e:
                logger.warning(f"Mislabel scan: failed to predict {img_path}: {e}")
                continue

            predicted_label = prediction["class"]
            confidence = prediction["confidence"]

            # Determine if prediction matches folder label
            if is_regression:
                # Regression model: compare as floats with tolerance
                try:
                    folder_val = float(folder_label)
                    pred_val = float(predicted_label)
                    is_match = circular_error(pred_val, folder_val) < 0.5
                except (ValueError, TypeError):
                    is_match = predicted_label == folder_label
            else:
                # Classification model: exact string match
                is_match = predicted_label == folder_label

            if not is_match:
                thumbnail_b64 = _make_thumbnail_base64(img_path)
                suspects.append(
                    {
                        "path": str(img_path),
                        "filename": img_path.name,
                        "current_label": folder_label,
                        "predicted_label": predicted_label,
                        "confidence": round(confidence, 4),
                        "image_base64": thumbnail_b64,
                    }
                )

    return {
        "suspects": suspects,
        "total_scanned": total_scanned,
        "total_suspects": len(suspects),
    }


def confirm_mislabeled(model_type: str, training_path: Path, selected_paths: list) -> dict:
    """Move selected suspect images from ground_truth back to input for relabeling.

    Each moved file is renamed to encode a label hint:
        {original_stem}_label={folder_label}.jpg

    Args:
        model_type: "digits" or "arrows"
        training_path: Base training data directory
        selected_paths: List of absolute image paths to move

    Returns:
        dict with moved_count, error_count, errors (list of error messages)
    """
    gt_dir = training_path / model_type / "ground_truth"
    input_dir = training_path / model_type / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    errors = []

    for path_str in selected_paths:
        src = Path(path_str)
        # Validate path stays within ground truth directory
        try:
            resolved = src.resolve()
            if not resolved.is_relative_to(gt_dir.resolve()):
                errors.append(f"Path outside ground truth: {path_str}")
                continue
        except (ValueError, OSError):
            errors.append(f"Invalid path: {path_str}")
            continue
        if not src.exists():
            errors.append(f"File not found: {path_str}")
            continue

        # Extract the class label from the parent folder name
        folder_label = src.parent.name

        # Build new filename with label hint
        new_name = f"{src.stem}_label={folder_label}{src.suffix}"
        dest = input_dir / new_name

        # Avoid overwriting existing files
        if dest.exists():
            counter = 1
            while dest.exists():
                new_name = f"{src.stem}_label={folder_label}_{counter}{src.suffix}"
                dest = input_dir / new_name
                counter += 1

        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except Exception as e:
            errors.append(f"Failed to move {path_str}: {e}")

    return {
        "moved_count": moved,
        "error_count": len(errors),
        "errors": errors,
    }
