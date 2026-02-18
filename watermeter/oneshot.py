"""
One-shot meter reading mode.

Performs a single meter reading pipeline:
  1. Load config
  2. Initialize inference service
  3. Fetch whole image
  4. Extract ROIs
  5. Run inference on each ROI
  6. Calculate total reading
  7. Print result and exit

Entry points:
  run_one_shot(config_path: str) -> int   (async coroutine)
  main(config_path: str)                  (sync wrapper)
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

from .image_pipeline import ImagePipeline
from .inference import get_inference_service
from .position_utils import get_position_ids

logger = logging.getLogger(__name__)


async def run_one_shot(config_path: str) -> int:
    """
    Perform a single meter reading pipeline.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        0 on success, 1 on any failure.
    """
    # Step 1: Load config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load config from {config_path!r}: {e}")
        return 1

    # Step 2: Initialize inference service
    inference = get_inference_service()
    try:
        inference.initialize(config)
    except Exception as e:
        logger.error(f"Failed to initialize inference service: {e}")
        return 1

    # Step 3: Check models loaded
    if not inference.models_loaded:
        logger.error("Inference models failed to load — aborting one-shot run")
        return 1

    # Step 4: Fetch whole image
    pipeline = ImagePipeline(config)
    try:
        image_bytes = await pipeline.fetch_whole_image()
    except Exception as e:
        logger.error(f"Exception fetching image: {e}")
        return 1

    if image_bytes is None:
        logger.error("fetch_whole_image() returned None — HTTP failure")
        return 1

    # Step 5: Extract ROIs
    try:
        rois: Dict[str, Tuple[bytes, str]] = pipeline.process_whole_image(image_bytes)
    except Exception as e:
        logger.error(f"Exception processing image: {e}")
        return 1

    if not rois:
        logger.error("No ROIs extracted from image — cannot run inference")
        return 1

    # Step 6: Run inference on each ROI
    predictions: Dict[str, Dict] = {}
    temp_dir = Path(tempfile.mkdtemp())
    try:
        for image_id, (roi_bytes, model_type) in rois.items():
            temp_path = temp_dir / f"{image_id}.jpg"
            temp_path.write_bytes(roi_bytes)
            try:
                result = inference.predict(model_type, str(temp_path))
                predictions[image_id] = {
                    "id": image_id,
                    "class": result["class"],
                    "confidence": result["confidence"],
                    "model": model_type,
                    "image_bytes": roi_bytes,
                }
                logger.debug(f"{image_id}: {result['class']} ({result['confidence']:.3f})")
            except Exception as e:
                logger.error(f"Inference failed for {image_id}: {e}")
                predictions[image_id] = {
                    "id": image_id,
                    "class": "ERROR",
                    "confidence": 0.0,
                    "model": model_type,
                    "image_bytes": roi_bytes,
                    "error": str(e),
                }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Step 7: Calculate total
    total, raw_values = _calculate_total(config, predictions)

    print(f"Meter reading: {total:.4f} m³  (digits={raw_values['digits']}, arrows={raw_values['arrows']})")
    return 0


def _calculate_total(config: dict, predictions: Dict[str, Dict]) -> Tuple[float, Dict]:
    """
    Calculate total meter reading from predictions.

    Replicates the logic from WatermeterService.calculate_total(), operating
    directly on a config dict and predictions dict without constructing a service.

    Returns:
        (total_value, {"digits": list[int], "arrows": list[float]})
    """
    digit_ids, arrow_ids = get_position_ids(config)

    digits = []
    arrows = []

    for image_id in digit_ids:
        if image_id in predictions:
            pred = predictions[image_id]
            if pred["class"] != "NAN" and pred["class"] != "ERROR":
                digits.append(int(pred["class"]))
            else:
                logger.warning(f"{image_id} has invalid class: {pred['class']}")
                digits.append(0)

    for image_id in arrow_ids:
        if image_id in predictions:
            pred = predictions[image_id]
            if pred["class"] != "ERROR":
                arrows.append(float(pred["class"]))
            else:
                logger.warning(f"{image_id} has error")
                arrows.append(0.0)

    total = 0.0

    # Digits contribution: first digit has highest place value
    for i, digit in enumerate(digits):
        multiplier = 10 ** (len(digits) - 1 - i)
        total += digit * multiplier

    # Arrows contribution: 0.1, 0.01, 0.001, ...
    for i, arrow in enumerate(arrows):
        multiplier = 10 ** (-(i + 1))
        total += int(arrow) * multiplier

    raw_values = {"digits": digits, "arrows": arrows}
    logger.info(f"Calculated total: {total:.4f} m³")
    return total, raw_values


def main(config_path: str) -> int:
    """Synchronous entry point — runs the async pipeline via asyncio.run()."""
    return asyncio.run(run_one_shot(config_path))
