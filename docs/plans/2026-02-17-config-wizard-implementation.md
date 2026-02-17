# Config Wizard (MQTT & HA Step) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the ROI wizard with Step 5 for MQTT broker, trigger mode, and Home Assistant configuration, plus `${ENV_VAR}` substitution in config.yaml.

**Architecture:** New Step 5 as a modular JS file (`mqtt-config.js`) embedded into the existing `roi_config.html`. New FastAPI router (`routes/mqtt.py`) for GET/POST config + connection test. ENV var substitution added to `config_utils.py`.

**Tech Stack:** FastAPI, Paho MQTT (already a dependency), ruamel.yaml, vanilla JS, CSS variables from existing design system.

**Design Doc:** `docs/plans/2026-02-17-config-wizard-design.md`

---

## Task 1: ENV Variable Substitution (`config_utils.py`)

**Files:**
- Modify: `watermeter/config_utils.py` (add after `load_config()` at line 43)
- Test: `tests/unit/test_env_var_substitution.py` (create)

### Step 1: Write failing tests

Create `tests/unit/test_env_var_substitution.py`:

```python
"""Tests for ${ENV_VAR} substitution in config values."""

import os
import pytest
from watermeter.config_utils import resolve_env_vars


class TestResolveEnvVars:
    """Test resolve_env_vars() function."""

    def test_simple_string_substitution(self, monkeypatch):
        monkeypatch.setenv("MQTT_HOST", "192.168.1.1")
        assert resolve_env_vars("${MQTT_HOST}") == "192.168.1.1"

    def test_string_with_surrounding_text(self, monkeypatch):
        monkeypatch.setenv("PORT", "1883")
        assert resolve_env_vars("broker:${PORT}") == "broker:1883"

    def test_multiple_vars_in_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "1883")
        assert resolve_env_vars("${HOST}:${PORT}") == "localhost:1883"

    def test_missing_env_var_keeps_original(self):
        result = resolve_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"

    def test_plain_string_unchanged(self):
        assert resolve_env_vars("hello world") == "hello world"

    def test_empty_string(self):
        assert resolve_env_vars("") == ""

    def test_dict_substitution(self, monkeypatch):
        monkeypatch.setenv("BROKER", "10.0.0.1")
        data = {"broker": "${BROKER}", "port": 1883}
        result = resolve_env_vars(data)
        assert result == {"broker": "10.0.0.1", "port": 1883}

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("USER", "admin")
        data = {"mqtt": {"username": "${USER}", "port": 1883}}
        result = resolve_env_vars(data)
        assert result == {"mqtt": {"username": "admin", "port": 1883}}

    def test_list_substitution(self, monkeypatch):
        monkeypatch.setenv("TOPIC", "watermeter/status")
        data = ["${TOPIC}", "other"]
        result = resolve_env_vars(data)
        assert result == ["watermeter/status", "other"]

    def test_non_string_passthrough(self):
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None
        assert resolve_env_vars(3.14) == 3.14

    def test_mixed_nested_structure(self, monkeypatch):
        monkeypatch.setenv("PASS", "secret")
        data = {
            "mqtt": {
                "password": "${PASS}",
                "port": 1883,
                "topics": ["a", "${PASS}"],
            }
        }
        result = resolve_env_vars(data)
        assert result["mqtt"]["password"] == "secret"
        assert result["mqtt"]["port"] == 1883
        assert result["mqtt"]["topics"] == ["a", "secret"]

    def test_dollar_without_braces_unchanged(self):
        assert resolve_env_vars("$NOT_A_VAR") == "$NOT_A_VAR"

    def test_partial_syntax_unchanged(self):
        assert resolve_env_vars("${") == "${"
        assert resolve_env_vars("${}") == "${}"
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/test_env_var_substitution.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_env_vars'`

### Step 3: Implement `resolve_env_vars()`

In `watermeter/config_utils.py`, add after line 43 (after `load_config()`), before `save_config()`:

```python
import re

def resolve_env_vars(value):
    """Replace ${VAR_NAME} patterns with environment variable values.

    Unset variables keep their ${VAR_NAME} syntax intact.
    Works recursively on dicts and lists.
    """
    if isinstance(value, str):
        return re.sub(
            r'\$\{(\w+)\}',
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(v) for v in value]
    return value
```

Note: `os` is already imported in config_utils.py. `re` needs to be added to imports at the top.

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_env_var_substitution.py -v`
Expected: All PASS

### Step 5: Commit

```bash
git add tests/unit/test_env_var_substitution.py watermeter/config_utils.py
git commit -m "claude: add resolve_env_vars() for \${ENV_VAR} substitution in config"
```

---

## Task 2: MQTT Config API Router (`routes/mqtt.py`)

**Files:**
- Create: `watermeter/routes/mqtt.py`
- Modify: `watermeter/app.py` (register router, lines 161-177)
- Test: `tests/unit/test_mqtt_routes.py` (create)

**Prereqs:** Task 1 (needs `resolve_env_vars`)

### Step 1: Write failing tests

Create `tests/unit/test_mqtt_routes.py`:

```python
"""Tests for MQTT config API routes."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def mock_service():
    """Mock WatermeterService for route tests."""
    service = MagicMock()
    service.config = {
        "mqtt": {
            "broker": "192.168.4.38",
            "port": 1883,
            "username": "${MQTT_USER}",
            "password": "${MQTT_PASS}",
            "client_id": "watermeter-ai",
            "keepalive": 60,
            "trigger_topic": "watermeter/status",
            "trigger_payload": "Flow finished",
            "reset_topic": "watermeter/reset",
        },
        "trigger": {
            "mode": "mqtt",
            "cyclic_interval": 300,
        },
        "homeassistant": {
            "enabled": False,
            "discovery_prefix": "homeassistant",
            "publish_topic": "watermeter/reading",
        },
    }
    service.reload_config = MagicMock(return_value={"config_updated": True, "mqtt_reconnected": False})
    return service


@pytest.fixture
def client(mock_service):
    """TestClient with mocked service."""
    with patch("watermeter.routes.mqtt.get_service", return_value=mock_service):
        from watermeter.app import app
        with TestClient(app) as c:
            yield c


class TestGetMqttConfig:
    """GET /api/mqtt/config tests."""

    def test_returns_mqtt_section(self, client, mock_service):
        resp = client.get("/api/mqtt/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "mqtt" in data
        assert data["mqtt"]["broker"] == "192.168.4.38"

    def test_returns_trigger_section(self, client):
        resp = client.get("/api/mqtt/config")
        data = resp.json()
        assert "trigger" in data
        assert data["trigger"]["mode"] == "mqtt"

    def test_returns_homeassistant_section(self, client):
        resp = client.get("/api/mqtt/config")
        data = resp.json()
        assert "homeassistant" in data

    def test_env_vars_not_resolved(self, client):
        """GET should return raw ${...} tokens, not resolved values."""
        resp = client.get("/api/mqtt/config")
        data = resp.json()
        assert data["mqtt"]["username"] == "${MQTT_USER}"


class TestSaveMqttConfig:
    """POST /api/mqtt/config tests."""

    def test_save_returns_success(self, client, mock_service):
        with patch("watermeter.routes.mqtt.config_utils") as mock_cu:
            mock_cu.load_config.return_value = dict(mock_service.config)
            mock_cu.save_config = MagicMock()
            resp = client.post("/api/mqtt/config", json={
                "mqtt": {"broker": "10.0.0.1", "port": 1883},
                "trigger": {"mode": "cyclic", "cyclic_interval": 60},
                "homeassistant": {"enabled": True},
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_save_calls_reload(self, client, mock_service):
        with patch("watermeter.routes.mqtt.config_utils") as mock_cu:
            mock_cu.load_config.return_value = dict(mock_service.config)
            mock_cu.save_config = MagicMock()
            client.post("/api/mqtt/config", json={
                "mqtt": {"broker": "10.0.0.1"},
                "trigger": {},
                "homeassistant": {},
            })
            mock_service.reload_config.assert_called_once()

    def test_save_missing_body_returns_error(self, client):
        resp = client.post("/api/mqtt/config", json={})
        # Should still work — empty sections are valid (partial update)
        assert resp.status_code == 200


class TestMqttConnectionTest:
    """POST /api/mqtt/test tests."""

    def test_successful_connection(self, client):
        with patch("watermeter.routes.mqtt.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.connect.return_value = None

            resp = client.post("/api/mqtt/test", json={
                "broker": "192.168.4.38",
                "port": 1883,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            mock_client.disconnect.assert_called_once()

    def test_connection_refused(self, client):
        with patch("watermeter.routes.mqtt.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.connect.side_effect = ConnectionRefusedError("Connection refused")

            resp = client.post("/api/mqtt/test", json={
                "broker": "192.168.4.38",
                "port": 1883,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"
            assert "refused" in data["message"].lower()

    def test_resolves_env_vars_in_credentials(self, client, monkeypatch):
        monkeypatch.setenv("TEST_MQTT_USER", "admin")
        monkeypatch.setenv("TEST_MQTT_PASS", "secret")
        with patch("watermeter.routes.mqtt.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            resp = client.post("/api/mqtt/test", json={
                "broker": "localhost",
                "port": 1883,
                "username": "${TEST_MQTT_USER}",
                "password": "${TEST_MQTT_PASS}",
            })
            assert resp.status_code == 200
            mock_client.username_pw_set.assert_called_once_with("admin", "secret")

    def test_missing_broker_returns_error(self, client):
        resp = client.post("/api/mqtt/test", json={"port": 1883})
        assert resp.status_code == 400

    def test_warns_about_unresolved_env_vars(self, client):
        with patch("watermeter.routes.mqtt.mqtt.Client") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.connect.return_value = None

            resp = client.post("/api/mqtt/test", json={
                "broker": "localhost",
                "port": 1883,
                "username": "${UNSET_VAR}",
            })
            data = resp.json()
            # Should warn that env var is not set
            assert "warning" in data or data["status"] == "ok"
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/test_mqtt_routes.py -v`
Expected: FAIL — `ImportError`

### Step 3: Create the MQTT router

Create `watermeter/routes/mqtt.py`:

```python
"""MQTT and Home Assistant configuration routes."""

import logging
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import config_utils
from ..config_utils import resolve_env_vars
from ..watermeter_service import get_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/mqtt/config",
    tags=["MQTT Configuration"],
    summary="Get MQTT, trigger, and Home Assistant config",
)
async def get_mqtt_config():
    """Return current MQTT/trigger/HA config with raw ${ENV_VAR} tokens."""
    service = get_service()
    config = service.config

    mqtt_config = config.get("mqtt", {})
    trigger_config = config.get("trigger", {})
    ha_config = config.get("homeassistant", {})

    # Re-read raw config from file to get ${...} tokens
    # (service.config has env vars already resolved by start_mqtt)
    try:
        raw_config = config_utils.load_config(Path("config.yaml"))
        mqtt_raw = dict(raw_config.get("mqtt", {}))
        trigger_raw = dict(raw_config.get("trigger", {}))
        ha_raw = dict(raw_config.get("homeassistant", {}))
    except Exception:
        mqtt_raw = dict(mqtt_config)
        trigger_raw = dict(trigger_config)
        ha_raw = dict(ha_config)

    return JSONResponse({
        "mqtt": mqtt_raw,
        "trigger": trigger_raw,
        "homeassistant": ha_raw,
    })


@router.post(
    "/api/mqtt/config",
    tags=["MQTT Configuration"],
    summary="Save MQTT, trigger, and Home Assistant config",
)
async def save_mqtt_config(request: Request):
    """Save MQTT/trigger/HA config sections and hot-reload."""
    try:
        data = await request.json()

        # Load current config (preserves comments via ruamel)
        config_path = Path("config.yaml")
        config = config_utils.load_config(config_path)

        # Merge submitted sections into config
        for section in ("mqtt", "trigger", "homeassistant"):
            if section in data and data[section]:
                if section not in config:
                    config[section] = {}
                for key, value in data[section].items():
                    config[section][key] = value

        # Save with comment preservation
        config_utils.save_config(config, config_path)

        # Reload service with plain dict (not CommentedMap)
        with open(config_path, "r") as f:
            plain_config = yaml.safe_load(f)

        service = get_service()
        reload_result = service.reload_config(plain_config)

        msg = "Configuration saved"
        if reload_result.get("mqtt_reconnected"):
            msg += " — MQTT reconnected"

        return JSONResponse({"success": True, "message": msg})

    except Exception as e:
        logger.error(f"Error saving MQTT config: {e}")
        return JSONResponse(
            {"success": False, "message": f"Error: {str(e)}"},
            status_code=500,
        )


@router.post(
    "/api/mqtt/test",
    tags=["MQTT Configuration"],
    summary="Test MQTT broker connection",
)
async def test_mqtt_connection(request: Request):
    """Test connection to MQTT broker without affecting the running service."""
    try:
        data = await request.json()
        broker = data.get("broker", "").strip()
        port = data.get("port", 1883)

        if not broker:
            return JSONResponse(
                {"status": "error", "message": "Broker address is required"},
                status_code=400,
            )

        # Resolve ${ENV_VAR} in credentials
        username = resolve_env_vars(data.get("username", ""))
        password = resolve_env_vars(data.get("password", ""))

        # Check for unresolved env vars
        warnings = []
        for field, value in [("username", username), ("password", password)]:
            if "${" in str(value):
                warnings.append(f"Environment variable in {field} is not set")

        # Create temporary client for testing
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="watermeter-connection-test",
        )

        if username:
            client.username_pw_set(username, password or None)

        try:
            client.connect(broker, int(port), keepalive=5)
            client.disconnect()
        except (ConnectionRefusedError, OSError, TimeoutError) as e:
            return JSONResponse({
                "status": "error",
                "message": f"Connection failed: {str(e)}",
            })

        result = {"status": "ok", "message": f"Connected to {broker}:{port}"}
        if warnings:
            result["warnings"] = warnings
        return JSONResponse(result)

    except Exception as e:
        logger.error(f"MQTT test error: {e}")
        return JSONResponse(
            {"status": "error", "message": f"Error: {str(e)}"},
            status_code=500,
        )
```

### Step 4: Register router in `app.py`

In `watermeter/app.py`, add after the last router import (around line 168, after the synthetic_router line):

```python
from .routes.mqtt import router as mqtt_router  # noqa: E402
```

And add after the last `app.include_router(...)` call (around line 177):

```python
app.include_router(mqtt_router)
```

### Step 5: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/test_mqtt_routes.py -v`
Expected: All PASS (some tests may need adjustment based on exact mock wiring)

### Step 6: Run full test suite

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=30`
Expected: All existing tests still pass

### Step 7: Commit

```bash
git add watermeter/routes/mqtt.py watermeter/app.py tests/unit/test_mqtt_routes.py
git commit -m "claude: add MQTT config API routes (GET/POST config + connection test)"
```

---

## Task 3: Step 5 HTML Template

**Files:**
- Modify: `watermeter/templates/roi_config.html` (add after line 250, before setup-complete)
- Modify: `watermeter/static/style.css` (add Step 5 styles at end)

### Step 1: Add Step 5 section to `roi_config.html`

After the Step 4 closing `</section>` (line 250) and before the `</main>` (line 251), insert the Step 5 HTML:

```html
    <!-- Step 5: MQTT & Home Assistant -->
    <section id="step-mqtt" class="roi-step">
        <h3>Step 5: MQTT & Home Assistant</h3>

        <div id="mqtt-edit">
            <!-- 5a: MQTT Broker -->
            <div class="mqtt-section">
                <h4 class="mqtt-section-title">MQTT Broker</h4>
                <div class="mqtt-form-grid">
                    <label for="mqtt-broker">Broker</label>
                    <input type="text" id="mqtt-broker" placeholder="192.168.x.x" />

                    <label for="mqtt-port">Port</label>
                    <input type="number" id="mqtt-port" value="1883" min="1" max="65535" />

                    <label for="mqtt-username">Username <span class="optional-hint">(optional)</span></label>
                    <input type="text" id="mqtt-username" placeholder="${MQTT_USER} or plain text" />

                    <label for="mqtt-password">Password <span class="optional-hint">(optional)</span></label>
                    <div class="password-wrapper">
                        <input type="password" id="mqtt-password" placeholder="${MQTT_PASSWORD} or plain text" />
                        <button type="button" class="btn-icon" id="mqtt-password-toggle" title="Show/hide password">
                            <span id="mqtt-password-eye">&#128065;</span>
                        </button>
                    </div>

                    <label for="mqtt-client-id">Client ID</label>
                    <input type="text" id="mqtt-client-id" placeholder="watermeter-ai-service" />
                </div>

                <div class="mqtt-test-row">
                    <button class="btn btn-secondary" id="mqtt-test-btn" onclick="MqttConfig.testConnection()">
                        Test Connection
                    </button>
                    <span id="mqtt-test-result"></span>
                </div>
            </div>

            <!-- 5b: Trigger Mode -->
            <div class="mqtt-section">
                <h4 class="mqtt-section-title">Trigger Mode</h4>
                <div class="mqtt-radio-group">
                    <label class="mqtt-radio-label">
                        <input type="radio" name="trigger-mode" value="mqtt" checked /> MQTT
                    </label>
                    <label class="mqtt-radio-label">
                        <input type="radio" name="trigger-mode" value="cyclic" /> Cyclic
                    </label>
                    <label class="mqtt-radio-label">
                        <input type="radio" name="trigger-mode" value="both" /> Both
                    </label>
                </div>

                <div id="trigger-mqtt-fields" class="mqtt-conditional-fields">
                    <div class="mqtt-form-grid">
                        <label for="trigger-topic">Trigger Topic</label>
                        <input type="text" id="trigger-topic" placeholder="watermeter/status" />

                        <label for="trigger-payload">Trigger Payload</label>
                        <input type="text" id="trigger-payload" placeholder="Flow finished" />
                    </div>
                </div>

                <div id="trigger-cyclic-fields" class="mqtt-conditional-fields" style="display: none;">
                    <div class="mqtt-form-grid">
                        <label for="cyclic-interval">Interval (seconds)</label>
                        <input type="number" id="cyclic-interval" value="300" min="10" />
                    </div>
                </div>
            </div>

            <!-- 5c: Home Assistant -->
            <div class="mqtt-section">
                <h4 class="mqtt-section-title">Home Assistant</h4>
                <div class="toggle-row">
                    <label class="toggle-switch">
                        <input type="checkbox" id="ha-enabled" />
                        <span class="toggle-slider"></span>
                    </label>
                    <span>Enable HA Discovery</span>
                </div>

                <div id="ha-fields" class="mqtt-conditional-fields" style="display: none;">
                    <div class="mqtt-form-grid">
                        <label for="ha-publish-topic">Publish Topic</label>
                        <input type="text" id="ha-publish-topic" placeholder="watermeter/reading" />

                        <label for="ha-discovery-prefix">Discovery Prefix</label>
                        <input type="text" id="ha-discovery-prefix" placeholder="homeassistant" />

                        <label for="ha-device-name">Device Name</label>
                        <input type="text" id="ha-device-name" placeholder="Wasserzähler" />

                        <label for="ha-sensor-type">Sensor Type</label>
                        <select id="ha-sensor-type">
                            <option value="total_increasing">Total Increasing (meter reading)</option>
                            <option value="measurement">Measurement (current flow)</option>
                        </select>

                        <label for="ha-unit">Unit</label>
                        <select id="ha-unit">
                            <option value="m³">m³</option>
                            <option value="L">L</option>
                            <option value="gal">gal</option>
                        </select>

                        <label for="ha-update-interval">Update Interval (sec)</label>
                        <input type="number" id="ha-update-interval" value="300" min="10" />
                    </div>
                </div>
            </div>

            <div class="mqtt-actions">
                <button class="btn btn-primary" id="mqtt-save-btn" onclick="MqttConfig.save()">
                    Save & Continue
                </button>
                <span id="mqtt-save-result"></span>
            </div>
        </div>

        <div id="mqtt-saved" style="display: none;">
            <div class="saved-indicator">
                <span class="saved-check">&#10003;</span> MQTT & Home Assistant configured
            </div>
            <button class="btn btn-secondary" onclick="MqttConfig.edit()">Change Settings</button>
        </div>
    </section>
```

### Step 2: Add the `mqtt-config.js` script tag

In `roi_config.html`, before the closing `</body>` tag (after the `roi-config.js` script tag at line 265), add:

```html
<script src="/static/mqtt-config.js"></script>
```

### Step 3: Add Step 5 CSS to `style.css`

Append to `watermeter/static/style.css` (after the last rule):

```css
/* ── Step 5: MQTT & HA Config ── */

.mqtt-section {
    margin-bottom: 1.5rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
}

.mqtt-section:last-of-type {
    border-bottom: none;
}

.mqtt-section-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text-dark);
    margin-bottom: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}

.mqtt-form-grid {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 0.5rem 1rem;
    align-items: center;
}

.mqtt-form-grid label {
    font-size: 0.85rem;
    color: var(--text-light);
}

.mqtt-form-grid input,
.mqtt-form-grid select {
    padding: 0.5rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 0.9rem;
    background: white;
}

.mqtt-form-grid input:focus,
.mqtt-form-grid select:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.15);
}

.mqtt-form-grid input.error {
    border-color: var(--danger);
}

.optional-hint {
    font-size: 0.75rem;
    color: var(--text-light);
}

.password-wrapper {
    display: flex;
    gap: 0.25rem;
}

.password-wrapper input {
    flex: 1;
}

.btn-icon {
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem;
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
}

.mqtt-test-row {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 1rem;
}

#mqtt-test-result {
    font-size: 0.85rem;
}

#mqtt-test-result.success {
    color: var(--success);
}

#mqtt-test-result.error {
    color: var(--danger);
}

#mqtt-test-result.loading {
    color: var(--text-light);
}

.mqtt-radio-group {
    display: flex;
    gap: 1.5rem;
    margin-bottom: 1rem;
}

.mqtt-radio-label {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.9rem;
    cursor: pointer;
}

.mqtt-conditional-fields {
    margin-top: 0.75rem;
    padding: 0.75rem;
    background: var(--bg-light);
    border-radius: 8px;
}

.toggle-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
    font-size: 0.9rem;
}

.mqtt-actions {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 1.5rem;
    padding-top: 1rem;
}

#mqtt-save-result {
    font-size: 0.85rem;
}

#mqtt-save-result.success {
    color: var(--success);
}

#mqtt-save-result.error {
    color: var(--danger);
}

.saved-indicator {
    font-size: 0.95rem;
    color: var(--success);
    margin-bottom: 0.75rem;
}

.saved-check {
    font-weight: bold;
}

@media (max-width: 768px) {
    .mqtt-form-grid {
        grid-template-columns: 1fr;
    }

    .mqtt-radio-group {
        flex-direction: column;
        gap: 0.5rem;
    }
}
```

### Step 4: Commit

```bash
git add watermeter/templates/roi_config.html watermeter/static/style.css
git commit -m "claude: add Step 5 HTML template and CSS for MQTT/HA config"
```

---

## Task 4: Step 5 JavaScript Module (`mqtt-config.js`)

**Files:**
- Create: `watermeter/static/mqtt-config.js`

### Step 1: Create `mqtt-config.js`

```javascript
/**
 * MQTT & Home Assistant configuration — Step 5 of the ROI wizard.
 * Loaded after roi-config.js. Exposes window.MqttConfig for onclick handlers.
 */
const MqttConfig = (function () {
    // ── DOM refs ──
    const fields = {
        broker: () => document.getElementById('mqtt-broker'),
        port: () => document.getElementById('mqtt-port'),
        username: () => document.getElementById('mqtt-username'),
        password: () => document.getElementById('mqtt-password'),
        clientId: () => document.getElementById('mqtt-client-id'),
        triggerTopic: () => document.getElementById('trigger-topic'),
        triggerPayload: () => document.getElementById('trigger-payload'),
        cyclicInterval: () => document.getElementById('cyclic-interval'),
        haEnabled: () => document.getElementById('ha-enabled'),
        haPublishTopic: () => document.getElementById('ha-publish-topic'),
        haDiscoveryPrefix: () => document.getElementById('ha-discovery-prefix'),
        haDeviceName: () => document.getElementById('ha-device-name'),
        haSensorType: () => document.getElementById('ha-sensor-type'),
        haUnit: () => document.getElementById('ha-unit'),
        haUpdateInterval: () => document.getElementById('ha-update-interval'),
    };

    // ── Init ──

    function init() {
        _setupTriggerModeRadios();
        _setupHaToggle();
        _setupPasswordToggle();
        loadConfig();
    }

    // ── Load config from server ──

    async function loadConfig() {
        try {
            const resp = await fetch('/api/mqtt/config');
            const data = await resp.json();

            // MQTT broker
            const mqtt = data.mqtt || {};
            fields.broker().value = mqtt.broker || '';
            fields.port().value = mqtt.port || 1883;
            fields.username().value = mqtt.username || '';
            fields.password().value = mqtt.password || '';
            fields.clientId().value = mqtt.client_id || '';

            // Trigger
            const trigger = data.trigger || {};
            const mode = trigger.mode || 'mqtt';
            const radio = document.querySelector(`input[name="trigger-mode"][value="${mode}"]`);
            if (radio) radio.checked = true;
            _updateTriggerVisibility(mode);

            fields.triggerTopic().value = mqtt.trigger_topic || trigger.mqtt_topic || '';
            fields.triggerPayload().value = mqtt.trigger_payload || trigger.mqtt_payload || '';
            fields.cyclicInterval().value = trigger.cyclic_interval || 300;

            // Home Assistant
            const ha = data.homeassistant || {};
            fields.haEnabled().checked = !!ha.enabled;
            _updateHaVisibility(ha.enabled);

            fields.haPublishTopic().value = ha.publish_topic || '';
            fields.haDiscoveryPrefix().value = ha.discovery_prefix || 'homeassistant';
            fields.haDeviceName().value = ha.device_name || ha.device?.name || '';
            fields.haSensorType().value = ha.sensor_type || 'total_increasing';
            fields.haUnit().value = ha.unit || 'm³';
            fields.haUpdateInterval().value = ha.update_interval || 300;
        } catch (e) {
            console.error('Failed to load MQTT config:', e);
        }
    }

    // ── Save config to server ──

    async function save() {
        const resultEl = document.getElementById('mqtt-save-result');
        const mode = document.querySelector('input[name="trigger-mode"]:checked')?.value || 'mqtt';

        // Validate
        const broker = fields.broker().value.trim();
        if ((mode === 'mqtt' || mode === 'both') && !broker) {
            fields.broker().classList.add('error');
            resultEl.textContent = 'Broker is required for MQTT trigger mode';
            resultEl.className = 'error';
            return;
        }
        fields.broker().classList.remove('error');

        const port = parseInt(fields.port().value);
        if (isNaN(port) || port < 1 || port > 65535) {
            resultEl.textContent = 'Port must be between 1 and 65535';
            resultEl.className = 'error';
            return;
        }

        resultEl.textContent = 'Saving...';
        resultEl.className = 'loading';

        const payload = {
            mqtt: {
                broker: broker,
                port: port,
                username: fields.username().value,
                password: fields.password().value,
                client_id: fields.clientId().value || 'watermeter-ai-service',
                keepalive: 60,
                trigger_topic: fields.triggerTopic().value,
                trigger_payload: fields.triggerPayload().value,
                reset_topic: 'watermeter/reset',
            },
            trigger: {
                mode: mode,
                cyclic_interval: parseInt(fields.cyclicInterval().value) || 300,
            },
            homeassistant: {
                enabled: fields.haEnabled().checked,
                publish_topic: fields.haPublishTopic().value,
                discovery_prefix: fields.haDiscoveryPrefix().value || 'homeassistant',
                device_name: fields.haDeviceName().value,
                sensor_type: fields.haSensorType().value,
                unit: fields.haUnit().value,
                update_interval: parseInt(fields.haUpdateInterval().value) || 300,
            },
        };

        try {
            const resp = await fetch('/api/mqtt/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();

            if (data.success) {
                resultEl.textContent = data.message;
                resultEl.className = 'success';

                // Show saved mode
                document.getElementById('mqtt-edit').style.display = 'none';
                document.getElementById('mqtt-saved').style.display = 'block';

                // In setup mode, show "Setup Complete"
                if (window.setupMode) {
                    const complete = document.getElementById('setup-complete');
                    if (complete) complete.style.display = 'block';
                }
            } else {
                resultEl.textContent = data.message || 'Save failed';
                resultEl.className = 'error';
            }
        } catch (e) {
            resultEl.textContent = 'Network error: ' + e.message;
            resultEl.className = 'error';
        }
    }

    // ── Edit mode (from saved state) ──

    function edit() {
        document.getElementById('mqtt-saved').style.display = 'none';
        document.getElementById('mqtt-edit').style.display = 'block';
    }

    // ── Test connection ──

    async function testConnection() {
        const resultEl = document.getElementById('mqtt-test-result');
        const btn = document.getElementById('mqtt-test-btn');

        const broker = fields.broker().value.trim();
        if (!broker) {
            resultEl.textContent = 'Enter a broker address first';
            resultEl.className = 'error';
            return;
        }

        btn.disabled = true;
        resultEl.textContent = 'Testing...';
        resultEl.className = 'loading';

        try {
            const resp = await fetch('/api/mqtt/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    broker: broker,
                    port: parseInt(fields.port().value) || 1883,
                    username: fields.username().value,
                    password: fields.password().value,
                }),
            });
            const data = await resp.json();

            if (data.status === 'ok') {
                let msg = '\u2705 ' + data.message;
                if (data.warnings?.length) {
                    msg += ' (Warning: ' + data.warnings.join(', ') + ')';
                }
                resultEl.textContent = msg;
                resultEl.className = 'success';
            } else {
                resultEl.textContent = '\u274c ' + data.message;
                resultEl.className = 'error';
            }
        } catch (e) {
            resultEl.textContent = 'Network error: ' + e.message;
            resultEl.className = 'error';
        } finally {
            btn.disabled = false;
        }
    }

    // ── Trigger mode radio show/hide ──

    function _setupTriggerModeRadios() {
        document.querySelectorAll('input[name="trigger-mode"]').forEach(radio => {
            radio.addEventListener('change', (e) => _updateTriggerVisibility(e.target.value));
        });
    }

    function _updateTriggerVisibility(mode) {
        const mqttFields = document.getElementById('trigger-mqtt-fields');
        const cyclicFields = document.getElementById('trigger-cyclic-fields');

        mqttFields.style.display = (mode === 'mqtt' || mode === 'both') ? 'block' : 'none';
        cyclicFields.style.display = (mode === 'cyclic' || mode === 'both') ? 'block' : 'none';
    }

    // ── HA toggle show/hide ──

    function _setupHaToggle() {
        const toggle = document.getElementById('ha-enabled');
        if (toggle) {
            toggle.addEventListener('change', (e) => _updateHaVisibility(e.target.checked));
        }
    }

    function _updateHaVisibility(enabled) {
        const haFields = document.getElementById('ha-fields');
        if (haFields) haFields.style.display = enabled ? 'block' : 'none';
    }

    // ── Password toggle ──

    function _setupPasswordToggle() {
        const btn = document.getElementById('mqtt-password-toggle');
        if (btn) {
            btn.addEventListener('click', () => {
                const input = fields.password();
                const isPassword = input.type === 'password';
                input.type = isPassword ? 'text' : 'password';
            });
        }
    }

    // ── Public API ──
    return { init, loadConfig, save, edit, testConnection };
})();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Only init if the step-mqtt element exists (we're on the ROI config page)
    if (document.getElementById('step-mqtt')) {
        MqttConfig.init();
    }
});
```

### Step 2: Commit

```bash
git add watermeter/static/mqtt-config.js
git commit -m "claude: add mqtt-config.js module for Step 5 MQTT/HA form logic"
```

---

## Task 5: Wire Step 5 into ROI Wizard Navigation (`roi-config.js`)

**Files:**
- Modify: `watermeter/static/roi-config.js` (lines ~1488-1587)

### Step 1: Extend `loadConfig()` to show Step 5

In `roi-config.js`, find the `loadConfig()` function (line 1488). After the existing step loading logic (which handles rotation → markers → digits → analogs), add Step 5 visibility logic.

The existing `loadConfig()` function hides steps when their prerequisites aren't met. Step 5 should always be visible (it doesn't depend on ROI config). Find the end of the function and ensure `step-mqtt` is shown.

After the final block in `loadConfig()` that handles analogs saved state, add:

```javascript
// Step 5: MQTT is always visible
document.getElementById('step-mqtt').style.display = 'block';
```

### Step 2: Update setup mode initialization

In the setup mode initialization block (lines ~1556-1587), ensure that Step 5 is hidden during setup until Step 4 is complete. Find where steps 1-4 are hidden when `window.setupMode` is true:

```javascript
document.getElementById('step-mqtt').style.display = 'none';
```

And in the analog save handler (or wherever "Step 4 complete" is signaled), show Step 5:

```javascript
document.getElementById('step-mqtt').style.display = 'block';
```

### Step 3: Move "Setup Complete" trigger

Currently, the "Setup Complete" section is shown after Step 4 (analogs). In the `saveAnalogs()` function, find where it shows `setup-complete` and instead make it just reveal Step 5. The "Setup Complete" display will be triggered by `MqttConfig.save()` (which already handles this in Task 4).

In `saveAnalogs()` — find the line:
```javascript
if (window.setupMode) {
    document.getElementById('setup-complete').style.display = 'block';
}
```

Replace with:
```javascript
if (window.setupMode) {
    document.getElementById('step-mqtt').style.display = 'block';
}
```

### Step 4: Test manually

Start debug container: `./debug.sh --detach`
Navigate to `http://localhost:8002/roi-config`
Verify: Step 5 is visible below Step 4

Navigate to `http://localhost:8002/roi-config?setup=1`
Verify: Step 5 is hidden initially, appears after completing Step 4

### Step 5: Commit

```bash
git add watermeter/static/roi-config.js
git commit -m "claude: wire Step 5 MQTT/HA into ROI wizard step navigation"
```

---

## Task 6: Integration Testing

**Files:**
- Modify: `tests/unit/test_mqtt_routes.py` (extend with roundtrip tests)
- Manual: Playwright verification against debug container

### Step 1: Add config roundtrip test

Add to `tests/unit/test_mqtt_routes.py`:

```python
class TestConfigRoundtrip:
    """Test save → reload → read cycle."""

    def test_save_then_get_returns_saved_values(self, client, mock_service, tmp_path):
        """Saved values should be readable via GET."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("mqtt:\n  broker: old\ntrigger:\n  mode: mqtt\nhomeassistant:\n  enabled: false\n")

        with patch("watermeter.routes.mqtt.Path") as MockPath:
            MockPath.return_value = config_file
            with patch("watermeter.routes.mqtt.config_utils") as mock_cu:
                # Setup load to return from file
                from ruamel.yaml import YAML
                y = YAML()
                mock_cu.load_config.return_value = y.load(config_file)
                mock_cu.save_config = MagicMock()

                resp = client.post("/api/mqtt/config", json={
                    "mqtt": {"broker": "10.0.0.1"},
                    "trigger": {"mode": "cyclic"},
                    "homeassistant": {"enabled": True},
                })
                assert resp.status_code == 200
                assert resp.json()["success"] is True
```

### Step 2: Run full test suite

Run: `.venv/bin/python -m pytest tests/ -v --timeout=30 -x`
Expected: All pass

### Step 3: Manual Playwright-style verification

Start debug container and manually verify in browser:
1. Navigate to `/roi-config` — Step 5 visible at bottom
2. Fill in broker, port — click "Test Connection" — check result display
3. Toggle trigger mode radios — MQTT/Cyclic fields show/hide correctly
4. Toggle HA enabled — HA fields appear/disappear
5. Click "Save & Continue" — config saved, shows saved state
6. Click "Change Settings" — back to edit mode
7. Navigate to `/roi-config?setup=1` — Step 5 hidden initially
8. Reload page and check `/api/mqtt/config` — values persisted

### Step 4: Commit any test fixes

```bash
git add tests/
git commit -m "claude: add integration tests for MQTT config roundtrip"
```

---

## Task 7: Final Cleanup & Codebase Map Update

**Files:**
- Modify: `docs/codebase_map.md` (add MQTT router, mqtt-config.js)

### Step 1: Update codebase map

Add the new MQTT router entry after `routes/roi.py` section:

```markdown
### `routes/mqtt.py` (NNN lines) -- MQTT & Home Assistant config API

- `GET /api/mqtt/config` LNNN -- return raw MQTT/trigger/HA config
- `POST /api/mqtt/config` LNNN -- save MQTT/trigger/HA config + hot-reload
- `POST /api/mqtt/test` LNNN -- test MQTT broker connection
```

Add mqtt-config.js to the Static JS section:

```markdown
### `mqtt-config.js` (NNN lines)
`MqttConfig.init` LNNN, `MqttConfig.loadConfig` LNNN, `MqttConfig.save` LNNN, `MqttConfig.testConnection` LNNN, `MqttConfig.edit` LNNN
```

Update `config_utils.py` entry to include `resolve_env_vars`.

### Step 2: Run linting

Run: `uvx ruff check watermeter/routes/mqtt.py watermeter/config_utils.py`
Run: `uvx black --check watermeter/routes/mqtt.py watermeter/config_utils.py`

Fix any issues.

### Step 3: Final commit

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with MQTT config routes and mqtt-config.js"
```

---

## Summary

| Task | Description | Agent | Dependencies |
|------|-------------|-------|-------------|
| 1 | ENV Variable Substitution | dev | — |
| 2 | MQTT Config API Router | dev | Task 1 |
| 3 | Step 5 HTML + CSS | frontend | — |
| 4 | Step 5 JavaScript Module | frontend | — |
| 5 | Wire into ROI Wizard Navigation | frontend | Tasks 3, 4 |
| 6 | Integration Testing | tester | Tasks 1-5 |
| 7 | Cleanup & Codebase Map | dev | Tasks 1-6 |

**Parallelizable:** Tasks 1+3+4 can run in parallel. Task 2 depends on Task 1. Task 5 depends on Tasks 3+4. Tasks 6+7 are sequential after everything else.
