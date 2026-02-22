# Full Codebase Review — 2026-02-22

> Team-based multi-perspective review of the entire watermeter-inference codebase.
> Reviewers: security-hawk, perf-nerd, clean-coder (all Sonnet, orchestrated by Opus coordinator)

## Executive Summary

| Category | HIGH | MEDIUM | LOW | Total |
|----------|------|--------|-----|-------|
| Security | 2 | 2 | 2 | 6 |
| Performance | 3 | 6 | 2 | 11 |
| Clean Code | 6 | 5 | 3 | 14 |
| **Total** | **11** | **13** | **7** | **31** |

**FIX-NOW items (trivial, applied immediately):** 18 items across 4 groups — committed to `claude/main`.

**Key themes across reviewers:**
- The `WatermeterService` god object (1206 lines) and its test-compat shims are the single biggest maintainability issue (clean-coder + perf-nerd agree)
- No authentication on any endpoint is the top security risk
- Blocking I/O in async routes is the top performance risk
- Fisheye correction code is duplicated between `image_pipeline.py` and `routes/roi.py`

---

## Findings by Severity

### HIGH

#### SEC-1: SSRF — Arbitrary internal network requests
- **Source:** security-hawk
- **File:** `watermeter/routes/roi.py:181-235`
- **What:** `POST /api/roi/image-source` validates URL only for non-empty scheme + hostname, then fetches via httpx. No blocklist for loopback, RFC-1918, link-local, or the production container at port 8001.
- **Impact:** Attacker can read production config (including MQTT credentials), probe cloud metadata endpoints, scan internal network.
- **Fix:** Block reserved/private IP ranges before fetching. Allowlist `http`/`https` schemes only. Set `follow_redirects=False`.

#### SEC-2: Zero authentication on all API endpoints
- **Source:** security-hawk
- **File:** `watermeter/app.py:145` / all routes
- **What:** No auth middleware, no API keys, no session cookies, no CORS restrictions. Every endpoint is open to any network peer.
- **Impact:** Anyone with LAN access can read config (incl. MQTT creds), delete models, upload malicious ZIPs, exfiltrate training images, pivot via SSRF.
- **Fix:** Add HTTP Basic Auth or static API token via FastAPI `Depends()`. Or bind to localhost + reverse proxy.

#### PERF-1: Disk round-trip on every inference cycle
- **Source:** perf-nerd
- **File:** `watermeter/watermeter_service.py:370-422`
- **What:** `run_inference()` creates temp dir, writes each ROI image to disk, then `Classifier.preprocess()` reads it back via `cv2.imread()`. Full encode-write-read-decode cycle for every ROI every cycle.
- **Impact:** ~5-50ms extra I/O per ROI per cycle. 6 unnecessary disk round-trips per reading on a typical meter.
- **Fix:** Add `predict_from_bytes(image_bytes)` method to Classifier/Regressor using `cv2.imdecode`. Eliminate temp files.

#### PERF-2: Blocking file I/O on the async event loop (stats loop)
- **Source:** perf-nerd
- **File:** `watermeter/scheduling.py:77-78`
- **What:** `publish_training_stats()` performs multiple `Path.glob()` scans synchronously from an asyncio task.
- **Impact:** Stalls all HTTP handling and cyclic trigger for duration of directory scan (every 5 minutes).
- **Fix:** `await loop.run_in_executor(None, self._stats_fn)`.

#### PERF-3: Heavy OpenCV ops block the event loop
- **Source:** perf-nerd
- **File:** `watermeter/watermeter_service.py:674`
- **What:** `process_whole_image()` runs `cv2.undistort`, `cv2.warpAffine`, `cv2.matchTemplate` synchronously inside async `process_reading()`.
- **Impact:** 50-200ms event loop blockage every reading cycle.
- **Fix:** `images = await loop.run_in_executor(None, self.process_whole_image, whole_image)`.

#### CC-1: Test-driven backward-compat shims bloat the god object
- **Source:** clean-coder + perf-nerd (merged)
- **File:** `watermeter/watermeter_service.py:173-533`
- **What:** ~180 lines of lazy-init machinery (`_ensure_state`, `_ensure_mqtt_stub`, `_ensure_confirmation_manager`, inline `_MeterStateAdapter`) exist purely to support tests using `object.__new__()`.
- **Impact:** Maintenance burden, divergent fallback paths, production code paying for test scaffolding.
- **Fix:** Change test fixtures to proper mocking (`patch __init__` or `create_autospec`). Remove `_ensure_*` methods.

#### CC-2: Fisheye correction code duplicated
- **Source:** clean-coder + perf-nerd (merged)
- **File:** `watermeter/image_pipeline.py:113-119` + `watermeter/routes/roi.py:79-96`
- **What:** Identical `cv2.undistort` logic in two places. Camera matrix rebuilt every frame (perf-nerd).
- **Impact:** DRY violation. If fisheye math changes, two locations need updating. Unnecessary allocations per frame.
- **Fix:** Extract to shared `watermeter/image_utils.py`. Cache camera matrix as instance variable.

#### CC-3: Four identical Pydantic ROI models
- **Source:** clean-coder
- **File:** `watermeter/routes/roi.py:34-73`
- **What:** `MarkerBox`, `DigitRoi`, `SingleRoiSubmission`, `AnalogRoi` all have `x, y, width, height` fields.
- **Impact:** DRY violation. Adding a field means updating four models.
- **Fix:** Define single `RoiBounds(BaseModel)` and reuse.

#### CC-4: _execute_training is 440 lines with 10 parameters
- **Source:** clean-coder
- **File:** `watermeter/training_manager.py:441-884`
- **What:** Monolithic function covering dataset prep, model creation, training loop, validation, export, metadata, registration.
- **Impact:** Impossible to test individual phases. Hard to navigate.
- **Fix:** Extract `_prepare_dataset`, `_build_model`, `_train_epochs`, `_export_and_register` sub-methods. Accept `job` only.

#### CC-5: Business logic in routes layer (mislabel scanning)
- **Source:** clean-coder
- **File:** `watermeter/routes/models.py:435-579`
- **What:** `scan_mislabeled` (80 lines) and `confirm_mislabeled` (60 lines) run full inference pipelines in the routes layer.
- **Impact:** Can't unit-test without route context. Business logic entangled with HTTP.
- **Fix:** Move to `watermeter/mislabel_detector.py`, leave routes as thin wrappers.

#### CC-6: Module-level mutable state as session store
- **Source:** clean-coder
- **File:** `watermeter/routes/models.py:306,419` + `routes/synthetic.py:20`
- **What:** `_prune_previews`, `_mislabel_scans`, `_generation_status` — module-level dicts as cross-request state.
- **Impact:** Not thread-safe. Server restart loses state. Hidden state machine.
- **Fix:** Move to service class with proper locking, or use TTLCache.

---

### MEDIUM

#### SEC-3: Plaintext credentials exposed via unauthenticated API
- **Source:** security-hawk
- **File:** `watermeter/routes/config.py:39`, `watermeter/routes/mqtt.py:57`
- **What:** `GET /api/config` and `GET /api/mqtt/config` return raw config including plaintext MQTT passwords.
- **Impact:** Full credential exfiltration. Compounded by lack of auth (SEC-2).
- **Fix:** Scrub credential fields before returning, or enforce auth first.

#### SEC-4: Zip bomb — unconstrained extraction to disk
- **Source:** security-hawk
- **File:** `watermeter/routes/training.py:357-360`
- **What:** `zipfile.extractall()` with no total extraction size check. 50MB zip can expand to 50GB+.
- **Impact:** Disk exhaustion, denial of service.
- **Fix:** Sum `member.file_size` from `zf.infolist()` before extraction, reject if > threshold.

#### PERF-4: get_position_ids() re-parsed every cycle
- **Source:** perf-nerd
- **File:** `watermeter/watermeter_service.py:713`
- **What:** Config doesn't change between readings, but position IDs are recomputed every cycle.
- **Fix:** Cache in `__init__`, invalidate in `reload_config()`.

#### PERF-5: New HashCache per ground_truth dir per save
- **Source:** perf-nerd
- **File:** `watermeter/low_confidence_capture.py:78-87`
- **What:** Every low-confidence save creates fresh HashCache objects, loading JSON from disk for each class dir.
- **Impact:** 10 class dirs = 10 JSON loads per low-confidence image per cycle.
- **Fix:** Cache HashCache instances by directory path.

#### PERF-6: Camera matrix arrays rebuilt every frame
- **Source:** perf-nerd (merged with CC-2)
- **File:** `watermeter/image_pipeline.py:115-119`
- **Fix:** Cache as instance variables, invalidate when k1 or dimensions change.

#### PERF-7: Hot-path property walks _ensure_confirmation_manager() every cycle
- **Source:** perf-nerd
- **File:** `watermeter/watermeter_service.py:535-543`
- **What:** `_pending_confirmation` property calls `_ensure_confirmation_manager()` with `hasattr` check on every access.
- **Fix:** Part of CC-1 — eliminate test-compat shims.

#### PERF-8: Blocking file I/O in submit_for_training route
- **Source:** perf-nerd
- **File:** `watermeter/routes/service.py:168-188`
- **What:** Sync `f.write(image_data)` in async route handler.
- **Fix:** `await loop.run_in_executor(None, filepath.write_bytes, image_data)` or make route non-async.

#### PERF-9: Blocking cv2 work in ROI preview/wizard routes
- **Source:** perf-nerd
- **File:** `watermeter/routes/roi.py:386-407` and multiple other handlers
- **What:** `cv2.imread`, `cv2.undistort`, `cv2.imencode` synchronous in async handlers.
- **Fix:** Wrap in `run_in_executor` or make routes synchronous `def` (FastAPI auto-threads them).

#### CC-7: Deferred circular import in confirmation._do_timeout
- **Source:** clean-coder
- **File:** `watermeter/confirmation.py:233-246`
- **What:** `from . import watermeter_service` inside method body — circular import hidden at call time.
- **Fix:** Pass `publish_fn` as parameter (same pattern as `handle_response`).

#### CC-8: Duplicated Classifier/Regressor preprocess methods
- **Source:** clean-coder
- **File:** `watermeter/inference.py:22-30`, `69-78`
- **What:** Identical 7-line preprocess body in both classes.
- **Fix:** Extract module-level `_preprocess_image()` function.

#### CC-9: Raw `request: dict` bypasses FastAPI validation
- **Source:** clean-coder
- **File:** `watermeter/routes/roi.py:181`, `routes/models.py:315,371,588,638`
- **What:** Multiple routes accept untyped `dict` instead of Pydantic models.
- **Impact:** No automatic validation, no OpenAPI schema, errors surface as 500s.
- **Fix:** Define Pydantic models for each endpoint.

#### CC-10: _recalculate_with_replacement diverges from calculate_total
- **Source:** clean-coder
- **File:** `watermeter/correction.py:49-78`
- **What:** Uses its own formula that floors arrow values to int, while `position_utils.calculate_total` uses different rounding.
- **Impact:** Corrections may evaluate against wrong hypothetical totals.
- **Fix:** Refactor to share calculation logic with `position_utils.calculate_total`.

#### CC-11: rate_history property exposes private internals
- **Source:** clean-coder
- **File:** `watermeter/watermeter_service.py:270-283`
- **What:** Returns `_rate_tracker._history` directly; setter bypasses `_trim()` invariant.
- **Fix:** Remove property, update tests to use `RateTracker.add()`/`seed()`.

---

### LOW

#### SEC-5: XSS risk in JS onclick context
- **Source:** security-hawk
- **File:** `watermeter/templates/status_fragment.html:100,115`
- **What:** `pred.id`, `pred.model`, `pred.image_base64` interpolated into `onclick` handlers. Currently safe (values from config, not user-controlled), but fragile.
- **Fix:** Use `data-*` attributes + `dataset` in JS, or `tojson` filter.

#### SEC-6: MQTT test endpoint as internal network scanner
- **Source:** security-hawk
- **File:** `watermeter/routes/mqtt.py:182`
- **What:** Connects to arbitrary broker:port. Connection timing reveals open/closed/filtered ports.
- **Fix:** Restrict to configured broker, or apply IP blocklist.

#### PERF-10: Unbounded log list in TrainingJob
- **Source:** perf-nerd
- **File:** `watermeter/training_manager.py:53-58`
- **What:** `self.logs.append()` grows forever during long training runs.
- **Fix:** Cap at 10,000 entries.

#### PERF-11: purge_duplicates() blocks lifespan startup
- **Source:** perf-nerd
- **File:** `watermeter/app.py:72-84`
- **What:** Sync dedup scan during startup delays health check availability.
- **Fix:** Run in background task or executor.

#### CC-12: Misleading alignment debug log
- **Source:** clean-coder
- **File:** `watermeter/image_pipeline.py:280-283`
- **What:** Logs X-coordinates of marker centers but labels them as "confidence".
- **Fix:** Log actual confidence values or remove label.

#### CC-13: Inconsistent return type in _load_corrected_reference()
- **Source:** clean-coder
- **File:** `watermeter/routes/roi.py:99-129`
- **What:** Returns `None` or `(img, height, width)`. Five callers repeat same unpacking pattern.
- **Fix:** Return named tuple or raise exception.

#### CC-14: Unnecessary double calculate_total call
- **Source:** clean-coder
- **File:** `watermeter/watermeter_service.py:683-689`
- **What:** `calculate_total` called before and after `correct_predictions`. First result discarded if corrections occur.
- **Fix:** Restructure to call once after correction.

---

## What's Clean (security-hawk commendation)

- `safe_subpath()` in `app.py:22-27` — correct path traversal guard, used consistently
- `ModelManager._validate_model_id()` — blocks path traversal in model IDs
- `ruamel.yaml` / `yaml.safe_load` throughout — no unsafe YAML deserialization
- `confirm_mislabeled()` — explicit `is_relative_to()` check
- `confirm_prune()` — explicit path traversal check
- No subprocess calls with user-controlled input
- No `|safe` Jinja2 filters — autoescape active
- MQTT password not logged; env-var override is correct pattern

---

## Suggested Priority Order

1. **SEC-2** (Authentication) — foundational, blocks all other security fixes from being meaningful
2. **SEC-1** (SSRF) — exploitable from LAN without auth
3. **PERF-3** (Event loop blocking in process_reading) — affects every reading cycle
4. **PERF-1** (Disk round-trip) — affects every reading cycle
5. **CC-1** (God object shims) — biggest maintainability win, unblocks other refactoring
6. **SEC-4** (Zip bomb) — easy to exploit once found
7. **CC-2** (Fisheye duplication) + **PERF-6** (camera matrix caching) — do together
8. **PERF-2** (Stats loop blocking) — affects responsiveness every 5 min
9. **CC-4** (_execute_training refactoring) — testability win
10. Everything else by severity

---

*Generated by team-based code review on 2026-02-22. Reviewers: security-hawk, perf-nerd, clean-coder.*
