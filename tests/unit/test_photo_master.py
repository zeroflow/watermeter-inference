import importlib
import sys

import pytest
import numpy as np
from PIL import Image

# The unit conftest mocks cv2 as a MagicMock. photo_master needs real cv2,
# which is available in the venv. Restore the real module before importing.
if 'cv2' in sys.modules and not hasattr(sys.modules['cv2'], 'cvtColor'):
    del sys.modules['cv2']
    import cv2  # noqa: F401 — re-import the real cv2
# Also reload photo_master in case it already imported the mocked cv2
if 'watermeter.photo_master' in sys.modules:
    importlib.reload(sys.modules['watermeter.photo_master'])


def _make_fake_arrow():
    """Create a fake arrow image: gray background + red triangle."""
    img = Image.new("RGB", (80, 80), (200, 200, 200))
    arr = np.array(img)
    # Red triangle-like region in center-top area
    for y in range(15, 45):
        for x in range(35, 45):
            arr[y, x] = [200, 40, 40]
    return Image.fromarray(arr)


class TestPointerExtractor:
    def test_extract_pointer_mask_returns_binary(self):
        from watermeter.photo_master import extract_pointer_mask
        img = _make_fake_arrow()
        mask = extract_pointer_mask(img)
        assert mask.shape == (80, 80)
        assert mask.dtype == np.uint8
        assert set(np.unique(mask)).issubset({0, 255})

    def test_extract_pointer_mask_finds_red(self):
        from watermeter.photo_master import extract_pointer_mask
        img = _make_fake_arrow()
        mask = extract_pointer_mask(img)
        assert mask.sum() > 0

    def test_inpaint_background_removes_pointer(self):
        from watermeter.photo_master import extract_pointer_mask, inpaint_background
        img = _make_fake_arrow()
        mask = extract_pointer_mask(img)
        bg = inpaint_background(img, mask)
        assert isinstance(bg, Image.Image)
        assert bg.size == (80, 80)
        arr = np.array(bg)
        red_pixels = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80)
        assert red_pixels.sum() < 10

    def test_extract_pointer_template_returns_rgba(self):
        from watermeter.photo_master import extract_pointer_mask, extract_pointer_template
        img = _make_fake_arrow()
        mask = extract_pointer_mask(img)
        template = extract_pointer_template(img, mask)
        assert isinstance(template, Image.Image)
        assert template.mode == "RGBA"
        assert template.size == (80, 80)


class TestArrowCompositor:
    def _make_compositor(self):
        from watermeter.photo_master import ArrowCompositor
        bg = Image.new("RGB", (80, 80), (200, 200, 200))
        template = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        t_arr = np.array(template)
        t_arr[10:40, 38:42, :] = [200, 40, 40, 255]
        template = Image.fromarray(t_arr, "RGBA")
        return ArrowCompositor(bg, template, source_angle_deg=0.0)

    def test_composite_returns_rgb(self):
        compositor = self._make_compositor()
        result = compositor.render("5.0")
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"
        assert result.size == (80, 80)

    def test_composite_all_classes(self):
        compositor = self._make_compositor()
        for i in range(10):
            for j in range(10):
                cls = f"{i}.{j}"
                result = compositor.render(cls)
                assert result.size == (80, 80)

    def test_different_angles_produce_different_images(self):
        compositor = self._make_compositor()
        img_00 = np.array(compositor.render("0.0"))
        img_50 = np.array(compositor.render("5.0"))
        assert not np.array_equal(img_00, img_50)


class TestBuildArrowCompositor:
    def test_build_from_photo_path(self, tmp_path):
        from watermeter.photo_master import build_arrow_compositor
        img = Image.new("RGB", (80, 80), (200, 200, 200))
        arr = np.array(img)
        arr[15:45, 35:45] = [200, 40, 40]
        Image.fromarray(arr).save(tmp_path / "test_arrow.jpg")

        compositor = build_arrow_compositor(str(tmp_path / "test_arrow.jpg"), "9.0")
        assert hasattr(compositor, "render")
        result = compositor.render("5.0")
        assert result.size == (80, 80)
        assert result.mode == "RGB"
