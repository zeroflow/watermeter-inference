# WatermeterService God Object Refactoring — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the 2400-line `WatermeterService` monolith into focused modules while keeping all 341 tests green at every step.

**Architecture:** Extract method groups into standalone classes, inject them into `WatermeterService` which becomes a thin orchestration facade. Use delegation wrappers during migration so existing tests and API routes work unchanged. Extract safest modules first (zero cross-dependencies), riskiest last.

**Tech Stack:** Python 3.12, pytest

---

## Strategy

### Extraction Pattern (same for every module)

1. Create new class in `watermeter/<module>.py` with extracted methods
2. New class takes only what it needs via `__init__` (config, callbacks)
3. Add delegation wrappers on `WatermeterService` → new class (tests still pass)
4. Write new focused tests for the extracted class
5. Gradually update old tests to use new class directly
6. Commit after each green step

### Phase Overview

| Phase | Modules | Risk | Shared State Impact |
|-------|---------|------|---------------------|
| **1** | image_pipeline, calculation_utils, low_confidence, scheduling | LOW | None — private state only |
| **2** | plausibility, correction | MEDIUM | `rate_history` shared read/write |
| **3** | confirmation, mqtt, manual_control | HIGH | `previous_value`, `current_state`, circular deps |

**This plan covers Phase 1.** Phases 2-3 get their own plans after Phase 1 lands and we learn from the pattern.

### Shared Utility: Position IDs

Before extracting modules, factor out duplicated position ID logic (appears in 6+ methods) into a shared utility function. This reduces coupling and deduplication across all future extractions.

---

## Phase 1: Safe Extractions

### Task 1: Extract `get_position_ids()` utility

The logic for computing `digit_ids` and `arrow_ids` from config is duplicated in `calculate_total`, `check_consistency`, `_get_ordered_position_ids`, `_recalculate_with_replacement`, and `process_reading`. Factor it out.

**Files:**
- Create: `watermeter/position_utils.py`
- Create: `tests/unit/test_position_utils.py`
- Modify: `watermeter/watermeter_service.py` (replace inline ID logic with calls to utility)

**Step 1: Write the test file**

```python
"""Tests for position ID utility functions."""
import pytest
from watermeter.position_utils import get_position_ids


class TestGetPositionIds:
    """Test get_position_ids() with various config shapes."""

    def test_simple_ids(self):
        """Config with explicit image IDs."""
        config = {
            "images": {"ids": ["digit_1", "digit_2", "analog_1", "analog_2"]},
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2"]
        assert arrow_ids == ["analog_1", "analog_2"]

    def test_process_separate_mode(self):
        """Config with process_separate detection mode."""
        config = {
            "images": {"ids": ["digit_1", "digit_2", "analog_1"]},
            "detection": {
                "digits": {"enabled": True, "ids": ["digit_1", "digit_2"]},
                "analogs": {"enabled": True, "ids": ["analog_1"]},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2"]
        assert arrow_ids == ["analog_1"]

    def test_empty_arrows(self):
        """Config with only digits, no arrows."""
        config = {
            "images": {"ids": ["digit_1", "digit_2"]},
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2"]
        assert arrow_ids == []

    def test_whole_image_mode(self):
        """Config using whole image processing with ROIs."""
        config = {
            "images": {"ids": ["digit_1", "digit_2", "analog_1"]},
            "detection": {
                "process_separate": False,
                "digits": {"ids": ["digit_1", "digit_2"]},
                "analogs": {"ids": ["analog_1"]},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2"]
        assert arrow_ids == ["analog_1"]
```

**Step 2: Run tests — verify FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_position_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watermeter.position_utils'`

**Step 3: Implement the utility**

Read `watermeter/watermeter_service.py` and find the position ID logic in `calculate_total()` (around L692-L704). Extract the pattern into the new file:

Create `watermeter/position_utils.py`:

```python
"""Shared utility for computing position IDs from config."""

from typing import List, Tuple


def get_position_ids(config: dict) -> Tuple[List[str], List[str]]:
    """Extract ordered digit and arrow position IDs from config.

    Handles both explicit IDs from images.ids (filtering by prefix)
    and structured detection config (digits.ids / analogs.ids).

    Args:
        config: The full application config dict.

    Returns:
        Tuple of (digit_ids, arrow_ids) in config order.
    """
    detection = config.get("detection", {})

    # Prefer structured detection config if present
    digits_config = detection.get("digits", {})
    analogs_config = detection.get("analogs", {})

    if digits_config.get("ids") or analogs_config.get("ids"):
        digit_ids = digits_config.get("ids", [])
        arrow_ids = analogs_config.get("ids", [])
    else:
        # Fall back to prefix-based filtering from images.ids
        all_ids = config.get("images", {}).get("ids", [])
        digit_ids = [i for i in all_ids if i.startswith("digit")]
        arrow_ids = [i for i in all_ids if i.startswith("analog")]

    return digit_ids, arrow_ids
```

**Step 4: Run tests — verify PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_position_utils.py -v`
Expected: All PASS

**Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add watermeter/position_utils.py tests/unit/test_position_utils.py
git commit -m "claude: extract get_position_ids utility from duplicated logic"
```

**Step 7: Replace inline ID logic in watermeter_service.py**

Read `watermeter/watermeter_service.py` and find ALL locations where digit_ids/arrow_ids are computed inline. Replace each with a call to `get_position_ids(self.config)`. Add the import at the top of the relevant section.

The key locations (search for `startswith("digit")` or `startswith("analog")` or `detection.get("digits")`):
- `calculate_total()` around L692
- `check_consistency()` around L764
- `_get_ordered_position_ids()` around L1237
- `_recalculate_with_replacement()` around L1272
- `process_reading()` around L1700

For each, replace the inline block with:
```python
from watermeter.position_utils import get_position_ids
digit_ids, arrow_ids = get_position_ids(self.config)
```

(Put the import at the top of the file, not inline.)

**Step 8: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All PASS

**Step 9: Commit**

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: replace inline position ID logic with get_position_ids utility"
```

---

### Task 2: Extract `ImagePipeline` class

The safest extraction — zero outbound dependencies, private state only.

**Files:**
- Create: `watermeter/image_pipeline.py`
- Modify: `watermeter/watermeter_service.py` (delegate to ImagePipeline)

**Step 1: Create the ImagePipeline class**

Read these methods in `watermeter/watermeter_service.py`:
- `fetch_images()` (L333)
- `fetch_whole_image()` (L373)
- `process_whole_image()` (L395)
- `_load_marker_templates()` (L462)
- `invalidate_marker_cache()` (L491)
- `_align_with_markers()` (L495)
- `_extract_roi()` (L580)

Create `watermeter/image_pipeline.py` containing an `ImagePipeline` class with all 7 methods. The constructor takes `config: dict`. The class owns `self._marker_templates` (previously on WatermeterService).

Replace all `self.config` references with the class's own `self.config`. Keep method signatures identical. Keep all internal `self._` calls (they all stay within the class).

Important: `process_whole_image` calls `_load_marker_templates`, `_align_with_markers`, and `_extract_roi` — these are all internal, so no cross-module calls.

The `fetch_images` and `fetch_whole_image` methods use `httpx` to fetch from a URL. Keep that dependency.

**Step 2: Add delegation in WatermeterService**

In `WatermeterService.__init__`, create the pipeline:
```python
self._image_pipeline = ImagePipeline(self.config)
```

Replace method bodies with delegation:
```python
def fetch_images(self):
    return self._image_pipeline.fetch_images()

def fetch_whole_image(self):
    return self._image_pipeline.fetch_whole_image()

def process_whole_image(self, image_bytes):
    return self._image_pipeline.process_whole_image(image_bytes)

def invalidate_marker_cache(self):
    self._image_pipeline.invalidate_marker_cache()
```

Remove `_load_marker_templates`, `_align_with_markers`, `_extract_roi` from WatermeterService (they were private, only called internally by the pipeline methods).

Also update `reload_config` to pass the new config to the pipeline:
```python
self._image_pipeline.config = new_config
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All PASS (delegation wrappers preserve the interface)

**Step 4: Commit**

```bash
git add watermeter/image_pipeline.py watermeter/watermeter_service.py
git commit -m "claude: extract ImagePipeline class from WatermeterService"
```

---

### Task 3: Extract `LowConfidenceCapture` class

Self-contained, private state only.

**Files:**
- Create: `watermeter/low_confidence_capture.py`
- Modify: `watermeter/watermeter_service.py`

**Step 1: Create the class**

Read `save_low_confidence()` (L1523) in `watermeter_service.py`. Extract it into a `LowConfidenceCapture` class.

Constructor takes `config: dict`. Owns `self.last_save_times` (previously on WatermeterService).

Create `watermeter/low_confidence_capture.py`:
```python
class LowConfidenceCapture:
    def __init__(self, config: dict):
        self.config = config
        self.last_save_times: dict = {}

    def save_low_confidence(self, image_id, image_bytes, prediction, next_bytes=None):
        # ... (move the full method body here)
```

**Step 2: Delegate in WatermeterService**

In `__init__`:
```python
self._low_confidence = LowConfidenceCapture(self.config)
```

Replace method:
```python
def save_low_confidence(self, image_id, image_bytes, prediction, next_bytes=None):
    return self._low_confidence.save_low_confidence(image_id, image_bytes, prediction, next_bytes)
```

Remove `self.last_save_times` from `__init__`.

Update `reload_config`:
```python
self._low_confidence.config = new_config
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add watermeter/low_confidence_capture.py watermeter/watermeter_service.py
git commit -m "claude: extract LowConfidenceCapture class from WatermeterService"
```

---

### Task 4: Extract `SchedulingManager` class

Thin async wrappers with minimal dependencies.

**Files:**
- Create: `watermeter/scheduling.py`
- Modify: `watermeter/watermeter_service.py`

**Step 1: Create the class**

Read the scheduling methods:
- `_cyclic_loop()` (L2376)
- `start_cyclic_loop()` (L2387)
- `stop_cyclic_loop()` (L2395)
- `_stats_loop()` (L2121)
- `start_stats_loop()` (L2134)
- `stop_stats_loop()` (L2142)

Create `watermeter/scheduling.py`:
```python
class SchedulingManager:
    def __init__(self, cyclic_interval: int, process_fn, stats_fn):
        self.cyclic_interval = cyclic_interval
        self._process_fn = process_fn  # callback: async process_reading()
        self._stats_fn = stats_fn      # callback: async publish_training_stats()
        self._cyclic_task = None
        self._stats_task = None
```

The key insight: `_cyclic_loop` calls `process_reading()` and `_stats_loop` calls `publish_training_stats()`. Instead of importing those methods, accept them as callbacks in the constructor. This breaks the dependency.

Move all 6 methods into the class, replacing `self.process_reading()` with `self._process_fn()` and `self.publish_training_stats()` with `self._stats_fn()`.

**Step 2: Delegate in WatermeterService**

In `__init__` (after `self.cyclic_interval` is set):
```python
self._scheduler = SchedulingManager(
    cyclic_interval=self.cyclic_interval,
    process_fn=self.process_reading,
    stats_fn=self.publish_training_stats,
)
```

Replace methods with delegation. Remove `self._cyclic_task` and `self._stats_task` from `__init__`.

Update `reload_config` to sync the interval:
```python
self._scheduler.cyclic_interval = self.cyclic_interval
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add watermeter/scheduling.py watermeter/watermeter_service.py
git commit -m "claude: extract SchedulingManager class from WatermeterService"
```

---

### Task 5: Update codebase map and backlog

**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md`

**Step 1: Update codebase map**

Add new sections for each extracted module:

```markdown
### position_utils.py
- `get_position_ids(config)` — L[n]: Extract digit/arrow IDs from config

### image_pipeline.py
- `class ImagePipeline` — L[n]: Image fetching, marker alignment, ROI extraction
  - `fetch_images()` — L[n]: Fetch ROI images via HTTP
  - `fetch_whole_image()` — L[n]: Fetch full source image
  - `process_whole_image(img_bytes)` — L[n]: Rotate, align markers, extract ROIs
  - `invalidate_marker_cache()` — L[n]: Clear cached marker templates

### low_confidence_capture.py
- `class LowConfidenceCapture` — L[n]: Save low-confidence images for labeling with dedup
  - `save_low_confidence(image_id, image_bytes, prediction, next_bytes)` — L[n]

### scheduling.py
- `class SchedulingManager` — L[n]: Cyclic trigger and stats publishing loops
  - `start_cyclic_loop()` / `stop_cyclic_loop()` — L[n]
  - `start_stats_loop()` / `stop_stats_loop()` — L[n]
```

Update the `watermeter_service.py` section to remove extracted methods and note the delegation pattern.

**Step 2: Update backlog**

Change BL-30 status from `idea` to `in-progress` (Phase 1 complete, Phases 2-3 remain).

Add a note:
```
  - Phase 1 done: image_pipeline, low_confidence, scheduling, position_utils extracted
  - Phase 2 planned: plausibility, correction
  - Phase 3 planned: confirmation, mqtt, manual_control
```

**Step 3: Commit**

```bash
git add docs/codebase_map.md backlog.md
git commit -m "claude: update codebase map for phase 1 extractions, BL-30 in-progress"
```

---

## Phase 2 Outline (future plan)

### Plausibility Module
- Extract `check_consistency`, `validate_plausibility`, rate history methods
- **Challenge:** `rate_history` is written by confirmation and manual modules
- **Solution:** Encapsulate rate history in a `RateTracker` class shared between modules

### Correction Module
- Extract all correction methods
- Depends on `RateTracker` from plausibility (for `_estimate_expected_range`)
- **Solution:** Pass `rate_tracker` as dependency

## Phase 3 Outline (future plan)

### State Management Redesign
- Before extracting confirmation/mqtt, introduce `MeterState` value object
- Encapsulates: `previous_value`, `last_update_time`, `current_state`, `consecutive_rejections`
- Provides explicit transitions: `accept()`, `reject()`, `revert()`, `reset()`, `manual_set()`
- All modules interact with state through this object

### MQTT Module
- Extract publish/subscribe/connect/disconnect
- `on_mqtt_message` becomes a thin dispatcher that routes to callbacks registered by core
- Breaks circular dependency with confirmation

### Confirmation Module
- Extract after `MeterState` exists — state mutations go through the state object
- Timer management stays encapsulated

### Manual Control
- Thin methods that call `MeterState` transitions + MQTT publishing
- May not need its own class — could stay on the facade
