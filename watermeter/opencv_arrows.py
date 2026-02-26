"""OpenCV-based arrow detection for circular gauge dials.

Detects the arrow/needle position (0.0-9.9) by:
1. HSV thresholding to isolate colored arrow pixels
2. 85th-percentile distance cutoff to isolate the arrow tip
3. Angular histogram peak detection on tip pixel angles

Tip pixels are binned into 100 angular bins (each representing 0.1 on the
gauge). The peak bin gives the gauge value, and the fraction of total weight
concentrated at the peak (plus neighbors) gives the confidence score.

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
    """Detects gauge arrow position using angular histogram peak detection.

    Implements the same predict interface as Classifier/Regressor so it can
    be used as a drop-in replacement for InferenceService._arrows_classifier.
    """

    def __init__(
        self,
        hue_ranges: list[list[int]] | None = None,
        saturation_min: int = _DEFAULT_SATURATION_MIN,
        value_min: int = _DEFAULT_VALUE_MIN,
    ):
        self.hue_ranges = hue_ranges if hue_ranges is not None else _DEFAULT_HUE_RANGES
        self.saturation_min = saturation_min
        self.value_min = value_min

    def _detect(self, image_bgr: np.ndarray) -> tuple[float | None, float]:
        """Run detection via angular histogram peak detection on tip pixels.

        Returns:
            (value, confidence) where value is 0.0-9.9 or None on failure.
            confidence is fraction of weight at peak bin and neighbors (0.0-1.0).
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

        # --- Angular histogram peak detection ---
        N_BINS = 100  # bins 0-99, each represents 0.1 on the gauge (0.0 to 9.9)
        bin_width = _GAUGE_ARC_DEG / N_BINS  # degrees per bin

        # Assign each tip pixel to a bin based on its gauge angle
        tip_angles = gauge_angles[tip_mask]
        tip_weights = dist[tip_mask]

        # Bin index for each tip pixel (clamp to 0..N_BINS-1)
        bin_indices = np.clip((tip_angles / bin_width).astype(int), 0, N_BINS - 1)

        # Accumulate distance-weighted scores per bin
        histogram = np.zeros(N_BINS, dtype=np.float64)
        np.add.at(histogram, bin_indices, tip_weights)

        # Peak bin = arrow direction
        peak_bin = int(np.argmax(histogram))
        value = peak_bin / 10.0  # bin 0 -> 0.0, bin 35 -> 3.5, bin 99 -> 9.9

        # Confidence = fraction of total weight concentrated at peak
        # Use peak + immediate neighbors to account for edge effects
        total_weight = np.sum(histogram)
        if total_weight == 0:
            return None, 0.0

        # Sum peak and neighbors (with wraparound for bin 0 and 99)
        peak_score = histogram[peak_bin]
        if peak_bin > 0:
            peak_score += histogram[peak_bin - 1]
        if peak_bin < N_BINS - 1:
            peak_score += histogram[peak_bin + 1]

        confidence = float(peak_score / total_weight)
        confidence = min(confidence, 1.0)
        # Gamma curve: rises fast then slowly approaches 1.0
        confidence = confidence ** 0.4

        value = round(value, 1)
        value = max(0.0, min(9.9, value))

        return value, confidence

    def predict(self, image_path) -> dict:
        """Predict gauge value from an image file path.

        Returns:
            {"class": "3.7", "confidence": float} or
            {"class": "NaN", "confidence": 0.0} on failure.
        """
        image = cv2.imread(str(image_path))
        if image is None:
            logger.warning("OpenCV arrow detector: failed to read %s", image_path)
            return {"class": "NaN", "confidence": 0.0}
        _, buf = cv2.imencode(".jpg", image)
        return self.predict_from_bytes(buf.tobytes())

    def predict_detailed(self, image_path, top_k: int = 3) -> list[dict]:
        """Return prediction from file path as a single-element list."""
        return [self.predict(image_path)]

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

        value, confidence = self._detect(image)
        if value is None:
            logger.warning("OpenCV arrow detector: no colored pixels found")
            return {"class": "NaN", "confidence": 0.0}

        return {"class": str(value), "confidence": confidence}

    def predict_detailed_from_bytes(
        self, image_bytes: bytes, top_k: int = 3
    ) -> list[dict]:
        """Return prediction as a single-element list (no top-K for OpenCV)."""
        return [self.predict_from_bytes(image_bytes)]
