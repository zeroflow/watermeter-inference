"""
Unit tests for marker-based image alignment in WatermeterService.

These tests use REAL cv2 and numpy to verify actual image-processing logic.
Heavy dependencies (paho.mqtt, httpx, openvino, etc.) are mocked at module level
so that watermeter.watermeter_service can be imported without Docker.

IMPORTANT: This file manages its own sys.modules state and does NOT rely on the
unit test conftest.py (which mocks watermeter_service entirely and also mocks cv2).
We must undo the conftest cv2 mock and force-load the real cv2 before importing
watermeter_service.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
import numpy as np

# ---------------------------------------------------------------------------
# The unit conftest.py mocks cv2 in sys.modules (with spec=ModuleType) BEFORE
# this file loads.  pytest.importorskip('cv2') would return that mock because
# it just does `import cv2` which finds the mock in sys.modules.
#
# Strategy:
#   1. Save the conftest mock cv2 so we can restore it later
#   2. Force-load the real cv2
#   3. Import watermeter_service with the real cv2
#   4. Restore the conftest mock cv2 so other test files are not affected
# ---------------------------------------------------------------------------

# Save conftest mocks that we will temporarily replace
_saved_cv2_mock = sys.modules.get("cv2")
_saved_ws_mocks = {}
for _ws_name in ["watermeter_service", "watermeter.watermeter_service", "watermeter.image_pipeline"]:
    if _ws_name in sys.modules:
        _saved_ws_mocks[_ws_name] = sys.modules[_ws_name]

# Remove mock cv2 so we can import the real one
if "cv2" in sys.modules and isinstance(sys.modules["cv2"], MagicMock):
    del sys.modules["cv2"]

# Now import the real cv2 -- skip the whole file if it is not installed
cv2 = pytest.importorskip("cv2")

# Verify we got the real thing
if not hasattr(cv2, "cvtColor"):
    pytest.skip("cv2 module is mocked, cannot run alignment tests", allow_module_level=True)

# Ensure the real cv2 is in sys.modules for watermeter_service to pick up
sys.modules["cv2"] = cv2

# ---------------------------------------------------------------------------
# Mock heavy modules that watermeter_service imports at module level.
# We must do this BEFORE importing the module under test.
# ---------------------------------------------------------------------------

_modules_to_mock = [
    "paho",
    "paho.mqtt",
    "paho.mqtt.client",
    "openvino",
    "openvino.runtime",
]
# Note: httpx is NOT mocked -- it is available in the venv and mocking it
# would break huggingface_hub (used by timm) in later test files.

for _mod in _modules_to_mock:
    if _mod not in sys.modules or isinstance(sys.modules[_mod], MagicMock):
        mock_mod = MagicMock()
        mock_mod.__name__ = _mod
        sys.modules[_mod] = mock_mod

# Mock watermeter.inference (has module-level init that loads OpenVINO models)
for _inf_name in ["inference", "watermeter.inference"]:
    if _inf_name not in sys.modules or isinstance(sys.modules[_inf_name], MagicMock):
        _inf_mock = MagicMock()
        _inf_mock.__name__ = _inf_name
        _inf_mock.get_inference_service = MagicMock(return_value=MagicMock())
        sys.modules[_inf_name] = _inf_mock

# Note: watermeter.persistence is NOT mocked -- it imports cleanly on the host
# and other test files (test_persistence.py) need the real module.

# Remove the conftest "full mock" of watermeter_service and image_pipeline
# so we can import the real ones with the real cv2.
for _ws_name in ["watermeter_service", "watermeter.watermeter_service", "watermeter.image_pipeline"]:
    if _ws_name in sys.modules:
        del sys.modules[_ws_name]

# Now import the real module -- it will see the real cv2 and numpy, mocked everything else
from watermeter.watermeter_service import WatermeterService  # noqa: E402

# Keep a reference to the real module for patch.object targets
import watermeter.watermeter_service as _ws_mod
import watermeter.image_pipeline as _ip_mod

assert hasattr(_ip_mod.cv2, "cvtColor"), "image_pipeline.cv2 is still a mock -- alignment tests cannot run"

# ---------------------------------------------------------------------------
# Restore conftest mocks so that other test files (test_api_routes, etc.)
# are not affected by our sys.modules changes.
#
# IMPORTANT: When we imported the real watermeter_service, Python also set it
# as an attribute on the `watermeter` package object.  Other modules that do
# `from watermeter import watermeter_service` (e.g. routes/label.py) resolve
# via the package attribute, not sys.modules.  We must restore the package
# attribute too, otherwise the conftest mock won't be used.
# ---------------------------------------------------------------------------
if _saved_cv2_mock is not None:
    sys.modules["cv2"] = _saved_cv2_mock

import watermeter as _wm_pkg

for _ws_name, _ws_mock in _saved_ws_mocks.items():
    sys.modules[_ws_name] = _ws_mock
    # Also restore the package attribute so `from watermeter import watermeter_service`
    # picks up the mock, not the real module we imported.
    _attr = _ws_name.split(".")[-1]  # 'watermeter_service'
    if hasattr(_wm_pkg, _attr):
        setattr(_wm_pkg, _attr, _ws_mock)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service():
    """
    Create a WatermeterService with only alignment-related state,
    bypassing __init__ (which requires config.yaml, MQTT, etc.).

    Uses _ip_mod.ImagePipeline (the fresh module with real cv2), not
    'from watermeter.image_pipeline import ...' which may resolve to a
    stale module restored by the conftest mock cleanup.
    """
    svc = object.__new__(WatermeterService)
    svc._image_pipeline = _ip_mod.ImagePipeline(config={})
    return svc


def make_synthetic_image(width=640, height=480, seed=42):
    """
    Create a BGR image with random but reproducible noise.
    """
    rng = np.random.RandomState(seed)
    return rng.randint(0, 256, (height, width, 3), dtype=np.uint8)


def draw_marker(img, center_x, center_y, radius=15, color=(255, 255, 255)):
    """
    Draw a distinctive filled circle marker on the image.
    Returns the grayscale template extracted around that marker.
    """
    cv2.circle(img, (center_x, center_y), radius, color, -1)
    # Also draw a smaller dark circle inside for distinctiveness
    cv2.circle(img, (center_x, center_y), radius // 3, (0, 0, 0), -1)

    # Extract the template region (square around the circle)
    pad = radius + 4
    y1 = max(0, center_y - pad)
    y2 = min(img.shape[0], center_y + pad)
    x1 = max(0, center_x - pad)
    x2 = min(img.shape[1], center_x + pad)
    roi = img[y1:y2, x1:x2]
    template = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return template


def make_markers_config(cx1, cy1, cx2, cy2, width, height, marker_w=30, marker_h=30):
    """
    Build normalised marker dicts from pixel-space center coordinates.
    """
    return [
        {
            "x": (cx1 - marker_w / 2) / width,
            "y": (cy1 - marker_h / 2) / height,
            "width": marker_w / width,
            "height": marker_h / height,
        },
        {
            "x": (cx2 - marker_w / 2) / width,
            "y": (cy2 - marker_h / 2) / height,
            "width": marker_w / width,
            "height": marker_h / height,
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFewerThanTwoMarkers:
    """Passing fewer than 2 markers should return a fail-closed AlignmentResult."""

    def test_zero_markers(self):
        svc = make_service()
        img = make_synthetic_image()
        result = svc._image_pipeline._align_with_markers(img, [])
        assert result.success is False
        assert result.error_reason == "insufficient_markers"
        assert result.image is None

    def test_one_marker(self):
        svc = make_service()
        img = make_synthetic_image()
        single = [{"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.05}]
        result = svc._image_pipeline._align_with_markers(img, single)
        assert result.success is False
        assert result.error_reason == "insufficient_markers"
        assert result.image is None


class TestMissingTemplateFiles:
    """Missing or unreadable template files should return a fail-closed AlignmentResult."""

    def test_no_template_on_disk(self):
        svc = make_service()
        img = make_synthetic_image()
        markers = make_markers_config(100, 100, 500, 350, 640, 480)

        # _load_marker_templates checks Path('/data/marker_1.jpg').exists()
        # Those files do not exist on the test host, so it returns None.
        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "templates_missing"
        assert result.image is None

    def test_template_file_exists_but_unreadable(self):
        """cv2.imread returns None for corrupted files."""
        svc = make_service()
        img = make_synthetic_image()
        markers = make_markers_config(100, 100, 500, 350, 640, 480)

        # Patch cv2.imread on the module where it was imported
        with patch.object(Path, "exists", return_value=True), patch.object(_ip_mod.cv2, "imread", return_value=None):
            result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "templates_missing"
        assert result.image is None


class TestLowConfidenceMatch:
    """A template that does not match should cause fail-closed (image=None)."""

    def test_random_noise_template_low_confidence(self):
        """A random-noise template should not match a structured image well."""
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=1)

        # Draw distinctive solid-colour patterns so the image has structure
        cv2.rectangle(img, (80, 80), (140, 140), (0, 0, 255), -1)
        cv2.rectangle(img, (460, 320), (520, 380), (0, 255, 0), -1)

        # Create random-noise templates that will not match
        rng = np.random.RandomState(999)
        noise_template_1 = rng.randint(0, 256, (30, 30), dtype=np.uint8)
        noise_template_2 = rng.randint(0, 256, (30, 30), dtype=np.uint8)

        svc._image_pipeline._marker_templates = [noise_template_1, noise_template_2]

        markers = make_markers_config(110, 110, 490, 350, width, height)
        result = svc._image_pipeline._align_with_markers(img, markers)
        # With random noise templates against structured patterns the
        # normalized cross-correlation confidence will be below 0.5,
        # producing a fail-closed result.
        assert result.success is False
        assert result.error_reason == "low_confidence"
        assert result.image is None


class TestAlreadyAlignedImage:
    """Markers at reference positions should produce an output very close to input."""

    def test_identity_alignment(self):
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=10)

        # Marker positions (pixel centers)
        cx1, cy1 = 120, 100
        cx2, cy2 = 520, 380

        # Draw markers on the image and capture templates
        template1 = draw_marker(img, cx1, cy1, radius=15)
        template2 = draw_marker(img, cx2, cy2, radius=15)

        svc._image_pipeline._marker_templates = [template1, template2]

        markers = make_markers_config(cx1, cy1, cx2, cy2, width, height, marker_w=30, marker_h=30)

        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is True
        assert result.image is not None

        # The transform should be very close to identity; pixel differences tiny.
        diff = np.abs(result.image.astype(np.float32) - img.astype(np.float32))
        mean_diff = diff.mean()
        assert mean_diff < 5.0, (
            f"Already-aligned image should stay nearly unchanged, " f"but mean pixel diff = {mean_diff:.2f}"
        )


class TestShiftedImageCorrected:
    """Translate image by ~10px, verify alignment brings markers back to reference."""

    def test_translate_corrected(self):
        svc = make_service()
        width, height = 640, 480

        # Build the reference image with markers
        ref_img = make_synthetic_image(width, height, seed=20)
        cx1, cy1 = 160, 120
        cx2, cy2 = 480, 360

        template1 = draw_marker(ref_img, cx1, cy1, radius=18)
        template2 = draw_marker(ref_img, cx2, cy2, radius=18)

        svc._image_pipeline._marker_templates = [template1, template2]

        # Shift the reference image by (dx, dy) to simulate camera drift
        dx, dy = 10, 7
        shift_matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        shifted_img = cv2.warpAffine(ref_img, shift_matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)

        # Marker config uses the *original* (reference) positions
        markers = make_markers_config(cx1, cy1, cx2, cy2, width, height, marker_w=36, marker_h=36)

        result = svc._image_pipeline._align_with_markers(shifted_img, markers)
        assert result.success is True
        assert result.image is not None
        aligned = result.image

        # After alignment, run template matching on the aligned image to verify
        # markers are back near their reference positions.
        gray_aligned = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        for template, (ref_cx, ref_cy) in [(template1, (cx1, cy1)), (template2, (cx2, cy2))]:
            th, tw = template.shape[:2]
            result = cv2.matchTemplate(gray_aligned, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            found_cx = max_loc[0] + tw / 2
            found_cy = max_loc[1] + th / 2
            dist = np.hypot(found_cx - ref_cx, found_cy - ref_cy)
            assert dist < 5.0, (
                f"After alignment, marker should be within 5px of reference "
                f"but was {dist:.1f}px away (ref=({ref_cx},{ref_cy}), "
                f"found=({found_cx:.1f},{found_cy:.1f}), confidence={max_val:.3f})"
            )


class TestTemplateCaching:
    """_load_marker_templates should cache; cv2.imread called only on first load."""

    def test_imread_called_once_for_two_loads(self):
        svc = make_service()

        # Create dummy templates
        t1 = np.zeros((20, 20), dtype=np.uint8)
        t2 = np.ones((20, 20), dtype=np.uint8) * 128

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(_ip_mod.cv2, "imread", side_effect=[t1, t2]) as mock_imread,
        ):

            # First load -- should call imread twice (once per template)
            result1 = svc._image_pipeline._load_marker_templates(2)
            assert result1 is not None
            assert len(result1) == 2
            assert mock_imread.call_count == 2

            # Second load -- should return cached, no additional imread calls
            result2 = svc._image_pipeline._load_marker_templates(2)
            assert result2 is result1, "Second call should return the cached list"
            assert mock_imread.call_count == 2, "cv2.imread should not be called again on second load"


class TestCacheInvalidation:
    """invalidate_marker_cache() clears cache; next load reads from disk again."""

    def test_invalidate_triggers_reload(self):
        svc = make_service()

        t1 = np.zeros((20, 20), dtype=np.uint8)
        t2 = np.ones((20, 20), dtype=np.uint8) * 128

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(_ip_mod.cv2, "imread", side_effect=[t1, t2, t1, t2]) as mock_imread,
        ):

            # First load
            result1 = svc._image_pipeline._load_marker_templates(2)
            assert result1 is not None
            assert mock_imread.call_count == 2

            # Invalidate
            svc.invalidate_marker_cache()
            assert svc._image_pipeline._marker_templates is None

            # Second load -- should call imread again
            result2 = svc._image_pipeline._load_marker_templates(2)
            assert result2 is not None
            assert mock_imread.call_count == 4, "After invalidation, cv2.imread should be called again"
            # Results should be a new list, not the same object
            assert result2 is not result1

    def test_invalidate_when_already_none(self):
        """Calling invalidate on a fresh service with no cache is a safe no-op."""
        svc = make_service()
        assert svc._image_pipeline._marker_templates is None
        svc.invalidate_marker_cache()  # should not raise
        assert svc._image_pipeline._marker_templates is None


class TestSearchRegionNearEdge:
    """Marker config near image edges should not crash."""

    def test_marker_at_top_left_corner(self):
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=30)

        # Place a marker very close to (0, 0)
        cx, cy = 10, 10
        template = draw_marker(img, cx, cy, radius=8)

        # Second marker in the middle
        cx2, cy2 = 320, 240
        template2 = draw_marker(img, cx2, cy2, radius=8)

        svc._image_pipeline._marker_templates = [template, template2]

        markers = make_markers_config(cx, cy, cx2, cy2, width, height, marker_w=16, marker_h=16)

        # Should not crash even though the search region is clipped at image bounds
        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is True
        assert result.image is not None
        assert result.image.shape == img.shape

    def test_marker_at_bottom_right_corner(self):
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=31)

        # First marker in the middle
        cx1, cy1 = 320, 240
        template1 = draw_marker(img, cx1, cy1, radius=8)

        # Second marker very close to bottom-right
        cx2, cy2 = width - 10, height - 10
        template2 = draw_marker(img, cx2, cy2, radius=8)

        svc._image_pipeline._marker_templates = [template1, template2]

        markers = make_markers_config(cx1, cy1, cx2, cy2, width, height, marker_w=16, marker_h=16)

        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is True
        assert result.image is not None
        assert result.image.shape == img.shape

    def test_search_region_too_small_for_template(self):
        """
        If the marker config places the search region so small that the template
        does not fit, the method should return a fail-closed AlignmentResult.
        """
        svc = make_service()
        width, height = 100, 80  # Very small image
        img = make_synthetic_image(width, height, seed=32)

        # Create oversized templates (bigger than the search region would allow)
        large_template = np.zeros((60, 60), dtype=np.uint8)
        svc._image_pipeline._marker_templates = [large_template, large_template]

        markers = make_markers_config(10, 10, 90, 70, width, height, marker_w=10, marker_h=10)

        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "search_region_too_small"
        assert result.image is None


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
        markers = make_markers_config(100, 100, 500, 350, 640, 480)[:1]
        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is False
        assert result.error_reason == "insufficient_markers"
        assert result.image is None

    def test_low_confidence_returns_failure_with_marker_index(self):
        """Forces low confidence by using random-noise templates."""
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=1)

        # Draw distinctive solid-colour patterns so the image has structure
        cv2.rectangle(img, (80, 80), (140, 140), (0, 0, 255), -1)
        cv2.rectangle(img, (460, 320), (520, 380), (0, 255, 0), -1)

        # Random-noise templates that will not match
        rng = np.random.RandomState(999)
        noise_template_1 = rng.randint(0, 256, (30, 30), dtype=np.uint8)
        noise_template_2 = rng.randint(0, 256, (30, 30), dtype=np.uint8)
        svc._image_pipeline._marker_templates = [noise_template_1, noise_template_2]

        markers = make_markers_config(110, 110, 490, 350, width, height)
        result = svc._image_pipeline._align_with_markers(img, markers)

        assert result.success is False
        assert result.error_reason == "low_confidence"
        assert result.failed_marker in (1, 2)
        assert result.image is None
        assert len(result.marker_confidences) >= 1
        assert all(c < 0.5 for c in result.marker_confidences)

    def test_successful_alignment_returns_image(self):
        """Markers exactly at reference positions yield a successful AlignmentResult."""
        svc = make_service()
        width, height = 640, 480
        img = make_synthetic_image(width, height, seed=10)

        # Marker positions and templates extracted from the image itself
        cx1, cy1 = 120, 100
        cx2, cy2 = 520, 380
        template1 = draw_marker(img, cx1, cy1, radius=15)
        template2 = draw_marker(img, cx2, cy2, radius=15)
        svc._image_pipeline._marker_templates = [template1, template2]

        markers = make_markers_config(cx1, cy1, cx2, cy2, width, height, marker_w=30, marker_h=30)

        result = svc._image_pipeline._align_with_markers(img, markers)
        assert result.success is True
        assert result.image is not None
        assert result.image.shape == img.shape
        assert len(result.marker_confidences) == 2
        assert all(c >= 0.5 for c in result.marker_confidences)
        assert result.error_reason is None
        assert result.failed_marker is None
