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
from pathlib import Path
from typing import Dict, Tuple

import yaml

from .image_pipeline import ImagePipeline
from .inference import get_inference_service
from .position_utils import calculate_total as _calculate_total

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
    for image_id, (roi_bytes, model_type) in rois.items():
        try:
            result = inference.predict_from_bytes(model_type, roi_bytes)
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

    # Step 7: Calculate total
    total, raw_values = _calculate_total(config, predictions)

    print(f"Meter reading: {total:.4f} m³  (digits={raw_values['digits']}, arrows={raw_values['arrows']})")
    return 0


def main(config_path: str) -> int:
    """Synchronous entry point — runs the async pipeline via asyncio.run()."""
    return asyncio.run(run_one_shot(config_path))
