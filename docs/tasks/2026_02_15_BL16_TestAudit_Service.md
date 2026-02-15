# BL-16 Test Audit: Service & Business Logic

## Scope

Analysis of all unit and regression tests covering service classes, business logic, algorithms, and core functionality. API route tests (`test_api_routes.py`) are handled by a separate audit. Integration tests (Docker-based) are out of scope.

Note: `test_mislabel.py` and `test_prune.py` contain both pure business logic tests AND API endpoint tests. Both categories are analyzed here since the business logic is the primary focus.

## Files Analyzed

| File | Tests | Category |
|------|-------|----------|
| `tests/unit/test_pure_functions.py` | 20 | Path safety, arrow class generation/rounding |
| `tests/unit/test_confirmation.py` | 40 | Confirmation request/response flow (BL-07) |
| `tests/unit/test_leak_detection.py` | 9 | Sustained consumption detection (BL-06) |
| `tests/unit/test_persistence.py` | 10 | StateStore save/load/clear |
| `tests/unit/test_model_manager.py` | 34 | ModelManager CRUD, activation, validation |
| `tests/unit/test_value_correction.py` | 21 | Correction engine, signals, predict_detailed (BL-04) |
| `tests/unit/test_cross_arrow_consistency.py` | 12 | Cross-arrow consistency signal (BL-05) |
| `tests/unit/test_arrow_regression.py` | 23 | Regression dataset, split, predict, Regressor, detect_mode (BL-08) |
| `tests/unit/test_trigger_mode.py` | 15 | Cyclic trigger mode, MQTT subscription |
| `tests/unit/test_config_utils.py` | 19 | YAML load/save/validate, schema |
| `tests/unit/test_image_hash.py` | 15 | dHash computation, hamming distance, HashCache |
| `tests/unit/test_marker_alignment.py` | 13 | Marker-based image alignment |
| `tests/unit/test_mislabel.py` | 29 | Mislabel scan/confirm logic + API endpoints (BL-03) |
| `tests/unit/test_prune.py` | 31 | Cluster/prune logic + API endpoints (BL-02) |
| `tests/regression/test_config_comments.py` | 3 | YAML comment preservation regression |
| `tests/regression/test_nan_class_label.py` | 1 | NAN class label regression |
| **Total** | **295** | |

## Test Execution Result

**All 295 tests PASS** (plus 17 API route tests and 5 warnings about unawaited coroutines).

Runtime: ~4 seconds. No skips, no xfails, no failures.

---

## Findings

### test_pure_functions.py (20 tests)

#### TestSafeSubpath (6 tests, lines 16-41)
- **Verdict**: good
- **Recommendation**: keep
- Tests path traversal prevention with various attack patterns (`..", absolute paths, nested traversal). Tests the real `safe_subpath` function. Good boundary coverage.

#### TestGenerateArrowClasses (6 tests, lines 60-94)
- **Verdict**: good
- **Recommendation**: keep
- Tests all valid class counts (10, 20, 50, 100), invalid input, and uniqueness. Uses real `TrainingManager` with minimal mocking (only model path).

#### TestRoundToArrowClass (8 tests, lines 97-137)
- **Verdict**: good
- **Recommendation**: keep
- Thorough coverage of rounding for all class counts, wrap-around at 10.0, invalid input, and cross-validation that rounded values are valid class labels.

---

### test_confirmation.py (40 tests)

#### TestShouldRequestConfirmation (6 tests, lines 166-234)
- **Verdict**: good
- **Recommendation**: keep
- Tests all trigger conditions: disabled mode, no triggers, warnings, low confidence, rate jump, already pending. Uses real `WatermeterService` class with bypass of `__init__`.

#### TestPublishConfirmationRequest (6 tests, lines 241-352)
- **Verdict**: good
- **Recommendation**: keep
- Tests MQTT publish format, payload fields, timer creation, no-MQTT fallback (auto-accept), and warning-status fallback. Good edge case coverage.

#### TestHandleConfirmationResponse (12 tests, lines 358-465)
- **Verdict**: good
- **Recommendation**: keep
- Comprehensive coverage of confirm/reject/correct flows. Tests value preservation, state reversion, rate history management, invalid values, unknown payloads, and no-pending edge case.

#### TestConfirmationTimeout (5 tests, lines 478-559)
- **Verdict**: good
- **Recommendation**: keep
- Tests timeout state reversion, rate history cleanup, no-op when nothing pending, event loop routing, and state_store.clear() branch when previous_value_before is None.

#### TestGetConfirmationStatus (3 tests, lines 566-608)
- **Verdict**: good, with one minor concern
- **Recommendation**: keep
- `test_seconds_remaining_decreases` (line 593) asserts `170 <= seconds_remaining <= 190`. This is a timing-dependent assertion but uses a generous 20-second window. Extremely unlikely to be flaky in practice. Acceptable.

#### TestResetClearsConfirmation (1 test, line 618)
- **Verdict**: good
- **Recommendation**: keep

#### TestMqttMessageRouting (1 test, line 638)
- **Verdict**: good
- **Recommendation**: keep
- Tests end-to-end MQTT message routing through the real `on_mqtt_message` method.

#### TestMqttConnectSubscription (2 tests, lines 674-704)
- **Verdict**: good
- **Recommendation**: keep

#### TestDisabledMode (2 tests, lines 711-725)
- **Verdict**: mildly redundant with `test_returns_none_when_disabled`
- **Recommendation**: keep -- provides clearer documentation of the disabled-mode contract, and `test_get_status_none` tests `get_confirmation_status` (different method)

#### TestProcessReadingSkipsPending (1 test, line 735)
- **Verdict**: good
- **Recommendation**: keep
- Tests the async `process_reading` path with a real asyncio lock. Validates that pending confirmation blocks new readings.

#### TestRejectWithNonePreviousValue (1 test, line 768)
- **Verdict**: good
- **Recommendation**: keep
- Tests the edge case where rejecting a reading reverts to `None` previous_value and calls `state_store.clear()` rather than `save()`.

---

### test_leak_detection.py (9 tests)

#### TestCheckSustainedConsumption (6 tests, lines 89-189)
- **Verdict**: good
- **Recommendation**: keep
- Tests insufficient history, below threshold, above threshold, mixed intervals, disabled feature, and warning message format with specific string assertions.

#### TestLeakWarningMqtt (2 tests, lines 196-249)
- **Verdict**: good
- **Recommendation**: keep
- Tests `publish_to_mqtt` with `leak_warning=True` and `False`, verifying the MQTT payload JSON structure.

#### TestLeakWarningStateManagement (3 tests, lines 256-310)
- **Verdict**: good
- **Recommendation**: keep
- Tests clearing behavior: low-rate reading clears warning, current_state integration, and `reset_previous_value()` clears leak flag.

---

### test_persistence.py (10 tests)

#### TestStateStore (10 tests, lines 12-93)
- **Verdict**: good
- **Recommendation**: keep
- Comprehensive coverage: save/load round-trip, missing file, None values, clear, clear-no-file, atomic write (no .tmp leftover), corrupted JSON, parent dir creation, precision preservation, and overwrite. All tests use `tmp_path` for isolation.

---

### test_model_manager.py (34 tests)

#### TestModelManagerValidation (7 tests, lines 31-59)
- **Verdict**: good
- **Recommendation**: keep
- Tests valid ID, path traversal variants (`../`, `/`, `\`), empty string, `.`, and invalid model type. Thorough security testing.

#### TestModelManagerCRUD (9 tests, lines 62-129)
- **Verdict**: good
- **Recommendation**: keep
- Tests list (empty, populated, newest-first ordering), ignoring non-dirs, ignoring dirs without metadata, get (existing, missing files, nonexistent), save metadata, delete (existing, nonexistent), and metadata fallback.

#### TestModelManagerActivation (8 tests, lines 132-227)
- **Verdict**: good
- **Recommendation**: keep
- Tests active model detection from config path, empty config, invalid type, non-XML model, activation with config update, classes/resolution from metadata, preservation when metadata lacks classes, and activation of nonexistent model.

#### TestModelManagerHelpers (10 tests, lines 229-271)
- **Verdict**: good
- **Recommendation**: keep
- Tests model ID generation (with/without timestamp), model path resolution, archive functionality, refresh no-op, and type isolation.

---

### test_value_correction.py (21 tests)

#### TestPredictDetailed (3 tests, lines 97-142)
- **Verdict**: good but tests a replicated algorithm, not the actual code
- **Recommendation**: keep with caveat
- The `_predict` and `_predict_detailed` functions (lines 40-57) are standalone reimplementations of the Classifier logic, NOT the actual source code. This is documented and intentional (OpenVINO unavailable on host). If the real Classifier implementation diverges from this replica, these tests would pass while the real code is broken. This is an acceptable tradeoff given the import constraints, but it should be noted.

#### TestHelperMethods (5 tests, lines 148-224)
- **Verdict**: good
- **Recommendation**: keep
- Tests real `WatermeterService` methods: `_estimate_expected_range`, `_recalculate_with_replacement`, `_get_ordered_position_ids`. Verifies exact numerical results.

#### TestConsistencyImprovement (2 tests, lines 231-280)
- **Verdict**: good
- **Recommendation**: keep
- Tests `_check_consistency_improvement` for both improvement-detected and no-improvement cases with detailed docstrings explaining the arrow-consistency logic.

#### TestSignalScoring (4 tests, lines 287-457)
- **Verdict**: good
- **Recommendation**: keep
- Tests individual correction signals (backward, forward, rate range, consistency) in isolation using `min_signal_agreement=1`. Each test documents the exact arithmetic and expected signal behavior.

#### TestCorrectionEngine (7 tests, lines 464-719)
- **Verdict**: good
- **Recommendation**: keep
- Tests the full `correct_predictions` method: disabled mode, all-confident (no correction), two-signal correction, single-signal rejection, NAN filtering, max-corrections limit, and warning string format.

---

### test_cross_arrow_consistency.py (12 tests)

#### TestCheckCrossArrowConsistency (9 tests, lines 64-241)
- **Verdict**: good
- **Recommendation**: keep
- Tests `_check_cross_arrow_consistency` directly: improvement, no-improvement, low-confidence skip, digit-predecessor skip, continuous upper/lower half, boundary at 0.5, integer lower-half, and first-position skip. Excellent boundary coverage.

#### TestCrossArrowIntegration (3 tests, lines 247-379)
- **Verdict**: good
- **Recommendation**: keep
- Tests Signal 4 through the full correction engine: basic correction, config gate respected, and transitive propagation through in-place updates.

---

### test_arrow_regression.py (23 tests)

#### TestRegressionArrowDataset (4 tests, lines 131-171)
- **Verdict**: good
- **Recommendation**: keep
- Tests dataset loading, target normalization (5.0 -> 0.5), empty dir error, and non-numeric dir skipping. Uses `pytest.importorskip('torch')` for graceful skip.

#### TestStratifiedSplitRegression (3 tests, lines 178-213)
- **Verdict**: good
- **Recommendation**: keep
- Tests that all targets appear in both splits, no overlap, and all indices covered.

#### TestRegressionPredict (4 tests, lines 220-253)
- **Verdict**: good
- **Recommendation**: keep
- Tests sigmoid midpoint, high positive/negative outputs, and range clamping.

#### TestRegressor (2 tests, lines 259-315)
- **Verdict**: good, somewhat over-mocked
- **Recommendation**: keep
- Uses `object.__new__()` to bypass OpenVINO init, mocks the compiled model. Tests return types rather than correctness. The mock setup is necessary (no OpenVINO on host) but the test primarily validates that `predict()` returns a dict with 'class'/'confidence' keys and `predict_detailed()` returns a list. Limited behavioral verification.

#### TestDetectTrainingMode (5 tests, lines 322-365)
- **Verdict**: good
- **Recommendation**: keep
- Tests metadata-based detection (continuous, discrete, absent) and filename-based fallback.

#### TestTrainingConfigValidation (3 tests, lines 372-400)
- **Verdict**: good
- **Recommendation**: keep
- Tests Pydantic model validation for training_mode field.

#### TestValidateModelConfigContinuous (2 tests, lines 407-431)
- **Verdict**: good
- **Recommendation**: keep
- Tests validation of continuous arrow model config patterns.

---

### test_trigger_mode.py (15 tests)

#### TestTriggerConfigSchema (3 tests, lines 42-58)
- **Verdict**: good
- **Recommendation**: keep
- Tests schema presence, field definitions, and optional status.

#### TestTriggerConfigParsing (3 tests, lines 61-94)
- **Verdict**: good
- **Recommendation**: keep
- Tests YAML parsing, backwards compatibility, and all three modes.

#### TestTriggerModeDefaults (1 test, line 100)
- **Verdict**: trivially passing / tests Python dict.get(), not real code
- **Recommendation**: rewrite or delete
- `test_default_trigger_mode_is_mqtt` (line 100) creates a local empty dict and calls `.get('trigger', {}).get('mode', 'mqtt')`. This tests Python's dict.get() default behavior, not any project code. The real default logic lives in `WatermeterService.__init__` (line 100 of watermeter_service.py: `self.trigger_mode = trigger_config.get('mode', 'mqtt')`). This test provides no value because it will always pass regardless of what the application does. Should be rewritten to test the actual WatermeterService initialization with a config lacking a trigger section.

#### TestCyclicLoop (6 tests, lines 112-193)
- **Verdict**: good, with one flaky risk
- **Recommendation**: keep, consider increasing timing margin
- `test_cyclic_loop_calls_process_reading` (line 116) uses 50ms interval and expects at least 2 calls in 180ms. This is timing-dependent. On a heavily loaded system, asyncio scheduling delays could reduce the call count. The 180ms / 50ms = 3.6 expected calls with min=2 is a generous margin. Low flaky risk but worth noting.
- `test_cyclic_loop_cancellation` (line 139) also uses timing (10s interval, cancel after 10ms). Relies on task being cancellable before first sleep completes. Very unlikely to be flaky.
- `test_start_cyclic_loop_creates_task` (line 154) and `test_start_cyclic_loop_noop_when_already_running` (line 166) are over-mocked: they use a fully mocked service and patch `asyncio.create_task`. They verify mock interactions rather than real behavior. Acceptable for unit tests of control flow logic.

#### TestMqttSubscriptionConditional (4 tests, lines 197-251)
- **Verdict**: good
- **Recommendation**: keep
- Tests that MQTT subscription is conditional on trigger mode (mqtt/cyclic/both) and that failed connection (rc != 0) prevents subscription.

---

### test_config_utils.py (19 tests)

#### TestLoadSaveConfig (3 tests, lines 18-47)
- **Verdict**: good
- **Recommendation**: keep
- Tests file-based round-trip, structure preservation, and file creation.

#### TestConfigString (5 tests, lines 50-78)
- **Verdict**: good
- **Recommendation**: keep
- Tests string-based load/dump, round-trip, comment preservation, and empty string.

#### TestUpdateConfig (2 tests, lines 81-110)
- **Verdict**: good
- **Recommendation**: keep
- Tests mutation via updater function, persistence, and comment preservation.

#### TestValidateConfig (7 tests, lines 113-150)
- **Verdict**: good
- **Recommendation**: keep
- Tests valid config, missing individual sections, missing multiple sections, invalid YAML syntax, and minimal valid config.

#### TestConfigSchema (2 tests, lines 153-165)
- **Verdict**: good
- **Recommendation**: keep
- Tests schema structure and JSON serialization.

---

### test_image_hash.py (15 tests)

#### TestComputeDhash (4 tests, lines 9-78)
- **Verdict**: good but over-mocked
- **Recommendation**: keep with caveat
- All tests mock `cv2.imdecode` and `cv2.resize`, then call `compute_dhash`. The hash computation on the mocked numpy arrays is real. This is necessary (cv2 is mocked in conftest) but means the preprocessing pipeline (imdecode + resize) is not tested. The hash algorithm itself IS tested.

#### TestHammingDistance (4 tests, lines 81-97)
- **Verdict**: good
- **Recommendation**: keep
- Pure function tests: identical, 1-bit diff, all-bits diff, symmetry. No mocking needed.

#### TestHashCache (7 tests, lines 100-191)
- **Verdict**: good
- **Recommendation**: keep
- Tests empty folder, add/find, threshold matching, no-duplicate-outside-threshold, persistence, stale entry pruning, and scan_and_update. Uses `tmp_path` for isolation.

---

### test_marker_alignment.py (13 tests)

#### TestFewerThanTwoMarkers (2 tests, lines 188-202)
- **Verdict**: good
- **Recommendation**: keep

#### TestMissingTemplateFiles (2 tests, lines 205-228)
- **Verdict**: good
- **Recommendation**: keep

#### TestLowConfidenceMatch (1 test, line 234)
- **Verdict**: good
- **Recommendation**: keep
- Tests fail-open behavior with random noise templates.

#### TestAlreadyAlignedImage (1 test, line 264)
- **Verdict**: good
- **Recommendation**: keep
- Uses REAL cv2 for actual image processing. Verifies near-identity transform produces low mean pixel difference.

#### TestShiftedImageCorrected (1 test, line 294)
- **Verdict**: good
- **Recommendation**: keep
- Uses REAL cv2 to apply a known translation, then verifies alignment correction. This is the most valuable test in the file -- tests actual algorithmic correctness.

#### TestTemplateCaching (1 test, line 341)
- **Verdict**: good
- **Recommendation**: keep

#### TestCacheInvalidation (2 tests, lines 369-401)
- **Verdict**: good
- **Recommendation**: keep

#### TestSearchRegionNearEdge (3 tests, lines 405-468)
- **Verdict**: good
- **Recommendation**: keep
- Tests robustness with markers near corners and oversized templates.

---

### test_mislabel.py (29 tests)

#### TestScanMislabeled (8 tests, lines 26-231)
- **Verdict**: good
- **Recommendation**: keep
- Tests empty GT, all-correct, mislabeled detection, regression tolerance for arrows, classification exact match, no active model, prediction errors, and base64 thumbnails. Properly mocks inference while testing real scan logic.

#### TestConfirmMislabeled (8 tests, lines 237-382)
- **Verdict**: good
- **Recommendation**: keep
- Tests file move, multiple files, nonexistent file, empty selection, overwrite avoidance, decimal labels, directory creation, and path traversal blocking. All use `tmp_path` for real filesystem operations.

#### TestMakeThumbnailBase64 (2 tests, lines 388-406)
- **Verdict**: good
- **Recommendation**: keep

#### TestMislabelAPIEndpoints (11 tests, lines 413-677)
- **Verdict**: good (API tests, noted for completeness)
- **Recommendation**: keep
- Tests invalid type, missing fields, scan-then-confirm workflow, path validation, scan no-active-model. These test API endpoints but involve significant business logic (stored scan state, path filtering).

---

### test_prune.py (31 tests)

#### TestClusterImagesByHash (7 tests, lines 13-81)
- **Verdict**: good
- **Recommendation**: keep
- Tests empty, single, identical, different, chain clustering, separate clusters, and all-unique. Thorough coverage of single-linkage clustering.

#### TestSelectPruneCandidates (5 tests, lines 88-143)
- **Verdict**: good
- **Recommendation**: keep
- Tests no clusters, singleton, pair, most-distinct preservation, and mixed clusters.

#### TestComputePrunePreview (4 tests, lines 150-258)
- **Verdict**: good
- **Recommendation**: keep
- Tests nonexistent directory, no duplicates, with duplicates, and median protection. Key business logic (median-based protection of thin classes).

#### TestConfirmPrune (4 tests, lines 265-369)
- **Verdict**: good
- **Recommendation**: keep
- Tests deletion, missing file error, empty preview, and multiple classes.

#### TestConfirmPrunePathTraversal (2 tests, lines 376-451)
- **Verdict**: good
- **Recommendation**: keep
- Tests filename and classname traversal attacks. Critical security tests.

#### TestHashCacheExtensions (3 tests, lines 458-494)
- **Verdict**: good
- **Recommendation**: keep

#### TestPruneAPIEndpoints (6 tests, lines 501-654)
- **Verdict**: good (API tests, noted for completeness)
- **Recommendation**: keep

---

### test_config_comments.py (3 tests)

#### Regression tests (3 tests, lines 12-39)
- **Verdict**: good
- **Recommendation**: keep
- Tests inline comment, block comment, and file round-trip comment preservation. Guards against accidental switch from ruamel.yaml to PyYAML.

---

### test_nan_class_label.py (1 test)

#### test_arrow_classes_do_not_contain_nan (line 14)
- **Verdict**: good
- **Recommendation**: keep
- Regression test verifying arrow classes are purely numeric (no 'NAN' or 'N'). Guards against a specific historical bug.

---

## Summary

| Metric | Count |
|--------|-------|
| Total tests analyzed | 295 |
| Good (keep as-is) | 292 |
| Issues found | 3 |

### Issues Found

| # | File | Test | Verdict | Recommendation |
|---|------|------|---------|----------------|
| 1 | `test_trigger_mode.py` | `test_default_trigger_mode_is_mqtt` (line 100) | **Trivially passing** | Rewrite to test actual `WatermeterService` initialization with a config missing the trigger section, rather than testing `dict.get()` defaults on a local variable |
| 2 | `test_value_correction.py` | `TestPredictDetailed` (3 tests, lines 97-142) | **Tests replicated algorithm** | Not testing real code -- tests a standalone reimplementation of the classifier's softmax+top-K logic. Keep but document that this validates the algorithm specification, not the actual `inference.py` implementation. Consider adding an integration test that exercises the real Classifier.predict_detailed() once Docker infra is available. |
| 3 | `test_trigger_mode.py` | `test_cyclic_loop_calls_process_reading` (line 116) | **Low flaky risk** | Uses timing (50ms interval, 180ms sleep, expects >= 2 calls). Very generous margin makes this unlikely to fail, but under extreme CPU pressure it could. Consider either (a) increasing the margin to >= 1 call, or (b) using a manual event/semaphore to avoid timing dependence. |

### Warnings (not issues, just notes)

1. **Unawaited coroutine warnings**: Two tests (`test_confirmation_response_routed`, `test_mqtt_mode_subscribes_to_trigger`) generate "coroutine was never awaited" warnings. These are harmless (the mocked async methods create coroutine objects that get GC'd) but could be suppressed with `pytest.mark.filterwarnings`.

2. **Module-level sys.modules manipulation**: Several test files (`test_confirmation.py`, `test_leak_detection.py`, `test_value_correction.py`, `test_cross_arrow_consistency.py`, `test_trigger_mode.py`, `test_marker_alignment.py`) manipulate `sys.modules` at module level to bypass the conftest mock and import the real `WatermeterService`. This is fragile and test-order-dependent. It works because pytest collects and imports all test modules before running any tests, and each file restores the mocks after importing the real class. However, this pattern could break if pytest changes its import ordering or if new test files are added without understanding this pattern. This is a known tradeoff, not a test quality issue.

3. **Mixed business logic + API tests**: `test_mislabel.py` and `test_prune.py` combine pure business logic tests with API endpoint tests in the same file. Consider splitting them into separate files for clearer organization if the test suite grows.

### Overall Assessment

The test suite is **high quality**. Out of 295 tests:

- **99%** are well-written, test real behavior, use appropriate mocking levels, and provide genuine value.
- **Zero tests are broken, skipped, or xfail'd.**
- **Zero tests are redundant enough to warrant deletion.**
- **One test is trivially passing** (tests Python dict.get() instead of application code).
- **One test group validates a reimplemented algorithm** rather than the actual source (justified by import constraints).
- **One test has low flaky risk** from timing dependence (generous margins mitigate this).

The dominant testing pattern -- bypassing `WatermeterService.__init__()` with `object.__new__()` and setting individual attributes -- is effective and allows testing real method implementations without Docker/OpenVINO. The fixture setup is consistent and well-documented across files.
