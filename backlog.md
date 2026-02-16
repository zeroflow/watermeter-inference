# Backlog

Format: `BL-{id}` | status: `idea` → `planned` → `in-progress` → `done`

Next ID: BL-47

See `backlog_archiv.md` for completed items (BL-01 through BL-25).

---

## P1 — Critical

- **BL-26** `idea` — **Paho-MQTT v2 API migration**: code uses v1 API (`mqtt.Client(client_id=...)`) but v2 is installed; runs on deprecated compat layer that breaks in v3
  - Add `CallbackAPIVersion` parameter, update callback signatures (5 args)
  - Wire up MQTT authentication (username/password) — `docker-compose.yml` mentions env vars but code never reads them
  - **Ref**: `watermeter_service.py:2293`, `requirements-docker.txt:15`
  - **Effort**: S

- **BL-27** `idea` — **Config editor does not reload service**: saving config via editor only writes to disk, in-memory config is not updated until container restart
  - Plausibility thresholds, trigger mode, MQTT settings, confidence thresholds, correction rules — all stale until restart
  - ROI/model routes reload properly, general config editor does not
  - **Ref**: `routes/config.py:72-75` vs `routes/models.py:117-127`
  - **Effort**: M

## P2 — Important

- **BL-28** `idea` — **Persist rate history across restarts**: `rate_history` is in-memory only, lost on container restart
  - First readings after restart can't validate by rate, leak detection blind until N readings accumulate, correction engine's expected-range signal unavailable
  - Extend `StateStore` (persistence.py) to include rate_history
  - **Ref**: `persistence.py:26-28`, `watermeter_service.py:289`
  - **Effort**: S

- **BL-29** `idea` — **Use label hint in labeling UI**: BL-03 mislabel rework encodes `_label=X` in filenames, but labeling UI never parses or pre-fills it
  - Parse `_label=` pattern in `/api/label/next-image`, pass to frontend, pre-fill suggestion
  - **Ref**: `routes/models.py:558` (encoding), `routes/label.py:38-89` (no parsing)
  - **Effort**: S

- **BL-30** `idea` — **WatermeterService god object refactoring**: 2352 lines, ~50 methods in single class
  - Handles image pipeline, plausibility, correction, MQTT, HA discovery, confirmation, persistence, scheduling
  - Split into focused modules: `image_pipeline.py`, `plausibility.py`, `correction.py`, `mqtt_publisher.py`, `confirmation.py`
  - **Effort**: XL

- **BL-31** `idea` — **Missing HTTP-level API tests**: 11 endpoints without HTTP-layer tests
  - `POST /api/set-value`, `POST /api/toggle-ha-publish`, `POST /api/submit-training`, `POST /api/models/{type}/{id}/activate`, `POST /api/models/{type}/{id}/archive`, `GET /api/models/{type}/{id}/logs`, `GET /api/status/html`, `POST /api/training-data/dedup`, `GET /api/training/progress/{job_id}`, `DELETE /api/training/queue/{index}`
  - **Ref**: `docs/tasks/2026_02_15_BL16_TestAudit_API.md:790-801`
  - **Effort**: L

- **BL-32** `idea` — **Correction engine does not work with regression models**: `Regressor.predict_detailed()` returns only single entry, so correction engine has zero alternatives and silently skips all arrow positions
  - Generate synthetic alternatives by perturbing regression output (+/- 1 dial position)
  - **Ref**: `inference.py:108-115`, deferred in `docs/tasks/2026_02_14_BL08_ArrowRegression.md:1246-1249`
  - **Effort**: M

- **BL-33** `idea` — **Regression confidence heuristic is weak**: `abs(sigmoid_val - 0.5) * 2.0` means edge values (0, 9.9) always appear confident, mid-range (4-6) always uncertain, regardless of actual model certainty
  - Better approaches: MC Dropout, learned calibration, or ensemble variance
  - **Ref**: `inference.py:100-104`, noted in `docs/tasks/2026_02_14_BL08_ArrowRegression.md:1240`
  - **Effort**: M

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

- **BL-41** `idea` — **Deeper health check**: `/health` only checks service running + MQTT connected, not models loaded, camera reachable, or last reading success
  - Add degraded states for monitoring/alerting
  - **Ref**: `routes/service.py:215-229`
  - **Effort**: S

## P4 — Someday / Maybe

- **BL-42** `idea` — **Git LFS for model files**: binary .xml/.bin/.onnx inflate clone size; relevant if default models are ever shipped in repo
  - **Effort**: M

- **BL-43** `idea` — **Learning rate scheduler**: fixed `1e-3` Adam; cosine annealing or reduce-on-plateau would improve convergence
  - **Ref**: `training_manager.py:566`
  - **Effort**: S

- **BL-44** `idea` — **Huber loss option for arrow regression**: currently MSE only; Huber more robust to labeling noise
  - **Ref**: `training_manager.py:569`
  - **Effort**: S

- **BL-45** `idea` — **Deduplicate `_create_background_task` helper**: identical code in `app.py:31-39` and `routes/service.py:23-31`
  - **Effort**: S

- **BL-46** `idea` — **Configurable data augmentation**: training transforms hardcoded in `training_core.py:53-80`, users can't adjust intensity via UI
  - **Effort**: M
