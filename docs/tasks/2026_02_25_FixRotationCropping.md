# Fix Rotation Cropping & Coordinate Mapping

**Date:** 2026-02-25
**Branch:** `claude/fix-rotation-cropping`
**Status:** in-progress

## Problem

Two related bugs when rotation ≠ 0°:

1. **Cropping:** `cv2.warpAffine` uses original image dimensions as output size. Rotated corners are clipped.
2. **Coordinates:** Frontend captures ROI coordinates in raw-image pixel space (canvas dimensions = raw image). With rotation applied, coordinates map to wrong content.

Previously unnoticed because old camera was calibrated to 0°.

## Root Cause

### Cropping (3 locations)
- `image_pipeline.py:137-142` — `cv2.warpAffine(img, matrix, (width, height))`
- `routes/roi.py:84-88` — same pattern in `_load_corrected_reference()`
- `roi-config.js:319-323` — canvas sized to raw image dimensions

### Fisheye
- `image_pipeline.py:20-37` — `cv2.undistort()` without `getOptimalNewCameraMatrix()`

### Coordinates
- Frontend loads RAW image, applies rotation only as canvas transform
- Coordinates captured in canvas-native space = raw image space
- Backend extracts from rotated image at these coordinates → mismatch at rotation ≠ 0°

## Solution

### Approach: Expand canvas to fit full rotated image

Compute expanded bounding box:
```
new_w = width * |cos θ| + height * |sin θ|
new_h = height * |cos θ| + width * |sin θ|
```

Adjust rotation matrix center offset:
```
matrix[0, 2] += (new_w - width) / 2
matrix[1, 2] += (new_h - height) / 2
```

Apply consistently in frontend AND backend.

## Work Packages

### WP1: Backend — Expand rotation canvas (dev)
**Files:** `watermeter/image_pipeline.py`, `watermeter/routes/roi.py`

1. Create helper function `rotate_image_full(img, rotation_degrees)` in `image_pipeline.py`:
   - Computes expanded bounding box
   - Adjusts rotation matrix for new center
   - Returns rotated image with full content (no cropping)
   - Returns (rotated_img, new_width, new_height)

2. Use this helper in:
   - `process_whole_image()` (line 137-142) — runtime pipeline
   - `_load_corrected_reference()` (roi.py line 84-88) — ROI config

3. Update `_load_corrected_reference()` to return actual (possibly expanded) dimensions

4. Update all callers of `_load_corrected_reference()` to use returned dimensions for coordinate math:
   - `save_markers()` (line 380)
   - `save_digits()` (line 510)
   - `save_analogs()` (line 683)
   - `preview_digit_inference()` (line 632)
   - `preview_analog_inference()` (line 803)

5. Update `fisheye_preview()` endpoint (roi.py line 347-368) to also apply rotation with expanded canvas, so the preview matches what the backend extracts.

6. Run existing tests to verify no regression.

**IMPORTANT:** The runtime marker alignment pipeline (`find_markers`, `align_image`) in `image_pipeline.py` also needs to work with the expanded image. The marker templates stored on disk were created with the old (cropped) rotation. After this fix, new templates will be created with expanded rotation. Need to ensure marker matching still works — the marker templates are small crops, so they should match regardless of the surrounding image expansion.

### WP2: Frontend — Expand canvas + coordinate fix (frontend)
**Files:** `watermeter/static/roi-config.js`

1. In `render()` (line 313+): When rotation is active, expand canvas to fit full rotated image:
   ```javascript
   const cos_a = Math.abs(Math.cos(rotation));
   const sin_a = Math.abs(Math.sin(rotation));
   const new_w = image.width * cos_a + image.height * sin_a;
   const new_h = image.height * cos_a + image.width * sin_a;
   canvas.width = Math.ceil(new_w);
   canvas.height = Math.ceil(new_h);
   ctx.translate(canvas.width / 2, canvas.height / 2);
   ctx.rotate(rotation);
   ctx.translate(-image.width / 2, -image.height / 2);
   ctx.drawImage(image, 0, 0);
   ```

2. Coordinate normalization in mouseup handler (line 155-160): Already normalizes by `canvas.width/canvas.height`. With expanded canvas, this naturally normalizes by expanded dimensions. **No code change needed here** — but verify it works correctly.

3. Overlay rendering (line 341+): Marker/digit/analog boxes use `m.x * canvas.width` etc. With expanded canvas, this maps correctly to expanded coordinates. **Verify overlays appear at correct positions.**

4. `updateMarkerPreview()` (line 458-494): Already applies rotation to temp canvas. Update to use expanded canvas dimensions. Same expansion math.

5. `loadImage()` (line 1559+): Default canvas size should be raw image dimensions (no rotation). Canvas expansion happens in `render()`.

6. **Important edge case:** When the user changes rotation dynamically (slider), the canvas needs to resize. Existing ROI overlays (from stored coordinates) are in the OLD normalized space. When canvas resizes, overlays should still appear correctly because they're normalized. But if the rotation changes, old overlays will be in the wrong position. This is expected — the user should redefine ROIs after changing rotation.

### WP3: Fisheye — Preserve all pixels (dev)
**Files:** `watermeter/image_pipeline.py`

1. In `apply_fisheye_correction()` (line 20-37):
   - Use `cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w, h), alpha=1)` to get new camera matrix that preserves all pixels
   - Pass new camera matrix to `cv2.undistort()`
   - This may change the output image dimensions — return actual dimensions

2. **Note:** alpha=1 means ALL source pixels are retained (some black borders may appear). This is what the user wants — no cropping.

3. If the output size changes due to fisheye, the rotation expansion must use the fisheye-corrected dimensions (since fisheye is applied BEFORE rotation).

4. Update `fisheye_preview()` endpoint to also use the new camera matrix.

### WP4: Test (tester)
1. Run existing test suite
2. Verify ROI config wizard works with rotation ≠ 0°
3. Verify markers/digits/analogs are extracted from correct positions
4. Verify no regression with rotation = 0°
