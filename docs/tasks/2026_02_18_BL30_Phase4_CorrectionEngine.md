# BL-30 Phase 4: Extract Correction Engine

> **Goal:** Extract the BL-04 correction engine (~270 lines, 5 methods) from `watermeter_service.py` into a standalone `watermeter/correction.py` module. This is the last major extractable block from the service.

---

## Current State (after Phase 1+2+3)

**`watermeter_service.py`** is ~1461 lines. The correction engine occupies lines 567-837:

| Line | Method | Lines | Description |
|------|--------|-------|-------------|
| 569 | `_get_ordered_position_ids()` | 4 | Return digit + arrow IDs in order |
| 574 | `_estimate_expected_range()` | 18 | Estimate plausible value range from rate history |
| 593 | `_recalculate_with_replacement()` | 30 | Compute hypothetical total with one position replaced |
| 624 | `_check_consistency_improvement()` | 50 | Check if replacing a position fixes a consistency violation |
| 675 | `_check_cross_arrow_consistency()` | 53 | Check if replacing an arrow improves cross-arrow consistency |
| 729 | `correct_predictions()` | 108 | Main correction loop: evaluate signals, apply best correction |

**Total: ~270 lines** (including blank lines and comments).

### Dependencies Used by the Correction Engine

The 5 methods access the following from `self` (the service):
- `self.config` — full config dict (reads `correction`, `detection`, `plausibility` sections)
- `self.previous_value` — property forwarding to `self._state.previous_value`
- `self.last_update_time` — property forwarding to `self._state.last_update_time`
- `self._rate_tracker` — RateTracker instance (reads `average_rate_per_hour`, `len()`)
- `get_position_ids(self.config)` — pure function import from `position_utils`

No MQTT, no persistence, no image pipeline, no confirmation manager. This is a pure computation module with read-only access to state and rate tracker.

### Tests Covering the Correction Engine

1. **`tests/unit/test_value_correction.py`** (721 lines) — 5 test classes:
   - `TestPredictDetailed` (3 tests) — standalone predict logic, not service-dependent
   - `TestHelperMethods` (4 tests) — `_estimate_expected_range`, `_recalculate_with_replacement`, `_get_ordered_position_ids`
   - `TestConsistencyImprovement` (2 tests) — `_check_consistency_improvement`
   - `TestSignalScoring` (4 tests) — individual signal scoring via `correct_predictions`
   - `TestCorrectionEngine` (7 tests) — full `correct_predictions` flow

2. **`tests/unit/test_cross_arrow_consistency.py`** (379 lines) — 2 test classes:
   - `TestCheckCrossArrowConsistency` (direct method tests)
   - `TestCrossArrowIntegration` (integration with `correct_predictions`)

Both test files use `object.__new__(WatermeterService)` to bypass `__init__` and set `svc.previous_value`, `svc.last_update_time`, `svc._rate_tracker`, etc. directly on the service instance.

---

## Architecture Decision

### Extraction Pattern (consistent with Phase 1-3)

Follow the same pattern as `PlausibilityChecker`, `LeakDetector`, etc.:
1. New file `watermeter/correction.py` with class `CorrectionEngine`
2. Constructor receives `config`, `rate_tracker`, and `meter_state` as dependencies
3. `self.config` is synced during `reload_config`
4. WatermeterService delegates via a thin one-liner wrapper
5. Tests can instantiate `CorrectionEngine` directly

### Class Design

```python
class CorrectionEngine:
    """BL-04: Auto-correction of low-confidence predictions using contextual signals."""

    def __init__(self, config: dict, rate_tracker: RateTracker, meter_state: MeterState) -> None:
        self.config = config
        self._rate_tracker = rate_tracker
        self._meter_state = meter_state

    def _get_ordered_position_ids(self) -> List[str]: ...
    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]: ...
    def _recalculate_with_replacement(self, predictions, replace_id, replace_class, raw_values) -> float: ...
    def _check_consistency_improvement(self, predictions, replace_id, replace_class) -> bool: ...
    def _check_cross_arrow_consistency(self, predictions, replace_id, replace_class) -> bool: ...
    def correct_predictions(self, predictions, raw_total, raw_values) -> List[str]: ...
```

**Key changes from service methods:**
- Replace `self.previous_value` with `self._meter_state.previous_value`
- Replace `self.last_update_time` with `self._meter_state.last_update_time`
- `self.config` and `self._rate_tracker` stay as-is (same pattern as other extracted modules)
- Import `get_position_ids` from `position_utils` (already imported in service)

### What Stays on WatermeterService After Phase 4

- `__init__` (wire components together)
- `run_inference`, `calculate_total` (core inference logic, tight coupling to InferenceService)
- `process_reading` (main orchestration pipeline)
- `reset_previous_value`, `set_manual_value`, `toggle_ha_publish` (cross-component orchestrators)
- `reload_config`
- Image pipeline, scheduling, low confidence delegation wrappers
- MQTT/confirmation delegation wrappers and backward-compat properties

**Expected reduction:** ~1461 lines -> ~1200 lines (removing ~260 lines of correction engine).

---

## Work Packages

### WP-1: Extract CorrectionEngine class
**Agent:** `dev`
**Dependencies:** None
**Files:**
- Create: `watermeter/correction.py`
- Modify: `watermeter/watermeter_service.py`

**Tasks:**
1. Create `watermeter/correction.py` with `CorrectionEngine` class
2. Move these 6 methods from `watermeter_service.py`:
   - `_get_ordered_position_ids` (L569-572)
   - `_estimate_expected_range` (L574-591)
   - `_recalculate_with_replacement` (L593-622)
   - `_check_consistency_improvement` (L624-673)
   - `_check_cross_arrow_consistency` (L675-727)
   - `correct_predictions` (L729-837)
3. Replace `self.previous_value` with `self._meter_state.previous_value` (5 occurrences)
4. Replace `self.last_update_time` with `self._meter_state.last_update_time` (1 occurrence)
5. Add import: `from .position_utils import get_position_ids`
6. In `watermeter_service.py`:
   - Add `from .correction import CorrectionEngine` import
   - In `__init__`, create `self._correction = CorrectionEngine(config=self.config, rate_tracker=self._rate_tracker, meter_state=self._state)`
   - Replace `self.correct_predictions(...)` call in `process_reading` (L913) with `self._correction.correct_predictions(...)`
   - Remove the 6 extracted methods
   - Add delegation wrapper: `def correct_predictions(self, ...) -> List[str]: return self._correction.correct_predictions(...)`
7. In `reload_config`, sync `self._correction.config = self.config` (follow the pattern used for `self._plausibility.config = self.config`, etc.)

**Done criteria:**
- `watermeter/correction.py` exists with `CorrectionEngine` class
- All 6 methods removed from `watermeter_service.py`
- `process_reading` delegates to `self._correction`
- `reload_config` syncs correction engine config

---

### WP-2: Update tests to use CorrectionEngine directly
**Agent:** `dev`
**Dependencies:** WP-1
**Files:**
- Modify: `tests/unit/test_value_correction.py`
- Modify: `tests/unit/test_cross_arrow_consistency.py`

**Tasks:**
1. In `test_value_correction.py`:
   - Change the import to import `CorrectionEngine` from `watermeter.correction` (and `RateTracker`, `MeterState`)
   - Update `make_service()` to create a `CorrectionEngine` instance instead of `WatermeterService` via `object.__new__()`:
     ```python
     def make_engine(config=None):
         config = config or { ... }  # same config as current make_service
         rate_tracker = RateTracker(max_size=25)
         state = MeterState()
         return CorrectionEngine(config=config, rate_tracker=rate_tracker, meter_state=state)
     ```
   - Update all test references: `svc.previous_value` -> `engine._meter_state.previous_value`, etc.
   - Update method calls: `svc.correct_predictions(...)` -> `engine.correct_predictions(...)`
   - Update helper method calls: `svc._estimate_expected_range()` -> `engine._estimate_expected_range()`
   - Remove the `sys.modules` mock-bypass hack (no longer needed since we import `CorrectionEngine` directly, not `WatermeterService`)
   - `TestPredictDetailed` stays unchanged (it tests standalone functions, not the service)

2. In `test_cross_arrow_consistency.py`:
   - Same pattern: import `CorrectionEngine`, update `make_service()` to `make_engine()`
   - Update all `svc.` references to `engine.`
   - Remove the `sys.modules` mock-bypass hack
   - `svc.rate_history = []` becomes unnecessary since `RateTracker` starts empty

**Done criteria:**
- Both test files import `CorrectionEngine` directly (no `WatermeterService` import)
- No `sys.modules` hack needed
- All tests pass

---

### WP-3: Backward-compat delegation wrapper and `process_reading` integration
**Agent:** `dev`
**Dependencies:** WP-1
**Files:**
- Modify: `watermeter/watermeter_service.py`

**Tasks:**
1. Ensure `correct_predictions` delegation wrapper exists on `WatermeterService` for any external callers
2. Ensure `process_reading` L913 calls `self._correction.correct_predictions(...)` (already done in WP-1 but verify)
3. Handle the `object.__new__()` edge case: tests that bypass `__init__` may still call `svc.correct_predictions()`. Add a lazy-init pattern similar to `_ensure_confirmation_manager()`:
   ```python
   def _ensure_correction_engine(self):
       if not hasattr(self, '_correction'):
           from .correction import CorrectionEngine
           from .rate_tracker import RateTracker
           from .meter_state import MeterState
           if not hasattr(self, '_rate_tracker'):
               self._rate_tracker = RateTracker(max_size=5)
           state = MeterState()
           state.previous_value = getattr(self, 'previous_value', None)
           state.last_update_time = getattr(self, 'last_update_time', None)
           self._correction = CorrectionEngine(
               config=getattr(self, 'config', {}),
               rate_tracker=self._rate_tracker,
               meter_state=state,
           )
       return self._correction
   ```
   However, since WP-2 updates the tests to use `CorrectionEngine` directly, this may be unnecessary. The dev agent should evaluate whether any remaining tests or code paths still call `svc.correct_predictions()` via `object.__new__()` and only add the lazy-init if needed.

**Done criteria:**
- No code path calls correction methods on WatermeterService without a working `_correction` attribute
- `process_reading` correctly delegates

**NOTE:** WP-3 can be merged into WP-1 if the dev agent determines the lazy-init pattern is unnecessary after WP-2 removes all `object.__new__()` callers.

---

### WP-4: Update codebase map
**Agent:** `dev`
**Dependencies:** WP-1, WP-2, WP-3
**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md` (BL-30 status)

**Tasks:**
1. Add a new section for `watermeter/correction.py` with the `CorrectionEngine` class and its 6 methods
2. Update the `watermeter/watermeter_service.py` section:
   - Remove the 6 extracted correction methods (L569-837)
   - Update remaining line numbers
   - Add note about `_correction` delegation
3. Update `test_value_correction.py` and `test_cross_arrow_consistency.py` descriptions to reflect they now test `CorrectionEngine` directly
4. Update BL-30 status in `backlog.md`: Phase 4 complete, service now at ~1200 lines

**Done criteria:**
- `docs/codebase_map.md` accurately reflects post-Phase-4 state
- BL-30 marked as done or final status noted

---

### WP-5: Full test suite verification
**Agent:** `tester`
**Dependencies:** WP-1, WP-2, WP-3, WP-4
**Files:** All test files

**Tasks:**
1. Run `.venv/bin/python -m pytest` — all tests must pass
2. Verify no import cycles: `python -c "from watermeter.correction import CorrectionEngine"`
3. Verify `watermeter_service.py` line count is under ~1200 lines
4. Verify `watermeter/correction.py` can be instantiated standalone without service dependencies

**Done criteria:**
- All tests pass (330+ tests)
- No import errors or cycles
- Line count target met

---

## Risk Assessment

1. **Low risk — pure computation extraction.** Unlike confirmation/MQTT (Phase 3), the correction engine has no threading, no async, no callbacks. It's pure computation with read-only access to state and rate tracker. This is the simplest extraction in the entire refactoring.

2. **Test refactoring is the main work.** The `sys.modules` hack in both test files exists solely because they import `WatermeterService`. Once they import `CorrectionEngine` directly, the hack is eliminated entirely.

3. **`object.__new__()` callers.** After WP-2 updates both correction test files, no test should use `object.__new__(WatermeterService)` to call correction methods. The delegation wrapper on `WatermeterService.correct_predictions` is only needed for `process_reading` (which uses a properly initialized service). No lazy-init pattern should be needed, but the dev agent should verify.

4. **`previous_value` / `last_update_time` access pattern change.** The correction engine will read these from `self._meter_state` instead of `self` (the service). The test helper `make_engine()` will set them on the `MeterState` instance. This is a straightforward mechanical change.

## Open Questions

None. The extraction is well-scoped and follows the established pattern.
