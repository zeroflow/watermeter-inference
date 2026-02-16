"""
Low Confidence Image Capture
Saves low-confidence predictions for later manual labeling and training.
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

from .image_hash import compute_dhash, HashCache

logger = logging.getLogger(__name__)


class LowConfidenceCapture:
    """Handles saving low-confidence images for manual review and training."""

    def __init__(self, config: dict):
        self.config = config
        self.last_save_times: Dict[str, float] = {}

    async def save_low_confidence(
        self, image_id: str, image_bytes: bytes, prediction: Dict, next_image_bytes: bytes = None
    ) -> None:
        """
        Save low confidence images for later training.

        Args:
            image_id: Image identifier
            image_bytes: Image data
            prediction: Prediction result
            next_image_bytes: Optional image of the next smaller dial (for annotation help)
        """
        config = self.config["low_confidence"]

        if not config["save_enabled"]:
            return

        # Check rate limiting
        now = time.time()
        last_save = self.last_save_times.get(image_id, 0)
        if now - last_save < config["save_rate_limit"]:
            logger.debug(f"Rate limit: skipping save for {image_id}")
            return

        # Generate timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        # Determine save path - save to input folder for manual review
        image_class = prediction["model"]
        base_path = Path(config["save_path"])
        save_dir = base_path / image_class / "input"
        save_dir.mkdir(parents=True, exist_ok=True)

        # Deduplication check
        new_hash = None
        input_cache = None
        dedup_enabled = config.get("dedup_enabled", True)
        if dedup_enabled:
            new_hash = compute_dhash(image_bytes)
            if new_hash is not None:
                threshold = config.get("dedup_threshold", 10)

                # Check against input folder
                input_cache = HashCache(save_dir)
                dup = input_cache.find_near_duplicate(new_hash, threshold)
                if dup:
                    logger.debug(f"Dedup: skipping {image_id}, near-duplicate of {dup} in input")
                    return

                # Check against ground truth if configured
                scope = config.get("dedup_scope", "input+ground_truth")
                if scope == "input+ground_truth":
                    gt_base = base_path / image_class / "ground_truth"
                    if gt_base.is_dir():
                        for class_dir in gt_base.iterdir():
                            if class_dir.is_dir():
                                gt_cache = HashCache(class_dir)
                                dup = gt_cache.find_near_duplicate(new_hash, threshold)
                                if dup:
                                    logger.debug(
                                        f"Dedup: skipping {image_id}, near-duplicate of "
                                        f"{dup} in ground_truth/{class_dir.name}"
                                    )
                                    return

        save_path = save_dir / f"{image_id}_{timestamp}.jpg"

        # Save image
        save_path.write_bytes(image_bytes)
        logger.info(f"Saved low confidence image: {save_path}")

        # Update input hash cache
        if dedup_enabled and new_hash is not None:
            try:
                if input_cache is None:
                    input_cache = HashCache(save_dir)
                input_cache.add(save_path.name, new_hash)
            except Exception as e:
                logger.warning(f"Failed to update hash cache: {e}")

        # Save next image if provided (helps with annotation)
        if next_image_bytes:
            next_save_path = save_dir / f"{image_id}_{timestamp}_next.jpg"
            next_save_path.write_bytes(next_image_bytes)
            logger.info(f"Saved next dial reference image: {next_save_path}")

        # Update rate limit
        self.last_save_times[image_id] = now
