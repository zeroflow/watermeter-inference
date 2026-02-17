# Synthetic Data v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve synthetic training data from ~20% to 60%+ accuracy on real AIOTE data by photo-based arrow compositing, style-matched digit rendering, stronger training augmentation, and a feedback loop for parameter tuning.

**Architecture:** Three independent improvements: (1) training augmentation in `training_core.py`, (2) photo-based arrow master generation via pointer extraction + compositing in new `photo_master.py`, (3) style-matched digit rendering with realistic colors/texture. A feedback loop script validates parameters against real reference photos before full generation.

**Tech Stack:** PIL/Pillow, OpenCV (inpainting, color thresholding), NumPy, PyTorch (torchvision transforms)

---

### Task 1: Training Augmentation

Extend `create_transforms()` with geometric augmentation. This helps ALL training (synthetic + real).

**Files:**
- Modify: `watermeter/training_core.py:53-76`
- Test: `tests/unit/test_training_augmentation.py` (create)

**Step 1: Write failing tests**

```python
# tests/unit/test_training_augmentation.py
import pytest
from torchvision import transforms
from PIL import Image


class TestCreateTransforms:
    def test_train_transform_has_random_affine(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.RandomAffine in types

    def test_train_transform_has_random_perspective(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.RandomPerspective in types

    def test_train_transform_has_gaussian_blur(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(128)
        types = [type(t) for t in train_tf.transforms]
        assert transforms.GaussianBlur in types

    def test_val_transform_has_no_augmentation(self):
        from watermeter.training_core import create_transforms
        _, val_tf = create_transforms(128)
        types = [type(t) for t in val_tf.transforms]
        assert transforms.RandomAffine not in types
        assert transforms.RandomPerspective not in types

    def test_train_transform_produces_tensor(self):
        from watermeter.training_core import create_transforms
        train_tf, _ = create_transforms(64)
        img = Image.new("RGB", (80, 80), "white")
        result = train_tf(img)
        assert result.shape == (3, 64, 64)

    def test_val_transform_produces_tensor(self):
        from watermeter.training_core import create_transforms
        _, val_tf = create_transforms(64)
        img = Image.new("RGB", (80, 80), "white")
        result = val_tf(img)
        assert result.shape == (3, 64, 64)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_training_augmentation.py -v`
Expected: 3 FAIL (RandomAffine, RandomPerspective, GaussianBlur not in transforms), 3 PASS

**Step 3: Implement**

In `watermeter/training_core.py`, replace `create_transforms()` (lines 53-76):

```python
def create_transforms(resolution: int) -> Tuple[transforms.Compose, transforms.Compose]:
    """Create train and validation transforms.

    Train transforms include geometric and color augmentation for domain
    robustness. Val transforms are deterministic (resize + normalize only).
    """
    train_transform = transforms.Compose(
        [
            transforms.Resize((resolution, resolution)),
            transforms.RandomAffine(
                degrees=5, translate=(0.05, 0.1), scale=(0.9, 1.1)
            ),
            transforms.RandomPerspective(distortion_scale=0.1, p=0.3),
            transforms.ColorJitter(
                brightness=0.4, contrast=0.4, saturation=0.3, hue=0.05
            ),
            transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 1.0)),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )

    val_transform = transforms.Compose(
        [
            transforms.Resize((resolution, resolution)),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )

    return train_transform, val_transform
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_training_augmentation.py -v`
Expected: ALL PASS

**Step 5: Run existing tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/integration -q`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add watermeter/training_core.py tests/unit/test_training_augmentation.py
git commit -m "claude: add geometric augmentation to training transforms"
```

---

### Task 2: Arrow Photo-Master — Pointer Extraction

Extract the red pointer from a real arrow photo and create a clean dial background via inpainting.

**Files:**
- Create: `watermeter/photo_master.py`
- Test: `tests/unit/test_photo_master.py` (create)

**Step 1: Write failing tests**

```python
# tests/unit/test_photo_master.py
import pytest
import numpy as np
from PIL import Image


class TestPointerExtractor:
    def _make_fake_arrow(self):
        """Create a fake arrow image: gray background + red triangle."""
        img = Image.new("RGB", (80, 80), (200, 200, 200))
        arr = np.array(img)
        # Red triangle in center-top area
        for y in range(20, 50):
            for x in range(35, 45):
                arr[y, x] = [200, 40, 40]
        return Image.fromarray(arr)

    def test_extract_pointer_mask_returns_binary(self):
        from watermeter.photo_master import extract_pointer_mask
        img = self._make_fake_arrow()
        mask = extract_pointer_mask(img)
        assert mask.shape == (80, 80)
        assert mask.dtype == np.uint8
        assert set(np.unique(mask)).issubset({0, 255})

    def test_extract_pointer_mask_finds_red(self):
        from watermeter.photo_master import extract_pointer_mask
        img = self._make_fake_arrow()
        mask = extract_pointer_mask(img)
        assert mask.sum() > 0  # found some red pixels

    def test_inpaint_background_removes_pointer(self):
        from watermeter.photo_master import extract_pointer_mask, inpaint_background
        img = self._make_fake_arrow()
        mask = extract_pointer_mask(img)
        bg = inpaint_background(img, mask)
        assert isinstance(bg, Image.Image)
        assert bg.size == (80, 80)
        # Inpainted area should not be red anymore
        arr = np.array(bg)
        red_pixels = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80)
        assert red_pixels.sum() < 10  # essentially no red left

    def test_extract_pointer_template_returns_rgba(self):
        from watermeter.photo_master import extract_pointer_mask, extract_pointer_template
        img = self._make_fake_arrow()
        mask = extract_pointer_mask(img)
        template = extract_pointer_template(img, mask)
        assert isinstance(template, Image.Image)
        assert template.mode == "RGBA"
        assert template.size == (80, 80)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_photo_master.py -v`
Expected: ALL FAIL (module not found)

**Step 3: Implement `photo_master.py`**

```python
"""Photo-based master image generation for arrows.

Extracts the red pointer from a real arrow photo, inpaints the background,
and provides compositing utilities to place a pointer at any angle.
"""

import math
from typing import Optional

import cv2
import numpy as np
from PIL import Image


def extract_pointer_mask(img: Image.Image) -> np.ndarray:
    """Extract a binary mask of the red pointer from an arrow photo.

    Uses HSV color thresholding to isolate red regions.
    Returns a uint8 mask (0 or 255).
    """
    arr = np.array(img)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

    # Red wraps around hue=0, so use two ranges
    lower_red1 = np.array([0, 50, 50])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 50, 50])
    upper_red2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    # Morphological cleanup: close small gaps, remove tiny noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    return mask


def inpaint_background(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Remove the pointer from the image by inpainting the masked region.

    Returns a clean dial background with the pointer region filled in.
    """
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    # Dilate mask slightly to cover edge artifacts
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    dilated = cv2.dilate(mask, kernel, iterations=1)

    inpainted = cv2.inpaint(arr, dilated, inpaintRadius=5, flags=cv2.INPAINT_TELEA)
    inpainted_rgb = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)

    return Image.fromarray(inpainted_rgb)


def extract_pointer_template(img: Image.Image, mask: np.ndarray) -> Image.Image:
    """Extract the pointer as an RGBA image (transparent background).

    The pointer pixels are preserved; everything else becomes transparent.
    """
    arr = np.array(img)
    rgba = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    rgba[:, :, :3] = arr
    rgba[:, :, 3] = mask  # alpha = mask

    return Image.fromarray(rgba, "RGBA")
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_photo_master.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add watermeter/photo_master.py tests/unit/test_photo_master.py
git commit -m "claude: add pointer extraction and inpainting for arrow photos"
```

---

### Task 3: Arrow Photo-Master — Compositing at Any Angle

Composite the extracted pointer onto the clean background at any rotation.

**Files:**
- Modify: `watermeter/photo_master.py`
- Test: `tests/unit/test_photo_master.py` (extend)

**Step 1: Write failing tests**

Add to `tests/unit/test_photo_master.py`:

```python
class TestArrowCompositor:
    def test_composite_returns_rgb(self):
        from watermeter.photo_master import ArrowCompositor
        bg = Image.new("RGB", (80, 80), (200, 200, 200))
        # Create a simple red pointer template (RGBA)
        template = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        t_arr = np.array(template)
        t_arr[10:40, 38:42, :] = [200, 40, 40, 255]  # vertical red bar
        template = Image.fromarray(t_arr, "RGBA")

        compositor = ArrowCompositor(bg, template, source_angle_deg=0.0)
        result = compositor.render("5.0")  # 180 degrees
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"
        assert result.size == (80, 80)

    def test_composite_all_classes(self):
        from watermeter.photo_master import ArrowCompositor
        bg = Image.new("RGB", (80, 80), (200, 200, 200))
        template = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        t_arr = np.array(template)
        t_arr[10:40, 38:42, :] = [200, 40, 40, 255]
        template = Image.fromarray(t_arr, "RGBA")

        compositor = ArrowCompositor(bg, template, source_angle_deg=0.0)
        for i in range(10):
            for j in range(10):
                cls = f"{i}.{j}"
                result = compositor.render(cls)
                assert result.size == (80, 80)

    def test_different_angles_produce_different_images(self):
        from watermeter.photo_master import ArrowCompositor
        bg = Image.new("RGB", (80, 80), (200, 200, 200))
        template = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        t_arr = np.array(template)
        t_arr[10:40, 38:42, :] = [200, 40, 40, 255]
        template = Image.fromarray(t_arr, "RGBA")

        compositor = ArrowCompositor(bg, template, source_angle_deg=0.0)
        img_00 = np.array(compositor.render("0.0"))
        img_50 = np.array(compositor.render("5.0"))
        assert not np.array_equal(img_00, img_50)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_photo_master.py::TestArrowCompositor -v`
Expected: ALL FAIL (ArrowCompositor not defined)

**Step 3: Implement `ArrowCompositor`**

Add to `watermeter/photo_master.py`:

```python
class ArrowCompositor:
    """Composites a pointer template onto a clean dial background at any angle."""

    def __init__(
        self,
        background: Image.Image,
        pointer_template: Image.Image,
        source_angle_deg: float,
    ):
        """
        Args:
            background: Clean dial background (RGB, pointer inpainted out).
            pointer_template: Pointer as RGBA (transparent background).
            source_angle_deg: The angle (in dial degrees, 0.0=up/north) of the
                pointer in the original photo. Class 0.0 = 0°, 5.0 = 180°.
        """
        self._bg = background.copy()
        self._template = pointer_template.copy()
        self._source_angle = source_angle_deg

    def render(self, arrow_class: str) -> Image.Image:
        """Render the dial at a specific arrow class position."""
        target_value = float(arrow_class)
        target_angle = (target_value / 10.0) * 360.0
        rotation = target_angle - self._source_angle

        # Rotate template around center
        # PIL rotates counter-clockwise, we want clockwise for dial
        rotated = self._template.rotate(
            -rotation, resample=Image.Resampling.BICUBIC, center=None
        )

        # Composite onto background
        result = self._bg.copy()
        result.paste(rotated, (0, 0), rotated)  # use alpha as mask
        return result
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_photo_master.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add watermeter/photo_master.py tests/unit/test_photo_master.py
git commit -m "claude: add ArrowCompositor for photo-based arrow generation"
```

---

### Task 4: Arrow Photo-Master — Build from Real Photo (End-to-End)

Convenience function that takes a real photo path + its known class, and returns an ArrowCompositor ready to render all 100 classes.

**Files:**
- Modify: `watermeter/photo_master.py`
- Test: `tests/unit/test_photo_master.py` (extend)

**Step 1: Write failing test**

```python
class TestBuildArrowCompositor:
    def test_build_from_photo_path(self, tmp_path):
        from watermeter.photo_master import build_arrow_compositor
        # Create a fake arrow photo: gray bg + red region
        img = Image.new("RGB", (80, 80), (200, 200, 200))
        arr = np.array(img)
        arr[15:45, 35:45] = [200, 40, 40]  # red pointer
        Image.fromarray(arr).save(tmp_path / "test_arrow.jpg")

        compositor = build_arrow_compositor(str(tmp_path / "test_arrow.jpg"), "9.0")
        assert hasattr(compositor, "render")
        result = compositor.render("5.0")
        assert result.size == (80, 80)
        assert result.mode == "RGB"
```

**Step 2: Implement**

Add to `watermeter/photo_master.py`:

```python
def build_arrow_compositor(
    photo_path: str, arrow_class: str, target_size: int = 80
) -> ArrowCompositor:
    """Build an ArrowCompositor from a real arrow photo.

    Args:
        photo_path: Path to a real arrow photo.
        arrow_class: The dial position in the photo (e.g. "9.0").
        target_size: Output image size (square).
    """
    img = Image.open(photo_path).convert("RGB")

    # Resize to target size (square)
    img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

    # Extract pointer
    mask = extract_pointer_mask(img)
    bg = inpaint_background(img, mask)
    template = extract_pointer_template(img, mask)

    source_angle = (float(arrow_class) / 10.0) * 360.0

    return ArrowCompositor(bg, template, source_angle)
```

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_photo_master.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add watermeter/photo_master.py tests/unit/test_photo_master.py
git commit -m "claude: add build_arrow_compositor end-to-end helper"
```

---

### Task 5: Digit Style-Matched Rendering

Update DigitRenderer to use realistic colors/texture measured from real photos.

**Files:**
- Modify: `watermeter/synthetic_generator.py:36-74`
- Test: `tests/unit/test_synthetic_digit_renderer.py` (extend)

**Step 1: Write failing tests**

Add to `tests/unit/test_synthetic_digit_renderer.py`:

```python
    def test_render_gray_background(self):
        """Background should be gray, not white."""
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("0")
        arr = np.array(img)
        # Sample corners (should be background)
        corners = [arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]]
        for c in corners:
            mean = c.mean()
            assert 140 < mean < 220, f"Background pixel {c} should be gray, not white"

    def test_render_digit_not_pure_black(self):
        """Digit should be dark gray, not pure black."""
        from watermeter.synthetic_generator import DigitRenderer
        renderer = DigitRenderer()
        img = renderer.render("8")
        arr = np.array(img)
        # Find darkest pixels (the digit)
        dark_mask = arr.mean(axis=2) < 100
        if dark_mask.sum() > 0:
            darkest = arr[dark_mask].mean()
            assert darkest > 20, "Digit should be dark gray, not pure black"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py::TestDigitRenderer::test_render_gray_background -v`
Expected: FAIL (background is white)

**Step 3: Implement style-matched rendering**

In `watermeter/synthetic_generator.py`, update `DigitRenderer.render()`:

```python
class DigitRenderer:
    WIDTH = 20
    HEIGHT = 32
    _RENDER_SCALE = 8

    # Style parameters measured from real meter photos
    BG_COLOR = (190, 188, 185)        # gray background (measured from real photos)
    DIGIT_COLOR = (60, 58, 55)        # dark gray digit (not pure black)
    BG_NOISE_SIGMA = 5                # subtle texture noise

    def __init__(self):
        self._font = _load_font(self.HEIGHT * self._RENDER_SCALE)

    def render(self, digit_class: str) -> Image.Image:
        if digit_class not in DIGIT_CLASSES:
            raise ValueError(f"Invalid digit class: {digit_class!r}. Must be one of {DIGIT_CLASSES}")

        # Render large, then crop tight, then resize to fill target
        big_size = self.HEIGHT * self._RENDER_SCALE
        big = Image.new("RGB", (big_size, big_size), self.BG_COLOR)
        draw = ImageDraw.Draw(big)
        bbox = draw.textbbox((0, 0), digit_class, font=self._font)
        x = (big_size - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (big_size - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((x, y), digit_class, fill=self.DIGIT_COLOR, font=self._font)

        # Crop to tight bounding box of the digit with small margin
        arr = np.array(big)
        bg_mean = np.array(self.BG_COLOR).mean()
        dark = arr.mean(axis=2) < (bg_mean - 20)
        if dark.any():
            rows = np.where(dark.any(axis=1))[0]
            cols = np.where(dark.any(axis=0))[0]
            margin = max(2, int(0.05 * (rows[-1] - rows[0])))
            top = max(0, rows[0] - margin)
            bot = min(big_size, rows[-1] + margin)
            left = max(0, cols[0] - margin)
            right = min(big_size, cols[-1] + margin)
            big = big.crop((left, top, right, bot))

        # Resize to fill the target canvas
        result = big.resize((self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS)

        # Add subtle background noise/texture
        arr = np.array(result).astype(np.float32)
        noise = np.random.normal(0, self.BG_NOISE_SIGMA, arr.shape)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)

        return Image.fromarray(arr)
```

**Step 4: Run ALL digit renderer tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_digit_renderer.py -v`
Expected: ALL PASS (some existing tests may need threshold adjustments — e.g. `test_render_has_dark_pixels` checks `mean < 30` which may need to be `mean < 80` for dark gray)

**Step 5: Fix any broken existing tests**

Likely adjustments:
- `test_render_white_background` → rename/adjust to `test_render_background_color` checking for gray range instead of white
- `test_render_has_dark_pixels` → adjust threshold from `< 30` to `< 100`

**Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/integration -q`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_digit_renderer.py
git commit -m "claude: style-match digit rendering to real photos (gray bg, dark gray digits)"
```

---

### Task 6: Integrate Photo-Based Arrows into SyntheticGenerator

When real arrow photos are available in `arrows/input/`, use `ArrowCompositor` instead of programmatic `ArrowRenderer`.

**Files:**
- Modify: `watermeter/synthetic_generator.py:280-338`
- Test: `tests/unit/test_synthetic_generator.py` (extend)

**Step 1: Write failing test**

Add to `tests/unit/test_synthetic_generator.py`:

```python
class TestPhotoBasedGeneration:
    def test_uses_photo_master_when_available(self, tmp_path):
        """If arrows/input/ has photos with annotations, use photo-based masters."""
        from watermeter.synthetic_generator import SyntheticGenerator

        # Create a fake arrow input photo
        input_dir = tmp_path / "arrows" / "input"
        input_dir.mkdir(parents=True)
        img = Image.new("RGB", (80, 80), (200, 200, 200))
        arr = np.array(img)
        arr[15:45, 35:45] = [200, 40, 40]  # red pointer
        Image.fromarray(arr).save(input_dir / "test_arrow.jpg")

        # Create annotation file
        import json
        annotations = {"test_arrow.jpg": "9.0"}
        (input_dir / "annotations.json").write_text(json.dumps(annotations))

        gen = SyntheticGenerator(base_dir=str(tmp_path))
        stats = gen.generate(type="arrows", count_per_class=2, seed=42)
        assert stats["arrows"] > 0
```

**Step 2: Implement**

In `SyntheticGenerator.generate()`, add photo-based arrow detection:

```python
def _get_arrow_renderer(self):
    """Return photo-based compositor if annotations exist, else programmatic."""
    input_dir = self.base_dir / "arrows" / "input"
    annotations_file = input_dir / "annotations.json"

    if annotations_file.exists():
        import json
        from .photo_master import build_arrow_compositor

        annotations = json.loads(annotations_file.read_text())
        # Use the first annotated photo
        for filename, arrow_class in annotations.items():
            photo_path = input_dir / filename
            if photo_path.exists():
                logger.info(f"Using photo-based arrow master: {filename} ({arrow_class})")
                return build_arrow_compositor(str(photo_path), arrow_class)

    logger.info("No arrow photo annotations found, using programmatic renderer")
    return ArrowRenderer()
```

Then in `generate()`, replace `renderer = ArrowRenderer()` with `renderer = self._get_arrow_renderer()`.

The `ArrowCompositor` already has a `.render(cls)` method matching `ArrowRenderer.render(cls)`, so the rest of the pipeline works unchanged.

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_synthetic_generator.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add watermeter/synthetic_generator.py tests/unit/test_synthetic_generator.py
git commit -m "claude: use photo-based arrow masters when annotations available"
```

---

### Task 7: Arrow Annotation UI

Add a simple way to annotate arrow input photos with their dial position, stored as `annotations.json`.

**Files:**
- Modify: `watermeter/templates/training.html`
- Modify: `watermeter/routes/synthetic.py`
- Test: `tests/unit/test_synthetic_routes.py` (extend)

**Step 1: Write failing test**

```python
    def test_save_arrow_annotation(self):
        response = self.client.post(
            "/api/synthetic/annotate",
            json={"filename": "analog_1.jpg", "arrow_class": "9.0"},
        )
        assert response.status_code == 200

    def test_get_arrow_annotations(self):
        response = self.client.get("/api/synthetic/annotations")
        assert response.status_code == 200
        data = response.json()
        assert "annotations" in data
```

**Step 2: Implement API endpoints**

In `watermeter/routes/synthetic.py`:

```python
@router.post("/api/synthetic/annotate")
async def save_annotation(data: dict):
    """Save arrow photo annotation (filename -> class)."""
    annotations_file = Path("/training/arrows/input/annotations.json")
    annotations = {}
    if annotations_file.exists():
        annotations = json.loads(annotations_file.read_text())
    annotations[data["filename"]] = data["arrow_class"]
    annotations_file.write_text(json.dumps(annotations, indent=2))
    return JSONResponse({"success": True})

@router.get("/api/synthetic/annotations")
async def get_annotations():
    """Get all arrow photo annotations."""
    annotations_file = Path("/training/arrows/input/annotations.json")
    annotations = {}
    if annotations_file.exists():
        annotations = json.loads(annotations_file.read_text())
    return JSONResponse({"annotations": annotations})
```

**Step 3: Add simple annotation UI in the synthetic generator section**

In `training.html`, inside the Getting Started synthetic section, add a list of arrow input photos with a text input for the dial position.

**Step 4: Run tests and commit**

```bash
git add watermeter/routes/synthetic.py watermeter/templates/training.html tests/unit/test_synthetic_routes.py
git commit -m "claude: add arrow photo annotation API and UI"
```

---

### Task 8: Feedback Loop Script

Script to iteratively tune rendering parameters against real reference photos.

**Files:**
- Create: `scripts/tune_synthetic.py`

**Step 1: Implement the feedback script**

```python
#!/usr/bin/env python3
"""Tune synthetic data parameters by training mini-models and benchmarking
against real reference photos.

Usage:
    .venv/bin/python scripts/tune_synthetic.py

The script:
1. Generates a small synthetic dataset with current renderer settings
2. Trains a tiny model (5 epochs, mobilenet_v3_small)
3. Evaluates against real photos in input/ directories
4. Reports accuracy per class
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from watermeter.synthetic_generator import SyntheticGenerator, DigitRenderer, ArrowRenderer
from watermeter.training_core import create_transforms

import torch
from torchvision import transforms
from PIL import Image
import numpy as np


def evaluate_digits(model, transform, input_dir: Path, classes: list) -> dict:
    """Evaluate model against real digit input photos."""
    results = {}
    for img_path in sorted(input_dir.glob("digit_*.jpg")):
        img = Image.open(img_path).convert("RGB")
        tensor = transform(img).unsqueeze(0)
        with torch.no_grad():
            output = model(tensor)
            _, predicted = torch.max(output, 1)
            pred_class = classes[predicted.item()]
        results[img_path.name] = pred_class
    return results


def main():
    print("=== Synthetic Data Parameter Tuning ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Generate small synthetic dataset
        print("1. Generating synthetic data (50 per class)...")
        gen = SyntheticGenerator(base_dir=tmpdir)
        stats = gen.generate(type="both", count_per_class=50, seed=42)
        print(f"   Generated: {stats}")

        # Step 2: Train mini model
        print("\n2. Training mini model (5 epochs)...")
        # ... (training logic using existing training_core utilities)

        # Step 3: Evaluate against real photos
        print("\n3. Evaluating against real reference photos...")
        # ... (evaluation logic)

    print("\nDone. Adjust parameters in synthetic_generator.py and re-run.")


if __name__ == "__main__":
    main()
```

This script is a development tool, not production code. It can be refined iteratively during the tuning process.

**Step 2: Commit**

```bash
git add scripts/tune_synthetic.py
git commit -m "claude: add synthetic parameter tuning script"
```

---

### Task 9: Visual Verification & Benchmark

Generate samples, visually compare, then run full benchmark.

**Step 1: Generate sample images with new renderers**

```bash
.venv/bin/python -c "
from watermeter.synthetic_generator import DigitRenderer, ArrowRenderer
d = DigitRenderer()
for i in range(10):
    d.render(str(i)).save(f'/tmp/v2_digit_{i}.jpg')
print('Digits saved')
"
```

**Step 2: If arrow annotations exist, generate photo-based samples**

```bash
.venv/bin/python -c "
from watermeter.photo_master import build_arrow_compositor
comp = build_arrow_compositor('arrows/input/analog_1_20260217_091927.jpg', '9.0')
for i in range(10):
    for j in [0, 5]:
        cls = f'{i}.{j}'
        comp.render(cls).save(f'/tmp/v2_arrow_{cls}.jpg')
print('Arrows saved')
"
```

**Step 3: Visual check and parameter adjustment**

Review `/tmp/v2_digit_*.jpg` and `/tmp/v2_arrow_*.jpg` against real photos. Adjust style parameters if needed.

**Step 4: Full generation + training + benchmark**

Deploy to debug container, generate full dataset, train, benchmark against AIOTE data.

**Step 5: Commit final parameter tuning**

```bash
git commit -m "claude: tune synthetic v2 parameters after benchmark"
```

---

## Execution Summary

| Task | Description | Estimated Complexity |
|------|-------------|---------------------|
| 1 | Training augmentation | Small (one function change) |
| 2 | Pointer extraction + inpainting | Medium (new module) |
| 3 | Arrow compositing | Small (one class) |
| 4 | End-to-end builder | Small (one function) |
| 5 | Digit style-matching | Medium (rendering overhaul) |
| 6 | Integrate into SyntheticGenerator | Small (fallback logic) |
| 7 | Annotation UI | Medium (API + frontend) |
| 8 | Feedback loop script | Small (dev tool) |
| 9 | Visual verification & benchmark | Manual / iterative |
