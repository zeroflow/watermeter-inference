import numpy as np
import pytest
from PIL import Image


class TestDigitRenderer:
    """Tests for the basic DigitRenderer (programmatic fallback)."""

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

    def test_render_greenish_tint(self):
        """Colors should have greenish tint like real meters."""
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("0")
        arr = np.array(img)
        assert arr[:, :, 1].mean() >= arr[:, :, 0].mean(), \
            "Green channel should be >= red (greenish tint)"

    def test_render_sufficient_contrast(self):
        """Contrast between digit and background should be strong."""
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("3")
        arr = np.array(img)
        luminance = arr.mean(axis=2)
        bg_mean = np.percentile(luminance, 75)
        digit_mean = np.percentile(luminance, 10)
        contrast = bg_mean - digit_mean
        assert contrast > 80, f"Contrast should be strong (got {contrast})"

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


class TestDigitCompositor:
    """Tests for photo-based digit compositing onto real backgrounds."""

    def _make_fake_background(self, tmp_path):
        """Create a fake background image mimicking real meter slot."""
        img = Image.new("RGB", (28, 40), (155, 175, 160))
        arr = np.array(img)
        # Add darker border edges
        arr[:2, :] = [100, 120, 105]
        arr[-2:, :] = [100, 120, 105]
        arr[:, :2] = [100, 120, 105]
        arr[:, -2:] = [100, 120, 105]
        bg_path = tmp_path / "bg_test.jpg"
        Image.fromarray(arr).save(bg_path)
        return bg_path

    def test_compositor_returns_rgb(self, tmp_path):
        from watermeter.synthetic_generator import DigitCompositor
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        self._make_fake_background(bg_dir).rename(bg_dir / "bg_test.jpg")
        compositor = DigitCompositor(str(bg_dir))
        result = compositor.render("3")
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"
        assert result.size == (20, 32)

    def test_compositor_all_classes(self, tmp_path):
        from watermeter.synthetic_generator import DigitCompositor
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        self._make_fake_background(bg_dir).rename(bg_dir / "bg_test.jpg")
        compositor = DigitCompositor(str(bg_dir))
        for digit in range(10):
            result = compositor.render(str(digit))
            assert result.size == (20, 32)

    def test_compositor_different_digits_differ(self, tmp_path):
        from watermeter.synthetic_generator import DigitCompositor
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        self._make_fake_background(bg_dir).rename(bg_dir / "bg_test.jpg")
        compositor = DigitCompositor(str(bg_dir))
        img0 = np.array(compositor.render("0"))
        img1 = np.array(compositor.render("1"))
        assert not np.array_equal(img0, img1)

    def test_compositor_has_dark_digit_pixels(self, tmp_path):
        from watermeter.synthetic_generator import DigitCompositor
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        self._make_fake_background(bg_dir).rename(bg_dir / "bg_test.jpg")
        compositor = DigitCompositor(str(bg_dir))
        result = compositor.render("8")
        arr = np.array(result)
        dark_mask = arr.mean(axis=2) < 60
        assert dark_mask.sum() > 10, "Should have dark pixels for the digit"

    def test_compositor_random_bg_selection(self, tmp_path):
        """With multiple backgrounds, compositor should use them."""
        from watermeter.synthetic_generator import DigitCompositor
        bg_dir = tmp_path / "backgrounds"
        bg_dir.mkdir()
        # Create two visually distinct backgrounds
        img1 = Image.new("RGB", (28, 40), (155, 175, 160))
        img1.save(bg_dir / "bg1.jpg")
        img2 = Image.new("RGB", (28, 40), (145, 165, 150))
        img2.save(bg_dir / "bg2.jpg")
        compositor = DigitCompositor(str(bg_dir))
        assert len(compositor._backgrounds) == 2
