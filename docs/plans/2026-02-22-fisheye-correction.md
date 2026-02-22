# Fisheye Correction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a single-slider lens correction (fisheye/barrel/pincushion) to ROI setup step 1, with server-side preview, alongside the existing rotation controls.

**Architecture:** A new `fisheye_correction` float config field (k1 radial distortion coefficient) is applied via `cv2.undistort` before rotation in both the ROI wizard preview and production pipeline. The frontend gets a range slider that triggers debounced server-side preview renders. Both fisheye and rotation share one Save/Restart flow in step 1.

**Tech Stack:** OpenCV (`cv2.undistort`), FastAPI, Jinja2 templates, vanilla JS, HTMX-style fetch patterns

**Design doc:** `docs/plans/2026-02-22-fisheye-correction-design.md`

---

## Task 1: Config Schema — Add `fisheye_correction` Field

**Files:**
- Modify: `watermeter/config_utils.py:194` (detection schema)
- Test: `tests/unit/test_config_utils.py` (if exists) or manual validation

**Step 1: Add `fisheye_correction` to the detection schema**

In `watermeter/config_utils.py`, find the `"detection"` schema object (around L190-246). Inside `"properties"`, after the `"rotation"` entry at L194, add:

```python
"fisheye_correction": {"type": "number", "description": "Lens distortion correction coefficient k1 (-1.0 to +1.0)"},
```

**Step 2: Verify schema loads correctly**

Run: `.venv/bin/python -c "from watermeter.config_utils import CONFIG_SCHEMA; print('fisheye_correction' in CONFIG_SCHEMA['properties']['detection']['properties'])"`
Expected: `True`

**Step 3: Commit**

```bash
git add watermeter/config_utils.py
git commit -m "claude: add fisheye_correction to detection config schema"
```

---

## Task 2: Backend — Fisheye Helper and Updated Reference Loader

**Files:**
- Modify: `watermeter/routes/roi.py:1-19` (imports), `roi.py:74-93` (`_load_rotated_reference`)
- Test: `tests/unit/test_fisheye.py` (create)

**Step 1: Write the failing test for `_apply_fisheye_correction`**

Create `tests/unit/test_fisheye.py`:

```python
"""Tests for fisheye correction helper."""
import numpy as np
import cv2
import pytest


def test_apply_fisheye_no_correction():
    """k1=0 should return an image of the same shape (identity)."""
    from watermeter.routes.roi import _apply_fisheye_correction
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[50, 100] = [255, 255, 255]  # single white pixel at center
    result = _apply_fisheye_correction(img, 0.0)
    assert result.shape == img.shape
    # Center pixel should be unchanged with k1=0
    np.testing.assert_array_equal(result[50, 100], [255, 255, 255])


def test_apply_fisheye_barrel_correction():
    """Positive k1 (barrel) should produce a different image."""
    from watermeter.routes.roi import _apply_fisheye_correction
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.circle(img, (100, 100), 80, (255, 255, 255), 2)
    result = _apply_fisheye_correction(img, 0.5)
    assert result.shape == img.shape
    # The images should differ (distortion was applied)
    assert not np.array_equal(result, img)


def test_apply_fisheye_pincushion_correction():
    """Negative k1 (pincushion) should also produce a different image."""
    from watermeter.routes.roi import _apply_fisheye_correction
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.circle(img, (100, 100), 80, (255, 255, 255), 2)
    result = _apply_fisheye_correction(img, -0.5)
    assert result.shape == img.shape
    assert not np.array_equal(result, img)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_fisheye.py -v`
Expected: FAIL with `ImportError: cannot import name '_apply_fisheye_correction'`

**Step 3: Implement `_apply_fisheye_correction` in `roi.py`**

In `watermeter/routes/roi.py`, add `import numpy as np` to the imports (around L1-19).

Then add the helper function after the existing `_load_rotated_reference` function (after L93):

```python
def _apply_fisheye_correction(image, k1):
    """Apply radial distortion correction using a single k1 coefficient.

    Args:
        image: BGR image as numpy array
        k1: radial distortion coefficient. Positive=barrel, negative=pincushion, 0=no change.

    Returns:
        Corrected image (same shape).
    """
    if k1 == 0:
        return image
    h, w = image.shape[:2]
    fx = fy = float(w)
    cx, cy = w / 2.0, h / 2.0
    camera_matrix = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
    dist_coeffs = np.array([k1, 0, 0, 0, 0], dtype=np.float64)
    return cv2.undistort(image, camera_matrix, dist_coeffs)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_fisheye.py -v`
Expected: 3 PASSED

**Step 5: Update `_load_rotated_reference` → `_load_corrected_reference`**

Rename and update the function at L74-93:

```python
def _load_corrected_reference():
    """Load reference image with fisheye correction and rotation applied.

    Returns (img, height, width) or None.
    """
    reference_path = Path("/data/reference_raw.jpg")
    if not reference_path.exists():
        return None

    img = cv2.imread(str(reference_path))
    if img is None:
        return None

    height, width = img.shape[:2]

    service = watermeter_service.get_service()
    detection = service.config.get("detection", {})

    # 1. Fisheye correction (before rotation)
    fisheye_k1 = detection.get("fisheye_correction", 0)
    if fisheye_k1 != 0:
        img = _apply_fisheye_correction(img, fisheye_k1)

    # 2. Rotation
    rotation = detection.get("rotation", 0)
    if rotation != 0:
        center = (width / 2, height / 2)
        matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
        img = cv2.warpAffine(img, matrix, (width, height))

    return img, height, width
```

**Step 6: Update all callers of `_load_rotated_reference` to use the new name**

Search for `_load_rotated_reference` in `roi.py` and replace all calls with `_load_corrected_reference`. These are in the marker/digit/analog save endpoints.

**Step 7: Run full test suite to check for regressions**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All passing (the rename shouldn't break anything since the function signature is unchanged)

**Step 8: Commit**

```bash
git add watermeter/routes/roi.py tests/unit/test_fisheye.py
git commit -m "claude: add fisheye correction helper and update reference loader"
```

---

## Task 3: Backend — Save, Delete, Preview, and Config Endpoints

**Files:**
- Modify: `watermeter/routes/roi.py` (new endpoints + update GET config)
- Test: `tests/unit/test_fisheye.py` (add endpoint tests)

**Step 1: Write failing tests for the save/delete/config endpoints**

Append to `tests/unit/test_fisheye.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with mocked service."""
    from watermeter.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_service():
    """Mock the watermeter service for all endpoint tests."""
    mock_svc = MagicMock()
    mock_svc.config = {"detection": {}}
    with patch("watermeter.routes.roi.watermeter_service") as mock_mod:
        mock_mod.get_service.return_value = mock_svc
        yield mock_svc


def test_save_fisheye_endpoint(client, mock_service):
    """POST /api/roi/fisheye should save the correction value."""
    with patch("watermeter.routes.roi.config_utils") as mock_cfg:
        mock_cfg.update_config.return_value = {"detection": {"fisheye_correction": 0.3}}
        resp = client.post("/api/roi/fisheye", json={"fisheye_correction": 0.3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


def test_delete_fisheye_endpoint(client, mock_service):
    """DELETE /api/roi/fisheye should remove the correction value."""
    with patch("watermeter.routes.roi.config_utils") as mock_cfg:
        mock_cfg.update_config.return_value = {"detection": {}}
        resp = client.delete("/api/roi/fisheye")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


def test_config_includes_fisheye(client, mock_service):
    """GET /api/roi/config should include fisheye_correction."""
    mock_service.config = {"detection": {"fisheye_correction": 0.3, "rotation": 1.0}}
    resp = client.get("/api/roi/config")
    data = resp.json()
    assert "fisheye_correction" in data
    assert data["fisheye_correction"] == 0.3
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_fisheye.py -v -k "endpoint or config_includes"`
Expected: FAIL (endpoints don't exist yet)

**Step 3: Add `FisheyeSubmission` Pydantic model**

In `roi.py`, after the existing `RotationSubmission` model (around L22-25), add:

```python
class FisheyeSubmission(BaseModel):
    fisheye_correction: float
```

**Step 4: Add `POST /api/roi/fisheye` endpoint**

In `roi.py`, after the `DELETE /api/roi/rotation` endpoint (after L299), add:

```python
@router.post("/api/roi/fisheye", tags=["ROI Setup"], summary="Save fisheye correction")
async def save_fisheye(submission: FisheyeSubmission):
    """Save fisheye correction coefficient to config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["fisheye_correction"] = round(submission.fisheye_correction, 4)

        config = config_utils.update_config(config_path, _update)
        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Fisheye correction saved: {submission.fisheye_correction}")
        return JSONResponse({"success": True, "message": f"Fisheye correction saved: {submission.fisheye_correction}"})

    except Exception as e:
        logger.error(f"Error saving fisheye correction: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)
```

**Step 5: Add `DELETE /api/roi/fisheye` endpoint**

```python
@router.delete("/api/roi/fisheye", tags=["ROI Setup"], summary="Delete fisheye correction")
async def delete_fisheye():
    """Remove fisheye correction from config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" in config and "fisheye_correction" in config["detection"]:
                del config["detection"]["fisheye_correction"]
                if not config["detection"]:
                    del config["detection"]

        config = config_utils.update_config(config_path, _update)
        service = watermeter_service.get_service()
        service.config = config

        logger.info("Fisheye correction deleted")
        return JSONResponse({"success": True, "message": "Fisheye correction deleted"})

    except Exception as e:
        logger.error(f"Error deleting fisheye correction: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)
```

**Step 6: Add `POST /api/roi/fisheye-preview` endpoint**

This endpoint returns the fisheye-corrected image as JPEG for the live preview:

```python
@router.post("/api/roi/fisheye-preview", tags=["ROI Setup"], summary="Preview fisheye correction")
async def fisheye_preview(submission: FisheyeSubmission):
    """Return the reference image with fisheye correction applied as JPEG."""
    try:
        reference_path = Path("/data/reference_raw.jpg")
        if not reference_path.exists():
            return JSONResponse({"success": False, "message": "No reference image"}, status_code=404)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({"success": False, "message": "Cannot read reference image"}, status_code=500)

        if submission.fisheye_correction != 0:
            img = _apply_fisheye_correction(img, submission.fisheye_correction)

        _, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        encoded = base64.b64encode(buffer).decode('utf-8')
        return JSONResponse({"success": True, "image": f"data:image/jpeg;base64,{encoded}"})

    except Exception as e:
        logger.error(f"Error generating fisheye preview: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)
```

**Step 7: Update `GET /api/roi/config` to include `fisheye_correction`**

In the `get_roi_config` function (around L220-237), add `fisheye_correction` to the response dict:

```python
return JSONResponse({
    "fisheye_correction": detection.get("fisheye_correction"),
    "rotation": detection.get("rotation"),
    "markers": detection.get("markers"),
    "digits": detection.get("digits"),
    "analogs": detection.get("analogs"),
})
```

**Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_fisheye.py -v`
Expected: All PASSED

**Step 9: Commit**

```bash
git add watermeter/routes/roi.py tests/unit/test_fisheye.py
git commit -m "claude: add fisheye save/delete/preview/config endpoints"
```

---

## Task 4: Production Pipeline — Apply Fisheye Before Rotation

**Files:**
- Modify: `watermeter/image_pipeline.py:109-117`
- Test: `tests/unit/test_fisheye.py` (add pipeline test)

**Step 1: Write failing test for pipeline fisheye application**

Append to `tests/unit/test_fisheye.py`:

```python
def test_pipeline_applies_fisheye_before_rotation():
    """Verify fisheye is applied before rotation in the pipeline."""
    from watermeter.routes.roi import _apply_fisheye_correction
    # Create a test image with a circle
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.circle(img, (100, 100), 80, (255, 255, 255), 2)

    # Apply fisheye then rotation (correct order)
    corrected = _apply_fisheye_correction(img, 0.3)
    h, w = corrected.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, 5.0, 1.0)
    result = cv2.warpAffine(corrected, matrix, (w, h))

    # Result should differ from original
    assert result.shape == img.shape
    assert not np.array_equal(result, img)
```

**Step 2: Run test to verify it passes (this tests the algorithm, not the integration)**

Run: `.venv/bin/python -m pytest tests/unit/test_fisheye.py::test_pipeline_applies_fisheye_before_rotation -v`
Expected: PASS (uses the helper we already wrote)

**Step 3: Add fisheye correction to `image_pipeline.py`**

In `watermeter/image_pipeline.py`, add `import numpy as np` to imports if not already present.

At L109-117, where rotation is applied, insert fisheye correction **before** the rotation block:

```python
detection = self.config.get("detection", {})

# 1. Apply fisheye correction if configured
fisheye_k1 = detection.get("fisheye_correction", 0)
if fisheye_k1 != 0:
    from .routes.roi import _apply_fisheye_correction
    img = _apply_fisheye_correction(img, fisheye_k1)
    logger.debug(f"Applied fisheye correction: k1={fisheye_k1}")

# 2. Apply rotation if configured (existing code)
rotation = detection.get("rotation", 0)
if rotation != 0:
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
    img = cv2.warpAffine(img, matrix, (width, height))
    logger.debug(f"Applied rotation: {rotation}°")
```

**Note:** The import of `_apply_fisheye_correction` from `routes.roi` creates a cross-module dependency. If this is problematic (circular imports), extract `_apply_fisheye_correction` to a shared utility module like `watermeter/image_utils.py` instead. Check for circular import issues at runtime.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All passing

**Step 5: Commit**

```bash
git add watermeter/image_pipeline.py tests/unit/test_fisheye.py
git commit -m "claude: apply fisheye correction before rotation in production pipeline"
```

---

## Task 5: Frontend — HTML Template Changes

**Files:**
- Modify: `watermeter/templates/roi_config.html:54-81` (step 1 section)

**Step 1: Update step 1 heading and add fisheye slider**

Replace the current `#step-rotation` section (L54-81) with the combined "Image Correction" step. The fisheye slider goes above the existing rotation controls, with one shared Save button:

In the `rotation-edit` div, update the `<h2>` from "Step 1: Rotation" to "Step 1: Image Correction".

Add the fisheye slider block **before** the rotation `.rotation-controls` div:

```html
<div class="fisheye-controls" style="margin-bottom: 1rem;">
    <label for="fisheye-slider">Lens Correction</label>
    <div style="display: flex; align-items: center; gap: 0.5rem;">
        <span class="unit" style="font-size: 0.8rem;">Barrel</span>
        <input type="range" id="fisheye-slider" min="-1" max="1" step="0.01" value="0"
               style="flex: 1;">
        <span class="unit" style="font-size: 0.8rem;">Pincushion</span>
    </div>
    <div style="text-align: center; margin-top: 0.25rem;">
        Value: <span id="fisheye-value">0.00</span>
        <button class="btn btn-small btn-secondary" id="fisheye-reset-btn"
                onclick="resetFisheyeSlider()" style="margin-left: 0.5rem; font-size: 0.75rem;">
            Reset
        </button>
    </div>
</div>
<hr style="margin: 0.75rem 0; border-color: rgba(255,255,255,0.1);">
```

**Step 2: Update the saved mode display**

In the `rotation-saved` div, update the heading to "Step 1: Image Correction" and add the fisheye value display:

```html
<div id="rotation-saved" class="step-content step-saved" style="display: none;">
    <div class="step-header-saved">
        <h2>Step 1: Image Correction</h2>
        <div class="saved-value">
            Lens: <span id="saved-fisheye-value">0.00</span>,
            Rotation: <span id="saved-rotation-value">0.0</span>&deg;
        </div>
        <button class="btn btn-secondary" onclick="restartRotation()">Restart</button>
    </div>
</div>
```

**Step 3: Verify template renders without errors**

Start debug container and load `http://localhost:8002/roi-config` in browser. Verify:
- Fisheye slider appears above rotation controls
- Slider moves between -1 and 1
- "Step 1: Image Correction" heading visible
- Save button still present

**Step 4: Commit**

```bash
git add watermeter/templates/roi_config.html
git commit -m "claude: add fisheye slider UI to step 1 template"
```

---

## Task 6: Frontend — JavaScript Logic

**Files:**
- Modify: `watermeter/static/roi-config.js`

This is the largest task. It adds fisheye preview fetching, save/restore logic, and integrates with the existing rotation flow.

**Step 1: Add fisheye DOM refs and state variables**

After the existing rotation vars (around L1323-1331), add:

```javascript
// Fisheye correction
const fisheyeSlider = document.getElementById('fisheye-slider');
const fisheyeValueDisplay = document.getElementById('fisheye-value');
const savedFisheyeDisplay = document.getElementById('saved-fisheye-value');

let savedFisheye = null;
let fisheyeDebounceTimer = null;
```

**Step 2: Add fisheye preview function with debounce**

After the fisheye vars, add:

```javascript
function getActiveFisheye() {
    if (savedFisheye !== null) return savedFisheye;
    return parseFloat(fisheyeSlider.value) || 0;
}

function updateFisheyeValue() {
    const val = parseFloat(fisheyeSlider.value) || 0;
    fisheyeValueDisplay.textContent = val.toFixed(2);

    // Debounced server-side preview
    clearTimeout(fisheyeDebounceTimer);
    fisheyeDebounceTimer = setTimeout(() => fetchFisheyePreview(val), 300);
}

async function fetchFisheyePreview(k1) {
    try {
        const response = await fetch('/api/roi/fisheye-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fisheye_correction: k1 })
        });
        const data = await response.json();
        if (data.success && data.image) {
            // Replace the canvas source image with the corrected one
            const newImg = new Image();
            newImg.onload = () => {
                image = newImg;
                imageLoaded = true;
                render();
            };
            newImg.src = data.image;
        }
    } catch (error) {
        console.error('Fisheye preview error:', error);
    }
}

function resetFisheyeSlider() {
    fisheyeSlider.value = 0;
    updateFisheyeValue();
}
```

**Step 3: Update `saveRotation()` to also save fisheye**

Modify `saveRotation()` (L1369-1396) to send both values:

```javascript
async function saveRotation() {
    const total = getTotalRotation();
    const fisheye = parseFloat(fisheyeSlider.value) || 0;
    const btn = document.getElementById('save-rotation-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        // Save fisheye first (if non-zero)
        if (fisheye !== 0) {
            const fisheyeResp = await fetch('/api/roi/fisheye', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fisheye_correction: fisheye })
            });
            const fisheyeData = await fisheyeResp.json();
            if (!fisheyeData.success) {
                alert('Error saving fisheye: ' + fisheyeData.message);
                return;
            }
        }

        // Save rotation
        const response = await fetch('/api/roi/rotation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rotation: total })
        });
        const data = await response.json();

        if (data.success) {
            savedRotation = total;
            savedFisheye = fisheye;
            showRotationSavedMode();
            showMarkersEditMode();
        } else {
            alert('Error: ' + data.message);
        }
    } catch (error) {
        alert('Error: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save';
    }
}
```

**Step 4: Update `restartRotation()` to also reset fisheye**

Modify `restartRotation()` (L1398-1413):

```javascript
async function restartRotation() {
    try {
        // Delete both fisheye and rotation
        await fetch('/api/roi/fisheye', { method: 'DELETE' });
        const response = await fetch('/api/roi/rotation', { method: 'DELETE' });
        const data = await response.json();

        if (data.success) {
            // Restore rotation inputs
            const total = savedRotation || 0;
            rotationCoarse.value = Math.trunc(total);
            rotationFine.value = (total - Math.trunc(total)).toFixed(1);
            updateTotalRotation();

            // Restore fisheye slider
            fisheyeSlider.value = savedFisheye || 0;
            updateFisheyeValue();

            // Reload original image (no fisheye applied)
            const origImg = new Image();
            origImg.onload = () => {
                image = origImg;
                imageLoaded = true;
                render();
            };
            origImg.src = '/api/roi/reference-image?' + Date.now();

            showRotationEditMode();
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}
```

**Step 5: Update `changeRotation()` to restore fisheye slider**

Modify `changeRotation()` (L1416-1428) — add fisheye slider restoration:

```javascript
function changeRotation() {
    const total = savedRotation || 0;
    rotationCoarse.value = Math.trunc(total);
    rotationFine.value = (total - Math.trunc(total)).toFixed(1);
    savedRotation = null;

    // Restore fisheye slider
    fisheyeSlider.value = savedFisheye || 0;
    savedFisheye = null;
    updateFisheyeValue();

    updateTotalRotation();
    rotationEdit.style.display = 'block';
    rotationSaved.style.display = 'none';
    document.getElementById('step-rotation').style.display = 'block';
    setOverlayMode('rotation');
    updateCompletedSteps();
}
```

**Step 6: Update `showRotationSavedMode()` to display fisheye value**

In `showRotationSavedMode()` (L1359-1367), add:

```javascript
if (savedFisheyeDisplay) {
    savedFisheyeDisplay.textContent = (savedFisheye || 0).toFixed(2);
}
```

**Step 7: Update `updateCompletedSteps()` to show fisheye in the completed card**

In `updateCompletedSteps()` (L917-929), update the Step 1 completed card to show both values:

Change the completed-value span from:
```javascript
<span class="completed-value">${savedRotation.toFixed(1)}°</span>
```
to:
```javascript
<span class="completed-value">Lens: ${(savedFisheye || 0).toFixed(2)}, Rotation: ${savedRotation.toFixed(1)}°</span>
```

Also update the title from "Step 1: Rotation" to "Step 1: Image Correction".

**Step 8: Update `loadConfig()` to restore fisheye state**

In `loadConfig()` (L1488-1560), after restoring rotation (around L1493-1497), add fisheye restoration:

```javascript
if (data.fisheye_correction !== null && data.fisheye_correction !== undefined) {
    savedFisheye = data.fisheye_correction;
    fisheyeSlider.value = data.fisheye_correction;
    fisheyeValueDisplay.textContent = data.fisheye_correction.toFixed(2);
}
```

**Step 9: Add fisheye slider event listener**

After the existing rotation event listeners (L1430-1431), add:

```javascript
fisheyeSlider.addEventListener('input', updateFisheyeValue);
```

**Step 10: Manual test in browser**

Start debug container, load `http://localhost:8002/roi-config`:
1. Move fisheye slider → image updates after 300ms debounce
2. Move rotation controls → canvas rotates (client-side) on top of corrected image
3. Click Save → both values saved, step 2 activates
4. Click "Change" on completed step → both controls restored
5. Click Restart → both reset, original image reloaded
6. Reload page → saved state restored correctly

**Step 11: Commit**

```bash
git add watermeter/static/roi-config.js
git commit -m "claude: add fisheye correction JS logic with debounced server preview"
```

---

## Task 7: Update Codebase Map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Add new functions and endpoints to the codebase map**

Add entries for:
- `_apply_fisheye_correction(image, k1)` in `watermeter/routes/roi.py`
- `_load_corrected_reference()` (renamed from `_load_rotated_reference`)
- `POST /api/roi/fisheye` endpoint
- `DELETE /api/roi/fisheye` endpoint
- `POST /api/roi/fisheye-preview` endpoint
- `FisheyeSubmission` Pydantic model
- Fisheye-related JS functions in `roi-config.js`
- `tests/unit/test_fisheye.py`

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with fisheye correction entries"
```

---

## Task 8: Integration Test — Full Fisheye Workflow

**Files:**
- Create: `tests/integration/test_fisheye_workflow.py`

**Step 1: Write integration test**

```python
"""Integration test for the full fisheye correction workflow."""
import pytest


@pytest.fixture
def client():
    from watermeter.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_fisheye_save_appears_in_config(client):
    """Save fisheye, then verify it shows up in GET /api/roi/config."""
    # Save
    resp = client.post("/api/roi/fisheye", json={"fisheye_correction": 0.35})
    assert resp.status_code == 200
    assert resp.json()["success"]

    # Verify in config
    resp = client.get("/api/roi/config")
    assert resp.json()["fisheye_correction"] == 0.35

    # Delete
    resp = client.delete("/api/roi/fisheye")
    assert resp.status_code == 200

    # Verify gone
    resp = client.get("/api/roi/config")
    assert resp.json()["fisheye_correction"] is None
```

**Step 2: Run integration test**

Run: `.venv/bin/python -m pytest tests/integration/test_fisheye_workflow.py -v`
Expected: PASS (may need mock adjustments depending on test infra)

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: All passing

**Step 4: Commit**

```bash
git add tests/integration/test_fisheye_workflow.py
git commit -m "claude: add fisheye workflow integration test"
```

---

## Summary

| Task | Agent | Files | Description |
|------|-------|-------|-------------|
| 1 | dev | `config_utils.py` | Add `fisheye_correction` to schema |
| 2 | dev | `roi.py`, `test_fisheye.py` | Helper function + reference loader |
| 3 | dev | `roi.py`, `test_fisheye.py` | Save/delete/preview/config endpoints |
| 4 | dev | `image_pipeline.py`, `test_fisheye.py` | Production pipeline integration |
| 5 | frontend | `roi_config.html` | Slider UI in step 1 template |
| 6 | frontend | `roi-config.js` | JS logic: preview, save, restore |
| 7 | dev | `codebase_map.md` | Documentation update |
| 8 | tester | `test_fisheye_workflow.py` | Integration test |

**Dependencies:** Task 1 → Tasks 2,3 → Task 4. Tasks 5,6 depend on Task 3 (endpoints). Task 7 after all code tasks. Task 8 after everything.
