# ROI Config: Fix Failure When No Models Are Available

## Bug Description

On a fresh setup with no trained models, the ROI configuration page breaks because the "live preview" inference calls fail. When a user draws a digit or analog ROI box, the JS automatically calls `runDigitInference()` / `runAnalogInference()` which hit the server endpoints `/api/roi/digit-preview` and `/api/roi/analog-preview`. These endpoints call `get_inference_service().predict(...)`, which raises `RuntimeError("No digits model loaded")` when no model exists, returning a 500 error.

Additionally, when loading an existing ROI config, `loadConfig()` (roi-config.js L1507-1521) fires inference on ALL saved digit and analog ROIs immediately. On a fresh setup, every single one of these fails.

## Root Cause Analysis

### The failure chain

1. **User draws a digit/analog ROI box** on the canvas (mouseup handler, roi-config.js L185/L198), OR the page loads an existing config (roi-config.js L1508-1521)
2. **JS calls `runDigitInference(index)` / `runAnalogInference(index)`** which POSTs to `/api/roi/digit-preview` or `/api/roi/analog-preview`
3. **Server endpoint** (`routes/roi.py` L560/L734) calls `get_inference_service().predict("digits", ...)` or `.predict("arrows", ...)`
4. **`InferenceService.predict()`** (`inference.py` L322-323) finds `self._digits_classifier is None` and raises `RuntimeError("No digits model loaded")`
5. **The endpoint catches the exception** and returns `{"success": false, "message": "Error: No digits model loaded"}` with HTTP 500
6. **JS shows `!` and `Error`** in the prediction result area (roi-config.js L786-788 / L1184-1186)

### What the user sees

- Every digit/analog ROI box shows `!` with `Error` instead of a prediction
- The ROI config page still *functions* (you can draw boxes, save them) but the UX is confusing: it looks like something is broken
- No clear explanation that "no model is loaded yet" is the cause

### What should happen

The live inference preview is a **nice-to-have** feature for validating ROI placement. It is NOT required for ROI configuration to work. The core ROI config flow (draw boxes, save coordinates) works perfectly without inference. When no model is available, the preview should gracefully degrade rather than showing alarming error indicators.

## Files Involved

| File | Role |
|------|------|
| `watermeter/routes/roi.py` L554-605 | `preview_digit_inference` endpoint - calls inference |
| `watermeter/routes/roi.py` L728-779 | `preview_analog_inference` endpoint - calls inference |
| `watermeter/inference.py` L306-330 | `InferenceService.predict()` - raises RuntimeError when no model |
| `watermeter/static/roi-config.js` L749-795 | `runDigitInference()` - calls preview endpoint, shows error |
| `watermeter/static/roi-config.js` L1147-1193 | `runAnalogInference()` - calls preview endpoint, shows error |
| `watermeter/static/roi-config.js` L1507-1521 | `loadConfig()` - auto-fires inference on all existing ROIs |

## Proposed Fix

### Strategy

Handle the "no model" case at the **server level** with a specific response, and at the **client level** with a graceful "no model" indicator instead of a generic error.

### WP-1: Server-side — return structured "no model" response (junior-dev / sonnet)

In `routes/roi.py`, for both `preview_digit_inference` and `preview_analog_inference`:

1. Before calling `get_inference_service().predict(...)`, check if the required model is available using `get_inference_service().get_classifier(model_type)`
2. If the classifier is `None`, return a successful response with a flag indicating no model:
   ```json
   {"success": true, "no_model": true, "image_base64": "..."}
   ```
   This still returns the cropped ROI image (useful for visual validation even without inference) but skips the prediction.
3. Keep the existing `try/except` as a safety net for other errors.

**Files to edit:** `watermeter/routes/roi.py` (two endpoints)

### WP-2: Client-side — graceful "no model" display (frontend / sonnet)

In `roi-config.js`, update `runDigitInference()` and `runAnalogInference()`:

1. When the response contains `no_model: true`, show a muted "No model" indicator instead of `!` / `Error`
2. Still update the preview image from `image_base64` (the cropped ROI is still useful)
3. Use a neutral style (gray, not red) to indicate this is informational, not an error

**Files to edit:** `watermeter/static/roi-config.js` (two functions)

### WP-3: Minimal CSS for "no model" state (frontend / sonnet, can combine with WP-2)

Add a `.no-model` class for the result display elements that uses a muted gray style instead of the red "low confidence" style.

**Files to edit:** `watermeter/static/style.css`

## Out of Scope

- Making inference work without models (impossible by definition)
- Adding a banner/wizard that says "train a model first" (separate feature)
- Changing the ROI save flow (it already works fine without inference)

## Testing

1. Stop any running models (or use a fresh config with no model paths)
2. Open `/roi-config`, set up rotation and markers
3. Draw digit ROI boxes -> should show cropped image preview + "No model" in gray (not red error)
4. Draw analog ROI boxes -> same behavior
5. Save digits and analogs -> should work normally
6. Load a page with existing ROI config -> should show "No model" instead of errors
7. With models loaded, repeat -> should show predictions as before
