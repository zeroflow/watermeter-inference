# Digit Renderer Style Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix synthetic digit rendering from ~20% to 60%+ accuracy on real meter data by compositing rendered digits onto real photo backgrounds.

**Architecture:** Use user-edited background templates from `digits/reference/background/` as the canvas, then composite rendered digit glyphs onto them. This gives us real border, texture, color gradients, and greenish tint for free — only the digit glyph itself is synthetic. Same pattern as `ArrowCompositor` for arrows. Font experimentation and color tuning complete the match.

**Tech Stack:** PIL/Pillow, NumPy

**Reference measurements** (user-confirmed from `digits/reference/`):
- Background (digit wheel surface): RGB ~(160, 179, 159) — light gray-green
- Digit (printed number): RGB ~(29, 41, 39) — very dark greenish
- Background templates: `digits/reference/background/bg_1.jpg` (28x43), `bg2.jpg` (28x40)

---

### Task 1: Update Tests for Photo-Based Digit Compositing

Write tests for a `DigitCompositor` that uses real background templates.

**Files:**
- Modify: `tests/unit/test_synthetic_digit_renderer.py`

**Step 1: Write tests for DigitCompositor**

Replace the full contents of `tests/unit/test_synthetic_digit_renderer.py`:

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py -v`
Expected: `TestDigitCompositor` tests all FAIL (DigitCompositor not defined). `TestDigitRenderer` tests mostly pass, but `test_render_greenish_tint` and `test_render_sufficient_contrast` FAIL (current colors wrong).

**Step 3: Commit**

```bash
git add tests/unit/test_synthetic_digit_renderer.py
git commit -m "claude: add tests for photo-based DigitCompositor and updated DigitRenderer"
```

---

### Task 2: Fix DigitRenderer Colors

Update the programmatic renderer colors to match reference photos. This serves as fallback when no background templates exist.

**Files:**
- Modify: `watermeter/synthetic_generator.py:36-85`

**Step 1: Update DigitRenderer color constants**

In `watermeter/synthetic_generator.py`, replace the class constants (lines 42-45):

```python
    # Style parameters measured from real meter counter photos
    # (measured from digits/reference/0/ and digits/reference/3/)
    BG_COLOR = (160, 179, 159)            # light gray-green wheel surface
    DIGIT_COLOR = (29, 41, 39)            # very dark greenish digit
    BG_NOISE_SIGMA = 5                     # subtle texture noise
```

**Step 2: Run DigitRenderer tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py::TestDigitRenderer -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add watermeter/synthetic_generator.py
git commit -m "claude: fix digit renderer colors to match real meter greenish counter style"
```

---

### Task 3: Implement DigitCompositor

Composites rendered digit glyphs onto real photo backgrounds. Randomly selects from available background templates per generated image.

**Files:**
- Modify: `watermeter/synthetic_generator.py` (add DigitCompositor class after DigitRenderer)

**Step 1: Implement DigitCompositor**

Add after the `DigitRenderer` class in `watermeter/synthetic_generator.py`:

```python
class DigitCompositor:
    """Composites rendered digit glyphs onto real photo backgrounds.

    Loads background templates from a directory and randomly selects one
    per render call, then composites a digit glyph onto it.
    """

    WIDTH = 20
    HEIGHT = 32
    _RENDER_SCALE = 8
    DIGIT_COLOR = (29, 41, 39)  # very dark greenish, measured from reference

    def __init__(self, background_dir: str):
        self._backgrounds = []
        bg_path = Path(background_dir)
        for ext in ("*.jpg", "*.jpeg", "*.png"):
            for f in sorted(bg_path.glob(ext)):
                img = Image.open(f).convert("RGB")
                self._backgrounds.append(img)
        if not self._backgrounds:
            raise ValueError(f"No background images found in {background_dir}")
        self._font = _load_font(self.HEIGHT * self._RENDER_SCALE)
        logger.info(f"DigitCompositor loaded {len(self._backgrounds)} backgrounds")

    def render(self, digit_class: str) -> Image.Image:
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}")

        # Pick background (deterministic per class for reproducibility)
        rng = np.random.RandomState(hash(digit_class) & 0xFFFFFFFF)
        bg = self._backgrounds[rng.randint(len(self._backgrounds))].copy()
        bg = bg.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)

        # Render digit glyph as RGBA (transparent background)
        big_size = self.HEIGHT * self._RENDER_SCALE
        glyph = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        bbox = draw.textbbox((0, 0), digit_class, font=self._font)
        x = (big_size - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (big_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((x, y), digit_class, fill=(*self.DIGIT_COLOR, 255), font=self._font)

        # Crop tight to digit bounding box
        alpha = np.array(glyph)[:, :, 3]
        if alpha.any():
            rows = np.where(alpha.any(axis=1))[0]
            cols = np.where(alpha.any(axis=0))[0]
            margin = max(2, int(0.05 * (rows[-1] - rows[0])))
            top = max(0, rows[0] - margin)
            bot = min(big_size, rows[-1] + margin)
            left = max(0, cols[0] - margin)
            right = min(big_size, cols[-1] + margin)
            glyph = glyph.crop((left, top, right, bot))

        # Resize glyph to fit within background (with small padding)
        pad = 2
        glyph = glyph.resize(
            (self.WIDTH - 2 * pad, self.HEIGHT - 2 * pad),
            Image.Resampling.LANCZOS,
        )

        # Composite glyph onto background
        bg.paste(glyph, (pad, pad), glyph)  # use alpha as mask
        return bg
```

**Step 2: Run compositor tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py::TestDigitCompositor -v`
Expected: ALL PASS

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/integration -q`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add watermeter/synthetic_generator.py
git commit -m "claude: add DigitCompositor for photo-based digit rendering"
```

---

### Task 4: Integrate DigitCompositor into SyntheticGenerator

When `digits/reference/background/` exists with images, use `DigitCompositor` instead of `DigitRenderer`. Same pattern as `_get_arrow_renderer()`.

**Files:**
- Modify: `watermeter/synthetic_generator.py` (SyntheticGenerator class)
- Modify: `tests/unit/test_synthetic_generator.py` (extend)

**Step 1: Write failing test**

Add to `tests/unit/test_synthetic_generator.py`:

```python
class TestPhotoBasedDigitGeneration:
    def test_uses_compositor_when_backgrounds_available(self, tmp_path):
        """If digits/reference/background/ has images, use DigitCompositor."""
        from watermeter.synthetic_generator import SyntheticGenerator
        from PIL import Image

        # Create background directory with a fake background
        bg_dir = tmp_path / "digits" / "reference" / "background"
        bg_dir.mkdir(parents=True)
        img = Image.new("RGB", (28, 40), (155, 175, 160))
        img.save(bg_dir / "bg_test.jpg")

        gen = SyntheticGenerator(base_dir=str(tmp_path))
        stats = gen.generate(type="digits", count_per_class=2, seed=42)
        assert stats["digits"] > 0
```

**Step 2: Implement `_get_digit_renderer()`**

Add method to `SyntheticGenerator`:

```python
def _get_digit_renderer(self):
    """Return photo-based compositor if background templates exist, else programmatic."""
    bg_dir = self.base_dir / "digits" / "reference" / "background"
    if bg_dir.exists() and any(bg_dir.glob("*.jpg")) or any(bg_dir.glob("*.png")):
        logger.info(f"Using photo-based digit compositor from {bg_dir}")
        return DigitCompositor(str(bg_dir))
    logger.info("No digit background templates found, using programmatic renderer")
    return DigitRenderer()
```

Then in `generate()`, replace `renderer = DigitRenderer()` with `renderer = self._get_digit_renderer()`.

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_generator.py
git commit -m "claude: use DigitCompositor when background templates available"
```

---

### Task 5: Font Experimentation

Try different system fonts to find the best match for the angular/blocky mechanical counter digits. The real "3" has squared-off corners and more uniform stroke width than Lato-Heavy.

**Files:**
- Modify: `watermeter/synthetic_generator.py:16-17` (font paths)

**Step 1: Generate comparison grid with different fonts**

Run: `.venv/bin/python -c "
from PIL import Image, ImageDraw, ImageFont
import numpy as np

fonts_to_try = [
    ('Lato-Heavy', '/usr/share/fonts/truetype/lato/Lato-Heavy.ttf'),
    ('Lato-Black', '/usr/share/fonts/truetype/lato/Lato-Black.ttf'),
    ('Liberation-Sans-Bold', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    ('Liberation-Mono-Bold', '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf'),
    ('FreeSans-Bold', '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf'),
    ('FreeMono-Bold', '/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf'),
    ('DejaVu-Sans-Bold', '.venv/lib/python3.12/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf'),
]

# Load a real background
bg_template = Image.open('digits/reference/background/bg_1.jpg').convert('RGB')
bg_template = bg_template.resize((20, 32), Image.Resampling.LANCZOS)

for font_name, font_path in fonts_to_try:
    try:
        font = ImageFont.truetype(font_path, 256)
    except Exception:
        print(f'SKIP {font_name}: not found')
        continue

    # Render digit glyph as RGBA
    glyph = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)
    bbox = draw.textbbox((0, 0), '3', font=font)
    x = (256 - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (256 - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), '3', fill=(29, 41, 39, 255), font=font)

    # Crop tight
    alpha = np.array(glyph)[:, :, 3]
    if alpha.any():
        rows = np.where(alpha.any(axis=1))[0]
        cols = np.where(alpha.any(axis=0))[0]
        m = max(2, int(0.05 * (rows[-1] - rows[0])))
        glyph = glyph.crop((max(0,cols[0]-m), max(0,rows[0]-m), min(256,cols[-1]+m), min(256,rows[-1]+m)))

    glyph = glyph.resize((16, 28), Image.Resampling.LANCZOS)

    # Composite onto real background
    result = bg_template.copy()
    result.paste(glyph, (2, 2), glyph)

    # Scale up for viewing
    result_big = result.resize((140, 224), Image.Resampling.NEAREST)
    result_big.save(f'/tmp/font_{font_name}_3.png')
    print(f'Saved: /tmp/font_{font_name}_3.png')

# Reference for comparison
ref = Image.open('digits/reference/3/digit_2_20260217_070949.jpg')
ref_big = ref.resize((140, 224), Image.Resampling.NEAREST)
ref_big.save('/tmp/font_REFERENCE_3.png')
print('Saved: /tmp/font_REFERENCE_3.png')
print('Done — compare all /tmp/font_*_3.png')
"`

**Step 2: Visual comparison**

View each `/tmp/font_*_3.png` alongside `/tmp/font_REFERENCE_3.png`. Pick the font whose stroke weight and shape best matches the angular counter digits.

**Step 3: Update font path if a better match is found**

Update `_FONT_PATH` and `_FALLBACK_FONT_PATH` in `watermeter/synthetic_generator.py`.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py -v`
Expected: ALL PASS

**Step 5: Commit if font changed**

```bash
git add watermeter/synthetic_generator.py
git commit -m "claude: select best-matching font for mechanical counter digits"
```

---

### Task 6: Visual Validation and Parameter Tuning

Generate composited digits and compare side-by-side with reference photos. Adjust parameters iteratively.

**Step 1: Generate composited digits for all classes**

Run: `.venv/bin/python -c "
from watermeter.synthetic_generator import DigitCompositor
comp = DigitCompositor('digits/reference/background')
for i in range(10):
    img = comp.render(str(i))
    img.save(f'/tmp/composited_digit_{i}.png')
    # Scale up for viewing
    img.resize((140, 224), 0).save(f'/tmp/composited_digit_{i}_big.png')
print('Saved to /tmp/composited_digit_*.png')
"`

**Step 2: Side-by-side comparison with references**

Run: `.venv/bin/python -c "
from watermeter.synthetic_generator import DigitCompositor
from PIL import Image
import numpy as np

comp = DigitCompositor('digits/reference/background')

for digit, ref_path in [('3', 'digits/reference/3/digit_2_20260217_070949.jpg'),
                         ('0', 'digits/reference/0/digit_1_20260217_091925.jpg')]:
    synth = comp.render(digit).resize((28, 40), Image.Resampling.LANCZOS)
    ref = Image.open(ref_path).convert('RGB')

    # Side-by-side scaled up
    compare = Image.new('RGB', (60, 40), (80, 80, 80))
    compare.paste(synth, (0, 0))
    compare.paste(ref, (32, 0))
    compare = compare.resize((420, 280), Image.Resampling.NEAREST)
    compare.save(f'/tmp/compare_{digit}.png')

    s = np.array(synth)
    r = np.array(ref)
    print(f'Digit {digit}: synth mean={s.mean(axis=(0,1)).astype(int)}, ref mean={r.mean(axis=(0,1)).astype(int)}')
"`

**Step 3: Adjust `DIGIT_COLOR` or padding if needed**

**Step 4: Commit final tuning**

```bash
git add watermeter/synthetic_generator.py
git commit -m "claude: tune digit compositor parameters against reference photos"
```

---

### Task 7: Regenerate Synthetic Data

Generate fresh synthetic digit data with the new compositor.

**Step 1: Regenerate**

Run: `.venv/bin/python -c "
from watermeter.synthetic_generator import SyntheticGenerator
gen = SyntheticGenerator()
stats = gen.generate(type='digits', count_per_class=200, seed=42)
print(stats)
"`

**Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/integration -q`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add -A digits/ground_truth/
git commit -m "claude: regenerate synthetic digits with photo-based compositor"
```

---

## Execution Summary

| Task | Description | Expected Impact |
|------|-------------|-----------------|
| 1 | Tests for DigitCompositor + updated DigitRenderer | TDD foundation |
| 2 | Fix DigitRenderer colors (greenish, higher contrast) | Fallback improvement |
| 3 | Implement DigitCompositor (real bg + rendered glyph) | **Biggest accuracy gain** |
| 4 | Integrate into SyntheticGenerator | Wiring |
| 5 | Font experimentation | Shape accuracy |
| 6 | Visual validation + parameter tuning | Fine polish |
| 7 | Regenerate synthetic data | Apply to training |

**Key insight:** By compositing onto real photo backgrounds, we get border, texture, color gradients, and greenish tint for free. Only the digit glyph shape needs to match — and that's a font + color problem.
