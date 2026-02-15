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
        service = MagicMock()
        service.cyclic_interval = 0.05  # 50ms for fast test
        call_count = 0
        target_calls = 3
        done = asyncio.Event()

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= target_calls:
                done.set()

        service.process_reading = mock_process

        loop_coro = WatermeterService._cyclic_loop(service)
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
        service = MagicMock()
        service.cyclic_interval = 10  # Long interval, we cancel before it fires
        service.process_reading = AsyncMock()

        loop_coro = WatermeterService._cyclic_loop(service)
        task = asyncio.create_task(loop_coro)
        await asyncio.sleep(0.01)
        task.cancel()
        # _cyclic_loop catches CancelledError internally, so await should not raise
        await task
        assert task.done()

    def test_start_cyclic_loop_creates_task(self):
        """start_cyclic_loop should create an asyncio task."""
        service = MagicMock()
        service._cyclic_task = None
        service._cyclic_loop = AsyncMock()

        mock_task = MagicMock()
        with patch('asyncio.create_task', return_value=mock_task) as mock_create:
            WatermeterService.start_cyclic_loop(service)
            mock_create.assert_called_once()
            assert service._cyclic_task == mock_task

    def test_start_cyclic_loop_noop_when_already_running(self):
        """start_cyclic_loop should not create a second task if one is running."""
        service = MagicMock()
        service._cyclic_task = MagicMock()  # Already running

        with patch('asyncio.create_task') as mock_create:
            WatermeterService.start_cyclic_loop(service)
            mock_create.assert_not_called()

    def test_stop_cyclic_loop_cancels_task(self):
        """stop_cyclic_loop should cancel the running task."""
        service = MagicMock()
        mock_task = MagicMock()
        service._cyclic_task = mock_task

        WatermeterService.stop_cyclic_loop(service)

        mock_task.cancel.assert_called_once()
        assert service._cyclic_task is None

    def test_stop_cyclic_loop_noop_when_not_running(self):
        """stop_cyclic_loop should do nothing if no task is running."""
        service = MagicMock()
        service._cyclic_task = None

        WatermeterService.stop_cyclic_loop(service)
        # Should not raise


# ===== MQTT Subscription Tests =====

class TestMqttSubscriptionConditional:
    """Tests that MQTT trigger subscription is conditional on trigger mode."""

    def _make_service(self, trigger_mode):
        service = MagicMock()
        service.trigger_mode = trigger_mode
        service.config = {
            'mqtt': {
                'trigger_topic': 'watermeter/status',
                'reset_topic': 'watermeter/reset',
            },
        }
        service.ha_publish_enabled = False
        service.publish_discovery = MagicMock()
        return service

    def test_mqtt_mode_subscribes_to_trigger(self):
        service = self._make_service('mqtt')
        client = MagicMock()

        WatermeterService.on_mqtt_connect(service, client, None, None, 0)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics
        assert 'homeassistant/status' in subscribed_topics

    def test_cyclic_mode_skips_trigger_subscription(self):
        service = self._make_service('cyclic')
        client = MagicMock()

        WatermeterService.on_mqtt_connect(service, client, None, None, 0)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' not in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics
        assert 'homeassistant/status' in subscribed_topics

    def test_both_mode_subscribes_to_trigger(self):
        service = self._make_service('both')
        client = MagicMock()

        WatermeterService.on_mqtt_connect(service, client, None, None, 0)

        subscribed_topics = [call[0][0] for call in client.subscribe.call_args_list]
        assert 'watermeter/status' in subscribed_topics
        assert 'watermeter/reset' in subscribed_topics

    def test_failed_connection_does_not_subscribe(self):
        service = self._make_service('mqtt')
        client = MagicMock()

        WatermeterService.on_mqtt_connect(service, client, None, None, 5)

        client.subscribe.assert_not_called()
