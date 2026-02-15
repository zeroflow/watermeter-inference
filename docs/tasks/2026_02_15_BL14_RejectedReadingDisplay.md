# BL-14: Show Rejected Reading on Dashboard

## Context

### Current State Management

The `WatermeterService` class in `watermeter/watermeter/watermeter_service.py` manages all state in a dict called `current_state` (line 51-61):

```python
self.current_state: Dict = {
    'total_value': None,
    'unit': 'm3',
    'last_update': None,
    'status': 'idle',
    'warnings': [],
    'predictions': [],
    'processing': False,
    'ha_publish_enabled': self.ha_publish_enabled,
    'leak_warning': False
}
```

Additionally, the service tracks:
- `self.previous_value: Optional[float]` -- the last **accepted** reading (used as baseline for plausibility checks)
- `self.last_update_time: Optional[datetime]` -- timestamp of last accepted reading

### Accept vs Reject Flow (process_reading, line 1376)

In `process_reading()`, after inference + calculation + correction:

1. **Plausibility check** (`validate_plausibility`, line 591) returns `(is_valid, warnings)`.
   - Rejects on: reverse detection, rate-per-reading too high, sustained rate-per-hour too high.
   - The rejection reason is returned as a warning string (e.g. `"Change per reading too high: 0.2153 m3 (max: 0.1)"`).

2. **If accepted** (`is_valid == True`, line 1513):
   - `self.previous_value = total_value`
   - `self.last_update_time = datetime.now()`
   - `current_state['total_value'] = total_value`
   - `current_state['status'] = 'ok'` or `'warning'`
   - Calls `publish_to_mqtt()` (unless confirmation is needed via BL-07)

3. **If rejected** (`is_valid == False`, line 1560):
   - `current_state['status'] = 'error'`
   - `current_state['total_value'] = total_value` (the rejected reading is stored here!)
   - `current_state['warnings'] = all_warnings` (contains rejection reason)
   - `self.previous_value` is NOT updated (stays at last accepted)
   - `publish_to_mqtt()` is NOT called

### Key Insight: What's Missing

When a reading is rejected, `current_state['total_value']` is overwritten with the rejected value. The last **published** (accepted) value is only in `self.previous_value` -- it's NOT exposed to the template. The template always shows `current_state['total_value']` which, after a rejection, is the rejected value.

So right now, after a rejection:
- The dashboard shows the **rejected** value as "Current Meter Reading" with an ERROR badge
- The user has no visibility into what the last successfully sent value was
- The rejection reason is in the warnings list but doesn't visually associate with the value comparison

### MQTT Publishing (line 1604)

`publish_to_mqtt()` only fires on accepted readings. No changes needed here -- BL-14 is display-only.

### Dashboard Display (status_fragment.html)

The status fragment is loaded via HTMX (`GET /api/status/html`, every 5s polling). The route in `watermeter/watermeter/routes/pages.py` (line 57-74) passes a copy of `current_state` plus `mqtt_connected` as the template context.

The template shows:
- "Current Meter Reading" card with `total_value`, `unit`, `last_update`, `status`
- Warnings/errors section below the card
- Prediction images grid

There is no display of `previous_value` or any "last published" concept.

### What Needs to Change

1. **Backend**: Add `last_published_value`, `last_published_timestamp`, and `last_rejected_value`, `last_rejected_timestamp`, `last_rejected_reasons` to `current_state` so the template can render the comparison.

2. **Template**: When status is `error` and a `last_published_value` exists, show a comparison card: "Last sent: X.XXXX m3 (HH:MM)" vs "Last read: Y.YYYY m3 (HH:MM) -- rejected: [reason]".

3. **CSS**: Style the comparison display.

## Work Packages

### WP-1: Track last-published and last-rejected state in backend
- **Agent**: senior-dev
- **Files**: `watermeter/watermeter/watermeter_service.py`
- **Task**: Add fields to track the last published value/time and last rejected value/time/reasons, and expose them in `current_state` so the template can render a comparison.
- **Details**:
  1. Add new fields to `current_state` dict in `__init__` (after line 60):
     ```python
     'last_published_value': None,
     'last_published_timestamp': None,
     'last_rejected_value': None,
     'last_rejected_timestamp': None,
     'last_rejected_reasons': [],
     ```
  2. In `process_reading()`, when a reading is **accepted** (the `is_valid` branch, ~line 1513):
     - Set `current_state['last_published_value'] = total_value`
     - Set `current_state['last_published_timestamp'] = self.last_update_time.strftime('%H:%M')`
     - Clear the rejected fields: set `last_rejected_value`, `last_rejected_timestamp`, `last_rejected_reasons` all to `None`/`[]`
     - This ensures that once a reading is accepted, the "rejected" banner disappears
  3. In `process_reading()`, when a reading is **rejected** (the `else` branch, ~line 1560):
     - Set `current_state['last_rejected_value'] = total_value`
     - Set `current_state['last_rejected_timestamp'] = datetime.now().strftime('%H:%M')`
     - Set `current_state['last_rejected_reasons'] = plausibility_warnings` (the specific rejection reasons, NOT all_warnings which includes consistency/low-conf warnings)
     - Keep `current_state['last_published_value']` and `last_published_timestamp` unchanged (they persist from the last accepted reading)
  4. Also populate `last_published_value`/`last_published_timestamp` on startup from persistence. In `__init__`, after loading from `self.state_store` (~line 68):
     - If `self.previous_value` is not None, set `current_state['last_published_value'] = self.previous_value` and `current_state['last_published_timestamp'] = self.last_update_time.strftime('%H:%M') if self.last_update_time else None`
  5. In `reset_previous_value()` (~line 1648): also clear all four new fields.
  6. **Edge case**: When BL-07 confirmation timeout/rejection reverts state, the `last_published_value` should remain correct (it was set during the previous accepted reading, and the confirmation revert restores `previous_value` but doesn't touch `current_state['last_published_value']` -- this is correct behavior, no action needed).

### WP-2: Add rejected-reading comparison display to the dashboard template
- **Agent**: frontend
- **Files**: `watermeter/watermeter/templates/status_fragment.html`, `watermeter/watermeter/static/style.css`
- **Task**: Show a comparison card when the last reading was rejected and a previous published value exists.
- **Details**:
  1. In `status_fragment.html`, **after** the `</div>` that closes the `.total-value` card (line 32) and **before** the warnings section (line 36), add a new conditional block:
     ```html
     {% if last_rejected_value is not none and last_published_value is not none %}
     <div class="rejected-comparison">
         <div class="comparison-row published">
             <span class="comparison-label">Last sent</span>
             <span class="comparison-value">{{ "%.4f"|format(last_published_value) }} {{ unit }}</span>
             {% if last_published_timestamp %}
             <span class="comparison-time">({{ last_published_timestamp }})</span>
             {% endif %}
         </div>
         <div class="comparison-row rejected">
             <span class="comparison-label">Last read</span>
             <span class="comparison-value">{{ "%.4f"|format(last_rejected_value) }} {{ unit }}</span>
             {% if last_rejected_timestamp %}
             <span class="comparison-time">({{ last_rejected_timestamp }})</span>
             {% endif %}
             {% if last_rejected_reasons %}
             <span class="comparison-reason">rejected: {{ last_rejected_reasons | join('; ') }}</span>
             {% endif %}
         </div>
     </div>
     {% endif %}
     ```
  2. The block should only render when both values are present. When the next accepted reading comes in, `last_rejected_value` will be cleared to `None` and the block will disappear.
  3. Add CSS styles to `style.css` for `.rejected-comparison`:
     - Container: `border-radius: 12px`, `margin-bottom: 24px`, `overflow: hidden`, no padding (rows handle their own padding)
     - `.comparison-row`: `display: flex`, `flex-wrap: wrap`, `align-items: baseline`, `gap: 8px 12px`, `padding: 12px 20px`
     - `.comparison-row.published`: green-tinted background (`rgba(5, 150, 105, 0.1)`), left border `3px solid var(--success)`
     - `.comparison-row.rejected`: red-tinted background (`rgba(220, 38, 38, 0.1)`), left border `3px solid var(--danger)`
     - `.comparison-label`: `font-weight: 600`, `min-width: 70px`
     - `.comparison-value`: `font-family: monospace`, `font-weight: 700`, `font-size: 15px`
     - `.comparison-time`: `color: var(--text-light)`, `font-size: 13px`
     - `.comparison-reason`: `font-size: 13px`, `color: var(--danger)`, `font-style: italic`, `width: 100%` (forces onto its own line)
  4. Mobile responsive: at `max-width: 768px`, make the comparison rows stack vertically (`flex-direction: column`, `gap: 4px`)

### WP-3: Test the rejected reading display
- **Agent**: tester
- **Files**: `watermeter/tests/` (new or existing test files)
- **Task**: Write tests to verify the new state tracking and template rendering.
- **Details**:
  1. **Unit test for state tracking**: Test that after a rejected reading, `current_state` contains both `last_published_value` (from previous acceptance) and `last_rejected_value` (from the rejection). After a subsequent accepted reading, verify `last_rejected_value` is cleared.
  2. **Unit test for persistence recovery**: Test that on startup with persisted state, `last_published_value` is populated from `previous_value`.
  3. **Unit test for reset**: Test that `reset_previous_value()` clears all four new fields.
  4. **Browser test (Playwright)**: Against `http://localhost:8002`:
     - Verify that the `.rejected-comparison` div does NOT appear when the last reading was accepted (normal state)
     - Optionally: mock or trigger a rejection scenario and verify the comparison appears (this may require manipulating state via API or direct state injection -- if not feasible via the UI alone, skip this and rely on unit tests)
