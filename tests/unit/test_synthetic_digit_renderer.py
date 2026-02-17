import numpy as np
import pytest
from PIL import Image


class TestDigitRenderer:
    def test_render_returns_pil_image(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("5")
        assert isinstance(img, Image.Image)

    def test_render_correct_size(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("0")
        assert img.size == (20, 32)

    def test_render_rgb_mode(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("3")
        assert img.mode == "RGB"

    def test_render_all_digit_classes(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        for digit in range(10):
            img = renderer.render(str(digit))
            assert img.size == (20, 32)

    def test_render_white_background(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("1")
        arr = np.array(img)
        corners = [arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]]
        for corner in corners:
            assert all(c > 200 for c in corner), f"Corner {corner} not white enough"

    def test_render_has_dark_pixels(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("8")
        arr = np.array(img)
        dark_pixels = np.sum(arr.mean(axis=2) < 50)
        assert dark_pixels > 10

    def test_different_digits_different_images(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img0 = np.array(renderer.render("0"))
        img1 = np.array(renderer.render("1"))
        assert not np.array_equal(img0, img1)

    def test_invalid_class_raises(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        with pytest.raises(ValueError):
            renderer.render("X")
