# BL-15: Manual Meter Reading Input

## Context

### Current Reset Button
- **Route**: `POST /api/reset` in `watermeter/routes/service.py:66-71`
- **Service method**: `WatermeterService.reset_previous_value()` in `watermeter/watermeter_service.py:1648-1664`
- **What it does**:
  1. Sets `self.previous_value = None`
  2. Sets `self.last_update_time = None`
  3. Clears `self.rate_history = []`
  4. Resets `self.consecutive_rejections = 0`
  5. Clears `self.leak_warning` and `self.current_state['leak_warning']`
  6. Cancels any pending confirmation (BL-07)
  7. Clears persisted state via `self.state_store.clear()`
- **UI**: Simple button in `watermeter/templates/dashboard.html:24-31` with `onclick="return confirm('Reset previous value?')"` and HTMX `hx-post="/api/reset"` that targets `#status-message`

### MQTT Publishing
- **Method**: `WatermeterService.publish_to_mqtt()` in `watermeter/watermeter_service.py:1604-1646`
- **Signature**: `async def publish_to_mqtt(self, value, warnings, predictions, *, leak_warning=False)`
- **Guards**: checks `self.ha_publish_enabled` and `self.mqtt_client.is_connected()`
- **Topic**: `self.config['homeassistant']['publish_topic']` (default: `homeassistant/sensor/watermeter/state`)
- **Payload format**:
  ```json
  {
    "state": 34.8961,
    "attributes": {
      "last_update": "2026-02-15T12:00:00",
      "warnings": [],
      "leak_warning": false,
      "confidences": {"main_dig1": 99.2, ...}
    }
  }
  ```
- **QoS**: 2, **retain**: True
- The method is `async` but only because it's called from async context; the actual `mqtt_client.publish()` is synchronous (paho)

### Previous Value Management
- **In-memory**: `self.previous_value: Optional[float]` on `WatermeterService` (line 48)
- **Persistence**: `StateStore` in `watermeter/persistence.py` saves `{previous_value, last_update_time}` to JSON file (default: `/data/state.json`)
- **Loaded on startup**: `watermeter_service.py:68` - `self.previous_value, self.last_update_time = self.state_store.load()`
- **Saved after each accepted reading**: `watermeter_service.py:1526-1527` - `self.state_store.save(self.previous_value, self.last_update_time)`

### Plausibility State to Reset
- `self.consecutive_rejections` (int, line 81) - tracks stuck readings
- `self.leak_warning` (bool, line 85) - leak detection flag
- `self.current_state['leak_warning']` - exposed to UI
- `self.current_state['status']` - should be set to `'ok'` after manual set
- `self.current_state['warnings']` - should be cleared
- `self.rate_history` - list of `(value, timestamp)` tuples for rate averaging
- `self._pending_confirmation` - any pending BL-07 confirmation

### Dashboard UI Layout
- `watermeter/templates/dashboard.html` - header-actions div with flex layout (`gap: 12px`)
- Contains: "Read Now" button (primary), "Reset" button (secondary), Auto-Refresh toggle, HA Publish toggle
- Status messages displayed in `#status-message` div, auto-hidden after 5 seconds via `dashboard.js:hideStatusMessage()`
- CSS: `.header-actions` has `flex-wrap: wrap`, responsive breakpoint at mobile stacks them

### JavaScript
- `watermeter/static/dashboard.js` - handles toggle functions, status message display, HTMX events
- Pattern for API calls: `fetch()` + `.then()` chain, update `#status-message`

## Design Decisions

1. **New endpoint `POST /api/set-value`** rather than modifying `/api/reset` - cleaner separation
2. **Simplified MQTT publish** - the manual set won't have real predictions/confidences, so we publish a simplified payload (empty predictions, "manual_input" in warnings/attributes)
3. **Keep the Reset button** - the backlog says "replace", but we should also keep a way to fully reset to `None` for meter replacement scenarios. Decision: replace the standalone Reset button with a collapsible input group that has both "Set" and "Reset" actions. The Reset becomes a small secondary action within the group.
4. **Rate history** - when manually setting a value, we should seed the rate history with this value so the next automated reading doesn't get a false spike rejection. We clear existing history and start fresh.

## Work Packages

### WP-1: Backend — New `/api/set-value` endpoint and service method
- **Agent**: senior-dev
- **Files**:
  - `watermeter/watermeter_service.py` — add `set_manual_value(value: float)` method
  - `watermeter/routes/service.py` — add `POST /api/set-value` endpoint
- **Task**: Implement the backend logic for manually setting the meter value
- **Details**:
  1. Add a Pydantic model `SetValueRequest` with field `value: float`
  2. Add `POST /api/set-value` route that:
     - Validates `value >= 0` (reject negative numbers)
     - Calls `service.set_manual_value(request.value)`
     - Returns `{"success": true, "message": "Meter set to X.XXXX m3", "value": X.XXXX}`
  3. Add `set_manual_value(self, value: float)` method on `WatermeterService`:
     - Set `self.previous_value = value`
     - Set `self.last_update_time = datetime.now()`
     - Clear `self.rate_history` and seed with `[(value, datetime.now())]`
     - Reset `self.consecutive_rejections = 0`
     - Clear `self.leak_warning = False` and `self.current_state['leak_warning'] = False`
     - Cancel any pending confirmation (`self._cancel_confirmation_timer()`, `self._pending_confirmation = None`)
     - Persist via `self.state_store.save(self.previous_value, self.last_update_time)`
     - Update `self.current_state` with the new value, status `'ok'`, empty warnings, and the current timestamp
     - Publish to MQTT with a simplified payload — call a new helper or directly publish:
       - Topic: same `ha_config['publish_topic']`
       - Payload: `{"state": value, "attributes": {"last_update": ..., "warnings": ["Manual input"], "leak_warning": false, "confidences": {}, "source": "manual"}}`
       - Same QoS 2, retain True
     - Log the manual set action
  4. The MQTT publish should respect `self.ha_publish_enabled` and connection checks, same as `publish_to_mqtt`

### WP-2: Frontend — Replace Reset button with manual input UI
- **Agent**: frontend
- **Files**:
  - `watermeter/templates/dashboard.html` — replace Reset button with input group
  - `watermeter/static/dashboard.js` — add `setMeterValue()` function
  - `watermeter/static/style.css` — add styles for the input group
- **Task**: Build the UI for manual meter value input
- **Details**:
  1. In `dashboard.html`, replace the Reset button (lines 24-31) with a meter input group:
     ```html
     <div class="meter-input-group">
         <input type="number" id="meter-value-input"
                step="0.0001" min="0" placeholder="e.g. 34.8961"
                class="meter-value-input">
         <button class="btn btn-primary btn-small" onclick="setMeterValue()">
             Set
         </button>
         <button class="btn btn-secondary btn-small"
                 onclick="if(confirm('Reset previous value to unknown? The next reading will be accepted without plausibility check.')) { fetch('/api/reset', {method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('status-message').textContent=d.message; hideStatusMessage();}); }">
             Reset
         </button>
     </div>
     ```
  2. In `dashboard.js`, add `setMeterValue()`:
     - Read value from `#meter-value-input`
     - Validate: must be a number, must be >= 0
     - Show confirmation dialog: `confirm('Set meter to X.XXXX m3?')` (format to 4 decimal places)
     - On confirm: `POST /api/set-value` with `{"value": parsedValue}`
     - On success: display success in `#status-message`, clear the input field
     - On error: display error message
  3. In `style.css`, add styles for `.meter-input-group` and `.meter-value-input`:
     - Flex layout, inline with the other header actions
     - Input: number field, width ~140px, matching border-radius and font of buttons
     - Should fit within the existing `.header-actions` flex container
     - Mobile responsive: stack or wrap nicely

### WP-3: Unit tests
- **Agent**: tester
- **Files**:
  - `tests/unit/test_api_routes.py` — add tests for `POST /api/set-value`
  - `tests/unit/test_set_value.py` (new) — test `set_manual_value()` service method
- **Task**: Write unit tests covering the new functionality
- **Details**:
  1. **API route tests** (in `test_api_routes.py`):
     - `test_set_value_success` — POST valid value, check 200 + response shape
     - `test_set_value_negative` — POST negative value, check 400
     - `test_set_value_missing_body` — POST with no body, check 422
     - `test_set_value_calls_service` — verify `service.set_manual_value()` is called with correct value
  2. **Service method tests** (new file `test_set_value.py`):
     - Use the same fixture pattern as `test_leak_detection.py` / `test_confirmation.py` (create a minimal `WatermeterService` with mocked dependencies)
     - `test_sets_previous_value` — verify `previous_value` is updated
     - `test_persists_state` — verify `state_store.save()` is called
     - `test_clears_rejections` — verify `consecutive_rejections` reset to 0
     - `test_clears_leak_warning` — verify `leak_warning` cleared
     - `test_clears_pending_confirmation` — verify `_pending_confirmation` set to None
     - `test_updates_current_state` — verify `current_state` dict updated with new value, status='ok'
     - `test_seeds_rate_history` — verify rate_history has one entry with the new value
     - `test_publishes_to_mqtt` — verify MQTT publish called with correct topic/payload (mock mqtt_client)
     - `test_skips_mqtt_when_disabled` — verify no publish when `ha_publish_enabled = False`

### WP-4: Integration / browser test
- **Agent**: tester
- **Files**: `tests/integration/test_smoke.py` (extend existing) or browser test against `http://localhost:8002`
- **Task**: Verify the UI works end-to-end in the debug container
- **Details**:
  1. Use Playwright against `http://localhost:8002`
  2. Verify the input field and Set button are visible
  3. Verify the Reset button is still accessible
  4. Enter a value, click Set, verify confirmation dialog appears
  5. Accept confirmation, verify success message appears
  6. Verify the status message disappears after timeout

## Execution Order

1. **WP-1** (backend) first - the API must exist before frontend can use it
2. **WP-2** (frontend) depends on WP-1
3. **WP-3** (unit tests) can start in parallel with WP-2 (API tests only need the route, service tests only need the method)
4. **WP-4** (browser test) last, after WP-1 + WP-2 are deployed to debug container
