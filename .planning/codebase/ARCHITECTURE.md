---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
<!-- refreshed: 2026-10-11 -->

# Architecture

**Analysis Date:** 2026-10-11

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                 Triggers                                                  │
│  MQTT trigger topic (`on_mqtt_message` in `watermeter/watermeter_service.py`)            │
│  Cyclic loop (`_cyclic_loop`)   POST /api/trigger (`routes/service.py`)  │
└───────────────────────────────┬──────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  WatermeterService facade  `watermeter/watermeter_service.py`            │
│  process_reading() guarded by asyncio.Lock (processing_lock)             │
└───┬──────────────┬──────────────┬──────────────┬──────────────┬──────────┘
    ▼              ▼              ▼              ▼              ▼
┌────────┐   ┌───────────┐  ┌────────────┐ ┌────────────┐ ┌──────────────┐
│ image_ │──▶│ inference │─▶│ position_  │▶│ plausibility│▶│ persistence /│
│pipeline│   │ .py       │  │ utils +    │ │ + confirm.  │ │ mqtt_publisher│
│        │   │(OpenVINO, │  │ correction │ │ + leak      │ │ (HA via MQTT)│
│        │   │ OpenCV,   │  │            │ │             │ │              │
│        │   │ calibrated│  │            │ │             │ │              │
└────────┘   └───────────┘  └────────────┘ └────────────┘ └──────────────┘
                                ▲
┌──────────────────────────────────────────────────────────────────────────┐
│  FastAPI app `watermeter/app.py`: routers in `watermeter/routes/`        │
│  Jinja2 pages + HTMX fragments + JSON API; static JS per page            │
└──────────────────────────────────────────────────────────────────────────┘
                                ▲
┌──────────────────────────────────────────────────────────────────────────┐
│  Background: TrainingManager (daemon threads) → training_core → IR export│
│  ModelManager (`model_manager.py`) owns /app/models/{digits,arrows}/<id>/│
└──────────────────────────────────────────────────────────────────────────┘
```

## Pattern Overview

**Overall:** Monolithic single-process service. A large facade class (`WatermeterService`) orchestrates focused modules for the reading pipeline. A FastAPI web app runs in the same process and shares the service and inference singletons. Training runs in-process in daemon threads.

**Key Characteristics:**
- One process, one container: the web server, the reading loop, MQTT client, stats loop, and training threads all live in the uvicorn process (`watermeter.app:app`).
- Process-wide singletons accessed through `get_service()` (`watermeter/watermeter_service.py`), `get_inference_service()` (`watermeter/inference.py`), and `get_training_manager()` (`watermeter/training_manager.py`). Routes and the service reach each other through these getters.
- Facade pattern: `WatermeterService` keeps public method names (`process_reading`, `calculate_total`, `validate_plausibility`, `correct_predictions`, etc.) while delegating to modules such as `image_pipeline`, `plausibility`, `confirmation`, `correction`, `rate_tracker`, `leak_detector`, `mqtt_publisher`, `low_confidence_capture`, `data_collector`, `persistence`, and `metrics`.
- Fail-closed reading: alignment failures and unresolvable totals keep the previous value. The pipeline status moves OK → DEGRADED → FAILED → STALE, and STALE triggers an MQTT notification (`_notify_stale`, `_derive_pipeline_status` in `watermeter/watermeter_service.py`).
- Config-driven: a single YAML file (`config.yaml`) is loaded into `WatermeterService.config` as a dict. Runtime edits go through `watermeter/config_utils.py` (ruamel.yaml, comment-preserving), and `reload_config` applies changes without restart.
- Plain-dict data passing: predictions, pipeline results, and state are dicts (`Dict[str, Dict]`) rather than typed domain objects. Only a few dataclasses exist (`AlignmentResult` in `watermeter/image_pipeline.py`, `PlausibilityResult` in `watermeter/plausibility.py`, `TrainingJob`/`BenchmarkJob` in `watermeter/training_manager.py`).

## Layers

**Entry / Composition Layer:**
- Purpose: Build the FastAPI app, wire routers, run lifecycle startup and shutdown.
- Location: `watermeter/app.py`, `watermeter/__main__.py`
- Contains: `lifespan()`, `safe_subpath()`, `app` object, `main()`.
- Depends on: `watermeter/watermeter_service.py`, `watermeter/inference.py`, `watermeter/image_hash.py` (startup dedup), all modules in `watermeter/routes/`.
- Used by: uvicorn (`watermeter.app:app`), Docker `CMD`, and `python -m watermeter`.

**HTTP / Presentation Layer:**
- Purpose: Serve HTML pages, HTMX fragments, and JSON API endpoints.
- Location: `watermeter/routes/*.py` (one router per feature area), `watermeter/templates/`, `watermeter/static/`.
- Contains: `APIRouter` objects `router` in each route module; Jinja2 templates; per-page vanilla JS.
- Depends on: `WatermeterService` (via `get_service()`), `InferenceService`, `TrainingManager`, `ModelManager`, `config_utils`, `image_pipeline` (ROI setup), `calibration_session` (calibration tab).
- Used by: Browser UI, Home Assistant or other API clients, the test suite (`tests/unit/test_api_routes.py` and friends).

**Orchestration / Domain Layer:**
- Purpose: Execute one reading end to end and hold live state.
- Location: `watermeter/watermeter_service.py` (1380 lines), `watermeter/scheduling.py`, `watermeter/meter_state.py`.
- Contains: `process_reading`, `fetch_images`, `run_inference`, `publish_to_mqtt`, `set_manual_value`, `reset_previous_value`, `reload_config`, cyclic and stats loops, `MeterState`, `SchedulingManager`.
- Depends on: every module in the processing layer below.
- Used by: `watermeter/app.py` lifespan, `watermeter/routes/service.py`, `watermeter/routes/mqtt.py`, `watermeter/oneshot.py` (indirectly via pipeline modules).

**Processing Layer (pure-ish logic):**
- Purpose: Image alignment, inference, value arithmetic, validation.
- Location and responsibilities:
  - `watermeter/image_pipeline.py`: fetch, fisheye, rotation, marker and feature alignment, ROI crop (`ImagePipeline`).
  - `watermeter/feature_alignment.py`: feature-based alignment fallback used by `image_pipeline`.
  - `watermeter/inference.py`: `Classifier`, `Regressor`, `InferenceService`, arrow backend selection (`_create_arrows_backend`).
  - `watermeter/opencv_arrows.py`, `watermeter/calibrated_arrows.py`, `watermeter/arrow_calibration.py`: classical arrow detection and calibrated geometric arrow reading.
  - `watermeter/position_utils.py`: carry-aware total calculation (`calculate_total`).
  - `watermeter/correction.py`: `CorrectionEngine` for optional value correction.
  - `watermeter/plausibility.py`: `PlausibilityChecker` (max change, reverse flow, sustained consumption).
  - `watermeter/reanchor.py`: re-anchor candidate logic used by plausibility.
  - `watermeter/rate_tracker.py`, `watermeter/leak_detector.py`: rate history and leak detection.
  - `watermeter/confirmation.py`: `ConfirmationManager`, MQTT user confirmation with timeout.
- Depends on: `watermeter/position_utils.py` is a leaf. `watermeter/correction.py` depends on `meter_state`, `position_utils`, `rate_tracker`. `watermeter/plausibility.py` depends on `position_utils`, `rate_tracker`, `reanchor`.
- Used by: `WatermeterService`, `watermeter/oneshot.py`, `watermeter/routes/roi.py` (ROI preview), `watermeter/routes/calibration.py`.

**Output / Side-Effect Layer:**
- Purpose: Persist state and publish readings.
- Location: `watermeter/persistence.py` (state file, failure history), `watermeter/metrics.py` (pipeline metrics), `watermeter/mqtt_publisher.py` (paho-mqtt client, HA discovery, retained state, stats), `watermeter/low_confidence_capture.py` and `watermeter/data_collector.py` (save images for training).
- Depends on: `watermeter/image_hash.py` for dedup of saved images.
- Used by: `WatermeterService`.

**Training & Model Layer:**
- Purpose: Train, export, benchmark, and manage models.
- Location:
  - `watermeter/training_manager.py`: `TrainingManager` job queue, daemon threads, benchmark jobs.
  - `watermeter/training_core.py`: PyTorch datasets, transforms, training helpers, `export_to_openvino`.
  - `watermeter/model_manager.py`: model directory layout, activation, archival, `_validate_model_id()`.
  - `watermeter/synthetic_generator.py`: synthetic digit and arrow data.
  - `watermeter/mislabel_detector.py`: suspicious-label detection over training data.
  - `watermeter/photo_master.py`: image helpers for the training and ROI tooling (see module docstring).
- Depends on: `training_core` depends on torch, timm, and OpenVINO. `training_manager` depends on `model_manager`.
- Used by: `watermeter/routes/training.py`, `watermeter/routes/models.py`, `watermeter/routes/synthetic.py`, `watermeter/routes/label.py`.

**Config & Cross-Cutting:**
- `watermeter/config_utils.py`: all config reads and writes; schema validation for new fields.
- `watermeter/config_utils.py` env substitution handles `MQTT_USERNAME`/`MQTT_PASSWORD`.
- `watermeter/metrics.py` and `watermeter/routes/metrics.py`: metrics collection and exposure.

## Data Flow

### Primary Request Path (one reading)

1. Trigger arrives: MQTT message on `trigger_topic` (`WatermeterService.on_mqtt_message`), cyclic loop tick (`_cyclic_loop`), or `POST /api/trigger` (`watermeter/routes/service.py`). All paths call `process_reading()`.
2. `process_reading` (`watermeter/watermeter_service.py`, around line 656) acquires `processing_lock`, returns early if confirmation is pending, no models are loaded, or no digit ROIs exist.
3. Step 1, fetch and align: `fetch_images()` → `ImagePipeline.fetch_images()` (`watermeter/image_pipeline.py`) pulls the whole image or one image per ROI from `images.src`. `process_whole_image` applies fisheye and rotation, then marker or feature alignment, then ROI crop. Alignment failures are counted in `consecutive_alignment_failures`.
4. Each successful alignment optionally feeds `CalibrationSession` (`_feed_calibration_session` in `watermeter/watermeter_service.py`).
5. Step 2, inference: `run_inference()` (`watermeter/watermeter_service.py`, around line 391) calls `InferenceService` in `watermeter/inference.py`. Digits use an 11-class `Classifier` (`0`–`9`, `NAN`). Arrows use a classifier or `Regressor` (mode from model `metadata.json` `training_mode`), or the `opencv` or `calibrated` backend from `_create_arrows_backend`.
6. Step 3, total: `calculate_total` (`watermeter/position_utils.py`) builds the value using the previous reading to resolve carries. Unresolvable readings are rejected.
7. Steps 4 and 5, checks: cross-arrow consistency (`check_consistency`), then plausibility (`validate_plausibility` → `PlausibilityChecker`, which also uses `RateTracker`, `LeakDetector`, and reanchor candidates).
8. Step 6, low-confidence handling: `save_low_confidence` (`watermeter/watermeter_service.py`, around line 556) writes crops via `LowConfidenceCapture` and `image_hash` dedup.
9. Optional correction: `correct_predictions` → `CorrectionEngine` (`watermeter/correction.py`) may adjust digits.
10. Optional confirmation: `_should_request_confirmation` → `_publish_confirmation_request`. The reading is held until `_handle_confirmation_response` or `_confirmation_timeout`.
11. Step 7, update state: `current_state` and `MeterState` are updated, `persistence` writes `/data/state.json`, and `publish_to_mqtt` (`watermeter/watermeter_service.py`, around line 1064) sends the value to Home Assistant via `MqttPublisher`.
12. Rejections keep the previous value. `POST /api/reset` (`reset_previous_value`) clears the baseline.

### Training Flow

1. Labeling UI (`watermeter/routes/label.py`, `static/label.js`) writes crops under `/training/{digits,arrows}/`.
2. Training is queued via `watermeter/routes/training.py` → `TrainingManager` (`watermeter/training_manager.py`), which starts a daemon thread.
3. The thread uses `training_core` (PyTorch) and then `export_to_openvino` to produce an OpenVINO IR `.xml`/`.bin` plus `metadata.json`, written by `ModelManager`.
4. Activation: `ModelManager` updates the `inference.{digits,arrows}_model` config path, and `InferenceService` hot-reloads via `reload_models`.

### Setup / ROI Flow

- `watermeter/routes/roi.py` (largest route file, ~876 lines) and `watermeter/static/roi-config.js` drive rotation, marker, digit, and dial configuration. Preview endpoints call `ImagePipeline` and `inference` directly.

**State Management:**
- Live state is in-memory on `WatermeterService` (`current_state`, `previous_value`, `consecutive_rejections`, `leak_warning`, `rate_history`), backed by `MeterState` and `persistence.StateStore`.
- Pipeline health: `FailureStore` (`watermeter/persistence.py`) and `PipelineMetrics` (`watermeter/metrics.py`) store JSON under `/data`.
- Concurrency: `processing_lock` (`asyncio.Lock`) serializes readings. Background tasks are kept in `_background_tasks` (`watermeter/app.py`) to avoid garbage collection.

## Key Abstractions

**WatermeterService:**
- Purpose: Facade and orchestrator for the reading pipeline and live state.
- Examples: `watermeter/watermeter_service.py`
- Pattern: Singleton via `get_service()`; facade over focused modules.

**InferenceService:**
- Purpose: Owns loaded OpenVINO models and the arrow backend.
- Examples: `watermeter/inference.py` (`Classifier`, `Regressor`, `InferenceService`, `validate_model_config`)
- Pattern: Singleton via `get_inference_service()`; tolerates missing models at startup.

**ImagePipeline and AlignmentResult:**
- Purpose: Turn a camera frame into aligned ROI crops and report alignment status.
- Examples: `watermeter/image_pipeline.py`
- Pattern: Class with pure helper functions (`apply_fisheye_correction`, `rotate_image_full`) at module level.

**Arrow backends:**
- Purpose: Three interchangeable arrow readers selected by `inference.arrows_mode`.
- Examples: `watermeter/inference.py` (model), `watermeter/opencv_arrows.py` (`OpenCVArrowDetector`), `watermeter/calibrated_arrows.py` (`CalibratedArrowDetector`), `watermeter/arrow_calibration.py`.
- Pattern: Factory `_create_arrows_backend` selects the implementation from config.

**PlausibilityChecker and ReanchorCandidates:**
- Purpose: Reject implausible jumps while allowing deliberate re-baselining.
- Examples: `watermeter/plausibility.py`, `watermeter/reanchor.py`.
- Pattern: Stateful checker updated by the service on each accepted or rejected value.

**TrainingJob / BenchmarkJob:**
- Purpose: Track long-running training or benchmark work and expose status to the UI.
- Examples: `watermeter/training_manager.py` (`JobStatus` enum, dataclasses).
- Pattern: Job queue with daemon threads and polling from HTMX fragments.

**CalibrationSession:**
- Purpose: Collect aligned frames and measured pivot data for the arrow calibration tab.
- Examples: `watermeter/calibration_session.py`, `watermeter/routes/calibration.py`, `watermeter/templates/calibration.html`.
- Pattern: Session object held by the service (`self.calibration_session`).

## Entry Points

**ASGI application:**
- Location: `watermeter/app.py` (`app = FastAPI(...)`)
- Triggers: `uvicorn watermeter.app:app` (Docker `CMD` in `Dockerfile`, port 8001; debug container on 8002 via `debug.sh`).
- Responsibilities: Lifespan startup (inference init, MQTT, stats loop, cyclic loop, dedup, initial reading) and shutdown; router wiring.

**CLI:**
- Location: `watermeter/__main__.py` (`cli()`), `python -m watermeter`.
- Triggers: `--one-shot --config <path>` runs `watermeter/oneshot.py` `main()` and exits 0 on success. Without the flag it runs `watermeter.app.main()` (uvicorn on the configured dashboard host and port).
- Responsibilities: One reading without the web server, used by `oneshot_test.sh` and `tests/integration/test_oneshot.py`.

**Entrypoint shell script:**
- Location: `docker-entrypoint.sh` (copied to `/docker-entrypoint.sh` by `Dockerfile`).
- Triggers: Container start.
- Responsibilities: Copies default config into `/config`, remaps user to `PUID`/`PGID`, installs `pretrained/` models into `/app/models` when empty, drops privileges.

**Training scripts (legacy):**
- Location: `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py` at repo root.
- Triggers: Manual invocation. Documented as legacy standalone scripts, not used by the server.

**Replay and tuning scripts:**
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

**What happens:** Some route handlers reach into `WatermeterService` internals or reimplement logic that the service already owns.
**Why it's wrong:** It bypasses the lock and the state invariants in `process_reading`, which can race with a reading in progress.
**Do this instead:** Add a public method on `WatermeterService` (see `set_manual_value` and `reset_previous_value` in `watermeter/watermeter_service.py`) and call it from the route, holding `processing_lock` where the state changes.

### Importing the app module from route modules

**What happens:** `watermeter/routes/label.py` and `watermeter/routes/service.py` import `from ..app import ...` to access shared helpers such as `safe_subpath`.
**Why it's wrong:** It couples routes to the composition root and makes import order fragile.
**Do this instead:** Move new shared helpers into a leaf module (for example `watermeter/config_utils.py` or a new `watermeter/paths.py`) and import from there. Keep `safe_subpath` usage for any user-supplied path (see the `safe_subpath()` rule in `CLAUDE.md`).

### Growing the facade

**What happens:** `watermeter/watermeter_service.py` (1380 lines) and `watermeter/training_manager.py` (1495 lines) keep accumulating methods.
**Why it's wrong:** The modules they coordinate are already split out (`plausibility`, `confirmation`, `correction`), so new logic belongs in those modules, not the facade.
**Do this instead:** Add new reading logic to a focused module and expose only a thin delegating method on `WatermeterService`.

## Error Handling

**Strategy:** Fail closed on the reading path, log and continue on background work.

**Patterns:**
- Alignment failures increment a counter and move the pipeline status. Consecutive failures transition to STALE and publish an MQTT notification (`_notify_stale`).
- Rejected readings (implausible, unresolvable total, consistency failure) keep the previous value and record a rejection count (`consecutive_rejections`, `max_consecutive_rejections`).
- Inference initialization failure at startup is logged as a warning and the app starts without models (`watermeter/app.py` lifespan).
- Persistence and metrics failures are logged, not raised (see `tests/unit/test_persistence_failures.py`).
- HTTP errors use FastAPI `HTTPException` in route modules.

## Cross-Cutting Concerns

**Logging:** Python `logging` with module-level `logger = logging.getLogger(__name__)`. Level and format are set through the `logging:` section in `config.yaml`.
**Validation:** Config validation in `watermeter/config_utils.py` (schema). Model IDs are validated with `_validate_model_id()` in `watermeter/model_manager.py`. Filesystem paths from user input use `safe_subpath()` in `watermeter/app.py`.
**Authentication:** None on the API by design (see `docs/decisions.md`, AD-01). MQTT credentials come from `MQTT_USERNAME` and `MQTT_PASSWORD` environment variables.
**Outbound requests:** Image source fetch in `watermeter/image_pipeline.py` has no SSRF protection by design (AD-02).

---

*Architecture analysis: 2026-10-11*
