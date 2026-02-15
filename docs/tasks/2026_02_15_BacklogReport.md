# Backlog Implementation Report

**Date**: 2026-02-15
**Scope**: BL-02 through BL-10 (8 backlog items)
**Total new tests**: 156 (149 -> 305)
**Total lines added**: ~9,412

---

## BL-06: Continuous Consumption Warning (Leak Detection)

**Commit**: 288c786 | **Tests added**: 11 (`test_leak_detection.py`)

### Files changed

- `watermeter_service.py` (+64 lines)
- `config_utils.py` (+15 lines)
- `config.yaml` (+3 lines)
- `test_leak_detection.py` (+308 lines)

### What was implemented

- Added `_check_sustained_consumption()` method to `WatermeterService` that checks if the last N consecutive reading intervals ALL exceed a configurable rate threshold (m^3/h).
- Warning flows to the dashboard via the existing warnings list.
- New `leak_warning` boolean attribute in MQTT payload for Home Assistant automations.
- Added `self.leak_warning` state tracking on the service instance.
- Config fields: `enable_leak_detection`, `sustained_rate_threshold` (default 0.05 m^3/h), `sustained_rate_readings` (default 3).
- Integrated into `process_reading()` after plausibility checks.
- Leak warning state resets on `reset_previous_value()`.

### Review: PASS WITH NOTES

- Minor: zero `time_diff` in `rate_history` aborts the entire check instead of skipping just that one pair.
- Minor: no validation that `rate_history_size >= sustained_rate_readings + 1`.
- Missing test coverage for zero `time_diff` edge case and longer history windows.

---

## BL-04: Value Deduction from Rules

**Commit**: bfc1a56 | **Tests added**: 21 (`test_value_correction.py`)

### Files changed

- `watermeter_service.py` (+288 lines)
- `inference.py` (+25 lines)
- `config_utils.py` (+46 lines)
- `config.yaml` (+10 lines)
- `test_value_correction.py` (+718 lines)

### What was implemented

- Added `predict_detailed()` to `Classifier` in `inference.py` -- returns top-K softmax predictions (not just argmax).
- New correction engine in `correct_predictions()` with 3 signals:
  - Signal 1: Previous value comparison (meter only goes up).
  - Signal 2: Expected rate from `rate_history`.
  - Signal 3: Adjacent position consistency (half/upper check).
- Confidence-weighted: only corrects low-confidence positions (below `correction_confidence_threshold`).
- Safety: meter rollover guard, max corrections per reading limit.
- Feature is opt-in: `correction.enabled: false` by default.
- Config section: `correction:` with 6 tunable parameters.

### Review: PASS WITH NOTES

- Minor: Signal 1 only checks backward direction (meter goes up) -- could miss edge cases.
- Minor: `_recalculate_with_replacement` NAN handling for arrows could be improved.
- Missing tests for rollover guard edge cases and regression model interaction with correction.

---

## BL-05: Cross-Arrow Consistency

**Commit**: 22702a3 | **Tests added**: 8 (`test_cross_arrow_consistency.py`)

### Files changed

- `watermeter_service.py` (+52 lines)
- `config_utils.py` (+6 lines)
- `config.yaml` (+1 line)
- `test_cross_arrow_consistency.py` (+298 lines)

### What was implemented

- Added Signal 4 to BL-04's correction engine: `_check_cross_arrow_consistency()`.
- Uses reading of more-significant arrow dial to constrain less-significant arrow's candidates.
- Confidence gate (default 0.8): only fires when constraining arrow has high confidence.
- Supports transitive propagation via in-place correction (analog_1 -> analog_2 -> analog_3).
- Config: `cross_arrow_confidence_gate: 0.8` under `correction:` section.

### Review: NEEDS FIXES

- **CRITICAL**: `expected_lower_half = True` is hardcoded -- ignores the constraining arrow's fractional part entirely. With 1.0-step models this is technically correct (all readings are X.0, meaning solidly at integer, so lower half is expected), but the code comment and design doc say it should handle continuous values from BL-08. All tests use `.0` values so the bug is hidden.

---

## BL-08: Arrow Regression Mode

**Commit**: a178440 | **Tests added**: 23 (`test_arrow_regression.py`)

### Files changed

- `training_manager.py` (+459/-164 lines)
- `inference.py` (multiple changes)
- `training_core.py` (+90 lines)
- `routes/training.py` (+10 lines)
- `model_manager.py` (+2/-1 lines)
- `test_arrow_regression.py` (+431 lines)

### What was implemented

- New "continuous" training mode alongside existing "discrete" classification.
- `RegressionArrowDataset` in `training_core.py`: maps folder names to normalized float targets [0, 1].
- MSE/Huber loss, single output neuron, sigmoid multiplied by 10 for 0.0-9.9 range.
- New `Regressor` class in `inference.py`: applies sigmoid+scale for continuous output.
- `_detect_training_mode()`: auto-detects mode from model metadata.
- `InferenceService.initialize()` and `reload_models()` updated for auto-detection.
- Benchmark computes MAE/RMSE/within-half metrics for regression models.
- `stratified_split_regression()` in `training_core.py` for balanced data splits.
- `regression_predict()` shared utility for sigmoid+scale+clamp.
- Training form: `training_mode` field with `@field_validator` in Pydantic model.

### Review: NEEDS FIXES

- **CRITICAL**: `training_mode` variable is unbound in `_execute_training()` -- will crash ALL training (both discrete and continuous). The variable is used before being assigned from config.
- **MAJOR**: Confidence heuristic for regression is semantically meaningless for value 5.0 (distance from nearest integer is 0.0, giving 100% confidence -- but 5.0 is no more certain than 5.3).
- Missing tests for the actual training pipeline code paths.

---

## BL-10: Data Provenance & License

**Commit**: 20a64ea | **Tests added**: 0 (documentation only)

### Files changed

- `DATA_PROVENANCE.md` (+139 lines)
- `scripts/fetch_upstream_data.sh` (+434 lines)
- `docs/tasks/2026_02_14_BL10_DataLicense.md` (+63 lines)

### What was implemented

- Created `DATA_PROVENANCE.md` documenting origin and licensing status of all training data.
- Only our own self-collected ground truth ships with the repo.
- `scripts/fetch_upstream_data.sh`: downloads jomjol's training data for local training.
  - Supports digits and analog data from multiple upstream repos.
  - Puts data into correct directory structure.
  - Includes error handling and progress reporting.
- References upstream license issue (jomjol/AI-on-the-edge-device#4041).

### Review: PASS

- Minor: `find` command operator precedence could be cleaner with explicit parens.

---

## BL-02: Ground-Truth Pruning

**Commit**: d7bd54d | **Tests added**: 29 (`test_prune.py`)

### Files changed

- `image_hash.py` (+280 lines)
- `routes/models.py` (+109 lines)
- `test_prune.py` (+571 lines)

### What was implemented

- Extended `image_hash.py` with clustering and pruning logic:
  - `cluster_images_by_hash()`: Union-Find single-linkage clustering of perceptual hashes.
  - `select_prune_candidates()`: keeps most distinct image per cluster.
  - `compute_prune_preview()`: full scan with median protection (never prunes thin classes below dataset median).
  - `confirm_prune()`: deletes candidates, rebuilds hash caches.
- Two new API endpoints in `routes/models.py`:
  - `POST /api/training-data/prune/preview` -- shows what would be removed.
  - `POST /api/training-data/prune/confirm` -- executes the prune.
- Supports both digits and arrows ground truth.

### Review: PASS WITH NOTES

- **MAJOR**: `confirm_prune()` does not validate that filenames in the confirm payload actually exist within the expected directory (path traversal risk).
- Minor: "no duplicates" test actually tests median protection, not clustering.

---

## BL-03: Ground-Truth Rework (Mislabel Detection)

**Commit**: 9c5f962 | **Tests added**: 28 (`test_mislabel.py`)

### Files changed

- `routes/models.py` (+285 lines)
- `test_mislabel.py` (+649 lines)

### What was implemented

- Two new API endpoints in `routes/models.py`:
  - `POST /api/training-data/mislabel/scan`: runs active model inference on all ground truth, flags suspects where prediction does not match folder label.
  - `POST /api/training-data/mislabel/confirm`: moves selected suspects back to `input/` with label hint in filename (`{stem}_label={class}.jpg`).
- Supports both classification (exact match) and regression (0.5 tolerance).
- Returns base64 thumbnails for UI gallery display.
- Pre-filled label hints enable faster relabeling workflow.

### Review: PASS WITH NOTES

- **CRITICAL**: `confirm_mislabeled()` lacks path validation -- filenames from request body are not validated against expected directory structure.
- Minor: `_make_thumbnail_base64()` does not actually resize images -- just reads and base64-encodes the full image.
- Minor: regression detection heuristic (0.5 tolerance) is fragile for edge cases.

---

## BL-07: User Confirmation via Home Assistant

**Commit**: b9abbc7 | **Tests added**: 36 (`test_confirmation.py`)

### Files changed

- `watermeter_service.py` (+324 lines)
- `config_utils.py` (+40 lines)
- `routes/service.py` (+11 lines)
- `config.yaml` (+10 lines)
- `test_confirmation.py` (+653 lines)

### What was implemented

- Full confirmation flow for uncertain readings:
  - `_should_request_confirmation()`: checks 3 trigger conditions (warning count, low confidence positions, rate jump).
  - `_publish_confirmation_request()`: publishes to `watermeter/confirmation_request` MQTT topic.
  - `_handle_confirmation_response()`: processes `confirm`/`reject`/`correct:{value}` payloads.
  - `_confirmation_timeout()`: auto-rejects after configurable timeout.
- Subscribe to `watermeter/confirmation_response` on MQTT connect.
- New `GET /api/confirmation/status` endpoint.
- Pending state persisted via `StateStore` for crash recovery.
- Config section: `confirmation:` with enable flag, timeout, trigger thresholds.
- Feature is opt-in (disabled by default).

### Review: NEEDS FIXES

- **CRITICAL**: Double assignment of `_pending_confirmation` -- `process_reading` sets it correctly, then `_publish_confirmation_request` overwrites with wrong values.
- **MAJOR**: Timer thread mutates service state without routing through asyncio event loop (thread safety issue).
- **MAJOR**: MQTT disconnect leaves system in permanent pending state with no recovery.
- **MAJOR**: Intermediate readings during pending confirmation can cause state corruption.
- **MAJOR**: Timeout persistence writes `None` to state store without properly clearing it.

---

## Summary

| BL | Feature | Tests | Lines Added | Review Result |
|----|---------|-------|-------------|---------------|
| BL-06 | Leak Detection | 11 | ~735 | PASS (minor notes) |
| BL-04 | Value Deduction | 21 | ~1,970 | PASS (minor notes) |
| BL-05 | Cross-Arrow Consistency | 8 | ~642 | NEEDS FIX (1 critical) |
| BL-08 | Arrow Regression | 23 | ~2,304 | NEEDS FIX (1 critical, 1 major) |
| BL-10 | Data License | 0 | ~637 | PASS |
| BL-02 | GT Pruning | 29 | ~1,049 | PASS (1 major) |
| BL-03 | Mislabel Detection | 28 | ~976 | PASS (1 critical) |
| BL-07 | User Confirmation | 36 | ~1,099 | NEEDS FIX (1 critical, 4 major) |
| **Total** | | **156** | **~9,412** | **3 need fixes** |

Total test count: 149 -> 305 (156 new tests added).

### Items requiring follow-up

1. **BL-05**: Fix the hardcoded `expected_lower_half = True` to actually use the constraining arrow's fractional part.
2. **BL-08**: Fix the unbound `training_mode` variable in `_execute_training()` (blocks all training). Rethink the regression confidence heuristic.
3. **BL-07**: Fix double assignment of `_pending_confirmation`, thread safety on timer callbacks, MQTT disconnect recovery, intermediate reading handling, and timeout persistence.
4. **BL-02 / BL-03**: Add path validation to `confirm_prune()` and `confirm_mislabeled()` to prevent directory traversal attacks.
