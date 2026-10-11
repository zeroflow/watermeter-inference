<!-- GSD:project-start source:PROJECT.md -->

## Project

**Watermeter Inference**

A self-hosted AI water meter reader. A single Docker container fetches snapshots from an ESP32-CAM (AI-on-the-edge), reads the digits and dial arrows with OpenVINO models, checks the reading for plausibility, and publishes it to Home Assistant via MQTT. The same container hosts a dashboard, a labelling UI, and in-process model training. The repo is public and ships as `ghcr.io/zeroflow/watermeter-inference`, so other people run it on their own home networks.

**Core Value:** The value Home Assistant shows is either correct and current, or visibly flagged as not. It is never silently wrong or frozen.

### Constraints

- **Production**: port 8001 / container `watermeter-dashboard-prod` must never be stopped, restarted, rebuilt, or exec'd into during development. Verify on debug (:8002).
- **Compatibility**: existing installs upgrade by pulling `:latest`/`:dev`; config and `/data` must keep working without manual edits beyond setting the password on first visit.
- **Public repo**: no real IPs, hostnames, credentials, or home paths in commits.
- **Tech stack**: stay within FastAPI/Starlette, Jinja2, and HTMX; add minimal dependencies (a password hash library is fine).
- **Config**: new config fields go through `config_utils.py` and its validation schema; the password hash should not live in the user-editable `config.yaml` (it would show in the config editor).
- **Git**: commit directly to `main` with the `claude: ` prefix; push to both `origin` and `gitea`.

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.10+ (code targets `py310` in `pyproject.toml`; CI and the local `.venv` use 3.12) - all application code in `watermeter/`, tests in `tests/`, and standalone scripts (`train_digits.py`, `train_arrows.py`, `benchmark_*.py`, `synthetic_generator.py`)
- HTML / Jinja2 templates - `watermeter/templates/`
- Vanilla JavaScript and CSS - `watermeter/static/` (no framework, no build step)
- YAML - runtime config `config.yaml`, `docker-compose.yml`, `docker-compose.gpu.yml`, CI workflows in `.github/workflows/` and `.gitea/workflows/`
- Shell - `docker-entrypoint.sh`, `debug.sh`, `debug_clean.sh`, `oneshot_test.sh`, `setup.sh` (stale, per CLAUDE.md), `run.sh`, `send_models.sh`
- Markdown - documentation under `docs/`, `README.md`, `DOCKER.md`, backlog in `backlog.md`

## Runtime

- Production/runtime container: base image `openvino/ubuntu22_runtime:2025.0.0` (defined in `Dockerfile`). Ships OpenVINO runtime and a system Python 3 on Ubuntu 22.04.
- Local development: uv-managed virtualenv at `.venv/` (CPython 3.12, per `.venv/pyvenv.cfg`).
- CI (`.github/workflows/ci.yml`): `actions/setup-python` with Python `3.12`.
- Container runs as non-root user `watermeter` (UID/GID 1000) after the entrypoint drops privileges via `gosu`.
- `uv` is the package manager for local work (`uv pip install -r requirements.txt -r requirements-dev.txt`). CLAUDE.md states no pip/poetry for local use.
- Docker image installs packages with `pip install` (see `Dockerfile`).
- Lockfile: Not detected. Dependencies are pinned by range only (`requirements*.txt`, `pyproject.toml` has no `[project]` dependency list).

## Frameworks

- FastAPI `>=0.100.0,<1.0.0` - HTTP API, dashboard pages, and routers in `watermeter/routes/` (entry point `watermeter.app:app`, `watermeter/app.py`)
- Uvicorn `>=0.23.0,<1.0.0` (with `standard` extras) - ASGI server; the container command is `python3 -m uvicorn watermeter.app:app --host 0.0.0.0 --port 8001` (`Dockerfile`)
- Pydantic (transitive via FastAPI; imported directly in `watermeter/routes/*.py`) - request models and validators
- Jinja2 `>=3.1.0,<4.0.0` - server-rendered templates in `watermeter/templates/`
- python-multipart `>=0.0.9,<1.0.0` - form parsing for FastAPI
- OpenVINO `>=2025.0.0,<2026.0.0` (pip package locally and in CI; provided by the base image in Docker) - model loading and inference via `ov.Core().compile_model()` in `watermeter/inference.py` and `watermeter/training_manager.py`. Device selected by `inference.device` (`AUTO`, `CPU`, `GPU`).
- OpenCV headless `>=4.8.0,<5.0.0` (`opencv-python-headless`) - image alignment, feature matching, ROI cropping (`watermeter/image_pipeline.py`, `watermeter/opencv_arrows.py`, `watermeter/feature_alignment.py`)
- NumPy `>=1.24.0,<3.0.0` and SciPy `>=1.11.0,<2.0.0` - numerics
- Pillow `>=10.0.0,<13.0.0` - image I/O
- PyTorch `>=2.1.0,<3.0.0` and torchvision `>=0.16.0,<1.0.0` - CPU-only wheels from `https://download.pytorch.org/whl/cpu` (installed before other deps in `Dockerfile`)
- timm `>=1.0.0,<2.0.0` - model architectures; `timm.create_model(..., pretrained=True)` in `watermeter/training_manager.py`
- ONNX `>=1.15.0,<2.0.0` and onnxscript `>=0.1.0,<1.0.0` - PyTorch -> ONNX export path (`torch.onnx.export` in `watermeter/training_core.py`); OpenVINO IR conversion via `ov.convert_model()`
- scikit-learn `>=1.3.0,<2.0.0` - `compute_class_weight` in `watermeter/training_core.py`
- matplotlib `>=3.7.0,<4.0.0` - training plots, Agg backend (`watermeter/training_manager.py`)
- pytest `>=7.0.0,<9.0.0` - config in `pytest.ini` (`testpaths = tests`, default run excludes the `integration` marker)
- pytest-asyncio `>=0.21.0,<1.0.0` - async tests
- httpx `>=0.24.0,<1.0.0` - FastAPI TestClient backend and integration tests
- Integration tests target a running server via `--base-url` (default: `http://localhost:8002`)
- Docker / Docker Compose - image build (`Dockerfile`), deployment (`docker-compose.yml`), GPU overlay (`docker-compose.gpu.yml`)
- Ruff (`uvx ruff`, `select = E, F, W, I`, line length 120) - lint, configured in `pyproject.toml`
- Black (`uvx black`, line length 120, target `py310`) - formatting, configured in `pyproject.toml`
- GitHub Actions (`.github/workflows/ci.yml`, `docker.yml`) and Gitea Actions mirror (`.gitea/workflows/`)
- Dependency-free repository config: `pyproject.toml` contains only tool sections (no `[project]` or `[build-system]`)

## Key Dependencies

- `openvino` 2025.x - the only inference runtime for production models; models are OpenVINO IR (`.xml` + `.bin`) under `/app/models/{digits,arrows}/<id>/`
- `opencv-python-headless` 4.8-4.x - alignment and arrow geometry; headless build avoids GUI libraries
- `fastapi` / `uvicorn` - all HTTP surface
- `paho-mqtt` `>=2.1.0,<3.0.0` - MQTT client (paho v2 callback API) in `watermeter/mqtt_publisher.py`; creates `mqtt.Client(...)` at line ~597 and uses paho's built-in reconnect backoff
- `ruamel.yaml` `>=0.18.0,<1.0.0` - round-trip YAML so comments in `config.yaml` survive edits (`watermeter/config_utils.py`)
- `httpx` `>=0.24.0,<1.0.0` - async HTTP client used to fetch camera images (`watermeter/image_pipeline.py`, lines ~154 and ~186), ROI preview fetches (`watermeter/routes/roi.py`), and tests
- `pyyaml` `>=6.0.0,<7.0.0` and `jsonschema` `>=4.0.0,<5.0.0` - config parsing and schema validation (`watermeter/config_utils.py` contains the JSON Schema, draft 2020-12)
- `timm`, `torch`, `torchvision`, `onnx`, `onnxscript`, `scikit-learn`, `matplotlib` - training stack only; not needed for inference-only operation
- `uv` - recommended local installer (not a runtime dependency)
- `pydantic` (transitive via FastAPI)
- `gosu` (apt package in `Dockerfile`, used by `docker-entrypoint.sh` to drop privileges)
- `libxcb*` apt packages in `Dockerfile` (runtime libraries for OpenCV)

## Configuration

- Runtime config file: `/config/config.yaml` inside the container, mapped from `./config` on the host. First run copies the shipped default from `config.yaml` (repo root) to `/config_default/config.yaml`, then `docker-entrypoint.sh` copies it to `/config`.
- Config values support `${VAR_NAME}` substitution from environment variables (`watermeter/config_utils.py`, `_substitute_env`); unset variables are left as literal text.
- `.env` file: present in the repo root (gitignored). Its contents are not documented here. `.env.example` lists the variable names used by compose: `HF_TOKEN`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `LOG_LEVEL`, `RENDER_GROUP`, `WATERMETER_TAG`, `PUID`, `PGID`.
- Environment variables read by application code:
- Key config sections in `config.yaml` (repo root, shipped default): `images`, `detection`, `alignment`, `trigger`, `mqtt`, `homeassistant`, `inference`, `plausibility`, `correction`, `confirmation`, `low_confidence`
- Config changes are made via `config_utils.py` (round-trip YAML) only. New fields must be added to its validation schema.
- Legacy `aiote:` section is ignored with a warning (CLAUDE.md).
- `Dockerfile` - base `openvino/ubuntu22_runtime:2025.0.0`, CPU torch wheels, `requirements-docker.txt`, creates UID 1000 user `watermeter`, exposes port 8001
- `requirements.txt` - core runtime + training deps (local development)
- `requirements-docker.txt` - container deps (openvino excluded; supplied by base image)
- `requirements-dev.txt` - pytest, pytest-asyncio, httpx
- `pyproject.toml` - Black and Ruff settings only
- `pytest.ini` - pytest markers and options
- `.dockerignore` - build context exclusions
- `docker-compose.yml` - service `watermeter` (container `watermeter-dashboard`), image `ghcr.io/zeroflow/watermeter-inference:${WATERMETER_TAG:-latest}`, port `8001:8001`, healthcheck hitting `/health` every 30s
- `debug.sh` - builds and runs `watermeter-dashboard-debug` on port 8002 with `config_debug/`, `data_debug/`, `models_debug/`
- `debug_clean.sh` - ephemeral first-boot test on port 8003
- `.github/workflows/docker.yml`: pushes to `main` publish `ghcr.io/<repo>:dev` and `sha-<short>`; tags `v*` publish `:<version>`, `:<major>.<minor>`, `:latest`. PRs build and smoke-test only (`curl /health` on port 8001).
- Actions are pinned by commit SHA.

## Platform Requirements

- Linux host (Proxmox PVE per CLAUDE.md); Python 3.12 via uv; Docker for debug container and image builds
- Optional Intel iGPU: `/dev/dri/renderD128` plus render group (`RENDER_GROUP`, default 44) for OpenVINO GPU (`docker-compose.gpu.yml`)
- Camera: AI-on-the-edge ESP32-CAM style HTTP image endpoint (`images.src` in `config.yaml`)
- Docker host running the `watermeter-dashboard` container (port 8001) with bind mounts for `/config`, `/data`, `/app/models`, `/training/*` and a named volume `hf_cache`
- CPU inference by default; Intel GPU optional
- Requires reachable MQTT broker (Home Assistant integration optional, `homeassistant.enabled`)

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- Python modules are `snake_case.py` under the `watermeter/` package, named for the concern they own: `image_pipeline.py`, `plausibility.py`, `rate_tracker.py`, `calibrated_arrows.py`, `config_utils.py`.
- Routes are one file per feature area in `watermeter/routes/` (`service.py`, `config.py`, `calibration.py`, `training.py`, `models.py`, `mqtt.py`, `metrics.py`, `roi.py`, `label.py`, `pages.py`, `synthetic.py`).
- Tests are `tests/unit/test_<module_or_feature>.py`. Helper modules that are not tests are plain names, e.g. `tests/unit/synthetic_dial.py`.
- Standalone legacy scripts at the repo root (`train_digits.py`, `train_arrows.py`, `benchmark_*.py`) are not part of the package and should not be imported from `watermeter/`.
- Markdown design docs go in `docs/plans/YYYY-MM-DD-name.md`; backlog items live in `backlog.md` as `BL-{id}`.
- `snake_case` everywhere (108 module-level `def` names under `watermeter/`).
- Module-private helpers use a leading underscore (31 module-level `_name` functions), e.g. `_round_half_up` in `watermeter/position_utils.py`, `_validate_model_id` as a `@staticmethod` on `ModelManager` in `watermeter/model_manager.py`.
- Async service entry points are `async def` (106 occurrences), e.g. `WatermeterService.process_reading` in `watermeter/watermeter_service.py`.
- Route handlers are plain `def`/`async def` functions decorated with `@router.get` / `@router.post` (see `watermeter/routes/service.py`).
- Module constants are `UPPER_SNAKE_CASE` at module top: `CALIBRATION_VERSION`, `MAX_PIVOT_CONDITION`, `MIN_TICKS`, `ROLL_WINDOW` (`watermeter/arrow_calibration.py`, `watermeter/position_utils.py`), `IMAGENET_MEAN` (`watermeter/training_core.py`).
- Position IDs are strings built by convention: `digit_1`, `digit_2`, ... and `analog_1`, ... (`get_position_ids` in `watermeter/position_utils.py`). Do not invent other formats.
- Class labels are strings. Digit classes are `"0"`–`"9"` plus the literal `"NAN"` (never `"N"`); arrow classes are numeric strings like `"0.0"`, `"0.1"`. See `tests/regression/test_nan_class_label.py`.
- Single-leading-underscore attributes hold internal state (`self._state`, `self._background_tasks`).
- Classes are `PascalCase`: `WatermeterService`, `PlausibilityChecker`, `RateTracker`, `StateStore`, `FailureStore`, `CalibrationSession`.
- Exception classes end in `Error` or describe the condition: `CalibrationError` (`watermeter/arrow_calibration.py`), `SessionError`, `SessionBusy` (`watermeter/calibration_session.py`).
- Enums use `PascalCase` with members in upper case: `JobStatus` (`watermeter/training_manager.py`).
- Result objects are small `@dataclass`es (8 occurrences), e.g. `PlausibilityResult` in `watermeter/plausibility.py`, `AlignmentResult` in `watermeter/image_pipeline.py`. Prefer a dataclass for new multi-field results instead of tuples or dicts.
- Pydantic `BaseModel` request bodies sit next to their routes (7 modules, e.g. `watermeter/routes/service.py`) and use `field_validator` (8 uses) for input checks.

## Type Annotations

- `typing` generics: `Dict`, `List`, `Optional`, `Tuple`, `Union` (about 44 `Dict[` uses, 112 `Optional[` uses; e.g. `watermeter/position_utils.py`, `watermeter/watermeter_service.py`, `watermeter/config_utils.py`).
- PEP 604 / builtin generics: `X | None`, `dict[...]`, `tuple[...]` (e.g. `watermeter/inference.py` `arrow_bin_width(...) -> float | None`, `watermeter/persistence.py` `tuple[Optional[float], Optional[datetime]]`).
- New code targets Python 3.10 (`target-version = "py310"`): `X | None` and `list[str]` are valid. Prefer the builtin/PEP 604 form in new modules.
- `from __future__ import annotations` is not used (7 files only touched for other reasons); do not rely on forward references without quoting.

## Code Style

- Black, line length **120**, target `py310` (`[tool.black]` in `pyproject.toml`). Run with `uvx black watermeter/ tests/`.
- Quote style: package modules use double quotes (e.g. `watermeter/plausibility.py`, `watermeter/config_utils.py`); many test files use single quotes in dict fixtures (e.g. `tests/unit/test_plausibility.py`). Keep the existing quote style of the file you touch; do not reformat unrelated lines.
- Trailing commas in multi-line calls and literals are the norm.
- Ruff, configured in `pyproject.toml` under `[tool.ruff]` with line length 120, rule sets `E`, `F`, `W`, `I` (isort).
- Ignored: `E501` (line length left to Black) and `E402` (module-level imports after `sys.path` / mock setup is required in test files and `tests/unit/conftest.py`).
- `known-first-party = ["watermeter"]`: the package is its own import section.
- CI runs `ruff check watermeter/ tests/` (`.github/workflows/ci.yml`). Run it locally as `uvx ruff check watermeter/ tests/` before committing.
- Type checking is not enforced in CI (no mypy config found in `pyproject.toml`).

## Import Organization

## Error Handling

- The service never raises into the reading loop for expected failures. Alignment failures, inference failures and unresolvable readings are recorded and the previous value is kept (see `process_reading` in `watermeter/watermeter_service.py`). Preserve this when adding steps: return a result or log and skip rather than letting an exception escape the loop.
- Broad handlers are common (`except Exception as e:`, 126 occurrences) in persistence, MQTT and routes, typically logging and falling back to a safe default. Example, `StateStore.load` in `watermeter/persistence.py` logs and returns `(None, None)`.
- A bare `except:` is not used (0 occurrences). Do not add one.
- Input validation raises `ValueError` with the offending value in the message: `ModelManager._validate_model_id` and `ModelManager._get_model_types_dir` in `watermeter/model_manager.py`, `safe_subpath` in `watermeter/app.py`.
- Path inputs from users must go through `safe_subpath(base, *parts)` (`watermeter/app.py`), which resolves and checks `is_relative_to`. Model IDs must go through `_validate_model_id()`.
- HTTP errors in routes return JSON with `success` and `message` keys and an explicit status, e.g. `resp.json()['success'] is False` with status 400 for bad config (`tests/unit/test_api_routes.py` documents the contract). `HTTPException` is used in `watermeter/routes/training.py` for upload validation.
- Domain errors use small custom exception classes (`CalibrationError`, `SessionError`, `SessionBusy`) which routes map to responses.

## Logging

- Use f-strings in log calls. This is the dominant style (about 240 `logger.<level>(f"...")` calls). Only 8 call sites use `%s` lazy formatting. Either is acceptable, but do not mix within one function.
- Levels: `logger.error` for failures that the code recovers from (persist failed, load failed); `logger.warning` for deprecated/ignored config (for example the legacy `aiote:` section in `watermeter/config_utils.py`) and degraded states; `logger.info` for lifecycle events; `logger.exception` only inside `except` blocks where the traceback matters (2 uses).
- Log file rotation is configured from `config.yaml` `logging` section in `WatermeterService.__init__`.
- `print()` appears only in the one-shot CLI output path (`watermeter/oneshot.py`). Do not use `print` in the service.

## Comments

- Explain *why* for non-obvious numerics and thresholds. Examples: the `ROLL_WINDOW` comment in `watermeter/position_utils.py` explains the 0.1-dial wrap, `MAX_PIVOT_CONDITION` in `watermeter/arrow_calibration.py` explains the fallback.
- Reference a backlog item or regression when a workaround exists (`# Populate last_published from persisted state (BL-14)` in `watermeter/watermeter_service.py`).
- Mark mock setup in tests with a comment explaining why a module is stubbed (`tests/unit/conftest.py`).
- Module docstrings describe the module's role in one or two sentences (`watermeter/position_utils.py`, `watermeter/config_utils.py`).
- Public functions and methods use triple-quoted docstrings with an `Args:` / `Returns:` section for non-trivial logic (`get_position_ids` in `watermeter/position_utils.py`). One-line docstrings are fine for simple methods.
- Test functions carry a one-line docstring stating the expectation (e.g. `"""Half/upper mismatch -> warning."""` in `tests/unit/test_plausibility.py`).

## Function Design

- Prefer keyword arguments for optional or multi-field calls, e.g. `PlausibilityChecker.validate_plausibility(new_value=..., previous_value=..., last_update_time=...)`.
- Configuration is passed as the parsed config dict (`config: Dict`) or as a sub-section. Modules read their own section keys (`config["plausibility"]`, `config["inference"]`). The dict is the contract; add new keys to the schema (below).
- Constructor injection is used for collaborators: `PlausibilityChecker(config=..., rate_tracker=...)`.
- Predicates and validators return tuples `(is_valid, warnings)` or a dataclass (`PlausibilityResult`). Keep that shape for new validators.
- Functions that can legitimately have no answer return `Optional[...]` / `X | None` and the caller checks for `None`.

## Configuration

- All reads and writes of `config.yaml` go through `watermeter/config_utils.py` (ruamel.yaml, comment-preserving; `preserve_quotes=True`, mapping indent 2, sequence indent 4 with offset 2). Do not call `yaml.safe_load` on the user config in new code. `WatermeterService.__init__` currently uses PyYAML for the runtime read; keep the write path on `config_utils`.
- Every new config field must be added to the validation schema in `config_utils.py`. The JSON schema is served at `/api/config/schema.json` and is checked by `tests/unit/test_api_routes.py`.
- Env-var substitution is supported in config values; see `tests/unit/test_env_var_substitution.py` for the accepted syntax before adding new substitutions.
- Secrets come from environment variables (`MQTT_USERNAME`, `MQTT_PASSWORD`), never from committed config. The repo `config.yaml` uses placeholders.

## Module Design

- `process_reading` is serialized by an `asyncio.Lock`. Use `asyncio.Lock` for async-only state.
- Use `threading.Lock` for state touched from daemon threads (training jobs in `watermeter/training_manager.py`, file writers). 5 `threading.Lock`/`RLock` usages exist.
- Blocking work called from async code goes through `asyncio.to_thread` or `run_in_executor` (7 call sites).

## Model/Data Conventions

- Model directories: `/app/models/{digits,arrows}/<id>/` with `<id>.xml`/`.bin` plus `metadata.json`. Read metadata through `ModelManager`, not by hand.
- Persisted runtime data is JSON under `/data` (state, failures, metrics), written through `StateStore`, `FailureStore`, `PipelineMetrics`. Do not add a second writer for the same file.
- Calibration files are versioned (`CALIBRATION_VERSION`); bump the version when the on-disk shape changes.

## Frontend Conventions (Jinja2 + HTMX)

- Templates in `watermeter/templates/`, static JS/CSS in `watermeter/static/`. Vanilla JS per page; no framework.
- Theming uses CSS variables in `watermeter/static/style.css` (light and dark). Avoid inline `style=` attributes; add a class and a variable instead.

## Git and Commits

- Commit messages start with `claude: ` (for example `claude: calibration tab review fixes`). Keep the subject in the imperative and short.
- Never amend or force-push without explicit approval. Do not commit real IPs, hostnames, credentials or home paths; the repo is public.

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## System Overview

```text

```

## Pattern Overview

- One process, one container: the web server, the reading loop, MQTT client, stats loop, and training threads all live in the uvicorn process (`watermeter.app:app`).
- Process-wide singletons accessed through `get_service()` (`watermeter/watermeter_service.py`), `get_inference_service()` (`watermeter/inference.py`), and `get_training_manager()` (`watermeter/training_manager.py`). Routes and the service reach each other through these getters.
- Facade pattern: `WatermeterService` keeps public method names (`process_reading`, `calculate_total`, `validate_plausibility`, `correct_predictions`, etc.) while delegating to modules such as `image_pipeline`, `plausibility`, `confirmation`, `correction`, `rate_tracker`, `leak_detector`, `mqtt_publisher`, `low_confidence_capture`, `data_collector`, `persistence`, and `metrics`.
- Fail-closed reading: alignment failures and unresolvable totals keep the previous value. The pipeline status moves OK → DEGRADED → FAILED → STALE, and STALE triggers an MQTT notification (`_notify_stale`, `_derive_pipeline_status` in `watermeter/watermeter_service.py`).
- Config-driven: a single YAML file (`config.yaml`) is loaded into `WatermeterService.config` as a dict. Runtime edits go through `watermeter/config_utils.py` (ruamel.yaml, comment-preserving), and `reload_config` applies changes without restart.
- Plain-dict data passing: predictions, pipeline results, and state are dicts (`Dict[str, Dict]`) rather than typed domain objects. Only a few dataclasses exist (`AlignmentResult` in `watermeter/image_pipeline.py`, `PlausibilityResult` in `watermeter/plausibility.py`, `TrainingJob`/`BenchmarkJob` in `watermeter/training_manager.py`).

## Layers

- Purpose: Build the FastAPI app, wire routers, run lifecycle startup and shutdown.
- Location: `watermeter/app.py`, `watermeter/__main__.py`
- Contains: `lifespan()`, `safe_subpath()`, `app` object, `main()`.
- Depends on: `watermeter/watermeter_service.py`, `watermeter/inference.py`, `watermeter/image_hash.py` (startup dedup), all modules in `watermeter/routes/`.
- Used by: uvicorn (`watermeter.app:app`), Docker `CMD`, and `python -m watermeter`.
- Purpose: Serve HTML pages, HTMX fragments, and JSON API endpoints.
- Location: `watermeter/routes/*.py` (one router per feature area), `watermeter/templates/`, `watermeter/static/`.
- Contains: `APIRouter` objects `router` in each route module; Jinja2 templates; per-page vanilla JS.
- Depends on: `WatermeterService` (via `get_service()`), `InferenceService`, `TrainingManager`, `ModelManager`, `config_utils`, `image_pipeline` (ROI setup), `calibration_session` (calibration tab).
- Used by: Browser UI, Home Assistant or other API clients, the test suite (`tests/unit/test_api_routes.py` and friends).
- Purpose: Execute one reading end to end and hold live state.
- Location: `watermeter/watermeter_service.py` (1380 lines), `watermeter/scheduling.py`, `watermeter/meter_state.py`.
- Contains: `process_reading`, `fetch_images`, `run_inference`, `publish_to_mqtt`, `set_manual_value`, `reset_previous_value`, `reload_config`, cyclic and stats loops, `MeterState`, `SchedulingManager`.
- Depends on: every module in the processing layer below.
- Used by: `watermeter/app.py` lifespan, `watermeter/routes/service.py`, `watermeter/routes/mqtt.py`, `watermeter/oneshot.py` (indirectly via pipeline modules).
- Purpose: Image alignment, inference, value arithmetic, validation.
- Location and responsibilities:
- Depends on: `watermeter/position_utils.py` is a leaf. `watermeter/correction.py` depends on `meter_state`, `position_utils`, `rate_tracker`. `watermeter/plausibility.py` depends on `position_utils`, `rate_tracker`, `reanchor`.
- Used by: `WatermeterService`, `watermeter/oneshot.py`, `watermeter/routes/roi.py` (ROI preview), `watermeter/routes/calibration.py`.
- Purpose: Persist state and publish readings.
- Location: `watermeter/persistence.py` (state file, failure history), `watermeter/metrics.py` (pipeline metrics), `watermeter/mqtt_publisher.py` (paho-mqtt client, HA discovery, retained state, stats), `watermeter/low_confidence_capture.py` and `watermeter/data_collector.py` (save images for training).
- Depends on: `watermeter/image_hash.py` for dedup of saved images.
- Used by: `WatermeterService`.
- Purpose: Train, export, benchmark, and manage models.
- Location:
- Depends on: `training_core` depends on torch, timm, and OpenVINO. `training_manager` depends on `model_manager`.
- Used by: `watermeter/routes/training.py`, `watermeter/routes/models.py`, `watermeter/routes/synthetic.py`, `watermeter/routes/label.py`.
- `watermeter/config_utils.py`: all config reads and writes; schema validation for new fields.
- `watermeter/config_utils.py` env substitution handles `MQTT_USERNAME`/`MQTT_PASSWORD`.
- `watermeter/metrics.py` and `watermeter/routes/metrics.py`: metrics collection and exposure.

## Data Flow

### Primary Request Path (one reading)

### Training Flow

### Setup / ROI Flow

- `watermeter/routes/roi.py` (largest route file, ~876 lines) and `watermeter/static/roi-config.js` drive rotation, marker, digit, and dial configuration. Preview endpoints call `ImagePipeline` and `inference` directly.
- Live state is in-memory on `WatermeterService` (`current_state`, `previous_value`, `consecutive_rejections`, `leak_warning`, `rate_history`), backed by `MeterState` and `persistence.StateStore`.
- Pipeline health: `FailureStore` (`watermeter/persistence.py`) and `PipelineMetrics` (`watermeter/metrics.py`) store JSON under `/data`.
- Concurrency: `processing_lock` (`asyncio.Lock`) serializes readings. Background tasks are kept in `_background_tasks` (`watermeter/app.py`) to avoid garbage collection.

## Key Abstractions

- Purpose: Facade and orchestrator for the reading pipeline and live state.
- Examples: `watermeter/watermeter_service.py`
- Pattern: Singleton via `get_service()`; facade over focused modules.
- Purpose: Owns loaded OpenVINO models and the arrow backend.
- Examples: `watermeter/inference.py` (`Classifier`, `Regressor`, `InferenceService`, `validate_model_config`)
- Pattern: Singleton via `get_inference_service()`; tolerates missing models at startup.
- Purpose: Turn a camera frame into aligned ROI crops and report alignment status.
- Examples: `watermeter/image_pipeline.py`
- Pattern: Class with pure helper functions (`apply_fisheye_correction`, `rotate_image_full`) at module level.
- Purpose: Three interchangeable arrow readers selected by `inference.arrows_mode`.
- Examples: `watermeter/inference.py` (model), `watermeter/opencv_arrows.py` (`OpenCVArrowDetector`), `watermeter/calibrated_arrows.py` (`CalibratedArrowDetector`), `watermeter/arrow_calibration.py`.
- Pattern: Factory `_create_arrows_backend` selects the implementation from config.
- Purpose: Reject implausible jumps while allowing deliberate re-baselining.
- Examples: `watermeter/plausibility.py`, `watermeter/reanchor.py`.
- Pattern: Stateful checker updated by the service on each accepted or rejected value.
- Purpose: Track long-running training or benchmark work and expose status to the UI.
- Examples: `watermeter/training_manager.py` (`JobStatus` enum, dataclasses).
- Pattern: Job queue with daemon threads and polling from HTMX fragments.
- Purpose: Collect aligned frames and measured pivot data for the arrow calibration tab.
- Examples: `watermeter/calibration_session.py`, `watermeter/routes/calibration.py`, `watermeter/templates/calibration.html`.
- Pattern: Session object held by the service (`self.calibration_session`).

## Entry Points

- Location: `watermeter/app.py` (`app = FastAPI(...)`)
- Triggers: `uvicorn watermeter.app:app` (Docker `CMD` in `Dockerfile`, port 8001; debug container on 8002 via `debug.sh`).
- Responsibilities: Lifespan startup (inference init, MQTT, stats loop, cyclic loop, dedup, initial reading) and shutdown; router wiring.
- Location: `watermeter/__main__.py` (`cli()`), `python -m watermeter`.
- Triggers: `--one-shot --config <path>` runs `watermeter/oneshot.py` `main()` and exits 0 on success. Without the flag it runs `watermeter.app.main()` (uvicorn on the configured dashboard host and port).
- Responsibilities: One reading without the web server, used by `oneshot_test.sh` and `tests/integration/test_oneshot.py`.
- Location: `docker-entrypoint.sh` (copied to `/docker-entrypoint.sh` by `Dockerfile`).
- Triggers: Container start.
- Responsibilities: Copies default config into `/config`, remaps user to `PUID`/`PGID`, installs `pretrained/` models into `/app/models` when empty, drops privileges.
- Location: `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py` at repo root.
- Triggers: Manual invocation. Documented as legacy standalone scripts, not used by the server.
- Location: `scripts/replay_*.py`, `scripts/calibrate_arrows.py`, `scripts/tune_synthetic.py`, `scripts/backfill_training_samples.py`.
- Triggers: Manual invocation for offline analysis.

## Architectural Constraints

- **Threading:** Single asyncio event loop in uvicorn for the reading pipeline and HTTP. Training uses daemon threads inside the same process. MQTT uses paho's network thread, with callbacks marshalled into the loop via `self.loop`. OpenVINO inference is CPU or GPU bound and runs inside the lock.
- **Global state:** Module-level singletons `_service` (`watermeter/watermeter_service.py`), `_inference_service` (`watermeter/inference.py`), `_training_manager` (`watermeter/training_manager.py`), and `_background_tasks` (`watermeter/app.py`). The `app` object is also module-level.
- **Circular imports:** `watermeter/routes/label.py` and `watermeter/routes/service.py` import `from ..app import ...`, which loads `app.py` and the route modules. `app.py` avoids cycles by importing routers after `safe_subpath` and the background-task helper are defined (see the `# noqa: E402` block near the bottom of `app.py`). New route modules must follow the same pattern or import from `watermeter/watermeter_service.py` directly.
- **Single writer for state:** Only `WatermeterService` should mutate `current_state` and `previous_value`. Routes call its methods (`reset_previous_value`, `set_manual_value`, `reload_config`).
- **Config reload:** `reload_config` must preserve the baseline and restart affected loops. Changes to inference models go through `InferenceService` reload, not a process restart.
- **Production isolation:** Port 8001 in container `watermeter-dashboard-prod` is production. Development runs on 8002 (`debug.sh`) or 8003 (`debug_clean.sh`).

## Anti-Patterns

### Routes calling `get_service()` and mutating state directly

### Importing the app module from route modules

### Growing the facade

## Error Handling

- Alignment failures increment a counter and move the pipeline status. Consecutive failures transition to STALE and publish an MQTT notification (`_notify_stale`).
- Rejected readings (implausible, unresolvable total, consistency failure) keep the previous value and record a rejection count (`consecutive_rejections`, `max_consecutive_rejections`).
- Inference initialization failure at startup is logged as a warning and the app starts without models (`watermeter/app.py` lifespan).
- Persistence and metrics failures are logged, not raised (see `tests/unit/test_persistence_failures.py`).
- HTTP errors use FastAPI `HTTPException` in route modules.

## Cross-Cutting Concerns

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
