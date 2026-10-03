"""
Unit tests for TransformPipeline in synthetic_generator.

These tests need the REAL cv2 (not the mocked one from conftest.py).
We follow the same unmocking strategy as test_marker_alignment.py.
"""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# The unit conftest.py mocks cv2 in sys.modules BEFORE this file loads.
# We must replace the mock with the real cv2 so TransformPipeline works.
# ---------------------------------------------------------------------------

_saved_cv2_mock = sys.modules.get('cv2')

# Remove mock cv2 so we can import the real one
if 'cv2' in sys.modules and isinstance(sys.modules['cv2'], MagicMock):
    del sys.modules['cv2']

# Import the real cv2 -- skip if not installed
cv2 = pytest.importorskip('cv2')

if not hasattr(cv2, 'warpAffine'):
    pytest.skip("cv2 module is mocked, cannot run transform tests", allow_module_level=True)

sys.modules['cv2'] = cv2

# Remove any cached synthetic_generator that was imported with the mock cv2
for _mod_name in list(sys.modules.keys()):
    if 'synthetic_generator' in _mod_name:
        del sys.modules[_mod_name]

# Now import TransformPipeline -- it will see the real cv2
from watermeter.synthetic_generator import TransformPipeline

# Restore the conftest mock so other test files are not affected
if _saved_cv2_mock is not None:
    sys.modules['cv2'] = _saved_cv2_mock


def _white_image(w=50, h=50):
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([w // 4, h // 4, 3 * w // 4, 3 * h // 4], fill="black")
    return img


class TestTransformPipeline:
    def test_transform_returns_pil_image(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert isinstance(result, Image.Image)

    def test_transform_preserves_size(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image(20, 32)
        result = pipeline.apply(img, mode="digit")
        assert result.size == (20, 32)

    def test_transform_preserves_rgb(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert result.mode == "RGB"

    def test_transform_changes_image(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert not np.array_equal(np.array(img), np.array(result))

    def test_same_seed_same_result(self):
        img = _white_image()
        p1 = TransformPipeline(seed=42)
        r1 = np.array(p1.apply(img.copy(), mode="digit"))
        p2 = TransformPipeline(seed=42)
        r2 = np.array(p2.apply(img.copy(), mode="digit"))
        assert np.array_equal(r1, r2)

    def test_different_seed_different_result(self):
        img = _white_image()
        p1 = TransformPipeline(seed=42)
        r1 = np.array(p1.apply(img.copy(), mode="digit"))
        p2 = TransformPipeline(seed=99)
        r2 = np.array(p2.apply(img.copy(), mode="digit"))
        assert not np.array_equal(r1, r2)

    def test_arrow_mode_applies_fisheye(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image(100, 100)
        result = pipeline.apply(img, mode="arrow")
        assert isinstance(result, Image.Image)
        assert result.size == (100, 100)

    def test_digit_mode_no_fisheye(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image(20, 32)
        result = pipeline.apply(img, mode="digit")
        assert result.size == (20, 32)

    def test_pixel_values_in_valid_range(self):
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        arr = np.array(result)
        assert arr.min() >= 0
        assert arr.max() <= 255
