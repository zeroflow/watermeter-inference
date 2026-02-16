# Paho-MQTT v2 API Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate from deprecated paho-mqtt v1 API to v2 API and wire up MQTT authentication support.

**Architecture:** Update `Client()` construction with `CallbackAPIVersion.VERSION2`, fix `on_connect` callback signature (5 params), add optional `username_pw_set()` before `connect()`. Auth reads from config with env var override (`MQTT_USERNAME`/`MQTT_PASSWORD`). Add `on_disconnect` callback for reconnect logging.

**Tech Stack:** Python, paho-mqtt 2.1.0, pytest

---

### Background

The code uses paho-mqtt v1 API but v2 is installed (`paho-mqtt>=2.1.0`). It runs on a deprecated compatibility layer that emits warnings and will break in v3. Key differences:

| Area | v1 (current) | v2 (target) |
|------|-------------|-------------|
| Client constructor | `mqtt.Client(client_id=...)` | `mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=...)` |
| `on_connect` | `(client, userdata, flags, rc)` | `(client, userdata, connect_flags, reason_code, properties)` |
| `on_disconnect` | `(client, userdata, rc)` | `(client, userdata, disconnect_flags, reason_code, properties)` |
| `on_message` | `(client, userdata, msg)` | same — no change |
| Success check | `rc == 0` | `reason_code == 0` (ReasonCode object, `==` comparison works) |

MQTT auth is documented in `docker-compose.yml` and `.env.example` but never wired up.

### Files Affected

| File | Change |
|------|--------|
| `watermeter/watermeter_service.py` | `start_mqtt()`, `on_mqtt_connect()`, new `on_mqtt_disconnect()` |
| `watermeter/config_utils.py` | Add `username`/`password` to mqtt schema |
| `tests/unit/test_trigger_mode.py` | Update `on_mqtt_connect` call signatures (4 methods) |
| `tests/unit/test_confirmation.py` | Update `on_mqtt_connect` call signatures (2 methods) |
| `docs/codebase_map.md` | Add `on_mqtt_disconnect` entry |

---

### Task 1: Update tests for v2 `on_connect` signature

**Files:**
- Modify: `tests/unit/test_trigger_mode.py` (lines 232, 243, 254, 264)
- Modify: `tests/unit/test_confirmation.py` (lines 686, 701)

Tests currently call `on_mqtt_connect` with v1 signature (4 args). Update to v2 (5 args). Tests will fail until the production code is updated.

**Step 1: Update test_trigger_mode.py**

Read `tests/unit/test_trigger_mode.py` and find the `TestMqttSubscriptionConditional` class (around line 212). Update all 4 test methods that call `on_mqtt_connect`:

In each test, find:
```python
WatermeterService.on_mqtt_connect(service, client, None, None, 0)
```
Replace with:
```python
WatermeterService.on_mqtt_connect(service, client, None, None, 0, None)
```

And for the failure test, find:
```python
WatermeterService.on_mqtt_connect(service, client, None, None, 5)
```
Replace with:
```python
WatermeterService.on_mqtt_connect(service, client, None, None, 5, None)
```

There are 4 calls total to update in this file.

**Step 2: Update test_confirmation.py**

Read `tests/unit/test_confirmation.py` and find the `TestMqttConnectSubscription` class (around line 674). Update both test methods:

Find:
```python
service.on_mqtt_connect(mock_client, None, None, 0)
```
Replace with:
```python
service.on_mqtt_connect(mock_client, None, None, 0, None)
```

There are 2 calls total to update in this file.

**Step 3: Run tests to verify they FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_trigger_mode.py::TestMqttSubscriptionConditional tests/unit/test_confirmation.py::TestMqttConnectSubscription -v`

Expected: FAIL — `on_mqtt_connect()` takes 5 positional arguments but 6 were given (because production code still has v1 signature with 4 params + self).

**Step 4: Commit**

```bash
git add tests/unit/test_trigger_mode.py tests/unit/test_confirmation.py
git commit -m "claude: update tests for paho-mqtt v2 on_connect signature"
```

---

### Task 2: Write tests for MQTT auth and disconnect callback

**Files:**
- Modify: `tests/unit/test_trigger_mode.py` (add new test class)

**Step 1: Write tests for auth and disconnect**

Read `tests/unit/test_trigger_mode.py` to understand the imports and patterns used. Add a new test class at the end of the file:

```python
class TestMqttV2Api:
    """Tests for paho-mqtt v2 API compliance."""

    def test_client_uses_callback_api_v2(self, mock_service):
        """Client should be constructed with CallbackAPIVersion.VERSION2."""
        import paho.mqtt.client as real_mqtt

        service = mock_service
        service.config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "watermeter/status",
                "trigger_payload": "Flow finished",
                "reset_topic": "watermeter/reset",
            }
        }

        with patch("watermeter.watermeter_service.mqtt.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value = mock_instance
            service.start_mqtt()

            mock_client_cls.assert_called_once_with(
                callback_api_version=real_mqtt.CallbackAPIVersion.VERSION2,
                client_id="test",
            )

    def test_auth_from_config(self, mock_service):
        """If username/password in config, call username_pw_set()."""
        service = mock_service
        service.config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "watermeter/status",
                "trigger_payload": "Flow finished",
                "reset_topic": "watermeter/reset",
                "username": "myuser",
                "password": "mypass",
            }
        }

        with patch("watermeter.watermeter_service.mqtt.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value = mock_instance
            service.start_mqtt()

            mock_instance.username_pw_set.assert_called_once_with("myuser", "mypass")

    def test_auth_from_env_overrides_config(self, mock_service):
        """Env vars MQTT_USERNAME/MQTT_PASSWORD override config values."""
        service = mock_service
        service.config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "watermeter/status",
                "trigger_payload": "Flow finished",
                "reset_topic": "watermeter/reset",
                "username": "config_user",
                "password": "config_pass",
            }
        }

        with patch("watermeter.watermeter_service.mqtt.Client") as mock_client_cls, \
             patch.dict("os.environ", {"MQTT_USERNAME": "env_user", "MQTT_PASSWORD": "env_pass"}):
            mock_instance = MagicMock()
            mock_client_cls.return_value = mock_instance
            service.start_mqtt()

            mock_instance.username_pw_set.assert_called_once_with("env_user", "env_pass")

    def test_no_auth_when_not_configured(self, mock_service):
        """No username_pw_set() call when auth not configured."""
        service = mock_service
        service.config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "watermeter/status",
                "trigger_payload": "Flow finished",
                "reset_topic": "watermeter/reset",
            }
        }

        with patch("watermeter.watermeter_service.mqtt.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value = mock_instance
            service.start_mqtt()

            mock_instance.username_pw_set.assert_not_called()

    def test_on_disconnect_logs_reconnect(self, mock_service):
        """on_mqtt_disconnect should exist and handle disconnection."""
        service = mock_service
        service.config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "watermeter/status",
                "trigger_payload": "Flow finished",
                "reset_topic": "watermeter/reset",
            }
        }
        # Verify the method exists
        assert hasattr(service, "on_mqtt_disconnect"), "on_mqtt_disconnect method missing"

        # Verify it accepts v2 signature (5 args after self)
        import inspect
        sig = inspect.signature(service.on_mqtt_disconnect)
        assert len(sig.parameters) == 5, f"Expected 5 params, got {len(sig.parameters)}: {list(sig.parameters)}"
```

IMPORTANT: Check what imports exist at the top of the test file. You'll need `patch` from `unittest.mock` and possibly `MagicMock`. These may already be imported. Also add `import os` if not present — though `patch.dict("os.environ", ...)` doesn't require an explicit `os` import.

**Step 2: Run tests to verify they FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_trigger_mode.py::TestMqttV2Api -v`

Expected: FAIL — `CallbackAPIVersion` not used, `username_pw_set` not called, `on_mqtt_disconnect` doesn't exist.

**Step 3: Commit**

```bash
git add tests/unit/test_trigger_mode.py
git commit -m "claude: add failing tests for mqtt v2 api and auth"
```

---

### Task 3: Migrate `start_mqtt()` to v2 API + auth

**Files:**
- Modify: `watermeter/watermeter_service.py` (lines 2278-2307)

**Step 1: Read the current `start_mqtt()` method**

Read `watermeter/watermeter_service.py` around lines 2278-2307.

**Step 2: Replace `start_mqtt()` implementation**

Find the line:
```python
    self.mqtt_client = mqtt.Client(client_id=mqtt_config["client_id"])
```

Replace with:
```python
    self.mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=mqtt_config["client_id"],
    )

    # MQTT authentication: env vars override config
    username = os.environ.get("MQTT_USERNAME") or mqtt_config.get("username")
    password = os.environ.get("MQTT_PASSWORD") or mqtt_config.get("password")
    if username:
        self.mqtt_client.username_pw_set(username, password)
        logger.info("MQTT authentication configured")
```

Also add `import os` at the top of the file if not already present. Check existing imports first — it may already be imported.

**Step 3: Run auth tests**

Run: `.venv/bin/python -m pytest tests/unit/test_trigger_mode.py::TestMqttV2Api::test_client_uses_callback_api_v2 tests/unit/test_trigger_mode.py::TestMqttV2Api::test_auth_from_config tests/unit/test_trigger_mode.py::TestMqttV2Api::test_auth_from_env_overrides_config tests/unit/test_trigger_mode.py::TestMqttV2Api::test_no_auth_when_not_configured -v`

Expected: These 4 should PASS. The disconnect test still fails (not implemented yet).

**Step 4: Commit**

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: migrate start_mqtt to paho v2 api with auth support"
```

---

### Task 4: Update `on_mqtt_connect()` to v2 signature

**Files:**
- Modify: `watermeter/watermeter_service.py` (line 2211)

**Step 1: Read the current callback**

Read `watermeter/watermeter_service.py` around lines 2211-2240.

**Step 2: Update the signature**

Find:
```python
def on_mqtt_connect(self, client, userdata, flags, rc):
    """MQTT connect callback."""
    if rc == 0:
```

Replace with:
```python
def on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties):
    """MQTT connect callback (paho v2 API)."""
    if reason_code == 0:
```

Also update the error log at the end of the method. Find:
```python
        logger.error(f"MQTT connection failed with code {rc}")
```

Replace with:
```python
        logger.error(f"MQTT connection failed: {reason_code}")
```

**Step 3: Run the connect-related tests**

Run: `.venv/bin/python -m pytest tests/unit/test_trigger_mode.py::TestMqttSubscriptionConditional tests/unit/test_confirmation.py::TestMqttConnectSubscription -v`

Expected: All 6 tests PASS (they were updated in Task 1 to pass 5 args).

**Step 4: Commit**

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: update on_mqtt_connect to paho v2 signature"
```

---

### Task 5: Add `on_mqtt_disconnect()` callback

**Files:**
- Modify: `watermeter/watermeter_service.py` (add after `on_mqtt_connect`, register in `start_mqtt`)

**Step 1: Add the disconnect callback**

Read `watermeter/watermeter_service.py` and find the end of `on_mqtt_connect()` (around line 2240). Add the new method directly after it:

```python
def on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
    """MQTT disconnect callback (paho v2 API)."""
    if reason_code == 0:
        logger.info("Disconnected from MQTT broker (clean)")
    else:
        logger.warning(f"Disconnected from MQTT broker: {reason_code} — will reconnect automatically")
```

**Step 2: Register the callback in `start_mqtt()`**

Find the lines where callbacks are registered:
```python
    self.mqtt_client.on_connect = self.on_mqtt_connect
    self.mqtt_client.on_message = self.on_mqtt_message
```

Add after them:
```python
    self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
```

**Step 3: Run all MQTT tests**

Run: `.venv/bin/python -m pytest tests/unit/test_trigger_mode.py::TestMqttV2Api tests/unit/test_trigger_mode.py::TestMqttSubscriptionConditional tests/unit/test_confirmation.py::TestMqttConnectSubscription -v`

Expected: All tests PASS including the disconnect signature test.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: add on_mqtt_disconnect callback for reconnect logging"
```

---

### Task 6: Add auth fields to config schema

**Files:**
- Modify: `watermeter/config_utils.py` (lines 243-256, mqtt schema section)

**Step 1: Read the config schema**

Read `watermeter/config_utils.py` around lines 243-256.

**Step 2: Add username and password fields**

Find the closing of the mqtt properties dict. After the `"reset_topic"` entry, add:

```python
            "username": {"type": "string", "description": "MQTT broker username (optional, env MQTT_USERNAME overrides)"},
            "password": {"type": "string", "description": "MQTT broker password (optional, env MQTT_PASSWORD overrides)"},
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`

Expected: All tests PASS.

**Step 4: Commit**

```bash
git add watermeter/config_utils.py
git commit -m "claude: add mqtt username/password fields to config schema"
```

---

### Task 7: Update codebase map and backlog

**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md`

**Step 1: Update codebase map**

Read `docs/codebase_map.md` and find the `watermeter_service.py` section. Find the `on_mqtt_connect` entry and add `on_mqtt_disconnect` after it:

```markdown
  - `on_mqtt_disconnect(client, userdata, disconnect_flags, reason_code, properties)` — L[line]: MQTT v2 disconnect callback with reconnect logging
```

Use the actual line number from the source.

**Step 2: Update backlog**

Read `backlog.md` and change BL-26 status from `idea` to `done`.

**Step 3: Commit**

```bash
git add docs/codebase_map.md backlog.md
git commit -m "claude: update codebase map and mark BL-26 done"
```
