"""Tests for OpenCV-based arrow detection."""

import importlib
import sys

import numpy as np
import pytest


@pytest.fixture(autouse=True, scope="module")
def real_cv2():
    """Replace the mocked cv2 with the real cv2 for opencv_arrows tests.

    The unit conftest mocks cv2 to avoid import errors in other modules.
    These tests require the actual cv2 (installed in .venv).
    """
    # Save the mock
    mock_cv2 = sys.modules.get("cv2")

    # Install the real cv2
    if mock_cv2 is not None:
        del sys.modules["cv2"]
    import cv2 as real
    sys.modules["cv2"] = real

    # Reload the module under test so it uses the real cv2
    if "watermeter.opencv_arrows" in sys.modules:
        importlib.reload(sys.modules["watermeter.opencv_arrows"])

    yield

    # Restore the mock after module tests complete
    if mock_cv2 is not None:
        sys.modules["cv2"] = mock_cv2
    elif "cv2" in sys.modules:
        del sys.modules["cv2"]

    # Reload again so other tests get the mock back
    if "watermeter.opencv_arrows" in sys.modules:
        importlib.reload(sys.modules["watermeter.opencv_arrows"])


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
        import cv2

        from watermeter.opencv_arrows import detect_color_mask

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
        """Config with arrows_mode should pass schema validation."""
        from watermeter.config_utils import validate_config_schema

        config = {
            "images": {"digits": ["d1"], "arrows": ["a1"]},
            "mqtt": {"broker": "localhost", "port": 1883},
            "inference": {
                "confidence_threshold": 0.8,
                "arrows_mode": "opencv",
                "opencv_arrows": {
                    "hue_ranges": [[0, 15], [165, 180]],
                    "saturation_min": 50,
                    "value_min": 50,
                    "bisection_iterations": 4,
                },
            },
        }
        result = validate_config_schema(config)
        assert result["valid"] is True, f"Schema validation failed: {result['error']}"
