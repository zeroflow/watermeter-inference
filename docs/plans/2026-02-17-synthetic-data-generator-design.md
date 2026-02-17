# Synthetic Data Generator — Design Document

**Date:** 2026-02-17
**Status:** Approved
**Goal:** Generate license-free synthetic training data for digits and arrows that can be shipped as a base dataset and augmented with real photos.

## Motivation

The project needs a distributable, license-free base dataset. Currently all training data comes from real meter photos which may have licensing constraints. Synthetic data provides:
- A license-free baseline dataset that ships with the project
- Consistent class balance (configurable count per class)
- Reproducible generation (seeded randomness)
- Augmentation with real photos from user's meters or AI-on-the-Edge

## Architecture

**Single module** (`synthetic_generator.py`) with:
- `DigitRenderer` — renders digit master images using PIL
- `ArrowRenderer` — renders arrow dial master images using PIL
- `TransformPipeline` — applies realistic distortions using PIL + OpenCV
- `SyntheticGenerator` — orchestrates rendering + transforms, writes output

**Libraries:** PIL/Pillow (rendering), OpenCV (geometric transforms). Both already project dependencies.

## Rendering

### Digits (DigitRenderer)
- PIL `ImageDraw.text()` with monospace font (Liberation Mono or similar, license-free)
- Black digit on white background
- Canvas size: 20×32px (matches real digit dimensions)
- 10 classes: `0-9` (NAN is NOT generated — those are defective/unreadable images)

### Arrows (ArrowRenderer)
- PIL draws a round dial face:
  - White background
  - 10 black tick marks (major ticks for 0-9, optional minor ticks)
  - Red pointer/arrow, rotated per class (0.0=0°, 5.0=180°, 9.9=356.4°)
- Canvas size: ~100×100px, scaled to variable sizes (78-157px range)
- 100 classes: `0.0` through `9.9` (full rotation)

## Transform Pipeline

Each master image is transformed with a random subset (~60-80%) of these effects at random intensity:

| Transform | Digits | Arrows | Description |
|-----------|--------|--------|-------------|
| X/Y offset | ±3px | ±5px | Random translation, simulates positioning error |
| Fisheye/barrel | No | k1=0.1-0.4 | OpenCV `remap` with radial distortion |
| Perspective | Light | Strong | OpenCV `warpPerspective`, simulates camera angle |
| Brightness | ±30% | ±30% | Lighting variation |
| Contrast | ±20% | ±20% | Exposure variation |
| Gaussian noise | σ=5-15 | σ=5-15 | Sensor noise |
| JPEG artifacts | Q=60-95 | Q=60-95 | Compression artifacts |
| Blur | σ=0-1.5 | σ=0-1.5 | Slight defocus |
| Shadow | Yes | Yes | Semi-circular gradient, random direction |
| Vignetting | No | Yes | Edge darkening (typical for fisheye) |
| Color cast | Light | Light | Yellow/blue tint, white balance simulation |
| Background variation | Light | Light | Off-white, slight gray/cream tones |

## Output Format

- **Location:** Directly into existing `ground_truth/` directories
  - Digits: `digits/ground_truth/{class}/synth_{idx}.jpg`
  - Arrows: `arrows/ground_truth/{class}/synth_{idx}.jpg`
- **Prefix:** `synth_` distinguishes synthetic from real images
- **Format:** JPEG, matching quality of real images
- Existing `ImageFolder`-based data loading works without changes

## Web UI Integration

### New UI Section
- Located on the Training page (or dedicated tab)
- Configuration:
  - Type: Digits / Arrows / Both
  - Count per class (slider/input, default: 500)
  - Seed (for reproducibility, default: 42)
- "Generate" button starts the process
- Progress bar via SSE (same pattern as training)
- Preview: show sample generated images after completion

### API Endpoints
- `POST /api/generate-synthetic` — start generation
  - Body: `{ "type": "digits"|"arrows"|"both", "count_per_class": 500, "seed": 42 }`
- `GET /api/generate-synthetic/status` — SSE stream for progress
- `DELETE /api/generate-synthetic` — delete all `synth_*` images from ground_truth

### Safety
- Lock mechanism: no parallel generation + training
- Idempotent: on abort, already-generated images remain
- Auto-creates ground_truth directories if missing

## Testing Strategy

- Unit tests for renderers (correct image size, class coverage)
- Unit tests for each transform (applied correctly, within bounds)
- Integration test: generate → training pipeline accepts images without errors
- Visual smoke test: fixture images for manual inspection

## Dependencies

No new dependencies needed. Uses existing:
- `Pillow` (PIL) — already used in training pipeline
- `opencv-python` (cv2) — already used for image preprocessing
- `numpy` — already a core dependency
