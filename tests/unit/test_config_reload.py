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
        service._image_pipeline = MagicMock()
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
