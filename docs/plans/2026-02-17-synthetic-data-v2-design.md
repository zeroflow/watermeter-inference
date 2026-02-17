# Synthetic Data v2 — Photo-based Generation + Feedback Loop

**Date:** 2026-02-17
**Status:** Approved
**Goal:** Improve synthetic training data from ~20% to 60%+ accuracy on real AIOTE data by closing the domain gap between synthetic and real images.

## Problem

Synthetic v1 achieves only ~20% accuracy on real data. Root causes:

| Gap | Digits | Arrows |
|-----|--------|--------|
| Background | White (synthetic) vs gray textured (real) | Flat gray vs cream/aged with texture |
| Subject style | Bold black font vs thin engraved/embossed | Geometric triangle vs organic teardrop |
| Color | Max contrast (black/white) vs low contrast (dark gray/gray) | Bright red (200,30,30) vs muted orange-red |
| Texture | Smooth, sterile | Smooth, sterile vs visible grain, dirt, scratches |
| Training augment | Only ColorJitter — no geometric augmentation | Same |

## Solution — Three Levers

### 1. Training Augmentation (applies to ALL training, not just synthetic)

Extend `create_transforms()` in `training_core.py`:
- `RandomAffine(degrees=5, translate=(0.05, 0.1), scale=(0.9, 1.1))`
- `RandomPerspective(distortion_scale=0.1, p=0.3)`
- `GaussianBlur(kernel_size=3, sigma=(0.1, 1.0))`
- Stronger `ColorJitter(brightness=0.4, contrast=0.4, saturation=0.3, hue=0.05)`

### 2. Arrow Photo-Compositing

Use real arrow photos as base instead of programmatic rendering:

1. **Extract pointer mask** from a real photo via red color thresholding (HSV space)
2. **Inpaint background** — `cv2.inpaint()` to fill pointer region, yielding a clean dial face with real texture, numbers, tick marks
3. **Extract pointer shape** — the masked red pointer region, stored as RGBA template
4. **Composite for each class** — rotate the pointer template to the correct angle, composite onto the real background

Reference photos (user's meter, license-free):
- `analog_1`: position 9.0
- `analog_2`: position 1.0
- `analog_3`: position 3.3
- `analog_4`: position 5.5

Use the sharpest photo as primary source. Multiple photos can provide background variation.

Result: Real texture + real colors + real dial face + pointer at all 100 positions.

### 3. Digit Style-Matched Rendering

Extract visual parameters from real digit photos (digit_1=0, digit_2=3):
- Background color range (~180-200 gray)
- Digit color range (~60-90 dark gray)
- Noise/texture level
- Contrast ratio

Update `DigitRenderer` to use measured parameters:
- Gray background with Gaussian noise texture
- Dark gray digit (not black)
- Lower contrast matching real images
- Optional subtle emboss/shadow for 3D counter look

Primary style: mechanical counter (main target). Keep some variation for flexibility.

### 4. Feedback Loop (parameter tuning)

Before generating the full dataset, optimize rendering parameters:

1. Render synthetic samples with candidate parameters
2. Train a small model (few epochs, small architecture)
3. Benchmark against real reference photos:
   - Digits: "0" and "3" from `digits/input/`
   - Arrows: 9.0, 1.0, 3.3, 5.5 from `arrows/input/`
4. Adjust parameters, repeat until accuracy on reference photos is maximized
5. Then generate the full dataset with optimized parameters

## Implementation Order

1. **Training augmentation** — quick win, helps all training
2. **Arrow photo-compositing** — biggest impact on arrow accuracy
3. **Digit style-matching** — close the digit domain gap
4. **Feedback loop** — tune parameters against real photos
5. **Full generation + benchmark** — validate against AIOTE data

## Files Changed

- `watermeter/training_core.py` — augmentation transforms
- `watermeter/synthetic_generator.py` — DigitRenderer, ArrowRenderer updates
- `watermeter/photo_master.py` (new) — photo-based master extraction for arrows
- Tests for all new functionality

## Success Criteria

- 60%+ accuracy on real AIOTE data with purely synthetic training
- Arrow photo-compositing produces visually convincing masters
- Digit rendering matches real photo style (background, contrast, texture)
