# Backlog

Format: `BL-{id}` | status: `idea` → `planned` → `in-progress` → `done`

Next ID: BL-85

See `backlog_archiv.md` for completed items (BL-01 through BL-25).

---

## P1 — Critical

- **BL-26** `done` — **Paho-MQTT v2 API migration**: code uses v1 API (`mqtt.Client(client_id=...)`) but v2 is installed; runs on deprecated compat layer that breaks in v3
  - Add `CallbackAPIVersion` parameter, update callback signatures (5 args)
  - Wire up MQTT authentication (username/password) — `docker-compose.yml` mentions env vars but code never reads them
  - **Ref**: `watermeter_service.py:2293`, `requirements-docker.txt:15`
  - **Effort**: S

- **BL-27** `done` — **Config editor does not reload service**: saving config via editor only writes to disk, in-memory config is not updated until container restart
  - Plausibility thresholds, trigger mode, MQTT settings, confidence thresholds, correction rules — all stale until restart
  - ROI/model routes reload properly, general config editor does not
  - **Ref**: `routes/config.py:72-75` vs `routes/models.py:117-127`
  - **Effort**: M

- **BL-48** `idea` — **HTTP API authentication**: all write endpoints (config save, model delete, training start, set-value) accept unauthenticated requests from any LAN host. `/api/config` returns plaintext MQTT credentials. Combined with `0.0.0.0` binding, full control from any host on the network.
  - HTTP Basic Auth as minimum; token-based or session auth as stretch goal
  - **Persona**: Priya, Dave, Marcus, Sam (4 personas) — unanimous Must-Fix
  - **Ref**: all write routes, `routes/config.py` (credential exposure)
  - **Effort**: L

- **BL-49** `done` — **Fix placeholder git URL in README**: `git clone <repository-url>` is a literal placeholder in Quick Start. Four personas independently flagged this as first point of failure.
  - Replace with actual Gitea URL or GitHub mirror URL
  - **Persona**: Klaus, Priya, Sam, Linda
  - **Effort**: S

- **BL-50** `idea` — **Hardware requirements documentation**: never states that an AI-on-the-edge ESP32-CAM (or compatible IP camera) is required, that the stack is Intel x86-64 only, or what minimum CPU/RAM is needed.
  - Add "What You Need" section before Quick Start: camera hardware, x86-64 requirement, min specs, pre-trained models ship included
  - **Persona**: Klaus, Linda, Yuki, Priya, Maya, Alex (6 personas)
  - **Effort**: S

- **BL-51** `idea` — **Offline/demo mode**: setup requires a live camera feed; no way to evaluate, develop, or test against static images without hand-editing config.
  - Add demo mode with bundled test images; allow `file://` or directory-based image source
  - **Persona**: Priya, Sam, Alex, Jordan (4 personas)
  - **Effort**: L

## P2 — Important

- **BL-28** `idea` — **Persist rate history across restarts**: `rate_history` is in-memory only, lost on container restart
  - First readings after restart can't validate by rate, leak detection blind until N readings accumulate, correction engine's expected-range signal unavailable
  - Extend `StateStore` (persistence.py) to include rate_history
  - **Ref**: `persistence.py:26-28`, `watermeter_service.py:289`
  - **Effort**: S

- **BL-29** `done` — **Use label hint in labeling UI**: BL-03 mislabel rework encodes `_label=X` in filenames, labeling UI now parses and pre-fills it
  - Parse `_label=` pattern in `/api/label/next-image`, pass to frontend, pre-fill suggestion
  - **Ref**: `routes/models.py:558` (encoding), `routes/label.py:38-89` (no parsing)
  - **Effort**: S

- **BL-30** `done` — **WatermeterService god object refactoring**: 2352 lines → 1206 lines (all 4 phases complete)
  - Phase 1: image_pipeline, low_confidence, scheduling, position_utils extracted
  - Phase 2: rate_tracker, leak_detector, plausibility extracted
  - Phase 3: confirmation.py (ConfirmationManager), mqtt_publisher.py (MqttPublisher, _HA_ENTITIES), meter_state.py (MeterState) extracted
  - Phase 4: correction.py (CorrectionEngine, 6 methods: `_get_ordered_position_ids`, `_estimate_expected_range`, `_recalculate_with_replacement`, `_check_consistency_improvement`, `_check_cross_arrow_consistency`, `correct_predictions`) extracted
  - Remaining on service: process_reading, run_inference, calculate_total, manual control (set_manual_value, toggle_ha_publish, reset_previous_value), and thin delegation wrappers to all extracted components
  - **Effort**: XL

- **BL-31** `idea` — **Missing HTTP-level API tests**: 11 endpoints without HTTP-layer tests
  - `POST /api/set-value`, `POST /api/toggle-ha-publish`, `POST /api/submit-training`, `POST /api/models/{type}/{id}/activate`, `POST /api/models/{type}/{id}/archive`, `GET /api/models/{type}/{id}/logs`, `GET /api/status/html`, `POST /api/training-data/dedup`, `GET /api/training/progress/{job_id}`, `DELETE /api/training/queue/{index}`
  - **Ref**: `docs/tasks/2026_02_15_BL16_TestAudit_API.md:790-801`
  - **Effort**: L

- **BL-32** `idea` — **Correction engine does not work with regression models**: `Regressor.predict_detailed()` returns only single entry, so correction engine has zero alternatives and silently skips all arrow positions
  - Generate synthetic alternatives by perturbing regression output (+/- 1 dial position)
  - **Ref**: `inference.py:108-115`, deferred in `docs/tasks/2026_02_14_BL08_ArrowRegression.md:1246-1249`
  - **Effort**: M

- **BL-33** `idea` — **Regression confidence heuristic is semantically incorrect**: `abs(sigmoid_val - 0.5) * 2.0` conflates output magnitude with uncertainty; position 5.0 always reports low confidence regardless of actual model certainty. Affects every regression prediction.
  - Better approaches: MC Dropout, learned calibration, or ensemble variance
  - **Persona**: Alex (ML Engineer), Yuki (Edge Engineer) — flagged as Must-Fix
  - **Ref**: `inference.py:100-104`, noted in `docs/tasks/2026_02_14_BL08_ArrowRegression.md:1240`
  - **Effort**: M

- **BL-52** `idea` — **CONTRIBUTING.md + PR process documentation**: external contributors have no guidance on code style, test expectations, or review process. Backlog is rich but on-ramp is missing.
  - **Persona**: Sam (OSS Contributor), Priya (IoT Startup Dev)
  - **Effort**: S

- **BL-53** `idea` — **Rate limiting on compute-heavy endpoints**: `/api/training/start`, `/api/synthetic/generate`, `/api/trigger` can be called without throttle. A single curl loop can saturate CPU and OOM the host.
  - Simple token bucket or cooldown timer per endpoint
  - **Persona**: Dave (Security), Priya (IoT Startup Dev)
  - **Effort**: M

- **BL-54** `idea` — **HTMX error/reconnect banner**: if server becomes unreachable, dashboard stops updating with no visible indication. Users see stale data and assume system is working.
  - Add `htmx:sendError` handler showing "Dashboard offline — retrying..." banner
  - **Persona**: Maya (UX), Marcus (DevOps)
  - **Effort**: S

- **BL-55** `idea` — **Remove German-language strings from JS**: `'Auto-refresh aktiviert'` in console, `'Fehler beim Einreichen'` shown to users on error. Breaks the English UI for non-German speakers.
  - **Persona**: Maya (UX)
  - **Effort**: S

- **BL-56** `idea` — **Bundle Monaco editor locally**: Monaco loaded from CDN fails silently in LAN-only deployments. Config editor is a key feature that breaks entirely without internet — which is the expected deployment environment.
  - Self-host Monaco JS/CSS or fall back to plain textarea
  - **Persona**: Maya (UX), Klaus (HA Hobbyist)
  - **Effort**: M

- **BL-57** `idea` — **Async mislabel scan with job queue**: mislabel scan is synchronous and unbounded — blocks the API thread on large datasets. Scales poorly; thousands of images will timeout or hang the UI.
  - Run as background task with progress polling (like training jobs)
  - **Persona**: Jordan (Data Scientist), Alex (ML Engineer)
  - **Effort**: M

- **BL-58** `idea` — **Docker resource limits**: training and inference share one container with no memory ceiling. A training job can OOM the host and take down inference.
  - Add `mem_limit` and `cpus` to docker-compose; document recommended values
  - **Persona**: Marcus (DevOps), Dave (Security)
  - **Effort**: S

- **BL-59** `done` — **Fix hardcoded path in debug.sh**: the HF cache path was hardcoded to one user's home; other developers got silent bind-mount failure, re-downloading model weights on every container start.
  - Use `$HOME/.cache/huggingface` or `${HF_HOME:-$HOME/.cache/huggingface}`
  - **Ref**: `debug.sh`
  - **Persona**: Marcus (DevOps), Sam (OSS Contributor)
  - **Effort**: S

- **BL-60** `done` (superseded: aiote section removed) — **Rename `aiote` to `camera` in config and UI**: "aiote" is an abbreviation of the upstream project name that no user recognizes. Four personas were confused by it.
  - Rename config section, update all references, keep backward-compat alias during transition
  - **Persona**: Klaus, Priya, Linda, Maya (4 personas)
  - **Effort**: M

- **BL-61** `idea` — **Add HEALTHCHECK to Dockerfile**: only compose deployments get health checks. Raw `docker run`, Kubernetes, and other orchestrators see no health metadata.
  - Add `HEALTHCHECK CMD curl -f http://localhost:5000/health || exit 1`
  - **Persona**: Marcus (DevOps)
  - **Effort**: S

- **BL-62** `idea` — **CORS policy + security headers**: no X-Frame-Options or CSP headers. Clickjacking via iframe possible; injected scripts would execute. No HTTPS means credentials travel in cleartext.
  - Add CORS middleware, CSP header, X-Frame-Options
  - **Persona**: Dave (Security)
  - **Effort**: S

- **BL-63** `idea` — **Log rotation in docker-compose**: long-running instances accumulate unbounded stdout logs. `json-file` driver with `max-size`/`max-file` should be the default.
  - **Persona**: Marcus (DevOps)
  - **Effort**: S

## P3 — Nice to Have

- **BL-34** `idea` — **Readings history / trend view**: dashboard shows only most recent reading, no history or consumption trends
  - SQLite-backed history + simple chart on dashboard for standalone users (not everyone has HA)
  - **Effort**: L

- **BL-35** `idea` — **Training data export/backup**: upload via ZIP exists, but no download/backup of current ground truth
  - "Download training data" button on training page as simple backup mechanism
  - **Ref**: `routes/training.py:298-401` (upload exists, no download)
  - **Effort**: M

- **BL-36** `idea` — **CI: include regression tests**: CI only runs `tests/unit/`, skips `tests/regression/`
  - Regression tests validate important invariants (config comment preservation, NaN class labels)
  - **Ref**: `.gitea/workflows/ci.yml:43`
  - **Effort**: S

- **BL-37** `idea` — **MQTT TLS + auth support**: no TLS, no authentication — fine locally, problematic for remote brokers
  - Add config fields for TLS certs, username, password; wire env vars from docker-compose
  - **Ref**: `watermeter_service.py:2278-2306`, `docker-compose.yml:42-44`
  - **Effort**: S

- **BL-38** `idea` — **Remove deprecated Docker Compose `version` key**: `version: '3.8'` is deprecated in Compose v2, causes warnings
  - **Ref**: `docker-compose.yml:1`
  - **Effort**: S

- **BL-39** `idea` — **Inference: temp files → in-memory buffers**: `run_inference()` writes each image to temp file, then reads back via `cv2.imread()` — unnecessary disk I/O for 7+ images per cycle
  - Preprocessing could accept bytes directly
  - **Ref**: `watermeter_service.py:623-675`, `inference.py:22-30`
  - **Effort**: M

- **BL-40** `idea` — **Multi-model ensemble** (deferred from BL-05): combine regression + classification models for better accuracy
  - Running 1.0 + 0.5 + 0.1 step models — deferred as "too expensive for now"
  - **Ref**: `backlog_archiv.md` BL-05
  - **Effort**: XL

- **BL-41** `idea` — **Deeper health check**: `/health` always returns 200 OK regardless of MQTT state, model availability, or inference failures. Orchestrators report healthy when service is non-functional.
  - Add degraded states for monitoring/alerting: models loaded, camera reachable, last reading success
  - **Persona**: Marcus (DevOps), Dave (Security) — flagged as Must-Fix
  - **Ref**: `routes/service.py:215-229`
  - **Effort**: S

- **BL-64** `idea` — **INT8 quantization export path**: 2-4x inference speedup on CPU, mandatory for peak iGPU throughput. OpenVINO supports it natively.
  - Add INT8 calibration step after training using representative dataset
  - **Persona**: Yuki (Edge Engineer)
  - **Effort**: M

- **BL-65** `idea` — **Class balance visualization + confidence histograms**: guide labeling effort and reveal which classes need more data. Currently requires manual API calls.
  - Add charts to Explore & Tune page or training stats
  - **Persona**: Jordan (Data Scientist)
  - **Effort**: M

- **BL-66** `idea` — **Per-class accuracy / confusion matrix in benchmarks**: aggregate accuracy hides systematic failures (e.g., "1" vs "7" confusion).
  - Add per-class breakdown and confusion matrix to benchmark results
  - **Persona**: Alex (ML Engineer), Jordan (Data Scientist)
  - **Effort**: M

- **BL-67** `idea` — **ONNX Runtime backend for ARM deployments**: ONNX files are already exported but unused. Would unlock Raspberry Pi, Jetson, and non-Intel platforms.
  - Add ONNX Runtime as alternative inference backend alongside OpenVINO
  - **Persona**: Yuki (Edge Engineer), Priya (IoT Startup Dev)
  - **Effort**: L

- **BL-68** `idea` — **Radial dial selector for arrow labeling**: typing "6.3" is error-prone for a 100-class problem. A visual click-to-set widget would reduce labeling errors.
  - SVG or canvas-based dial picker for arrow label input
  - **Persona**: Jordan (Data Scientist)
  - **Effort**: M

- **BL-69** `idea` — **Multi-meter / fleet management**: currently single-meter only. Multiple meters require separate Docker containers with no aggregation view.
  - **Persona**: Priya (IoT Startup Dev)
  - **Effort**: XL

- **BL-70** `idea` — **Auto-train trigger when label count crosses threshold**: the active learning loop is manual at every step. An automatic trigger would close the feedback loop.
  - Configurable threshold (e.g., "train when 50 new labels accumulated")
  - **Persona**: Jordan (Data Scientist)
  - **Effort**: S

- **BL-71** `idea` — **Webhook / Prometheus metrics endpoint**: the only outbound channel is MQTT to Home Assistant. Non-HA users have no observability path beyond polling.
  - `/metrics` endpoint with Prometheus format; optional webhook on reading
  - **Persona**: Priya (IoT Startup Dev)
  - **Effort**: M

- **BL-72** `idea` — **Accessibility improvements (WCAG)**: multiple failures — skip-to-content link, focus-visible styles, accessible ROI canvas, non-color-only confidence indicators.
  - WCAG 2.4.1, 1.4.1, 4.1.3 compliance
  - **Persona**: Maya (UX)
  - **Effort**: M

- **BL-73** `idea` — **Mobile-friendly dashboard**: homeowners checking the meter on their phone is a primary use case. Current dashboard not responsive for small screens.
  - Responsive breakpoints for dashboard, labeling, and training pages
  - **Persona**: Klaus (HA Hobbyist), Linda (Homeowner)
  - **Effort**: L

- **BL-74** `idea` — **Data versioning / soft-delete for prune and mislabel operations**: destructive operations are permanent with no recovery path.
  - Move to trash/archive instead of hard-delete; allow undo
  - **Persona**: Jordan (Data Scientist)
  - **Effort**: M

- **BL-75** `idea` — **Ship a working mosquitto.conf template**: the mosquitto service is in compose but the config file must be created manually with no example provided.
  - Include `mosquitto.conf.example` with sensible defaults
  - **Persona**: Klaus (HA Hobbyist)
  - **Effort**: S

- **BL-76** `idea` — **sin/cos arrow regressor**: training uses MSE on sigmoid, so 9.9↔0.0 is not adjacent; use a 2-output sin/cos head with a circular loss and `training_mode: continuous_sincos`.
  - **Effort**: M

- **BL-77** `done` — **OpenCV arrows: detect the dial centre per crop**: superseded by BL-83. The dial (scale) centre is the wrong target: under the oblique camera the raised needle rotates around a pivot 15-20 px away from it (parallax). `arrows_mode: calibrated` calibrates the needle pivot instead.
  - **Effort**: M

- **BL-83** `done` — **Calibrated arrows mode**: `inference.arrows_mode: calibrated` reads the needle tip around the calibrated pivot and interpolates between the detected scale ticks. Calibration (ticks, pivot via needle-axis intersection plus a radial parallax model for slow dials, offsets via cross-arrow consistency) comes from the raw archive via `POST /api/arrows/calibrate` or `scripts/calibrate_arrows.py`. On 875 debug frames, the cross-arrow MAE went from 0.146 (opencv) to 0.022 held-out.
  - Open: UI button for calibration on the ROI page; recalibration hint in the dashboard when dials fall back to opencv.
  - **Ref**: `docs/plans/2026-10-04-calibrated-arrows.md`, `watermeter/arrow_calibration.py`, `watermeter/calibrated_arrows.py`
  - **Effort**: L

- **BL-84** `idea` — **Calibrated arrows: angle-dependent (harmonic) self-calibration**: after the offset fit, the cross-arrow residual still varies with the needle angle (sinusoidal, up to ~0.06-0.1 at some positions). That is a first-harmonic signature of a small pivot or needle-shape error. Fit offset + cos/sin terms per dial from the consistency residuals, only where the archive covers enough angles. With 2 days of archive (slow dials cover 1-3 of 10 sectors) the held-out gain was marginal (MAE 0.023 -> 0.021, P95 0.075 -> 0.057), so retry with 1-2 weeks of archive.
  - Caveat: the cross-arrow metric cannot see whole-unit errors on the slowest dial; verify it against digit roll-overs or history.
  - **Effort**: M

- **BL-78** `idea` — **Held-out hand-labelled test set for this meter**: digit accuracy figures are on training data; newer arrow labels are biased by OpenCV pre-labelling.
  - **Effort**: M

- **BL-79** `idea` — **Correct DATA_PROVENANCE.md**: most ground truth looks like upstream (jomjol-style) data, not self-collected.
  - **Effort**: S

- **BL-80** `idea` — **Correction engine without previous-value context**: `CorrectionEngine._recalculate_with_replacement` evaluates alternatives via `calculate_total` without `previous_value` (deliberately, so carry context doesn't mask the replacement). A NAN digit that the main path resolved from context then makes every alternative `None`, and signal 1 (rate plausibility) may mis-score. Consider passing context for digit resolution only, or skipping correction when the main total needed carry context.
  - **Ref**: `watermeter/correction.py`, `watermeter/position_utils.py`
  - **Effort**: S

- **BL-81** `idea` — **Informational notes count toward confirmation `min_warnings`**: jitter hold, re-anchor and arrow pair-consistency notes are warnings like any other, so with confirmation enabled every jitter reading can prompt the user. Consider excluding informational notes from the confirmation trigger.
  - **Ref**: `watermeter/confirmation.py` (`should_request`), `watermeter/watermeter_service.py` (`process_reading`)
  - **Effort**: S

- **BL-82** `idea` — **Mark the marker setup as optional in the UI**: with `alignment.method: features` (default for new installs) the AKAZE alignment needs only the reference image and ROIs; the two template markers serve solely as a fallback when feature alignment fails. The ROI setup/config wizard still presents marker selection as a required step. Label it "optional — fallback only" and allow finishing setup without markers.
  - **Ref**: `watermeter/routes/roi.py` (marker endpoints), `watermeter/image_pipeline.py` (`_align`), ROI/config wizard templates
  - **Effort**: S

## P4 — Someday / Maybe

- **BL-42** `idea` — **Git LFS for model files**: binary .xml/.bin/.onnx inflate clone size; relevant if default models are ever shipped in repo
  - **Effort**: M

- **BL-43** `idea` — **Learning rate warmup + early stopping**: pretrained weights receive full LR from epoch 1, risking feature extractor destabilization. No mechanism to stop when validation plateaus. Add warmup schedule + early stopping.
  - Cosine annealing or reduce-on-plateau for scheduler
  - **Persona**: Alex (ML Engineer) — flagged as Should-Fix
  - **Ref**: `training_manager.py:566`
  - **Effort**: S

- **BL-44** `idea` — **Huber loss option for arrow regression**: currently MSE only; Huber more robust to labeling noise
  - **Ref**: `training_manager.py:569`
  - **Effort**: S

- **BL-45** `idea` — **Deduplicate `_create_background_task` helper**: identical code in `app.py:31-39` and `routes/service.py:23-31`
  - **Effort**: S

- **BL-46** `idea` — **Configurable data augmentation**: training transforms hardcoded in `training_core.py:53-80`, users can't adjust intensity via UI
  - **Effort**: M

- **BL-47** `idea` — **Comprehensive API documentation**: README lists only key endpoints; full reference (45+ endpoints) exists only in Swagger `/docs`. Write dedicated API docs or improve endpoint docstrings for auto-generated docs
  - Covers: labeling, config editor, ROI config, training queue, benchmark control, model archive/logs, synthetic data, MQTT config
  - **Effort**: L
