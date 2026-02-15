# BL-12: Fix Console Errors

## Context

Three browser console errors occur when loading the dashboard:

### Error 1: `/api/status` returns 500 Internal Server Error

**Root cause found.** The `/api/status/html` endpoint in `routes/pages.py` passes `service.current_state` (the live, shared mutable dict) directly as the Jinja2 template context. Starlette's `TemplateResponse` internally calls `context.setdefault("request", request)`, which **mutates the original `current_state` dict** by injecting a `Request` object into it. Once that happens, any subsequent call to `/api/status` (which serializes `current_state` via `JSONResponse`) fails with:

```
TypeError: Object of type Request is not JSON serializable
```

**The fix already exists in the local codebase** (`state = dict(service.current_state)` on line 61 of `routes/pages.py`), but the production container still runs the old code (`state = service.current_state`). The local fix is correct but fragile -- a shallow `dict()` copy prevents the `request` key from leaking, but doesn't protect against future regressions.

**Evidence:**
- Prod container `pages.py` line 51: `state = service.current_state` (direct reference)
- Local `pages.py` line 61: `state = dict(service.current_state)` (shallow copy -- already fixed)
- Prod traceback: `TypeError: Object of type Request is not JSON serializable` at `service.py:44`

**Recommended fix:** Keep the existing `dict()` copy, but additionally pop the `request` key from `current_state` defensively in the `/api/status` endpoint, and add a code comment explaining why the copy is necessary.

### Error 2: `dashboard.js:93` JSON parse error

**Consequence of Error 1.** At `dashboard.js` line 87, `response.json()` is called on the 500 error response, which returns an HTML error page (not JSON). The JSON parser chokes on the HTML.

**Fix:** Check `response.ok` before calling `response.json()`. If the response is not OK, skip the JSON parsing gracefully.

### Error 3: `/favicon.ico` 404 Not Found

**No favicon exists.** There is no favicon file anywhere in the project, and no `<link rel="icon">` tag in any template. Browsers request `/favicon.ico` automatically.

**Fix:** Generate a simple SVG favicon (water drop emoji) and add a `<link>` tag to all 5 page templates, or use a data URI to avoid needing a file. An inline SVG data URI favicon in the `<head>` is the simplest approach -- no file to serve, no route to add.

## Files Involved

| File | Role |
|------|------|
| `watermeter/routes/pages.py` | Already fixed locally (shallow copy). Needs hardening. |
| `watermeter/routes/service.py` | `/api/status` endpoint -- needs defensive cleanup of `request` key |
| `watermeter/static/dashboard.js` | JSON parse error on line 87 -- needs `response.ok` check |
| `watermeter/templates/dashboard.html` | Needs favicon `<link>` tag |
| `watermeter/templates/label.html` | Needs favicon `<link>` tag |
| `watermeter/templates/roi_config.html` | Needs favicon `<link>` tag |
| `watermeter/templates/config_editor.html` | Needs favicon `<link>` tag |
| `watermeter/templates/training.html` | Needs favicon `<link>` tag |

## Work Packages

### WP-1: Harden `/api/status` against non-serializable state pollution
- **Agent**: junior-dev
- **Files**: `watermeter/routes/service.py`, `watermeter/routes/pages.py`
- **Task**: Make the `/api/status` endpoint resilient to any non-serializable objects that may have been injected into `current_state`.
- **Details**:
  1. In `watermeter/routes/pages.py` line 61, add a comment above `state = dict(service.current_state)` explaining WHY the copy is critical: Starlette's `TemplateResponse` mutates the context dict by adding a `"request"` key, so passing `current_state` directly would pollute the shared state with a non-serializable `Request` object.
  2. In `watermeter/routes/service.py`, in the `get_status()` function (line 41-44), make the serialization defensive. Create a copy of `current_state` and remove any keys that are known to be non-serializable (specifically `"request"`) before passing to `JSONResponse`. This acts as a safety net in case the `pages.py` copy is ever accidentally reverted:
     ```python
     @router.get("/api/status")
     async def get_status():
         """Get current status as JSON."""
         service = watermeter_service.get_service()
         state = dict(service.current_state)
         state.pop("request", None)  # Remove if leaked from TemplateResponse context
         return JSONResponse(state)
     ```
  3. Run `pytest` to ensure nothing breaks.

### WP-2: Fix JSON parse error in dashboard.js
- **Agent**: frontend
- **Files**: `watermeter/static/dashboard.js`
- **Task**: Add error handling for non-OK responses before attempting JSON parsing.
- **Details**:
  In the `window.addEventListener('load', ...)` handler (lines 85-95), check `response.ok` before calling `response.json()`:
  ```javascript
  window.addEventListener('load', function() {
      fetch('/api/status')
          .then(response => {
              if (!response.ok) throw new Error(`Status ${response.status}`);
              return response.json();
          })
          .then(data => {
              const checkbox = document.getElementById('ha-publish');
              if (checkbox && data.ha_publish_enabled !== undefined) {
                  checkbox.checked = data.ha_publish_enabled;
              }
          })
          .catch(error => console.error('Error loading HA publish status:', error));
  });
  ```
  This prevents the confusing JSON parse error and instead logs a clean error message.

### WP-3: Add favicon to suppress 404
- **Agent**: frontend
- **Files**: `watermeter/templates/dashboard.html`, `watermeter/templates/label.html`, `watermeter/templates/roi_config.html`, `watermeter/templates/config_editor.html`, `watermeter/templates/training.html`
- **Task**: Add an inline SVG favicon to all page templates to eliminate the `/favicon.ico` 404.
- **Details**:
  Add the following line to the `<head>` section of each of the 5 page templates, right after the existing `<link rel="stylesheet" ...>` line:
  ```html
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💧</text></svg>">
  ```
  This uses an inline SVG data URI with a water drop emoji. No file serving needed, no new routes, works in all modern browsers. The 5 templates are:
  - `dashboard.html`
  - `label.html`
  - `roi_config.html`
  - `config_editor.html`
  - `training.html`

### WP-4: Rebuild and test debug container
- **Agent**: tester
- **Files**: N/A (Docker operations)
- **Task**: Rebuild the debug container and verify all three console errors are resolved.
- **Details**:
  1. Rebuild the debug container: `cd /var/ml/openvino-notebooks/watermeter && bash debug.sh`
  2. Open `http://localhost:8002` in the browser (Playwright)
  3. Check the browser console for errors -- all three should be gone:
     - No 500 error on `/api/status`
     - No JSON parse error in `dashboard.js`
     - No 404 on `/favicon.ico`
  4. Verify the favicon appears in the browser tab
  5. Verify the HA publish checkbox initializes correctly (reads from `/api/status` JSON)
