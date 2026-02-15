# BL-16 Test Audit: API & Routes

## Files Analyzed

### Unit Tests (with mocked services, run without Docker)
- `tests/unit/test_api_routes.py` (15 tests)
- `tests/unit/test_model_manager.py` (23 tests)
- `tests/unit/test_config_utils.py` (14 tests)
- `tests/unit/test_persistence.py` (10 tests)
- `tests/unit/test_pure_functions.py` (18 tests)
- `tests/unit/test_confirmation.py` (27 tests)
- `tests/unit/test_trigger_mode.py` (14 tests)
- `tests/unit/test_leak_detection.py` (9 tests)
- `tests/unit/test_value_correction.py` (13 tests)
- `tests/unit/test_cross_arrow_consistency.py` (11 tests)
- `tests/unit/test_arrow_regression.py` (17 tests)
- `tests/unit/test_image_hash.py` (12 tests)
- `tests/unit/test_marker_alignment.py` (10 tests)
- `tests/unit/test_mislabel.py` (21 tests -- 8 pure logic, 13 HTTP)
- `tests/unit/test_prune.py` (19 tests -- 12 pure logic, 7 HTTP)

### Regression Tests
- `tests/regression/test_nan_class_label.py` (1 test)
- `tests/regression/test_config_comments.py` (3 tests)

### Integration Tests (real Docker container)
- `tests/integration/test_smoke.py` (5 tests)
- `tests/integration/test_models.py` (6 tests)
- `tests/integration/test_config.py` (4 tests)
- `tests/integration/test_training.py` (3 tests)
- `tests/integration/test_benchmark.py` (3 tests)

### Supporting Fixtures
- `tests/conftest.py` -- root-level fixtures and CLI options
- `tests/unit/conftest.py` -- mock modules, `test_client`, `mock_service`
- `tests/integration/conftest.py` -- Docker container lifecycle, `api` client

---

## Findings

---

### tests/unit/test_api_routes.py

#### TestConfigEndpoints.test_get_config (line 16)
- **Verdict**: good
- **Reason**: Tests the GET /api/config endpoint at the HTTP level. Verifies status 200, success flag, and content contains expected key. Uses `test_client` which has a real config.yaml written to tmp_path -- the route reads from disk, so this exercises real path through config_utils.
- **Recommendation**: keep

#### TestConfigEndpoints.test_get_config_schema (line 24)
- **Verdict**: good
- **Reason**: Tests GET /api/config/schema.json. Verifies the schema has expected top-level keys. This calls real `config_utils.get_config_schema()` through the route, exercising actual code.
- **Recommendation**: keep

#### TestConfigEndpoints.test_save_valid_config (line 31)
- **Verdict**: good
- **Reason**: Tests POST /api/config/save with valid YAML. Exercises the full save flow: validation, parsing, writing to disk. The route writes to `config.yaml` in `tmp_path` (set by monkeypatch.chdir).
- **Recommendation**: keep

#### TestConfigEndpoints.test_save_invalid_yaml (line 45)
- **Verdict**: good
- **Reason**: Tests rejection of syntactically invalid YAML. Verifies 400 status and success=False.
- **Recommendation**: keep

#### TestConfigEndpoints.test_save_missing_required_sections (line 54)
- **Verdict**: good
- **Reason**: Tests that YAML missing required sections (images, mqtt, inference) is rejected with appropriate error message.
- **Recommendation**: keep

#### TestTrainingStatus.test_training_status (line 68)
- **Verdict**: good (appropriately mocked)
- **Reason**: Tests GET /api/training/status. Mocks `get_training_manager` at the correct level (the route module, not the class itself). Verifies response structure (training, benchmark, queue keys). The mock is appropriate because TrainingManager has heavy dependencies and this test validates route-level response shaping.
- **Recommendation**: keep

#### TestModelEndpoints.test_list_models_by_type (line 88)
- **Verdict**: good
- **Reason**: Tests GET /api/models?model_type=digits. Mocks ModelManager at route module level. Verifies response structure. Appropriate mocking for a unit test.
- **Recommendation**: keep

#### TestModelEndpoints.test_get_model_details (line 103)
- **Verdict**: good
- **Reason**: Tests GET /api/models/digits/model_a. Verifies model details are returned correctly.
- **Recommendation**: keep

#### TestModelEndpoints.test_get_model_not_found (line 116)
- **Verdict**: good
- **Reason**: Tests 404 response when model doesn't exist. Important edge case.
- **Recommendation**: keep

#### TestModelEndpoints.test_delete_model (line 126)
- **Verdict**: good
- **Reason**: Tests DELETE /api/models/digits/model_a. Verifies success response.
- **Recommendation**: keep

#### TestLabelValidation.test_invalid_model_type (line 147)
- **Verdict**: good
- **Reason**: Tests that an invalid model_type (not "digits" or "arrows") returns 400. Creates the necessary file structure so the test reaches the model_type validation logic.
- **Recommendation**: keep

#### TestLabelValidation.test_digits_valid_labels (line 161)
- **Verdict**: good
- **Reason**: Parametric test of valid digit labels (0-9, NAN). Creates actual files in tmp_path and exercises the full label submission flow including file move.
- **Recommendation**: keep

#### TestLabelValidation.test_digits_reject_invalid (line 180)
- **Verdict**: good
- **Reason**: Tests rejection of invalid digit labels (10, -1, nan, N, abc, empty string). This is a regression test for the NAN-vs-N bug. Creates the source file so it reaches the label validation.
- **Recommendation**: keep

#### TestLabelValidation.test_arrows_decimal_labels (line 194)
- **Verdict**: good
- **Reason**: Tests valid arrow decimal labels (0.0, 1.5, 5.3, 9.9). Exercises the real parsing logic.
- **Recommendation**: keep

#### TestLabelValidation.test_arrows_legacy_integer_labels (line 210)
- **Verdict**: good
- **Reason**: Tests backward-compatible integer-to-decimal conversion (12 -> 1.2). Verifies the response contains the converted label.
- **Recommendation**: keep

#### TestLabelValidation.test_arrows_reject_out_of_range (line 226)
- **Verdict**: good
- **Reason**: Tests rejection of out-of-range arrow labels (10.0, -1.0, abc).
- **Recommendation**: keep

#### TestLabelValidation.test_file_not_found (line 240)
- **Verdict**: good
- **Reason**: Tests 404 when the input image doesn't exist. Does NOT create the file, verifying the not-found path.
- **Recommendation**: keep

---

### tests/unit/test_model_manager.py

All 23 tests in this file test `ModelManager` directly (not through HTTP routes) using a real filesystem (`tmp_path`). These are pure unit tests of business logic.

#### TestModelManagerValidation (lines 31-59, 7 tests)
- **Verdict**: good
- **Reason**: Tests path traversal protection (`..`, `/`, `\`, `.`, empty string), valid IDs, and invalid model types. These are security-critical validations.
- **Recommendation**: keep all

#### TestModelManagerCRUD (lines 62-129, 9 tests)
- **Verdict**: good
- **Reason**: Tests list/get/save/delete with real filesystem operations. Covers edge cases like empty directories, stray files, missing metadata, missing model files (files_exist=False).
- **Recommendation**: keep all

#### TestModelManagerActivation (lines 132-226, 7 tests)
- **Verdict**: good
- **Reason**: Tests model activation logic including config file updates, class/resolution propagation from metadata, preservation of existing config when metadata lacks classes, and failure on nonexistent models. Uses real YAML file operations.
- **Recommendation**: keep all

#### TestModelManagerHelpers (lines 229-271, 7 tests)
- **Verdict**: good
- **Reason**: Tests helper methods (create_model_id, get_model_path, archive_model, refresh, type isolation). All use real filesystem.
- **Recommendation**: keep all

---

### tests/unit/test_mislabel.py

#### TestScanMislabeled (lines 26-231, 8 tests)
- **Verdict**: good
- **Reason**: Tests `scan_mislabeled()` directly (not through HTTP). Mocks only the inference service (which requires OpenVINO), but uses real filesystem for ground truth directories. Covers: empty GT, all correct, mislabeled detection, regression arrow tolerance, classification exact match, no active model, prediction errors, base64 thumbnails. The mocking is appropriate -- inference is the only thing that can't run on the host.
- **Recommendation**: keep all

#### TestConfirmMislabeled (lines 237-382, 9 tests)
- **Verdict**: good
- **Reason**: Tests `confirm_mislabeled()` with real filesystem operations. Covers: file move, multiple files, nonexistent files, empty selection, overwrite avoidance, decimal labels, directory creation, path traversal blocking. No mocking needed -- this is pure file logic.
- **Recommendation**: keep all

#### TestMakeThumbnailBase64 (lines 388-406, 2 tests)
- **Verdict**: good
- **Reason**: Tests helper function with real and missing files.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_scan_invalid_type (line 416)
- **Verdict**: good
- **Reason**: Tests 400 response for invalid model type via HTTP.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_invalid_type (line 425)
- **Verdict**: good
- **Reason**: Tests 400 for invalid type on confirm endpoint.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_missing_selected (line 433)
- **Verdict**: good
- **Reason**: Tests 400 when 'selected' field is missing. Clears stale state before testing.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_without_scan (line 445)
- **Verdict**: good
- **Reason**: Tests 409 when confirming without a prior scan. Important workflow validation.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_empty_selection (line 457)
- **Verdict**: good
- **Reason**: Tests early return for empty selection list (200 with zero moves).
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_rejects_paths_not_in_scan (line 468)
- **Verdict**: good
- **Reason**: Tests 400 when selected paths don't match the scan results. Important security check.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_scan_endpoint_returns_structure (line 486)
- **Verdict**: good
- **Reason**: End-to-end HTTP test of scan endpoint. Sets up real GT files, mocks only inference, verifies response JSON structure.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_scan_stores_result_for_confirm (line 525)
- **Verdict**: good
- **Reason**: Verifies scan stores results in module-level dict for subsequent confirm. Tests internal state contract.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_full_scan_then_confirm_workflow (line 562)
- **Verdict**: good
- **Reason**: Full two-step workflow test (scan then confirm). Verifies file is actually moved on disk. Excellent integration-style test at the unit level.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_confirm_clears_scan (line 612)
- **Verdict**: good
- **Reason**: Verifies scan state is cleared after confirm, preventing replay. Second confirm fails with 409.
- **Recommendation**: keep

#### TestMislabelAPIEndpoints.test_scan_no_active_model (line 652)
- **Verdict**: good
- **Reason**: Tests 409 when no inference model is loaded.
- **Recommendation**: keep

---

### tests/unit/test_prune.py

#### TestClusterImagesByHash (lines 13-81, 7 tests)
- **Verdict**: good
- **Reason**: Pure logic tests for hash clustering. No mocking. Covers empty, single, identical, different, chain clustering, separate clusters, all unique.
- **Recommendation**: keep all

#### TestSelectPruneCandidates (lines 88-141, 4 tests)
- **Verdict**: good
- **Reason**: Pure logic tests for candidate selection from clusters. No mocking.
- **Recommendation**: keep all

#### TestComputePrunePreview (lines 150-258, 4 tests)
- **Verdict**: good
- **Reason**: Tests the full preview computation including median protection. Uses real filesystem with hash caches.
- **Recommendation**: keep all

#### TestConfirmPrune (lines 265-369, 4 tests)
- **Verdict**: good
- **Reason**: Tests actual file deletion. Covers candidates, missing files (error counting), empty preview, multiple classes.
- **Recommendation**: keep all

#### TestConfirmPrunePathTraversal (lines 376-451, 2 tests)
- **Verdict**: good
- **Reason**: Security tests for path traversal in filenames and class names. Verifies files outside gt_base are not deleted.
- **Recommendation**: keep all

#### TestPruneAPIEndpoints.test_preview_invalid_type (line 504)
- **Verdict**: good
- **Reason**: Tests 400 for invalid type parameter.
- **Recommendation**: keep

#### TestPruneAPIEndpoints.test_confirm_invalid_type (line 514)
- **Verdict**: good
- **Reason**: Tests 400 for invalid type on confirm.
- **Recommendation**: keep

#### TestPruneAPIEndpoints.test_confirm_without_preview (line 522)
- **Verdict**: good
- **Reason**: Tests 409 when confirming without prior preview. Important workflow guard.
- **Recommendation**: keep

#### TestPruneAPIEndpoints.test_preview_returns_structure (line 536)
- **Verdict**: good
- **Reason**: Tests HTTP response structure for preview endpoint with real GT files.
- **Recommendation**: keep

#### TestPruneAPIEndpoints.test_preview_then_confirm (line 568)
- **Verdict**: good
- **Reason**: Full two-step workflow test. Verifies files are actually deleted on disk and correct count remains.
- **Recommendation**: keep

#### TestPruneAPIEndpoints.test_confirm_clears_preview (line 625)
- **Verdict**: good
- **Reason**: Verifies preview state is cleared after confirm, second confirm fails with 409.
- **Recommendation**: keep

---

### tests/unit/test_config_utils.py

All 14 tests test `config_utils.py` functions directly (no HTTP). They use real YAML operations with `ruamel.yaml`.

#### TestLoadSaveConfig (lines 17-47, 3 tests)
- **Verdict**: good
- **Reason**: Round-trip, structure preservation, and file creation tests.
- **Recommendation**: keep all

#### TestConfigString (lines 50-78, 5 tests)
- **Verdict**: good
- **Reason**: String-based config load/dump/round-trip, comment preservation, empty string handling.
- **Recommendation**: keep all

#### TestUpdateConfig (lines 81-110, 2 tests)
- **Verdict**: good
- **Reason**: Tests atomic update-in-place with comment preservation.
- **Recommendation**: keep all

#### TestValidateConfig (lines 113-150, 5 tests)
- **Verdict**: good
- **Reason**: Tests validation of required sections, missing sections (individual and multiple), and invalid YAML syntax.
- **Recommendation**: keep all

#### TestConfigSchema (lines 153-165, 2 tests)
- **Verdict**: good
- **Reason**: Tests JSON schema structure and serialization.
- **Recommendation**: keep all

---

### tests/unit/test_persistence.py

All 10 tests test `StateStore` directly. No mocking, uses real filesystem.

- **Verdict**: good (all 10)
- **Reason**: Thorough coverage of save/load/clear lifecycle, edge cases (no file, None values, corrupted file, nested directories, precision, overwrite, atomic write).
- **Recommendation**: keep all

---

### tests/unit/test_pure_functions.py

#### TestSafeSubpath (lines 17-41, 6 tests)
- **Verdict**: good
- **Reason**: Tests the `safe_subpath` security function used by label and service routes. Covers simple join, single part, traversal attacks (dotdot, deep, absolute path), and the borderline case of `a/..` resolving within base.
- **Recommendation**: keep all

#### TestGenerateArrowClasses (lines 60-94, 6 tests)
- **Verdict**: good
- **Reason**: Tests arrow class generation for 10/20/50/100 class counts plus invalid count and uniqueness.
- **Recommendation**: keep all

#### TestRoundToArrowClass (lines 97-136, 6 tests)
- **Verdict**: good
- **Reason**: Tests rounding logic for all supported class counts, wrap-around at 10.0, and invalid count. The `test_round_class_is_in_generated_classes` cross-validates rounding against generation -- excellent.
- **Recommendation**: keep all

---

### tests/unit/test_confirmation.py

All 27 tests test `WatermeterService` confirmation logic by bypassing `__init__` and setting attributes directly. This is a valid approach since `__init__` requires MQTT, config.yaml, etc.

#### TestShouldRequestConfirmation (5 tests)
- **Verdict**: good
- **Reason**: Tests the trigger condition evaluator: disabled returns None, no triggers returns None, warnings trigger, low confidence triggers (with threshold count), single low confidence below threshold doesn't trigger, rate jump triggers, already-pending suppresses.
- **Recommendation**: keep all

#### TestPublishConfirmationRequest (6 tests)
- **Verdict**: good
- **Reason**: Tests MQTT publishing: correct topic, required payload fields, timer start, no-MQTT auto-accept behavior (both ok and warning status), and that _publish does not overwrite pending state.
- **Recommendation**: keep all

#### TestHandleConfirmationResponse (10 tests)
- **Verdict**: good
- **Reason**: Tests all three response types (confirm, reject, correct) plus edge cases (invalid correct value, unknown payload, no pending). Verifies state changes, value reversions, rate history manipulation, HA publish scheduling.
- **Recommendation**: keep all

#### TestConfirmationTimeout (5 tests)
- **Verdict**: good
- **Reason**: Tests timeout auto-reject: state reversion, rate history pop, no-op when nothing pending, event loop routing, state_store.clear() when previous_value_before is None.
- **Recommendation**: keep all

#### TestGetConfirmationStatus (3 tests)
- **Verdict**: good
- **Reason**: Tests the status query method: None when no pending, details when pending, seconds_remaining calculation.
- **Recommendation**: keep all

#### TestResetClearsConfirmation (1 test)
- **Verdict**: good
- **Reason**: Tests that reset_previous_value clears pending confirmation and timer.
- **Recommendation**: keep

#### TestMqttMessageRouting (1 test)
- **Verdict**: good
- **Reason**: Tests that MQTT messages on the confirmation response topic are correctly dispatched.
- **Recommendation**: keep

#### TestMqttConnectSubscription (2 tests)
- **Verdict**: good
- **Reason**: Tests conditional MQTT subscription (enabled vs disabled).
- **Recommendation**: keep

#### TestDisabledMode (2 tests)
- **Verdict**: good
- **Reason**: Verifies completely transparent pass-through when confirmation is disabled.
- **Recommendation**: keep

#### TestProcessReadingSkipsPending (1 test)
- **Verdict**: good
- **Reason**: Tests that process_reading returns early when confirmation is pending. Uses `asyncio.Lock()` -- the only async test in this file.
- **Recommendation**: keep

#### TestRejectWithNonePreviousValue (1 test)
- **Verdict**: good
- **Reason**: Tests the edge case where reject reverts to None and calls state_store.clear() instead of save.
- **Recommendation**: keep

---

### tests/unit/test_trigger_mode.py

#### TestTriggerConfigSchema (3 tests)
- **Verdict**: good
- **Reason**: Tests that the config schema includes trigger section with correct enum values and constraints.
- **Recommendation**: keep all

#### TestTriggerConfigParsing (3 tests)
- **Verdict**: good
- **Reason**: Tests YAML parsing of trigger config, backward compatibility (missing trigger section), and all three mode values.
- **Recommendation**: keep all

#### TestTriggerModeDefaults.test_default_trigger_mode_is_mqtt (line 100)
- **Verdict**: trivially passing
- **Reason**: This test does `config = {}; mode = config.get('trigger', {}).get('mode', 'mqtt')` and asserts `mode == 'mqtt'`. It tests Python's `dict.get()` default value behavior, not any application code. No application function is called.
- **Recommendation**: rewrite to test actual application default behavior (e.g., how WatermeterService resolves trigger_mode from an empty config), or delete

#### TestCyclicLoop (6 tests)
- **Verdict**: good
- **Reason**: Tests the async cyclic loop (calls process_reading, cancellation, task creation, noop when already running, task cancellation). The timing-based test (`test_cyclic_loop_calls_process_reading`) uses generous margins (50ms interval, 180ms wait, expecting >= 2 calls) which reduces flakiness.
- **Recommendation**: keep all. Note: `test_cyclic_loop_calls_process_reading` is inherently timing-sensitive but the margins are generous enough to be reliable.

#### TestMqttSubscriptionConditional (4 tests)
- **Verdict**: good
- **Reason**: Tests that MQTT trigger subscription is conditional on trigger mode. Covers all three modes plus failed connection.
- **Recommendation**: keep all

---

### tests/unit/test_leak_detection.py

All 9 tests test `WatermeterService._check_sustained_consumption()` and related leak detection behavior.

#### TestCheckSustainedConsumption (6 tests)
- **Verdict**: good
- **Reason**: Tests detection logic: insufficient history, rate below threshold, all above threshold, one interval below, feature disabled, warning message format. All use real rate_history data with precise calculations.
- **Recommendation**: keep all

#### TestLeakWarningMqtt (2 tests)
- **Verdict**: good (with minor concern)
- **Reason**: Tests MQTT payload includes leak_warning flag. The mocking of mqtt_client is appropriate. One minor concern: `test_leak_warning_in_mqtt_payload` (line 200) parses the payload using two different extraction methods (`call_args[1]['json']` or `call_args[0][1]`) suggesting the author wasn't sure of the exact call signature. This could silently pass if the assertion extracts from the wrong location, but in practice the fallback works.
- **Recommendation**: keep, but consider standardizing the payload extraction

#### TestLeakWarningStateManagement (3 tests)
- **Verdict**: good
- **Reason**: Tests that leak warning clears when rate drops, is reflected in current_state, and clears on reset.
- **Recommendation**: keep all

---

### tests/unit/test_value_correction.py

All 13 tests test the value correction engine.

#### TestPredictDetailed (3 tests)
- **Verdict**: good
- **Reason**: Tests standalone implementations of predict/predict_detailed that replicate the Classifier algorithm. The approach of reimplementing the algorithm in tests is explicitly documented and justified (OpenVINO not available on host).
- **Recommendation**: keep all

#### TestHelperMethods (4 tests)
- **Verdict**: good
- **Reason**: Tests _estimate_expected_range, _recalculate_with_replacement (digit and arrow), and _get_ordered_position_ids. All use precise mathematical expectations.
- **Recommendation**: keep all

#### TestConsistencyImprovement (2 tests)
- **Verdict**: good
- **Reason**: Tests _check_consistency_improvement with both positive and negative cases.
- **Recommendation**: keep all

#### TestSignalScoring (4 tests)
- **Verdict**: good
- **Reason**: Tests individual correction signals in isolation (min_signal_agreement=1). Covers backward, forward (no trigger), rate range, and adjacent consistency signals.
- **Recommendation**: keep all

#### TestCorrectionEngine (6 tests)
- **Verdict**: good
- **Reason**: Tests the full correct_predictions() method: disabled, all confident, two signals, single signal below threshold, NAN filtering, max corrections limit, and warning string format. Thorough coverage of the most complex business logic.
- **Recommendation**: keep all

---

### tests/unit/test_cross_arrow_consistency.py

All 11 tests test the cross-arrow consistency signal.

#### TestCheckCrossArrowConsistency (9 tests)
- **Verdict**: good
- **Reason**: Tests _check_cross_arrow_consistency directly with various scenarios: improvement (lower half), no improvement (already consistent), low confidence gate, digit predecessor (skipped), continuous values (upper/lower/boundary), integer values, and first position (no predecessor). Very thorough edge case coverage.
- **Recommendation**: keep all

#### TestCrossArrowIntegration (3 tests)
- **Verdict**: good
- **Reason**: Tests Signal 4 through the full correction engine. Covers: signal fires as correction, config gate blocks signal, and transitive propagation (correcting one arrow enables correcting the next).
- **Recommendation**: keep all

---

### tests/unit/test_arrow_regression.py

#### TestRegressionArrowDataset (4 tests)
- **Verdict**: good
- **Reason**: Tests the regression dataset class with real PIL images. Covers loading, target normalization, empty dir error, and non-numeric dir skipping.
- **Recommendation**: keep all

#### TestStratifiedSplitRegression (3 tests)
- **Verdict**: good
- **Reason**: Tests stratified splitting: all targets in both splits, no overlap, full coverage.
- **Recommendation**: keep all

#### TestRegressionPredict (4 tests)
- **Verdict**: good
- **Reason**: Tests the sigmoid-based prediction function at boundary conditions: midpoint, high positive, high negative, clamping.
- **Recommendation**: keep all

#### TestRegressor (2 tests)
- **Verdict**: good (appropriately mocked)
- **Reason**: Tests the Regressor class by bypassing __init__ and mocking the compiled OpenVINO model. This is the only way to test the predict/predict_detailed interface without OpenVINO. The mock is minimal (only the compiled model and preprocess), and the rest of the logic runs for real.
- **Recommendation**: keep both

#### TestDetectTrainingMode (5 tests)
- **Verdict**: good
- **Reason**: Tests metadata-based detection (continuous, discrete, absent) and filename-based fallback (continuous pattern, discrete pattern). Uses real filesystem.
- **Recommendation**: keep all

#### TestTrainingConfigValidation (3 tests)
- **Verdict**: good
- **Reason**: Tests the Pydantic model validation for training_mode field. This is route-adjacent (tests the request model used by POST /api/training/start).
- **Recommendation**: keep all

#### TestValidateModelConfigContinuous (2 tests)
- **Verdict**: conditional (may skip)
- **Reason**: Tests validate_model_config with continuous filename patterns. Has `autouse` fixture that skips if the function isn't available from inference.py. Good when it runs, but may silently skip if the module loading fails.
- **Recommendation**: keep, but consider logging when skipped so CI notices

---

### tests/unit/test_image_hash.py

All 12 tests test the image_hash module.

#### TestComputeDhash (4 tests)
- **Verdict**: good (appropriately mocked)
- **Reason**: Mocks cv2 (not available on host) but uses real numpy arrays for hash computation. Tests determinism, different images, invalid images, and bit width.
- **Recommendation**: keep all

#### TestHammingDistance (4 tests)
- **Verdict**: good
- **Reason**: Pure function tests: identical, one bit diff, all bits diff, symmetry. No mocking needed.
- **Recommendation**: keep all

#### TestHashCache (6 tests)
- **Verdict**: good
- **Reason**: Tests cache lifecycle: empty folder, add/find, near-duplicate threshold, no-duplicate outside threshold, persistence, stale entry pruning, scan_and_update.
- **Recommendation**: keep all

---

### tests/unit/test_marker_alignment.py

All 10 tests test marker-based image alignment using real cv2 and numpy.

- **Verdict**: good (all 10)
- **Reason**: This file does sophisticated sys.modules management to import the real cv2 (removing the conftest mock) while keeping other heavy deps mocked. Tests cover: fewer than 2 markers, missing templates, unreadable templates, low confidence match (random noise), identity alignment, shift correction, template caching, cache invalidation, edge markers (corners), and oversized templates. The tests use actual image processing operations.
- **Recommendation**: keep all

---

### tests/regression/test_nan_class_label.py

#### test_arrow_classes_do_not_contain_nan (line 14)
- **Verdict**: good
- **Reason**: Regression test for the NAN-vs-N bug. Tests all supported num_classes values (10, 20, 50, 100). Uses real TrainingManager with patched ModelManager (appropriate -- ModelManager tries to create /app/models/).
- **Recommendation**: keep

---

### tests/regression/test_config_comments.py

#### test_inline_comment_preserved (line 12)
- **Verdict**: good
- **Reason**: Regression test for ruamel.yaml comment preservation. Tests inline comments survive load/dump.
- **Recommendation**: keep

#### test_block_comment_preserved (line 19)
- **Verdict**: good
- **Reason**: Tests that block comments survive load/dump.
- **Recommendation**: keep

#### test_comment_survives_file_round_trip (line 27)
- **Verdict**: good
- **Reason**: Tests that comments survive a full file-based load/modify/save round-trip with value changes.
- **Recommendation**: keep

---

### tests/integration/test_smoke.py

#### test_health_endpoint (line 8)
- **Verdict**: good
- **Reason**: Smoke test: verifies the container is up and /health returns 200 with status "ok".
- **Recommendation**: keep

#### test_status_endpoint (line 14)
- **Verdict**: good
- **Reason**: Smoke test: verifies /api/status returns 200 with state fields.
- **Recommendation**: keep

#### test_dashboard_page (line 22)
- **Verdict**: good
- **Reason**: Smoke test: verifies / returns 200 with text/html content type.
- **Recommendation**: keep

#### test_training_page (line 28)
- **Verdict**: good
- **Reason**: Smoke test: verifies /training returns 200 with text/html.
- **Recommendation**: keep

#### test_training_status_endpoint (line 34)
- **Verdict**: good
- **Reason**: Smoke test: verifies /api/training/status returns 200 with training, benchmark, and queue keys. This overlaps with the unit test in test_api_routes.py, but the integration test runs against a real container so it exercises the real TrainingManager.
- **Recommendation**: keep (not redundant -- unit vs integration)

---

### tests/integration/test_models.py

#### test_list_architectures (line 8)
- **Verdict**: good
- **Reason**: Tests /api/models/architectures with a real query. Verifies timm integration returns a non-empty list of strings.
- **Recommendation**: keep

#### test_list_architectures_short_query_returns_empty (line 17)
- **Verdict**: good
- **Reason**: Tests the <2 char query guard.
- **Recommendation**: keep

#### test_list_digits_models (line 24)
- **Verdict**: good
- **Reason**: Tests /api/models?model_type=digits against real container.
- **Recommendation**: keep

#### test_list_arrows_models (line 32)
- **Verdict**: good
- **Reason**: Same for arrows type.
- **Recommendation**: keep

#### test_training_data_stats (line 40)
- **Verdict**: good
- **Reason**: Tests /api/training-data/stats against real filesystem in container.
- **Recommendation**: keep

#### test_model_not_found (line 48)
- **Verdict**: good
- **Reason**: Tests 404 for nonexistent model in a real container. Overlaps conceptually with unit test but exercises real ModelManager.
- **Recommendation**: keep

---

### tests/integration/test_config.py

#### test_get_config (line 9)
- **Verdict**: good
- **Reason**: Tests GET /api/config against real container. Verifies the container has a valid config file.
- **Recommendation**: keep

#### test_get_config_schema (line 18)
- **Verdict**: good
- **Reason**: Tests GET /api/config/schema.json in real container.
- **Recommendation**: keep

#### test_save_invalid_yaml (line 25)
- **Verdict**: good
- **Reason**: Tests POST /api/config/save with invalid YAML in real container.
- **Recommendation**: keep

#### test_config_roundtrip (line 35)
- **Verdict**: good
- **Reason**: Excellent integration test: reads config, saves it back, reads again, verifies semantic equality using yaml.safe_load comparison. Tests real config preservation including ruamel.yaml quirks.
- **Recommendation**: keep

---

### tests/integration/test_training.py

#### test_training_e2e (line 58)
- **Verdict**: good
- **Reason**: Full end-to-end training cycle: checks ground truth availability, starts training, polls to completion, verifies model appears in model list, cleans up test models, verifies logs exist. Skips gracefully when insufficient data.
- **Recommendation**: keep

#### test_training_cancel (line 106)
- **Verdict**: good (with flakiness note)
- **Reason**: Tests start-then-cancel flow. Accepts multiple valid final statuses (cancelled, idle, completed, failed) to handle race conditions. The `time.sleep(1)` before cancel and `time.sleep(2)` after introduce timing sensitivity but this is inherent to testing cancellation.
- **Recommendation**: keep. The timing windows are reasonable.

#### test_training_queue (line 134)
- **Verdict**: good
- **Reason**: Tests queueing behavior: starts two jobs, verifies queue has entries, clears queue, cancels running job, waits for cleanup. Tests a complex stateful workflow.
- **Recommendation**: keep

---

### tests/integration/test_benchmark.py

#### test_benchmark_digits (line 72)
- **Verdict**: good
- **Reason**: Full benchmark cycle: finds a model, checks ground truth, runs benchmark, verifies result structure (accuracy, mean_confidence, total_images, correct_predictions, per_class_accuracy), and enforces minimum accuracy gate (90%). Skips gracefully when preconditions aren't met.
- **Recommendation**: keep

#### test_benchmark_arrows (line 103)
- **Verdict**: good
- **Reason**: Same as digits but for arrows. Slightly less detailed structure checks (acceptable since digits test covers the general case).
- **Recommendation**: keep

#### test_benchmark_cancel (line 124)
- **Verdict**: good
- **Reason**: Tests benchmark start-then-cancel flow. Similar timing concerns as training cancel but handled with multiple valid final states.
- **Recommendation**: keep

---

### tests/unit/conftest.py (supporting fixtures)

#### mock_service fixture (line 56)
- **Verdict**: good
- **Reason**: Provides a well-structured mock WatermeterService with realistic state. Used by test_client.
- **Recommendation**: keep

#### test_client fixture (line 80)
- **Verdict**: good
- **Reason**: Creates a FastAPI TestClient with properly isolated tmp_path, mock templates, mock config.yaml, and mocked services. The `raise_server_exceptions=False` is appropriate for testing error status codes. Uses monkeypatch for all state, ensuring clean teardown.
- **Recommendation**: keep

---

## Summary

### Overall Assessment

The test suite is **well-structured, thorough, and professionally written**. The code shows evidence of careful thought about test isolation, edge cases, and security boundaries. The mocking strategy is sound throughout -- heavy dependencies (OpenVINO, cv2, paho.mqtt) are mocked at the minimum level needed, while business logic runs for real.

### Counts

| Category | Count |
|----------|-------|
| Total tests analyzed | ~258 |
| Good | 257 |
| Trivially passing | 1 |
| Redundant | 0 |
| Over-mocked | 0 |
| Wrong assertions | 0 |
| Broken/skipped | 0 |
| Flaky | 0 |

### Issues Found

| File | Test | Verdict | Fix |
|------|------|---------|-----|
| `test_trigger_mode.py` | `TestTriggerModeDefaults.test_default_trigger_mode_is_mqtt` (line 100) | **Trivially passing** | Rewrite to test actual application code (e.g., how WatermeterService resolves `trigger_mode` from an empty config dict), or delete. Currently it only tests Python's `dict.get()` default behavior. |

### Observations (Not Issues)

1. **Timing-sensitive tests**: `test_cyclic_loop_calls_process_reading` in `test_trigger_mode.py` uses `asyncio.sleep` with generous margins. Not flagged as flaky because the margins (50ms interval, 180ms wait, >= 2 calls expected) leave ample room.

2. **Integration test prerequisites**: `test_training.py` and `test_benchmark.py` skip gracefully when ground truth data or models are unavailable. This is the correct approach but means CI may report fewer tests than expected.

3. **Module-level sys.modules manipulation**: Several test files (`test_confirmation.py`, `test_trigger_mode.py`, `test_leak_detection.py`, `test_value_correction.py`, `test_cross_arrow_consistency.py`, `test_marker_alignment.py`) perform complex `sys.modules` surgery to bypass conftest mocks and import the real `WatermeterService`. This pattern is repeated identically and could be extracted into a shared helper, but it works correctly as-is.

4. **Conditional test skipping**: `test_arrow_regression.py` has fixtures that check if inference symbols loaded successfully and skip if not. This means tests may silently skip in some environments -- consider logging or marking these explicitly.

5. **Test isolation concern**: The mislabel and prune test files manipulate module-level dicts (`_mislabel_scans`, `_prune_previews`) which are shared state. Tests that clear this state before running (`models_mod._mislabel_scans.clear()`) are correct, but test ordering could theoretically matter. In practice, each test that needs clean state explicitly clears it, so this is not a real issue.

### Missing Coverage

While not part of the "issues found" scope, the following API endpoints have no direct HTTP-level unit tests:

- `POST /api/trigger` -- only integration-level smoke
- `POST /api/reset` -- no direct test
- `POST /api/set-value` -- no direct test (BL-15 feature)
- `POST /api/toggle-ha-publish` -- no direct test
- `POST /api/submit-training` -- no direct test
- `GET /api/confirmation/status` -- tested via service method, but not HTTP level
- `POST /api/models/{type}/{id}/activate` -- no unit test
- `POST /api/models/{type}/{id}/archive` -- no unit test
- `GET /api/models/{type}/{id}/logs` -- no unit test
- `GET /api/status/html` -- no test
- `POST /api/training-data/dedup` -- no test
- `GET /api/training/progress/{job_id}` -- no test
- `DELETE /api/training/queue/{index}` -- no test (DELETE /api/training/queue is tested in integration)

These represent potential targets for future test additions but do not affect the quality assessment of existing tests.
