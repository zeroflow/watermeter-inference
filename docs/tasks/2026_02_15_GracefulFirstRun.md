# Graceful First-Run Experience

**Date:** 2026-02-15
**Status:** planned

---

## Goal

Make the watermeter project clone-and-run for newcomers: Docker build succeeds without pre-existing model files, the app starts gracefully without models, and users are guided through data acquisition and training.

## Background

Three blocking issues prevent newcomers from using this project after a fresh `git clone`:

1. **Docker build fails** -- `Dockerfile` L45-46 runs `COPY digits/selected/` and `COPY arrows/selected/`, but these directories do not exist after a fresh clone. The `debug.sh` script creates them from `digits/ov_model/` and `arrows/ov_model/`, but those also require pre-trained models.

2. **Git submodules unreachable** -- `.gitmodules` references `ssh://gitea@192.168.4.38:2222/...` (a private Gitea server). External users cannot clone the submodules, and `git clone --recursive` will fail.

3. **No graceful degradation** -- Without model files at the config-specified paths, the app crashes during startup:
   - `inference.py` L399-407: Module-level code reads `config.yaml`, creates `InferenceService`, and calls `initialize()` which calls `Classifier()` / `Regressor()` constructors (L176, L189, L203). These call `ov.Core().read_model(model_path)` which throws `RuntimeError` if the file doesn't exist.
   - This crash happens at **import time** of `inference.py`, triggered by `watermeter_service.py` L21: `from .inference import get_inference_service`.
   - So the entire app fails to start.

## Upstream Data Sources (Research)

Training data lives in two separate GitHub repos (NOT in the main AI-on-the-edge repo):

| Type | Repository | Branch | Data Directory | ZIP Size | Images |
|------|-----------|--------|----------------|----------|--------|
| Digits | `jomjol/neural-network-digital-counter-readout` | `master` | `03_data_resize_all-use_for_training/` | 9.6 MB | ~1162 (32x20px JPG) |
| Analog | `jomjol/neural-network-analog-needle-readout` | **`main`** | `data_raw_all/` | 127 MB | ~2493 (32x32px JPG) |

**Critical: Flat file structure!** Both repos use filename prefixes (`3_xyz.jpg`, `NaN_abc.jpg`, `5.7_abc.jpg`), NOT subdirectories per class. Our fetch script and training pipeline expect subdirectories.

**Download method:** GitHub Archive ZIP is the best option — only needs `curl` + `unzip`, downloads in ~15 seconds total. Already used by `fetch_upstream_data.sh`, but the script has 3 bugs (see WP-5a).

**License:** No explicit license on training data ([issue #4041](https://github.com/jomjol/AI-on-the-edge-device/issues/4041)). Users download at own risk for personal use only.

---

## Three Application States

### State 1 -- Fresh Install (no models, no data)

| Component | Current Behavior | Target Behavior |
|-----------|-----------------|-----------------|
| Docker build | **FAILS** at `COPY digits/selected/` | Succeeds -- no model COPY needed |
| App startup | **CRASHES** -- OpenVINO can't read model | Starts successfully, logs "no models loaded" |
| Dashboard | N/A (app is dead) | Shows "No models configured" message, "Read Now" button disabled |
| `/api/trigger` | N/A | Returns 503 "No inference models loaded" |
| `/api/status` | N/A | Returns `status: "no_models"`, `total_value: null` |
| MQTT publish | N/A | Silent -- never publishes without a valid reading |
| Training page | N/A | Shows instructions on how to get training data (3 options) |
| Training/benchmark | N/A | Training works (if data present), benchmark works on trained models |

### State 2 -- Data Present, No Model

| Component | Behavior |
|-----------|----------|
| Dashboard | Same as State 1 ("No models") |
| Training page | Shows dataset stats, "Train" button active |
| After training | User activates model via training page, app transitions to State 3 |

### State 3 -- Model Active (current behavior)

Normal operation. No changes needed. This is the current behavior when models exist.

---

## Crash Path Analysis

### Critical: Module-level initialization in `inference.py`

```
inference.py L399-407:
    with open("config.yaml", "r") as f:      # L400 - reads config at import time
        config = yaml.safe_load(f)            # L401
    app = FastAPI()                            # L403 - creates unused FastAPI app (!)
    _inference_service = InferenceService()    # L406
    _inference_service.initialize(config)      # L407 - CRASHES if model files missing
```

This code runs when `watermeter_service.py` L21 does:
```python
from .inference import get_inference_service
```

`InferenceService.initialize()` (L164) calls:
- `validate_model_config()` (L170) -- reads model path from config, won't crash if file doesn't exist (only validates filename pattern)
- `Classifier(model_path, ...)` (L176) -- constructor calls `ov.Core().read_model(model_path)` at L22 which throws `RuntimeError` if file doesn't exist
- Same for arrows: L185-209

### Secondary: `watermeter_service.py` resilience

`run_inference()` (L397) already has per-image try/except (L449-458) that catches inference errors and returns `class: "ERROR"`. This is good -- but it only helps if the InferenceService was successfully initialized (classifiers are non-None).

If `_digits_classifier` or `_arrows_classifier` is `None`, calling `.predict()` at L294/L296 will raise `AttributeError`.

### MQTT is already safe

`publish_to_mqtt()` (L1615) checks:
- `self.ha_publish_enabled` (L1626) -- can be False
- `self.mqtt_client.is_connected()` (L1630) -- won't publish if disconnected

No reading will be published if `process_reading()` fails or never runs.

### Dashboard template is already safe

`status_fragment.html` L7 checks `{% if status == 'idle' or not total_value %}` and shows "No data available" with "Click Read Now to start". This will work in State 1 -- but we should add a better message for "no models" state.

### ModelManager is already safe

`list_models()` (L40-74) checks `if not models_dir.exists(): return []`. This handles the case where no models directory exists.

---

## Work Packages

### WP-1: Remove submodules and model COPY from build

**Agent:** junior-dev (sonnet)
**Dependencies:** None

#### Files to modify:

1. **`Dockerfile`** -- Remove L44-46:
   ```dockerfile
   # DELETE these lines:
   # Copy selected models to staging area (entrypoint copies to /app/models/ on first run)
   COPY digits/selected/ /app/digits/selected/
   COPY arrows/selected/ /app/arrows/selected/
   ```
   Also remove the `/app/digits/selected` and `/app/arrows/selected` mkdir from L30-37 if present (they are not -- the mkdir only creates `/app/models`).

2. **`.gitmodules`** -- Delete this file entirely. It references:
   - `arrows/ground_truth` -> `ssh://gitea@192.168.4.38:2222/zeroflow/watermeter-arrows.git`
   - `digits/ground_truth` -> `ssh://gitea@192.168.4.38:2222/zeroflow/watermeter-digits.git`

3. **Remove submodule git metadata:**
   ```bash
   git rm --cached arrows/ground_truth digits/ground_truth
   # The directories themselves should remain as normal dirs (they contain .gitkeep or data)
   ```

4. **`docker-entrypoint.sh`** -- Update L20-78. Currently the entrypoint copies models from `/app/digits/selected/` and `/app/arrows/selected/` to `/app/models/`. Since those staging directories will no longer exist in the image, the entrypoint should:
   - Keep the existing logic structure (check if models dir is empty, copy from staging if available)
   - Add a clear log message when no default models exist: `echo "INFO: No default models shipped. Train or import a model via the Training page."`
   - Change the existing `WARN` messages at L44 and L74 to `INFO` level (they are not errors in the new flow)
   - Must NOT fail (`set -e` is at L2, so we must ensure no command returns non-zero)

5. **`debug.sh`** -- L27-58 creates `digits/selected/` and `arrows/selected/` by copying from `digits/ov_model/` and `arrows/ov_model/`. This will fail if no local models exist. Update to:
   - Check if model files exist in `digits/ov_model/` and `arrows/ov_model/`
   - If models exist: copy to `selected/` (current behavior)
   - If models don't exist: create empty `digits/selected/` and `arrows/selected/` directories, log info message
   - This allows `docker build` to succeed even without models (the COPY lines will be gone after this WP, but `debug.sh` should still work standalone)
   - Actually, since `COPY digits/selected/` is being removed from Dockerfile, `debug.sh` no longer needs to create `selected/` directories at all. Remove L27-60 entirely and the `MISSING` validation block L42-53.

#### Acceptance criteria:
- `docker build .` succeeds after a fresh clone (no model files present)
- `.gitmodules` is deleted
- Submodule references are removed from git index
- `docker-entrypoint.sh` does not fail when no default models exist in the image
- `debug.sh` builds and runs without requiring pre-existing model files

---

### WP-2: Make InferenceService tolerate missing models

**Agent:** senior-dev (opus)
**Dependencies:** None (can run in parallel with WP-1)

#### Files to modify:

1. **`watermeter/inference.py`**

   **A) Remove module-level initialization (L399-412):**

   Lines 399-412 currently do:
   ```python
   with open("config.yaml", "r") as f:
       config = yaml.safe_load(f)
   app = FastAPI()
   _inference_service = InferenceService()
   _inference_service.initialize(config)
   ```

   This code serves TWO purposes:
   - Creates the singleton `_inference_service` used by `get_inference_service()`
   - Initializes a separate `app = FastAPI()` with Label Studio ML Backend routes (L445-524)

   Change to:
   - Create the singleton lazily in `get_inference_service()` (it already exists at L410 but just returns the eagerly-created instance)
   - Do NOT call `initialize()` at import time
   - The Label Studio routes (L403, L445-524) are dead code (never mounted in `app.py`). Leave them for now but do not let them block startup.

   New `get_inference_service()`:
   ```python
   _inference_service = None

   def get_inference_service() -> InferenceService:
       global _inference_service
       if _inference_service is None:
           _inference_service = InferenceService()
       return _inference_service
   ```

   **B) Make `InferenceService.initialize()` (L164) tolerate missing model files:**

   Wrap the model loading in try/except. If a model file doesn't exist:
   - Log a warning: `"Digits model not found at {path} -- inference disabled for digits"`
   - Leave `self._digits_classifier = None` (already the default from `__init__`)
   - Same for arrows
   - Do NOT raise -- let initialization complete partially

   **C) Make `InferenceService.predict()` (L281) handle None classifiers:**

   At L293-298, if `self._digits_classifier is None` or `self._arrows_classifier is None`:
   ```python
   def predict(self, model_type: str, image_path: str) -> dict:
       with self._lock:
           if model_type == "digits":
               if self._digits_classifier is None:
                   raise RuntimeError("No digits model loaded")
               return self._digits_classifier.predict(image_path)
           elif model_type == "arrows":
               if self._arrows_classifier is None:
                   raise RuntimeError("No arrows model loaded")
               return self._arrows_classifier.predict(image_path)
   ```

   Same for `predict_detailed()` at L300-308.

   **D) Add a `models_loaded` property:**
   ```python
   @property
   def models_loaded(self) -> bool:
       """Check if both model types are loaded and ready."""
       return self._digits_classifier is not None and self._arrows_classifier is not None

   @property
   def loaded_model_types(self) -> list:
       """Return list of model types that are loaded."""
       types = []
       if self._digits_classifier is not None:
           types.append("digits")
       if self._arrows_classifier is not None:
           types.append("arrows")
       return types
   ```

   **E) Make `reload_models()` (L214) also tolerate missing files** -- same pattern as initialize: try/except, leave classifier as None if file missing, log warning.

2. **`watermeter/app.py`** -- Lifespan (L42-85)

   The lifespan creates the service at L47: `service = get_service()`. This triggers `WatermeterService.__init__()` which does NOT directly initialize inference -- it only reads config.

   However, inference initialization happens when `inference.py` is imported (due to module-level code). After WP-2A removes that, initialization needs to happen explicitly.

   Add explicit inference initialization in lifespan:
   ```python
   # After service = get_service()
   from .inference import get_inference_service
   inference_svc = get_inference_service()
   try:
       inference_svc.initialize(service.config)
   except Exception as e:
       logger.warning(f"Inference initialization failed (no models?): {e}")
       logger.info("App starting without inference -- train or import models via /training")
   ```

   Also: at L76 (`_create_background_task(service.process_reading())`), only trigger initial reading if models are loaded:
   ```python
   if inference_svc.models_loaded:
       logger.info("Triggering initial reading...")
       _create_background_task(service.process_reading())
   else:
       logger.info("Skipping initial reading (no models loaded)")
   ```

#### Acceptance criteria:
- App starts successfully when model files referenced in config.yaml don't exist
- `get_inference_service()` returns an InferenceService even without models
- `inference_svc.models_loaded` returns False when no models exist
- `predict()` raises `RuntimeError("No digits/arrows model loaded")` when called without models
- `reload_models()` works to load models after training (existing behavior preserved)
- No changes to Classifier/Regressor classes themselves

---

### WP-3: Graceful service behavior without models

**Agent:** senior-dev (opus)
**Dependencies:** WP-2

#### Files to modify:

1. **`watermeter/watermeter_service.py`**

   **A) `run_inference()` (L397):** Already handles per-image exceptions at L449-458 and returns `class: "ERROR"`. However, if `get_inference_service()._digits_classifier is None`, the error message from WP-2C will be "No digits model loaded" which is clear enough. No changes needed here -- the existing error handling is sufficient.

   **B) `process_reading()` (L1371):** Add an early guard at the top (after L1391, before the try block):
   ```python
   from .inference import get_inference_service
   if not get_inference_service().models_loaded:
       self.current_state["status"] = "no_models"
       self.current_state["warnings"] = ["No inference models loaded. Train or import models via the Training page."]
       return self.current_state
   ```

   **C) Add `"no_models"` to the initial state dict (L47-62):** Not strictly needed since the key gets set dynamically, but for clarity add it to the docstring or initial comment.

2. **`watermeter/routes/service.py`**

   **A) `trigger_reading()` (L68):** Add guard before triggering:
   ```python
   from ..inference import get_inference_service
   if not get_inference_service().models_loaded:
       return JSONResponse(
           {"message": "No inference models loaded. Train or import models first."},
           status_code=503
       )
   ```

   **B) `get_status()` (L54):** No changes needed -- it returns `current_state` as-is, which will include the `"no_models"` status set by process_reading or the default `"idle"`.

#### Acceptance criteria:
- `/api/trigger` returns 503 with helpful message when no models loaded
- `/api/status` returns `status: "no_models"` (or `"idle"` with no value) when no models loaded
- `process_reading()` exits early without crashing when no models loaded
- MQTT never publishes when no models are loaded (already guaranteed by existing checks)
- App remains responsive in "no models" state -- all pages load, training works

---

### WP-4: Dashboard empty state UI

**Agent:** frontend (sonnet)
**Dependencies:** WP-2, WP-3

#### Files to modify:

1. **`watermeter/templates/status_fragment.html`** (112 lines)

   Add a new block at the top (before L1, or right after the MQTT warning at L1-5) to handle the `no_models` status:

   ```html
   {% if status == 'no_models' %}
   <div class="no-data">
       <h3>No Models Loaded</h3>
       <p>The application is running, but no inference models are configured.</p>
       <p>Visit the <a href="/training">Training page</a> to train or import a model.</p>
       {% if warnings %}
       <ul>
           {% for warning in warnings %}
           <li>{{ warning }}</li>
           {% endfor %}
       </ul>
       {% endif %}
   </div>
   {% elif status == 'idle' or not total_value %}
   ```

   Note: The existing `{% if status == 'idle' or not total_value %}` at L7 becomes `{% elif ... %}`.

2. **`watermeter/templates/dashboard.html`** (59 lines)

   The "Read Now" button at L17-23: consider disabling it when no models are loaded. However, since the button uses HTMX (`hx-post="/api/trigger"`), and the API will return 503, the error will be shown in `#status-message`. No template change strictly needed -- the API-level guard is sufficient. But for better UX, we could pass a `models_loaded` flag to the template context and conditionally disable the button. This requires a change to:

3. **`watermeter/routes/pages.py`** -- `dashboard()` (L24):

   Pass inference state to template context:
   ```python
   from ..inference import get_inference_service

   async def dashboard(request: Request):
       inference_svc = get_inference_service()
       return templates.TemplateResponse(request, "dashboard.html", context={
           "nav_active": "dashboard",
           "models_loaded": inference_svc.models_loaded,
       })
   ```

   Then in `dashboard.html`, wrap the "Read Now" button:
   ```html
   {% if models_loaded %}
   <button hx-post="/api/trigger" ...>Read Now</button>
   {% else %}
   <button class="btn btn-primary" disabled title="No models loaded">Read Now</button>
   {% endif %}
   ```

4. **`watermeter/static/style.css`** -- Add styling for the no-models state if needed. The existing `.no-data` class (L487-502) should work. May want to add a link style inside `.no-data`.

#### Acceptance criteria:
- Dashboard shows "No Models Loaded" message with link to training page when no models exist
- "Read Now" button is disabled when no models loaded
- Status fragment handles `status == 'no_models'` without Jinja errors
- When models are loaded (State 3), dashboard behaves exactly as before

---

### WP-5: Training page -- data acquisition guide + ZIP upload

**Agent:** frontend (sonnet) for UI; senior-dev (opus) for upload endpoint
**Dependencies:** None (can run in parallel)

#### Design Decision: Links + Upload (no auto-download)

The upstream training data repos (jomjol) have **no license**. Instead of automating the download (which could be seen as facilitating use of unlicensed data), we:

1. **Link** to the source repos — user downloads ZIP manually via browser (conscious decision)
2. **Upload** — user uploads the ZIP through the web UI
3. **Sort** — backend unpacks and sorts images into class directories

This approach has **zero legal risk** for the project (no download facilitation), and the upload mechanism is **generic** — works for any data source, not just jomjol's repos.

#### Files to modify:

1. **`watermeter/templates/training.html`** (1285 lines)

   Add a "Getting Started" section (collapsible) near the top of the training content area, before the dataset stats section. First child inside `<div class="training-content">`.

   ```html
   <div class="section">
       <h2>Getting Started</h2>
       <details open>
           <summary>How to get training data</summary>
           <div class="getting-started-content">
               <h4>Option A: Collect your own data (recommended)</h4>
               <p>Connect your AI-on-the-edge (AIOTE) device, configure ROIs,
                  and use the labeling interface to collect and label images.
                  This produces the highest quality models for your specific meter.</p>
               <ol>
                   <li>Configure your meter in <a href="/roi-config">ROI Config</a></li>
                   <li>Let the system collect low-confidence images automatically</li>
                   <li>Label images in the <a href="/label">Labeling</a> interface</li>
                   <li>Train a model below</li>
               </ol>

               <h4>Option B: Upload community reference data</h4>
               <p>Download a ZIP of training images from one of these community repos,
                  then upload it here:</p>
               <ul class="repo-links">
                   <li><strong>Digits (0-9):</strong>
                       <a href="https://github.com/jomjol/neural-network-digital-counter-readout/archive/refs/heads/master.zip"
                          target="_blank" rel="noopener">Download ZIP</a>
                       (9.6 MB, ~1162 images)
                       — <a href="https://github.com/jomjol/neural-network-digital-counter-readout"
                            target="_blank" rel="noopener">Repository</a>
                   </li>
                   <li><strong>Analog dials (0.0-9.9):</strong>
                       <a href="https://github.com/jomjol/neural-network-analog-needle-readout/archive/refs/heads/main.zip"
                          target="_blank" rel="noopener">Download ZIP</a>
                       (127 MB, ~2493 images)
                       — <a href="https://github.com/jomjol/neural-network-analog-needle-readout"
                            target="_blank" rel="noopener">Repository</a>
                   </li>
               </ul>

               <div class="upload-area">
                   <label for="data-type-select">Upload as:</label>
                   <select id="data-type-select">
                       <option value="digits">Digits</option>
                       <option value="arrows">Analog / Arrows</option>
                   </select>
                   <input type="file" id="training-data-zip" accept=".zip">
                   <button id="upload-data-btn" class="btn btn-primary"
                           onclick="uploadTrainingData()">Upload ZIP</button>
               </div>
               <div id="upload-status"></div>
               <div id="upload-progress" style="display:none">
                   <progress id="upload-progress-bar" max="100" value="0"></progress>
                   <span id="upload-progress-text"></span>
               </div>

               <p class="license-notice"><strong>Note:</strong> The community repos above have
                  no explicit license. Use for personal/local training only.
                  <a href="https://github.com/jomjol/AI-on-the-edge-device/issues/4041"
                     target="_blank" rel="noopener">License tracking issue</a></p>

               <h4>Option C: Import a pre-trained model</h4>
               <p>If someone shares an exported model (.xml + .bin + metadata.json),
                  place the files in <code>/app/models/digits/&lt;model_id&gt;/</code> or
                  <code>/app/models/arrows/&lt;model_id&gt;/</code> and activate it
                  in the Models section below.</p>
           </div>
       </details>
   </div>
   ```

   Add JS function `uploadTrainingData()`:
   ```javascript
   async function uploadTrainingData() {
       const fileInput = document.getElementById('training-data-zip');
       const dataType = document.getElementById('data-type-select').value;
       const statusEl = document.getElementById('upload-status');
       const progressEl = document.getElementById('upload-progress');
       const progressBar = document.getElementById('upload-progress-bar');
       const progressText = document.getElementById('upload-progress-text');
       const btn = document.getElementById('upload-data-btn');

       if (!fileInput.files.length) {
           statusEl.textContent = 'Please select a ZIP file.';
           statusEl.style.color = 'var(--danger)';
           return;
       }

       const file = fileInput.files[0];
       if (!file.name.endsWith('.zip')) {
           statusEl.textContent = 'Only ZIP files are supported.';
           statusEl.style.color = 'var(--danger)';
           return;
       }

       const formData = new FormData();
       formData.append('file', file);
       formData.append('type', dataType);

       btn.disabled = true;
       btn.textContent = 'Uploading...';
       statusEl.textContent = '';
       progressEl.style.display = 'block';

       try {
           const xhr = new XMLHttpRequest();
           xhr.open('POST', '/api/training-data/upload');

           xhr.upload.onprogress = (e) => {
               if (e.lengthComputable) {
                   const pct = Math.round((e.loaded / e.total) * 100);
                   progressBar.value = pct;
                   progressText.textContent = `${pct}% uploaded`;
               }
           };

           xhr.onload = () => {
               progressEl.style.display = 'none';
               const data = JSON.parse(xhr.responseText);
               if (xhr.status === 200) {
                   statusEl.textContent = data.message;
                   statusEl.style.color = 'var(--success)';
                   loadTrainingStats();  // Refresh dataset stats
               } else {
                   statusEl.textContent = 'Error: ' + data.detail;
                   statusEl.style.color = 'var(--danger)';
               }
           };

           xhr.onerror = () => {
               progressEl.style.display = 'none';
               statusEl.textContent = 'Upload failed.';
               statusEl.style.color = 'var(--danger)';
           };

           xhr.send(formData);
       } finally {
           btn.disabled = false;
           btn.textContent = 'Upload ZIP';
       }
   }
   ```

2. **`watermeter/static/style.css`** -- Add styles for upload area and license notice:
   ```css
   .upload-area {
       display: flex;
       gap: 12px;
       align-items: center;
       flex-wrap: wrap;
       padding: 16px;
       background: var(--bg-secondary);
       border-radius: 8px;
       margin: 12px 0;
   }
   .upload-area select {
       padding: 8px 12px;
       border: 1px solid var(--border);
       border-radius: 6px;
   }
   .upload-area input[type="file"] {
       flex: 1;
       min-width: 200px;
   }
   .license-notice {
       font-size: 0.9em;
       color: var(--text-light);
       background: #fef3cd;
       padding: 8px 12px;
       border-radius: 6px;
       margin-top: 12px;
   }
   .repo-links {
       list-style: none;
       padding-left: 0;
   }
   .repo-links li {
       padding: 8px 0;
       border-bottom: 1px solid var(--border-light);
   }
   #upload-progress {
       display: flex;
       align-items: center;
       gap: 12px;
       margin-top: 8px;
   }
   #upload-progress progress {
       flex: 1;
       height: 8px;
   }
   ```

#### Acceptance criteria:
- Training page shows "Getting Started" guide with 3 options
- Direct download links to jomjol repo ZIPs (open in new tab)
- ZIP file upload with type selector (digits/arrows) and progress bar
- Upload calls `POST /api/training-data/upload` (implemented in WP-5a)
- License notice visible with link to issue #4041
- Guide is collapsible (details/summary)
- Links to ROI Config, Labeling pages work

---

### WP-5a: Training data ZIP upload endpoint + sorting logic

**Agent:** senior-dev (opus)
**Dependencies:** None (can run in parallel)

#### New API endpoint: `POST /api/training-data/upload`

Accepts a ZIP file upload, detects the internal structure, extracts images, and sorts them into the correct `ground_truth/{class}/` directory structure.

#### Sorting logic — two supported formats:

**Format 1: Subdirectory-based (our format, standard)**
```
uploaded.zip
  └── 0/
  │   ├── img001.jpg
  │   └── img002.jpg
  ├── 1/
  │   └── img003.jpg
  ├── NAN/
  │   └── img004.jpg
  ...
```
Detection: ZIP contains directories named `0/`, `1/`, ..., `9/`, `NAN/` (digits) or `0.0/`, `0.1/`, ..., `9.9/` (arrows) with image files inside.
Action: Extract directly into `ground_truth/`.

**Format 2: Prefix-based (jomjol format)**
```
uploaded.zip
  └── some_directory/
      └── data_subdir/
          ├── 0_img001.jpg
          ├── 3_img002.jpg
          ├── NaN_img003.jpg
          ...
```
Detection: ZIP contains image files whose names start with a class prefix followed by `_` (e.g. `3_`, `NaN_`, `5.7_`).
Action: Create class subdirectories and sort files by prefix.

**Class mappings:**
- Digits: `0`-`9` → `0/`-`9/`, `NaN` → `NAN/`
- Arrows: `0.0`-`9.9` → `0.0/`-`9.9/`

#### Files to modify:

1. **`watermeter/routes/training.py`** -- Add new endpoint:

   ```python
   @router.post("/api/training-data/upload",
                 summary="Upload training data ZIP",
                 tags=["Training Data"])
   async def upload_training_data(
       file: UploadFile = File(...),
       type: str = Form(...)  # "digits" or "arrows"
   ):
   ```

   Implementation:
   ```python
   import zipfile
   import tempfile
   import shutil
   import re
   from pathlib import Path

   DIGIT_CLASSES = [str(i) for i in range(10)] + ["NAN"]
   DIGIT_PREFIX_RE = re.compile(r'^(NaN|[0-9])_')
   ARROW_PREFIX_RE = re.compile(r'^([0-9]\.[0-9])_')
   IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp'}

   async def upload_training_data(file, type):
       if type not in ("digits", "arrows"):
           raise HTTPException(400, "type must be 'digits' or 'arrows'")

       if not file.filename.endswith('.zip'):
           raise HTTPException(400, "Only ZIP files are supported")

       ground_truth_dir = Path(f"/training/{type}/ground_truth")
       ground_truth_dir.mkdir(parents=True, exist_ok=True)

       # Save uploaded file to temp
       with tempfile.TemporaryDirectory() as tmp_dir:
           tmp_zip = Path(tmp_dir) / "upload.zip"
           with open(tmp_zip, "wb") as f:
               content = await file.read()
               f.write(content)

           # Validate ZIP
           if not zipfile.is_zipfile(tmp_zip):
               raise HTTPException(400, "Invalid ZIP file")

           with zipfile.ZipFile(tmp_zip, 'r') as zf:
               zf.extractall(Path(tmp_dir) / "extracted")

           extracted = Path(tmp_dir) / "extracted"

           # Collect all image files recursively
           all_images = [
               p for p in extracted.rglob("*")
               if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
           ]

           if not all_images:
               raise HTTPException(400, "No image files found in ZIP")

           # Detect format
           format_type, sorted_images = detect_and_sort(all_images, type)

           # Copy sorted images to ground_truth
           imported = 0
           for class_name, image_paths in sorted_images.items():
               class_dir = ground_truth_dir / class_name
               class_dir.mkdir(parents=True, exist_ok=True)
               for img_path in image_paths:
                   dest = class_dir / img_path.name
                   # Avoid overwriting — add suffix if exists
                   if dest.exists():
                       stem = img_path.stem
                       dest = class_dir / f"{stem}_imported{img_path.suffix}"
                   shutil.copy2(img_path, dest)
                   imported += 1

           return {
               "message": f"Imported {imported} images into {len(sorted_images)} classes ({format_type} format)",
               "format": format_type,
               "images": imported,
               "classes": len(sorted_images)
           }
   ```

2. **Sorting detection function** (same file or utility module):

   ```python
   def detect_and_sort(images: list[Path], data_type: str) -> tuple[str, dict]:
       """Detect ZIP structure and return (format_name, {class: [paths]}).

       Tries subdirectory-based first (standard), falls back to prefix-based.
       """
       # --- Try Format 1: subdirectory-based ---
       by_parent = {}
       for img in images:
           parent_name = img.parent.name
           by_parent.setdefault(parent_name, []).append(img)

       if data_type == "digits":
           valid_classes = set(str(i) for i in range(10)) | {"NAN", "NaN", "nan"}
       else:  # arrows
           valid_classes = set(f"{i}.{j}" for i in range(10) for j in range(10))

       # Check if parent directory names match expected classes
       matching_parents = set(by_parent.keys()) & valid_classes
       if len(matching_parents) >= 3:  # at least 3 classes found in dirs
           result = {}
           for parent_name, imgs in by_parent.items():
               normalized = parent_name.upper() if parent_name.lower() == "nan" else parent_name
               if normalized in valid_classes or parent_name in valid_classes:
                   result[normalized] = imgs
           if result:
               return ("subdirectory", result)

       # --- Try Format 2: prefix-based ---
       prefix_re = DIGIT_PREFIX_RE if data_type == "digits" else ARROW_PREFIX_RE
       result = {}
       for img in images:
           match = prefix_re.match(img.name)
           if match:
               class_name = match.group(1)
               if data_type == "digits" and class_name == "NaN":
                   class_name = "NAN"
               result.setdefault(class_name, []).append(img)

       if result:
           return ("prefix", result)

       raise HTTPException(400,
           "Could not detect data format. Expected either: "
           "subdirectories per class (0/, 1/, ...) or "
           "filename prefixes (3_img.jpg, NaN_img.jpg)")
   ```

#### Acceptance criteria:
- `POST /api/training-data/upload` accepts ZIP + type (digits/arrows)
- **Subdirectory format** (our standard): ZIP with `0/`, `1/`, ..., `NAN/` subdirs → extracted directly
- **Prefix format** (jomjol): ZIP with `3_img.jpg`, `NaN_img.jpg` flat files → sorted into subdirs
- Auto-detection: tries subdirectory first, falls back to prefix
- `NaN` normalized to `NAN` (our convention)
- Existing files not overwritten (suffix added)
- Returns count of imported images and classes
- Rejects non-ZIP files, empty ZIPs, ZIPs without images
- Works for both digits (11 classes) and arrows (100 classes)

---

### WP-5b: Fix fetch_upstream_data.sh (standalone)

**Agent:** junior-dev (sonnet)
**Dependencies:** None (can run in parallel)

The existing `scripts/fetch_upstream_data.sh` stays in the repo as a standalone CLI tool (NOT copied into the Docker image). It has 3 bugs that make it non-functional.

#### Bugs to fix:

1. **Wrong branch for analog repo (critical):**
   ```bash
   # Line ~29: WRONG — causes 404
   ANALOG_BRANCH="master"
   # CORRECT:
   ANALOG_BRANCH="main"
   ```

2. **Wrong primary data directory for digits:**
   ```bash
   # Line ~37: WRONG — directory doesn't exist
   DIGITS_DATA_SUBDIR="neural-network-digital-counter-readout-${DIGITS_BRANCH}/ziffer_sortiert_resize"
   # CORRECT:
   DIGITS_DATA_SUBDIR="neural-network-digital-counter-readout-${DIGITS_BRANCH}/03_data_resize_all-use_for_training"
   ```

3. **Flat file structure vs. expected subdirectories (critical):**

   Both repos use flat directories with filename prefixes, but the script expects subdirectories per class. The sorting logic (~L222-250) must handle both formats:

   **For digits:**
   ```bash
   mkdir -p "$target_dir"/{0,1,2,3,4,5,6,7,8,9,NAN}
   for img in "$src_dir"/*.jpg "$src_dir"/*.png; do
       [[ ! -f "$img" ]] && continue
       filename=$(basename "$img")
       if [[ "$filename" =~ ^(NaN|[0-9])_ ]]; then
           class="${BASH_REMATCH[1]}"
           [[ "$class" == "NaN" ]] && class="NAN"
           cp "$img" "$target_dir/$class/"
       fi
   done
   ```

   **For analog:**
   ```bash
   for img in "$src_dir"/*.jpg "$src_dir"/*.png; do
       [[ ! -f "$img" ]] && continue
       filename=$(basename "$img")
       if [[ "$filename" =~ ^([0-9]\.[0-9])_ ]]; then
           class="${BASH_REMATCH[1]}"
           mkdir -p "$target_dir/$class"
           cp "$img" "$target_dir/$class/"
       fi
   done
   ```

#### Files to modify:

1. **`scripts/fetch_upstream_data.sh`** — Fix all 3 bugs above

#### Acceptance criteria:
- Script downloads and sorts digits into `ground_truth/{0,1,...,9,NAN}/`
- Script downloads and sorts analog into `ground_truth/{0.0,0.1,...,9.9}/`
- Script prints license warning before downloading
- Script handles both flat (prefix-based) and subdirectory-based source structures
- Cleanup of temp download directory after extraction

---

### WP-6: Entrypoint and config resilience

**Agent:** junior-dev (sonnet)
**Dependencies:** WP-1

#### Files to modify:

1. **`docker-entrypoint.sh`** -- Already modified in WP-1 to handle missing default models. Additionally:
   - Ensure the config.yaml shipped as default does NOT reference specific model files that don't exist. The default `config.yaml` currently points to specific model paths (L111-118). If those files don't exist, `InferenceService.initialize()` will try to load them and fail (which is now graceful after WP-2).
   - However, the `sed` commands at L41 and L71 that update config with model paths should only run when models are actually being installed.
   - After WP-1, when no models ship with the image, the config should retain its default model paths. This is fine -- `InferenceService.initialize()` will log warnings and proceed.

2. **`config.yaml`** -- Consider whether the default config should have empty/placeholder model paths instead of specific paths that don't exist. Options:
   - **Option A (recommended):** Keep current paths -- they serve as documentation of the expected format. `InferenceService` will warn and proceed.
   - **Option B:** Set paths to empty string or a sentinel like `""`. This requires `InferenceService.initialize()` to check for empty paths.

   Go with **Option A** -- less invasive, and the inference service already handles missing files gracefully after WP-2.

3. **`Dockerfile`** -- No changes needed for fetch script (it stays outside the image). The upload endpoint (WP-5a) uses Python's `zipfile` module, which is in the stdlib.

#### Acceptance criteria:
- Container starts cleanly with no models, no data
- Config.yaml is valid and parseable even without models
- Entrypoint logs info message when no default models exist

---

### WP-7: Clean up dead code in inference.py

**Agent:** junior-dev (sonnet)
**Dependencies:** WP-2

#### Files to modify:

1. **`watermeter/inference.py`**

   Lines 399-524 contain:
   - L399-407: Module-level initialization (removed in WP-2)
   - L403: A separate `app = FastAPI()` that is NEVER mounted or used by the main app
   - L415: `classifiers = None` -- deprecated variable
   - L420-432: `SetupRequest` / `PredictRequest` Pydantic models for Label Studio
   - L434-441: `download_image()` helper for Label Studio
   - L445-524: Label Studio ML Backend routes (`/predict/{model_type}/setup`, `/predict/{model_type}/predict`, `/health`, etc.)

   These Label Studio routes are **dead code** -- they are mounted on a separate `app` variable that is never imported or used. The main FastAPI app in `app.py` uses its own `app = FastAPI(...)`.

   **Action:** Remove all dead code from L399 onward. Keep only:
   - The `InferenceService` class
   - The `get_inference_service()` singleton function
   - The `validate_model_config()` function
   - The `Classifier`, `Regressor`, and `_detect_training_mode` helpers

   This removes ~125 lines of dead code and eliminates the confusing module-level `app = FastAPI()`.

#### Acceptance criteria:
- No module-level side effects in `inference.py`
- Label Studio dead code removed
- All existing tests pass (if any reference the removed code, they need updating too)
- `get_inference_service()` works as a lazy singleton

---

### WP-9: Setup Wizard (rotation & ROI configuration)

**Agent:** senior-dev (opus) for backend + routing; frontend (sonnet) for template + JS
**Dependencies:** WP-2 (app must start without models), WP-3 (graceful service behavior)

#### Motivation

The existing `/roi-config` page is already a 4-step wizard (rotation, markers, digits, analogs), implemented as a single-page canvas-based tool with `roi-config.js` (1490 lines). It works well for reconfiguration, but has two gaps for the first-run experience:

1. **No auto-launch on first start.** A newcomer must manually navigate to `/roi-config`.
2. **No image source URL step.** The wizard assumes `images.src` in `config.yaml` is already correct. If the default placeholder URL (`http://192.168.x.x/img_tmp/alg.jpg`) is wrong, `fetchAndReload()` fails silently.

#### Architecture: Extend existing page, not a new one

The existing `/roi-config` IS a wizard. Creating a duplicate would mean maintaining two canvas/overlay/step systems. Instead:

1. **Add "Step 0: Image Source"** to the existing `/roi-config` page
2. **Add first-run redirect** from dashboard to `/roi-config?setup=1`
3. **Add "Re-run Setup" button** on the config editor page

#### First-Run Detection

**Condition:** Zero digit ROIs in config → unconfigured.

```python
def is_first_run(config: dict) -> bool:
    rois = config.get("detection", {}).get("digits", {}).get("rois", [])
    return len(rois) == 0
```

- Checked in `routes/pages.py` `dashboard()` — redirects to `/roi-config?setup=1`
- Stateless: no flag, no cookie, purely config-based
- Uses HTTP 303 redirect to avoid caching issues

#### Wizard Flow

**Step 0: Image Source** (new — shown only in setup mode or when no reference image exists)

1. Text input pre-filled with current `images.src` value
2. User enters AIOTE camera URL (e.g. `http://192.168.1.105/img_tmp/alg.jpg`)
3. Clicks "Test & Save" → `POST /api/roi/image-source`
4. Backend validates URL, fetches image (10s timeout), saves URL to `config.yaml` (`images.src` + `aiote.host`), saves image to `/data/reference_raw.jpg`
5. On success: image displayed on canvas, advance to Step 1 (Rotation)
6. On failure: clear error message ("Could not reach device at ...")

**Steps 1-4:** Unchanged (Rotation, Markers, Digits, Analogs)

**Completion (setup mode only):** After saving analogs, show "Setup Complete!" with "Go to Dashboard" link.

#### Files to Modify

1. **`watermeter/routes/pages.py`** (L24, L48)
   - `dashboard()` L24: Add first-run check → `RedirectResponse("/roi-config?setup=1", status_code=303)` when no digit ROIs
   - `roi_config_page()` L48: Read `?setup` query param, pass `setup_mode` and `image_src` to template context

2. **`watermeter/routes/roi.py`** (after L136)
   - New endpoint: `POST /api/roi/image-source`
   - Accepts `{"url": "http://..."}`, validates, fetches, saves URL to config + image to disk
   - Returns `{success: true/false, message: "..."}`

3. **`watermeter/templates/roi_config.html`** (L27)
   - Add Step 0 section before canvas container:
     ```html
     <section id="step-image-source" class="roi-step" {% if not setup_mode %}style="display:none"{% endif %}>
         <h2>Step 0: Camera Image Source</h2>
         <p>Enter the URL of your AI-on-the-edge device's camera image.</p>
         <div class="image-source-controls">
             <input type="url" id="image-source-url" value="{{ image_src }}"
                    placeholder="http://192.168.1.x/img_tmp/alg.jpg" class="image-source-input">
             <button id="test-image-btn" class="btn btn-primary" onclick="testImageSource()">Test & Save</button>
         </div>
         <div id="image-source-status"></div>
     </section>
     ```
   - Add setup mode welcome banner (conditional)
   - Add completion section at bottom (shown after Step 4 save in setup mode)

4. **`watermeter/static/roi-config.js`** (~L1365)
   - Add `testImageSource()` function: POST to `/api/roi/image-source`, handle response, advance to Step 1 on success
   - Modify initialization (~L1488): in setup mode, show Step 0 and hide other steps until image source is validated
   - After `saveAnalogs()` (~L1149): in setup mode, show `#setup-complete` div

5. **`watermeter/templates/config_editor.html`** (L211)
   - Add "Setup Wizard" button in toolbar: `<a href="/roi-config?setup=1" class="reload-btn">Setup Wizard</a>`

6. **`watermeter/static/style.css`** (~L628)
   - `.image-source-controls` — flex layout for input + button
   - `.image-source-input` — monospace, bordered, full-width
   - `.setup-wizard-banner` — gradient background, welcome styling
   - `#setup-complete` — centered, success-colored heading

#### Edge Cases

- **Direct `/roi-config` access (no `?setup=1`):** Works as today, Step 0 hidden
- **Image source changes later:** User can re-run via config editor button or use "Reload Image" on ROI page
- **Setup complete but no models:** Dashboard shows WP-4's "No Models" state → Training page
- **User cancels wizard midway:** No ROIs saved → next visit to `/` redirects again

#### Acceptance Criteria

1. Navigating to `/` with no digit ROIs redirects to `/roi-config?setup=1`
2. Setup mode shows welcome banner + Step 0 (Image Source)
3. "Test & Save" validates URL, fetches image, saves config, advances to Step 1
4. Invalid/unreachable URLs show clear error messages
5. Steps 1-4 work identically in both modes
6. Completion message with "Go to Dashboard" link shown after Step 4 in setup mode
7. Config editor has "Setup Wizard" button linking to `/roi-config?setup=1`
8. Normal `/roi-config` access (without `?setup=1`) unchanged
9. Redirect stops once digit ROIs are configured

---

### WP-8: Integration testing

**Agent:** tester (sonnet)
**Dependencies:** WP-1 through WP-9

#### Testing plan:

1. **Docker build test (no models):**
   ```bash
   # From a clean state (no selected/ dirs, no ov_model/ dirs)
   docker build . -t watermeter-test-fresh
   ```
   Verify: build succeeds.

2. **Container startup test (no models):**
   ```bash
   docker run --rm -p 8099:8001 watermeter-test-fresh
   ```
   Verify:
   - Container starts without crashing
   - Logs show "No digits model" and "No arrows model" warnings
   - Logs show "Skipping initial reading (no models loaded)"

3. **First-run redirect test (no ROIs):**
   - Navigate to `http://localhost:8099/`
   - Verify: redirects to `/roi-config?setup=1`
   - Verify: welcome banner and Step 0 (Image Source) visible
   - Verify: Steps 1-4 hidden until Step 0 completed

4. **Dashboard test (ROIs configured, no models):**
   - Configure ROIs, then navigate to `http://localhost:8099/`
   - Verify: "No Models Loaded" message appears
   - Verify: "Read Now" button is disabled
   - Verify: Link to training page works

5. **API test (no models):**
   ```bash
   curl http://localhost:8099/api/status
   # Expect: {"status": "idle", "total_value": null, ...}

   curl -X POST http://localhost:8099/api/trigger
   # Expect: 503 {"message": "No inference models loaded..."}
   ```

6. **Training page test:**
   - Navigate to `http://localhost:8099/training`
   - Verify: "Getting Started" section is visible
   - Verify: Dataset stats show 0 images
   - Verify: All 3 data acquisition options are documented

7. **Model activation test (State 2 -> State 3):**
   - Start container with no models
   - Copy model files into `/app/models/digits/test_model/`
   - Activate model via API
   - Verify: inference becomes available
   - Verify: "Read Now" works (will fail to fetch images, but inference is attempted)

8. **Setup wizard test (Step 0):**
   - Navigate to `/roi-config?setup=1`
   - Enter invalid URL → verify error message
   - Enter valid AIOTE URL → verify image fetch + save + advance to Step 1
   - Complete all steps → verify "Setup Complete" message + "Go to Dashboard" link
   - Verify config editor has "Setup Wizard" button

9. **Regression test (State 3, normal operation):**
   - Use `debug.sh` with existing models
   - Verify: everything works as before
   - Run existing tests if applicable

#### Acceptance criteria:
- All 9 test scenarios pass
- No regression in normal (State 3) operation
- Container startup time is not noticeably slower

---

## Execution Order

```
WP-1  (Dockerfile/submodules) ---|
                                  |---> WP-6 (entrypoint/config) --|
WP-2  (inference resilience) ----|---> WP-7 (dead code cleanup) --|
                                  |---> WP-3 (service behavior) --|--> WP-9 (setup wizard) --> WP-8 (testing)
                                  |---> WP-4 (dashboard UI) ------|
WP-5  (training page UI+links) --|
WP-5a (upload endpoint+sorting) -|
WP-5b (fetch script bugfix) -----|
```

**Parallel group 1:** WP-1, WP-2, WP-5, WP-5a, WP-5b (no dependencies)
**Sequential after WP-2:** WP-3, WP-4, WP-7
**Sequential after WP-1:** WP-6
**After WP-2 + WP-3:** WP-9 (setup wizard)
**Final:** WP-8 (after all others including WP-9)

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Module-level inference.py code is imported by multiple paths | Grep for all `from .inference import` and `import inference` to verify no other import triggers initialization |
| Removing COPY from Dockerfile breaks production deploy | Production uses volume-mounted models at `/app/models/`, not the baked-in staging dirs. Entrypoint already checks `/app/models/` first. |
| `debug.sh` stops working for users who DO have models | Update `debug.sh` to skip model prep gracefully, not fail |
| Training page instructions become stale | Use generic instructions, link to upstream repos |
| Label Studio dead code removal breaks something | The separate `app = FastAPI()` in inference.py is never imported -- verify with grep |
| Setup wizard redirect loop | Only triggers when digit ROIs list is empty — once one ROI is saved, redirect stops |
| Step 0 breaks existing ROI config flow | Step 0 is hidden by default (only shown with `?setup=1`), existing `/roi-config` unchanged |
| `fetch_upstream_data.sh` fails due to upstream repo changes | Script already has fallback paths; archive URLs are stable as long as repos exist |
