"""Tests for fisheye correction helper.

Note: cv2 is mocked in unit test conftest. Tests that exercise cv2.undistort
patch the module-level cv2 reference in roi.py with the real cv2 module.
"""
import sys

import numpy as np


def _real_cv2():
    """Return the real cv2 module, bypassing the unit-test mock.

    The conftest replaces cv2 in sys.modules with a MagicMock. We temporarily
    remove it, import the real module, then put the mock back.
    """
    cv2_mod = sys.modules.get('cv2')
    if cv2_mod is not None and hasattr(cv2_mod, 'undistort'):
        return cv2_mod  # already the real cv2
    # Temporarily remove the mock so the real cv2 can be imported
    mock_cv2 = sys.modules.pop('cv2', None)
    try:
        import cv2 as real_cv2_module
        return real_cv2_module
    finally:
        # Restore the mock so other unit tests are not affected
        if mock_cv2 is not None:
            sys.modules['cv2'] = mock_cv2


def _make_circle_image(size=200):
    """Create a test image with a ring drawn via numpy (no cv2 needed)."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    cx, cy, r = size // 2, size // 2, size * 2 // 5
    ys, xs = np.ogrid[:size, :size]
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    mask = (dist >= r - 1.5) & (dist <= r + 1.5)
    img[mask] = [255, 255, 255]
    return img


def test_apply_fisheye_no_correction():
    """k1=0 should return the original image object unchanged (identity fast-path)."""
    from watermeter.routes.roi import _apply_fisheye_correction
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[50, 100] = [255, 255, 255]  # single white pixel at center
    result = _apply_fisheye_correction(img, 0.0)
    assert result.shape == img.shape
    # k1=0 returns original object unchanged
    assert result is img


def test_apply_fisheye_barrel_correction():
    """Positive k1 (barrel) should produce a different image via cv2.undistort."""
    import unittest.mock
    real_cv2 = _real_cv2()
    with unittest.mock.patch('watermeter.routes.roi.cv2', real_cv2):
        from watermeter.routes.roi import _apply_fisheye_correction
        img = _make_circle_image(200)
        result = _apply_fisheye_correction(img, 0.5)
    assert result.shape == img.shape
    # The images should differ (distortion was applied)
    assert not np.array_equal(result, img)


def test_apply_fisheye_pincushion_correction():
    """Negative k1 (pincushion) should also produce a different image via cv2.undistort."""
    import unittest.mock
    real_cv2 = _real_cv2()
    with unittest.mock.patch('watermeter.routes.roi.cv2', real_cv2):
        from watermeter.routes.roi import _apply_fisheye_correction
        img = _make_circle_image(200)
        result = _apply_fisheye_correction(img, -0.5)
    assert result.shape == img.shape
    assert not np.array_equal(result, img)
