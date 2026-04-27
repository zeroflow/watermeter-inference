# Robustness Engineering — Watermeter Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project convention:** This repo uses a coordinator pattern. The coordinator dispatches `dev`, `frontend`, `tester`, `reviewer` subagents per CLAUDE.md. Each task below is sized for a single subagent dispatch. The coordinator commits after each task lands green.

**Goal:** Move the pipeline from "happy path works" to "failures are detected and handled cleanly". No more silent wrong readings.

**Done when:** Marker-failures discard readings instead of corrupting them, the dashboard shows pipeline status, failure metrics are visible.

**Architecture:** Replace fail-open marker alignment with a typed `AlignmentResult` returned from `image_pipeline._align_with_markers`. The service short-circuits on failure, persists structured failure metadata (timestamp, which marker, confidences, reason) to a new `failure_history.json` next to `state.json`, surfaces a tri-state pipeline status (`OK`/`DEGRADED`/`FAILED`/`STALE`) through MQTT/HA + the dashboard fragment, and emits rolling counters for marker confidence + failure rates. Camera calibration and matrix caching are mechanical follow-ups gated on metrics-driven evidence.

**Tech Stack:** Python 3.12, OpenCV (template matching, affine), FastAPI + Jinja2 + HTMX (dashboard), paho-mqtt (notifications via existing HA discovery), ruamel.yaml (config), pytest + stdlib mocks. No new runtime dependencies.

**Plans dir convention:** `docs/plans/YYYY-MM-DD-<name>.md` (this file). Decisions appended to existing `docs/decisions.md`.

**Linting/format:** `uvx black --line-length 120 .` and `uvx ruff check .`. Tests run via `.venv/bin/python -m pytest`.

---

## Pre-Flight: Branch Setup

This is a multi-task feature. Per CLAUDE.md branch model: branch from `claude/main` into a feature branch, merge back when done.

- [ ] **Step 0.1: Create feature branch**

```bash
cd /home/claude/watermeter-inference
git checkout claude/main
git pull --ff-only
git checkout -b claude/robustness-milestone
```

Expected: `Switched to a new branch 'claude/robustness-milestone'`.

---

## Task 1: Make Marker Confidence Threshold Configurable + Investigation Tooling (Issue #3)

**Files:**
- Modify: `watermeter/image_pipeline.py` (replace hardcoded `CONFIDENCE_THRESHOLD = 0.5` constant with config-driven value)
- Modify: `config.yaml` (add `alignment.marker_confidence_threshold`)
- Modify: `tests/unit/test_marker_alignment.py` (add test asserting threshold is read from config)
- Create: `scripts/replay_marker_confidence.py` (offline analysis script)
- Modify: `watermeter_service.py` (add archive hook after `fetch_whole_image()`, behind a config flag)
- Append: `docs/decisions.md` (document chosen threshold + reasoning)

**Why this task is first:** #1's TDD step "low-confidence rejection" needs a knob to flip the threshold in tests. Even before we tune it from real data, making it configurable unblocks #1.

- [ ] **Step 1.1: Write the failing test for config-driven threshold**

Add to `tests/unit/test_marker_alignment.py` (append to the bottom, before the `if __name__ == "__main__"` if any):

```python
class TestThresholdConfigurable:
    """Marker confidence threshold must be loaded from config, not hardcoded."""

    def test_threshold_from_config_overrides_default(self):
        from watermeter.image_pipeline import ImagePipeline

        ip = ImagePipeline(config={"alignment": {"marker_confidence_threshold": 0.85}})
        assert ip.CONFIDENCE_THRESHOLD == 0.85

    def test_threshold_default_when_missing(self):
        from watermeter.image_pipeline import ImagePipeline

        ip = ImagePipeline(config={})
        assert ip.CONFIDENCE_THRESHOLD == 0.5  # Backwards compatible default
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py::TestThresholdConfigurable -v
```

Expected: `FAILED` — `ip.CONFIDENCE_THRESHOLD` is a class attribute, not instance, set to 0.5 hardcoded.

- [ ] **Step 1.3: Make the threshold instance-level + config-driven**

In `watermeter/image_pipeline.py`, find the existing `CONFIDENCE_THRESHOLD = 0.5` class constant (around line 82) and the `__init__` method. Apply this change:

```python
class ImagePipeline:
    SEARCH_MARGIN = 0.15  # +/-15% of image dimensions for template search
    DEFAULT_CONFIDENCE_THRESHOLD = 0.5  # cv2.TM_CCOEFF_NORMED min match score (back-compat default)

    def __init__(self, config: dict):
        self.config = config
        alignment_cfg = config.get("alignment", {}) if isinstance(config, dict) else {}
        self.CONFIDENCE_THRESHOLD = float(
            alignment_cfg.get("marker_confidence_threshold", self.DEFAULT_CONFIDENCE_THRESHOLD)
        )
        self._marker_templates = None  # Keep existing init lines below this
```

Remove the old `CONFIDENCE_THRESHOLD = 0.5` class attribute. All existing reads (`self.CONFIDENCE_THRESHOLD < ...`) keep working because we set it as an instance attribute.

- [ ] **Step 1.4: Run the test to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py::TestThresholdConfigurable -v
```

Expected: 2 PASSED. Also run the full alignment test class to make sure nothing else broke:

```bash
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py -v
```

Expected: all existing tests still PASS.

- [ ] **Step 1.5: Add `alignment` section to `config.yaml`**

Find the `detection:` block (around line 24) and add a sibling `alignment` section above or below it:

```yaml
alignment:
  # Minimum normalized cross-correlation score for cv2.matchTemplate.
  # Default 0.5 is loose; 0.7-0.8 is conventional for TM_CCOEFF_NORMED.
  # See docs/decisions.md and scripts/replay_marker_confidence.py.
  marker_confidence_threshold: 0.5
  # Archive every fetched whole-image for offline confidence analysis.
  # Disable in production once threshold is tuned (consumes disk).
  archive_raw_images: false
  archive_dir: "/data/raw_archive"
```

- [ ] **Step 1.6: Add raw-image archival hook to the service**

In `watermeter_service.py`, locate the `fetch_whole_image` call inside `process_reading` (around line 480 per the codebase report). Immediately after the bytes are fetched, conditionally archive:

```python
# Inside process_reading(), right after `whole_image = await self.fetch_whole_image()`
alignment_cfg = self.config.get("alignment", {})
if alignment_cfg.get("archive_raw_images", False):
    await asyncio.get_running_loop().run_in_executor(
        None, self._archive_raw_image, whole_image
    )
```

Add the helper as a method on `WatermeterService`:

```python
def _archive_raw_image(self, image_bytes: bytes) -> None:
    """Persist a raw fetched image for offline confidence analysis. Best-effort, never raises."""
    try:
        archive_dir = Path(self.config.get("alignment", {}).get("archive_dir", "/data/raw_archive"))
        now = datetime.now()
        day_dir = archive_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{now.strftime('%H%M%S')}.jpg"
        path.write_bytes(image_bytes)
    except Exception as e:
        logger.warning(f"Failed to archive raw image: {e}")
```

Add `from pathlib import Path` at the top of `watermeter_service.py` if not already imported.

- [ ] **Step 1.7: Write the offline replay/histogram script**

Create `scripts/replay_marker_confidence.py`:

```python
"""Offline confidence-histogram analysis for marker alignment.

Usage:
    .venv/bin/python scripts/replay_marker_confidence.py \
        --archive-dir /data/raw_archive \
        --config /data/config.yaml \
        --output /data/marker_confidence_report.json

Walks every JPEG under archive-dir, runs the production marker matcher,
and emits min/max/median/p5/p95 of the confidence per marker, plus how
many images would be REJECTED at thresholds 0.5/0.6/0.7/0.8.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import cv2
import numpy as np

from watermeter.config_utils import load_config
from watermeter.image_pipeline import ImagePipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    markers = config.get("detection", {}).get("markers", [])
    if len(markers) < 2:
        print("Need at least 2 markers in config.detection.markers")
        return 2

    ip = ImagePipeline(config=config)
    templates = ip._load_marker_templates(len(markers))
    if templates is None:
        print("Marker templates missing on disk")
        return 2

    # Per-marker confidence accumulator
    per_marker: list[list[float]] = [[] for _ in markers[:2]]

    for jpg in sorted(args.archive_dir.rglob("*.jpg")):
        img = cv2.imread(str(jpg))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]
        for i, marker in enumerate(markers[:2]):
            template = templates[i]
            th, tw = template.shape[:2]
            ref_cx = (marker["x"] + marker["width"] / 2) * w
            ref_cy = (marker["y"] + marker["height"] / 2) * h
            mx = int(ip.SEARCH_MARGIN * w)
            my = int(ip.SEARCH_MARGIN * h)
            sx1, sy1 = max(0, int(ref_cx - mx)), max(0, int(ref_cy - my))
            sx2, sy2 = min(w, int(ref_cx + mx)), min(h, int(ref_cy + my))
            if (sx2 - sx1) < tw or (sy2 - sy1) < th:
                continue
            region = gray[sy1:sy2, sx1:sx2]
            res = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            per_marker[i].append(float(max_val))

    def summarize(scores: list[float]) -> dict:
        if not scores:
            return {"count": 0}
        scores_sorted = sorted(scores)
        return {
            "count": len(scores),
            "min": scores_sorted[0],
            "max": scores_sorted[-1],
            "median": statistics.median(scores),
            "p5": scores_sorted[max(0, int(0.05 * len(scores)) - 1)],
            "p95": scores_sorted[min(len(scores) - 1, int(0.95 * len(scores)))],
            "rejected_at_0.5": sum(1 for s in scores if s < 0.5),
            "rejected_at_0.6": sum(1 for s in scores if s < 0.6),
            "rejected_at_0.7": sum(1 for s in scores if s < 0.7),
            "rejected_at_0.8": sum(1 for s in scores if s < 0.8),
        }

    report = {
        "marker_1": summarize(per_marker[0]),
        "marker_2": summarize(per_marker[1]) if len(per_marker) > 1 else {"count": 0},
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 1.8: Append decision record to `docs/decisions.md`**

Append the following section. The chosen threshold is left as `0.5` (back-compat) until real archive data is available; document the followup explicitly:

```markdown
## 2026-04-27: Marker Confidence Threshold

**Status:** Provisional — review after 7 days of archive data.

**Decision:** Default `alignment.marker_confidence_threshold = 0.5` retained for back-compat.
Threshold is now configurable via `config.yaml`. Production should re-tune after running
`scripts/replay_marker_confidence.py` against `/data/raw_archive/` (enable
`alignment.archive_raw_images: true` to populate).

**Why 0.5 default:** Matches pre-existing hardcoded value at `image_pipeline.py:82`.
Conventional ranges for `cv2.TM_CCOEFF_NORMED` are 0.7-0.8 but actual baseline
in this deployment is unknown without sample data. Tightening blindly risks regressing
the happy path.

**Followup:** After 7 days of archive collection, run the replay script. If p5
> 0.7 across both markers, raise threshold to 0.7. If p5 < 0.6 on either marker,
investigate marker template quality before raising threshold (false-rejection risk).
```

- [ ] **Step 1.9: Lint + commit**

```bash
uvx black --line-length 120 watermeter/image_pipeline.py watermeter_service.py scripts/replay_marker_confidence.py tests/unit/test_marker_alignment.py
uvx ruff check watermeter/image_pipeline.py watermeter_service.py scripts/replay_marker_confidence.py
git add watermeter/image_pipeline.py watermeter_service.py config.yaml scripts/replay_marker_confidence.py tests/unit/test_marker_alignment.py docs/decisions.md
git commit -m "claude: make marker confidence threshold configurable + add replay script"
```

---

## Task 2: Fail-Closed Alignment Logic (Issue #1)

**Files:**
- Modify: `watermeter/image_pipeline.py` — introduce `AlignmentResult` dataclass; refactor `_align_with_markers` to return it; update all 7 fail-open paths to return `AlignmentResult(success=False, ...)`; update successful path to return `AlignmentResult(success=True, image=aligned, ...)`. Update `process_whole_image` to surface the failure to the caller.
- Modify: `watermeter_service.py` — short-circuit `process_reading` when alignment fails; persist failure metadata; log at ERROR.
- Modify: `watermeter/persistence.py` — add a `FailureStore` class backed by a separate `failure_history.json` (ring buffer of last 50). Don't change the existing `StateStore` surface.
- Modify: `tests/unit/test_marker_alignment.py` — convert existing tests to assert on `AlignmentResult.success/error_reason` instead of "image is unchanged".
- Create: `tests/unit/test_persistence_failures.py` — unit-tests for the new failure-history API.
- Create: `tests/unit/test_service_fail_closed.py` — service-level test that an alignment failure short-circuits inference.

- [ ] **Step 2.1: Write the failing test for the `AlignmentResult` shape**

Add a new test class to `tests/unit/test_marker_alignment.py`:

```python
class TestAlignmentResult:
    """_align_with_markers returns a typed result object, not a raw image."""

    def test_zero_markers_returns_failure(self):
        svc = make_service()
        img = make_synthetic_image()
        result = svc._image_pipeline._align_with_markers(img, [])
        assert result.success is False
        assert result.error_reason == "insufficient_markers"
        assert result.image is None  # Never propagate misaligned image
        assert result.marker_confidences == []
        assert result.failed_marker is None

    def test_one_marker_returns_failure(self):
        svc = make_service()
        img = make_synthetic_image()
        markers = make_markers_config(100, 100, None, None, 640, 480)[:1]
        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "insufficient_markers"

    def test_low_confidence_returns_failure_with_marker_index(self):
        # Forces low confidence by using random-noise templates
        svc = make_service()
        img = make_synthetic_image()
        markers = make_markers_config(100, 100, 500, 350, 640, 480)
        with patch_marker_templates_with_random_noise():
            result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "low_confidence"
        assert result.failed_marker in (1, 2)
        assert len(result.marker_confidences) >= 1
        assert all(c < 0.5 for c in result.marker_confidences)

    def test_successful_alignment_returns_image(self):
        svc = make_service()
        # Set up an image with markers at exactly the expected positions
        # (re-use existing TestAlreadyAlignedImage helpers if present)
        img, markers = build_aligned_test_image()
        with patch_marker_templates_from(img, markers):
            result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is True
        assert result.image is not None
        assert result.image.shape == img.shape
        assert len(result.marker_confidences) == 2
        assert all(c >= 0.5 for c in result.marker_confidences)
        assert result.error_reason is None
```

If `patch_marker_templates_with_random_noise()` and `build_aligned_test_image()` aren't already module-level helpers in the file, copy the inline setup directly from existing tests in the same file (`TestLowConfidenceMatch`, `TestAlreadyAlignedImage`) — repeat the code rather than depending on a helper that doesn't exist yet.

- [ ] **Step 2.2: Run the new tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py::TestAlignmentResult -v
```

Expected: FAIL with `AttributeError: 'numpy.ndarray' object has no attribute 'success'` — current impl returns the raw image array.

- [ ] **Step 2.3: Define `AlignmentResult` and refactor `_align_with_markers`**

In `watermeter/image_pipeline.py`, near the top of the file (after imports):

```python
from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class AlignmentResult:
    """Outcome of marker-based image alignment.

    On success, `image` holds the warped image and `marker_confidences`
    holds the cv2.matchTemplate scores per marker.

    On failure, `image` is None (callers MUST NOT proceed with the original
    misaligned image — that's the whole point of fail-closed). `error_reason`
    is one of: "insufficient_markers", "templates_missing", "search_region_too_small",
    "low_confidence", "transform_failed". `failed_marker` is 1-indexed when
    a single marker is at fault, else None.
    """

    success: bool
    image: Optional["np.ndarray"] = None
    error_reason: Optional[str] = None
    failed_marker: Optional[int] = None
    marker_confidences: List[float] = field(default_factory=list)
```

Now rewrite `_align_with_markers` (currently around lines 252–335). Replace each `return img` failure path with a typed `AlignmentResult(success=False, ...)` and the success path with `AlignmentResult(success=True, image=aligned, marker_confidences=[...])`. Also raise log levels from `.warning(...)` to `.error(...)`:

```python
def _align_with_markers(self, img: np.ndarray, markers: List[dict]) -> AlignmentResult:
    """Align image using saved marker positions via template matching.

    Fail-closed: never returns a misaligned image. Callers receive an
    AlignmentResult and MUST check .success before using .image.
    """
    height, width = img.shape[:2]
    if len(markers) < 2:
        logger.error(f"Alignment failed: need at least 2 markers, got {len(markers)}")
        return AlignmentResult(success=False, error_reason="insufficient_markers")

    templates = self._load_marker_templates(len(markers))
    if templates is None:
        logger.error("Alignment failed: marker templates not loadable")
        return AlignmentResult(success=False, error_reason="templates_missing")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ref_centers: list[list[float]] = []
    found_centers: list[list[float]] = []
    confidences: list[float] = []

    for i, marker in enumerate(markers[:2]):
        template = templates[i]
        th, tw = template.shape[:2]
        ref_cx = (marker["x"] + marker["width"] / 2) * width
        ref_cy = (marker["y"] + marker["height"] / 2) * height
        ref_centers.append([ref_cx, ref_cy])

        margin_x = int(self.SEARCH_MARGIN * width)
        margin_y = int(self.SEARCH_MARGIN * height)
        sx1 = max(0, int(ref_cx - margin_x))
        sy1 = max(0, int(ref_cy - margin_y))
        sx2 = min(width, int(ref_cx + margin_x))
        sy2 = min(height, int(ref_cy + margin_y))

        if (sx2 - sx1) < tw or (sy2 - sy1) < th:
            logger.error(f"Alignment failed: search region too small for marker {i+1}")
            return AlignmentResult(
                success=False,
                error_reason="search_region_too_small",
                failed_marker=i + 1,
                marker_confidences=confidences,
            )

        search_region = gray[sy1:sy2, sx1:sx2]
        result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        confidences.append(float(max_val))

        if max_val < self.CONFIDENCE_THRESHOLD:
            logger.error(
                f"Alignment failed: marker {i+1} match confidence {max_val:.3f} "
                f"< threshold {self.CONFIDENCE_THRESHOLD}"
            )
            return AlignmentResult(
                success=False,
                error_reason="low_confidence",
                failed_marker=i + 1,
                marker_confidences=confidences,
            )

        found_cx = sx1 + max_loc[0] + tw / 2
        found_cy = sy1 + max_loc[1] + th / 2
        found_centers.append([found_cx, found_cy])

    ref_pts = np.float32(ref_centers).reshape(-1, 1, 2)
    found_pts = np.float32(found_centers).reshape(-1, 1, 2)
    transform, _inliers = cv2.estimateAffinePartial2D(found_pts, ref_pts)
    if transform is None:
        logger.error("Alignment failed: estimateAffinePartial2D returned None")
        return AlignmentResult(
            success=False,
            error_reason="transform_failed",
            marker_confidences=confidences,
        )

    aligned = cv2.warpAffine(img, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
    logger.debug(f"Alignment ok (confidences: {[f'{c:.3f}' for c in confidences]})")
    return AlignmentResult(success=True, image=aligned, marker_confidences=confidences)
```

**Important:** Do NOT add a "last known good affine" fallback. The acceptance criterion explicitly forbids it (would mask gradual drift).

- [ ] **Step 2.4: Update existing tests in `tests/unit/test_marker_alignment.py`**

The legacy classes `TestFewerThanTwoMarkers`, `TestMissingTemplateFiles`, `TestLowConfidenceMatch`, `TestAlreadyAlignedImage` previously asserted `result is img` or `result.shape == img.shape`. Convert each to assert on `AlignmentResult` fields:

```python
# Was: assert result is img
# Becomes:
assert result.success is False
assert result.error_reason == "insufficient_markers"

# Was: assert result.shape == img.shape   (low confidence)
# Becomes:
assert result.success is False
assert result.error_reason == "low_confidence"

# Was: assert mean_diff < 5.0   (already-aligned)
# Becomes:
assert result.success is True
diff = np.abs(result.image.astype(np.float32) - img.astype(np.float32))
assert diff.mean() < 5.0
```

- [ ] **Step 2.5: Run all marker tests, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py -v
```

Expected: every test in `TestAlignmentResult` + the converted legacy tests PASS.

- [ ] **Step 2.6: Update `process_whole_image` to propagate the failure**

In `watermeter/image_pipeline.py`, find `process_whole_image()` (around line 171 per the report). Today it calls `_align_with_markers` and treats the return as an image. Change the contract so it returns `(images_dict_or_None, AlignmentResult)`:

```python
def process_whole_image(self, image_bytes: bytes) -> tuple[Optional[dict], AlignmentResult]:
    """Decode whole image, run lens correction, run marker alignment, slice ROIs.

    Returns:
        (images_dict, alignment_result):
        - On alignment success: (dict of {id: (bytes, class)}, AlignmentResult(success=True, ...))
        - On alignment failure: (None, AlignmentResult(success=False, ...))
    """
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    # ... existing fisheye correction ...
    markers = self.config.get("detection", {}).get("markers", [])
    if markers:
        align = self._align_with_markers(img, markers)
        if not align.success:
            return None, align
        img = align.image
    else:
        align = AlignmentResult(success=True, image=img, marker_confidences=[])
    # ... existing ROI slicing into `images` dict ...
    return images, align
```

- [ ] **Step 2.7: Write the failing service-level test**

Create `tests/unit/test_service_fail_closed.py`:

```python
"""Service-level tests: alignment failure must short-circuit inference + persist a record."""
import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

# Re-uses unit conftest fixtures (mocked cv2/openvino/paho)


def _make_service_with_alignment_failure(alignment_result):
    """Build a WatermeterService whose pipeline always returns the given alignment failure."""
    from watermeter_service import WatermeterService

    svc = object.__new__(WatermeterService)
    svc.config = {"images": {"process_separate": False}, "alignment": {}}
    svc.current_state = {"status": "idle", "warnings": []}
    svc.previous_value = None
    svc.consecutive_rejections = 0
    svc._failure_store = MagicMock()  # FailureStore is mocked here

    # Pipeline returns (None, AlignmentResult(success=False, ...))
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(None, alignment_result))

    # Inference shouldn't be reached; if it is, the test fails loudly
    svc.run_inference = AsyncMock(side_effect=AssertionError("inference must not run on alignment failure"))
    svc.fetch_whole_image = AsyncMock(return_value=b"fake jpeg bytes")
    svc.publish_to_mqtt = AsyncMock()
    return svc


def test_alignment_failure_short_circuits_inference():
    from watermeter.image_pipeline import AlignmentResult

    align = AlignmentResult(
        success=False,
        error_reason="low_confidence",
        failed_marker=2,
        marker_confidences=[0.82, 0.31],
    )
    svc = _make_service_with_alignment_failure(align)

    asyncio.run(svc.process_reading())

    # No inference, no MQTT publish of a value
    svc.run_inference.assert_not_called()
    # State reflects ALIGNMENT_FAILED
    assert svc.current_state.get("status") == "alignment_failed"
    # Failure was persisted
    svc._failure_store.record_failure.assert_called_once()
    kwargs = svc._failure_store.record_failure.call_args.kwargs
    assert kwargs.get("reason") == "low_confidence"
    assert kwargs.get("failed_marker") == 2
    assert kwargs.get("marker_confidences") == [0.82, 0.31]


def test_alignment_failure_increments_consecutive_failures():
    from watermeter.image_pipeline import AlignmentResult

    align = AlignmentResult(success=False, error_reason="insufficient_markers", marker_confidences=[])
    svc = _make_service_with_alignment_failure(align)
    svc.consecutive_alignment_failures = 0  # New counter introduced in Task 4

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 1

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 2
```

- [ ] **Step 2.8: Run the new service test, verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_service_fail_closed.py -v
```

Expected: FAIL — `process_reading` doesn't yet check the alignment result.

- [ ] **Step 2.9: Implement the failure-store API in `persistence.py`**

Extend `watermeter/persistence.py` with a `FailureStore` class (do NOT modify `StateStore`'s existing surface — additive only):

```python
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class FailureStore:
    """Ring-buffer of recent pipeline failures, persisted as JSON.

    Schema: {"records": [{"timestamp": iso8601, "reason": str,
    "failed_marker": int|None, "marker_confidences": list[float],
    "stage": "alignment"|"inference"}, ...]}.
    Last 50 records retained.
    """

    MAX_RECORDS = 50

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def record_failure(
        self,
        *,
        reason: str,
        stage: str = "alignment",
        failed_marker: Optional[int] = None,
        marker_confidences: Optional[List[float]] = None,
    ) -> None:
        records = self._load()["records"]
        records.append(
            {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "stage": stage,
                "failed_marker": failed_marker,
                "marker_confidences": list(marker_confidences or []),
            }
        )
        records = records[-self.MAX_RECORDS :]
        self._save({"records": records})

    def last_failure(self) -> Optional[dict]:
        records = self._load()["records"]
        return records[-1] if records else None

    def all_failures(self) -> List[dict]:
        return list(self._load()["records"])

    def clear(self) -> None:
        if self.file_path.exists():
            self.file_path.unlink()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {"records": []}
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "records" not in data:
                return {"records": []}
            return data
        except (json.JSONDecodeError, OSError):
            return {"records": []}

    def _save(self, data: dict) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=self.file_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.file_path)
```

- [ ] **Step 2.10: Wire `FailureStore` into the service**

In `watermeter_service.py`, locate `WatermeterService.__init__`. Add (alongside the existing `StateStore` instantiation):

```python
from watermeter.persistence import StateStore, FailureStore  # update existing import

# Inside __init__, near where StateStore is created:
state_dir = Path(self.config.get("state", {}).get("dir", "/data"))
self._failure_store = FailureStore(state_dir / "failure_history.json")
```

If the existing init uses a different state-path convention, follow that — don't introduce a new `state.dir` config if the project already encodes the path elsewhere.

- [ ] **Step 2.11: Make `process_reading` short-circuit on alignment failure**

In `watermeter_service.py`, around the `process_reading` block (lines 470–545 per the report). The current flow is:
```python
whole_image = await self.fetch_whole_image()
images = await asyncio.get_running_loop().run_in_executor(
    None, self.process_whole_image, whole_image
)
predictions = await self.run_inference(images)
# ... downstream ...
```

Change the executor call to receive both images and alignment result, and short-circuit:

```python
whole_image = await self.fetch_whole_image()
# (existing archive hook from Task 1)
images, alignment = await asyncio.get_running_loop().run_in_executor(
    None, self._image_pipeline.process_whole_image, whole_image
)
if not alignment.success:
    self._failure_store.record_failure(
        reason=alignment.error_reason,
        stage="alignment",
        failed_marker=alignment.failed_marker,
        marker_confidences=alignment.marker_confidences,
    )
    self.consecutive_alignment_failures = getattr(self, "consecutive_alignment_failures", 0) + 1
    self.current_state["status"] = "alignment_failed"
    self.current_state["last_alignment_error"] = alignment.error_reason
    self.current_state["last_alignment_timestamp"] = datetime.now().isoformat()
    logger.error(
        f"Reading discarded: alignment failed ({alignment.error_reason}, "
        f"failed_marker={alignment.failed_marker}, confidences={alignment.marker_confidences})"
    )
    return {"status": "alignment_failed", "reason": alignment.error_reason}

# Reset counter on successful alignment (we got past the fail-closed gate)
self.consecutive_alignment_failures = 0
predictions = await self.run_inference(images)
# ... rest of existing flow unchanged ...
```

If the original `process_reading` referenced `self.process_whole_image` (a method on the service that wraps the pipeline), update that wrapper too — it now also returns `(images, alignment)`.

- [ ] **Step 2.12: Run the service-level test, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_service_fail_closed.py -v
.venv/bin/python -m pytest tests/unit/test_marker_alignment.py -v
```

Expected: both files PASS.

- [ ] **Step 2.13: Write FailureStore unit tests**

Create `tests/unit/test_persistence_failures.py`:

```python
"""Unit tests for FailureStore (ring-buffered failure history)."""
from pathlib import Path

import pytest

from watermeter.persistence import FailureStore


def test_empty_store_has_no_failures(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    assert store.last_failure() is None
    assert store.all_failures() == []


def test_record_and_retrieve(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    store.record_failure(
        reason="low_confidence",
        stage="alignment",
        failed_marker=2,
        marker_confidences=[0.7, 0.3],
    )
    last = store.last_failure()
    assert last["reason"] == "low_confidence"
    assert last["failed_marker"] == 2
    assert last["marker_confidences"] == [0.7, 0.3]
    assert "timestamp" in last


def test_ring_buffer_caps_at_max(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    for i in range(60):
        store.record_failure(reason=f"r{i}")
    all_records = store.all_failures()
    assert len(all_records) == FailureStore.MAX_RECORDS
    assert all_records[0]["reason"] == "r10"
    assert all_records[-1]["reason"] == "r59"


def test_persists_across_instances(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    store.record_failure(reason="transform_failed")
    store2 = FailureStore(tmp_path / "fail.json")
    assert store2.last_failure()["reason"] == "transform_failed"


def test_corrupt_file_resets_safely(tmp_path):
    path = tmp_path / "fail.json"
    path.write_text("not json")
    store = FailureStore(path)
    assert store.all_failures() == []
    store.record_failure(reason="ok")
    assert store.last_failure()["reason"] == "ok"
```

- [ ] **Step 2.14: Run the persistence test**

```bash
.venv/bin/python -m pytest tests/unit/test_persistence_failures.py -v
```

Expected: 5 PASSED.

- [ ] **Step 2.15: Run the full unit suite to catch downstream breakage**

```bash
.venv/bin/python -m pytest tests/unit/ -v
```

Expected: green. If anything outside the alignment/service paths broke, it's likely callers of the old `process_whole_image` (which now returns a tuple). Search for usages and adapt.

- [ ] **Step 2.16: Lint + commit**

```bash
uvx black --line-length 120 watermeter/image_pipeline.py watermeter_service.py watermeter/persistence.py tests/unit/test_marker_alignment.py tests/unit/test_persistence_failures.py tests/unit/test_service_fail_closed.py
uvx ruff check watermeter/ tests/unit/
git add watermeter/image_pipeline.py watermeter_service.py watermeter/persistence.py tests/unit/test_marker_alignment.py tests/unit/test_persistence_failures.py tests/unit/test_service_fail_closed.py
git commit -m "claude: fail-closed marker alignment with typed AlignmentResult + failure persistence"
```

---

## Task 3: UI Signal for Pipeline Status (Issue #2)

**Files:**
- Modify: `watermeter_service.py` — derive `pipeline_status` (`OK`/`DEGRADED`/`FAILED`/`STALE`) and `last_valid_reading_age_seconds` into `current_state`. Set `is_live: false` when status is `FAILED`/`STALE`.
- Modify: `watermeter/templates/status_fragment.html` — add prominent status badge at top; show "Letztes gültiges Reading: vor X" + "N consecutive failures" rows; suppress the live-value glyph when not OK.
- Modify: `watermeter/static/style.css` — add status-badge variants for `.pipeline-ok`, `.pipeline-degraded`, `.pipeline-failed`, `.pipeline-stale`.
- Create: `tests/unit/test_status_fragment.py` — assert that the HTML rendered for an `alignment_failed` state contains the expected indicator and does NOT contain the live-value class.

**Status policy (locks the contract used by Tasks 4 & 5):**
- `OK` — last reading succeeded and within `last_valid_reading_age_seconds < 2 × poll_interval`.
- `DEGRADED` — at least 1 failure in the last 5 readings, but the most recent reading is OK.
- `FAILED` — most recent reading failed (alignment or inference).
- `STALE` — `consecutive_alignment_failures >= max_consecutive_failures` (defined in Task 4).

- [ ] **Step 3.1: Write the failing template test**

Create `tests/unit/test_status_fragment.py`:

```python
"""Unit tests for the dashboard status fragment template."""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "templates"


@pytest.fixture
def env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def render(env, **state) -> str:
    """Render status_fragment.html with a current_state dict."""
    base = {
        "models_loaded": True,
        "current_state": {
            "processing": False,
            "total_value": None,
            "unit": "m³",
            "last_update": None,
            "status": "idle",
            "warnings": [],
            "predictions": [],
            "pipeline_status": "OK",
            "last_valid_reading_age_seconds": None,
            "consecutive_alignment_failures": 0,
            "is_live": True,
        },
    }
    base["current_state"].update(state.pop("current_state_overrides", {}))
    base.update(state)
    return env.get_template("status_fragment.html").render(**base)


def test_pipeline_ok_renders_green_badge(env):
    html = render(env, current_state_overrides={"pipeline_status": "OK", "total_value": 12.345})
    assert "pipeline-status-badge" in html
    assert "pipeline-ok" in html
    assert "OK" in html


def test_alignment_failed_renders_red_badge_and_suppresses_live_value(env):
    html = render(
        env,
        current_state_overrides={
            "pipeline_status": "FAILED",
            "status": "alignment_failed",
            "total_value": 12.345,
            "consecutive_alignment_failures": 3,
            "last_valid_reading_age_seconds": 600,
            "last_alignment_error": "low_confidence",
            "is_live": False,
        },
    )
    assert "pipeline-failed" in html
    assert "FAILED" in html
    assert "3 consecutive failures" in html
    # Live value is suppressed (no .total-value-live class)
    assert "total-value-live" not in html
    # Stale timestamp shown (10 minutes ago)
    assert "10" in html


def test_degraded_state_shows_yellow_badge(env):
    html = render(
        env,
        current_state_overrides={
            "pipeline_status": "DEGRADED",
            "consecutive_alignment_failures": 0,
            "total_value": 12.345,
            "last_valid_reading_age_seconds": 30,
            "is_live": True,
        },
    )
    assert "pipeline-degraded" in html
    assert "DEGRADED" in html


def test_stale_state_shows_alarm_badge(env):
    html = render(
        env,
        current_state_overrides={
            "pipeline_status": "STALE",
            "consecutive_alignment_failures": 12,
            "total_value": 12.345,
            "is_live": False,
        },
    )
    assert "pipeline-stale" in html
    assert "STALE" in html
```

- [ ] **Step 3.2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_status_fragment.py -v
```

Expected: FAIL — the new badge classes don't exist in the template.

- [ ] **Step 3.3: Add status derivation to the service**

In `watermeter_service.py`, add a helper method on `WatermeterService` and call it whenever `current_state` is mutated (at minimum: end of `process_reading` success path, alignment-failure path, and immediately before responding to `/api/status/html`):

```python
def _derive_pipeline_status(self) -> None:
    """Compute pipeline_status, last_valid_reading_age_seconds, and is_live flag.

    Mutates self.current_state in-place. Idempotent.
    """
    max_failures = self.config.get("alignment", {}).get("max_consecutive_failures", 10)
    consecutive = getattr(self, "consecutive_alignment_failures", 0)
    last_status = self.current_state.get("status")

    if consecutive >= max_failures:
        pipeline_status = "STALE"
    elif last_status in ("alignment_failed", "inference_failed"):
        pipeline_status = "FAILED"
    elif consecutive > 0:
        pipeline_status = "DEGRADED"
    else:
        pipeline_status = "OK"

    self.current_state["pipeline_status"] = pipeline_status
    self.current_state["consecutive_alignment_failures"] = consecutive

    # Age of last successful reading
    last_pub_ts = self.current_state.get("last_published_timestamp")
    if last_pub_ts:
        try:
            ts = datetime.fromisoformat(last_pub_ts) if isinstance(last_pub_ts, str) else last_pub_ts
            self.current_state["last_valid_reading_age_seconds"] = int(
                (datetime.now() - ts).total_seconds()
            )
        except (ValueError, TypeError):
            self.current_state["last_valid_reading_age_seconds"] = None
    else:
        self.current_state["last_valid_reading_age_seconds"] = None

    # Suppress "live" rendering when pipeline isn't healthy
    self.current_state["is_live"] = pipeline_status == "OK"
```

Wire it: at the end of `process_reading()` (success and failure paths) and at the start of any handler that returns the status fragment.

- [ ] **Step 3.4: Update the status fragment template**

In `watermeter/templates/status_fragment.html`, near the top of the rendered body (before the existing total-value section), insert:

```html
{# --- Pipeline status banner (Issue #2) --- #}
{% set pstatus = current_state.pipeline_status or 'OK' %}
{% set pclass = {
    'OK': 'pipeline-ok',
    'DEGRADED': 'pipeline-degraded',
    'FAILED': 'pipeline-failed',
    'STALE': 'pipeline-stale'
  }[pstatus] %}
<div class="pipeline-status-badge {{ pclass }}">
  <span class="pipeline-status-dot"></span>
  <strong>{{ pstatus }}</strong>
  {% if current_state.last_valid_reading_age_seconds is not none %}
    {% set s = current_state.last_valid_reading_age_seconds %}
    {% if s < 60 %}
      <span>Letztes gültiges Reading: vor {{ s }}s</span>
    {% elif s < 3600 %}
      <span>Letztes gültiges Reading: vor {{ (s // 60) }} Min</span>
    {% else %}
      <span>Letztes gültiges Reading: vor {{ (s // 3600) }}h {{ ((s % 3600) // 60) }} Min</span>
    {% endif %}
  {% else %}
    <span>Noch kein gültiges Reading</span>
  {% endif %}
  {% if current_state.consecutive_alignment_failures and current_state.consecutive_alignment_failures > 0 %}
    <span class="failure-counter">{{ current_state.consecutive_alignment_failures }} consecutive failures</span>
  {% endif %}
  {% if current_state.last_alignment_error %}
    <span class="failure-reason">({{ current_state.last_alignment_error }})</span>
  {% endif %}
</div>
```

Then locate the existing total-value section and gate the "live" class:

```html
{# Was: <div class="total-value-live">{{ current_state.total_value }}</div> #}
{# Becomes: #}
<div class="total-value{% if current_state.is_live %} total-value-live{% endif %}{% if not current_state.is_live %} total-value-stale{% endif %}">
  {{ "%.4f" | format(current_state.total_value) if current_state.total_value is not none else "—" }}
  <span class="unit">{{ current_state.unit or 'm³' }}</span>
  {% if not current_state.is_live %}
    <span class="stale-marker">stale</span>
  {% endif %}
</div>
```

- [ ] **Step 3.5: Add CSS for the four pipeline statuses**

Append to `watermeter/static/style.css`:

```css
/* --- Pipeline status banner (Issue #2) --- */
.pipeline-status-badge {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.95rem;
  margin-bottom: 1rem;
  border: 1px solid transparent;
}
.pipeline-status-badge .pipeline-status-dot {
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  flex-shrink: 0;
}
.pipeline-status-badge.pipeline-ok        { background: #d4edda; color: #155724; border-color: #c3e6cb; }
.pipeline-status-badge.pipeline-ok        .pipeline-status-dot { background: #28a745; }
.pipeline-status-badge.pipeline-degraded  { background: #fff3cd; color: #856404; border-color: #ffeaa7; }
.pipeline-status-badge.pipeline-degraded  .pipeline-status-dot { background: #ffc107; }
.pipeline-status-badge.pipeline-failed    { background: #f8d7da; color: #721c24; border-color: #f5c6cb; }
.pipeline-status-badge.pipeline-failed    .pipeline-status-dot { background: #dc3545; }
.pipeline-status-badge.pipeline-stale     { background: #e2e3e5; color: #383d41; border-color: #d6d8db; }
.pipeline-status-badge.pipeline-stale     .pipeline-status-dot { background: #6c757d; }

.pipeline-status-badge .failure-counter   { font-weight: 600; }
.pipeline-status-badge .failure-reason    { font-style: italic; opacity: 0.8; }

.total-value-stale .stale-marker {
  margin-left: 0.5rem;
  font-size: 0.7em;
  color: #6c757d;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

- [ ] **Step 3.6: Run the template test, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_status_fragment.py -v
```

Expected: 4 PASSED.

- [ ] **Step 3.7: Smoke-test in the debug container**

Per CLAUDE.md, port 8002 = `watermeter-dashboard-debug`. Don't touch port 8001.

```bash
./debug.sh --detach
sleep 6
curl -s http://localhost:8002/api/status/html | grep pipeline-status-badge
```

Expected: stdout contains `pipeline-status-badge pipeline-ok` (or similar — at minimum the new badge HTML appears).

- [ ] **Step 3.8: Lint + commit**

```bash
uvx black --line-length 120 watermeter_service.py tests/unit/test_status_fragment.py
git add watermeter_service.py watermeter/templates/status_fragment.html watermeter/static/style.css tests/unit/test_status_fragment.py
git commit -m "claude: surface pipeline_status (OK/DEGRADED/FAILED/STALE) on dashboard"
```

---

## Task 4: Consecutive-Failure Handling Policy (Issue #5)

**Files:**
- Modify: `config.yaml` — add `alignment.max_consecutive_failures` (default 10).
- Modify: `watermeter_service.py` — track `consecutive_alignment_failures`, trigger MQTT notification when it crosses the threshold, reset on first success.
- Modify: `watermeter/mqtt_publisher.py` — extend `_HA_ENTITIES` with a `pipeline_status` enum entity (states `OK`/`DEGRADED`/`FAILED`/`STALE`) and a `consecutive_alignment_failures` numeric entity.
- Create: `tests/unit/test_consecutive_failures.py` — simulate N failures, assert STALE state + notification call.

The "notification" channel is the existing MQTT/HA bus. Per the codebase report, MQTT is already wired and a `consecutive_rejections` HA entity exists. We add `consecutive_alignment_failures` and `pipeline_status` as new HA-discovered entities so any HA automation (e.g. push notification on `STALE`) is one user-side template config away — no new infra here.

- [ ] **Step 4.1: Write the failing test**

Create `tests/unit/test_consecutive_failures.py`:

```python
"""Tests for consecutive-failure tracking and STALE state notification."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_failing_service(*, max_failures=10):
    from watermeter.image_pipeline import AlignmentResult
    from watermeter_service import WatermeterService

    svc = object.__new__(WatermeterService)
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {"max_consecutive_failures": max_failures},
    }
    svc.current_state = {"status": "idle", "warnings": []}
    svc.previous_value = None
    svc.consecutive_rejections = 0
    svc.consecutive_alignment_failures = 0
    svc._failure_store = MagicMock()

    fail_align = AlignmentResult(
        success=False,
        error_reason="low_confidence",
        failed_marker=1,
        marker_confidences=[0.31, 0.85],
    )
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(None, fail_align))
    svc.fetch_whole_image = AsyncMock(return_value=b"fake")
    svc.publish_to_mqtt = AsyncMock()
    svc.run_inference = AsyncMock(side_effect=AssertionError("must not be called"))
    return svc


def test_consecutive_failures_counter_increments():
    svc = make_failing_service()
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 3
    assert svc.current_state["pipeline_status"] == "FAILED"


def test_state_transitions_to_stale_at_threshold():
    svc = make_failing_service(max_failures=5)
    for _ in range(5):
        asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 5
    assert svc.current_state["pipeline_status"] == "STALE"


def test_stale_triggers_mqtt_notification_once():
    svc = make_failing_service(max_failures=3)
    svc._notify_stale = MagicMock()
    for _ in range(5):
        asyncio.run(svc.process_reading())
    # Notification fires exactly once on the boundary, not on every subsequent failure
    assert svc._notify_stale.call_count == 1


def test_first_success_resets_counter_and_clears_stale():
    from watermeter.image_pipeline import AlignmentResult

    svc = make_failing_service(max_failures=3)
    for _ in range(4):
        asyncio.run(svc.process_reading())
    assert svc.current_state["pipeline_status"] == "STALE"

    # Now flip the pipeline to succeed
    success_align = AlignmentResult(
        success=True,
        image=MagicMock(),
        marker_confidences=[0.95, 0.91],
    )
    fake_images = {"d1": (b"...", "digits")}
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(fake_images, success_align))
    svc.run_inference = AsyncMock(
        return_value={"d1": {"id": "d1", "class": 7, "confidence": 0.99, "model": "digits", "image_bytes": b"..."}}
    )
    svc.calculate_total = MagicMock(return_value=(1.234, [7]))
    svc.check_consistency = MagicMock(return_value=[])
    svc.validate_plausibility = MagicMock(return_value=(True, []))

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 0
    assert svc.current_state["pipeline_status"] in ("OK", "DEGRADED")
```

- [ ] **Step 4.2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_consecutive_failures.py -v
```

Expected: FAIL — `_notify_stale` doesn't exist; counter logic isn't bound to threshold.

- [ ] **Step 4.3: Add `max_consecutive_failures` to `config.yaml`**

Inside the `alignment:` block from Task 1:

```yaml
alignment:
  marker_confidence_threshold: 0.5
  archive_raw_images: false
  archive_dir: "/data/raw_archive"
  # Pipeline transitions to STALE after this many consecutive alignment failures.
  # The MQTT pipeline_status entity goes to "STALE" so HA automations can alert.
  max_consecutive_failures: 10
```

- [ ] **Step 4.4: Implement the threshold + notification logic**

In `watermeter_service.py`, extend the alignment-failure branch in `process_reading` (added in Task 2) to handle the threshold-crossing edge:

```python
# Inside the `if not alignment.success:` branch in process_reading
self.consecutive_alignment_failures = getattr(self, "consecutive_alignment_failures", 0) + 1
max_fail = self.config.get("alignment", {}).get("max_consecutive_failures", 10)
self._failure_store.record_failure(
    reason=alignment.error_reason,
    stage="alignment",
    failed_marker=alignment.failed_marker,
    marker_confidences=alignment.marker_confidences,
)
self.current_state["status"] = "alignment_failed"
self.current_state["last_alignment_error"] = alignment.error_reason
self.current_state["last_alignment_timestamp"] = datetime.now().isoformat()
self._derive_pipeline_status()  # Computes STALE if >= threshold

# Edge-trigger notification: fire only when crossing into STALE for the first time
if (
    self.consecutive_alignment_failures >= max_fail
    and not getattr(self, "_stale_notified", False)
):
    self._notify_stale()
    self._stale_notified = True

logger.error(
    f"Reading discarded: alignment failed ({alignment.error_reason}), "
    f"consecutive_alignment_failures={self.consecutive_alignment_failures}, "
    f"pipeline_status={self.current_state['pipeline_status']}"
)
return {"status": "alignment_failed", "reason": alignment.error_reason}
```

Add the notification helper. It publishes a one-shot MQTT event to a dedicated topic so HA automations can pick it up:

```python
def _notify_stale(self) -> None:
    """One-shot notification when pipeline transitions into STALE.

    Publishes a JSON payload to the HA event topic. HA automations can
    forward this to push notifications, email, etc. The method is
    best-effort and never raises (we never want notification failure to
    block reading processing).
    """
    try:
        if self.mqtt_client is None:
            logger.error(
                f"STALE pipeline (no MQTT): consecutive_alignment_failures="
                f"{self.consecutive_alignment_failures}"
            )
            return
        ha_cfg = self.config.get("home_assistant", {})
        topic = ha_cfg.get("event_topic", f"{ha_cfg.get('publish_topic', 'watermeter')}/event")
        payload = {
            "event": "pipeline_stale",
            "consecutive_alignment_failures": self.consecutive_alignment_failures,
            "last_alignment_error": self.current_state.get("last_alignment_error"),
            "timestamp": datetime.now().isoformat(),
        }
        import json
        self.mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=False)
        logger.error(f"Pipeline STALE notification published to {topic}")
    except Exception as e:
        logger.error(f"Failed to publish STALE notification: {e}")
```

In the success path of `process_reading` (after a reading is accepted), reset the counter and clear the latch:

```python
# After validate_plausibility returns is_valid=True and state is updated:
self.consecutive_alignment_failures = 0
self._stale_notified = False
self._derive_pipeline_status()
```

- [ ] **Step 4.5: Add HA discovery entries for the new entities**

In `watermeter/mqtt_publisher.py`, append to the `_HA_ENTITIES` list (around lines 42–133):

```python
{
    "object_id": "pipeline_status",
    "name": "Pipeline Status",
    "component": "sensor",
    "icon": "mdi:pipe-valve",
    "device_class": "enum",
    "options": ["OK", "DEGRADED", "FAILED", "STALE"],
    "entity_category": "diagnostic",
},
{
    "object_id": "consecutive_alignment_failures",
    "name": "Consecutive Alignment Failures",
    "component": "sensor",
    "icon": "mdi:image-broken-variant",
    "state_class": "measurement",
    "entity_category": "diagnostic",
},
```

In the publish payload (`publish_to_mqtt`), add the new fields:

```python
payload = {
    # ... existing fields ...
    "pipeline_status": self.current_state.get("pipeline_status", "OK"),
    "consecutive_alignment_failures": getattr(self, "consecutive_alignment_failures", 0),
    "last_alignment_error": self.current_state.get("last_alignment_error"),
}
```

- [ ] **Step 4.6: Run all new tests, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_consecutive_failures.py tests/unit/test_service_fail_closed.py tests/unit/test_status_fragment.py -v
```

Expected: all PASS.

- [ ] **Step 4.7: Lint + commit**

```bash
uvx black --line-length 120 watermeter_service.py watermeter/mqtt_publisher.py tests/unit/test_consecutive_failures.py
git add config.yaml watermeter_service.py watermeter/mqtt_publisher.py tests/unit/test_consecutive_failures.py
git commit -m "claude: STALE pipeline state + MQTT notification at max_consecutive_failures"
```

---

## Task 5: Alignment Observability Metrics (Issue #4)

**Files:**
- Create: `watermeter/metrics.py` — in-process counters and rolling-window confidence sketches, persisted as JSON (`/data/metrics.json`).
- Modify: `watermeter_service.py` — record metrics on every reading (success counter, alignment-fail counter per marker, confidence values).
- Create: `watermeter/routes/metrics.py` — `GET /api/metrics` endpoint returning current metrics + 1h/24h failure rates.
- Modify: `watermeter/templates/status_fragment.html` — add a small "Pipeline Health" widget; the dashboard JS reads `/api/metrics` directly and renders into the widget using safe DOM construction.
- Modify: `watermeter/static/dashboard.js` — add a poll-and-render function (uses `textContent` and `createElement` — no `innerHTML`).
- Create: `tests/unit/test_metrics.py` — unit-tests for the metrics module.

We deliberately don't add Prometheus — no `prometheus_client` dep in the project today and the report confirms there's no scraper. JSON-on-disk + a single `/api/metrics` endpoint is the lightest path that gives us 1h/24h failure-rate aggregations for the dashboard widget.

- [ ] **Step 5.1: Write the failing metrics-module test**

Create `tests/unit/test_metrics.py`:

```python
"""Unit tests for the pipeline metrics module."""
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from watermeter.metrics import PipelineMetrics


def test_counters_start_at_zero(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    snap = m.snapshot()
    assert snap["readings_total"] == {"ok": 0, "alignment_failed": 0, "inference_failed": 0}
    assert snap["marker_detection_failures_total"] == {"M1": 0, "M2": 0}


def test_record_reading_increments_status_counter(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    m.record_reading(status="ok")
    m.record_reading(status="ok")
    m.record_reading(status="alignment_failed")
    snap = m.snapshot()
    assert snap["readings_total"]["ok"] == 2
    assert snap["readings_total"]["alignment_failed"] == 1


def test_record_marker_failure_per_marker(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    m.record_marker_failure(marker_index=1)
    m.record_marker_failure(marker_index=2)
    m.record_marker_failure(marker_index=1)
    snap = m.snapshot()
    assert snap["marker_detection_failures_total"]["M1"] == 2
    assert snap["marker_detection_failures_total"]["M2"] == 1


def test_record_marker_confidence_rolling_median(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    for c in [0.7, 0.8, 0.9, 0.95, 0.6]:
        m.record_marker_confidence(marker_index=1, confidence=c)
    snap = m.snapshot()
    assert snap["marker_match_confidence"]["M1"]["count"] == 5
    assert snap["marker_match_confidence"]["M1"]["median"] == 0.8


def test_failure_rate_window(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    now = datetime.now()
    # 4 successes + 1 failure in the last hour
    m._record_event_at(now - timedelta(minutes=10), status="ok")
    m._record_event_at(now - timedelta(minutes=20), status="ok")
    m._record_event_at(now - timedelta(minutes=30), status="ok")
    m._record_event_at(now - timedelta(minutes=40), status="ok")
    m._record_event_at(now - timedelta(minutes=50), status="alignment_failed")
    # Old events outside the 1h window must not count toward 1h rate
    m._record_event_at(now - timedelta(hours=3), status="alignment_failed")
    snap = m.snapshot()
    assert snap["failure_rate_1h"] == pytest.approx(1 / 5)
    assert snap["failure_rate_24h"] == pytest.approx(2 / 6)


def test_persist_across_instances(tmp_path):
    path = tmp_path / "metrics.json"
    m = PipelineMetrics(path)
    m.record_reading(status="ok")
    m.record_marker_confidence(marker_index=2, confidence=0.91)
    m2 = PipelineMetrics(path)
    snap = m2.snapshot()
    assert snap["readings_total"]["ok"] == 1
    assert snap["marker_match_confidence"]["M2"]["count"] == 1
```

- [ ] **Step 5.2: Run the test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_metrics.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'watermeter.metrics'`.

- [ ] **Step 5.3: Implement `watermeter/metrics.py`**

```python
"""Pipeline observability metrics, persisted as JSON.

No new dependencies (no prometheus_client). Designed for a single-instance
service where the dashboard reads a snapshot via /api/metrics every few
seconds. All writes are atomic (tmp + rename).
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, List, Optional


class PipelineMetrics:
    ROLLING_CONFIDENCE_WINDOW = 100  # last 100 samples per marker
    EVENT_RETENTION_HOURS = 25  # keep 25h of events for 24h-rate calculations

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._load()
        self._readings_total: Dict[str, int] = data.get(
            "readings_total", {"ok": 0, "alignment_failed": 0, "inference_failed": 0}
        )
        self._marker_failures_total: Dict[str, int] = data.get(
            "marker_detection_failures_total", {"M1": 0, "M2": 0}
        )
        self._confidence_samples: Dict[str, Deque[float]] = {
            "M1": deque(data.get("confidence_samples", {}).get("M1", []), maxlen=self.ROLLING_CONFIDENCE_WINDOW),
            "M2": deque(data.get("confidence_samples", {}).get("M2", []), maxlen=self.ROLLING_CONFIDENCE_WINDOW),
        }
        # Events: list of {ts: iso, status: str}
        self._events: List[dict] = data.get("events", [])
        self._prune_old_events()

    # --- Recorders ---
    def record_reading(self, *, status: str) -> None:
        if status not in self._readings_total:
            return
        self._readings_total[status] += 1
        self._record_event_at(datetime.now(), status=status)
        self._save()

    def record_marker_failure(self, *, marker_index: int) -> None:
        key = f"M{marker_index}"
        if key not in self._marker_failures_total:
            return
        self._marker_failures_total[key] += 1
        self._save()

    def record_marker_confidence(self, *, marker_index: int, confidence: float) -> None:
        key = f"M{marker_index}"
        if key not in self._confidence_samples:
            return
        self._confidence_samples[key].append(float(confidence))
        self._save()

    def _record_event_at(self, ts: datetime, *, status: str) -> None:
        """Internal: append an event with a specific timestamp (used by tests)."""
        self._events.append({"ts": ts.isoformat(), "status": status})
        self._prune_old_events()

    # --- Snapshot ---
    def snapshot(self) -> dict:
        confidence_summary = {}
        for key, samples in self._confidence_samples.items():
            samples_list = list(samples)
            confidence_summary[key] = {
                "count": len(samples_list),
                "median": statistics.median(samples_list) if samples_list else None,
                "min": min(samples_list) if samples_list else None,
                "max": max(samples_list) if samples_list else None,
                "mean": statistics.fmean(samples_list) if samples_list else None,
            }
        return {
            "readings_total": dict(self._readings_total),
            "marker_detection_failures_total": dict(self._marker_failures_total),
            "marker_match_confidence": confidence_summary,
            "failure_rate_1h": self._failure_rate(timedelta(hours=1)),
            "failure_rate_24h": self._failure_rate(timedelta(hours=24)),
            "snapshot_timestamp": datetime.now().isoformat(),
        }

    # --- Internal ---
    def _failure_rate(self, window: timedelta) -> Optional[float]:
        cutoff = datetime.now() - window
        in_window = [
            e for e in self._events if datetime.fromisoformat(e["ts"]) >= cutoff
        ]
        if not in_window:
            return 0.0
        failures = sum(1 for e in in_window if e["status"] != "ok")
        return failures / len(in_window)

    def _prune_old_events(self) -> None:
        cutoff = datetime.now() - timedelta(hours=self.EVENT_RETENTION_HOURS)
        self._events = [e for e in self._events if datetime.fromisoformat(e["ts"]) >= cutoff]

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        data = {
            "readings_total": self._readings_total,
            "marker_detection_failures_total": self._marker_failures_total,
            "confidence_samples": {k: list(v) for k, v in self._confidence_samples.items()},
            "events": self._events,
        }
        fd, tmp_path = tempfile.mkstemp(dir=self.file_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self.file_path)
```

- [ ] **Step 5.4: Run the metrics test, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_metrics.py -v
```

Expected: 6 PASSED.

- [ ] **Step 5.5: Wire metrics into the service**

In `watermeter_service.py`, instantiate `PipelineMetrics` in `__init__`:

```python
from watermeter.metrics import PipelineMetrics

# Inside __init__, near FailureStore:
state_dir = Path(self.config.get("state", {}).get("dir", "/data"))
self._metrics = PipelineMetrics(state_dir / "metrics.json")
```

In `process_reading`'s alignment-failure branch (added Task 2):

```python
# After self._failure_store.record_failure(...)
if alignment.failed_marker:
    self._metrics.record_marker_failure(marker_index=alignment.failed_marker)
for i, conf in enumerate(alignment.marker_confidences):
    self._metrics.record_marker_confidence(marker_index=i + 1, confidence=conf)
self._metrics.record_reading(status="alignment_failed")
```

In `process_reading`'s success branch (after a reading is fully published):

```python
# Record marker confidences from the successful alignment too
for i, conf in enumerate(alignment.marker_confidences):
    self._metrics.record_marker_confidence(marker_index=i + 1, confidence=conf)
self._metrics.record_reading(status="ok")
```

In any inference-failure path (existing exception handlers in `run_inference`/`process_reading`):

```python
self._metrics.record_reading(status="inference_failed")
```

- [ ] **Step 5.6: Add the `/api/metrics` route**

Create `watermeter/routes/metrics.py`:

```python
"""GET /api/metrics — JSON snapshot of pipeline metrics."""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/metrics")
async def get_metrics(request: Request) -> dict:
    """Returns the current PipelineMetrics snapshot."""
    service = request.app.state.service
    return service._metrics.snapshot()
```

Register the router. Find where existing routes are mounted in `watermeter/app.py` (or wherever the FastAPI app is constructed) and add:

```python
from watermeter.routes import metrics as metrics_routes
app.include_router(metrics_routes.router)
```

- [ ] **Step 5.7: Add the dashboard widget markup**

In `watermeter/templates/status_fragment.html`, near the bottom (after warnings, before predictions grid), add:

```html
{# --- Pipeline Health Widget (Issue #4) --- #}
<details class="pipeline-health" id="pipeline-health">
  <summary>📊 Pipeline Health</summary>
  <div class="health-body" id="pipeline-health-body" data-metrics-url="/api/metrics">
    <p class="hint">Loading metrics…</p>
  </div>
</details>
```

We deliberately do NOT use HTMX `hx-swap` here — the metrics endpoint returns JSON, and we want to render structured rows from it without any HTML interpolation. Polling + render is done by JS (next step) using safe DOM construction (no `innerHTML`).

- [ ] **Step 5.8: Add the safe-DOM JS poller**

Append to `watermeter/static/dashboard.js`:

```javascript
// --- Pipeline Health metrics poller (Issue #4) ---
// Polls /api/metrics every 30s, renders into #pipeline-health-body using
// textContent + createElement only. No innerHTML, no template strings into DOM.
(function() {
  const POLL_INTERVAL_MS = 30000;

  function makeRow(label, value) {
    const row = document.createElement('div');
    row.className = 'metric';
    const labelEl = document.createElement('span');
    labelEl.textContent = label;
    const valueEl = document.createElement('strong');
    valueEl.textContent = value;
    row.appendChild(labelEl);
    row.appendChild(valueEl);
    return row;
  }

  function fmtPct(x) {
    if (x === null || x === undefined) return '—';
    return (x * 100).toFixed(1) + '%';
  }
  function fmtFloat(x, digits) {
    if (x === null || x === undefined) return '—';
    return Number(x).toFixed(digits);
  }

  function renderMetrics(body, data) {
    // Clear via DOM, not innerHTML
    while (body.firstChild) body.removeChild(body.firstChild);

    const okCount = data.readings_total.ok;
    const failCount = data.readings_total.alignment_failed + data.readings_total.inference_failed;
    body.appendChild(makeRow('Readings OK / Failed', okCount + ' / ' + failCount));
    body.appendChild(makeRow('Failure rate (1h)', fmtPct(data.failure_rate_1h)));
    body.appendChild(makeRow('Failure rate (24h)', fmtPct(data.failure_rate_24h)));

    ['M1', 'M2'].forEach(function(m) {
      const c = data.marker_match_confidence[m] || { count: 0 };
      if (c.count > 0) {
        body.appendChild(makeRow(
          m + ' confidence (median / min)',
          fmtFloat(c.median, 3) + ' / ' + fmtFloat(c.min, 3)
        ));
      }
      body.appendChild(makeRow(
        m + ' failures total',
        String(data.marker_detection_failures_total[m] || 0)
      ));
    });
  }

  function pollOnce() {
    const body = document.getElementById('pipeline-health-body');
    if (!body) return;
    const url = body.dataset.metricsUrl || '/api/metrics';
    fetch(url, { credentials: 'same-origin' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (data) renderMetrics(body, data);
      })
      .catch(function() { /* silent — keep last good render */ });
  }

  // Re-attach poller after every HTMX swap of the status fragment, since the
  // <details id="pipeline-health"> element gets replaced.
  function startPolling() {
    pollOnce();
    if (window.__pipelineHealthInterval) clearInterval(window.__pipelineHealthInterval);
    window.__pipelineHealthInterval = setInterval(pollOnce, POLL_INTERVAL_MS);
  }

  document.addEventListener('DOMContentLoaded', startPolling);
  document.body.addEventListener('htmx:afterSwap', startPolling);
})();
```

The key safety properties: every value written into the DOM goes through `textContent`, which the browser treats as plain text — so even if a future `/api/metrics` extension included an attacker-controlled string, it could not introduce HTML or script. There is no `innerHTML` and no string-to-HTML interpolation.

- [ ] **Step 5.9: Smoke-test the metrics endpoint**

```bash
./debug.sh --detach
sleep 6
curl -s http://localhost:8002/api/metrics | python3 -m json.tool
```

Expected: JSON with `readings_total`, `marker_detection_failures_total`, `marker_match_confidence`, `failure_rate_1h`, `failure_rate_24h` keys.

- [ ] **Step 5.10: Lint + commit**

```bash
uvx black --line-length 120 watermeter/metrics.py watermeter_service.py watermeter/routes/metrics.py tests/unit/test_metrics.py
uvx ruff check watermeter/metrics.py watermeter/routes/metrics.py
git add watermeter/metrics.py watermeter_service.py watermeter/routes/metrics.py watermeter/templates/status_fragment.html watermeter/static/dashboard.js tests/unit/test_metrics.py
git commit -m "claude: pipeline metrics endpoint + dashboard health widget"
```

---

## Task 6: Real Camera Calibration (Issue #6) — gated on metrics evidence

**Status flag:** Only execute this task **after Task 5 has been collecting data for ≥7 days** AND the metrics widget shows the median marker confidence is meaningfully below 0.85 (suggesting residual lens distortion is hurting matching). If the deployment is matching cleanly, this is YAGNI — skip and move on to Task 7.

**Files:**
- Create: `scripts/calibrate_camera.py` — runs `cv2.findChessboardCorners` + `cv2.calibrateCamera` against a directory of checkerboard images, writes `/data/camera_calibration.json`.
- Modify: `watermeter/image_pipeline.py` — `apply_fisheye_correction` (lines 20–43) reads full distortion model from config when present, falls back to single-k1 behaviour for back-compat.
- Modify: `config.yaml` — add `detection.lens_calibration_path` (optional).
- Create: `tests/unit/test_calibration_loading.py` — assert that a JSON calibration file is correctly applied; fall-back to k1-only when the file is missing.

- [ ] **Step 6.1: Write the calibration script**

Create `scripts/calibrate_camera.py`:

```python
"""Run cv2.calibrateCamera against an 8x6 inner-corner checkerboard pattern.

Usage:
    .venv/bin/python scripts/calibrate_camera.py \
        --images-dir /data/calibration_images/ \
        --output /data/camera_calibration.json \
        --pattern 8x6 \
        --square-size 25.0   # mm (used for re-projection error reporting only)

Writes JSON: {"camera_matrix": 3x3, "dist_coeffs": [k1,k2,p1,p2,k3], "image_size": [w,h], "rms": float}.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--pattern", default="8x6", help="inner corners e.g. 8x6")
    ap.add_argument("--square-size", default=1.0, type=float)
    args = ap.parse_args()

    cols, rows = (int(x) for x in args.pattern.split("x"))
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * args.square_size

    obj_points, img_points = [], []
    image_size = None
    used = 0

    for jpg in sorted(args.images_dir.glob("*.jpg")):
        img = cv2.imread(str(jpg))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if image_size is None:
            image_size = (gray.shape[1], gray.shape[0])
        ok, corners = cv2.findChessboardCorners(gray, (cols, rows))
        if not ok:
            print(f"[skip] {jpg.name}: corners not found")
            continue
        corners = cv2.cornerSubPix(
            gray,
            corners,
            (11, 11),
            (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001),
        )
        obj_points.append(objp.copy())
        img_points.append(corners)
        used += 1
        print(f"[ok]   {jpg.name}")

    if used < 10:
        print(f"Need at least 10 usable images, got {used}")
        return 2

    rms, camera_matrix, dist_coeffs, _, _ = cv2.calibrateCamera(
        obj_points, img_points, image_size, None, None
    )
    out = {
        "camera_matrix": camera_matrix.tolist(),
        "dist_coeffs": dist_coeffs.flatten().tolist(),
        "image_size": list(image_size),
        "rms": float(rms),
        "images_used": used,
    }
    args.output.write_text(json.dumps(out, indent=2))
    print(f"\nCalibration written to {args.output}\n  rms = {rms:.4f}\n  images = {used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6.2: Write the failing fall-back test**

Create `tests/unit/test_calibration_loading.py`:

```python
"""Lens-correction must accept a calibration JSON when provided, fall back to k1 otherwise."""
import json
from pathlib import Path

import numpy as np
import pytest

from watermeter.image_pipeline import ImagePipeline


def test_no_calibration_falls_back_to_k1(tmp_path):
    cfg = {"detection": {"fisheye_correction": -0.05}}
    ip = ImagePipeline(config=cfg)
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    out = ip._apply_lens_correction(img)  # New unified entry point
    assert out.shape == img.shape  # Smoke test: didn't crash


def test_calibration_file_overrides_k1(tmp_path):
    calib_path = tmp_path / "calib.json"
    calib_path.write_text(
        json.dumps(
            {
                "camera_matrix": [[600, 0, 320], [0, 600, 240], [0, 0, 1]],
                "dist_coeffs": [-0.3, 0.1, 0.001, 0.001, 0.0],
                "image_size": [640, 480],
                "rms": 0.5,
            }
        )
    )
    cfg = {
        "detection": {
            "fisheye_correction": 0.0,  # Should be ignored when calibration file present
            "lens_calibration_path": str(calib_path),
        }
    }
    ip = ImagePipeline(config=cfg)
    assert ip._lens_calibration is not None
    assert ip._lens_calibration["dist_coeffs"][0] == pytest.approx(-0.3)
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    out = ip._apply_lens_correction(img)
    assert out.shape == img.shape


def test_missing_calibration_file_silently_falls_back(tmp_path):
    cfg = {
        "detection": {
            "fisheye_correction": -0.1,
            "lens_calibration_path": str(tmp_path / "does_not_exist.json"),
        }
    }
    ip = ImagePipeline(config=cfg)
    assert ip._lens_calibration is None
```

- [ ] **Step 6.3: Run, verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_calibration_loading.py -v
```

Expected: FAIL — `_apply_lens_correction` and `_lens_calibration` don't exist.

- [ ] **Step 6.4: Implement the unified lens correction**

In `watermeter/image_pipeline.py`, add to `__init__`:

```python
# Lens calibration: prefer JSON file, fall back to single-k1 from config
calib_path = config.get("detection", {}).get("lens_calibration_path") if isinstance(config, dict) else None
self._lens_calibration: Optional[dict] = None
if calib_path:
    try:
        with open(calib_path, "r") as f:
            self._lens_calibration = json.load(f)
        logger.info(f"Loaded lens calibration from {calib_path}")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load lens calibration {calib_path}: {e}")
```

Add the unified method (and replace usages of `apply_fisheye_correction` inside `process_whole_image` with this call):

```python
def _apply_lens_correction(self, image: np.ndarray) -> np.ndarray:
    """Apply lens correction. Uses full calibration JSON if loaded, else single-k1 fallback."""
    if self._lens_calibration is not None:
        h, w = image.shape[:2]
        camera_matrix = np.array(self._lens_calibration["camera_matrix"], dtype=np.float64)
        dist_coeffs = np.array(self._lens_calibration["dist_coeffs"], dtype=np.float64)
        new_cam, _ = cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w, h), alpha=1, newImgSize=(w, h))
        return cv2.undistort(image, camera_matrix, dist_coeffs, None, new_cam)

    # Back-compat single-k1 path (existing code)
    k1 = self.config.get("detection", {}).get("fisheye_correction", 0)
    return apply_fisheye_correction(image, k1)
```

Don't delete the existing `apply_fisheye_correction` module-level function — back-compat for any external callers and the existing tests reference it.

- [ ] **Step 6.5: Run, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_calibration_loading.py -v
```

Expected: 3 PASSED.

- [ ] **Step 6.6: Update `config.yaml`**

```yaml
detection:
  # ... existing fields ...
  fisheye_correction: -0.05  # Single-k1 fallback when no calibration file is available
  lens_calibration_path: null  # Set to path of camera_calibration.json from scripts/calibrate_camera.py
```

- [ ] **Step 6.7: Lint + commit**

```bash
uvx black --line-length 120 watermeter/image_pipeline.py scripts/calibrate_camera.py tests/unit/test_calibration_loading.py
git add watermeter/image_pipeline.py scripts/calibrate_camera.py config.yaml tests/unit/test_calibration_loading.py
git commit -m "claude: optional full camera calibration JSON, k1 remains as fallback"
```

---

## Task 7: PERF-6 — Camera-Matrix Caching (Issue #7)

**Files:**
- Modify: `watermeter/image_pipeline.py` — cache `(camera_matrix, dist_coeffs, new_camera_matrix)` per `(image_w, image_h)`. Invalidate on size change.
- Create: `tests/unit/test_camera_matrix_cache.py` — assert `cv2.getOptimalNewCameraMatrix` is called once per pipeline lifetime when frame size is constant.

- [ ] **Step 7.1: Write the failing test**

Create `tests/unit/test_camera_matrix_cache.py`:

```python
"""Camera matrix derived from calibration must be cached across frames."""
import json
from unittest.mock import patch

import numpy as np
import pytest

from watermeter.image_pipeline import ImagePipeline


def test_get_optimal_called_once_for_constant_size(tmp_path):
    calib = tmp_path / "calib.json"
    calib.write_text(
        json.dumps(
            {
                "camera_matrix": [[600, 0, 320], [0, 600, 240], [0, 0, 1]],
                "dist_coeffs": [-0.3, 0.1, 0.001, 0.001, 0.0],
                "image_size": [640, 480],
                "rms": 0.5,
            }
        )
    )
    ip = ImagePipeline(config={"detection": {"lens_calibration_path": str(calib)}})
    img = np.zeros((480, 640, 3), dtype=np.uint8)

    with patch("cv2.getOptimalNewCameraMatrix", wraps=__import__("cv2").getOptimalNewCameraMatrix) as spy:
        ip._apply_lens_correction(img)
        ip._apply_lens_correction(img)
        ip._apply_lens_correction(img)
    assert spy.call_count == 1


def test_cache_invalidates_on_size_change(tmp_path):
    calib = tmp_path / "calib.json"
    calib.write_text(
        json.dumps(
            {
                "camera_matrix": [[600, 0, 320], [0, 600, 240], [0, 0, 1]],
                "dist_coeffs": [-0.3, 0.1, 0.001, 0.001, 0.0],
                "image_size": [640, 480],
                "rms": 0.5,
            }
        )
    )
    ip = ImagePipeline(config={"detection": {"lens_calibration_path": str(calib)}})
    big = np.zeros((480, 640, 3), dtype=np.uint8)
    small = np.zeros((240, 320, 3), dtype=np.uint8)

    with patch("cv2.getOptimalNewCameraMatrix", wraps=__import__("cv2").getOptimalNewCameraMatrix) as spy:
        ip._apply_lens_correction(big)
        ip._apply_lens_correction(big)
        ip._apply_lens_correction(small)
        ip._apply_lens_correction(small)
    assert spy.call_count == 2
```

- [ ] **Step 7.2: Run, verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_camera_matrix_cache.py -v
```

Expected: FAIL — `getOptimalNewCameraMatrix` is called once per frame.

- [ ] **Step 7.3: Add the cache**

In `ImagePipeline.__init__`:

```python
self._lens_cache: Optional[dict] = None  # {"size": (w,h), "cm": ..., "dc": ..., "new_cm": ...}
```

Refactor `_apply_lens_correction` (added in Task 6) to read the cache:

```python
def _apply_lens_correction(self, image: np.ndarray) -> np.ndarray:
    if self._lens_calibration is not None:
        h, w = image.shape[:2]
        cache = self._lens_cache
        if cache is None or cache["size"] != (w, h):
            cm = np.array(self._lens_calibration["camera_matrix"], dtype=np.float64)
            dc = np.array(self._lens_calibration["dist_coeffs"], dtype=np.float64)
            new_cm, _ = cv2.getOptimalNewCameraMatrix(cm, dc, (w, h), alpha=1, newImgSize=(w, h))
            self._lens_cache = {"size": (w, h), "cm": cm, "dc": dc, "new_cm": new_cm}
        c = self._lens_cache
        return cv2.undistort(image, c["cm"], c["dc"], None, c["new_cm"])

    k1 = self.config.get("detection", {}).get("fisheye_correction", 0)
    return apply_fisheye_correction(image, k1)
```

- [ ] **Step 7.4: Run, verify green**

```bash
.venv/bin/python -m pytest tests/unit/test_camera_matrix_cache.py -v
.venv/bin/python -m pytest tests/unit/test_calibration_loading.py -v
```

Expected: both files PASS.

- [ ] **Step 7.5: Lint + commit**

```bash
uvx black --line-length 120 watermeter/image_pipeline.py tests/unit/test_camera_matrix_cache.py
git add watermeter/image_pipeline.py tests/unit/test_camera_matrix_cache.py
git commit -m "claude: cache camera matrix per (w,h) in ImagePipeline"
```

---

## Final: Full Test Suite + Merge to claude/main

- [ ] **Step F.1: Run the full test suite**

```bash
.venv/bin/python -m pytest -v
```

Expected: green. Per the project memory, ~317+ tests should be passing; this milestone adds ~25–30 new tests.

- [ ] **Step F.2: Integration smoke-test against debug container**

```bash
./debug.sh --detach
sleep 8
.venv/bin/python -m pytest tests/integration/ --base-url=http://localhost:8002 -v
```

Expected: integration tests green. If the dashboard test asserts on the old status fragment, update its expectation to include the new pipeline-status badge.

- [ ] **Step F.3: Manual UI verification**

Per CLAUDE.md, frontend changes need browser verification. Dispatch the `tester` subagent to use Playwright against `http://localhost:8002`:

- Confirm the pipeline-status badge renders OK on a healthy reading
- Force an alignment failure (e.g. point a black image at the configured marker positions, or temporarily remove the marker template files) and confirm badge transitions to FAILED + counter shows
- Confirm the metrics widget loads and shows non-zero counts after a few readings

- [ ] **Step F.4: Merge feature branch into `claude/main`**

```bash
git checkout claude/main
git merge --no-ff claude/robustness-milestone -m "claude: merge robustness milestone (#1-#7)"
git push origin claude/main
git branch -d claude/robustness-milestone
```

- [ ] **Step F.5: (User-triggered) PR to `main`**

When the user signals release, open the PR via Gitea API per CLAUDE.md (token at `/home/claude/.config/gitea/token`).

---

## Notes for Reviewers

- **Type consistency:** `AlignmentResult` is the single source of truth — defined in Task 2, consumed by Tasks 2/3/4/5. `pipeline_status` enum (`OK`/`DEGRADED`/`FAILED`/`STALE`) is set in `_derive_pipeline_status` (Task 3) and consumed by Tasks 3/4/5/UI/MQTT.
- **No DB migration:** All new persistence is JSON-on-disk via `FailureStore` and `PipelineMetrics`, both modeled after the existing `StateStore` (atomic tmp-rename writes, schema-tolerant load).
- **No new runtime deps:** Everything uses cv2/paho/ruamel/jinja2/htmx that the project already pulls in.
- **Production safety:** No task touches port 8001 or the prod container. All UI smoke-tests target port 8002 (`./debug.sh`).
- **Don't introduce a "last known good affine" fallback:** explicitly forbidden by Issue #1 acceptance — the test in Task 2 enforces this by setting `result.image = None` on failure.
- **No `innerHTML` in dashboard JS:** the metrics widget is rendered via `document.createElement` + `textContent` only, to keep the XSS surface minimal even though `/api/metrics` is a same-origin endpoint.

## Notes on Skipped/Optional Work

- **Task 6 (Calibration)** is **gated**: only run after Task 5 metrics show median marker confidence < ~0.85. If the deployment is matching cleanly with the existing single-k1 correction, this is premature optimization.
- **Task 7 (Caching)** is opportunistic — bundled here because Task 6 already touches lens correction. If Task 6 is skipped, Task 7 is mostly a no-op (the legacy single-k1 path doesn't allocate the heavy `cv2.getOptimalNewCameraMatrix` matrix in the same way).
