# Config Editor Reload Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When config is saved via the editor, apply changes to the running service immediately without container restart.

**Architecture:** Add `reload_config(new_config)` method to `WatermeterService` that updates `self.config`, re-syncs cached scalars, and returns a diff of what changed. The config save route calls this method after writing to disk. MQTT reconnects only if broker/port/auth changed. Cyclic loop restarts only if trigger mode/interval changed.

**Tech Stack:** Python, FastAPI, pytest

---

### Background

The config editor (`POST /api/config/save`) writes to disk but never updates `service.config` in memory. The service continues using stale config until container restart. ROI routes and model routes already reload properly — we follow the same pattern.

### Cached Scalars That Need Re-sync

| Attribute | Config Key | Set in `__init__` |
|-----------|-----------|-------------------|
| `self.trigger_mode` | `trigger.mode` | L302 |
| `self.cyclic_interval` | `trigger.cyclic_interval` | L303 |
| `self.rate_history_size` | `plausibility.rate_history_size` | L291 |
| `self.ha_publish_enabled` | `homeassistant.enabled` | L252 |

Values read dynamically via `self.config[...]` are automatically fixed by updating `self.config`.

### MQTT/Cyclic Side Effects

- **MQTT**: If `mqtt.broker`, `mqtt.port`, `mqtt.username`, or `mqtt.password` change → `stop_mqtt()` + `start_mqtt()`
- **Cyclic loop**: If `trigger.mode` or `trigger.cyclic_interval` change → stop/start cyclic loop
- **Inference**: Model path changes handled separately by model activation routes — NOT by config editor

---

### Task 1: Write failing tests for `reload_config()`

**Files:**
- Create: `tests/unit/test_config_reload.py`

**Step 1: Write the tests**

```python
"""Tests for WatermeterService.reload_config() method."""
import sys
import yaml
import pytest
from unittest.mock import MagicMock, patch
from copy import deepcopy

# Load the real WatermeterService class (bypassing conftest mocks)
_ws_mock_backup = {}
for _name in ["watermeter_service", "watermeter.watermeter_service"]:
    if _name in sys.modules:
        _ws_mock_backup[_name] = sys.modules.pop(_name)

from watermeter.watermeter_service import WatermeterService  # noqa: E402

for _name, _mock in _ws_mock_backup.items():
    sys.modules[_name] = _mock


def _make_config(**overrides):
    """Create a minimal valid config dict."""
    base = {
        "images": {"source": "url", "url": "http://cam/snap", "ids": ["digit_1"]},
        "mqtt": {
            "broker": "localhost",
            "port": 1883,
            "client_id": "test",
            "keepalive": 60,
            "trigger_topic": "watermeter/status",
            "trigger_payload": "Flow finished",
            "reset_topic": "watermeter/reset",
        },
        "trigger": {"mode": "mqtt", "cyclic_interval": 300},
        "inference": {"confidence_threshold": 0.5},
        "plausibility": {"rate_history_size": 5},
        "homeassistant": {"enabled": True},
        "logging": {"level": "INFO"},
        "detection": {},
        "low_confidence": {"enabled": False},
    }
    for key, val in overrides.items():
        parts = key.split(".")
        d = base
        for p in parts[:-1]:
            d = d[p]
        d[parts[-1]] = val
    return base


class TestReloadConfig:
    """Tests for WatermeterService.reload_config()."""

    def _make_service(self, config):
        """Create a service instance with mocked externals."""
        service = MagicMock(spec=WatermeterService)
        service.config = deepcopy(config)
        service.trigger_mode = config["trigger"]["mode"]
        service.cyclic_interval = config["trigger"].get("cyclic_interval", 300)
        service.rate_history_size = config["plausibility"].get("rate_history_size", 5)
        service.ha_publish_enabled = config["homeassistant"]["enabled"]
        service.mqtt_client = MagicMock()
        service.mqtt_client.is_connected.return_value = True
        service._cyclic_task = None
        # Bind the real method
        service.reload_config = WatermeterService.reload_config.__get__(service)
        return service

    def test_updates_config_dict(self):
        """reload_config should update self.config."""
        old = _make_config()
        new = _make_config(**{"inference.confidence_threshold": 0.8})
        service = self._make_service(old)

        service.reload_config(new)

        assert service.config["inference"]["confidence_threshold"] == 0.8

    def test_resyncs_cached_scalars(self):
        """Cached scalars should be updated from new config."""
        old = _make_config()
        new = _make_config(
            **{
                "plausibility.rate_history_size": 10,
                "homeassistant.enabled": False,
            }
        )
        service = self._make_service(old)

        service.reload_config(new)

        assert service.rate_history_size == 10
        assert service.ha_publish_enabled is False

    def test_resyncs_trigger_mode(self):
        """Trigger mode change should update cached value."""
        old = _make_config()
        new = _make_config(**{"trigger.mode": "cyclic"})
        service = self._make_service(old)

        service.reload_config(new)

        assert service.trigger_mode == "cyclic"

    def test_resyncs_cyclic_interval(self):
        """Cyclic interval change should update cached value."""
        old = _make_config()
        new = _make_config(**{"trigger.cyclic_interval": 600})
        service = self._make_service(old)

        service.reload_config(new)

        assert service.cyclic_interval == 600

    def test_mqtt_reconnects_on_broker_change(self):
        """Changing mqtt.broker should trigger MQTT reconnect."""
        old = _make_config()
        new = _make_config(**{"mqtt.broker": "newhost"})
        service = self._make_service(old)

        service.reload_config(new)

        service.stop_mqtt.assert_called_once()
        service.start_mqtt.assert_called_once()

    def test_mqtt_reconnects_on_port_change(self):
        """Changing mqtt.port should trigger MQTT reconnect."""
        old = _make_config()
        new = _make_config(**{"mqtt.port": 8883})
        service = self._make_service(old)

        service.reload_config(new)

        service.stop_mqtt.assert_called_once()
        service.start_mqtt.assert_called_once()

    def test_no_mqtt_reconnect_when_unchanged(self):
        """No MQTT reconnect when mqtt config hasn't changed."""
        old = _make_config()
        new = _make_config(**{"inference.confidence_threshold": 0.9})
        service = self._make_service(old)

        service.reload_config(new)

        service.stop_mqtt.assert_not_called()
        service.start_mqtt.assert_not_called()

    def test_returns_changes_summary(self):
        """reload_config should return a dict describing what changed."""
        old = _make_config()
        new = _make_config(**{"mqtt.broker": "newhost", "plausibility.rate_history_size": 10})
        service = self._make_service(old)

        result = service.reload_config(new)

        assert isinstance(result, dict)
        assert result.get("mqtt_reconnected") is True
        assert result.get("config_updated") is True
```

**Step 2: Run tests to verify they FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_config_reload.py -v`

Expected: FAIL with `AttributeError: 'WatermeterService' object has no attribute 'reload_config'`

**Step 3: Commit**

```bash
git add tests/unit/test_config_reload.py
git commit -m "claude: add failing tests for config reload method"
```

---

### Task 2: Implement `reload_config()` on WatermeterService

**Files:**
- Modify: `watermeter/watermeter_service.py` (add method near `stop_mqtt`)

**Step 1: Read the file and find a good location**

Read `watermeter/watermeter_service.py` and find `stop_mqtt()` (around line 2321). Add the new method BEFORE `stop_mqtt()` — it's a high-level service method, not an MQTT detail.

**Step 2: Implement the method**

```python
def reload_config(self, new_config: dict) -> dict:
    """Hot-reload config into the running service.

    Updates self.config, re-syncs cached scalars, and reconnects
    MQTT if broker/port/auth changed.

    Args:
        new_config: The new config dict (already validated).

    Returns:
        Dict with keys: config_updated (bool), mqtt_reconnected (bool).
    """
    old_mqtt = self.config.get("mqtt", {})
    new_mqtt = new_config.get("mqtt", {})

    # Check if MQTT connection params changed
    mqtt_changed = any(
        old_mqtt.get(k) != new_mqtt.get(k)
        for k in ("broker", "port", "username", "password")
    )

    # Update main config
    self.config = new_config

    # Re-sync cached scalars
    trigger_config = new_config.get("trigger", {})
    self.trigger_mode = trigger_config.get("mode", "mqtt")
    self.cyclic_interval = trigger_config.get("cyclic_interval", 300)
    self.rate_history_size = new_config.get("plausibility", {}).get("rate_history_size", 5)
    self.ha_publish_enabled = new_config.get("homeassistant", {}).get("enabled", True)

    # Reconnect MQTT if connection params changed
    if mqtt_changed and self.mqtt_client:
        logger.info("MQTT config changed — reconnecting")
        self.stop_mqtt()
        self.start_mqtt()

    logger.info("Config reloaded successfully")
    return {"config_updated": True, "mqtt_reconnected": mqtt_changed}
```

**Step 3: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_config_reload.py -v`

Expected: All tests PASS.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: add reload_config method to WatermeterService"
```

---

### Task 3: Write test for config save route reload

**Files:**
- Modify: `tests/unit/test_api_routes.py` (add test to `TestConfigEndpoints`)

**Step 1: Read the test file**

Read `tests/unit/test_api_routes.py` and find the `TestConfigEndpoints` class. Understand how the test client and mocks work.

**Step 2: Add a test**

Add a new test method to `TestConfigEndpoints`:

```python
    def test_save_config_reloads_service(self):
        """Saving config should call reload_config on the service."""
        valid_yaml = "images:\n  source: url\nmqtt:\n  broker: localhost\n  port: 1883\ninference:\n  confidence_threshold: 0.5\n"

        with patch("watermeter.routes.config.config_utils") as mock_utils, \
             patch("watermeter.routes.config.watermeter_service") as mock_ws_module:
            mock_utils.validate_config.return_value = {"valid": True}
            mock_utils.load_config_string.return_value = {"mqtt": {"broker": "localhost"}}

            mock_service = MagicMock()
            mock_ws_module.get_service.return_value = mock_service

            response = self.client.post(
                "/api/config/save",
                json={"content": valid_yaml, "save_option": "saveonly"},
            )

            assert response.status_code == 200
            mock_service.reload_config.assert_called_once()
```

**Step 2: Run to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestConfigEndpoints::test_save_config_reloads_service -v`

Expected: FAIL — `reload_config` not called (route doesn't call it yet).

**Step 3: Commit**

```bash
git add tests/unit/test_api_routes.py
git commit -m "claude: add failing test for config save reload"
```

---

### Task 4: Update config save route to reload service

**Files:**
- Modify: `watermeter/routes/config.py`

**Step 1: Read the route**

Read `watermeter/routes/config.py` to see the full `save_config` function and its imports.

**Step 2: Add import**

At the top of the file, add (if not already present):

```python
from watermeter import watermeter_service
```

Check if this import already exists — the file may already import it.

**Step 3: Add reload after save**

Find the section after `config_utils.save_config(config, config_path)`:

```python
        # Save to file
        config_path = Path("config.yaml")
        config_utils.save_config(config, config_path)

        logger.info(f"Config saved (option: {submission.save_option})")

        # If restart requested, we'd need to trigger a service reload
        message = "Config saved successfully"
        if submission.save_option == "restart":
            message += ". Please restart the service to apply changes."
```

Replace everything from the logger.info line onwards (up to `return JSONResponse(...)`) with:

```python
        logger.info(f"Config saved (option: {submission.save_option})")

        # Reload config into running service
        import yaml

        with open(config_path, "r") as f:
            plain_config = yaml.safe_load(f)

        service = watermeter_service.get_service()
        reload_result = service.reload_config(plain_config)

        message = "Config saved and applied"
        if reload_result.get("mqtt_reconnected"):
            message += " (MQTT reconnected)"
```

NOTE: We re-read with `yaml.safe_load` because `load_config_string` returns a ruamel `CommentedMap` which may cause issues. The service expects a plain dict.

**Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestConfigEndpoints::test_save_config_reloads_service -v`

Expected: PASS

**Step 5: Run all config tests**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestConfigEndpoints tests/unit/test_config_reload.py -v`

Expected: All PASS.

**Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`

Expected: All PASS.

**Step 7: Commit**

```bash
git add watermeter/routes/config.py
git commit -m "claude: config editor now reloads service on save"
```

---

### Task 5: Update codebase map and backlog

**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md`

**Step 1: Update codebase map**

Read `docs/codebase_map.md` and find the `watermeter_service.py` section. Add the new method entry in the appropriate location (near stop_mqtt or in a "config" group):

```markdown
  - `reload_config(new_config)` — L[line]: Hot-reload config, re-sync cached scalars, reconnect MQTT if needed
```

Use the actual line number.

**Step 2: Update backlog**

Change BL-27 from `idea` to `done`.

**Step 3: Commit**

```bash
git add docs/codebase_map.md backlog.md
git commit -m "claude: update codebase map and mark BL-27 done"
```
