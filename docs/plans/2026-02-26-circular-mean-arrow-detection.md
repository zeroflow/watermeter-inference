# Circular Mean Arrow Detection — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the 8-slice + bisection arrow detection algorithm with a distance-weighted circular mean of tip pixels — eliminates the cascade failure where a wrong initial slice pick sends bisection in the wrong direction.

**Architecture:** Instead of picking a winning sector and refining within it, compute the distance-weighted circular mean angle of all tip pixels in one pass. The circular mean naturally handles the 360° wrap-around and produces both a direction (the gauge value) and a concentration metric R (the confidence). No iterative refinement needed.

**Tech Stack:** NumPy circular statistics (atan2 of weighted sin/cos sums).

---

## Why This Change

The old algorithm has a fundamental flaw: if the 8-slice initial scan picks the wrong sector (arrow tail beats tip, or noise), all subsequent bisection refinement goes deeper in the wrong direction. There is no recovery mechanism.

The circular mean approach evaluates ALL tip pixels simultaneously. No single wrong pick can cascade. The result is the weighted average direction of all outermost colored pixels.

### The Math

```
angles_rad = gauge_angles_of_tip_pixels (in radians)
weights    = distance_from_center_of_tip_pixels

wx = sum(weights * cos(angles_rad))
wy = sum(weights * sin(angles_rad))

mean_angle = atan2(wy, wx) % 360°
value      = mean_angle / 360 * 10

R = sqrt(wx² + wy²) / sum(weights)   # resultant length, 0-1
# R ≈ 1.0 → all tip pixels point same direction (high confidence)
# R ≈ 0.0 → tip pixels spread around circle (low confidence)
```

### Config Impact

`bisection_iterations` is removed (no longer applicable). The `opencv_arrows` config becomes:

```yaml
opencv_arrows:
  hue_ranges: [[0, 15], [165, 180]]
  saturation_min: 50
  value_min: 50
```

---

## Task 1: Replace `_detect()` algorithm with circular mean

**Files:**
- Modify: `watermeter/opencv_arrows.py`
- Modify: `tests/unit/test_opencv_arrows.py`

### Step 1: Update the tests to remove bisection-specific tests and add circular mean tests

In `tests/unit/test_opencv_arrows.py`:

**Remove** `test_bisection_iterations_affects_precision` entirely (bisection no longer exists).

**Update** tests that pass `bisection_iterations=` to constructors — remove that parameter.

**Add** a new test for the confidence metric:

```python
def test_confidence_reflects_angular_concentration(self):
    """A clean arrow should have high confidence (R close to 1.0)."""
    import cv2
    from watermeter.opencv_arrows import OpenCVArrowDetector

    detector = OpenCVArrowDetector()
    img = self._make_arrow_image(3.0)
    _, buf = cv2.imencode(".jpg", img)
    result = detector.predict_from_bytes(buf.tobytes())
    assert result["class"] != "NaN"
    # A clean synthetic arrow should have high concentration
    assert result["confidence"] >= 0.8, f"Expected high confidence, got {result['confidence']}"
```

### Step 2: Run tests to verify the new test fails and updated tests fail

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: failures due to constructor changes and new test

### Step 3: Rewrite `_detect()` in `watermeter/opencv_arrows.py`

Replace the entire `_detect` method. The new implementation:

```python
def _detect(self, image_bgr: np.ndarray) -> tuple[float | None, float]:
    """Run detection via distance-weighted circular mean of tip pixels.

    Returns:
        (value, confidence) where value is 0.0-9.9 or None on failure.
        confidence is the resultant length R (0.0-1.0).
    """
    img_h, img_w = image_bgr.shape[:2]
    cx, cy = img_w // 2, img_h // 2

    # Color mask
    color_mask = detect_color_mask(
        image_bgr, self.hue_ranges, self.saturation_min, self.value_min
    )
    if np.count_nonzero(color_mask) == 0:
        return None, 0.0

    # Gauge angles for every pixel (0-360, 0 = top/12 o'clock)
    ys, xs = np.mgrid[0:img_h, 0:img_w]
    dx = (xs - cx).astype(np.float64)
    dy = (ys - cy).astype(np.float64)
    image_angles = np.degrees(np.arctan2(dy, dx))
    gauge_angles = (image_angles - _GAUGE_START_DEG) % 360.0

    # Distance from center
    dist = np.sqrt(dx**2 + dy**2)

    # Isolate tip pixels (outermost by distance)
    colored_pixels = color_mask > 0
    colored_dists = dist[colored_pixels]
    tip_cutoff = np.percentile(colored_dists, _TIP_PERCENTILE)
    tip_mask = colored_pixels & (dist >= tip_cutoff)

    # Distance-weighted circular mean of tip pixel angles
    tip_angles_rad = np.radians(gauge_angles[tip_mask])
    weights = dist[tip_mask]

    wx = np.sum(weights * np.cos(tip_angles_rad))
    wy = np.sum(weights * np.sin(tip_angles_rad))

    mean_angle_deg = np.degrees(np.arctan2(wy, wx)) % 360.0

    # Resultant length R as confidence (0-1)
    resultant = np.sqrt(wx**2 + wy**2)
    weight_sum = np.sum(weights)
    confidence = float(resultant / weight_sum) if weight_sum > 0 else 0.0

    # Convert angle to gauge value
    value = mean_angle_deg / _GAUGE_ARC_DEG * 10.0
    value = round(value, 1)
    value = max(0.0, min(9.9, value))

    return value, confidence
```

Also update `__init__` to remove `bisection_iterations`:

```python
def __init__(
    self,
    hue_ranges: list[list[int]] | None = None,
    saturation_min: int = _DEFAULT_SATURATION_MIN,
    value_min: int = _DEFAULT_VALUE_MIN,
):
    self.hue_ranges = hue_ranges if hue_ranges is not None else _DEFAULT_HUE_RANGES
    self.saturation_min = saturation_min
    self.value_min = value_min
```

Update `predict_from_bytes` — `_detect` now returns `(value, confidence)` directly:

```python
def predict_from_bytes(self, image_bytes: bytes) -> dict:
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        logger.warning("OpenCV arrow detector: failed to decode image")
        return {"class": "NaN", "confidence": 0.0}

    value, confidence = self._detect(image)
    if value is None:
        logger.warning("OpenCV arrow detector: no colored pixels found")
        return {"class": "NaN", "confidence": 0.0}

    return {"class": str(value), "confidence": confidence}
```

Remove module constants `_INITIAL_SLICES` (no longer used). Keep `_TIP_PERCENTILE`.

Update the module docstring to describe the new algorithm.

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: All PASS

### Step 5: Commit

```bash
git add watermeter/opencv_arrows.py tests/unit/test_opencv_arrows.py
git commit -m "claude: replace 8-slice+bisection with circular mean arrow detection"
```

---

## Task 2: Remove `bisection_iterations` from config, schema, and factory

**Files:**
- Modify: `watermeter/config_utils.py` (CONFIG_SCHEMA)
- Modify: `watermeter/inference.py` (`_create_arrows_backend`)
- Modify: `config.yaml`
- Modify: `tests/unit/test_opencv_arrows.py` (config and factory tests)

### Step 1: Update config tests

In `tests/unit/test_opencv_arrows.py`:

**Update `TestOpenCVArrowsConfig.test_schema_has_opencv_arrows`** — remove the `bisection_iterations` assertion.

**Update `TestOpenCVArrowsConfig.test_default_config_validates`** — remove `bisection_iterations` from the test config dict.

**Update `TestInferenceServiceOpenCVMode.test_opencv_mode_returns_detector`** — remove `bisection_iterations` from config and assertion.

**Update `TestInferenceServiceOpenCVMode.test_opencv_mode_uses_defaults`** — remove the `bisection_iterations` assertion.

### Step 2: Run tests to see failures

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: some tests fail because schema still has `bisection_iterations`

### Step 3: Remove from CONFIG_SCHEMA

In `watermeter/config_utils.py`, remove the `bisection_iterations` entry from the `opencv_arrows` properties in CONFIG_SCHEMA.

### Step 4: Remove from factory function

In `watermeter/inference.py`, in `_create_arrows_backend()`, remove the `bisection_iterations` line from the `OpenCVArrowDetector()` constructor call.

### Step 5: Remove from config.yaml

In `config.yaml`, remove the commented `#   bisection_iterations: 4` line.

### Step 6: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: All PASS

### Step 7: Run full test suite

Run: `.venv/bin/python -m pytest --tb=short -q`
Expected: All pass, no regressions

### Step 8: Commit

```bash
git add watermeter/config_utils.py watermeter/inference.py config.yaml tests/unit/test_opencv_arrows.py
git commit -m "claude: remove bisection_iterations config (no longer used by circular mean)"
```

---

## Task 3: Update codebase map

**Files:**
- Modify: `docs/codebase_map.md`

### Step 1: Update the `watermeter/opencv_arrows.py` entry

- Update the module description from "8-slice + bisection" to "circular mean"
- Remove `_INITIAL_SLICES` if it was listed
- Update `_detect` description from "8-slice + bisection detection algorithm" to "distance-weighted circular mean detection"
- Update line numbers if they shifted

### Step 2: Commit

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map for circular mean algorithm"
```
