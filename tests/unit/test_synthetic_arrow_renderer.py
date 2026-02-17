import pytest
import numpy as np
from PIL import Image


class TestArrowRenderer:
    def test_render_returns_pil_image(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("5.0")
        assert isinstance(img, Image.Image)

    def test_render_square_image(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("0.0")
        w, h = img.size
        assert w == h

    def test_render_default_size(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("0.0")
        assert img.size == (100, 100)

    def test_render_rgb_mode(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("3.7")
        assert img.mode == "RGB"

    def test_render_all_100_classes(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        for i in range(10):
            for j in range(10):
                cls = f"{i}.{j}"
                img = renderer.render(cls)
                assert img.size == (100, 100)

    def test_render_has_red_pixels(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("5.0")
        arr = np.array(img)
        red_mask = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 100) & (arr[:, :, 2] < 100)
        assert red_mask.sum() > 20

    def test_render_has_dark_pixels_for_ticks(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("0.0")
        arr = np.array(img)
        dark_pixels = np.sum(arr.mean(axis=2) < 30)
        assert dark_pixels > 10

    def test_different_classes_different_images(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img_00 = np.array(renderer.render("0.0"))
        img_50 = np.array(renderer.render("5.0"))
        assert not np.array_equal(img_00, img_50)

    def test_opposite_classes_pointer_opposite(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img_00 = np.array(renderer.render("0.0"))
        img_50 = np.array(renderer.render("5.0"))
        red_00 = (img_00[:, :, 0] > 150) & (img_00[:, :, 1] < 100)
        red_50 = (img_50[:, :, 0] > 150) & (img_50[:, :, 1] < 100)
        if red_00.sum() > 0 and red_50.sum() > 0:
            cy_00 = np.where(red_00)[0].mean()
            cy_50 = np.where(red_50)[0].mean()
            assert abs(cy_00 - cy_50) > 10

    def test_invalid_class_raises(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        with pytest.raises(ValueError):
            renderer.render("10.0")
