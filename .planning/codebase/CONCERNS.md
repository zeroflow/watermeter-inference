---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# Codebase Concerns

**Analysis Date:** 2026-10-11

## Accepted Risks (Documented Decisions, Not Bugs)

These are deliberate trade-offs recorded in `docs/decisions.md`. Do not raise them as defects or "fix" them unasked.

- **AD-01: No authentication on API endpoints.** All routes (config save, model delete, training start, `POST /api/reset`, `POST /api/trigger`, `/api/config` which returns MQTT credentials) are open to any LAN host. Revisit if the device becomes reachable from untrusted networks. Related backlog: `BL-48` (idea), `BL-62` (CORS/security headers, idea), `BL-37` (MQTT TLS + auth, idea).
- **AD-02: No SSRF protection on image-source fetch.** `POST /api/roi/image-source` (`watermeter/routes/roi.py`, lines ~117 and ~163) and `watermeter/image_pipeline.py` (lines ~154, ~186) fetch user-supplied URLs with `httpx`. Accepted only because AD-01 holds. Revisit together with AD-01.

## Tech Debt

**Oversized modules (god-object pattern persists in other files):**
- Issue: `watermeter/watermeter_service.py` (~1380 lines) was reduced from 2352 lines under `BL-30` (done), but still carries reading orchestration, state, and reset logic. `watermeter/training_manager.py` (~1495 lines), `watermeter/config_utils.py` (~680), `watermeter/arrow_calibration.py` (~656) and `watermeter/mqtt_publisher.py` (~633) are similarly large.
- Files: `watermeter/watermeter_service.py`, `watermeter/training_manager.py`, `watermeter/config_utils.py`, `watermeter/arrow_calibration.py`, `watermeter/mqtt_publisher.py`
- Impact: Changes to one concern (e.g. plausibility) require reading unrelated state; higher regression risk.
- Fix approach: Continue the BL-30 split pattern. Extract cohesive units (e.g. training job lifecycle out of `training_manager.py`, MQTT reconnect out of `mqtt_publisher.py`).

**Broad exception handling:**
- Issue: About 119 `except Exception` blocks across `watermeter/`, several of which swallow errors silently or only log them.
- Files: `watermeter/watermeter_service.py`, `watermeter/routes/*.py`, `watermeter/training_manager.py`, `watermeter/mqtt_publisher.py`
- Impact: Failures in the reading pipeline can degrade to "keep previous value" without a visible signal; debugging relies on logs.
- Fix approach: Narrow exception types at I/O boundaries (httpx, OpenVINO, MQTT, JSON). Keep broad catches only at route/task top level, and ensure they log with traceback.

**Duplicated background-task helper:**
- Issue: `_create_background_task` is implemented twice.
- Files: `watermeter/app.py` (lines ~30-39), `watermeter/routes/service.py` (lines ~23-31)
- Impact: Drift risk; the two copies already differ in how they keep task references.
- Fix approach: Tracked as `BL-45` (idea). Move to one module and import it.

**Legacy standalone scripts duplicate package logic:**
- Issue: `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py` (root) are documented as legacy but remain tracked and overlap with `watermeter/training_core.py` and `watermeter/training_manager.py`.
- Files: `train_digits.py`, `train_arrows.py`, `benchmark_digits.py`, `benchmark_arrows.py`, `benchmark_arrows.py`
- Impact: Fixes applied to the in-process pipeline are not applied to the scripts, so the two can diverge silently.
- Fix approach: Either delete them or mark them as unsupported in the README and exclude them from CI lint scope.

**Stale planning and review artifacts at repo root:**
- Issue: `review_0210_python.md`, `review_0213_structure.md`, `plan_failure_tracking.md` and `backlog_archiv.md` are committed at the root. The Feb review findings (e.g. "1796-line app.py", "stub alignment", "config written without lock") describe a code state that has since changed.
- Files: `review_0210_python.md`, `review_0213_structure.md`, `plan_failure_tracking.md`
- Impact: Readers (human or agent) may act on findings that are already fixed or no longer apply.
- Fix approach: Move to `docs/archive/` with a header noting the date, or delete once their open items are copied into `backlog.md`.

**Shallow health check:**
- Issue: `/health` returns 200 regardless of MQTT state, model availability, or pipeline status; the compose healthcheck (`docker-compose.yml`, ~line 65) depends on it.
- Files: `watermeter/app.py`, `docker-compose.yml`
- Impact: Orchestrators report healthy while the service cannot publish readings.
- Fix approach: Tracked as `BL-41` (idea). Return degraded status when pipeline is FAILED/STALE or MQTT is disconnected.

**Docker image runs as root by default:**
- Issue: `Dockerfile` sets `USER root`; the entrypoint (`docker-entrypoint.sh`) remaps to `PUID`/`PGID` and drops privileges, but the image itself has no non-root default.
- Files: `Dockerfile`, `docker-entrypoint.sh`
- Impact: Any process started before the privilege drop (or via `docker run --entrypoint`) runs as root.
- Fix approach: Document the entrypoint contract; add `HEALTHCHECK` (`BL-61`, idea) and resource limits (`BL-58`, idea).

**Committed binary model artifacts:**
- Issue: `pretrained/{digits,arrows}/*.bin|*.xml` (~14 MB) are in git history; further model files would inflate clones.
- Files: `pretrained/digits/`, `pretrained/arrows/`
- Impact: Clone size growth; history rewrites are costly (a history rewrite already happened, see memory notes).
- Fix approach: `BL-42` (idea), Git LFS, or move to a release artifact fetched by the Dockerfile.

**Orphan ground-truth gitlinks:**
- Issue: `arrows/ground_truth` and `digits/ground_truth` are in the index as gitlinks (mode 160000, i.e. submodule pointers), but there is no `.gitmodules`. `.gitignore` has `**/ground_truth/`, so the directory contents are not tracked. No labelled images are published, only two dangling commit pointers.
- Files: `arrows/ground_truth`, `digits/ground_truth`, `.gitignore`
- Impact: `git clone --recursive` / `git submodule` commands warn or fail; confusing next to the gitignore rule. `DATA_PROVENANCE.md` also needs correction (`BL-79`).
- Fix approach: `git rm --cached arrows/ground_truth digits/ground_truth` (keeps local data), or add a proper `.gitmodules` if a private submodule was intended.

## Known Bugs and Correctness Issues

**Regression confidence heuristic is wrong:**
- Symptoms: `abs(sigmoid_val - 0.5) * 2.0` is reported as confidence, so a position of 5.0 is always "low confidence" regardless of model certainty.
- Files: `watermeter/inference.py`
- Trigger: Any regression-mode arrow model.
- Workaround: None; `BL-33` (idea) tracks it.

**Correction engine ineffective for regression models:**
- Symptoms: `Regressor.predict_detailed()` returns a single entry, so correction has no alternatives and silently skips arrow positions.
- Files: `watermeter/inference.py`, `watermeter/correction.py`
- Trigger: `training_mode` regression models.
- Workaround: Use classifier arrows; tracked as `BL-32` (idea).

**Correction without carry context:**
- Symptoms: `CorrectionEngine._recalculate_with_replacement` calls `calculate_total` without `previous_value`. A NAN digit that the main path resolved via carry context makes every alternative `None`, and the rate-plausibility signal may mis-score.
- Files: `watermeter/correction.py`, `watermeter/position_utils.py`
- Trigger: NAN digit in a reading that needed carry resolution.
- Workaround: None; tracked as `BL-80` (idea).

**Informational notes trigger confirmation:**
- Symptoms: Jitter-hold, re-anchor and arrow pair-consistency notes count toward `min_warnings`, so with confirmation enabled routine readings prompt the user.
- Files: `watermeter/confirmation.py`, `watermeter/plausibility.py`
- Trigger: Confirmation flow enabled.
- Workaround: Raise `min_warnings`; tracked as `BL-81` (idea).

**Config-write races:**
- Symptoms: Config writes from several endpoints are not serialized by a shared lock; reads and writes can interleave.
- Files: `watermeter/config_utils.py`, `watermeter/routes/config.py`, `watermeter/routes/roi.py`, `watermeter/routes/mqtt.py`
- Trigger: Concurrent UI saves (e.g. ROI setup and MQTT config page).
- Workaround: Save one page at a time. Fix: funnel writes through one `asyncio.Lock` or `filelock` in `config_utils.py` (noted in the Feb review, still open per code inspection).

**Mixed-thread state mutation:**
- Symptoms: `WatermeterService.reset_previous_value()` (`watermeter/watermeter_service.py`, ~line 1099) mutates `previous_value`, rate history, and re-anchor candidates directly. It does not take `processing_lock`. Verified call sites: the async route `POST /api/reset` (`watermeter/routes/service.py:103`) runs on the event loop and can interleave with an in-flight `process_reading` at its await points (the reading then writes its result over the reset). The MQTT `on_reset` callback (`watermeter/watermeter_service.py:175`) runs on paho's network thread, which is a true cross-thread mutation.
- Files: `watermeter/watermeter_service.py`, `watermeter/routes/service.py`, `watermeter/mqtt_publisher.py`
- Trigger: Reset or trigger arriving during an in-flight reading.
- Workaround: None. Fix: route the reset through the same lock or `run_coroutine_threadsafe` as the reading path.

**Unbounded lock wait:**
- Symptoms: `processing_lock.acquire()` has no timeout, so a hung fetch (`watermeter/image_pipeline.py`) blocks all subsequent triggers indefinitely.
- Files: `watermeter/watermeter_service.py` (line ~670), `watermeter/image_pipeline.py`
- Trigger: Camera stops responding without closing the connection.
- Workaround: Restart container (not on production port 8001). Fix: `asyncio.wait_for` on lock acquisition and a fetch timeout that is checked on the pipeline status.

**MQTT reconnect guard is a plain flag:**
- Symptoms: `_reconnecting` in `watermeter/mqtt_publisher.py` (lines ~295, ~508-514) is read and set without a lock, and reconnect runs in a fresh daemon thread per disconnect.
- Files: `watermeter/mqtt_publisher.py`
- Trigger: Rapid disconnect/connect flapping.
- Workaround: None needed in practice; a `threading.Lock` would make the guard correct.

**Reconnect loop uses blocking `connect()` path:**
- Symptoms: Initial connection is synchronous; the reconnect loop sleeps in a thread, but first-connect can block startup.
- Files: `watermeter/mqtt_publisher.py`, `watermeter/app.py` (lifespan)
- Trigger: Broker unreachable at startup.
- Workaround: Compose restart policy. Fix: `connect_async()` or run in an executor.

**Session stores grow without bound:**
- Symptoms: `_prune_previews` and `_mislabel_scans` (`watermeter/routes/models.py`, lines ~360 and ~473) are `_SessionStore` instances with no TTL or size cap; each preview/scan holds full result payloads.
- Files: `watermeter/routes/models.py`
- Trigger: Repeated prune previews or mislabel scans without a confirm/cancel step.
- Workaround: Restart container. Fix: add TTL eviction and a max-entry cap.

**Mislabel scan is synchronous and unbounded:**
- Symptoms: Large datasets block the request thread and can time out.
- Files: `watermeter/routes/models.py`
- Trigger: Thousands of ground-truth images.
- Workaround: None. Tracked as `BL-57` (idea).

**Unbounded image payloads:**
- Symptoms: Base64 image fields (`watermeter/routes/service.py` ~line 40, `watermeter/routes/label.py`, `watermeter/routes/calibration.py` ~line 136) are decoded without a size limit.
- Files: `watermeter/routes/service.py`, `watermeter/routes/label.py`, `watermeter/routes/calibration.py`
- Trigger: Oversized request body.
- Workaround: None. Fix: enforce a max request size (middleware or length check before `b64decode`).

**German UI strings:**
- Symptoms: Error text such as `'Fehler beim Einreichen'` and console messages such as `'Auto-refresh aktiviert'` in `watermeter/static/*.js`.
- Files: `watermeter/static/*.js`
- Trigger: Any error in those flows.
- Workaround: None. Tracked as `BL-55` (idea).

## Security Considerations

**Plaintext MQTT credentials via API:**
- Risk: `/api/config` returns the MQTT password in plaintext to any caller (see AD-01).
- Files: `watermeter/routes/config.py`, `config.yaml`
- Current mitigation: Credentials can come from `MQTT_USERNAME`/`MQTT_PASSWORD` env vars (`.env`, gitignored). LAN-only deployment.
- Recommendations: Mask secrets in the GET response even without auth; tracked within `BL-48`.

**Public repository hygiene:**
- Risk: The repo is public on GitHub; any real IP, hostname, or home path committed would be exposed.
- Files: `config.yaml` (currently uses `192.168.x.x` placeholders), `docs/plans/*.md` (example IPs in test snippets), `.env.example`
- Current mitigation: Placeholders; `.env` gitignored.
- Recommendations: Add a pre-commit or CI grep for private IP ranges and `/home/` paths.

**No security headers or CSP:**
- Risk: Clickjacking; injected script would run with full API access.
- Files: `watermeter/app.py`, `watermeter/templates/*.html`
- Current mitigation: LAN-only.
- Recommendations: `BL-62` (idea).

**Container runs as root at start:**
- Risk: Entrypoint must correctly drop privileges; any bug there yields root in the container.
- Files: `Dockerfile`, `docker-entrypoint.sh`
- Current mitigation: Privilege drop after PUID/PGID remap.
- Recommendations: Verify drop in CI; consider `USER` default with `gosu`-style handling.

**Paths from user input:**
- Risk: Path traversal if a new route bypasses `safe_subpath()`.
- Files: `watermeter/app.py` (`safe_subpath`, line ~22), `watermeter/model_manager.py` (`_validate_model_id`)
- Current mitigation: `safe_subpath()` and `_validate_model_id()` are used on the paths reviewed; the Feb review's traversal finding is addressed.
- Recommendations: Add a unit test that every route with a path parameter calls one of the two validators.

## Performance Bottlenecks

**Multiple full-image decodes per cycle:**
- Problem: `run_inference()` writes each crop to a temp file and reads it back with `cv2.imread()`.
- Files: `watermeter/inference.py`
- Cause: Legacy file-based hand-off.
- Improvement path: `BL-39` (idea): pass in-memory arrays.

**Training blocks inference on a shared host:**
- Problem: Training runs in daemon threads inside the same process as inference (`watermeter/training_manager.py`, threads at ~lines 170, 203, 444) and the same container.
- Files: `watermeter/training_manager.py`, `docker-compose.yml`
- Cause: Single-process design; no CPU or memory ceiling.
- Improvement path: `BL-58` (resource limits, idea), `BL-53` (rate limiting on `/api/training/start`, `/api/synthetic/generate`, `/api/trigger`, idea). Production port 8001 must not be disturbed during training experiments.

**Rate history is in-memory:**
- Problem: `rate_history` resets on container restart, weakening rate-plausibility checks for a while after restart.
- Files: `watermeter/rate_tracker.py`
- Cause: No persistence.
- Improvement path: `BL-28` (idea).

**Large frontend templates:**
- Problem: `training.html` and `roi_config.html` are very large single files (tens of KB), which slows editing and review.
- Files: `watermeter/templates/training.html`, `watermeter/templates/roi_config.html`
- Cause: Inline markup and scripts.
- Improvement path: Split into partials and move scripts to `watermeter/static/`.

## Fragile Areas

**Alignment fail-closed state machine:**
- Files: `watermeter/image_pipeline.py`, `watermeter/watermeter_service.py`, `watermeter/meter_state.py`
- Why fragile: Status transitions OK → DEGRADED → FAILED → STALE depend on consecutive-failure counters and an MQTT notification on STALE. Several interacting fallbacks (feature alignment, then marker templates) make the path hard to reason about.
- Safe modification: Change one transition at a time; run `tests/unit/test_consecutive_failures.py`, `tests/unit/test_derive_pipeline_status.py`, `tests/unit/test_service_fail_closed.py`.
- Test coverage: Unit coverage is good; no real-camera regression. `BL-82` notes the marker setup is presented as required when it is only a fallback.

**Total cascade and carry logic:**
- Files: `watermeter/position_utils.py`, `watermeter/watermeter_service.py`
- Why fragile: `calculate_total` depends on the previous value; unresolvable readings are rejected, so a bad prior value can cause repeated rejections (handled by reset).
- Safe modification: Keep `tests/unit/test_calculate_total.py` and `tests/unit/test_position_utils.py` green; run `scripts/replay_cascade.py` on archived frames.
- Test coverage: Unit only; no end-to-end replay in CI.

**Arrow calibration and the pivot model:**
- Files: `watermeter/arrow_calibration.py`, `watermeter/calibrated_arrows.py`, `watermeter/calibration_session.py`, `scripts/calibrate_arrows.py`
- Why fragile: Calibration depends on archive coverage (slow dials cover few sectors). Harmonic residual is unmodelled (`BL-84`, idea), and a calibration without a measured pivot is refused.
- Safe modification: Recalibrate only from the raw archive; check cross-arrow MAE before and after.
- Test coverage: `tests/unit/test_arrow_calibration.py`, `tests/unit/test_calibrated_arrows.py`, `tests/unit/test_calibration_*.py`.

**Config round-trip via ruamel.yaml:**
- Files: `watermeter/config_utils.py`, `config.yaml`
- Why fragile: Comments must survive writes; new fields must be added to the validation schema or they are rejected or dropped.
- Safe modification: Add fields to the schema first; run `tests/regression/test_config_comments.py`.
- Test coverage: Regression test exists; not in CI (`BL-36`).

**Model hot-reload:**
- Files: `watermeter/model_manager.py`, `watermeter/inference.py` (`_inference_service` global, line ~619), `watermeter/model_manager.py` (`_model_manager` global, line ~332)
- Why fragile: Module-level singletons are replaced on reload; references held elsewhere go stale. Earlier review noted backward-compat aliases.
- Safe modification: Always access through `get_inference_service()` / `get_model_manager()`.
- Test coverage: `tests/unit/test_model_manager.py`.

**Module-level singletons and globals:**
- Files: `watermeter/watermeter_service.py` (`service`, line ~1377), `watermeter/training_manager.py` (line ~1487), `watermeter/routes/synthetic.py` (`_generation_status`, line ~44), `watermeter/routes/calibration.py` (`_calibration_lock`, line ~34)
- Why fragile: Global mutable state complicates tests and makes ordering of startup important (lifespan in `watermeter/app.py`).
- Safe modification: Reset singletons in test fixtures (`tests/unit/conftest.py`).

## Scaling Limits

**Single meter per container:**
- Current capacity: One meter, one camera, one MQTT topic set.
- Limit: Multiple meters need separate containers with no aggregation view.
- Scaling path: `BL-69` (idea).

**Single processing lane:**
- Current capacity: One `process_reading` at a time (asyncio lock).
- Limit: Triggers queue behind a slow fetch or inference (see unbounded lock wait above).
- Scaling path: Bounded queue with timeout and drop policy.

## Dependencies at Risk

**Paho-MQTT v2 API:**
- Risk: Migration (`BL-26`) is done, but `on_connect`/`on_disconnect` signatures in `watermeter/mqtt_publisher.py` depend on the v2 callback API.
- Impact: Upgrade to a future major version breaks connection handling.
- Migration plan: Pin and track in `requirements.txt`; dependabot is configured in `.github/dependabot.yml`.

**OpenVINO and PyTorch in one image:**
- Risk: Large image; training stack (PyTorch, timm, ONNX) inflates runtime for inference-only deployments.
- Impact: Image size and CVE surface.
- Migration plan: Split training into a separate image or extra (`requirements-docker.txt`); ONNX Runtime backend option is `BL-67` (idea).

## Missing Critical Features

**Deeper health and observability:**
- Problem: Only polling and MQTT expose state; no metrics endpoint.
- Blocks: Alerting when readings stop or diverge.
- Reference: `BL-41`, `BL-71`.

**Data recovery:**
- Problem: Prune and mislabel operations are permanent.
- Blocks: Safe cleanup of ground truth.
- Reference: `BL-74` (idea), `BL-35` (no backup download).

**Offline demo / hardware requirements:**
- Problem: Setup requires a live camera; hardware requirements are not documented.
- Blocks: Evaluation without hardware.
- Reference: `BL-51`, `BL-50`.

## Test Coverage Gaps

**HTTP-layer tests:**
- What's not tested: 11 endpoints lack HTTP-level tests (`BL-31`); `tests/unit/test_api_routes.py` covers only part of the surface.
- Files: `watermeter/routes/*.py`
- Risk: Route-level validation regressions go unnoticed.
- Priority: High

**Regression tests not in CI:**
- What's not tested: `tests/regression/` is skipped by CI, which runs `tests/unit/` only (`.github/workflows/ci.yml`, mirrored in `.gitea/workflows/ci.yml`).
- Files: `tests/regression/test_config_comments.py`, `tests/regression/test_nan_class_label.py`
- Risk: NAN-label and config-comment regressions merge unnoticed.
- Priority: High (`BL-36`)

**Concurrency paths:**
- What's not tested: `reset_previous_value` vs in-flight reading, lock timeouts, MQTT reconnect races.
- Files: `watermeter/watermeter_service.py`, `watermeter/mqtt_publisher.py`
- Risk: Intermittent state corruption under load.
- Priority: Medium

**End-to-end replay:**
- What's not tested: Full reading pipeline on archived frames runs only through `scripts/replay_*.py` manually.
- Files: `scripts/replay_alignment_compare.py`, `scripts/replay_cascade.py`, `scripts/replay_marker_confidence.py`, `scripts/replay_plausibility.py`
- Risk: Threshold changes (e.g. `alignment.marker_confidence_threshold`, see `docs/decisions.md`) are validated only by hand.
- Priority: Medium

**Held-out labels:**
- What's not tested: Digit accuracy is reported on training data; newer arrow labels are biased by OpenCV pre-labelling.
- Files: `digits/`, `arrows/` (ground truth), `DATA_PROVENANCE.md`
- Risk: Overstated accuracy; silent regressions after retraining.
- Priority: High (`BL-78`, `BL-79`)

**Benchmark per-class visibility:**
- What's not tested: No per-class accuracy or confusion matrix (`BL-66`).
- Files: `benchmark_digits.py`, `benchmark_arrows.py`
- Risk: Systematic confusions such as 1 vs 7 hidden by aggregate accuracy.
- Priority: Low

---

*Concerns audit: 2026-10-11*
