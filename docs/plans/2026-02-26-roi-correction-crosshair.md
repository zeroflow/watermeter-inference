# ROI Config: Correction Step Crosshair & Coordinate Verification

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a static centered crosshair and 80% dotted circle overlay to Step 1 (Image Correction) for lens alignment, and verify that canvas ROI coordinates match server-side crops.

**Architecture:** Add a `correctionStep` boolean flag to the overlay system. When true, `render()` draws a centered crosshair and inscribed circle. The flag is set/cleared by the existing step transition functions. For coordinate verification, compare client-side canvas crop dimensions against the server-side `/api/roi/corrected-reference-image` dimensions.

**Tech Stack:** JavaScript (Canvas 2D API), existing `roi-config.js` overlay system

---

### Task 1: Add correction guide overlays to render()

**Files:**
- Modify: `watermeter/static/roi-config.js:61-84` (overlay state)
- Modify: `watermeter/static/roi-config.js:305-405` (render function)

**Step 1: Add `correctionStep` flag to overlay state**

In `roi-config.js`, add a `correctionStep` boolean to the overlays object at line 62:

```js
// Around line 62, after: mode: null,
const overlays = {
    mode: null,
    correctionStep: false,  // ADD THIS LINE
    mouse: { x: null, y: null, active: false },
    // ... rest unchanged
};
```

**Step 2: Draw correction guides in render()**

In the `render()` function, after the image is drawn (line 313) and before the selection-mode crosshair check (line 315), add the correction overlay block:

```js
// After line 313: ctx.drawImage(image, 0, 0);
// ADD THIS BLOCK:

// Draw correction guides (centered crosshair + 80% circle) during Step 1
if (overlays.correctionStep) {
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;

    // Static centered crosshair
    drawCrosshair(cx, cy, 'rgba(255, 255, 255, 0.5)', 1, true, false);

    // Dotted circle at 80% of canvas (diameter = 80% of shorter dimension)
    const radius = Math.min(canvas.width, canvas.height) * 0.4;
    ctx.save();
    ctx.setLineDash([8, 6]);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.restore();
}
```

**Step 3: Verify visually in browser**

Open `http://localhost:8002/roi-config` in the browser. Temporarily set `overlays.correctionStep = true` in the browser console and call `render()`. Confirm:
- Centered crosshair appears (dashed white lines, full canvas width/height)
- Dotted circle is visible, centered, fits within the image
- Both disappear when `correctionStep` is set back to `false`

**Step 4: Commit**

```bash
git add watermeter/static/roi-config.js
git commit -m "claude: add correction guide overlays (crosshair + circle) to render()"
```

---

### Task 2: Wire correctionStep flag to Step 1 transitions

**Files:**
- Modify: `watermeter/static/roi-config.js:1351-1375` (step transition functions)
- Modify: `watermeter/static/roi-config.js:1448-1470` (changeRotation function)

**Step 1: Set flag in `showRotationEditMode()`**

At line 1351, the `showRotationEditMode()` function is called when entering Step 1. Add `overlays.correctionStep = true;` before the existing `setOverlayMode(null)` call:

```js
function showRotationEditMode() {
    overlays.correctionStep = true;  // ADD
    rotationEdit.style.display = 'block';
    rotationSaved.style.display = 'none';
    document.getElementById('step-rotation').style.display = 'block';
    savedRotation = null;
    setOverlayMode(null);  // triggers render() which now draws guides
    showMarkersDisabledMode();
    updateCompletedSteps();
    fetchCorrectionPreview();
}
```

**Step 2: Clear flag in `showRotationSavedMode()`**

At line 1363, the `showRotationSavedMode()` function is called when leaving Step 1 (after Save). Add `overlays.correctionStep = false;` at the top:

```js
function showRotationSavedMode() {
    overlays.correctionStep = false;  // ADD
    rotationEdit.style.display = 'none';
    rotationSaved.style.display = 'none';
    document.getElementById('step-rotation').style.display = 'none';
    // ... rest unchanged
}
```

**Step 3: Set flag in `changeRotation()` too**

At line 1448, `changeRotation()` re-enters Step 1 from the completed state. Add the flag there:

```js
function changeRotation() {
    overlays.correctionStep = true;  // ADD
    const total = savedRotation || 0;
    // ... rest unchanged
}
```

**Step 4: Verify in browser**

1. Open `http://localhost:8002/roi-config`
2. Step 1 should show crosshair + circle on the image
3. Save Step 1 -> crosshair + circle should disappear
4. Click "Restart" to re-enter Step 1 -> crosshair + circle should reappear
5. Adjust rotation/fisheye sliders -> guides should persist through image updates (render is called after each preview load)

**Step 5: Commit**

```bash
git add watermeter/static/roi-config.js
git commit -m "claude: wire correction guides to Step 1 enter/exit transitions"
```

---

### Task 3: Verify canvas coordinates match server-side crops

**Files:**
- Read: `watermeter/static/roi-config.js:97-104` (getCanvasCoordinates)
- Read: `watermeter/static/roi-config.js:446-457` (client-side crop)
- Read: `watermeter/static/roi-config.js:680-689` (digit crop)
- Read: `watermeter/routes/roi.py:66-96` (_load_corrected_reference)
- Read: `watermeter/routes/roi.py:205-218` (corrected-reference-image endpoint)
- Read: `watermeter/routes/roi.py:687-723` (digit-preview endpoint)

**Step 1: Verify the coordinate chain is consistent**

The coordinate chain is:

1. **Frontend canvas dimensions** = `image.width` x `image.height` (set at `roi-config.js:309-310` and `1513-1514`)
2. **`image` source** = `/api/roi/corrected-reference-image` (loaded at `roi-config.js:1524`)
3. **Server endpoint** calls `_load_corrected_reference()` -> applies fisheye + rotation -> encodes as JPEG (at `roi.py:213-216`)
4. **Client-side crop**: `drawImage(image, px, py, pw, ph, 0, 0, pw, ph)` where `px = norm.x * canvas.width` (at `roi-config.js:448-457`)
5. **Server-side crop**: `img[px_y:px_y+px_h, px_x:px_x+px_w]` where `px_x = int(norm.x * width)` and `(img, height, width) = _load_corrected_reference()` (at `roi.py:690-702`)

Both client and server use the same corrected image and the same normalized-to-pixel formula. This chain is correct **if** the JPEG-encoded image dimensions match the original numpy array dimensions. JPEG encoding preserves dimensions, so this is safe.

**Potential issue:** `int()` truncates on the server (Python) while `canvas.width` is an integer on the client. The JS `image.width` from a decoded JPEG matches the original pixel count. These should be identical.

**Step 2: Write a verification test**

Run this in the browser console on `http://localhost:8002/roi-config` after defining at least one digit ROI:

```js
// Compare client canvas dimensions against server image dimensions
fetch('/api/roi/corrected-reference-image').then(r => r.blob()).then(blob => {
    const img = new Image();
    img.onload = () => {
        const serverW = img.width;
        const serverH = img.height;
        const canvasEl = document.getElementById('roi-canvas');
        const canvasW = canvasEl.width;
        const canvasH = canvasEl.height;
        console.log('Server image: ' + serverW + 'x' + serverH);
        console.log('Canvas size:  ' + canvasW + 'x' + canvasH);
        console.log('Match: ' + (serverW === canvasW && serverH === canvasH));
    };
    img.src = URL.createObjectURL(blob);
});
```

Expected: Both dimensions match exactly.

**Step 3: Visual crop comparison**

If a digit ROI is defined, compare the client-side preview against the server-side preview:

1. Look at the digit preview image shown in the ROI config UI (client-side crop from canvas image)
2. The "Run Inference" button sends normalized coords to `/api/roi/digit-preview`, which crops server-side and returns the image
3. After clicking "Run Inference", the preview is replaced with the server-rendered image (line 728)
4. If both look identical, coordinates match

**Step 4: Document findings**

If coordinates match (expected): no code changes needed.
If mismatch found: fix the scaling formula and add a regression test.

**Step 5: Commit**

```bash
git commit --allow-empty -m "claude: verify ROI canvas coordinates match server-side crops -- confirmed consistent"
```

---

## Summary of Changes

| Task | What | Files |
|------|------|-------|
| 1 | Render centered crosshair + 80% circle | `roi-config.js` render() |
| 2 | Wire flag to Step 1 enter/exit | `roi-config.js` step transitions |
| 3 | Verify coordinate consistency | Read-only verification, no changes expected |
