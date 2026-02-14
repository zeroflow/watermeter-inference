# Task: Add Cyclic Trigger Mode

## Goal
Add a new trigger mode that cyclically fetches images and processes them, independent of AIOTE MQTT triggers.

## Plan

### Changes:
1. **`config.yaml`**: Add `trigger` section with `mode` and `cyclic_interval`
2. **`config_utils.py`**: Add defaults + migration for `trigger` section
3. **`watermeter_service.py`**: Add `start_cyclic_loop()` / `stop_cyclic_loop()`, make MQTT trigger subscription conditional
4. **`app.py`**: Make lifespan modal based on `trigger.mode`
5. **Tests**: Unit tests for cyclic loop and conditional MQTT behavior

### Config:
```yaml
trigger:
  mode: "mqtt"          # "mqtt" | "cyclic" | "both"
  cyclic_interval: 300  # seconds
```

### Behavior:
- `mqtt`: MQTT trigger + subscribe (today's behavior)
- `cyclic`: Periodic loop, MQTT only for publishing (no trigger subscribe)
- `both`: MQTT trigger + cyclic fallback

## Progress
- [x] config.yaml + config_utils.py (trigger section + JSON schema)
- [x] watermeter_service.py (cyclic loop + conditional MQTT subscribe)
- [x] app.py lifespan (modal startup based on trigger.mode)
- [x] Tests (17 new tests)
- [x] All 121 tests pass (104 existing + 17 new)

## Done Criteria
- [x] All 3 modes work correctly (mqtt, cyclic, both)
- [x] Existing behavior unchanged with `mode: "mqtt"` (default)
- [x] Tests cover config parsing, defaults, cyclic loop, MQTT subscription
- [x] All existing + new tests pass
