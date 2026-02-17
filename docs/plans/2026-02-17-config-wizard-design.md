# Config Wizard Design — MQTT & Home Assistant Step

**Date:** 2026-02-17
**Status:** Approved

## Summary

Extend the existing ROI configuration wizard (`/roi-config`) with a new **Step 5: MQTT & Home Assistant**. This step is always visible (not only during first-run setup) and covers MQTT broker connection, trigger mode selection, and Home Assistant integration.

Additionally, introduce `${ENV_VAR}` substitution in `config.yaml` for secure credential handling.

## Architecture Decision

**Approach: Modular Step** — Step 5 lives in its own JS module (`mqtt-config.js`) embedded into the existing `roi_config.html`. The ROI wizard's step navigation in `roi-config.js` is extended to coordinate with the new module. Same UX, clean code separation.

## Step Flow

```
Step 0: Image Source (existing, setup-mode only)
Step 1: Rotation (existing)
Step 2: Alignment Markers (existing)
Step 3: Digit ROIs (existing)
Step 4: Analog ROIs (existing)
Step 5: MQTT & Home Assistant (NEW)
  ├── 5a: MQTT Broker (broker, port, credentials, test button)
  ├── 5b: Trigger Mode (mqtt / cyclic / both + interval)
  └── 5c: Home Assistant (toggle, publish topic, discovery prefix,
           device name, sensor type, unit, update interval)
```

## API Endpoints

New router: `watermeter/routes/mqtt.py`

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/mqtt/config` | Load current MQTT/trigger/HA config (raw, ENV vars not resolved) |
| `POST` | `/api/mqtt/config` | Save MQTT/trigger/HA config + hot-reload |
| `POST` | `/api/mqtt/test` | Test MQTT broker connection (connect + disconnect) |

### GET /api/mqtt/config — Response

```json
{
  "mqtt": {
    "broker": "192.168.4.38",
    "port": 1883,
    "username": "${MQTT_USER}",
    "password": "${MQTT_PASSWORD}",
    "client_id": "watermeter-ai-service",
    "keepalive": 60
  },
  "trigger": {
    "mode": "mqtt",
    "cyclic_interval": 300,
    "mqtt_topic": "watermeter/status",
    "mqtt_payload": "Flow finished"
  },
  "homeassistant": {
    "enabled": true,
    "publish_topic": "watermeter/reading",
    "discovery_prefix": "homeassistant",
    "device_name": "Wasserzähler",
    "sensor_type": "total_increasing",
    "unit": "m³",
    "update_interval": 300
  }
}
```

### POST /api/mqtt/config — Request

Same JSON shape as GET response. Writes to corresponding sections in `config.yaml` and calls `service.reload_config()`.

### POST /api/mqtt/test — Request/Response

Request: `{ "broker": "...", "port": 1883, "username": "...", "password": "..." }`
ENV vars in username/password are resolved before connecting.

Success: `{ "status": "ok", "message": "Connected to broker" }`
Error: `{ "status": "error", "message": "Connection refused: ..." }`

## ENV Variable Substitution

**Syntax:** `${ENV_VAR_NAME}` (shell-style)

**Implementation in `config_utils.py`:**

```python
import re, os

def resolve_env_vars(value):
    """Replace ${VAR_NAME} patterns with environment variable values."""
    if isinstance(value, str):
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value
        )
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(v) for v in value]
    return value
```

- Called when loading config for runtime use
- NOT called when saving — `${...}` tokens stay in YAML
- Existing hardcoded ENV override logic (MQTT_USERNAME/MQTT_PASSWORD in watermeter_service.py) remains as fallback

## UI Layout — Step 5

```
┌─────────────────────────────────────────────────┐
│  Step 5: MQTT & Home Assistant                  │
│                                                 │
│  ── MQTT Broker ──────────────────────────────  │
│  Broker:    [192.168.4.38        ]              │
│  Port:      [1883                ]              │
│  Username:  [${MQTT_USER}        ]  (optional)  │
│  Password:  [${MQTT_PASSWORD}    ]  (optional)  │
│  Client ID: [watermeter-ai       ]              │
│                                                 │
│  [ Test Connection ]  ✅ Connected              │
│                                                 │
│  ── Trigger Mode ─────────────────────────────  │
│  (●) MQTT   ( ) Cyclic   ( ) Both              │
│                                                 │
│  ┌─ MQTT Trigger (visible for mqtt/both) ────┐  │
│  │ Topic:   [watermeter/status              ] │  │
│  │ Payload: [Flow finished                  ] │  │
│  └────────────────────────────────────────────┘  │
│  ┌─ Cyclic (visible for cyclic/both) ────────┐  │
│  │ Interval: [300] seconds                    │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ── Home Assistant ───────────────────────────  │
│  [Toggle] Enable HA Discovery                   │
│                                                 │
│  ┌─ (visible when HA enabled) ───────────────┐  │
│  │ Publish Topic:    [watermeter/reading     ]│  │
│  │ Discovery Prefix: [homeassistant          ]│  │
│  │ Device Name:      [Wasserzähler           ]│  │
│  │ Sensor Type: [▼ total_increasing       ]  │  │
│  │ Unit:        [▼ m³                     ]  │  │
│  │ Update Interval: [300] sec                │  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│           [ Save & Continue ]                   │
└─────────────────────────────────────────────────┘
```

### Interactions

- **Test Connection**: Async call to `POST /api/mqtt/test`. Spinner during test, green check on success, red error text on failure.
- **Trigger Mode radio buttons**: Show/hide MQTT topic fields vs cyclic interval section.
- **HA Toggle**: Show/hide HA configuration fields.
- **Sensor Type dropdown**: `total_increasing` (meter reading), `measurement` (current flow).
- **Unit dropdown**: `m³`, `L`, `gal`.
- **Password field**: `type="password"` with eye toggle. Shows `${...}` as cleartext when it's an ENV var reference.
- **Save & Continue**: Validates (broker required when mode=mqtt/both), saves via API, shows success. In setup mode, shows "Setup Complete!" section afterward.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| MQTT test fails | Red text under test button, config can still be saved |
| Broker empty with trigger=mqtt | Client-side validation blocks save, field highlighted red |
| ENV var not set | `${VAR}` stays as string — warning during test ("Variable MQTT_PASSWORD not set") |
| Config save fails | Toast/banner with error message, form stays open |
| Invalid port | Client-side validation (1-65535), save blocked |

## Files Changed

| File | Change |
|------|--------|
| `watermeter/static/js/mqtt-config.js` | **NEW** — Step 5 logic |
| `watermeter/routes/mqtt.py` | **NEW** — API endpoints |
| `watermeter/templates/roi_config.html` | Add Step 5 container |
| `watermeter/static/js/roi-config.js` | Extend step navigation for Step 5 |
| `watermeter/config_utils.py` | Add `resolve_env_vars()` |
| `watermeter/main.py` | Register MQTT router |

## Testing Strategy

- Unit tests for `resolve_env_vars()` — various patterns, nested dicts, missing vars
- Unit tests for API endpoints — mock MQTT client for test endpoint
- Integration tests for save → reload → config-check roundtrip
- Playwright tests for Step 5 UI: fill fields, radio toggle, HA toggle show/hide, save flow

## Out of Scope

- MQTT dashboard widget (live connection status) — separate feature
- TLS/SSL for MQTT — can be configured via YAML editor
- Multiple MQTT brokers — not supported
- Auto-discovery of brokers on network
