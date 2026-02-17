# Synthetic Data Generator — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate license-free synthetic training data (digits + arrows) with realistic transforms, integrated into the web UI.

**Architecture:** Single module `watermeter/synthetic_generator.py` with PIL rendering + OpenCV transforms. Output goes directly to `/training/{type}/ground_truth/{class}/synth_{idx}.jpg`. API routes in `watermeter/routes/synthetic.py`, UI in existing training page. Background thread with polling (same pattern as training).

**Tech Stack:** PIL/Pillow (rendering, font), OpenCV (fisheye, perspective), NumPy (noise), FastAPI (routes), Jinja2/HTMX (UI)

**Design Doc:** `docs/plans/2026-02-17-synthetic-data-generator-design.md`

---

## Task 1: DigitRenderer — Core Rendering

**Files:**
- Create: `watermeter/synthetic_generator.py`
- Create: `tests/unit/test_synthetic_digit_renderer.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_synthetic_digit_renderer.py
import pytest
import numpy as np
from PIL import Image


class TestDigitRenderer:
    """Test digit master image rendering."""

    def test_render_returns_pil_image(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("5")
        assert isinstance(img, Image.Image)

    def test_render_correct_size(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("0")
        assert img.size == (20, 32)  # width x height

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
        # Corners should be white-ish (background)
        corners = [arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]]
        for corner in corners:
            assert all(c > 200 for c in corner), f"Corner {corner} not white enough"

    def test_render_has_dark_pixels(self):
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("8")
        arr = np.array(img)
        # Should have some dark pixels (the digit)
        dark_pixels = np.sum(arr.mean(axis=2) < 50)
        assert dark_pixels > 10, "No dark pixels found — digit not rendered"

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
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watermeter.synthetic_generator'`

**Step 3: Write minimal implementation**

```python
# watermeter/synthetic_generator.py
"""Synthetic training data generator for watermeter digits and arrows."""

import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Font path — Liberation Mono is license-free and available in the container
_FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"
_FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf"

DIGIT_CLASSES = [str(i) for i in range(10)]
ARROW_CLASSES = [f"{i}.{j}" for i in range(10) for j in range(10)]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a monospace font, with fallback."""
    for path in [_FONT_PATH, _FALLBACK_FONT_PATH]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class DigitRenderer:
    """Renders synthetic digit master images.

    Produces 20x32px RGB images with a black digit on white background,
    matching the format of real watermeter digit captures.
    """

    WIDTH = 20
    HEIGHT = 32

    def __init__(self):
        self._font = _load_font(28)

    def render(self, digit_class: str) -> Image.Image:
        """Render a single digit master image.

        Args:
            digit_class: Digit class "0"-"9".

        Returns:
            PIL Image (RGB, 20x32).

        Raises:
            ValueError: If digit_class is not "0"-"9".
        """
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}. Must be one of {DIGIT_CLASSES}")

        img = Image.new("RGB", (self.WIDTH, self.HEIGHT), "white")
        draw = ImageDraw.Draw(img)

        # Center the digit
        bbox = draw.textbbox((0, 0), digit_class, font=self._font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (self.WIDTH - text_w) / 2 - bbox[0]
        y = (self.HEIGHT - text_h) / 2 - bbox[1]

        draw.text((x, y), digit_class, fill="black", font=self._font)
        return img
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py -v`
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_digit_renderer.py
git commit -m "claude: add DigitRenderer for synthetic data generation"
```

---

## Task 2: ArrowRenderer — Dial Rendering

**Files:**
- Modify: `watermeter/synthetic_generator.py`
- Create: `tests/unit/test_synthetic_arrow_renderer.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_synthetic_arrow_renderer.py
import pytest
import numpy as np
from PIL import Image


class TestArrowRenderer:
    """Test arrow dial master image rendering."""

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
        assert w == h, f"Expected square, got {w}x{h}"

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
                assert img.size == (100, 100), f"Failed for class {cls}"

    def test_render_has_red_pixels(self):
        """Arrow pointer should be red."""
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("5.0")
        arr = np.array(img)
        # Red channel high, green/blue low
        red_mask = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 100) & (arr[:, :, 2] < 100)
        assert red_mask.sum() > 20, "No red pixels found — arrow not rendered"

    def test_render_has_dark_pixels_for_ticks(self):
        """Tick marks should be black."""
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img = renderer.render("0.0")
        arr = np.array(img)
        dark_pixels = np.sum(arr.mean(axis=2) < 30)
        assert dark_pixels > 10, "No dark pixels — ticks not rendered"

    def test_different_classes_different_images(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img_00 = np.array(renderer.render("0.0"))
        img_50 = np.array(renderer.render("5.0"))
        assert not np.array_equal(img_00, img_50)

    def test_opposite_classes_pointer_opposite(self):
        """0.0 and 5.0 should have pointer on opposite sides."""
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        img_00 = np.array(renderer.render("0.0"))
        img_50 = np.array(renderer.render("5.0"))
        # Check red pixel center of mass
        red_00 = (img_00[:, :, 0] > 150) & (img_00[:, :, 1] < 100)
        red_50 = (img_50[:, :, 0] > 150) & (img_50[:, :, 1] < 100)
        if red_00.sum() > 0 and red_50.sum() > 0:
            cy_00 = np.where(red_00)[0].mean()
            cy_50 = np.where(red_50)[0].mean()
            # 0.0 pointer should be in different Y region than 5.0
            assert abs(cy_00 - cy_50) > 10, "Pointer positions too similar for opposite classes"

    def test_invalid_class_raises(self):
        from watermeter.synthetic_generator import ArrowRenderer
        renderer = ArrowRenderer()
        with pytest.raises(ValueError):
            renderer.render("10.0")
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_arrow_renderer.py -v`
Expected: FAIL with `ImportError: cannot import name 'ArrowRenderer'`

**Step 3: Write implementation**

Add to `watermeter/synthetic_generator.py`:

```python
import math

class ArrowRenderer:
    """Renders synthetic arrow/dial master images.

    Produces 100x100px RGB images with a round dial face:
    - White background
    - Black tick marks (10 major for 0-9)
    - Red pointer rotated to the target position
    - One full rotation = 0.0 to 9.9 (10.0 wraps to 0.0)
    """

    SIZE = 100

    def render(self, arrow_class: str) -> Image.Image:
        """Render a single arrow dial master image.

        Args:
            arrow_class: Arrow class "0.0"-"9.9".

        Returns:
            PIL Image (RGB, 100x100).

        Raises:
            ValueError: If arrow_class is not valid.
        """
        if arrow_class not in ARROW_CLASSES:
            raise ValueError(f"Invalid arrow class: {arrow_class!r}")

        value = float(arrow_class)
        angle_deg = (value / 10.0) * 360.0  # 0.0->0°, 5.0->180°, 9.9->356.4°

        img = Image.new("RGB", (self.SIZE, self.SIZE), "white")
        draw = ImageDraw.Draw(img)
        cx, cy = self.SIZE / 2, self.SIZE / 2
        radius = self.SIZE / 2 - 5

        # Draw tick marks
        for tick in range(10):
            tick_angle = math.radians((tick / 10.0) * 360.0 - 90)  # -90 to start at top
            outer_r = radius
            inner_r = radius - 10
            x1 = cx + inner_r * math.cos(tick_angle)
            y1 = cy + inner_r * math.sin(tick_angle)
            x2 = cx + outer_r * math.cos(tick_angle)
            y2 = cy + outer_r * math.sin(tick_angle)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=2)

        # Draw minor ticks
        for tick in range(100):
            if tick % 10 == 0:
                continue
            tick_angle = math.radians((tick / 100.0) * 360.0 - 90)
            outer_r = radius
            inner_r = radius - 4
            x1 = cx + inner_r * math.cos(tick_angle)
            y1 = cy + inner_r * math.sin(tick_angle)
            x2 = cx + outer_r * math.cos(tick_angle)
            y2 = cy + outer_r * math.sin(tick_angle)
            draw.line([(x1, y1), (x2, y2)], fill="black", width=1)

        # Draw center dot
        draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill="black")

        # Draw red pointer
        pointer_angle = math.radians(angle_deg - 90)  # -90 to start at top
        pointer_len = radius - 14
        px = cx + pointer_len * math.cos(pointer_angle)
        py = cy + pointer_len * math.sin(pointer_angle)
        draw.line([(cx, cy), (px, py)], fill="red", width=3)

        # Small red triangle at pointer tip
        tip_size = 4
        perp_angle = pointer_angle + math.pi / 2
        t1 = (px, py)
        t2 = (px - tip_size * math.cos(perp_angle) - tip_size * math.cos(pointer_angle),
              py - tip_size * math.sin(perp_angle) - tip_size * math.sin(pointer_angle))
        t3 = (px + tip_size * math.cos(perp_angle) - tip_size * math.cos(pointer_angle),
              py + tip_size * math.sin(perp_angle) - tip_size * math.sin(pointer_angle))
        draw.polygon([t1, t2, t3], fill="red")

        return img
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_arrow_renderer.py -v`
Expected: All 10 tests PASS

**Step 5: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_arrow_renderer.py
git commit -m "claude: add ArrowRenderer for synthetic dial rendering"
```

---

## Task 3: TransformPipeline — Realistic Distortions

**Files:**
- Modify: `watermeter/synthetic_generator.py`
- Create: `tests/unit/test_synthetic_transforms.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_synthetic_transforms.py
import pytest
import numpy as np
from PIL import Image


def _white_image(w=50, h=50):
    """Create a simple test image with a black square in center."""
    img = Image.new("RGB", (w, h), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([w // 4, h // 4, 3 * w // 4, 3 * h // 4], fill="black")
    return img


class TestTransformPipeline:
    """Test the transform pipeline."""

    def test_transform_returns_pil_image(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert isinstance(result, Image.Image)

    def test_transform_preserves_size(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image(20, 32)
        result = pipeline.apply(img, mode="digit")
        assert result.size == (20, 32)

    def test_transform_preserves_rgb(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert result.mode == "RGB"

    def test_transform_changes_image(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        assert not np.array_equal(np.array(img), np.array(result))

    def test_same_seed_same_result(self):
        from watermeter.synthetic_generator import TransformPipeline
        img = _white_image()
        p1 = TransformPipeline(seed=42)
        r1 = np.array(p1.apply(img.copy(), mode="digit"))
        p2 = TransformPipeline(seed=42)
        r2 = np.array(p2.apply(img.copy(), mode="digit"))
        assert np.array_equal(r1, r2)

    def test_different_seed_different_result(self):
        from watermeter.synthetic_generator import TransformPipeline
        img = _white_image()
        p1 = TransformPipeline(seed=42)
        r1 = np.array(p1.apply(img.copy(), mode="digit"))
        p2 = TransformPipeline(seed=99)
        r2 = np.array(p2.apply(img.copy(), mode="digit"))
        assert not np.array_equal(r1, r2)

    def test_arrow_mode_applies_fisheye(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image(100, 100)
        result = pipeline.apply(img, mode="arrow")
        # Just verify it runs without error and changes the image
        assert isinstance(result, Image.Image)
        assert result.size == (100, 100)

    def test_digit_mode_no_fisheye(self):
        """Digit mode should not crash even though fisheye is disabled."""
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image(20, 32)
        result = pipeline.apply(img, mode="digit")
        assert result.size == (20, 32)

    def test_pixel_values_in_valid_range(self):
        from watermeter.synthetic_generator import TransformPipeline
        pipeline = TransformPipeline(seed=42)
        img = _white_image()
        result = pipeline.apply(img, mode="digit")
        arr = np.array(result)
        assert arr.min() >= 0
        assert arr.max() <= 255
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_transforms.py -v`
Expected: FAIL with `ImportError: cannot import name 'TransformPipeline'`

**Step 3: Write implementation**

Add to `watermeter/synthetic_generator.py`:

```python
import random
import cv2
import numpy as np

class TransformPipeline:
    """Applies realistic distortions to master images.

    Each call to apply() uses a random subset of transforms at random intensity.
    Uses PIL for color transforms and OpenCV for geometric transforms.

    Args:
        seed: Random seed for reproducibility.
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._np_rng = np.random.RandomState(seed)

    def apply(self, img: Image.Image, mode: str = "digit") -> Image.Image:
        """Apply random transforms to an image.

        Args:
            img: Input PIL Image.
            mode: "digit" or "arrow" — controls which transforms are active.

        Returns:
            Transformed PIL Image (same size and mode).
        """
        original_size = img.size
        arr = np.array(img)

        # --- Geometric transforms (OpenCV) ---
        # X/Y offset
        if self._rng.random() < 0.8:
            max_shift = 3 if mode == "digit" else 5
            dx = self._rng.randint(-max_shift, max_shift)
            dy = self._rng.randint(-max_shift, max_shift)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            arr = cv2.warpAffine(arr, M, (arr.shape[1], arr.shape[0]),
                                 borderMode=cv2.BORDER_REPLICATE)

        # Perspective warp
        if self._rng.random() < 0.7:
            strength = 3 if mode == "digit" else 8
            h, w = arr.shape[:2]
            pts1 = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
            pts2 = np.float32([
                [self._rng.randint(0, strength), self._rng.randint(0, strength)],
                [w - self._rng.randint(0, strength), self._rng.randint(0, strength)],
                [self._rng.randint(0, strength), h - self._rng.randint(0, strength)],
                [w - self._rng.randint(0, strength), h - self._rng.randint(0, strength)],
            ])
            M = cv2.getPerspectiveTransform(pts1, pts2)
            arr = cv2.warpPerspective(arr, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        # Fisheye / barrel distortion (arrows only)
        if mode == "arrow" and self._rng.random() < 0.8:
            h, w = arr.shape[:2]
            k1 = self._rng.uniform(0.1, 0.4)
            cx, cy = w / 2, h / 2
            map_x = np.zeros((h, w), dtype=np.float32)
            map_y = np.zeros((h, w), dtype=np.float32)
            for y in range(h):
                for x in range(w):
                    nx = (x - cx) / cx
                    ny = (y - cy) / cy
                    r = math.sqrt(nx * nx + ny * ny)
                    nr = r * (1 + k1 * r * r)
                    map_x[y, x] = cx + nr * (nx / max(r, 1e-6)) * cx
                    map_y[y, x] = cy + nr * (ny / max(r, 1e-6)) * cy
            arr = cv2.remap(arr, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)

        # --- Color transforms ---
        arr = arr.astype(np.float32)

        # Brightness variation
        if self._rng.random() < 0.8:
            factor = self._rng.uniform(0.7, 1.3)
            arr = arr * factor

        # Contrast variation
        if self._rng.random() < 0.7:
            factor = self._rng.uniform(0.8, 1.2)
            mean = arr.mean()
            arr = (arr - mean) * factor + mean

        # Color cast (white balance)
        if self._rng.random() < 0.5:
            cast = np.array([
                self._rng.uniform(-10, 10),
                self._rng.uniform(-10, 10),
                self._rng.uniform(-10, 10),
            ], dtype=np.float32)
            arr = arr + cast

        # Background variation (push white toward cream/gray)
        if self._rng.random() < 0.6:
            bg_shift = self._rng.uniform(-15, 0)  # Darken whites slightly
            white_mask = (arr > 200).all(axis=2)
            arr[white_mask] += bg_shift

        # Shadow
        if self._rng.random() < 0.5:
            h, w = arr.shape[:2]
            shadow_dir = self._rng.uniform(0, 2 * math.pi)
            Y, X = np.mgrid[0:h, 0:w]
            cx, cy = w / 2, h / 2
            gradient = ((X - cx) * math.cos(shadow_dir) + (Y - cy) * math.sin(shadow_dir))
            gradient = gradient / max(gradient.max() - gradient.min(), 1e-6)
            shadow_strength = self._rng.uniform(10, 30)
            arr = arr - (gradient[:, :, np.newaxis] * shadow_strength)

        # Vignetting (arrows only)
        if mode == "arrow" and self._rng.random() < 0.6:
            h, w = arr.shape[:2]
            Y, X = np.mgrid[0:h, 0:w]
            cx, cy = w / 2, h / 2
            dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
            max_dist = math.sqrt(cx ** 2 + cy ** 2)
            vignette = 1.0 - (dist / max_dist) ** 2 * self._rng.uniform(0.2, 0.5)
            arr = arr * vignette[:, :, np.newaxis]

        # Clip and convert back
        arr = np.clip(arr, 0, 255).astype(np.uint8)

        # --- Noise / degradation ---
        # Gaussian noise
        if self._rng.random() < 0.7:
            sigma = self._rng.uniform(5, 15)
            noise = self._np_rng.normal(0, sigma, arr.shape).astype(np.float32)
            arr = np.clip(arr.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        # Blur
        if self._rng.random() < 0.5:
            sigma = self._rng.uniform(0.3, 1.5)
            ksize = int(sigma * 4) | 1  # Ensure odd
            if ksize >= 3:
                arr = cv2.GaussianBlur(arr, (ksize, ksize), sigma)

        # JPEG artifacts (encode/decode cycle)
        if self._rng.random() < 0.6:
            quality = self._rng.randint(60, 95)
            _, encoded = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, quality])
            arr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

        # Ensure output matches original size
        result = Image.fromarray(arr)
        if result.size != original_size:
            result = result.resize(original_size, Image.BILINEAR)

        return result
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_transforms.py -v`
Expected: All 9 tests PASS

**Step 5: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_transforms.py
git commit -m "claude: add TransformPipeline with realistic distortions"
```

---

## Task 4: SyntheticGenerator — Orchestrator

**Files:**
- Modify: `watermeter/synthetic_generator.py`
- Create: `tests/unit/test_synthetic_generator.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_synthetic_generator.py
import pytest
import tempfile
from pathlib import Path


class TestSyntheticGenerator:
    """Test the orchestrator that generates full datasets."""

    def test_generate_digits(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=3, seed=42)

        gt_dir = tmp_path / "digits" / "ground_truth"
        for digit in range(10):
            class_dir = gt_dir / str(digit)
            assert class_dir.exists(), f"Missing class dir: {class_dir}"
            images = list(class_dir.glob("synth_*.jpg"))
            assert len(images) == 3, f"Expected 3 images in {class_dir}, got {len(images)}"

    def test_generate_arrows(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="arrows", count_per_class=2, seed=42)

        gt_dir = tmp_path / "arrows" / "ground_truth"
        for i in range(10):
            for j in range(10):
                class_dir = gt_dir / f"{i}.{j}"
                assert class_dir.exists(), f"Missing class dir: {class_dir}"
                images = list(class_dir.glob("synth_*.jpg"))
                assert len(images) == 2, f"Expected 2 images in {class_dir}, got {len(images)}"

    def test_generate_both(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="both", count_per_class=1, seed=42)

        assert (tmp_path / "digits" / "ground_truth" / "0").exists()
        assert (tmp_path / "arrows" / "ground_truth" / "0.0").exists()

    def test_images_are_valid_jpeg(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        from PIL import Image
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)

        img_path = next((tmp_path / "digits" / "ground_truth" / "5").glob("synth_*.jpg"))
        img = Image.open(img_path)
        assert img.mode == "RGB"

    def test_reproducible_with_seed(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        import numpy as np
        from PIL import Image

        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"

        gen1 = SyntheticGenerator(base_dir=str(dir1))
        gen1.generate(type="digits", count_per_class=1, seed=42)

        gen2 = SyntheticGenerator(base_dir=str(dir2))
        gen2.generate(type="digits", count_per_class=1, seed=42)

        img1 = Image.open(next((dir1 / "digits" / "ground_truth" / "3").glob("synth_*.jpg")))
        img2 = Image.open(next((dir2 / "digits" / "ground_truth" / "3").glob("synth_*.jpg")))
        assert np.array_equal(np.array(img1), np.array(img2))

    def test_progress_callback(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        progress_calls = []
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(
            type="digits",
            count_per_class=2,
            seed=42,
            progress_callback=lambda cur, total, msg: progress_calls.append((cur, total, msg)),
        )
        assert len(progress_calls) > 0
        last = progress_calls[-1]
        assert last[0] == last[1], "Final progress should be 100%"

    def test_synth_prefix(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)

        for img_path in (tmp_path / "digits" / "ground_truth" / "0").glob("*.jpg"):
            assert img_path.name.startswith("synth_"), f"Missing synth_ prefix: {img_path.name}"

    def test_does_not_overwrite_existing(self, tmp_path):
        """Existing non-synth files should not be touched."""
        from watermeter.synthetic_generator import SyntheticGenerator
        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)
        existing = gt_dir / "real_image.jpg"
        existing.write_bytes(b"original")

        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)

        assert existing.read_bytes() == b"original"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py -v`
Expected: FAIL with `ImportError: cannot import name 'SyntheticGenerator'`

**Step 3: Write implementation**

Add to `watermeter/synthetic_generator.py`:

```python
from typing import Callable, Optional

class SyntheticGenerator:
    """Orchestrates synthetic data generation.

    Renders master images and applies transforms to generate
    a full synthetic training dataset.

    Args:
        base_dir: Base directory for output (contains digits/ and arrows/ subdirs).
    """

    def __init__(self, base_dir: str = "/training"):
        self.base_dir = Path(base_dir)

    def generate(
        self,
        type: str,
        count_per_class: int = 500,
        seed: int = 42,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict:
        """Generate synthetic training data.

        Args:
            type: "digits", "arrows", or "both".
            count_per_class: Number of images per class.
            seed: Random seed for reproducibility.
            progress_callback: Optional callback(current, total, message).

        Returns:
            Dict with generation stats.
        """
        stats = {"digits": 0, "arrows": 0}
        types = []
        if type in ("digits", "both"):
            types.append("digits")
        if type in ("arrows", "both"):
            types.append("arrows")

        total_images = 0
        for t in types:
            classes = DIGIT_CLASSES if t == "digits" else ARROW_CLASSES
            total_images += len(classes) * count_per_class

        current = 0
        for t in types:
            if t == "digits":
                renderer = DigitRenderer()
                classes = DIGIT_CLASSES
                mode = "digit"
            else:
                renderer = ArrowRenderer()
                classes = ARROW_CLASSES
                mode = "arrow"

            gt_dir = self.base_dir / t / "ground_truth"

            for cls in classes:
                class_dir = gt_dir / cls
                class_dir.mkdir(parents=True, exist_ok=True)

                master = renderer.render(cls)

                for i in range(count_per_class):
                    # Each image gets a unique seed derived from base seed
                    img_seed = seed + hash((cls, i)) % (2**31)
                    pipeline = TransformPipeline(seed=img_seed)
                    transformed = pipeline.apply(master.copy(), mode=mode)

                    filename = f"synth_{i:04d}.jpg"
                    transformed.save(str(class_dir / filename), "JPEG", quality=90)
                    stats[t] += 1
                    current += 1

                    if progress_callback and current % 10 == 0:
                        progress_callback(current, total_images, f"Generating {t} class {cls}")

        if progress_callback:
            progress_callback(total_images, total_images, "Generation complete")

        return stats
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py -v`
Expected: All 8 tests PASS

**Step 5: Run all synthetic tests together**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py tests/unit/test_synthetic_arrow_renderer.py tests/unit/test_synthetic_transforms.py tests/unit/test_synthetic_generator.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_generator.py
git commit -m "claude: add SyntheticGenerator orchestrator"
```

---

## Task 5: Delete Synthetic Data Function

**Files:**
- Modify: `watermeter/synthetic_generator.py`
- Modify: `tests/unit/test_synthetic_generator.py`

**Step 1: Write the failing test**

Add to `tests/unit/test_synthetic_generator.py`:

```python
class TestDeleteSynthetic:
    def test_delete_removes_synth_files(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=2, seed=42)

        # Verify files exist
        assert len(list((tmp_path / "digits" / "ground_truth" / "0").glob("synth_*.jpg"))) == 2

        deleted = gen.delete_synthetic(type="digits")
        assert deleted > 0
        assert len(list((tmp_path / "digits" / "ground_truth" / "0").glob("synth_*.jpg"))) == 0

    def test_delete_preserves_real_files(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)
        (gt_dir / "real_001.jpg").write_bytes(b"real")

        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)
        gen.delete_synthetic(type="digits")

        assert (gt_dir / "real_001.jpg").read_bytes() == b"real"

    def test_delete_returns_count(self, tmp_path):
        from watermeter.synthetic_generator import SyntheticGenerator
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=3, seed=42)
        deleted = gen.delete_synthetic(type="digits")
        assert deleted == 30  # 10 classes * 3 images
```

**Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py::TestDeleteSynthetic -v`
Expected: FAIL with `AttributeError: 'SyntheticGenerator' object has no attribute 'delete_synthetic'`

**Step 3: Implement**

Add to `SyntheticGenerator` class:

```python
    def delete_synthetic(self, type: str) -> int:
        """Delete all synthetic images (synth_* prefix) from ground truth.

        Args:
            type: "digits", "arrows", or "both".

        Returns:
            Number of files deleted.
        """
        deleted = 0
        types = []
        if type in ("digits", "both"):
            types.append("digits")
        if type in ("arrows", "both"):
            types.append("arrows")

        for t in types:
            gt_dir = self.base_dir / t / "ground_truth"
            if not gt_dir.exists():
                continue
            for synth_file in gt_dir.rglob("synth_*.jpg"):
                synth_file.unlink()
                deleted += 1

        return deleted
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_generator.py
git commit -m "claude: add delete_synthetic to clean up generated images"
```

---

## Task 6: API Routes

**Files:**
- Create: `watermeter/routes/synthetic.py`
- Create: `tests/unit/test_synthetic_routes.py`
- Modify: `watermeter/app.py` (register router)

**Step 1: Write the failing tests**

```python
# tests/unit/test_synthetic_routes.py
import pytest
from unittest.mock import patch, MagicMock


class TestSyntheticRoutes:
    """Test synthetic data generation API routes."""

    def test_generate_endpoint_exists(self, test_client):
        response = test_client.post("/api/synthetic/generate", json={
            "type": "digits",
            "count_per_class": 5,
            "seed": 42,
        })
        assert response.status_code in (200, 409)  # 200 OK or 409 if already running

    def test_generate_returns_job_id(self, test_client):
        response = test_client.post("/api/synthetic/generate", json={
            "type": "digits",
            "count_per_class": 5,
            "seed": 42,
        })
        data = response.json()
        if response.status_code == 200:
            assert "job_id" in data
            assert data["success"] is True

    def test_generate_invalid_type(self, test_client):
        response = test_client.post("/api/synthetic/generate", json={
            "type": "invalid",
            "count_per_class": 5,
            "seed": 42,
        })
        assert response.status_code == 422  # Pydantic validation error

    def test_status_endpoint_exists(self, test_client):
        response = test_client.get("/api/synthetic/status")
        assert response.status_code == 200

    def test_delete_endpoint_exists(self, test_client):
        response = test_client.delete("/api/synthetic/digits")
        assert response.status_code == 200

    def test_delete_returns_count(self, test_client):
        response = test_client.delete("/api/synthetic/digits")
        data = response.json()
        assert "deleted" in data
```

**Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_routes.py -v`
Expected: FAIL (404 — routes don't exist yet)

**Step 3: Implement routes**

```python
# watermeter/routes/synthetic.py
"""Synthetic data generation routes."""

import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ..synthetic_generator import SyntheticGenerator

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level state for background generation
_generation_lock = threading.Lock()
_generation_status = {
    "running": False,
    "job_id": None,
    "progress": 0,
    "total": 0,
    "message": "Idle",
    "type": None,
}
_cancel_flag = threading.Event()


class SyntheticConfig(BaseModel):
    """Request model for synthetic data generation."""

    type: str  # "digits", "arrows", or "both"
    count_per_class: int = 500
    seed: int = 42

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        if v not in ("digits", "arrows", "both"):
            raise ValueError("type must be 'digits', 'arrows', or 'both'")
        return v

    @field_validator("count_per_class")
    @classmethod
    def validate_count(cls, v):
        if v < 1 or v > 10000:
            raise ValueError("count_per_class must be between 1 and 10000")
        return v


def _run_generation(config: SyntheticConfig, job_id: str):
    """Background thread for synthetic data generation."""
    global _generation_status
    try:
        gen = SyntheticGenerator(base_dir="/training")

        def on_progress(current, total, message):
            _generation_status.update({
                "progress": current,
                "total": total,
                "message": message,
            })

        stats = gen.generate(
            type=config.type,
            count_per_class=config.count_per_class,
            seed=config.seed,
            progress_callback=on_progress,
        )

        _generation_status.update({
            "running": False,
            "message": f"Complete: {stats}",
        })
    except Exception as e:
        logger.exception("Synthetic generation failed")
        _generation_status.update({
            "running": False,
            "message": f"Error: {e}",
        })


@router.post(
    "/api/synthetic/generate",
    tags=["Synthetic Data"],
    summary="Start synthetic data generation",
)
async def generate_synthetic(config: SyntheticConfig):
    global _generation_status

    if _generation_status["running"]:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "Generation already running"},
        )

    # Check training is not running
    from ..training_manager import get_training_manager
    tm = get_training_manager()
    if tm.active_training_job and tm.active_training_job.status.value == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "Training is running — cannot generate during training"},
        )

    import uuid
    job_id = f"synth_{uuid.uuid4().hex[:8]}"

    _generation_status.update({
        "running": True,
        "job_id": job_id,
        "progress": 0,
        "total": 0,
        "message": "Starting...",
        "type": config.type,
    })

    thread = threading.Thread(target=_run_generation, args=(config, job_id), daemon=True)
    thread.start()

    return JSONResponse(content={"success": True, "job_id": job_id})


@router.get(
    "/api/synthetic/status",
    tags=["Synthetic Data"],
    summary="Get synthetic generation status",
)
async def get_synthetic_status():
    return JSONResponse(content=_generation_status)


@router.delete(
    "/api/synthetic/{type}",
    tags=["Synthetic Data"],
    summary="Delete synthetic images",
)
async def delete_synthetic(type: str):
    if type not in ("digits", "arrows", "both"):
        raise HTTPException(status_code=400, detail="type must be 'digits', 'arrows', or 'both'")

    if _generation_status["running"]:
        raise HTTPException(status_code=409, detail="Cannot delete while generation is running")

    gen = SyntheticGenerator(base_dir="/training")
    deleted = gen.delete_synthetic(type=type)

    return JSONResponse(content={"success": True, "deleted": deleted, "type": type})
```

**Step 4: Register router in app.py**

Add to `watermeter/app.py` after the existing `include_router` lines:

```python
from .routes.synthetic import router as synthetic_router
app.include_router(synthetic_router)
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_routes.py -v`
Expected: All 6 tests PASS

**Step 6: Commit**

```bash
git add watermeter/routes/synthetic.py watermeter/app.py tests/unit/test_synthetic_routes.py
git commit -m "claude: add API routes for synthetic data generation"
```

---

## Task 7: Web UI — Generation Controls

**Files:**
- Modify: `watermeter/templates/training.html`
- Modify: `watermeter/static/training.js`

**Step 1: Add HTML section to training.html**

Add after the "Training Data Stats" section (before the training form), a new card:

```html
<!-- Synthetic Data Generation -->
<div class="card" id="synthetic-section">
    <div class="card-header">
        <h3>Synthetic Data Generator</h3>
        <span class="badge" id="synth-status-badge">Idle</span>
    </div>
    <div class="card-body">
        <p class="text-muted">Generate license-free synthetic training images with realistic transforms.</p>

        <form id="synthetic-form" onsubmit="startSyntheticGeneration(event)">
            <div class="form-row">
                <div class="form-group">
                    <label for="synth-type">Type</label>
                    <select id="synth-type" name="type">
                        <option value="digits">Digits</option>
                        <option value="arrows">Arrows</option>
                        <option value="both" selected>Both</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="synth-count">Images per Class</label>
                    <input type="number" id="synth-count" name="count_per_class"
                           value="500" min="1" max="10000" step="1">
                </div>
                <div class="form-group">
                    <label for="synth-seed">Seed</label>
                    <input type="number" id="synth-seed" name="seed"
                           value="42" min="0" step="1">
                </div>
            </div>

            <div class="button-row">
                <button type="submit" class="btn btn-primary" id="synth-generate-btn">
                    Generate
                </button>
                <button type="button" class="btn btn-danger" id="synth-delete-btn"
                        onclick="deleteSyntheticData()">
                    Delete Synthetic Data
                </button>
            </div>
        </form>

        <!-- Progress bar (hidden by default) -->
        <div id="synth-progress-section" style="display: none;">
            <div class="progress-bar-container">
                <div class="progress-bar" id="synth-progress-bar" style="width: 0%"></div>
            </div>
            <span class="progress-text" id="synth-progress-text">0%</span>
            <span class="progress-message" id="synth-progress-message"></span>
        </div>
    </div>
</div>
```

**Step 2: Add JavaScript functions to training.js**

```javascript
// --- Synthetic Data Generation ---

let synthPollingInterval = null;

async function startSyntheticGeneration(event) {
    event.preventDefault();

    const config = {
        type: document.getElementById('synth-type').value,
        count_per_class: parseInt(document.getElementById('synth-count').value),
        seed: parseInt(document.getElementById('synth-seed').value),
    };

    try {
        const response = await fetch('/api/synthetic/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        });
        const data = await response.json();
        if (data.success) {
            showMessage('Synthetic generation started', 'success');
            document.getElementById('synth-progress-section').style.display = '';
            document.getElementById('synth-generate-btn').disabled = true;
            synthPollingInterval = setInterval(pollSyntheticStatus, 1000);
        } else {
            showMessage(data.message, 'error');
        }
    } catch (error) {
        console.error('Error starting synthetic generation:', error);
        showMessage('Error starting generation', 'error');
    }
}

async function pollSyntheticStatus() {
    try {
        const response = await fetch('/api/synthetic/status');
        const status = await response.json();

        const badge = document.getElementById('synth-status-badge');
        const progressBar = document.getElementById('synth-progress-bar');
        const progressText = document.getElementById('synth-progress-text');
        const progressMessage = document.getElementById('synth-progress-message');

        if (status.running) {
            badge.textContent = 'Running';
            badge.className = 'badge badge-warning';
            const pct = status.total > 0 ? Math.round((status.progress / status.total) * 100) : 0;
            progressBar.style.width = pct + '%';
            progressText.textContent = pct + '%';
            progressMessage.textContent = status.message || '';
        } else {
            badge.textContent = 'Idle';
            badge.className = 'badge';
            if (synthPollingInterval) {
                clearInterval(synthPollingInterval);
                synthPollingInterval = null;
            }
            document.getElementById('synth-generate-btn').disabled = false;
            if (status.message && status.message.startsWith('Complete')) {
                progressBar.style.width = '100%';
                progressText.textContent = '100%';
                progressMessage.textContent = status.message;
                showMessage('Synthetic data generation complete!', 'success');
                loadTrainingStats();  // Refresh the data counts
            } else if (status.message && status.message.startsWith('Error')) {
                showMessage(status.message, 'error');
            }
        }
    } catch (error) {
        console.error('Error polling synthetic status:', error);
    }
}

async function deleteSyntheticData() {
    const type = document.getElementById('synth-type').value;
    if (!confirm(`Delete all synthetic ${type} images? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/synthetic/${type}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showMessage(`Deleted ${data.deleted} synthetic images`, 'success');
            loadTrainingStats();  // Refresh counts
        } else {
            showMessage(data.detail || 'Error deleting', 'error');
        }
    } catch (error) {
        console.error('Error deleting synthetic data:', error);
        showMessage('Error deleting synthetic data', 'error');
    }
}
```

**Step 3: Verify UI manually**

Run: Open `http://localhost:8002` in browser (debug container), navigate to Training page, verify the Synthetic Data Generator card appears.

**Step 4: Commit**

```bash
git add watermeter/templates/training.html watermeter/static/training.js
git commit -m "claude: add synthetic data generator UI to training page"
```

---

## Task 8: Integration Test

**Files:**
- Create: `tests/integration/test_synthetic.py`

**Step 1: Write integration test**

```python
# tests/integration/test_synthetic.py
"""Integration tests for synthetic data generation."""

import time
import pytest

pytestmark = pytest.mark.integration


def test_synthetic_generation_e2e(api):
    """Full round-trip: generate, check status, delete."""
    # Start generation with minimal count
    response = api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 2,
        "seed": 42,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "job_id" in data

    # Poll until done (max 60s)
    for _ in range(60):
        status = api.get("/api/synthetic/status").json()
        if not status["running"]:
            break
        time.sleep(1)
    else:
        pytest.fail("Generation did not complete in 60s")

    assert "Complete" in status["message"]

    # Delete
    response = api.delete("/api/synthetic/digits")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 20  # 10 classes * 2 images


def test_synthetic_cannot_run_twice(api):
    """Cannot start two generations simultaneously."""
    # Start first
    api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 100,
        "seed": 42,
    })

    # Try second immediately
    response = api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 1,
        "seed": 42,
    })
    assert response.status_code == 409

    # Wait for first to finish
    for _ in range(120):
        status = api.get("/api/synthetic/status").json()
        if not status["running"]:
            break
        time.sleep(1)
```

**Step 2: Run integration test**

Run: `.venv/bin/python -m pytest tests/integration/test_synthetic.py -v --base-url=http://localhost:8002`
Expected: All tests PASS (requires debug container running)

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All unit tests PASS

**Step 4: Commit**

```bash
git add tests/integration/test_synthetic.py
git commit -m "claude: add integration tests for synthetic data generation"
```

---

## Task 9: Update Codebase Map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Add synthetic generator entries**

Add entries for:
- `watermeter/synthetic_generator.py` — classes `DigitRenderer`, `ArrowRenderer`, `TransformPipeline`, `SyntheticGenerator`
- `watermeter/routes/synthetic.py` — routes `POST /api/synthetic/generate`, `GET /api/synthetic/status`, `DELETE /api/synthetic/{type}`
- Test files in `tests/unit/` and `tests/integration/`

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with synthetic generator"
```

---

## Summary

| Task | Component | Tests | Files |
|------|-----------|-------|-------|
| 1 | DigitRenderer | 8 unit | synthetic_generator.py, test_synthetic_digit_renderer.py |
| 2 | ArrowRenderer | 10 unit | synthetic_generator.py, test_synthetic_arrow_renderer.py |
| 3 | TransformPipeline | 9 unit | synthetic_generator.py, test_synthetic_transforms.py |
| 4 | SyntheticGenerator | 8 unit | synthetic_generator.py, test_synthetic_generator.py |
| 5 | delete_synthetic | 3 unit | synthetic_generator.py, test_synthetic_generator.py |
| 6 | API Routes | 6 unit | routes/synthetic.py, app.py, test_synthetic_routes.py |
| 7 | Web UI | manual | training.html, training.js |
| 8 | Integration | 2 integration | test_synthetic.py |
| 9 | Codebase Map | — | codebase_map.md |

**Total: 46 tests, 9 commits**
