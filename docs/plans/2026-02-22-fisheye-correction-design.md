# Fisheye Correction in ROI Setup Step 1

**Date:** 2026-02-22
**Status:** Approved

## Goal

Add general-purpose lens (fisheye/barrel/pincushion) correction to ROI setup step 1, alongside the existing rotation controls. Users visually adjust a single slider until straight lines in the image appear straight.

## Requirements

- Single "Lens Correction" slider controlling radial distortion coefficient k1
- Applied before rotation (lens distortion is a property of the camera, rotation is alignment)
- Server-side preview (debounced, pixel-accurate via cv2.undistort)
- Fisheye slider above rotation controls in step 1, combined Save button
- Same config/reset patterns as rotation

## Approach: Single k1 Radial Distortion

Use OpenCV's `cv2.undistort` with one radial coefficient (k1). Slider range: [-1.0, +1.0]. This is the standard approach used by photo editing tools (Lightroom, GIMP).

Alternatives considered and rejected:
- Full calibration matrix (k1-k3, p1-p2): overkill, users won't have calibration data
- Two-term polynomial (k1+k2): second parameter hard to explain, rarely needed

## Config

```yaml
detection:
  fisheye_correction: 0.3   # float, range [-1.0, +1.0], 0 = no correction
  rotation: -2.5             # existing, unchanged
```

- Stored as float rounded to 4 decimal places
- Default 0 when absent
- Deleted on reset (same as rotation)

## Server-Side

### New helper: `_apply_fisheye_correction(image, k1)`

```python
def _apply_fisheye_correction(image, k1):
    h, w = image.shape[:2]
    fx = fy = w  # focal length = image width
    cx, cy = w / 2, h / 2
    camera_matrix = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
    dist_coeffs = np.array([k1, 0, 0, 0, 0])
    return cv2.undistort(image, camera_matrix, dist_coeffs)
```

### Updated reference loader

`_load_rotated_reference()` → `_load_corrected_reference()`:
1. Load `/data/reference_raw.jpg`
2. Apply fisheye correction if `fisheye_correction != 0`
3. Apply rotation if `rotation != 0`
4. Return corrected image

### New API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/roi/fisheye-preview` | Returns fisheye-corrected image as JPEG (accepts `{k1: float}`) |
| POST | `/api/roi/fisheye` | Saves `fisheye_correction` to config |
| DELETE | `/api/roi/fisheye` | Removes `fisheye_correction` from config |

## Frontend UI

```
┌─ Step 1: Image Correction ──────────────────┐
│                                              │
│  Lens Correction                             │
│  ◄━━━━━━━━━━●━━━━━━━━━━━► [-1.0 ... +1.0]  │
│  Barrel (-) ──── None ──── Pincushion (+)    │
│                                              │
│  ─────────────────────────────────────────── │
│                                              │
│  Rotation                                    │
│  Coarse: [____]°   Fine: [____]°             │
│  Total: 0.0°                                 │
│                                              │
│              [ Save ]                        │
└──────────────────────────────────────────────┘
```

### Behavior

- Slider `input` event debounced (300ms) → `POST /api/roi/fisheye-preview` → response replaces canvas source image
- Rotation applied client-side via `ctx.rotate()` on top of the fisheye-corrected image
- Save sends both fisheye and rotation
- Restart resets both
- Completed-steps card: "Lens: 0.3, Rotation: -2.5°"

## Production Pipeline

Insert `_apply_fisheye_correction()` into `ImagePipeline.process()` before the existing rotation step. Read `fisheye_correction` from config, apply if non-zero.

## Files to Modify

- `watermeter/routes/roi.py` — new endpoints, updated reference loader
- `watermeter/templates/roi_config.html` — fisheye slider UI in step 1
- `watermeter/static/roi-config.js` — fisheye preview logic, save/restart updates
- `watermeter/image_pipeline.py` — fisheye in production pipeline
- `watermeter/config_utils.py` — schema update for fisheye_correction
- `docs/codebase_map.md` — update with new functions/endpoints
