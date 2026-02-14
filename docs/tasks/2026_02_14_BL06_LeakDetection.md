# BL-06: Continuous Consumption Warning (Leak Detection)

## Goal

Detect sustained high water consumption (running toilet, dripping pipe, open valve) by analyzing
consecutive per-reading rates from `rate_history`, and surface the warning on both the dashboard
(via the existing `warnings` list) and in the MQTT payload (via a new `leak_warning` attribute).

## Architecture

### Detection Logic

**Where**: New method `_check_sustained_consumption()` on `WatermeterService`
(watermeter/watermeter_service.py), called from `process_reading()` at line 797, right after
plausibility warnings are collected and before low-confidence handling.

**What it checks**: Whether the last N consecutive readings each individually show a consumption
rate above a configurable threshold (m^3/h). This is NOT the average over the whole history
window -- it is a per-pair check ensuring every recent interval was elevated.

**Why per-pair, not average**: An average can mask a burst followed by idle. A leak is
characterized by *sustained* flow, meaning every single interval between consecutive readings
shows consumption above threshold. If even one gap shows zero/low flow, the leak warning clears.

**Pseudocode**:

```python
def _check_sustained_consumption(self) -> Optional[str]:
    """
    Check if the last N consecutive readings all show rate above threshold.

    Returns:
        Warning message string if leak detected, None otherwise.
    """
    config = self.config['plausibility']

    # Feature gate
    if not config.get('enable_leak_detection', True):
        return None

    threshold = config.get('sustained_rate_threshold', 0.05)  # m^3/h
    min_readings = config.get('sustained_rate_readings', 3)    # consecutive pairs

    # Need at least min_readings + 1 entries in rate_history to form min_readings pairs
    if len(self.rate_history) < min_readings + 1:
        return None

    # Check the last min_readings consecutive pairs
    # rate_history is [(value, timestamp), ...] ordered oldest-to-newest
    tail = self.rate_history[-(min_readings + 1):]

    for i in range(len(tail) - 1):
        val_prev, ts_prev = tail[i]
        val_curr, ts_curr = tail[i + 1]

        time_diff_s = (ts_curr - ts_prev).total_seconds()
        if time_diff_s <= 0:
            return None  # Bad data, bail

        rate_per_hour = ((val_curr - val_prev) / time_diff_s) * 3600

        if rate_per_hour < threshold:
            return None  # At least one interval below threshold -- no sustained leak

    # All pairs above threshold -- compute summary stats for the warning message
    total_time_s = (tail[-1][1] - tail[0][1]).total_seconds()
    total_time_min = total_time_s / 60
    avg_rate = ((tail[-1][0] - tail[0][0]) / total_time_s) * 3600

    return (
        f"Sustained consumption: {avg_rate:.3f} m\u00b3/h over {total_time_min:.0f} min "
        f"({min_readings} consecutive readings above {threshold} m\u00b3/h)"
    )
```

**Key design decisions**:
- Returns a single warning string (or None), not a bool -- the caller appends it to `all_warnings`
- Uses the tail of `rate_history` (which is already capped at `rate_history_size`)
- Only fires when ALL of the last N pairs exceed threshold, avoiding false positives from single spikes
- Computes human-readable avg rate and duration for the warning message

### Config Changes

New fields under the existing `plausibility` section in `config.yaml` (line ~127):

```yaml
plausibility:
  # ... existing fields ...
  enable_leak_detection: true           # Feature gate (default: true)
  sustained_rate_threshold: 0.05        # m^3/h -- rate above which a single interval counts as "high"
  sustained_rate_readings: 3            # Number of consecutive intervals that must ALL exceed threshold
```

**Defaults rationale**:
- `0.05 m^3/h` = 50 liters/hour = ~0.83 liters/minute -- a running toilet typically uses 1-3 L/min
- `3 consecutive readings` -- with a typical 5-minute cycle, this means ~15 minutes of sustained flow before warning

Schema addition in `config_utils.py` CONFIG_SCHEMA, inside the `plausibility.properties` dict (line ~326):

```python
"enable_leak_detection": {
    "type": "boolean",
    "description": "Detect sustained high consumption (leak warning)",
    "default": True
},
"sustained_rate_threshold": {
    "type": "number",
    "minimum": 0,
    "description": "Rate threshold in m\u00b3/h for leak detection (each interval must exceed this)"
},
"sustained_rate_readings": {
    "type": "integer",
    "minimum": 2,
    "description": "Number of consecutive above-threshold intervals before warning fires"
}
```

### MQTT Changes

In `publish_to_mqtt()` (watermeter_service.py, line 917), the `attributes` dict already contains
`last_update`, `warnings`, and `confidences`. Add a new key:

```python
'leak_warning': bool  # True if leak warning is active, False otherwise
```

This is a flat boolean, not the full message string -- HA automations need a simple trigger
condition. The full message is already in the `warnings` list for humans.

**Implementation**: Pass a `leak_warning: bool` parameter to `publish_to_mqtt()`, or derive it
from the warnings list by checking if any warning starts with `"Sustained consumption:"`.
The cleaner approach is passing it explicitly.

Updated `publish_to_mqtt` signature:

```python
async def publish_to_mqtt(self, value: float, warnings: List[str],
                          predictions: Dict, *, leak_warning: bool = False) -> None:
```

Payload addition (after line 947):

```python
'leak_warning': leak_warning,
```

### Dashboard Changes

**We do NOT modify `watermeter/templates/` or `watermeter/static/`** per CLAUDE.md constraints.

The warning already flows naturally: `_check_sustained_consumption()` returns a string that gets
appended to `all_warnings` in `process_reading()`. The template `status_fragment.html` (lines 36-45)
iterates over `warnings` and renders each as a `<li>`. No template changes needed.

The dashboard will show the leak warning as a regular warning item. The status will be `'warning'`
(not `'error'`) because the reading is still accepted -- it just has a warning attached.

If the UI team wants to style leak warnings differently (e.g. red banner), they can key off the
warning text prefix `"Sustained consumption:"` -- but that is their concern, not ours.

### Persistence Considerations

**`rate_history` is NOT persisted** -- it is an in-memory `List[Tuple[float, datetime]]` initialized
to `[]` on every startup (line 75). This means:

- After a restart, the leak detector needs `sustained_rate_readings + 1` readings to accumulate
  before it can fire. With default config (3 readings, 5-min cycle), that is ~20 minutes.
- This is **acceptable** because:
  1. A real leak will still be there after restart -- it will be re-detected within minutes.
  2. Persisting rate_history adds complexity (need to serialize datetime tuples, handle clock skew
     after restarts, manage stale data from hours/days ago).
  3. The existing `StateStore` only persists `previous_value` and `last_update_time` -- adding
     rate_history would require a schema migration.
  4. False positives from stale rate_history (e.g. service down for 2 hours, then restarts with
     old rate data) would be worse than a brief blind spot after restart.

**Conclusion**: Do not persist rate_history. The brief warm-up period is an acceptable trade-off.

### State tracking for MQTT

We need to track `leak_warning` as a boolean on the service instance so that:
1. It can be included in `current_state` for the dashboard API
2. It can be passed to `publish_to_mqtt`

Add `self.leak_warning: bool = False` to `__init__` (around line 79, near `consecutive_rejections`).
Update it in `process_reading` based on `_check_sustained_consumption()` result.
Include it in `current_state` dict (line 50) so the `/api/status` endpoint exposes it.

## Work Packages

### WP1: Config schema + defaults

**Files**:
- `watermeter/config_utils.py` -- add 3 new properties to `CONFIG_SCHEMA["properties"]["plausibility"]["properties"]` (after line 353)
- `config.yaml` -- add 3 new fields under `plausibility:` section (after line 127)

**Changes**:

1. In `config_utils.py`, add to the `plausibility.properties` dict (after the `enable_consistency_check` entry at line 353):

```python
"enable_leak_detection": {
    "type": "boolean",
    "description": "Detect sustained high consumption (leak warning)",
    "default": True
},
"sustained_rate_threshold": {
    "type": "number",
    "minimum": 0,
    "description": "Rate threshold in m\u00b3/h for leak detection (each interval must exceed this)"
},
"sustained_rate_readings": {
    "type": "integer",
    "minimum": 2,
    "description": "Number of consecutive above-threshold intervals before warning fires"
}
```

2. In `config.yaml`, add after line 127 (`enable_consistency_check: false`):

```yaml
  enable_leak_detection: true            # Detect sustained consumption (leak warning)
  sustained_rate_threshold: 0.05         # m^3/h threshold per interval
  sustained_rate_readings: 3             # Consecutive intervals above threshold before warning
```

### WP2: Leak detection logic

**File**: `watermeter/watermeter_service.py`

**New method**: `_check_sustained_consumption(self) -> Optional[str]`

- Insert after `_calculate_average_rate_per_hour()` (after line 653)
- Full implementation as shown in the Architecture > Detection Logic section above
- Reads config keys: `enable_leak_detection`, `sustained_rate_threshold`, `sustained_rate_readings`
- Reads `self.rate_history` (no mutation)
- Returns warning string or None

**New instance variable**: `self.leak_warning: bool = False`

- Add at line ~79 (after `self.max_consecutive_rejections = 5`)
- Also add `'leak_warning': False` to `self.current_state` dict (line 50 area)

### WP3: MQTT payload extension

**File**: `watermeter/watermeter_service.py`

**Method**: `publish_to_mqtt()` (line 917)

1. Add keyword argument `leak_warning: bool = False` to the signature:

```python
async def publish_to_mqtt(self, value: float, warnings: List[str],
                          predictions: Dict, *, leak_warning: bool = False) -> None:
```

2. Add `'leak_warning': leak_warning` to the `attributes` dict in the payload (after line 947, inside the `attributes` dict):

```python
'attributes': {
    'last_update': datetime.now().isoformat(),
    'warnings': warnings,
    'leak_warning': leak_warning,
    'confidences': { ... }
}
```

### WP4: Warning integration in process_reading

**File**: `watermeter/watermeter_service.py`

**Method**: `process_reading()` (line 744)

Integration point: After line 797 (`all_warnings = consistency_warnings + plausibility_warnings`)
and before line 800 (low confidence handling). Insert:

```python
# 4b. Leak detection (sustained consumption check)
leak_msg = self._check_sustained_consumption()
self.leak_warning = leak_msg is not None
self.current_state['leak_warning'] = self.leak_warning
if leak_msg:
    all_warnings.append(leak_msg)
    logger.warning(leak_msg)
```

Also update the `publish_to_mqtt` call at line 872 to pass the flag:

```python
await self.publish_to_mqtt(total_value, all_warnings, predictions,
                           leak_warning=self.leak_warning)
```

**Reset behavior**: In `reset_previous_value()` (line 960), `rate_history` is already cleared
(line 965). Add `self.leak_warning = False` and `self.current_state['leak_warning'] = False`
to clear the flag on reset.

**Edge case -- rejected readings**: Leak detection only runs on the `rate_history` which is only
updated for accepted readings (line 858). If a reading is rejected, the leak warning state from
the previous cycle persists. This is correct behavior -- we do not want a misread to clear a
legitimate leak warning.

### WP5: Unit tests

**File**: `tests/unit/test_leak_detection.py` (new file)

**Test cases**:

1. **`test_no_warning_insufficient_history`**: rate_history has fewer than `sustained_rate_readings + 1` entries. Expect None.

2. **`test_no_warning_rate_below_threshold`**: rate_history has enough entries but rates are below threshold. Expect None.

3. **`test_warning_all_above_threshold`**: rate_history has `N+1` entries where all N consecutive pairs exceed threshold. Expect warning string with correct rate and duration.

4. **`test_no_warning_one_interval_below`**: rate_history has `N+1` entries but one pair is below threshold. Expect None.

5. **`test_no_warning_feature_disabled`**: `enable_leak_detection` is False. Expect None regardless of history.

6. **`test_warning_message_format`**: Verify the warning string contains rate, duration, and reading count.

7. **`test_leak_warning_in_mqtt_payload`**: Mock MQTT client, run a cycle where leak is detected, verify `leak_warning: true` in published payload.

8. **`test_leak_warning_clears`**: After a leak warning fires, add a reading with low rate. Verify warning clears on next check.

9. **`test_leak_warning_in_current_state`**: Verify `current_state['leak_warning']` is True when leak is active, False otherwise.

10. **`test_leak_warning_reset_on_reset_previous_value`**: Call `reset_previous_value()`, verify `leak_warning` is False.

**Test approach**: Directly manipulate `service.rate_history` with synthetic `(value, datetime)` tuples to avoid needing real image processing. Use `unittest.mock.patch` for config overrides.

## Open Items

- None currently. Design is straightforward and all integration points are identified.

## Progress Log

- 2026-02-14: Task document created. Architecture designed. Work packages defined.

## Done Criteria

- [ ] WP1: Config schema updated in `config_utils.py`, defaults added to `config.yaml`
- [ ] WP2: `_check_sustained_consumption()` method implemented in `watermeter_service.py`
- [ ] WP3: `publish_to_mqtt()` includes `leak_warning` attribute in payload
- [ ] WP4: `process_reading()` calls leak check and wires warning to dashboard + MQTT
- [ ] WP5: All unit tests pass (`python -m pytest tests/unit/test_leak_detection.py -v`)
- [ ] Full test suite passes (`python -m pytest tests/unit/ tests/regression/ --tb=short -q`)
- [ ] `backlog.md` BL-06 status updated to `done`
