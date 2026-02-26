"""OpenCV-based arrow detection for circular gauge dials.

Detects the arrow/needle position (0.0-9.9) by:
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

# Gauge geometry: full 360 degree circular dial, 0 at 12 o'clock, clockwise
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
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    hue_mask = np.zeros(hue.shape, dtype=bool)
    for lo, hi in hue_ranges:
        hue_mask |= (hue >= lo) & (hue <= hi)

    mask = hue_mask & (sat >= saturation_min) & (val >= value_min)
    return mask.astype(np.uint8) * 255


class OpenCVArrowDetector:
    """Detects gauge arrow position using OpenCV color thresholding + bisection.

    Implements the same predict interface as Classifier/Regressor so it can
    be used as a drop-in replacement for InferenceService._arrows_classifier.
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
            (value, precision) where value is 0.0-9.9 or None on failure.
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

        # Distance-weighted colored pixel map for bisection
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
