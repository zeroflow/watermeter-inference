# Structural Review - 2026-02-13

## 1. Folder Structure

### Current Layout
```
watermeter/
  app.py                  (1796 lines) - FastAPI app, all routes
  watermeter_service.py    (969 lines) - Core inference service
  training_manager.py     (1165 lines) - Training + benchmark execution
  model_manager.py         (322 lines) - Model metadata/files
  inference.py             (373 lines) - OpenVINO inference wrapper
  config_utils.py          (409 lines) - YAML config read/write
  persistence.py            (73 lines) - State persistence

  train_digits.py          (499 lines) - Standalone digits training script
  train_arrows.py          (635 lines) - Standalone arrows training script
  benchmark_digits.py      (427 lines) - Standalone digits benchmark
  benchmark_arrows.py      (446 lines) - Standalone arrows benchmark

  templates/                           - Jinja2 HTML templates
    dashboard.html          (6 KB)
    training.html          (83 KB!)     <<< very large
    label.html             (20 KB)
    roi_config.html        (78 KB!)     <<< very large
    config_editor.html     (12 KB)
    status_fragment.html    (4 KB)
  static/
    style.css              (20 KB)

  config.yaml                          - Main config (checked in with real IPs!)
  config/config.yaml                   - Docker volume mount target (gitignored)
  config_debug/config.yaml             - Debug config

  digits/
    dataset/               (1.6 MB)    - Original training data (10 classes)
    ov_model/              (1.8 GB!)   - 27 trained OpenVINO models
    ground_truth/                      - Git submodule (watermeter-digits)
    ground_truth_orig/                 - Backup of ground truth
    input/                             - 45 unlabeled images
    selected/                          - Currently selected model (2 files)
    Digits.ipynb                       - Jupyter notebook (original training)
  arrows/
    dataset/               (51 MB)     - Original training data (10 classes)
    ov_model/              (3.4 GB!)   - 14 trained OpenVINO models
    ground_truth/                      - Git submodule (watermeter-arrows)
    ground_truth_orig/                 - Backup of ground truth
    input/                             - 294 unlabeled images
    selected/                          - Currently selected model (2 files)
    Arrows.ipynb                       - Jupyter notebook (original training)

  data/                                - Runtime state (gitignored content)
  data_debug/                          - Debug runtime state
  models_debug/            (436 MB)    - Debug model storage

  tests/
    conftest.py
    unit/                  (5 test files)
    regression/            (3 test files)

  Dockerfile
  docker-compose.yml
  docker-entrypoint.sh     (159 lines)
  .dockerignore
  .gitignore
  .gitmodules                          - ground_truth submodules
  .env                                 - HF_TOKEN (gitignored)
  requirements.txt                     - Local dev dependencies
  requirements-docker.txt              - Docker dependencies (subset)

  run.sh                               - Quick docker run (no volumes)
  debug.sh                             - Full debug: build + run with volumes
  setup.sh                             - Create venv + install deps
  send_models.sh                       - rsync models to remote host

  flows.json               (29 KB)     - Node-RED flow export?
  README.md, QUICKSTART.md, DOCKER.md, Progress.md
  Integrated_training.md               - Training feature spec
  plan_failure_tracking.md
  review_0210_python.md                - Previous Python review
  venv/                                - Local virtual environment
```

### Findings

**F1.1 - Massive ov_model directories (5.2 GB total)**
The `digits/ov_model/` (27 models, 1.8 GB) and `arrows/ov_model/` (14 models, 3.4 GB) directories contain every model ever trained locally. Only 1 model per type is actually used (in `selected/`). These are gitignored but bloat the working directory and are a source of confusion.
- **Suggestion**: Archive old models. Keep only `selected/` in the working tree. Use a naming convention or symlink for the "current" model.
- **Decision:**: Keep, those are for benchmarks

**F1.2 - Duplicated directory purposes**
There are parallel directories serving similar purposes:
- `config/` vs `config_debug/` vs root `config.yaml`
- `data/` vs `data_debug/`
- `models_debug/` (for debug container) vs `digits/ov_model/` + `arrows/ov_model/` (local training output)
This makes it hard to understand which config/data is actually used when.
- **Suggestion**: Document the purpose of each or consolidate. The root `config.yaml` is the template; `config/` and `config_debug/` are volume-mount targets.
- **Decision:**: it's ok, this is for debugging + local instance issues.

**F1.3 - ground_truth_orig directories (untracked)**
Both `arrows/ground_truth_orig/` and `digits/ground_truth_orig/` appear in `git status` as untracked. These seem to be manual backups of the git submodule content.
- **Suggestion**: If these are backups, they shouldn't live in the project tree. If they're intentional, add them to .gitignore or track them.
- **Decision:**: Old files, keep at the memoent

**F1.4 - Config file checked in with real infrastructure IPs**
`config.yaml` in the repo root contains real IP addresses (192.168.5.136 for the meter, 192.168.4.11 for MQTT broker). This is fine for a private repo but would be a problem if ever shared.
- **Suggestion**: Acceptable for private repo. Note: the `.env` file IS properly gitignored.
- **Decision:**: remove for checked in version. Rev 1 will be squashed anyways.

**F1.5 - Standalone scripts duplicate integrated logic**
`train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py` (2007 lines total) exist alongside `training_manager.py` (1165 lines) which implements the same functionality for the web UI.
- **Suggestion**: These were the original standalone scripts. Now that training_manager.py exists, consider if these are still needed. If kept for CLI usage, they should ideally share code with training_manager.py rather than being independent implementations.
- **Decision:**: Share code, refactor

**F1.6 - flows.json (Node-RED)**
A 29 KB Node-RED flow export sits in the project root. This seems to belong to a different layer of the automation stack.
- **Suggestion**: Move to a separate repo or a `nodered/` subfolder, or document its purpose.
- **Decision:**: These were the old files. Remove

**F1.7 - Giant template files**
`training.html` (83 KB) and `roi_config.html` (78 KB) are enormous single files. These likely contain significant inline JavaScript.
- **Suggestion**: Consider extracting JavaScript into separate `.js` files in `static/`. This improves cacheability, testability, and readability.
- **Decision:**: Extract.

**F1.8 - No src/ package structure**
All Python modules are flat in the root directory. This works fine for the current scale (7 modules), but there's no `__init__.py` or package structure.
- **Suggestion**: Acceptable at current scale. If more modules are added, consider a `watermeter/` package.
- **Decision:**: Change and create watermeter package
---

## 2. Dependencies & Environment

### requirements.txt (local dev)
```
torch>=2.0.0, torchvision>=0.15.0    - PyTorch (could pull GPU version)
timm>=0.9.0                           - Model zoo
onnx>=1.15.0, onnxscript>=0.1.0      - ONNX export
onnxruntime-gpu>=1.16.0               - ONNX runtime (GPU!)
openvino>=2024.0.0                    - OpenVINO inference
opencv-python-headless>=4.8.0         - CV
numpy, scipy, scikit-learn, matplotlib, Pillow
fastapi, uvicorn[standard], jinja2, python-multipart
paho-mqtt, httpx
pyyaml, ruamel.yaml
```

### requirements-docker.txt (Docker)
```
fastapi, uvicorn, jinja2, python-multipart
opencv-python-headless
paho-mqtt, httpx
pyyaml, ruamel.yaml
timm, scikit-learn, matplotlib, numpy, onnxscript
```
Note: torch/torchvision are NOT in this file (installed separately as CPU-only in Dockerfile).

### Findings

**F2.1 - No version pinning**
Both requirements files use `>=` minimum versions only. This means builds are not reproducible - a future `pip install` might pull breaking changes.
- **Suggestion**: Pin exact versions (or use a lockfile). At minimum, pin major versions: `torch>=2.0.0,<3.0.0`.
- **Decision:**: Pin versions

**F2.2 - Local requirements.txt includes onnxruntime-gpu**
The local requirements file pulls `onnxruntime-gpu` which depends on CUDA. This is likely unnecessary since inference runs through OpenVINO, not ONNX Runtime.
- **Suggestion**: Verify if onnxruntime-gpu is actually used anywhere. If only used for ONNX export validation, `onnxruntime` (CPU) would suffice.

**F2.3 - Docker requirements missing openvino**
`requirements-docker.txt` doesn't list `openvino` - it comes from the base image (`openvino/ubuntu22_runtime:latest`). This is correct but implicit. If the base image version changes, the openvino version changes silently.
- **Suggestion**: Document this dependency. Consider pinning the base image tag.

**F2.4 - No dev/test dependencies file**
`pytest` and any other test dependencies aren't listed anywhere.
- **Suggestion**: Add `requirements-dev.txt` with pytest, httpx (for TestClient), etc.
- **Decision:**: Add requirements-dev.txt

**F2.5 - onnx + onnxscript only needed for export**
These are only used during the training->export step, not at runtime. Docker includes them via the base requirements but they add image size.
- **Suggestion**: Low priority. Could separate into training-only deps.
- **Decision:**: The docker has a training manager -> needed at runtime

---

## 3. Dockerfile & Container Setup

### Dockerfile
- Base: `openvino/ubuntu22_runtime:latest` (unpinned!)
- Installs X11/XCB libs (for OpenCV?), then CPU-only PyTorch, then requirements-docker.txt
- Copies 7 Python files individually, templates/, static/, selected models, default config
- Entrypoint: docker-entrypoint.sh, CMD: uvicorn

### docker-compose.yml
- 5 volume mounts: config, data, models, training, huggingface cache
- Intel GPU passthrough (renderD128)
- Healthcheck using httpx
- Hardcoded HuggingFace cache path: `/home/thomas/.cache/huggingface`

### docker-entrypoint.sh (159 lines)
- Parses model filenames to extract metadata (architecture, resolution, classes, seed)
- Copies default config if not present
- Copies default models if models dir is empty, creates metadata.json
- Updates config.yaml with model paths via sed
- Symlinks config

### Findings

**F3.1 - Unpinned base image**
`FROM openvino/ubuntu22_runtime:latest` means every build could use a different OpenVINO version.
- **Suggestion**: Pin to a specific tag, e.g., `openvino/ubuntu22_runtime:2024.6.0`.
- **Decision:**: Pin

**F3.2 - Runs as root**
The Dockerfile sets `USER root` and never drops privileges. The app runs as root inside the container.
- **Suggestion**: Create a non-root user. This is security best practice, especially since the app handles network requests.
- **Decision:**: Change to non-root user

**F3.3 - Hardcoded host paths in docker-compose.yml**
`/home/thomas/.cache/huggingface:/root/.cache/huggingface` is machine-specific.
- **Suggestion**: Use an environment variable: `${HF_CACHE:-~/.cache/huggingface}:/root/.cache/huggingface`

**F3.4 - Smart entrypoint but complex**
The 159-line entrypoint.sh does a lot: filename parsing with regex, model copying, config patching with sed. This is fragile - if model naming conventions change, the regex breaks.
- **Suggestion**: Consider moving model initialization logic into the Python app (startup event). Python is better suited for JSON generation and config manipulation than bash+sed.
- **Decision:**: Keep in bash but simplify. Just copy selected models if model folder is empty. No regexes.

**F3.5 - COPY lists individual Python files**
```dockerfile
COPY app.py watermeter_service.py persistence.py inference.py config_utils.py model_manager.py training_manager.py /app/
```
If a new module is added, Dockerfile must be manually updated.
- **Suggestion**: Use `COPY *.py /app/` or maintain a `src/` directory.
- **Decision:**: Resolved by F1.8 — package structure means `COPY watermeter/ /app/watermeter/`

**F3.6 - docker-compose healthcheck uses httpx inside container**
The healthcheck runs `python3 -c "import httpx; ..."` which is clever but slower than a simple `curl` (Python startup overhead on each check).
- **Suggestion**: Minor. Could use `curl -f http://localhost:8001/health` if curl is installed, or add a lightweight `/health` check.

**F3.7 - run.sh uses different image name than debug.sh**
`run.sh` uses image `wmi`, `debug.sh` builds and tags as `wmi_full`. Confusing.
- **Suggestion**: Standardize image naming.

**F3.8 - debug.sh maps different port**
`debug.sh` maps `-p 8002:8001` while `docker-compose.yml` maps `8001:8001`. This is intentional (run both simultaneously) but not documented.
- **Decision:**: Document.

---

## 4. Python App Organization

### Module Responsibilities
| Module | Lines | Role |
|--------|-------|------|
| app.py | 1796 | FastAPI routes, all HTTP endpoints, template rendering |
| watermeter_service.py | 969 | Core service: image fetch, inference orchestration, MQTT, HA |
| training_manager.py | 1165 | Training job execution, benchmark execution, progress tracking |
| model_manager.py | 322 | Model CRUD, metadata, activation |
| inference.py | 373 | OpenVINO model loading and inference |
| config_utils.py | 409 | YAML config reading/writing (comment-preserving) |
| persistence.py | 73 | JSON state file read/write |

### Findings

**F4.1 - app.py is a monolith (1796 lines)**
All 35+ routes live in a single file. No use of FastAPI's `APIRouter` to organize endpoints by domain.
- **Suggestion**: Split into routers: `routes/training.py`, `routes/models.py`, `routes/dashboard.py`, etc.
- **Decision:**: Split into APIRouters

**F4.2 - No clear layering**
`app.py` directly accesses `training_manager`, `model_manager`, `config_utils`, and `watermeter_service`. There's no service layer between the HTTP handlers and the business logic.
- **Suggestion**: Acceptable at current scale. The manager classes already provide some separation. If app.py grows further, introduce proper routers.

**F4.3 - Global state**
The app likely uses module-level global instances (TrainingManager, ModelManager, WatermeterService). This is typical for single-process FastAPI apps but makes testing harder.
- **Suggestion**: Consider FastAPI dependency injection for testability.

---

## 5. Configuration Management

### config.yaml Structure
```yaml
aiote:          # Water meter device connection
images:         # Image source + ROI IDs
detection:      # ROI coordinates (analogs, digits, markers, rotation)
mqtt:           # Broker settings
homeassistant:  # HA integration
inference:      # Model paths, classes, resolution, threshold, device
plausibility:   # Rate limiting, consistency checks
low_confidence: # Warning/save settings
persistence:    # State file path
dashboard:      # Host, port, refresh interval
logging:        # Level, format, file
```

### Findings

**F5.1 - Model paths in config are absolute Docker paths**
`digits_model: "/app/models/digits/..."` - these paths only make sense inside the container. For local development, different paths would be needed.
- **Suggestion**: This is handled by the debug.sh volume mounts. Acceptable, but could benefit from an environment variable override.

**F5.2 - Two YAML libraries**
Both `pyyaml` and `ruamel.yaml` are dependencies. `ruamel.yaml` is used for comment-preserving writes in `config_utils.py`.
- **Suggestion**: Acceptable. ruamel.yaml preserves comments on round-trip editing, which pyyaml cannot do.

**F5.3 - Classes defined in config, not derived from model**
`digits_classes` and `arrows_classes` are hardcoded in config.yaml. If a model is trained with different classes, the config must be manually updated.
- **Suggestion**: Consider storing classes in model metadata.json and reading from there when a model is activated.
- **Decision:**: Yes, read classes from model metadata

---

## 6. API Endpoint Design

(Deferred to code review - requires reading app.py routes in detail)

---

## 7. Frontend / Templates

### File Sizes
| Template | Size |
|----------|------|
| training.html | 83 KB |
| roi_config.html | 78 KB |
| label.html | 20 KB |
| config_editor.html | 12 KB |
| dashboard.html | 6 KB |
| status_fragment.html | 4 KB |
| **style.css** | **20 KB** |

### Findings

**F7.1 - Inline JavaScript in templates**
`training.html` (83 KB) and `roi_config.html` (78 KB) likely contain thousands of lines of inline JavaScript. This is the #1 structural issue in the frontend.
- **Suggestion**: Extract JS into `static/training.js`, `static/roi_config.js`, etc. Benefits: browser caching, linting, potential for bundling.

**F7.2 - Single CSS file**
`style.css` at 20 KB is manageable. Uses CSS variables for theming.
- **Suggestion**: Fine as-is.

**F7.3 - No JS dependencies or build step**
No npm, no bundler. Pure vanilla JS + HTMX (loaded from CDN presumably).
- **Suggestion**: This is actually a strength - zero build complexity. Keep it.

---

## 8. Training & Benchmark Scripts

### Standalone Scripts
| Script | Lines | Purpose |
|--------|-------|---------|
| train_digits.py | 499 | CLI digits training |
| train_arrows.py | 635 | CLI arrows training |
| benchmark_digits.py | 427 | CLI digits benchmark |
| benchmark_arrows.py | 446 | CLI arrows benchmark |

### Integrated (training_manager.py)
| Method | Purpose |
|--------|---------|
| _execute_training() | Training via web UI |
| _execute_benchmark() | Benchmark via web UI |

### Findings

**F8.1 - Code duplication between standalone and integrated**
The standalone scripts and training_manager.py implement the same training/benchmark logic independently. Changes to one don't propagate to the other.
- **Suggestion**: Extract shared training logic into a `training_core.py` module. Both standalone scripts and training_manager.py should call into it.

**F8.2 - Arrows training has more features than digits**
`train_arrows.py` (635 lines) is 27% larger than `train_digits.py` (499 lines), likely because arrows has dataset subsampling (`step_size`) and 100-class handling.
- **Suggestion**: A unified training script parameterized by type would reduce duplication.

---

## 9. Model Lifecycle & Data Flow

### Flow: Image -> Reading
```
MQTT trigger -> WatermeterService.process()
  -> fetch image from AI-on-the-edge device
  -> extract ROIs (using detection config)
  -> inference.py: classify each ROI
  -> assemble reading (digits + arrows)
  -> plausibility checks
  -> publish via MQTT to Home Assistant
  -> save state via persistence.py
```

### Flow: Training
```
Web UI -> POST /api/training/start
  -> TrainingManager._execute_training()
    -> load ground_truth images
    -> create PyTorch dataset + timm model
    -> train N epochs with progress callbacks
    -> export to ONNX -> OpenVINO
    -> save to /app/models/{type}/{model_id}/
    -> create metadata.json
```

### Flow: Model Activation
```
Web UI -> POST /api/models/{type}/{id}/activate
  -> ModelManager updates status in metadata.json
  -> Updates config.yaml with new model path
  -> WatermeterService reloads models
```

### Findings

**F9.1 - Model storage: local ov_model/ vs Docker /app/models/**
Locally trained models go to `{type}/ov_model/`. Docker-trained models go to `/app/models/{type}/{model_id}/`. The `selected/` directory bridges them for Docker builds.
- This dual storage is the most confusing part of the project structure.

**F9.2 - Ground truth as git submodules**
Ground truth data is stored in separate git repos, linked via .gitmodules. This keeps the main repo lean but adds setup complexity.
- **Suggestion**: Acceptable approach for binary data. Just needs clear setup docs.

---

## 10. Testing

### Structure
```
tests/
  conftest.py              - Root fixtures
  unit/
    conftest.py            - Unit test fixtures
    test_api_routes.py     - API endpoint tests
    test_config_utils.py   - Config utility tests
    test_model_manager.py  - Model manager tests
    test_persistence.py    - State persistence tests
    test_pure_functions.py - Pure function tests
  regression/
    test_config_comments.py  - YAML comment preservation
    test_nan_class_label.py  - NAN class handling
    test_roi_rounding.py     - ROI coordinate rounding
```

### Findings

**F10.1 - Good test structure**
Separation of unit and regression tests is good. Regression tests target specific bugs that were fixed.
- **Suggestion**: Continue this pattern. Each bug fix should get a regression test.

**F10.2 - No test dependencies file**
pytest is not in any requirements file.
- **Suggestion**: Add `requirements-dev.txt`.
- **Decision:**: Add (same as F2.4)

**F10.3 - No integration tests**
No tests that spin up the full app and test end-to-end flows.
- **Suggestion**: Add a basic integration test using FastAPI's TestClient.

---

## Priority Summary

### Decisions — Action Items (ordered by implementation sequence)

| Step | Finding | Decision | Status |
|------|---------|----------|--------|
| 1 | **F1.6** | Remove flows.json | TODO |
| 2 | **F1.4** | Sanitize config.yaml (remove real IPs) | TODO |
| 3 | **F2.4/F10.2** | Add requirements-dev.txt | TODO |
| 4 | **F2.1** | Pin dependency versions | TODO |
| 5 | **F1.8** | Create watermeter/ package structure | TODO |
| 6 | **F4.1** | Split app.py into APIRouters | TODO |
| 7 | **F1.5** | Refactor standalone scripts to share code with training_manager | TODO |
| 8 | **F7.1/F1.7** | Extract inline JS from templates | TODO |
| 9 | **F3.1** | Pin Docker base image | TODO |
| 10 | **F3.2** | Non-root container user | TODO |
| 11 | **F3.4** | Simplify entrypoint.sh (copy selected if empty, no regex) | TODO |
| 12 | **F3.5** | Update Dockerfile COPY for package structure | TODO |
| 13 | **F5.3** | Read classes from model metadata instead of config | TODO |
| 14 | **F3.8** | Document debug.sh port difference | TODO |

### Decisions — No Action (accepted as-is)

| Finding | Decision |
|---------|----------|
| **F1.1** | Keep ov_model dirs — needed for benchmarks |
| **F1.2** | Keep duplicated dirs — debugging/local instance |
| **F1.3** | Keep ground_truth_orig for now |
| **F2.5** | Keep onnx/onnxscript — training manager needs them |
| **F5.2** | Two YAML libs acceptable |
| **F7.2** | Single CSS file fine |
| **F7.3** | No JS build step is a strength |
| **F9.2** | Git submodules acceptable |
| **F10.1** | Good test structure, continue pattern |

### Undecided (no explicit decision yet)

| Finding | Suggestion |
|---------|------------|
| **F2.2** | onnxruntime-gpu → CPU-only? |
| **F2.3** | Document implicit openvino dep |
| **F3.3** | Hardcoded host paths in compose |
| **F3.6** | Healthcheck uses Python/httpx |
| **F3.7** | run.sh vs debug.sh image names |
| **F4.2** | No clear layering (acceptable) |
| **F4.3** | Global state / DI |
| **F5.1** | Absolute Docker paths in config |
| **F8.2** | Arrows training larger than digits |
| **F9.1** | Dual model storage confusion |
| **F10.3** | No integration tests |
