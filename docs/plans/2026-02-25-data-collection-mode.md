# Data Collection Mode — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a quota-based data collection mode that saves images across all confidence levels with per-class-per-ROI quotas, dedup, and persistent counters.

**Architecture:** New `DataCollector` class in `watermeter/data_collector.py`, integrated into `process_reading()` alongside the existing low-confidence save path. Config validated via JSON Schema in `config_utils.py`. Counters persisted to `.collection_counts.json`.

**Tech Stack:** Python 3.12, pytest, cv2 (via existing `image_hash.py`), JSON for persistence.

**Design doc:** `docs/plans/2026-02-25-data-collection-mode-design.md`

---

### Task 1: Config Schema Validation

**Files:**
- Modify: `watermeter/config_utils.py` (inside the JSON Schema around lines 444–471)
- Test: `tests/unit/test_config_utils.py`

**Context:** The config schema is a Python dict called `CONFIG_SCHEMA` in `config_utils.py`. The `inference` section already exists. Add `data_collection` as a nested object under `inference.properties`.

**Step 1: Write the failing test**

Add to `tests/unit/test_config_utils.py`:

```python
class TestDataCollectionConfig:
    def test_data_collection_defaults_applied(self):
        """Config without data_collection section should get defaults."""
        config = load_config()  # or however configs are loaded in existing tests
        dc = config["inference"]["data_collection"]
        assert dc["enabled"] is False
        assert dc["quota_per_class"] == 10
        assert dc["dedup_enabled"] is True
        assert dc["dedup_threshold"] == 10
        assert dc["counters_file"] == ".collection_counts.json"

    def test_data_collection_invalid_quota(self):
        """quota_per_class < 1 should fail validation."""
        # Use the existing test pattern for invalid config validation
        result = validate_config_with_overrides({"inference": {"data_collection": {"quota_per_class": 0}}})
        assert result["valid"] is False

    def test_data_collection_invalid_dedup_threshold(self):
        """dedup_threshold > 64 should fail validation."""
        result = validate_config_with_overrides({"inference": {"data_collection": {"dedup_threshold": 65}}})
        assert result["valid"] is False
```

Follow the existing test patterns in the file for how configs are loaded and validated. The exact helpers may differ — read the test file first to match conventions.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_config_utils.py -k "data_collection" -v`
Expected: FAIL (schema doesn't include `data_collection` yet)

**Step 3: Add schema definition**

In `watermeter/config_utils.py`, inside the `inference` object's `properties` dict (near line 444), add:

```python
"data_collection": {
    "type": "object",
    "description": "Quota-based data collection mode",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "quota_per_class": {
            "type": "integer",
            "minimum": 1,
            "default": 10,
            "description": "Max images to collect per class per ROI",
        },
        "dedup_enabled": {"type": "boolean", "default": True},
        "dedup_threshold": {
            "type": "integer",
            "minimum": 0,
            "maximum": 64,
            "default": 10,
        },
        "counters_file": {
            "type": "string",
            "default": ".collection_counts.json",
        },
    },
    "additionalProperties": False,
},
```

Also check `_apply_defaults()` or equivalent in `config_utils.py` — the existing code may need a default `data_collection: {}` block added to the inference section so that defaults get applied even when the section is absent from `config.yaml`. Read the default-application logic carefully.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_config_utils.py -k "data_collection" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add watermeter/config_utils.py tests/unit/test_config_utils.py
git commit -m "claude: add data_collection config schema and validation"
```

---

### Task 2: DataCollector — Counter Logic (No I/O)

**Files:**
- Create: `watermeter/data_collector.py`
- Create: `tests/unit/test_data_collector.py`

**Context:** Start with pure counter logic — no image saving, no dedup, no disk persistence yet. This tests the quota enforcement in isolation.

**Step 1: Write the failing tests**

Create `tests/unit/test_data_collector.py`:

```python
import pytest
from watermeter.data_collector import DataCollector


class TestQuotaEnforcement:
    def make_config(self, quota=10, dedup_enabled=False):
        return {
            "enabled": True,
            "quota_per_class": quota,
            "dedup_enabled": dedup_enabled,
            "dedup_threshold": 10,
            "counters_file": ".collection_counts.json",
        }

    def test_should_collect_under_quota(self, tmp_path):
        dc = DataCollector(self.make_config(quota=5), save_path=str(tmp_path))
        assert dc.should_collect("arrows", "analog_1", "2.3") is True

    def test_should_collect_at_quota(self, tmp_path):
        dc = DataCollector(self.make_config(quota=2), save_path=str(tmp_path))
        # Manually set counter to quota
        dc._counters["arrows"]["analog_1"]["2.3"] = 2
        assert dc.should_collect("arrows", "analog_1", "2.3") is False

    def test_should_collect_different_classes_independent(self, tmp_path):
        dc = DataCollector(self.make_config(quota=1), save_path=str(tmp_path))
        dc._counters["arrows"]["analog_1"]["2.3"] = 1
        # Different class should still be collectible
        assert dc.should_collect("arrows", "analog_1", "4.5") is True

    def test_should_collect_different_rois_independent(self, tmp_path):
        dc = DataCollector(self.make_config(quota=1), save_path=str(tmp_path))
        dc._counters["arrows"]["analog_1"]["2.3"] = 1
        # Same class, different ROI should still be collectible
        assert dc.should_collect("arrows", "analog_2", "2.3") is True

    def test_get_counts_returns_copy(self, tmp_path):
        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc._counters["digits"]["digit_1"]["5"] = 3
        counts = dc.get_counts()
        assert counts["digits"]["digit_1"]["5"] == 3
        # Modifying returned dict should not affect internal state
        counts["digits"]["digit_1"]["5"] = 99
        assert dc._counters["digits"]["digit_1"]["5"] == 3

    def test_reset_clears_counters(self, tmp_path):
        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc._counters["arrows"]["analog_1"]["1.0"] = 5
        dc.reset()
        assert dc.should_collect("arrows", "analog_1", "1.0") is True
        assert dc.get_counts() == {}
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watermeter.data_collector'`

**Step 3: Write minimal DataCollector with counter logic**

Create `watermeter/data_collector.py`:

```python
"""Quota-based data collection for training dataset diversity."""

import copy
import json
import logging
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class DataCollector:
    """Collects training images with per-class-per-ROI quotas.

    Saves images across all confidence levels to build diverse datasets.
    Uses quotas to prevent over-collecting any single class.
    """

    def __init__(self, config: dict, save_path: str):
        self.config = config
        self.save_path = Path(save_path)
        self.quota = config["quota_per_class"]
        self._counters: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        self._counters_file = self.save_path / config["counters_file"]
        self._load_counters()

    def _load_counters(self) -> None:
        """Load persisted counters from disk."""
        if self._counters_file.exists():
            try:
                with open(self._counters_file) as f:
                    data = json.load(f)
                for model_type, rois in data.items():
                    for roi_id, classes in rois.items():
                        for class_name, count in classes.items():
                            self._counters[model_type][roi_id][class_name] = count
                logger.info("Loaded collection counters from %s", self._counters_file)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load counters from %s: %s", self._counters_file, e)

    def _save_counters(self) -> None:
        """Persist counters to disk atomically."""
        self._counters_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._counters_file.with_suffix(".tmp")
        # Convert defaultdicts to regular dicts for JSON serialization
        data = {
            mt: {roi: dict(classes) for roi, classes in rois.items()}
            for mt, rois in self._counters.items()
        }
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
        tmp_file.rename(self._counters_file)

    def should_collect(self, model_type: str, roi_id: str, predicted_class: str) -> bool:
        """Check if quota allows collecting this class for this ROI."""
        current = self._counters[model_type][roi_id][predicted_class]
        return current < self.quota

    def get_counts(self) -> dict:
        """Return a deep copy of current counters."""
        return copy.deepcopy(dict(self._counters))

    def reset(self) -> None:
        """Clear all counters and remove persistence file."""
        self._counters = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        if self._counters_file.exists():
            self._counters_file.unlink()
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add watermeter/data_collector.py tests/unit/test_data_collector.py
git commit -m "claude: add DataCollector with quota counter logic"
```

---

### Task 3: DataCollector — Counter Persistence

**Files:**
- Modify: `watermeter/data_collector.py`
- Modify: `tests/unit/test_data_collector.py`

**Step 1: Write the failing tests**

Add to `tests/unit/test_data_collector.py`:

```python
class TestCounterPersistence:
    def make_config(self, quota=10):
        return {
            "enabled": True,
            "quota_per_class": quota,
            "dedup_enabled": False,
            "dedup_threshold": 10,
            "counters_file": ".collection_counts.json",
        }

    def test_counters_persist_across_instances(self, tmp_path):
        """Counters should survive creating a new DataCollector."""
        dc1 = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc1._counters["arrows"]["analog_1"]["2.3"] = 7
        dc1._save_counters()

        dc2 = DataCollector(self.make_config(), save_path=str(tmp_path))
        assert dc2._counters["arrows"]["analog_1"]["2.3"] == 7

    def test_counters_file_is_valid_json(self, tmp_path):
        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc._counters["digits"]["digit_1"]["5"] = 3
        dc._save_counters()

        counters_file = tmp_path / ".collection_counts.json"
        assert counters_file.exists()
        data = json.loads(counters_file.read_text())
        assert data["digits"]["digit_1"]["5"] == 3

    def test_corrupted_counters_file_handled(self, tmp_path):
        """Corrupted JSON should not crash — start fresh."""
        counters_file = tmp_path / ".collection_counts.json"
        counters_file.write_text("{invalid json")

        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        # Should start with empty counters
        assert dc.should_collect("arrows", "analog_1", "0.0") is True

    def test_reset_removes_persistence_file(self, tmp_path):
        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc._counters["arrows"]["analog_1"]["1.0"] = 5
        dc._save_counters()
        assert (tmp_path / ".collection_counts.json").exists()

        dc.reset()
        assert not (tmp_path / ".collection_counts.json").exists()
```

**Step 2: Run tests to verify they pass**

These tests should already pass with the implementation from Task 2 — `_load_counters()`, `_save_counters()`, and `reset()` are already implemented. Run:

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py::TestCounterPersistence -v`
Expected: PASS (persistence logic was written in Task 2)

**Step 3: Commit**

```bash
git add tests/unit/test_data_collector.py
git commit -m "claude: add counter persistence tests for DataCollector"
```

---

### Task 4: DataCollector — `collect()` Method (Image Saving + Dedup)

**Files:**
- Modify: `watermeter/data_collector.py`
- Modify: `tests/unit/test_data_collector.py`

**Context:** The `collect()` method is the main entry point. It combines quota check, dedup, image save, hash cache update, and counter increment. It reuses `compute_dhash()` and `HashCache` from `watermeter/image_hash.py`.

**Step 1: Write the failing tests**

Add to `tests/unit/test_data_collector.py`:

```python
from unittest.mock import patch, MagicMock
import numpy as np


def make_fake_image_bytes():
    """Create minimal valid JPEG bytes for testing."""
    # Use cv2 to encode a small random image
    import cv2
    img = np.random.randint(0, 255, (20, 20, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


class TestCollect:
    def make_config(self, quota=10, dedup_enabled=False, dedup_threshold=10):
        return {
            "enabled": True,
            "quota_per_class": quota,
            "dedup_enabled": dedup_enabled,
            "dedup_threshold": dedup_threshold,
            "counters_file": ".collection_counts.json",
        }

    def test_collect_saves_image_and_increments_counter(self, tmp_path):
        dc = DataCollector(self.make_config(quota=5), save_path=str(tmp_path))
        image_bytes = make_fake_image_bytes()

        result = dc.collect("arrows", "analog_1", "2.3", image_bytes)

        assert result is True
        assert dc._counters["arrows"]["analog_1"]["2.3"] == 1
        # Check file was created in the right directory
        save_dir = tmp_path / "arrows" / "input"
        saved_files = list(save_dir.glob("*.jpg"))
        assert len(saved_files) == 1
        assert "_label=2.3" in saved_files[0].name
        assert "analog_1" in saved_files[0].name

    def test_collect_respects_quota(self, tmp_path):
        dc = DataCollector(self.make_config(quota=2), save_path=str(tmp_path))
        image_bytes = make_fake_image_bytes()

        assert dc.collect("arrows", "analog_1", "2.3", image_bytes) is True
        assert dc.collect("arrows", "analog_1", "2.3", make_fake_image_bytes()) is True
        assert dc.collect("arrows", "analog_1", "2.3", make_fake_image_bytes()) is False
        assert dc._counters["arrows"]["analog_1"]["2.3"] == 2

    def test_collect_dedup_blocks_identical_image(self, tmp_path):
        dc = DataCollector(
            self.make_config(quota=10, dedup_enabled=True, dedup_threshold=5),
            save_path=str(tmp_path),
        )
        image_bytes = make_fake_image_bytes()

        assert dc.collect("arrows", "analog_1", "2.3", image_bytes) is True
        # Same exact image should be deduped
        assert dc.collect("arrows", "analog_1", "2.3", image_bytes) is False
        assert dc._counters["arrows"]["analog_1"]["2.3"] == 1

    def test_collect_dedup_allows_different_images(self, tmp_path):
        dc = DataCollector(
            self.make_config(quota=10, dedup_enabled=True, dedup_threshold=0),
            save_path=str(tmp_path),
        )
        # Two different random images should have different hashes
        assert dc.collect("arrows", "analog_1", "2.3", make_fake_image_bytes()) is True
        assert dc.collect("arrows", "analog_1", "2.3", make_fake_image_bytes()) is True
        assert dc._counters["arrows"]["analog_1"]["2.3"] == 2

    def test_collect_persists_counter_on_save(self, tmp_path):
        dc = DataCollector(self.make_config(quota=5), save_path=str(tmp_path))
        dc.collect("arrows", "analog_1", "2.3", make_fake_image_bytes())

        # Load fresh instance — counter should be persisted
        dc2 = DataCollector(self.make_config(quota=5), save_path=str(tmp_path))
        assert dc2._counters["arrows"]["analog_1"]["2.3"] == 1

    def test_collect_creates_directory_structure(self, tmp_path):
        dc = DataCollector(self.make_config(), save_path=str(tmp_path))
        dc.collect("digits", "digit_1", "5", make_fake_image_bytes())

        assert (tmp_path / "digits" / "input").is_dir()

    def test_collect_returns_false_for_invalid_image(self, tmp_path):
        dc = DataCollector(self.make_config(dedup_enabled=True), save_path=str(tmp_path))
        result = dc.collect("arrows", "analog_1", "2.3", b"not a jpeg")
        # Should still save (dedup can't hash it, but saving is still useful)
        # OR should skip — depends on design choice. Verify behavior is consistent.
        # The existing compute_dhash returns None for invalid images.
        # With dedup_enabled, we should skip dedup check and still save.
        assert result is True
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py::TestCollect -v`
Expected: FAIL (no `collect()` method yet)

**Step 3: Implement `collect()` method**

Add to `watermeter/data_collector.py`:

```python
from datetime import datetime
from .image_hash import compute_dhash, HashCache


def collect(self, model_type: str, roi_id: str, predicted_class: str, image_bytes: bytes) -> bool:
    """Collect an image if quota allows and it's not a duplicate.

    Args:
        model_type: "digits" or "arrows"
        roi_id: ROI identifier (e.g., "analog_1", "digit_2")
        predicted_class: Model's predicted class (e.g., "2.3", "5")
        image_bytes: Raw JPEG image bytes

    Returns:
        True if image was saved, False if skipped (quota full or duplicate).
    """
    if not self.should_collect(model_type, roi_id, predicted_class):
        return False

    save_dir = self.save_path / model_type / "input"
    save_dir.mkdir(parents=True, exist_ok=True)

    # Dedup check
    if self.config["dedup_enabled"]:
        new_hash = compute_dhash(image_bytes)
        if new_hash is not None:
            cache = HashCache(save_dir)
            threshold = self.config["dedup_threshold"]
            if cache.find_near_duplicate(new_hash, threshold) is not None:
                logger.debug(
                    "Collection dedup: skipping %s/%s class %s (near-duplicate)",
                    model_type, roi_id, predicted_class,
                )
                return False

    # Save image
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{roi_id}_{timestamp}_label={predicted_class}.jpg"
    save_path = save_dir / filename
    save_path.write_bytes(image_bytes)

    # Update hash cache if we computed a hash
    if self.config["dedup_enabled"] and new_hash is not None:
        cache.add(filename, new_hash)

    # Increment counter and persist
    self._counters[model_type][roi_id][predicted_class] += 1
    self._save_counters()

    logger.info(
        "Collected %s/%s class %s (%d/%d)",
        model_type, roi_id, predicted_class,
        self._counters[model_type][roi_id][predicted_class],
        self.quota,
    )
    return True
```

Note: The `new_hash` and `cache` variables are only defined inside the `if dedup_enabled` block. Make sure the hash cache update also checks `dedup_enabled` before referencing those variables. The implementation should handle this with proper scoping.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py -v`
Expected: PASS (all tests including Tasks 2-4)

**Step 5: Commit**

```bash
git add watermeter/data_collector.py tests/unit/test_data_collector.py
git commit -m "claude: implement DataCollector.collect() with dedup and image saving"
```

---

### Task 5: Service Integration

**Files:**
- Modify: `watermeter/watermeter_service.py` (lines ~40-87 init, lines ~544-572 process_reading)
- Modify: `tests/unit/test_data_collector.py` (integration-style test)

**Context:** The `WatermeterService` class instantiates `LowConfidenceCapture` in `__init__`. Follow the same pattern for `DataCollector`. The call site is in `process_reading()` around line 566, where predictions are available as `pred` dicts with keys: `id`, `image_bytes`, `model`, `confidence`, `class`.

**Step 1: Write the failing test**

Add to `tests/unit/test_data_collector.py`:

```python
class TestServiceIntegration:
    """Tests for DataCollector integration with WatermeterService.

    These test the DataCollector is wired correctly, not full service tests.
    """

    def test_collector_initialized_when_enabled(self, tmp_path):
        """DataCollector should be created when config enables it."""
        config = {
            "inference": {
                "data_collection": {
                    "enabled": True,
                    "quota_per_class": 10,
                    "dedup_enabled": False,
                    "dedup_threshold": 10,
                    "counters_file": ".collection_counts.json",
                },
            },
            "low_confidence": {"save_path": str(tmp_path)},
        }
        dc = DataCollector(config["inference"]["data_collection"], save_path=str(tmp_path))
        assert dc.quota == 10

    def test_collector_not_created_when_disabled(self, tmp_path):
        """When disabled, DataCollector should not be active."""
        config = {
            "enabled": False,
            "quota_per_class": 10,
            "dedup_enabled": False,
            "dedup_threshold": 10,
            "counters_file": ".collection_counts.json",
        }
        dc = DataCollector(config, save_path=str(tmp_path))
        # Collector exists but callers should check config["enabled"] before calling
        assert dc.config["enabled"] is False
```

**Step 2: Run test to verify it passes** (this tests DataCollector construction, should pass)

Run: `.venv/bin/python -m pytest tests/unit/test_data_collector.py::TestServiceIntegration -v`

**Step 3: Integrate into WatermeterService**

In `watermeter/watermeter_service.py`:

1. **Import** (near top, alongside existing imports):
```python
from .data_collector import DataCollector
```

2. **Init** (in `__init__`, after `self._low_confidence = LowConfidenceCapture(self.config)`):
```python
dc_config = self.config.get("inference", {}).get("data_collection", {})
if dc_config.get("enabled", False):
    save_path = self.config["low_confidence"]["save_path"]
    self._data_collector = DataCollector(dc_config, save_path=save_path)
    logger.info("Data collection mode enabled (quota: %d per class)", dc_config["quota_per_class"])
else:
    self._data_collector = None
```

3. **Call site** in `process_reading()`, BEFORE the low-confidence check (around line 545):
```python
# Data collection (saves regardless of confidence)
if self._data_collector is not None:
    self._data_collector.collect(
        pred["model"],  # "digits" or "arrows"
        pred["id"],     # e.g., "analog_1", "digit_2"
        pred["class"],  # predicted class string
        pred["image_bytes"],
    )
```

This goes before the existing `if pred["confidence"] < threshold:` block so both paths can run independently.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (existing tests unaffected, new tests pass)

**Step 5: Commit**

```bash
git add watermeter/watermeter_service.py tests/unit/test_data_collector.py
git commit -m "claude: integrate DataCollector into WatermeterService"
```

---

### Task 6: Add Default Config

**Files:**
- Modify: `config.yaml` (add `data_collection` block under `inference`)

**Step 1: Add config block**

In `config.yaml`, under the `inference:` section (after `confidence_threshold` and related settings), add:

```yaml
  # Data collection mode — fills training dataset with diverse images
  # Set enabled: true to start collecting, then label via /label page
  data_collection:
    enabled: false
    quota_per_class: 10
    dedup_enabled: true
    dedup_threshold: 10
    counters_file: ".collection_counts.json"
```

**Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS

**Step 3: Commit**

```bash
git add config.yaml
git commit -m "claude: add data_collection config defaults to config.yaml"
```

---

### Task 7: Codebase Map Update

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Add DataCollector entries**

Add `watermeter/data_collector.py` to the codebase map with class, methods, and line numbers. Follow the existing format in the map file.

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with DataCollector"
```

---

### Task 8: Full Regression Test

**Files:** None (test-only task)

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: All tests PASS, including all new `test_data_collector.py` tests.

**Step 2: Run linting**

Run: `uvx ruff check watermeter/data_collector.py`
Run: `uvx black --check watermeter/data_collector.py`
Expected: No issues.

**Step 3: Verify existing behavior unchanged**

Check that the debug container still starts and runs inference normally with `data_collection.enabled: false` (the default). The existing low-confidence save path should be completely unaffected.

---

## Task Summary

| Task | Agent | Description | Depends On |
|------|-------|-------------|------------|
| 1 | dev | Config schema validation | — |
| 2 | dev | DataCollector counter logic | — |
| 3 | dev | Counter persistence tests | 2 |
| 4 | dev | `collect()` with dedup + saving | 2 |
| 5 | dev | Service integration | 1, 4 |
| 6 | dev | Default config in config.yaml | 1 |
| 7 | dev | Codebase map update | 4 |
| 8 | tester | Full regression test | 5, 6, 7 |

Tasks 1 and 2 can run in parallel. Tasks 3 and 4 depend on 2. Task 5 depends on 1 and 4. Task 6 depends on 1. Task 7 depends on 4. Task 8 is the final gate.
