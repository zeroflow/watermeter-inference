# Code Review: watermeter-inference

**Date**: 2026-02-10
**Codebase**: ~13,400 lines across 19 source files

---

## Review Plan

The review is organized into 6 increments by logical domain. Each increment reviews a cohesive group of files and produces findings inline below.

### Increment 1: Core Application & API Layer
**Files**: `app.py` (1779 lines)
**Focus**: Route organization, request/response patterns, error handling, security (path traversal, input validation), code duplication, API design consistency.

### Increment 2: Service Layer & Configuration
**Files**: `watermeter_service.py` (968), `config_utils.py` (389), `persistence.py` (64)
**Focus**: Service singleton pattern, MQTT lifecycle, config loading/saving, state management, thread safety, error resilience.

### Increment 3: Inference & Model Management
**Files**: `inference.py` (378), `model_manager.py` (319), `server_detect.py` (85)
**Focus**: Model loading/reloading, inference pipeline correctness, model file handling, active model switching, resource management.

### Increment 4: Training & Benchmark Backend
**Files**: `training_manager.py` (1149), `train_digits.py` (498), `train_arrows.py` (634), `benchmark_digits.py` (427), `benchmark_arrows.py` (447)
**Focus**: Job orchestration, thread management, cancellation logic, data pipeline, training correctness, benchmark accuracy calculations, code duplication between digits/arrows variants.

### Increment 5: Frontend Templates
**Files**: `templates/training.html` (2101), `templates/roi_config.html` (1729), `templates/label.html` (593), `templates/config_editor.html` (397), `templates/dashboard.html` (161), `templates/status_fragment.html` (82)
**Focus**: HTMX usage patterns, JavaScript quality, UX consistency, XSS risks, accessibility, inline script size.

### Increment 6: Infrastructure & Cross-Cutting Concerns
**Files**: `Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `requirements*.txt`, `config.yaml`
**Focus**: Docker build efficiency, volume mounts, dependency management, startup sequence, overall architecture assessment, summary of top findings.

---

## Findings

*(Findings will be appended per increment below)*

---

### Increment 1: Core Application & API Layer

**File**: `app.py` — 1779 lines, ~45 endpoints

#### SECURITY

**S1. Path traversal in training/label endpoints** (High) ✅ FIXED
- `submit_for_training` (line 223): `submission.model` is used directly in `Path(save_path) / submission.model / 'input'`. A crafted value like `../../etc` would escape the intended directory.
- `submit_label` (line 1181): `submission.filename` is used in path construction. A filename like `../../../etc/passwd` would traverse.
- `delete_image` (line 1267): Same issue with `submission.filename`.
- **Fix**: Validate that resolved paths stay within the expected base directory, e.g. `resolved.resolve().is_relative_to(base_path)`.

**S2. No input size limits on base64 uploads** (Medium)
- `submit_for_training` (line 227): Decodes arbitrary base64 without size checks. An attacker could fill the disk with a single large payload.
- **Fix**: Add a max size check on `len(submission.image_base64)` before decoding, or use FastAPI's `UploadFile` with size limits.

**S3. No authentication on destructive endpoints** (Low for home use)
- Endpoints like `DELETE /api/models/...`, `POST /api/config/save`, `POST /api/training/start` have no auth. Acceptable for a LAN-only home automation tool but worth noting.

#### BUGS / CORRECTNESS

**B1. `delete_markers` hardcodes `range(1, 3)`** (line 625) ✅ FIXED
- Only deletes `marker_1.jpg` and `marker_2.jpg`. If a user configured 3+ markers, their image files are orphaned on disk.
- **Fix**: Count actual markers from config before deletion (like `delete_digits` and `delete_analogs` already do correctly).

**B2. `asyncio.create_task` without storing reference** (lines 125, 191) ✅ FIXED
- Fire-and-forget tasks can be garbage-collected before completion in CPython. The asyncio docs explicitly warn about this.
- **Fix**: Store references in a set, e.g. `background_tasks = set(); t = asyncio.create_task(...); background_tasks.add(t); t.add_done_callback(background_tasks.discard)`.

**B3. Config save destroys YAML comments** (9 endpoints) ✅ FIXED
- The ROI endpoints (rotation, markers, digits, analogs — save and delete) use `yaml.safe_load` + `yaml.dump` (lines 449–962), which strips all comments from `config.yaml`.
- Meanwhile, the config editor endpoint (line 336) correctly uses `config_utils` with ruamel.yaml to preserve comments.
- **Fix**: All config-writing endpoints should use `config_utils` for consistency.

**B4. Config read-modify-write race condition** (all ROI save/delete endpoints)
- Multiple concurrent requests each read config.yaml, modify in-memory, and write back. The last writer wins, silently dropping the other's changes.
- **Fix**: Use a file lock (`fcntl.flock` or `filelock` library), or funnel all config writes through a single async function with an `asyncio.Lock`.

#### CODE DUPLICATION

**D1. ROI extraction pattern repeated 5 times** (~50 lines each)
- `save_markers` (line 516), `save_digits` (line 663), `save_analogs` (line 888), `preview_digit_inference` (line 813), `preview_analog_inference` (line 1038) all contain the identical sequence: load reference image → apply rotation → convert normalized coords to pixels → clamp → extract ROI.
- **Fix**: Extract a helper like `_load_and_extract_roi(submission) -> (img, roi_img)`.

**D2. Pydantic models with identical fields** (4 models)
- `MarkerBox`, `DigitRoi`, `AnalogRoi`, `SingleRoiSubmission` all have the same 4 fields: `x`, `y`, `width`, `height`.
- **Fix**: Single `RoiBox` base model, reuse or alias where needed.

**D3. Config load-modify-save boilerplate** (~10 endpoints) ✅ FIXED (update_config helper created)
- Every ROI endpoint repeats: open config.yaml → safe_load → ensure 'detection' exists → modify → dump → reload service.config.
- **Fix**: A helper like `update_config(path: str, updater: Callable[[dict], None])` would eliminate this.

#### API DESIGN

**A1. Inconsistent response format**
- Some endpoints return bare dicts: `health()` (line 1300), `get_status()` (line 161).
- Most return `JSONResponse({"success": True/False, ...})`.
- `get_training_status()` returns `{"training": ..., "benchmark": ..., "queue": ...}` without a `success` field.
- **Fix**: Standardize on a single envelope format or use FastAPI's response_model consistently.

**A2. `config.dict()` deprecated in Pydantic v2** (line 1349)
- Should be `config.model_dump()`. Currently works but will emit deprecation warnings.

**A3. Inconsistent parameter passing style**
- `cancel_training` (line 1368) takes `job_id` and `clear_queue` as query params.
- `cancel_benchmark` (line 1419) takes `job_id` as a query param.
- `start_training` (line 1345) takes a JSON body.
- For POST endpoints that take a single ID, consider using path params or a small body model for consistency.

#### STYLE / MINOR

**M1. Unused import: `numpy`** (line 14) ✅ FIXED
- `import numpy as np` is never used in app.py.

**M2. Redundant import in `__main__` block** (line 1769)
- `import yaml` is already imported at module level (line 13).

**M3. `import tempfile` inside function body** (lines 817, 1042)
- Unconventional; should be at module top-level with other imports.

**M4. Mixed typing styles** (line 1320 vs line 69)
- `List[int]` (typing module) and `list[MarkerBox]` (built-in) mixed in the same file. Pick one style (prefer built-in `list[]` for Python 3.9+).

**M5. `health()` is sync** (line 1300)
- All other endpoints are `async def`. This one is `def`, which runs in a threadpool. Works fine but inconsistent.

**M6. German/English mixed in user-facing strings**
- Line 252: `"Bild gespeichert"`, line 259: `"Fehler beim Speichern"`, while all other messages are English.

### Increment 2: Service Layer & Configuration

**Files**: `watermeter_service.py` (968), `config_utils.py` (389), `persistence.py` (64)

#### BUGS / CORRECTNESS

**B5. `_align_with_markers` is a dead stub** (`watermeter_service.py:217-250`)
- The method builds source points from marker coordinates, then returns the image unchanged. A comment says "for future use." The entire marker alignment pipeline is non-functional — markers are saved and extracted in app.py, but never used for alignment. Users configuring markers get no benefit.
- **Fix**: Either implement alignment or remove the stub and the marker-related code from app.py to avoid misleading users.

**B6. MQTT thread mutates shared state without locking** (`watermeter_service.py:900-918`) ✅ FIXED
- `on_mqtt_message` runs in paho-mqtt's network thread. It calls `reset_previous_value()` which mutates `self.previous_value`, `self.rate_history`, `self.consecutive_rejections` — all of which are also accessed from the async `process_reading()` coroutine on the event loop thread.
- `run_coroutine_threadsafe` is correctly used for `process_reading()` (line 913), but `reset_previous_value()` (line 918) is called directly from the MQTT thread.
- **Fix**: Route `reset_previous_value` through `run_coroutine_threadsafe` as well, or protect the shared state with a threading lock.

**B7. MQTT `connect()` blocks the event loop** (`watermeter_service.py:941`)
- `start_mqtt()` is called during the FastAPI lifespan (async context). `mqtt_client.connect()` is a blocking network call that stalls the event loop until the TCP handshake completes (or times out).
- **Fix**: Use `connect_async()` or run `connect()` in an executor via `await loop.run_in_executor(None, ...)`.

**B8. No timeout on processing lock** (`watermeter_service.py:618`)
- `async with self.processing_lock` blocks indefinitely if a previous `process_reading()` hangs (e.g., on a network fetch that doesn't time out). All subsequent trigger requests would queue silently.
- **Fix**: Use `asyncio.wait_for(self.processing_lock.acquire(), timeout=120)` or similar.

**B9. Persistence writes are not atomic** (`persistence.py:29-30`) ✅ FIXED
- `json.dump()` writes directly to the target file. A crash mid-write (or disk full) corrupts the state file, losing the previous value permanently.
- **Fix**: Write to a temporary file in the same directory, then `os.rename()` (atomic on POSIX).

**B10. Config key access without defensive `.get()`** (`watermeter_service.py:42,48,75`)
- `self.config['logging']['level']` (line 42), `self.config['homeassistant']['enabled']` (line 48), `self.config['plausibility']` (line 75) — if any of these keys are missing from config.yaml, the service crashes on startup with a KeyError.
- Other parts of the code correctly use `.get()` with defaults, making this inconsistent.

**B11. `on_mqtt_connect` callback signature may break with paho-mqtt v2** (`watermeter_service.py:882`)
- Signature `(client, userdata, flags, rc)` is for paho-mqtt v1. Version 2 expects `(client, userdata, flags, reason_code, properties)`. If requirements don't pin paho-mqtt to v1, a future update would silently break the callback.

#### ARCHITECTURE / DESIGN

**A4. `logging.basicConfig` called at module import time** (`watermeter_service.py:25-28`)
- Configures the root logger when the module is imported. This can override or conflict with FastAPI/uvicorn's logging setup, or any other logging configuration from the calling application.
- **Fix**: Move to the lifespan startup or `__init__`.

**A5. Singleton `get_service()` is not thread-safe** (`watermeter_service.py:963-968`)
- Uses a global variable without locking. MQTT callbacks run in a separate thread that could race with the main thread during startup, though in practice this is unlikely since MQTT starts after the singleton is created.

**A6. In-memory predictions hold raw image bytes** (`watermeter_service.py:317`)
- `image_bytes` is stored in every prediction dict, carried through the full pipeline, then base64-encoded into `current_state['predictions']` (line 707). These base64 strings persist in memory until the next reading.
- For a system with many ROIs or frequent readings, this could accumulate noticeable memory pressure.

**A7. Schema defined but not used for validation** (`config_utils.py:121-378`)
- `CONFIG_SCHEMA` is a comprehensive JSON Schema served to the Monaco editor, but `validate_config()` (line 88) only checks 3 required top-level keys. It doesn't use `jsonschema.validate()` or any schema-based validation.
- **Fix**: Use `jsonschema` library to validate against `CONFIG_SCHEMA` in `validate_config()`.

#### STYLE / MINOR

**M7. `import tempfile` / `import shutil` inside method body** (`watermeter_service.py:297,332`)
- Same pattern noted in app.py (M3). Should be at module top level.

**M8. `get_config_schema_json()` appears unused** (`config_utils.py:387-389`)
- Only `get_config_schema()` is called from app.py. The JSON-string variant seems to be dead code.

**M9. Persistence errors silently swallowed** (`persistence.py:33,53,63`)
- All three methods (`save`, `load`, `clear`) catch all exceptions and just log them. Callers never know if persistence failed — could lead to silent data loss where the user thinks state is saved but it isn't.

### Increment 3: Inference & Model Management

**Files**: `inference.py` (378), `model_manager.py` (319), `server_detect.py` (85)

#### BUGS / CORRECTNESS

**B12. Hot-reload is broken for backward-compat imports** (`inference.py:262-263`) ✅ FIXED
- Module-level aliases are set at import time:
  ```python
  digits_classifier = _inference_service.digits_classifier
  arrows_classifier = _inference_service.arrows_classifier
  ```
- `app.py` imports these: `from inference import digits_classifier, arrows_classifier`.
- When `reload_models()` swaps the internal classifiers, these module-level references still point to the **old** classifiers. The hot-reload feature (used by "activate model" in app.py:1588) has no effect on inference done via these imports — which includes all ROI preview endpoints and the entire `watermeter_service.py` pipeline.
- **Fix**: Remove the backward-compat aliases. Have callers use `get_inference_service().predict(model_type, path)` or access the property each time.

**B13. `validate_model_config` calls `sys.exit(1)`** (`inference.py:227,240`) ✅ FIXED
- On filename/config mismatch, the function hard-kills the process. This runs during both startup (`initialize`) and hot-reload (`reload_models`). If a user activates a model whose filename doesn't match the naming convention, the **entire application crashes** instead of returning an error.
- **Fix**: Raise a `ValueError` and let the caller handle it. `reload_models` already has a try/except that would propagate it correctly.

**B14. `archive_model` persists computed fields to metadata.json** (`model_manager.py:280-285`)
- `get_model()` adds transient fields (`id`, `model_type`, `files_exist`) to the dict it returns. `archive_model` calls `get_model`, sets `status`, then calls `save_metadata` — writing those computed fields back to `metadata.json`. On the next read, `files_exist` from the file could be stale (true even if files were deleted).
- **Fix**: Either strip computed fields before saving, or keep a separate `_load_raw_metadata()` for write operations.

**B15. `get_classifier()` returns reference outside lock** (`inference.py:172-180`)
- Acquires the lock, gets the classifier reference, releases the lock, and the caller then uses the classifier freely. During a concurrent `reload_models()`, the classifier could be swapped mid-use by another thread. Compare with `predict()` (line 153) which correctly holds the lock during the entire inference call.
- **Fix**: Deprecate `get_classifier()` or document that callers must hold their own synchronization.

**B16. Temp files leak on inference errors** (`inference.py:321-323, 371-377`)
- In `predict_ls`: if `clf.predict(tmp_path)` throws, `Path(tmp_path).unlink()` is skipped.
- In `predict_direct`: same — if `predict()` fails, temp file persists on disk.
- **Fix**: Use try/finally for cleanup, or use `NamedTemporaryFile` with context manager.

**B17. `Classifier.preprocess` doesn't check for imread failure** (`inference.py:30`) ✅ FIXED
- If `cv2.imread` returns `None` (corrupt file, wrong path), the next line `cv2.cvtColor(img, ...)` crashes with `error: (-206:BadFlag) parameter or structure field` — an opaque OpenCV error.
- **Fix**: Check `if img is None: raise ValueError(f"Failed to read image: {image_path}")`.

#### SECURITY

**S4. Path traversal via `model_id` in ModelManager** (`model_manager.py:80`) ✅ FIXED
- `model_id` comes from URL path parameters (e.g., `GET /api/models/{model_type}/{model_id}`). The value is used directly: `_get_model_types_dir(model_type) / model_id`. A `model_id` of `../../etc` would escape the models directory.
- Affects: `get_model`, `save_metadata`, `delete_model`, `activate_model`, `get_model_path`, `archive_model`.
- **Fix**: Validate `model_id` doesn't contain path separators or `..`, e.g. `if '/' in model_id or '..' in model_id: raise ValueError(...)`.

**S5. Hardcoded server credentials** (`server_detect.py:8-10`) ✅ FIXED (file deleted, see M22)
- `SERVER_IP = "192.168.4.35"`, `SERVER_USER = "thomas"`, plus filesystem paths are hardcoded. Not a runtime security issue (this is a dev/sync script), but credentials in source code are a bad practice, especially if the repo becomes public.

#### ARCHITECTURE / DESIGN

**A8. Two separate FastAPI apps — orphaned Label Studio endpoints** (`inference.py:249`)
- `inference.py` creates its own `app = FastAPI()` at module level with Label Studio ML Backend endpoints (`/predict/{model_type}/setup`, `/predict/{model_type}/predict`, `/health`, etc.).
- When imported by `app.py`, this second app is instantiated but never mounted or served — all Label Studio endpoints are dead code from the main application's perspective. They only work if `inference.py` is run as a standalone `uvicorn inference:app` service.
- **Issue**: Unclear deployment model. If Label Studio integration is still needed, document it. If not, the second FastAPI app and ~110 lines of Label Studio code should be removed.

**A9. Config loaded twice at import time** (`inference.py:246-247`)
- `inference.py` reads `config.yaml` at module level during import. `watermeter_service.py` reads it again in `WatermeterService.__init__()`. Two independent config loads mean the system has two potentially different config snapshots.

**A10. `activate_model` uses `yaml.dump` — same as B3** (`model_manager.py:206-222`) ✅ FIXED
- Yet another config write path that destroys YAML comments. This is the third distinct code path for config writes (also: ROI endpoints in app.py, config editor via config_utils).

#### STYLE / MINOR

**M10. Unused imports in inference.py** (`inference.py:5-8`)
- From `app.py`'s perspective: `FastAPI`, `File`, `UploadFile`, `JSONResponse`, `BaseModel`, `httpx`, `tempfile` are only needed for the Label Studio endpoints. They're loaded into memory on every import even if Label Studio is never used.

**M11. `download_image` has no timeout** (`inference.py:286`)
- `httpx.AsyncClient()` without a timeout could hang indefinitely. Minor since it's only used from the (possibly orphaned) Label Studio endpoint.

**M12. `ModelManager.refresh()` is a no-op** (`model_manager.py:300-307`)
- Documented as "for API compatibility." Callers might expect cache invalidation behavior that doesn't exist.

**M13. `get_model_metadata` is a trivial alias for `get_model`** (`model_manager.py:287-298`)
- Only difference: returns `{}` instead of `None`. Two methods for the same thing adds confusion about which to use.

### Increment 4: Training & Benchmark Backend

**Files**: `training_manager.py` (1149), `train_digits.py` (498), `train_arrows.py` (634), `benchmark_digits.py` (427), `benchmark_arrows.py` (447)

#### BUGS / CORRECTNESS

**B18. Digits benchmark uses class `'N'` instead of `'NAN'`** (`benchmark_digits.py:149`) ✅ FIXED
- `generate_classes()` returns `[..., 'N']` but the ground truth folder is named `NAN/`. Every NAN image will show 0% accuracy because the predicted label can never match the expected label.
- The training manager's version (`training_manager.py:953`) correctly uses `'NAN'`.
- **Fix**: Change `['N']` to `['NAN']` in `benchmark_digits.py:149`.

**B19. `check_cuda_available` always returns False** (`benchmark_arrows.py:22-23`) ✅ FIXED
- `return False` is placed before the docstring — the function unconditionally returns False. All code below it is unreachable dead code. Compare with `benchmark_digits.py` which has a proper 30-line CUDA check.
- **Fix**: Move `return False` below the docstring, or remove it if CUDA testing is desired.

**B20. Softmax without numerical stability in test inference** (`train_digits.py:397`, `train_arrows.py:498`) ✅ FIXED
- Both standalone training scripts' OpenVINO validation step does:
  ```python
  result_softmax = np.exp(result[0]) / np.exp(result[0]).sum()
  ```
  Without subtracting the max first. Large logit values could cause `exp()` overflow → NaN. The benchmark scripts and `Classifier` class all correctly do `result = result - result.max()` first.

**B21. `_auto_benchmark_pending` accessed without lock** (`training_manager.py:134,212,366,390`)
- This list is mutated from multiple threads:
  - Training thread appends at line 366
  - HTTP request thread clears at line 212 (`cancel_training`)
  - Auto-benchmark thread reads/clears at lines 390-391
- Only `training_queue` is protected by `_queue_lock`. `_auto_benchmark_pending` has no synchronization.
- **Fix**: Protect with `_queue_lock` or a separate lock.

**B22. Benchmark cancellation sets FAILED instead of CANCELLED** (`training_manager.py:1003`) ✅ FIXED
- When cancelled, `_execute_benchmark` raises `RuntimeError("Benchmark cancelled")`, caught by the outer handler which sets `job.status = JobStatus.FAILED`. Compare with `_run_training` which explicitly sets `JobStatus.CANCELLED` (line 306). Users see "failed" instead of "cancelled."
- **Fix**: Catch cancellation separately in `_run_benchmark`, set `JobStatus.CANCELLED`.

**B23. Arrow benchmark class counts limited to 4 fixed values** (`training_manager.py:1083-1094`)
- `_generate_arrow_classes` and `_round_to_arrow_class` only support 10, 20, 50, 100 classes. But `_create_arrow_dataset` accepts arbitrary `step_size` — e.g., `step_size=0.3` yields ~33 classes, which crashes the benchmark with `ValueError("Unsupported num_classes")`.
- Same limitation exists in standalone `benchmark_arrows.py:108-127`.
- **Fix**: Compute class labels dynamically from `step_size` instead of hardcoding 4 options.

**B24. `_process_next_in_queue` can recurse unboundedly** (`training_manager.py:386`) ✅ FIXED
- If starting a queued job fails, the catch block recursively calls `_process_next_in_queue()`. A queue full of bad configs would cause a stack overflow.
- **Fix**: Use a loop instead of recursion.

#### CODE DUPLICATION

**D4. Training logic exists in 3 places** (~250 lines each)
- `train_digits.py:train_model()` (lines 163-419)
- `train_arrows.py:train_model()` (lines 264-520)
- `training_manager.py:_execute_training()` (lines 414-758)
- All three contain the same: data loading, stratified split, class weight computation, model creation with timm, training loop, ONNX export, OpenVINO conversion. Differences are minimal (arrows adds seed/step_size handling).
- **Impact**: A bug fix in one must be manually applied to the other two. For example, the softmax stability fix (B20) was applied in training_manager but not in the standalone scripts.

**D5. Benchmark logic exists in 3 places** (~150 lines each)
- `benchmark_digits.py` (entire main pipeline)
- `benchmark_arrows.py` (entire main pipeline)
- `training_manager.py:_execute_benchmark()` (lines 936-1081)
- Near-identical: classifier class, preprocess, inference, accuracy calculation, result formatting.

**D6. Utility functions duplicated between arrows and digits variants**
- `create_data_loaders()` — identical in both training scripts
- `compute_class_weights()` — identical in both
- `analyze_dataset()` — nearly identical
- `generate_classes()` — in both benchmark scripts and training manager
- `round_to_class()` / `_round_to_arrow_class()` — duplicated between benchmark_arrows.py and training_manager.py
- **Fix**: Extract shared logic into a `training_utils.py` module. The standalone scripts can import from it, and `training_manager` can too.

#### ARCHITECTURE / DESIGN

**A11. Heavy ML imports inside `_execute_training`** (`training_manager.py:422-434`)
- `import torch, timm, openvino, torchvision, sklearn` inside the method (lazy loading). This is likely intentional to avoid loading ML libraries at web server startup. Good design decision — but the first training run will be noticeably slower due to import overhead.

**A12. No GPU memory cleanup after training** (`training_manager.py`)
- After training completes, there's no `torch.cuda.empty_cache()` or explicit `del model`. In queue-based training (multiple seeds), GPU memory from one run may not be freed before the next. On a GPU-constrained system, this could cause OOM.

**A13. Benchmark persists computed fields to metadata** (`training_manager.py:909-920`)
- Same issue as B14: `get_model()` adds transient fields, then the entire dict (including benchmark results) is saved back with `save_metadata()`, persisting `files_exist`, `id`, `model_type` alongside the benchmark data.

#### STYLE / MINOR

**M14. Misleading comment "Use symlink instead of copy"** (`train_arrows.py:130`)
- Comment says "Use symlink instead of copy for efficiency" but the next line does `shutil.copy()`. Comment is stale.

**M15. `import json` / `import shutil` / `import math` repeatedly inside methods** (`training_manager.py:687,752,762,793,839,840,946,1098`)
- Standard library imports scattered inside various methods. Unlike the ML imports (which are intentionally lazy), these should be at module level.

**M16. `train_arrows.py:main()` step counter label says `[4/4]` but header says `[1/5]`** (line 391 vs 474)
- Minor numbering inconsistency in progress output.

### Increment 5: Frontend Templates

**Files**: `training.html` (2101), `roi_config.html` (1729), `label.html` (593), `config_editor.html` (397), `dashboard.html` (161), `status_fragment.html` (82), `static/style.css` (1217)

#### SECURITY / XSS

**S6. Log output rendered via `innerHTML` without escaping** (Medium) ✅ FIXED
- `training.html:1523,1663`: Training logs are inserted via `innerHTML`:
  ```js
  logViewer.innerHTML = data.logs.map(log => `<div class="log-line">${log}</div>`).join('');
  ```
- `training.html:2059`: Same pattern in the model log viewer modal.
- Log strings come from the training backend and can contain arbitrary text (error messages, file paths, model names). If a log line contained `<img src=x onerror=alert(1)>`, it would execute.
- Also affected: `training.html:1409` — training config info (`cfg.architecture`, `cfg.model_type`) rendered via innerHTML.
- **Fix**: Use `textContent` instead of `innerHTML`, or escape HTML entities before insertion.

**S7. Dashboard injects server messages as HTML** (`dashboard.html:87,133,135`) ✅ FIXED
- `msg.innerHTML = data.message` and `` msg.innerHTML = `✅ ${data.message}` `` — server-returned messages are rendered as raw HTML. If the API ever echoes user input in the message, this becomes an XSS vector.
- **Fix**: Use `textContent` for the message text.

**S8. Base64 images embedded in onclick attributes** (`status_fragment.html:58,73`)
- `onclick="submitForTraining('{{ pred.id }}', '{{ pred.image_base64 }}', '{{ pred.model }}')"` — Full base64-encoded images (~50-200KB each) are embedded directly in HTML `onclick` attributes. With multiple ROIs, this can bloat the DOM by several megabytes.
- This also creates potential for attribute injection: while Jinja2's autoescaping handles HTML entities, embedding such large data in attributes is fragile and wasteful.
- **Fix**: Store image data in a `data-*` attribute or a JavaScript object, and reference by index in the onclick handler.

#### BUGS / CORRECTNESS

**B25. Warning/error status badge is always hidden** (`style.css:219-223`) ✅ FIXED
- The CSS defines:
  ```css
  .status.warning { background: rgba(217, 119, 6, 0.3); }
  .status.error, .status.warning, .status.none { display: none; }
  ```
- The `display: none` overrides the background. The status badge in `status_fragment.html:24` (`<span class="status {{ status }}">`) is only visible when status is `ok`. When there's an error or warning, the user sees no badge — the background styling on `.status.warning` is dead CSS.
- **Fix**: If intentional, remove the dead `.status.warning { background }` rule. If error/warning badges should show, remove them from the `display: none` rule.

**B26. Label progress bars reset on page refresh** (`label.html:358`)
- `initialCounts` starts at `{ digits: 0, arrows: 0, total: 0 }` and is set from the first API response. If the user refreshes the page, initial counts reset to the current remaining count, making progress bars restart from 0% even if most images were already labeled.
- **Fix**: Store initial counts in `sessionStorage`, or have the API return both total and remaining counts.

**B27. ROI canvas has no touch support** (`roi_config.html:298-431`)
- All canvas interactions use mouse events only: `mousemove`, `mousedown`, `mouseup`, `mouseleave`, `click`. On touch devices (tablets), the ROI drawing is completely non-functional. The HTML declares `<meta name="viewport" content="width=device-width">` suggesting mobile support is intended.
- **Fix**: Add `touchstart`, `touchmove`, `touchend` handlers, or use pointer events (`pointerdown`, `pointermove`, `pointerup`) for unified mouse+touch support.

**B28. Auto-refresh toggle doesn't properly cancel HTMX polling** (`dashboard.html:64-77`)
- When unchecking auto-refresh, `main.setAttribute('hx-trigger', 'load')` changes the attribute, then `htmx.process(main)` reprocesses it. However, an in-flight HTMX request is not cancelled, and the timing of `htmx.process()` on an already-initialized element may not reliably stop the polling timer. Users may see one more update after disabling.
- Minor issue — mostly works in practice.

#### CODE DUPLICATION

**D7. Inline `<style>` in training.html is 962 lines** (`training.html:8-963`)
- Nearly 1000 lines of CSS are embedded inline rather than in an external stylesheet. Meanwhile `style.css` handles dashboard and ROI config styling. The training page gets no benefit from browser caching of the CSS.
- **Fix**: Move to `static/training.css` or append to `static/style.css`.

**D8. Canvas crop-and-preview pattern repeated 3 times** (`roi_config.html`)
- `updateMarkerPreview` (line 647-683), `updateDigitPreviewFromOverlay` (line 890-937), `updateAnalogPreviewFromOverlay` (line 1288-1335) — all three contain identical logic: create temp canvas → draw rotated image → extract crop → set as img src. ~30 lines each.
- **Fix**: Extract a `getCroppedImage(x, y, w, h)` helper function.

**D9. Toast/message component reimplemented 4 times**
- `training.html:738-775`: `.message` with slide-in animation
- `label.html:181-217`: `.message` with identical slide-in
- `config_editor.html:130-166`: `.message-toast` with slide-up animation
- `dashboard.html`: `#status-message` with auto-hide
- Each has its own CSS, its own `showMessage()` function, and slightly different behavior (auto-hide timing, animation direction, positioning).
- **Fix**: Extract a shared toast component into `style.css` + a small JS helper.

**D10. Digit/analog UI code near-identical** (`roi_config.html`)
- The digit section (lines 796-1089) and analog section (lines 1191-1484) are structurally identical: `updateXBoxes()`, `selectXInPicture()`, `updateXInputs()`, `updateXFromInput()`, `updateXPreviewFromOverlay()`, `runXInference()`, `saveX()`, `restartX()`, `changeX()`, `showXEditMode()`, `showXSavedMode()`, `showXDisabledMode()`. Each pair differs only in the variable name (`digits`/`analogs`), API endpoint, and color scheme.
- ~290 lines duplicated with trivial differences.
- **Fix**: Parameterize a single `RoiSection` class/factory that takes the config name, API path, and colors.

#### ARCHITECTURE / DESIGN

**A14. CDN dependencies without local fallback** (`config_editor.html:226`, `dashboard.html:8`)
- Monaco Editor from `cdn.jsdelivr.net`, HTMX from `unpkg.com`. If CDN is unreachable (common in air-gapped or restricted LAN environments), the config editor is completely non-functional and the dashboard loses all dynamic behavior.
- **Fix**: Bundle locally or add a local fallback script tag.

**A15. Inconsistent polling approaches across pages**
- Dashboard: HTMX `hx-trigger="every 5s"` for status polling
- Training: Manual `setInterval` + `fetch` every 2 seconds
- Label: No polling (single-shot requests on user action)
- ROI Config: No polling (event-driven)
- Each page reinvents its own client-server communication pattern. Not necessarily wrong, but increases maintenance surface.

**A16. roi_config.html is a 1729-line monolith**
- The entire ROI configuration UI — rotation, markers, digits, analogs, canvas drawing, drag handling, API calls, DOM manipulation, state management — is a single inline `<script>` block. With 4 sections of near-identical code (D10), adding a 5th step type would require ~300 more lines of copy-paste.
- No module system, no separation of concerns, no state management pattern.

#### STYLE / MINOR

**M17. Mixed `lang` attributes across templates**
- `dashboard.html`: `lang="de"`, German UI text
- `label.html`: `lang="de"`, but mostly English content
- `config_editor.html`: `lang="en"`, English content
- `training.html`: `lang="de"`, but entirely English content
- `status_fragment.html`: German text
- **Fix**: Decide on one language. If German: translate training/label/config pages. If English: translate dashboard/status.

**M18. Unused CSS classes in style.css**
- `.section-divider` (line 368): defined but never used in any template.
- `.saved-digit-preview`, `.saved-digit-info`, `.saved-digit-label`, `.saved-digit-value` (lines 889-922): defined but unused — the saved-mode views use the `completed-step` pattern instead.
- Same for `.saved-analog-*` (lines 1183-1216).
- ~70 lines of dead CSS.

**M19. Broad CSS class names risk collisions**
- `style.css` defines `.error` (line 445), `.loading` (line 157), `.message` (in training.html/label.html). These generic names could collide if templates are ever composed or if a CSS reset is applied. Consider namespacing (e.g., `.wm-error`, `.wm-loading`) or using BEM.

**M20. No `<meta>` description or favicon** (all templates)
- All pages declare `<meta charset>` and `<meta viewport>` but no description, favicon, or other SEO/PWA tags. Minor for a LAN tool, but a favicon would help browser tab identification when multiple tabs are open.

**M21. HTMX version pinned to 1.x** (`dashboard.html:8`)
- `htmx.org@1.9.10` — HTMX 2.x has been available since 2024. The 1.x version still works but won't receive security patches. No urgency but worth a planned upgrade.

### Increment 6: Infrastructure & Cross-Cutting

**Files**: `Dockerfile` (50), `docker-compose.yml` (47), `docker-entrypoint.sh` (158), `requirements.txt` (49), `requirements-docker.txt` (27), `arrows/requirements.txt` (30), `config.yaml` (147), `.dockerignore` (51), `.gitignore` (25), `server_detect.py` (86)

#### SECURITY

**S9. Hardcoded server credentials in `server_detect.py`** (Low — commented out, dev tool) ✅ FIXED (file deleted, see M22)
- `server_detect.py:8-11`: Hardcodes `SERVER_IP = "192.168.4.35"`, `SERVER_USER = "thomas"`, and absolute paths. This file is committed to git.
- Currently commented out in `train_arrows.py:5-6` and `train_digits.py:4-5`, so no runtime risk. But it exposes internal network topology and usernames.
- **Fix**: Add to `.gitignore` or move credentials to an env file/`.env`.

**S10. Docker container runs as root** (`Dockerfile:2`)
- `USER root` is set and never switched back. The application runs as root inside the container, which is a well-known Docker anti-pattern. If the FastAPI app is compromised, the attacker has root in the container.
- **Fix**: Create a non-root user (`RUN useradd -m appuser`) and switch to it before `CMD`. Adjust directory permissions accordingly.

**S11. Config file contains hardcoded IP addresses** (`config.yaml`)
- `aiote.host: "192.168.5.136"`, `mqtt.broker: "192.168.4.11"` — internal network IPs are baked into the default config shipped with the Docker image. This is fine for personal use but would be problematic if the image were shared.
- Minor: more of a deployment concern than a code issue.

#### BUGS / CORRECTNESS

**B29. Healthcheck uses `curl` but `curl` is not installed** (`docker-compose.yml:42`, `Dockerfile`) ✅ FIXED
- The healthcheck command is `["CMD", "curl", "-f", "http://localhost:8001/health"]`, but the Dockerfile installs only `libxcb-*` libraries. The `openvino/ubuntu22_runtime` base image is a minimal runtime and likely doesn't include `curl`.
- If `curl` is missing, the healthcheck will always fail, causing Docker to report the container as "unhealthy" after `start_period + retries * interval`.
- **Fix**: Either install `curl` in the Dockerfile, or use a Python-based healthcheck: `["CMD", "python3", "-c", "import httpx; httpx.get('http://localhost:8001/health').raise_for_status()"]`.

**B30. `docker-compose.yml` version key is deprecated** (line 1) ✅ FIXED
- `version: '3.8'` — Docker Compose V2 (the current standard) ignores the `version` key entirely. It's harmless but generates deprecation warnings.
- **Fix**: Remove the `version` line.

**B31. `requirements.txt` lists `onnxruntime-gpu` — not used anywhere at runtime** (line 13)
- `onnxruntime-gpu>=1.16.0` is in `requirements.txt` but only `benchmark_digits.py` and `benchmark_arrows.py` (standalone scripts) import `onnxruntime`. The main app uses OpenVINO for inference. Installing `onnxruntime-gpu` pulls in CUDA dependencies (~500MB+), completely unnecessary for the CPU Docker container.
- `requirements-docker.txt` correctly omits it, but `requirements.txt` (used for local dev) still pulls it in.
- **Fix**: Remove from `requirements.txt` or replace with `onnxruntime` (CPU-only, much lighter).

**B32. `requirements.txt` lists packages unused by the application**
- `Pillow>=10.0.0`: Never imported in any `.py` file (OpenCV is used instead).
- `scipy>=1.10.0`: Never imported in any `.py` file.
- `onnx>=1.15.0`: Only imported in `benchmark_digits.py:38` (standalone script), not needed for the web app.
- These inflate install time and virtual environment size.
- **Fix**: Remove unused packages, or split into `requirements-dev.txt` for standalone tools.

**B33. `requirements-docker.txt` missing `onnxscript` dependency context**
- `onnxscript>=0.1.0` is listed in the Docker requirements. It's only needed for ONNX export during training (`torch.onnx.export`). This is correct for the Docker image (which does training), but the comment "Training dependencies" only mentions `timm`, `scikit-learn`, `matplotlib`, `numpy`.
- Minor: the comment could be more complete.

**B34. `openvino` not in `requirements-docker.txt`**
- OpenVINO is the core inference engine. It's not listed in `requirements-docker.txt` because it comes pre-installed in the `openvino/ubuntu22_runtime` base image. However, there's no comment explaining this, and `training_manager.py:430` does `import openvino as ov` (lazy import in training execution).
- If the base image is ever changed, OpenVINO would silently be missing.
- **Fix**: Add a comment in `requirements-docker.txt`: `# openvino: provided by base image (openvino/ubuntu22_runtime)`.

**B35. `config/config.yaml` and `config.yaml` have diverged**
- The repo root `config.yaml` (shipped as default in Docker) differs from `config/config.yaml` (live runtime config):
  - `config/config.yaml` has `arrows_model` pointing to flat path (no model directory wrapper), while root `config.yaml` uses the new `/app/models/{type}/{id}/` structure.
  - `config/config.yaml` is missing the `images.src` key.
  - `config/config.yaml` uses `homeassistant.enabled: true`, root uses `false`.
  - Digit ROI sizes differ between the two files.
  - `config/config.yaml` uses `mqtt.client_id: watermeter-ai-service`, root uses `watermeter-ai-service-debug`.
- The `config/` directory is git-ignored (`.gitignore:19-20`), so this divergence won't cause issues in Docker. But it makes local development confusing: which config is "correct"?
- **Fix**: Document that `config.yaml` (root) is the template/default, and `config/config.yaml` is runtime state (never committed).

#### CODE DUPLICATION

**D11. Entrypoint digits/arrows model init is copy-paste** (`docker-entrypoint.sh:72-148`)
- The digits model initialization (lines 72-109) and arrows model initialization (lines 111-148) are structurally identical: check if empty → copy files → parse name → create `metadata.json` → update config via `sed`. ~37 lines duplicated with only variable names changed.
- **Fix**: Extract an `init_model_type()` function parameterized by type name and defaults.

**D12. Three separate `requirements*.txt` files with overlapping dependencies**
- `requirements.txt`: 17 packages (local dev, includes GPU/CUDA deps)
- `requirements-docker.txt`: 12 packages (Docker, CPU-only)
- `arrows/requirements.txt`: 11 packages (standalone arrow training)
- Shared packages (`timm`, `scikit-learn`, `matplotlib`, `opencv-python-headless`, `numpy`, `openvino`) are repeated across files with potentially different version constraints.
- **Fix**: Use a layered approach: `requirements-base.txt` (shared) + `-r requirements-base.txt` in others. Or document the intentional separation.

#### ARCHITECTURE / DESIGN

**A17. Base image tag `:latest` is non-reproducible** (`Dockerfile:1`)
- `FROM openvino/ubuntu22_runtime:latest` — the `latest` tag can change at any time. A rebuild weeks later could pull a different OpenVINO version, potentially introducing incompatibilities.
- **Fix**: Pin to a specific version tag, e.g., `openvino/ubuntu22_runtime:2024.6.0`.

**A18. Docker layer ordering wastes cache on code changes** (`Dockerfile:14-32`)
- The Dockerfile installs all Python dependencies (lines 15-19) before copying application code (lines 30-32). This is good.
- However, `COPY requirements-docker.txt` is after the `torch` install. If `requirements-docker.txt` changes, the `torch` layer still caches. This is actually well-structured.
- The one inefficiency: model files (`digits/selected/`, `arrows/selected/` — 89MB total) are copied after application code (line 35-36). Since model files change rarely, they could be copied earlier for better caching. Minor optimization.

**A19. `LOG_LEVEL` environment variable is ignored** (`docker-compose.yml:34`)
- `LOG_LEVEL=INFO` is set as an environment variable, but no Python code reads `os.environ` or `os.getenv` anywhere. The logging level is read from `config.yaml` (via `config_utils.py`). The env var is dead configuration.
- **Fix**: Either remove the env var from `docker-compose.yml`, or add code to use it as an override: `level = os.getenv('LOG_LEVEL', config.get('logging', {}).get('level', 'INFO'))`.

**A20. `HF_TOKEN` environment variable is passed but never used by Python code**
- `docker-compose.yml:37` passes `HF_TOKEN`, and `docker-entrypoint.sh:55-59` checks for its presence. But no Python code reads it. The `timm` library / HuggingFace Hub client reads `HF_TOKEN` from the environment automatically, so this actually works — but only by convention, not explicit wiring.
- Minor: Add a comment in the compose file noting that `timm`/`huggingface_hub` reads this implicitly.

**A21. No `.env` file or `.env.example` for required configuration**
- `docker-compose.yml` references `${RENDER_GROUP:-44}` and `${HF_TOKEN:-}`. These expect environment variables, but there's no `.env.example` documenting what's available/required.
- **Fix**: Create a `.env.example` with documented defaults.

**A22. Train/benchmark scripts not in Docker but referenced by `training_manager.py`**
- The Dockerfile only copies 7 specific `.py` files (line 30). `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py`, and `server_detect.py` are not copied into the image.
- However, `training_manager.py` doesn't use these files — it reimplements training and benchmarking inline. The standalone scripts are development tools.
- This is correct behavior, but the presence of both standalone scripts AND inline implementations (D5/D6 from Increment 4) creates maintenance divergence: a fix to training logic needs to be applied in two places.

**A23. `/home/thomas/.cache/huggingface` hardcoded in compose** (`docker-compose.yml:22`)
- `- /home/thomas/.cache/huggingface:/root/.cache/huggingface` — The host path is hardcoded to a specific user's home directory. Anyone else using this compose file would need to edit it.
- **Fix**: Use `- ${HF_CACHE:-~/.cache/huggingface}:/root/.cache/huggingface` or document in `.env.example`.

#### STYLE / MINOR

**M22. `server_detect.py` is a dead file** ✅ FIXED (deleted)
- Imported nowhere (commented out in `train_arrows.py:5` and `train_digits.py:4`). Not copied into Docker image. Contains hardcoded IPs and paths specific to one development setup.
- **Fix**: Delete or move to a `tools/` directory.

**M23. `.dockerignore` excludes `*.md` but includes `benchmark.py`**
- `*.md` on line 46 excludes all markdown (including potential important files). `benchmark.py` on line 49 is excluded but no file by that name exists (the actual files are `benchmark_digits.py` and `benchmark_arrows.py`).
- Also missing from `.dockerignore`: `server_detect.py`, `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py`, `models_debug/`, `config_debug/`, `.env`. Currently these are not copied (the Dockerfile uses explicit `COPY` for specific files), so this is a defense-in-depth concern, not a bug.

**M24. `docker-compose.yml` uses bind mounts for everything**
- All 5 volumes are host bind mounts (`./config:/config`, `./data:/data`, etc.). This works for single-machine deployment but makes the setup non-portable. Named volumes would be more Docker-idiomatic for `data` and `models`.
- Minor: acceptable for a personal/home automation project.

**M25. Floating point artifacts in config** (`config.yaml:38,42,63`) ✅ FIXED
- `x: 0.41000000000000003`, `x: 0.23500000000000001`, `y: 0.5750000000000001` — These are floating point representation artifacts from Python, saved to YAML. Functionally harmless but visually distracting.
- **Fix**: Round to reasonable precision (e.g., 4 decimal places) when saving ROI coordinates.

---

## Summary & Recommendations

### By the Numbers

| Category | Count | Fixed | Remaining |
|----------|-------|-------|-----------|
| Security | 11 | 6 (S1,S4,S5,S6,S7,S9) | 5 |
| Bugs / Correctness | 35 | 14 (B1,B2,B3,B6,B9,B12,B13,B17,B18,B19,B20,B22,B24,B25) | 21 |
| Code Duplication | 12 | 1 (D3) | 11 |
| Architecture / Design | 23 | 1 (A10) | 22 |
| Style / Minor | 25 | 3 (M1,M22,M25) | 22 |
| **Total** | **106** | **25** | **81** |

> All 6 Critical Path items and all 16 Quick Wins are done. Remaining items are refactoring waves and lower-priority findings.

### Critical Path — Fix These First

These have the highest impact-to-effort ratio and address real correctness or security risks.

#### 1. ✅ Path Traversal (S1, S4) — **DONE**
Both `app.py` training/label endpoints and `ModelManager` accept user-controlled strings used directly in filesystem paths. A single validation helper resolves both:
```python
def safe_subpath(base: Path, untrusted: str) -> Path:
    resolved = (base / untrusted).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("Invalid path")
    return resolved
```
Apply to: `submit_for_training`, `submit_label`, `delete_image`, and all `ModelManager` methods that take `model_id`.

#### 2. ✅ Hot-Reload is Broken (B12) — **DONE**
Activating a new model via the UI has no effect on inference because `app.py` and `watermeter_service.py` hold stale references from import time. Fix by removing the module-level aliases in `inference.py` and having callers go through `get_inference_service()` each time.

#### 3. ✅ `sys.exit(1)` in Model Validation (B13) — **DONE**
`validate_model_config` kills the entire process on a filename mismatch. Replace with `raise ValueError(...)`. This makes model activation safe.

#### 4. ✅ Config Comments Destroyed on Save (B3, A10) — **DONE**
Every ROI save/delete endpoint and `model_manager.activate_model` use `yaml.safe_load` + `yaml.dump`, stripping user comments. Route all config writes through `config_utils` which already uses `ruamel.yaml`.

#### 5. ✅ MQTT Thread Safety (B6) — **DONE**
`on_mqtt_message` directly mutates shared state from the MQTT thread. Route through `asyncio.run_coroutine_threadsafe` like the trigger handler already does.

#### 6. ✅ XSS via innerHTML (S6, S7) — **DONE**
Replace `innerHTML` with `textContent` in `training.html` (log viewer, config display) and `dashboard.html` (status messages). ~10 lines changed across 2 files.

### Recommended Refactoring Waves

Grouped by effort and dependency. Each wave can be done independently.

#### Wave A: Config Write Consolidation (B3, B4, A10, D3) — mostly done
- ✅ Create a single `update_config(key_path, value)` function in `config_utils.py` using `ruamel.yaml`
- ✅ Replace all direct yaml.safe_load/dump patterns in app.py's 9 ROI endpoints and model_manager.py
- ⬜ Add an `asyncio.Lock` to prevent concurrent config writes (B4 still open)
- **Estimated scope**: ~200 lines changed across 3 files

#### Wave B: Training/Benchmark Deduplication (D4, D5, D6)
**Keep `training_manager.py` as authoritative**, delete standalone scripts (less code, but loses CLI training capability)

- Extract shared utilities (`create_data_loaders`, `compute_class_weights`, `analyze_dataset`, `generate_classes`, `round_to_class`) into `training_utils.py`
- Unify digits/arrows training into a single parameterized function
- Same for benchmark
- **Estimated scope**: ~1,500 lines removed, ~300 lines new shared module

#### Wave C: Frontend Cleanup (D7, D8, D9, D10, S8)
Create `static/training.css` (separate concern, parallel loading)


- Move inline `<style>` from `training.html` to external stylesheet
- Extract shared toast/message component (used in 4 templates)
- Parameterize digit/analog sections in `roi_config.html` (D10)
- Move base64 image data out of onclick attributes into JS data structures (S8)
- **Estimated scope**: ~600 lines removed, ~150 lines new shared code

#### Wave D: Docker & Infrastructure (B29, S10, A17, A19, D11) — partially done
- ⬜ Pin base image version (e.g., `openvino/ubuntu22_runtime:2024.6.0`) (A17)
- ✅ Switch to Python-based healthcheck (B29)
- ⬜ Add non-root user (S10)
- ⬜ Extract entrypoint model-init into a function (D11)
- ⬜ Remove dead `LOG_LEVEL` env var (or wire it up) (A19)
- ⬜ Create `.env.example` (A21)
- ✅ Remove deprecated `version` key (B30)
- ✅ Delete `server_detect.py` (M22, S5, S9)
- **Estimated scope**: ~50 lines changed across 3 files

### Items to Defer or Ignore

These are real findings but low-priority for a personal/home-automation project:

- **S3** (no auth) — acceptable for LAN-only deployment
- **A14** (CDN dependencies) — only matters in air-gapped environments
- **A15** (inconsistent polling) — each page's approach works; unifying adds complexity without user benefit
- **M17** (mixed languages) — cosmetic, unless the project will be shared publicly
- **M24** (bind mounts) — correct for single-host Docker deployment
- **B27** (no touch support in ROI canvas) — only relevant if tablet use is planned

### Standalone Bug Fixes (Quick Wins)

These are independent one-line to five-line fixes that can be done in any order:

| ID | File | Fix | Status |
|----|------|-----|--------|
| B1 | `app.py:625` | Count markers from config instead of `range(1,3)` | ✅ |
| B2 | `app.py:125,191` | Store `asyncio.create_task` references | ✅ |
| B9 | `persistence.py:29` | Write-then-rename for atomic saves | ✅ |
| B13 | `inference.py:227,240` | Replace `sys.exit(1)` with `raise ValueError` | ✅ |
| B17 | `inference.py:30` | Check `cv2.imread` return value | ✅ |
| B18 | `benchmark_digits.py:149` | Change `'N'` to `'NAN'` | ✅ |
| B19 | `benchmark_arrows.py:22` | Move `return False` below docstring | ✅ |
| B20 | `train_digits.py:397`, `train_arrows.py:498` | Add `result = result - result.max()` before softmax | ✅ |
| B22 | `training_manager.py:1003` | Set `CANCELLED` instead of `FAILED` | ✅ |
| B24 | `training_manager.py:386` | Replace recursion with loop | ✅ |
| B25 | `style.css:219-223` | Fix `.status.error/.warning` display rule | ✅ |
| B29 | `Dockerfile` or `docker-compose.yml` | Install curl or use Python healthcheck | ✅ |
| B30 | `docker-compose.yml:1` | Remove `version: '3.8'` | ✅ |
| M1 | `app.py:14` | Remove unused `import numpy` | ✅ |
| M22 | `server_detect.py` | Delete file | ✅ |
| M25 | `config_utils.py` (ROI save) | Round floats to 4 decimal places | ✅ |

> **TODO: Decision needed** — Label Studio integration (`inference.py:249-378`, finding A8):
Decision: **Remove** if all labeling is done through the built-in `/label` page.

> **TODO: Decision needed** — Marker alignment (`watermeter_service.py:217-250`, finding B5):
> - `_align_with_markers` is a stub that returns the image unchanged. Users can configure markers via the UI, but they have no effect.
Decision: - **Implement** perspective correction using the marker coordinates.
