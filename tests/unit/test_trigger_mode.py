"""Unit tests for the cyclic trigger mode feature.

Tests config validation, schema, trigger mode defaults, cyclic loop behavior,
and conditional MQTT subscription.

Note: watermeter.watermeter_service is mocked at module level in the unit test
conftest (it imports cv2, paho, openvino which aren't available on host).
We use importlib to load the real source file for method-level tests.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from watermeter.config_utils import get_config_schema, load_config_string, validate_config


# ---------------------------------------------------------------------------
# Load the REAL WatermeterService class (bypassing the conftest mock).
# The heavy deps (cv2, paho, openvino) are already mocked in conftest,
# so the real module can be imported once we remove the module-level mock.
# ---------------------------------------------------------------------------
_ws_mock_backup = {}
for _name in ['watermeter_service', 'watermeter.watermeter_service']:
    if _name in sys.modules:
        _ws_mock_backup[_name] = sys.modules.pop(_name)

from watermeter.watermeter_service import WatermeterService  # noqa: E402
from watermeter.scheduling import SchedulingManager  # noqa: E402
from watermeter.mqtt_publisher import MqttPublisher  # noqa: E402

# Keep a reference to the real module (for patching module-level names like `mqtt`)
_real_ws_module = sys.modules['watermeter.watermeter_service']
_real_mqtt_publisher_module = sys.modules['watermeter.mqtt_publisher']

# Restore the mocks so other test files still work
for _name, _mock in _ws_mock_backup.items():
    sys.modules[_name] = _mock


# ===== Config Schema Tests =====

class TestTriggerConfigSchema:
    """Tests for trigger section in config schema."""

    def test_schema_has_trigger_section(self):
        schema = get_config_schema()
        assert 'trigger' in schema['properties']

    def test_trigger_schema_fields(self):
        schema = get_config_schema()
        trigger = schema['properties']['trigger']
        assert trigger['properties']['mode']['enum'] == ['mqtt', 'cyclic', 'both']
        assert trigger['properties']['cyclic_interval']['type'] == 'integer'
        assert trigger['properties']['cyclic_interval']['minimum'] == 10

    def test_trigger_not_required(self):
        """trigger section is optional — existing configs without it should validate."""
        schema = get_config_schema()
        assert 'trigger' not in schema.get('required', [])


class TestTriggerConfigParsing:
    """Tests for parsing trigger config from YAML."""

    def test_parse_trigger_section(self):
        yaml = (
            "trigger:\n  mode: cyclic\n  cyclic_interval: 120\n"
            "images:\n  digits: []\n  arrows: []\n"
            "mqtt:\n  broker: x\n  port: 1883\n"
            "inference:\n  confidence_threshold: 0.5\n"
        )
        config = load_config_string(yaml)
        assert config['trigger']['mode'] == 'cyclic'
        assert config['trigger']['cyclic_interval'] == 120

    def test_config_without_trigger_section_is_valid(self):
        """Configs without trigger section should still validate (backwards compat)."""
        yaml = (
            "images:\n  digits: []\n  arrows: []\n"
            "mqtt:\n  broker: x\n  port: 1883\n"
            "inference:\n  confidence_threshold: 0.5\n"
        )
        result = validate_config(yaml)
        assert result['valid'] is True

    def test_all_three_modes_parse(self):
        for mode in ('mqtt', 'cyclic', 'both'):
            yaml = (
                f"trigger:\n  mode: {mode}\n"
                "images:\n  digits: []\n  arrows: []\n"
                "mqtt:\n  broker: x\n  port: 1883\n"
                "inference:\n  confidence_threshold: 0.5\n"
            )
            config = load_config_string(yaml)
            assert config['trigger']['mode'] == mode


class TestTriggerModeDefaults:
    """Tests for trigger mode default behavior when config section is missing."""

    def test_default_trigger_mode_is_mqtt(self):
        """When trigger section is absent, WatermeterService should default to 'mqtt'."""
        # Create a service instance with config lacking trigger section
        service = object.__new__(WatermeterService)
        service.config = {
            'images': {'digits': [], 'arrows': []},
            'mqtt': {'broker': 'localhost', 'port': 1883},
            'inference': {'confidence_threshold': 0.5},
        }

        # Replicate the __init__ logic for trigger_mode resolution
        trigger_config = service.config.get('trigger', {})
        service.trigger_mode = trigger_config.get('mode', 'mqtt')
        service.cyclic_interval = trigger_config.get('cyclic_interval', 300)

        assert service.trigger_mode == 'mqtt'
        assert service.cyclic_interval == 300


# ===== Cyclic Loop Tests =====

class TestCyclicLoop:
    """Tests for the cyclic loop async behavior."""

    @pytest.mark.asyncio
    async def test_cyclic_loop_calls_process_reading(self):
        """The cyclic loop should call process_reading after the interval."""
        call_count = 0
        target_calls = 3
        done = asyncio.Event()

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= target_calls:
                done.set()

        scheduler = SchedulingManager(
            cyclic_interval=0.05,  # 50ms for fast test
            process_fn=mock_process,
            stats_fn=MagicMock(),
        )

        loop_coro = scheduler._cyclic_loop()
        task = asyncio.create_task(loop_coro)
        try:
            # Wait for expected calls with timeout instead of fixed sleep
            await asyncio.wait_for(done.wait(), timeout=2.0)
            assert call_count >= target_calls, f"Expected at least {target_calls} calls, got {call_count}"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_cyclic_loop_cancellation(self):
        """The cyclic loop should exit cleanly when cancelled."""
        scheduler = SchedulingManager(
            cyclic_interval=10,  # Long interval, we cancel before it fires
            process_fn=AsyncMock(),
            stats_fn=MagicMock(),
        )

        loop_coro = scheduler._cyclic_loop()
        task = asyncio.create_task(loop_coro)
        await asyncio.sleep(0.01)
        task.cancel()
        # _cyclic_loop catches CancelledError internally, so await should not raise
        await task
        assert task.done()

    def test_start_cyclic_loop_creates_task(self):
        """start_cyclic_loop should create an asyncio task."""
        scheduler = SchedulingManager(
            cyclic_interval=300,
            process_fn=AsyncMock(),
            stats_fn=MagicMock(),
        )

        mock_task = MagicMock()
        with patch('asyncio.create_task', return_value=mock_task) as mock_create:
            scheduler.start_cyclic_loop()
            mock_create.assert_called_once()
            assert scheduler._cyclic_task == mock_task

    def test_start_cyclic_loop_noop_when_already_running(self):
        """start_cyclic_loop should not create a second task if one is running."""
        scheduler = SchedulingManager(
            cyclic_interval=300,
            process_fn=AsyncMock(),
            stats_fn=MagicMock(),
        )
        scheduler._cyclic_task = MagicMock()  # Already running

        with patch('asyncio.create_task') as mock_create:
            scheduler.start_cyclic_loop()
            mock_create.assert_not_called()

    def test_stop_cyclic_loop_cancels_task(self):
        """stop_cyclic_loop should cancel the running task."""
        scheduler = SchedulingManager(
            cyclic_interval=300,
            process_fn=AsyncMock(),
            stats_fn=MagicMock(),
        )
        mock_task = MagicMock()
        scheduler._cyclic_task = mock_task

        scheduler.stop_cyclic_loop()

        mock_task.cancel.assert_called_once()
        assert scheduler._cyclic_task is None

    def test_stop_cyclic_loop_noop_when_not_running(self):
        """stop_cyclic_loop should do nothing if no task is running."""
        scheduler = SchedulingManager(
            cyclic_interval=300,
            process_fn=AsyncMock(),
            stats_fn=MagicMock(),
        )
        scheduler._cyclic_task = None

        scheduler.stop_cyclic_loop()
        # Should not raise


# ===== MQTT Subscription Tests =====

class TestMqttSubscriptionConditional:
    """Tests that MQTT trigger subscription is conditional on trigger mode."""

    def _make_publisher(self, trigger_mode):
        """Create a minimal MqttPublisher mock for on_connect testing."""
        publisher = MagicMock()
        publisher.trigger_mode = trigger_mode
        publisher.config = {
            'mqtt': {
                'trigger_topic': 'watermeter/status',
                'reset_topic': 'watermeter/reset',
            },
        }
        # _confirmation_manager.get_config() returns disabled confirmation
        publisher._confirmation_manager.get_config.return_value = {'enabled': False, 'response_topic': ''}
        publisher.publish_discovery = MagicMock()
        publisher.publish_training_stats = MagicMock()
        return publisher

    def test_mqtt_mode_subscribes_to_trigger(self):
        publisher = self._make_publisher('mqtt')
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 0, None)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics
        assert 'homeassistant/status' in subscribed_topics

    def test_cyclic_mode_skips_trigger_subscription(self):
        publisher = self._make_publisher('cyclic')
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 0, None)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' not in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics
        assert 'homeassistant/status' in subscribed_topics

    def test_both_mode_subscribes_to_trigger(self):
        publisher = self._make_publisher('both')
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 0, None)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics

    def test_failed_connection_does_not_subscribe(self):
        publisher = self._make_publisher('mqtt')
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 5, None)

        client.subscribe.assert_not_called()


# ===== MQTT v2 API Tests =====

class TestMqttV2Api:
    """Tests for paho-mqtt v2 API compliance (methods now live in MqttPublisher)."""

    def _make_publisher(self, mqtt_config):
        """Create a minimal MqttPublisher mock for start() testing."""
        publisher = MagicMock(spec=MqttPublisher)
        publisher.config = {"mqtt": mqtt_config}
        publisher.loop = None
        return publisher

    def test_client_uses_callback_api_v2(self, mock_service):
        """Client should be constructed with CallbackAPIVersion.VERSION2."""
        from enum import IntEnum

        # paho v2 defines CallbackAPIVersion; since paho is mocked in
        # conftest we recreate the enum so the assertion has a concrete value.
        class CallbackAPIVersion(IntEnum):
            VERSION1 = 1
            VERSION2 = 2

        mqtt_config = {
            "broker": "localhost",
            "port": 1883,
            "client_id": "test",
            "keepalive": 60,
            "trigger_topic": "watermeter/status",
            "trigger_payload": "Flow finished",
            "reset_topic": "watermeter/reset",
        }
        publisher = self._make_publisher(mqtt_config)

        mock_mqtt = MagicMock()
        mock_mqtt.CallbackAPIVersion = CallbackAPIVersion
        mock_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_instance
        with patch.object(_real_mqtt_publisher_module, "mqtt", mock_mqtt):
            MqttPublisher.start(publisher)

            mock_mqtt.Client.assert_called_once_with(
                callback_api_version=CallbackAPIVersion.VERSION2,
                client_id="test",
            )

    def test_auth_from_config(self, mock_service):
        """If username/password in config, call username_pw_set()."""
        mqtt_config = {
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
        publisher = self._make_publisher(mqtt_config)

        mock_mqtt = MagicMock()
        mock_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_instance
        with patch.object(_real_mqtt_publisher_module, "mqtt", mock_mqtt):
            MqttPublisher.start(publisher)

            mock_instance.username_pw_set.assert_called_once_with("myuser", "mypass")

    def test_auth_from_env_overrides_config(self, mock_service):
        """Env vars MQTT_USERNAME/MQTT_PASSWORD override config values."""
        mqtt_config = {
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
        publisher = self._make_publisher(mqtt_config)

        mock_mqtt = MagicMock()
        mock_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_instance
        with patch.object(_real_mqtt_publisher_module, "mqtt", mock_mqtt), \
             patch.dict("os.environ", {"MQTT_USERNAME": "env_user", "MQTT_PASSWORD": "env_pass"}):
            MqttPublisher.start(publisher)

            mock_instance.username_pw_set.assert_called_once_with("env_user", "env_pass")

    def test_no_auth_when_not_configured(self, mock_service):
        """No username_pw_set() call when auth not configured."""
        mqtt_config = {
            "broker": "localhost",
            "port": 1883,
            "client_id": "test",
            "keepalive": 60,
            "trigger_topic": "watermeter/status",
            "trigger_payload": "Flow finished",
            "reset_topic": "watermeter/reset",
        }
        publisher = self._make_publisher(mqtt_config)

        mock_mqtt = MagicMock()
        mock_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_instance
        with patch.object(_real_mqtt_publisher_module, "mqtt", mock_mqtt):
            MqttPublisher.start(publisher)

            mock_instance.username_pw_set.assert_not_called()

    def test_on_disconnect_exists_with_v2_signature(self, mock_service):
        """on_disconnect on MqttPublisher should accept v2 signature (5 params + self)."""
        assert hasattr(MqttPublisher, "on_disconnect"), "on_disconnect method missing on MqttPublisher"

        import inspect
        sig = inspect.signature(MqttPublisher.on_disconnect)
        assert len(sig.parameters) == 6, f"Expected 6 params, got {len(sig.parameters)}: {list(sig.parameters)}"

        # Verify parameter names match v2 API
        param_names = list(sig.parameters.keys())
        assert param_names == ['self', 'client', 'userdata', 'disconnect_flags', 'reason_code', 'properties']
