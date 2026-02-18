# BL-30 Phase 3: WatermeterService God Object Refactoring

> **Goal:** Extract confirmation flow, MQTT management, manual control, and MeterState from `watermeter_service.py` to complete the god-object decomposition.

---

## Current State (after Phase 1+2)

**`watermeter_service.py`** is down to ~1919 lines from the original ~2350. Phase 1 extracted `image_pipeline.py`, `low_confidence.py`, `scheduling.py`, `position_utils.py`. Phase 2 extracted `rate_tracker.py`, `leak_detector.py`, `plausibility.py`.

**What remains** in WatermeterService (~60% of lines):
- State management (previous_value, last_update_time, current_state dict, ha_publish_enabled, etc.)
- `__init__` (120 lines) — config loading, logging setup, state init, component wiring
- `run_inference` (70 lines)
- `calculate_total` (50 lines)
- `correct_predictions` and correction helpers (210 lines) — these were rewired in Phase 2 but kept in-place
- Confirmation flow (270 lines): `_get_confirmation_config`, `_should_request_confirmation`, `_publish_confirmation_request`, `_cancel_confirmation_timer`, `_confirmation_timeout`, `_do_confirmation_timeout`, `_handle_confirmation_response`, `get_confirmation_status`
- MQTT (240 lines): `_HA_ENTITIES` registry (185 lines), `publish_to_mqtt`, `publish_discovery`, `publish_training_stats`, `on_mqtt_connect`, `on_mqtt_disconnect`, `on_mqtt_message`, `start_mqtt`, `stop_mqtt`
- Manual control (95 lines): `reset_previous_value`, `set_manual_value`, `toggle_ha_publish`
- `process_reading` (265 lines) — main pipeline orchestration
- Backward-compat properties for rate_history (30 lines)
- Config reload (55 lines)
- Stats loop/cyclic loop delegations (15 lines)

---

## Architecture Decisions

### Extraction Pattern (consistent with Phase 1+2)

Each extracted module:
1. Gets its own file under `watermeter/`
2. Is a class that receives config + dependencies in constructor
3. Has `self.config` that the service syncs during `reload_config`
4. WatermeterService delegates via thin one-liner wrappers
5. Tests can instantiate the extracted class directly

### Extraction Order (dependencies)

1. **MeterState** first — it's a pure data container, no external deps
2. **ConfirmationManager** second — depends on MeterState + RateTracker
3. **MqttPublisher** third — depends on MeterState + ConfirmationManager for state reads
4. **Manual control** stays inline — only 3 short methods, not worth a separate class

### Design Decisions

- **MeterState**: Dataclass-like object holding `previous_value`, `last_update_time`, `current_state` dict, `consecutive_rejections`, `leak_warning`, `ha_publish_enabled`. Pure data + simple getters/setters. NOT a full model — just groups scattered instance variables.
- **ConfirmationManager**: Encapsulates the entire BL-07 confirmation request/response/timeout flow. Receives `rate_tracker`, `state_store`, and `meter_state` as constructor deps. Methods that touch `mqtt_client` and `loop` receive them as parameters (not stored), keeping MQTT as an external concern.
- **MqttPublisher**: Encapsulates `_HA_ENTITIES`, `publish_to_mqtt`, `publish_discovery`, `publish_training_stats`, `start_mqtt`, `stop_mqtt`, and the MQTT callbacks (`on_mqtt_connect`, `on_mqtt_disconnect`, `on_mqtt_message`). Owns `self.mqtt_client` and `self.loop`. The message router in `on_mqtt_message` needs to call back to the service for `process_reading` trigger and `reset_previous_value`; these are injected as callbacks.
- **Manual control** (`reset_previous_value`, `set_manual_value`, `toggle_ha_publish`): These are thin orchestrators that touch state + MQTT + confirmation. They remain on WatermeterService as they coordinate across all extracted components. Extracting them would just create a pass-through layer.
- **Backward-compat rate_history properties** (L372-398): Remove them entirely in this phase. Update any tests that set `service.rate_history` directly to use `service._rate_tracker._history` or the proper API.

### What stays on WatermeterService after Phase 3

- `__init__` (wire components together)
- `run_inference`, `calculate_total` (core inference, not worth extracting alone)
- `correct_predictions` + correction helpers (BL-04 correction engine, candidate for future Phase 4)
- `process_reading` (main orchestration pipeline)
- `reset_previous_value`, `set_manual_value`, `toggle_ha_publish`
- `reload_config`
- Image pipeline delegation wrappers
- Scheduling delegation wrappers

Expected reduction: ~1919 lines -> ~1200 lines (removing ~700 lines of confirmation + MQTT + entity registry).

---

## Work Packages

### WP-1: Extract MeterState data object
**Agent:** `dev`
**Dependencies:** None
**Files:**
- Create: `watermeter/meter_state.py`
- Modify: `watermeter/watermeter_service.py`

**Methods/attributes to extract:**
- Group these instance variables into MeterState:
  - `previous_value: Optional[float]`
  - `last_update_time: Optional[datetime]`
  - `consecutive_rejections: int`
  - `max_consecutive_rejections: int`
  - `leak_warning: bool`
  - `ha_publish_enabled: bool`
  - `current_state: Dict` (the full status dict)
- MeterState should have:
  - `__init__` that initializes all fields with defaults
  - A `reset()` method that clears published/rejected tracking fields
  - A `to_dict()` or similar for API responses is NOT needed — `current_state` already serves that role

**Integration:**
- WatermeterService.__init__ creates `self._state = MeterState()`
- Replace `self.previous_value` -> `self._state.previous_value` etc.
- Add `@property` forwarding on WatermeterService for `current_state`, `previous_value`, `last_update_time` to maintain backward compatibility with routes and tests
- Remove the backward-compat `rate_history` / `rate_history_size` properties (they exist only for confirmation tests; those tests will be updated in WP-2)

**Done criteria:**
- All existing tests pass unchanged (property forwarding ensures compatibility)
- `watermeter/meter_state.py` exists with MeterState class
- No test imports need to change

---

### WP-2: Extract ConfirmationManager
**Agent:** `dev`
**Dependencies:** WP-1
**Files:**
- Create: `watermeter/confirmation.py`
- Modify: `watermeter/watermeter_service.py`
- Modify: `tests/unit/test_confirmation.py`

**Methods to extract (L566-853):**
- `_get_confirmation_config()` -> `ConfirmationManager.get_config()`
- `_should_request_confirmation()` -> `ConfirmationManager.should_request()`
- `_publish_confirmation_request()` -> `ConfirmationManager.publish_request()`
- `_cancel_confirmation_timer()` -> `ConfirmationManager.cancel_timer()`
- `_confirmation_timeout()` -> `ConfirmationManager._timeout_sync()`
- `_do_confirmation_timeout()` -> `ConfirmationManager._do_timeout()`
- `_handle_confirmation_response()` -> `ConfirmationManager.handle_response()`
- `get_confirmation_status()` -> `ConfirmationManager.get_status()`

**State to move:**
- `_pending_confirmation: Optional[Dict]`
- `_confirmation_timer: Optional[threading.Timer]`

**Constructor signature:**
```python
class ConfirmationManager:
    def __init__(self, config: dict, rate_tracker: RateTracker, meter_state: MeterState, state_store: Optional[StateStore]):
```

**Key design points:**
- Methods that need `mqtt_client` and `loop` receive them as parameters (e.g. `publish_request(mqtt_client, loop, ...)`)
- `handle_response` needs `mqtt_client`, `loop`, and a `publish_fn` callback for publishing to MQTT after confirm/reject
- `_do_timeout` needs `mqtt_client`, `loop`, and `publish_fn` for the same reason
- The publish callback is `MqttPublisher.publish_to_mqtt` (from WP-3) or initially just `service.publish_to_mqtt`

**Test updates:**
- `test_confirmation.py` fixture creates `ConfirmationManager` directly instead of `WatermeterService` via `object.__new__`
- Tests that set `service.rate_history` directly switch to `rate_tracker._history =`
- Tests that check `service.current_state` get it from `meter_state.current_state`
- The import pattern (bypassing mock) stays the same but imports from `watermeter.confirmation`

**Done criteria:**
- All 791 lines of test_confirmation.py pass
- ConfirmationManager is standalone (no WatermeterService import)
- WatermeterService delegates to `self._confirmation_manager`

---

### WP-3: Extract MqttPublisher
**Agent:** `dev`
**Dependencies:** WP-1, WP-2
**Files:**
- Create: `watermeter/mqtt_publisher.py`
- Modify: `watermeter/watermeter_service.py`
- Modify: `watermeter/app.py` (start_mqtt/stop_mqtt calls)
- Modify: `tests/unit/test_confirmation.py` (if any MQTT-related assertions changed)
- Modify: `tests/unit/test_trigger_mode.py`
- Modify: `tests/unit/test_leak_detection.py`

**Code to extract:**
- Module-level `_HA_ENTITIES` list (L56-240, 185 lines)
- `publish_to_mqtt()` (L1424-1495)
- `publish_discovery()` (L1654-1714)
- `publish_training_stats()` (L1597-1640)
- `on_mqtt_connect()` (L1717-1746)
- `on_mqtt_disconnect()` (L1748-1753)
- `on_mqtt_message()` (L1755-1789)
- `start_mqtt()` (L1791-1832)
- `stop_mqtt()` (L1889-1894)

**State to move:**
- `mqtt_client`
- `loop` (event loop reference)

**Constructor signature:**
```python
class MqttPublisher:
    def __init__(
        self,
        config: dict,
        meter_state: MeterState,
        rate_tracker: RateTracker,
        confirmation_manager: ConfirmationManager,
        on_trigger: Callable,       # callback for process_reading trigger
        on_reset: Callable,         # callback for reset_previous_value
    ):
```

**Key design points:**
- `on_mqtt_message` dispatches to `on_trigger()`, `on_reset()`, and `confirmation_manager.handle_response()`
- `publish_to_mqtt` reads from `meter_state` and `rate_tracker`
- `on_mqtt_connect` calls `confirmation_manager.get_config()` for topic subscription
- `start_mqtt` and `stop_mqtt` manage the paho client lifecycle
- `publish_discovery` uses the `_HA_ENTITIES` registry (moved into this module)

**Integration:**
- `WatermeterService.__init__` creates `self._mqtt = MqttPublisher(...)`
- `service.start_mqtt()` -> `self._mqtt.start()`
- `service.stop_mqtt()` -> `self._mqtt.stop()`
- `service.mqtt_client` -> property forwarding to `self._mqtt.mqtt_client`
- `app.py` lifespan calls unchanged (delegates through service)

**Test updates:**
- `test_trigger_mode.py`: Tests that access `service.mqtt_client` and MQTT callbacks will need to reference the publisher, OR forwarding properties maintain compatibility
- `test_leak_detection.py`: Tests that check `service.mqtt_client` use forwarding property

**Done criteria:**
- All MQTT-related tests pass (test_trigger_mode.py, test_confirmation.py MQTT sections, test_leak_detection.py)
- `_HA_ENTITIES` no longer in watermeter_service.py (saves ~185 lines)
- `watermeter/mqtt_publisher.py` is a self-contained module

---

### WP-4: Update codebase map and clean up
**Agent:** `dev`
**Dependencies:** WP-1, WP-2, WP-3
**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md` (BL-30 status update)

**Tasks:**
- Add sections for `meter_state.py`, `confirmation.py`, `mqtt_publisher.py`
- Update `watermeter_service.py` section (remove extracted methods, update line numbers)
- Update BL-30 status: note Phase 3 complete, what remains for Phase 4
- Remove any dead code or unused imports in watermeter_service.py

**Done criteria:**
- `docs/codebase_map.md` accurately reflects the post-Phase-3 codebase
- BL-30 status updated in `backlog.md`

---

### WP-5: Full test suite verification
**Agent:** `tester`
**Dependencies:** WP-1, WP-2, WP-3, WP-4
**Files:** All test files

**Tasks:**
- Run `.venv/bin/python -m pytest` — all tests must pass
- Verify no import cycles (the new modules should be importable in isolation)
- Verify `watermeter_service.py` line count is under ~1250 lines
- Spot-check that routes still work (confirmation status endpoint, health endpoint MQTT check)

**Done criteria:**
- All tests pass (317+ tests)
- No import errors
- Line count target met

---

## Risk Assessment

1. **Circular imports**: MqttPublisher needs to call back to service for `process_reading`. Solved by injecting callbacks, not importing the service module.
2. **Test fixture breakage**: Many tests use `object.__new__(WatermeterService)` to bypass `__init__`. Property forwarding from WatermeterService to MeterState keeps these working during transition. WP-2 updates the confirmation tests to use the new class directly.
3. **MQTT callback thread safety**: The existing pattern routes MQTT callbacks through `loop.call_soon_threadsafe`. This pattern is preserved in MqttPublisher.
4. **process_reading complexity**: This method orchestrates across all components and is the hardest to extract. It stays on WatermeterService for now — extracting it would require a full rewrite of the pipeline orchestration.

## Open Questions

1. **Correction engine (Phase 4?)**: `correct_predictions` + 4 helper methods (~210 lines) remain. Worth a Phase 4 extraction to `watermeter/correction.py`? Decision deferred.
2. **`_compute_raw_total` and `_get_active_model_name`**: Small utility methods (~20 lines total) that stay on WatermeterService. Could move to a utils module but not worth the churn.
