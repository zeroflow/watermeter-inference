"""
Data collection module for gathering training images with quota enforcement.

Tracks per-class collection counts across model types and ROIs,
persists counters to disk, and enforces configurable quotas.
"""

import copy
import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from watermeter.image_hash import HashCache, compute_dhash

logger = logging.getLogger(__name__)


def _nested_defaultdict():
    """Create a three-level nested defaultdict: model_type -> roi_id -> class_name -> count."""
    return defaultdict(lambda: defaultdict(lambda: defaultdict(int)))


def _defaultdict_from_dict(data: dict) -> defaultdict:
    """Convert a regular nested dict into a three-level nested defaultdict."""
    result = _nested_defaultdict()
    for model_type, rois in data.items():
        for roi_id, classes in rois.items():
            for class_name, count in classes.items():
                result[model_type][roi_id][class_name] = count
    return result


def _defaultdict_to_dict(dd) -> dict:
    """Convert a nested defaultdict to regular dicts for JSON serialization."""
    if isinstance(dd, defaultdict):
        return {k: _defaultdict_to_dict(v) for k, v in dd.items()}
    return dd


class DataCollector:
    """Pure counter logic with quota enforcement for training data collection.

    Tracks how many images have been collected per (model_type, roi_id, class_name)
    and enforces a configurable quota per class. Counters are persisted to disk
    as JSON for survival across restarts.
    """

    def __init__(self, config: dict, save_path: str):
        """Initialize the DataCollector.

        Args:
            config: Configuration dict with keys:
                - enabled: Whether collection is active
                - quota_per_class: Max images to collect per class
                - dedup_enabled: Whether deduplication is enabled (used in Task 4)
                - dedup_threshold: Similarity threshold for dedup (used in Task 4)
                - counters_file: Filename for persisted counters
            save_path: Base directory for training data storage.
        """
        self._enabled = config.get("enabled", False)
        self._quota_per_class = config.get("quota_per_class", 10)
        self._dedup_enabled = config.get("dedup_enabled", False)
        self._dedup_threshold = config.get("dedup_threshold", 10)
        self._counters_file = config.get("counters_file", ".collection_counts.json")
        self._save_path = Path(save_path)
        self._save_path.mkdir(parents=True, exist_ok=True)

        self._counters = _nested_defaultdict()
        self._load_counters()

    @property
    def _counters_path(self) -> Path:
        """Full path to the counters persistence file."""
        return self._save_path / self._counters_file

    def should_collect(self, model_type: str, roi_id: str, predicted_class: str) -> bool:
        """Check whether an image should be collected for the given class.

        Returns True if collection is enabled and the current count for
        (model_type, roi_id, predicted_class) is below quota_per_class.

        Note: This method does NOT auto-vivify keys in the counters dict.
        It only reads; use increment_counter() to actually bump the count.
        """
        if not self._enabled:
            return False
        # Use .get() to avoid auto-vivifying defaultdict keys
        model_counts = self._counters.get(model_type)
        if model_counts is None:
            return True
        roi_counts = model_counts.get(roi_id)
        if roi_counts is None:
            return True
        current = roi_counts.get(predicted_class, 0)
        return current < self._quota_per_class

    def collect(
        self,
        model_type: str,
        roi_id: str,
        predicted_class: str,
        image_bytes: bytes,
    ) -> bool:
        """Collect an image if quota allows and it's not a duplicate.

        Saves the image to {save_path}/{model_type}/input/ with a timestamped
        filename, performs optional deduplication via perceptual hashing, and
        increments the per-class counter.

        Args:
            model_type: Model type (e.g. "arrows", "digits").
            roi_id: ROI identifier (e.g. "analog_1").
            predicted_class: Predicted class label (e.g. "0.0", "5").
            image_bytes: Raw image bytes (typically JPEG).

        Returns:
            True if image was saved, False if skipped (quota or duplicate).
        """
        # 1. Quota check
        if not self.should_collect(model_type, roi_id, predicted_class):
            return False

        # 2. Dedup check
        img_hash = None
        if self._dedup_enabled:
            img_hash = compute_dhash(image_bytes)
            if img_hash is not None:
                input_dir = self._save_path / model_type / "input"
                input_dir.mkdir(parents=True, exist_ok=True)
                cache = HashCache(input_dir)
                match = cache.find_near_duplicate(img_hash, self._dedup_threshold)
                if match is not None:
                    logger.debug(
                        "Dedup: skipping %s/%s/%s — near-duplicate of %s",
                        model_type, roi_id, predicted_class, match,
                    )
                    return False

        # 3. Save image
        input_dir = self._save_path / model_type / "input"
        input_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        filename = f"{roi_id}_{timestamp}_label={predicted_class}.jpg"
        filepath = input_dir / filename
        filepath.write_bytes(image_bytes)
        logger.debug("Saved collection image: %s", filepath)

        # 4. Update hash cache
        if self._dedup_enabled and img_hash is not None:
            cache.add(filename, img_hash)

        # 5. Increment counter
        self._counters[model_type][roi_id][predicted_class] += 1

        # 6. Persist counters
        self._save_counters()

        return True

    def get_counts(self) -> dict:
        """Return a deep copy of the current counters.

        The returned dict is a regular dict (not defaultdict), and modifying
        it will not affect the internal state.
        """
        return copy.deepcopy(_defaultdict_to_dict(self._counters))

    def reset(self) -> None:
        """Clear all counters and delete the persistence file if it exists."""
        self._counters = _nested_defaultdict()
        try:
            if self._counters_path.exists():
                self._counters_path.unlink()
                logger.info("Collection counters file deleted: %s", self._counters_path)
        except OSError as e:
            logger.warning("Failed to delete counters file: %s", e)

    def _load_counters(self) -> None:
        """Load counters from the persistence file.

        Handles missing file (starts fresh) and corrupted JSON (logs warning,
        starts fresh).
        """
        if not self._counters_path.exists():
            logger.debug("No counters file at %s, starting fresh", self._counters_path)
            return

        try:
            with open(self._counters_path, "r") as f:
                data = json.load(f)
            self._counters = _defaultdict_from_dict(data)
            logger.info("Loaded collection counters from %s", self._counters_path)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "Corrupted counters file %s: %s — starting fresh",
                self._counters_path,
                e,
            )
            self._counters = _nested_defaultdict()
        except OSError as e:
            logger.warning(
                "Failed to read counters file %s: %s — starting fresh",
                self._counters_path,
                e,
            )
            self._counters = _nested_defaultdict()

    def _save_counters(self) -> None:
        """Persist counters to disk using atomic write.

        Writes to a temporary file first, then renames to the target path.
        This prevents corruption if the process is interrupted mid-write.
        Converts defaultdicts to regular dicts for clean JSON serialization.
        """
        data = _defaultdict_to_dict(self._counters)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._save_path, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._counters_path)
            logger.debug("Collection counters saved to %s", self._counters_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
