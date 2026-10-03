# OpenCV Arrow Detection Mode — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an `inference.arrows_mode` config switch ("model" / "opencv") so arrows can be read via the existing ML model or via the OpenCV HSV+bisection algorithm from `detect_arrow.py`.

**Architecture:** A new `OpenCVArrowDetector` class in `watermeter/opencv_arrows.py` implements the same `predict_from_bytes()` / `predict_detailed_from_bytes()` interface as `Classifier`/`Regressor`. `InferenceService` instantiates it when `arrows_mode == "opencv"` instead of loading an OpenVINO model. Config exposes HSV color thresholds and bisection iteration count.

**Tech Stack:** OpenCV (already a dependency), NumPy, existing config/schema system.

---

## Context for the Implementer

### How Arrow Inference Currently Works

`InferenceService` (in `watermeter/inference.py`) holds `self._arrows_classifier` — either a `Classifier` (discrete softmax, 10 classes) or a `Regressor` (continuous sigmoid, 0.0–9.9). The choice is auto-detected from model metadata. Both expose:

- `predict_from_bytes(image_bytes) -> {"class": "3.0", "confidence": 0.87}`
- `predict_detailed_from_bytes(image_bytes, top_k) -> [{"class": ..., "confidence": ...}, ...]`

The new `OpenCVArrowDetector` must match this interface exactly.

### The OpenCV Algorithm (from `detect_arrow.py`)

1. HSV threshold to isolate colored arrow pixels
2. 85th-percentile distance cutoff to isolate arrow tip pixels
3. 8-slice initial scan (count tip pixels per 45° sector)
4. Bisection within the winning sector (distance-weighted pixel sums)
5. Returns `(value: 0.0–9.9, precision: float)`

### Config Design

```yaml
inference:
  arrows_mode: "model"          # "model" (default) or "opencv"
  opencv_arrows:
    hue_ranges: [[0, 15], [165, 180]]  # HSV hue ranges for arrow color (red default)
    saturation_min: 50                   # min HSV saturation (0-255)
    value_min: 50                        # min HSV value/brightness (0-255)
    bisection_iterations: 4              # refinement steps (4 = ~±0.04 precision)
```

### Key Files (read `docs/codebase_map.md` for full map)

| File | Role |
|------|------|
| `watermeter/inference.py` | `InferenceService`, `Classifier`, `Regressor`, `_detect_training_mode()` |
| `watermeter/config_utils.py` | `CONFIG_SCHEMA`, `validate_config()`, `get_data_collection_config()` |
| `config.yaml` | Production config |
| `config_debug/config.yaml` | Debug container config |
| `detect_arrow.py` | Standalone PoC to extract algorithm from |

---

## Task 1: Create `OpenCVArrowDetector` class with tests

**Files:**
- Create: `watermeter/opencv_arrows.py`
- Create: `tests/unit/test_opencv_arrows.py`

### Step 1: Write the failing tests

Create `tests/unit/test_opencv_arrows.py`:

```python
"""Tests for OpenCV-based arrow detection."""

import numpy as np
import pytest


class TestDetectRedMask:
    """Test HSV color thresholding for arrow pixel detection."""

    def test_pure_red_pixels_detected(self):
        """A solid red image should produce an all-white mask."""
        from watermeter.opencv_arrows import detect_color_mask

        # Pure red in BGR
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:, :] = (0, 0, 255)  # BGR red
        mask = detect_color_mask(image)
        assert mask.shape == (100, 100)
        assert np.all(mask > 0)

    def test_pure_blue_not_detected(self):
        """A blue image should produce an all-black mask with red thresholds."""
        from watermeter.opencv_arrows import detect_color_mask

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:, :] = (255, 0, 0)  # BGR blue
        mask = detect_color_mask(image)
        assert np.all(mask == 0)

    def test_custom_hue_ranges(self):
        """Custom hue ranges should detect different colors."""
        from watermeter.opencv_arrows import detect_color_mask

        # Create a green image (H ~60 in OpenCV HSV)
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:, :] = (0, 255, 0)  # BGR green
        # Default red thresholds should NOT detect green
        mask_red = detect_color_mask(image)
        assert np.all(mask_red == 0)
        # Custom green hue range should detect it
        mask_green = detect_color_mask(
            image, hue_ranges=[[35, 85]], saturation_min=50, value_min=50
        )
        assert np.all(mask_green > 0)

    def test_low_saturation_rejected(self):
        """Grayish pixels should be rejected even with correct hue."""
        from watermeter.opencv_arrows import detect_color_mask

        # Create a desaturated reddish image
        import cv2

        hsv = np.zeros((100, 100, 3), dtype=np.uint8)
        hsv[:, :] = (5, 20, 200)  # H=5 (red), S=20 (low), V=200
        image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        mask = detect_color_mask(image, saturation_min=50)
        assert np.all(mask == 0)


class TestOpenCVArrowDetector:
    """Test the full detector class."""

    def _make_arrow_image(self, value, size=200):
        """Create a synthetic gauge image with a red arrow at `value` (0-10).

        Draws a thin red wedge from center outward at the correct angle.
        """
        import cv2

        img = np.zeros((size, size, 3), dtype=np.uint8)
        img[:] = (200, 200, 200)  # gray background
        cx, cy = size // 2, size // 2
        radius = size // 2 - 10

        # Gauge angle: value 0 at top (12 o'clock = -90°), clockwise
        angle_deg = -90.0 + value * 36.0
        angle_rad = np.radians(angle_deg)

        # Draw a filled triangle (arrow) from center to tip
        tip_x = int(cx + radius * np.cos(angle_rad))
        tip_y = int(cy + radius * np.sin(angle_rad))

        # Perpendicular offset for arrow width
        perp_rad = angle_rad + np.pi / 2
        half_w = 8
        base1 = (
            int(cx + half_w * np.cos(perp_rad)),
            int(cy + half_w * np.sin(perp_rad)),
        )
        base2 = (
            int(cx - half_w * np.cos(perp_rad)),
            int(cy - half_w * np.sin(perp_rad)),
        )

        pts = np.array([(tip_x, tip_y), base1, base2], dtype=np.int32)
        cv2.fillConvexPoly(img, pts, (0, 0, 255))  # BGR red

        return img

    def test_predict_from_bytes_returns_correct_format(self):
        """predict_from_bytes must return {"class": str, "confidence": float}."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        detector = OpenCVArrowDetector()
        img = self._make_arrow_image(3.0)
        _, buf = cv2.imencode(".jpg", img)
        result = detector.predict_from_bytes(buf.tobytes())
        assert "class" in result
        assert "confidence" in result
        assert isinstance(result["class"], str)
        assert isinstance(result["confidence"], float)

    def test_predict_from_bytes_detects_value_3(self):
        """Arrow pointing at value 3 should be detected as ~3."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        detector = OpenCVArrowDetector(bisection_iterations=6)
        img = self._make_arrow_image(3.0)
        _, buf = cv2.imencode(".jpg", img)
        result = detector.predict_from_bytes(buf.tobytes())
        detected = float(result["class"])
        assert abs(detected - 3.0) <= 0.5, f"Expected ~3.0, got {detected}"

    def test_predict_from_bytes_detects_value_7(self):
        """Arrow pointing at value 7 should be detected as ~7."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        detector = OpenCVArrowDetector(bisection_iterations=6)
        img = self._make_arrow_image(7.0)
        _, buf = cv2.imencode(".jpg", img)
        result = detector.predict_from_bytes(buf.tobytes())
        detected = float(result["class"])
        assert abs(detected - 7.0) <= 0.5, f"Expected ~7.0, got {detected}"

    def test_predict_detailed_from_bytes(self):
        """predict_detailed_from_bytes returns a single-element list."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        detector = OpenCVArrowDetector()
        img = self._make_arrow_image(5.0)
        _, buf = cv2.imencode(".jpg", img)
        results = detector.predict_detailed_from_bytes(buf.tobytes(), top_k=3)
        assert isinstance(results, list)
        assert len(results) == 1
        assert "class" in results[0]
        assert "confidence" in results[0]

    def test_no_colored_pixels_returns_nan(self):
        """An image with no matching color pixels should return NaN/error."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        detector = OpenCVArrowDetector()
        # All-blue image — no red pixels
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[:] = (255, 0, 0)  # BGR blue
        _, buf = cv2.imencode(".jpg", img)
        result = detector.predict_from_bytes(buf.tobytes())
        assert result["class"] == "NaN"
        assert result["confidence"] == 0.0

    def test_custom_color_config(self):
        """Detector with custom hue ranges detects non-red arrows."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        # Green arrow image
        size = 200
        img = np.zeros((size, size, 3), dtype=np.uint8)
        img[:] = (200, 200, 200)
        cx, cy = size // 2, size // 2
        # Draw green arrow at value 5 (6 o'clock)
        angle_rad = np.radians(-90.0 + 5.0 * 36.0)
        radius = size // 2 - 10
        tip = (int(cx + radius * np.cos(angle_rad)),
               int(cy + radius * np.sin(angle_rad)))
        cv2.line(img, (cx, cy), tip, (0, 255, 0), 10)  # green line

        detector = OpenCVArrowDetector(
            hue_ranges=[[35, 85]], saturation_min=50, value_min=50,
            bisection_iterations=4,
        )
        _, buf = cv2.imencode(".jpg", img)
        result = detector.predict_from_bytes(buf.tobytes())
        assert result["class"] != "NaN"
        detected = float(result["class"])
        assert abs(detected - 5.0) <= 1.0, f"Expected ~5.0, got {detected}"

    def test_bisection_iterations_affects_precision(self):
        """More iterations should give equal or better precision."""
        import cv2
        from watermeter.opencv_arrows import OpenCVArrowDetector

        img = self._make_arrow_image(4.5)
        _, buf = cv2.imencode(".jpg", img)
        image_bytes = buf.tobytes()

        det_2 = OpenCVArrowDetector(bisection_iterations=2)
        det_8 = OpenCVArrowDetector(bisection_iterations=8)

        r2 = det_2.predict_from_bytes(image_bytes)
        r8 = det_8.predict_from_bytes(image_bytes)

        # Both should detect something
        assert r2["class"] != "NaN"
        assert r8["class"] != "NaN"
        # 8 iterations should have higher confidence (tighter precision)
        assert r8["confidence"] >= r2["confidence"]
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watermeter.opencv_arrows'`

### Step 3: Implement `watermeter/opencv_arrows.py`

Create `watermeter/opencv_arrows.py`:

```python
"""OpenCV-based arrow detection for circular gauge dials.

Detects the arrow/needle position (0.0–9.9) by:
1. HSV thresholding to isolate colored arrow pixels
2. 85th-percentile distance cutoff to isolate the arrow tip
3. 8-slice initial scan to find the arrow's sector
4. Recursive bisection within that sector to refine the value

This module provides `OpenCVArrowDetector`, which implements the same
predict interface as `Classifier` and `Regressor` in `inference.py`.
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Gauge geometry: full 360° circular dial, 0 at 12 o'clock, clockwise
_GAUGE_START_DEG = -90.0
_GAUGE_ARC_DEG = 360.0
_INITIAL_SLICES = 8
_TIP_PERCENTILE = 85

# Default HSV thresholds for red arrow detection
_DEFAULT_HUE_RANGES = [[0, 15], [165, 180]]
_DEFAULT_SATURATION_MIN = 50
_DEFAULT_VALUE_MIN = 50


def detect_color_mask(
    image_bgr: np.ndarray,
    hue_ranges: list[list[int]] | None = None,
    saturation_min: int = _DEFAULT_SATURATION_MIN,
    value_min: int = _DEFAULT_VALUE_MIN,
) -> np.ndarray:
    """Create binary mask of colored pixels using HSV thresholding.

    Args:
        image_bgr: Input image in BGR format.
        hue_ranges: List of [low, high] hue ranges (OpenCV 0-179 scale).
            Defaults to red: [[0, 15], [165, 180]].
        saturation_min: Minimum saturation (0-255).
        value_min: Minimum value/brightness (0-255).

    Returns:
        Binary mask (uint8, 0 or 255).
    """
    if hue_ranges is None:
        hue_ranges = _DEFAULT_HUE_RANGES

    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    hue_mask = np.zeros(h.shape, dtype=bool)
    for lo, hi in hue_ranges:
        hue_mask |= (h >= lo) & (h <= hi)

    mask = hue_mask & (s >= saturation_min) & (v >= value_min)
    return mask.astype(np.uint8) * 255


class OpenCVArrowDetector:
    """Detects gauge arrow position using OpenCV color thresholding + bisection.

    Implements the same predict interface as Classifier/Regressor so it can
    be used as a drop-in replacement for `InferenceService._arrows_classifier`.
    """

    def __init__(
        self,
        hue_ranges: list[list[int]] | None = None,
        saturation_min: int = _DEFAULT_SATURATION_MIN,
        value_min: int = _DEFAULT_VALUE_MIN,
        bisection_iterations: int = 4,
    ):
        self.hue_ranges = hue_ranges if hue_ranges is not None else _DEFAULT_HUE_RANGES
        self.saturation_min = saturation_min
        self.value_min = value_min
        self.bisection_iterations = bisection_iterations

    def _detect(self, image_bgr: np.ndarray) -> tuple[float | None, float]:
        """Run the detection algorithm on a BGR image.

        Returns:
            (value, precision) where value is 0.0–9.9 or None on failure.
        """
        h, w = image_bgr.shape[:2]
        cx, cy = w // 2, h // 2

        # Color mask
        color_mask = detect_color_mask(
            image_bgr, self.hue_ranges, self.saturation_min, self.value_min
        )
        if np.count_nonzero(color_mask) == 0:
            return None, 0.0

        # Gauge angles for every pixel (0-360, 0 = top/12 o'clock)
        ys, xs = np.mgrid[0:h, 0:w]
        dx = (xs - cx).astype(np.float64)
        dy = (ys - cy).astype(np.float64)
        image_angles = np.degrees(np.arctan2(dy, dx))
        gauge_angles = (image_angles - _GAUGE_START_DEG) % 360.0

        # Distance from center (for tip isolation and bisection weighting)
        dist = np.sqrt(dx**2 + dy**2)

        # Isolate tip pixels (outermost by distance)
        colored_pixels = color_mask > 0
        colored_dists = dist[colored_pixels]
        tip_cutoff = np.percentile(colored_dists, _TIP_PERCENTILE)
        tip_mask = colored_pixels & (dist >= tip_cutoff)

        # 8-slice initial scan: count tip pixels per sector
        slice_size = _GAUGE_ARC_DEG / _INITIAL_SLICES
        slice_counts = []
        for i in range(_INITIAL_SLICES):
            lo = i * slice_size
            hi = (i + 1) * slice_size
            sector = (gauge_angles >= lo) & (gauge_angles < hi)
            slice_counts.append(np.count_nonzero(tip_mask & sector))

        winner = int(np.argmax(slice_counts))
        angle_lo = winner * slice_size
        angle_hi = (winner + 1) * slice_size

        # Distance-weighted red pixel map for bisection
        red_weights = np.where(colored_pixels, dist, 0.0)

        # Bisection refinement
        for _ in range(self.bisection_iterations):
            angle_mid = (angle_lo + angle_hi) / 2.0
            left_mask = (gauge_angles >= angle_lo) & (gauge_angles < angle_mid)
            right_mask = (gauge_angles >= angle_mid) & (gauge_angles < angle_hi)
            left_w = red_weights[left_mask].sum()
            right_w = red_weights[right_mask].sum()

            if left_w == 0 and right_w == 0:
                break

            if left_w >= right_w:
                angle_hi = angle_mid
            else:
                angle_lo = angle_mid

        # Final value
        final_angle = (angle_lo + angle_hi) / 2.0
        value = final_angle / _GAUGE_ARC_DEG * 10.0
        value = round(value, 1)
        value = max(0.0, min(9.9, value))

        precision = (angle_hi - angle_lo) / _GAUGE_ARC_DEG * 10.0 / 2.0
        return value, precision

    def predict_from_bytes(self, image_bytes: bytes) -> dict:
        """Predict gauge value from JPEG/PNG bytes.

        Returns:
            {"class": "3.7", "confidence": float} or
            {"class": "NaN", "confidence": 0.0} on failure.
        """
        buf = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("OpenCV arrow detector: failed to decode image")
            return {"class": "NaN", "confidence": 0.0}

        value, precision = self._detect(image)
        if value is None:
            logger.warning("OpenCV arrow detector: no colored pixels found")
            return {"class": "NaN", "confidence": 0.0}

        # Map precision to confidence: tighter precision = higher confidence
        # With 4 bisections: precision ~0.04, confidence ~0.99
        # With 0 bisections: precision ~0.63, confidence ~0.87
        confidence = max(0.0, min(1.0, 1.0 - precision / 5.0))

        return {"class": str(value), "confidence": float(confidence)}

    def predict_detailed_from_bytes(
        self, image_bytes: bytes, top_k: int = 3
    ) -> list[dict]:
        """Return prediction as a single-element list (no top-K for OpenCV)."""
        return [self.predict_from_bytes(image_bytes)]
```

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: All PASS

### Step 5: Commit

```bash
git add watermeter/opencv_arrows.py tests/unit/test_opencv_arrows.py
git commit -m "claude: add OpenCVArrowDetector with HSV+bisection algorithm"
```

---

## Task 2: Add config schema and defaults for `arrows_mode` / `opencv_arrows`

**Files:**
- Modify: `watermeter/config_utils.py` (CONFIG_SCHEMA, around line 156)
- Modify: `config.yaml`
- Modify: `config_debug/config.yaml`

### Step 1: Write the failing test

Add to `tests/unit/test_opencv_arrows.py`:

```python
class TestOpenCVArrowsConfig:
    """Test config schema includes opencv_arrows settings."""

    def test_schema_has_arrows_mode(self):
        from watermeter.config_utils import CONFIG_SCHEMA

        inference_props = CONFIG_SCHEMA["properties"]["inference"]["properties"]
        assert "arrows_mode" in inference_props
        assert inference_props["arrows_mode"]["enum"] == ["model", "opencv"]

    def test_schema_has_opencv_arrows(self):
        from watermeter.config_utils import CONFIG_SCHEMA

        inference_props = CONFIG_SCHEMA["properties"]["inference"]["properties"]
        assert "opencv_arrows" in inference_props
        opencv_props = inference_props["opencv_arrows"]["properties"]
        assert "hue_ranges" in opencv_props
        assert "saturation_min" in opencv_props
        assert "value_min" in opencv_props
        assert "bisection_iterations" in opencv_props

    def test_default_config_validates(self):
        """Config with arrows_mode should pass validation."""
        from watermeter.config_utils import validate_config

        config = {
            "images": {"digits": ["d1"], "arrows": ["a1"]},
            "mqtt": {"broker": "localhost"},
            "inference": {
                "arrows_mode": "opencv",
                "opencv_arrows": {
                    "hue_ranges": [[0, 15], [165, 180]],
                    "saturation_min": 50,
                    "value_min": 50,
                    "bisection_iterations": 4,
                },
            },
        }
        errors = validate_config(config)
        # Should not have errors about arrows_mode or opencv_arrows
        arrows_errors = [e for e in errors if "arrows_mode" in e or "opencv_arrows" in e]
        assert arrows_errors == []
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py::TestOpenCVArrowsConfig -v`
Expected: FAIL — `arrows_mode` not in schema

### Step 3: Add schema entries to `config_utils.py`

In `watermeter/config_utils.py`, inside `CONFIG_SCHEMA["properties"]["inference"]["properties"]`, add:

```python
"arrows_mode": {
    "type": "string",
    "enum": ["model", "opencv"],
    "description": "Arrow inference backend: 'model' (ML/OpenVINO) or 'opencv' (color threshold + bisection)",
    "default": "model",
},
"opencv_arrows": {
    "type": "object",
    "description": "Settings for OpenCV-based arrow detection (used when arrows_mode='opencv')",
    "properties": {
        "hue_ranges": {
            "type": "array",
            "description": "HSV hue ranges for arrow color detection (OpenCV 0-179 scale)",
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
            },
            "default": [[0, 15], [165, 180]],
        },
        "saturation_min": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "description": "Minimum HSV saturation for arrow pixels",
            "default": 50,
        },
        "value_min": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "description": "Minimum HSV value (brightness) for arrow pixels",
            "default": 50,
        },
        "bisection_iterations": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Number of bisection refinement steps (4 = ~±0.04 precision)",
            "default": 4,
        },
    },
    "additionalProperties": False,
},
```

### Step 4: Add defaults to config files

In `config.yaml` and `config_debug/config.yaml`, under the `inference:` block, add:

```yaml
  arrows_mode: "model"           # "model" (ML/OpenVINO) or "opencv" (color threshold)
  # opencv_arrows:               # Settings for arrows_mode: "opencv"
  #   hue_ranges: [[0, 15], [165, 180]]  # HSV hue ranges (red default)
  #   saturation_min: 50
  #   value_min: 50
  #   bisection_iterations: 4
```

### Step 5: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: All PASS

### Step 6: Commit

```bash
git add watermeter/config_utils.py config.yaml config_debug/config.yaml
git commit -m "claude: add arrows_mode and opencv_arrows config schema"
```

---

## Task 3: Wire `OpenCVArrowDetector` into `InferenceService`

**Files:**
- Modify: `watermeter/inference.py` (`InferenceService.initialize` ~line 215, `reload_models` ~line 283)

### Step 1: Write the failing test

Add to `tests/unit/test_opencv_arrows.py`:

```python
from unittest.mock import patch, MagicMock


class TestInferenceServiceOpenCVMode:
    """Test InferenceService uses OpenCVArrowDetector when arrows_mode='opencv'."""

    def test_initialize_opencv_mode(self):
        """When arrows_mode='opencv', _arrows_classifier should be OpenCVArrowDetector."""
        from watermeter.opencv_arrows import OpenCVArrowDetector

        # We need to test that InferenceService picks OpenCVArrowDetector.
        # Since InferenceService has heavy deps (OpenVINO), we test the
        # factory logic via _create_arrows_backend().
        from watermeter.inference import _create_arrows_backend

        config = {
            "inference": {
                "arrows_mode": "opencv",
                "opencv_arrows": {
                    "hue_ranges": [[0, 15], [165, 180]],
                    "saturation_min": 50,
                    "value_min": 50,
                    "bisection_iterations": 6,
                },
            }
        }
        backend = _create_arrows_backend(config)
        assert isinstance(backend, OpenCVArrowDetector)
        assert backend.bisection_iterations == 6
        assert backend.hue_ranges == [[0, 15], [165, 180]]

    def test_initialize_model_mode_returns_none(self):
        """When arrows_mode='model', _create_arrows_backend returns None (use ML)."""
        from watermeter.inference import _create_arrows_backend

        config = {"inference": {"arrows_mode": "model"}}
        backend = _create_arrows_backend(config)
        assert backend is None

    def test_initialize_default_mode_returns_none(self):
        """When arrows_mode is absent, _create_arrows_backend returns None."""
        from watermeter.inference import _create_arrows_backend

        config = {"inference": {}}
        backend = _create_arrows_backend(config)
        assert backend is None
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py::TestInferenceServiceOpenCVMode -v`
Expected: FAIL — `cannot import name '_create_arrows_backend'`

### Step 3: Implement the factory function and wire it in

In `watermeter/inference.py`, add the factory function (near the top, after imports):

```python
from watermeter.opencv_arrows import OpenCVArrowDetector


def _create_arrows_backend(config: dict) -> OpenCVArrowDetector | None:
    """Create OpenCV arrow detector if arrows_mode is 'opencv', else None.

    Returns None when the ML model path should be used instead.
    """
    inference_cfg = config.get("inference", {})
    mode = inference_cfg.get("arrows_mode", "model")

    if mode != "opencv":
        return None

    opencv_cfg = inference_cfg.get("opencv_arrows", {})
    return OpenCVArrowDetector(
        hue_ranges=opencv_cfg.get("hue_ranges"),
        saturation_min=opencv_cfg.get("saturation_min", 50),
        value_min=opencv_cfg.get("value_min", 50),
        bisection_iterations=opencv_cfg.get("bisection_iterations", 4),
    )
```

In `InferenceService.initialize()` (~line 246), BEFORE the existing arrows model loading, add:

```python
# Check for OpenCV arrows mode
opencv_detector = _create_arrows_backend(config)
if opencv_detector is not None:
    self._arrows_classifier = opencv_detector
    logger.info("Arrows initialized in OPENCV mode (HSV + bisection)")
else:
    # ... existing ML model loading code ...
```

Same pattern in `reload_models()` (~line 283).

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_opencv_arrows.py -v`
Expected: All PASS

### Step 5: Run full test suite to check for regressions

Run: `.venv/bin/python -m pytest --tb=short`
Expected: All existing tests still PASS

### Step 6: Commit

```bash
git add watermeter/inference.py
git commit -m "claude: wire OpenCVArrowDetector into InferenceService via arrows_mode config"
```

---

## Task 4: Update codebase map

**Files:**
- Modify: `docs/codebase_map.md`

### Step 1: Add the new module to the codebase map

Add entry for `watermeter/opencv_arrows.py` with:
- `detect_color_mask()` — function signature and purpose
- `OpenCVArrowDetector` — class with `__init__`, `_detect`, `predict_from_bytes`, `predict_detailed_from_bytes`
- `_create_arrows_backend()` in `inference.py`

### Step 2: Commit

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with OpenCVArrowDetector"
```

---

## Summary of Config Options

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `inference.arrows_mode` | `"model"` \| `"opencv"` | `"model"` | Arrow inference backend |
| `inference.opencv_arrows.hue_ranges` | `[[int, int], ...]` | `[[0, 15], [165, 180]]` | HSV hue ranges (red) |
| `inference.opencv_arrows.saturation_min` | `int` (0-255) | `50` | Min HSV saturation |
| `inference.opencv_arrows.value_min` | `int` (0-255) | `50` | Min HSV value |
| `inference.opencv_arrows.bisection_iterations` | `int` (1-20) | `4` | Bisection refinement steps |
