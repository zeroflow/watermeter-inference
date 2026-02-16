"""Unit tests for BL-06: Continuous consumption warning (leak detection).

Tests the _check_sustained_consumption() method, MQTT payload leak_warning flag,
current_state integration, and reset behavior.

Note: watermeter.watermeter_service is mocked at module level in the unit test
conftest (it imports cv2, paho, openvino which aren't available on host).
We temporarily remove the mock to import the real WatermeterService class.
"""

import json
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service():
    """Create a minimal WatermeterService with a mock config for leak detection tests.

    Bypasses __init__ entirely and sets the attributes that _check_sustained_consumption,
    publish_to_mqtt, and reset_previous_value rely on.
    """
    svc = object.__new__(WatermeterService)

    svc.config = {
        'plausibility': {
            'enable_leak_detection': True,
            'sustained_rate_threshold': 0.05,   # m^3/h
            'sustained_rate_readings': 3,        # consecutive pairs
        },
        'homeassistant': {
            'enabled': False,
            'publish_topic': 'watermeter/state',
        },
    }

    svc.rate_history = []
    svc.leak_warning = False
    svc.current_state = {
        'total_value': None,
        'unit': 'm\u00b3',
        'last_update': None,
        'status': 'idle',
        'warnings': [],
        'predictions': [],
        'processing': False,
        'ha_publish_enabled': False,
        'leak_warning': False,
    }
    svc.previous_value = None
    svc.last_update_time = None
    svc.consecutive_rejections = 0
    svc.state_store = None
    svc.mqtt_client = None
    svc.ha_publish_enabled = False
    svc._pending_confirmation = None
    svc._confirmation_timer = None
    svc._last_inference_duration_ms = None
    svc._last_processing_duration_s = None

    # Mock _get_active_model_name method
    svc._get_active_model_name = lambda model_type: None

    return svc


# ---------------------------------------------------------------------------
# Detection logic tests
# ---------------------------------------------------------------------------

class TestCheckSustainedConsumption:
    """Tests for _check_sustained_consumption()."""

    def test_no_warning_insufficient_history(self, service):
        """rate_history has fewer than sustained_rate_readings + 1 entries -> None."""
        base = datetime(2026, 2, 14, 12, 0)
        # Need 4 entries for 3 pairs; provide only 3
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
        ]

        result = service._check_sustained_consumption()
        assert result is None

    def test_no_warning_rate_below_threshold(self, service):
        """All rates between consecutive pairs are below threshold -> None."""
        base = datetime(2026, 2, 14, 12, 0)
        # 0.001 m^3 in 5 min = 0.012 m^3/h, well below 0.05 threshold
        service.rate_history = [
            (100.000, base),
            (100.001, base + timedelta(minutes=5)),
            (100.002, base + timedelta(minutes=10)),
            (100.003, base + timedelta(minutes=15)),
        ]

        result = service._check_sustained_consumption()
        assert result is None

    def test_warning_all_above_threshold(self, service):
        """All N consecutive pairs exceed threshold -> warning string."""
        base = datetime(2026, 2, 14, 12, 0)
        # 0.020 m^3 in 5 min = 0.24 m^3/h, above 0.05 threshold
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
            (100.060, base + timedelta(minutes=15)),
        ]

        result = service._check_sustained_consumption()
        assert result is not None
        assert "Sustained consumption" in result

    def test_no_warning_one_interval_below(self, service):
        """One pair has rate below threshold -> None."""
        base = datetime(2026, 2, 14, 12, 0)
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),    # 0.24 m^3/h - high
            (100.021, base + timedelta(minutes=10)),   # 0.012 m^3/h - LOW
            (100.041, base + timedelta(minutes=15)),   # 0.24 m^3/h - high
        ]

        result = service._check_sustained_consumption()
        assert result is None

    def test_no_warning_feature_disabled(self, service):
        """enable_leak_detection is False -> None regardless of history."""
        service.config['plausibility']['enable_leak_detection'] = False

        base = datetime(2026, 2, 14, 12, 0)
        # Data that would normally trigger a warning
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
            (100.060, base + timedelta(minutes=15)),
        ]

        result = service._check_sustained_consumption()
        assert result is None

    def test_warning_message_format(self, service):
        """Verify the warning string contains rate, duration, reading count, and threshold."""
        base = datetime(2026, 2, 14, 12, 0)
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
            (100.060, base + timedelta(minutes=15)),
        ]

        result = service._check_sustained_consumption()
        assert result is not None

        # Should contain the avg rate as a number (0.240 m^3/h)
        assert "0.240" in result

        # Should contain duration in minutes (15 min total span)
        assert "15" in result

        # Should contain the reading count (3 consecutive)
        assert "3" in result

        # Should contain the threshold (0.05)
        assert "0.05" in result

        # Should contain unit
        assert "m\u00b3/h" in result


# ---------------------------------------------------------------------------
# MQTT payload test
# ---------------------------------------------------------------------------

class TestLeakWarningMqtt:
    """Tests for leak_warning flag in MQTT payload."""

    @pytest.mark.asyncio
    async def test_leak_warning_in_mqtt_payload(self, service):
        """publish_to_mqtt includes leak_warning: True in the payload."""
        # Enable HA publishing
        service.ha_publish_enabled = True
        service.config['homeassistant']['enabled'] = True

        # Set up mock MQTT client
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        # Call publish_to_mqtt with leak_warning=True
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '5', 'confidence': 0.95},
        }
        await service.publish_to_mqtt(
            123.456, ["Sustained consumption: 0.240 m\u00b3/h over 15 min"], predictions,
            leak_warning=True,
        )

        # Verify MQTT client was called
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args

        # Parse the published payload
        payload = json.loads(call_args[1]['json'] if 'json' in call_args[1] else call_args[0][1])
        assert payload['leak_warning'] is True

    @pytest.mark.asyncio
    async def test_no_leak_warning_in_mqtt_payload(self, service):
        """publish_to_mqtt includes leak_warning: False when no leak."""
        service.ha_publish_enabled = True
        service.config['homeassistant']['enabled'] = True

        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '5', 'confidence': 0.95},
        }
        await service.publish_to_mqtt(
            123.456, [], predictions,
            leak_warning=False,
        )

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload['leak_warning'] is False


# ---------------------------------------------------------------------------
# State clearing / reset tests
# ---------------------------------------------------------------------------

class TestLeakWarningStateManagement:
    """Tests for leak_warning in current_state and clearing behavior."""

    def test_leak_warning_clears(self, service):
        """After a leak warning fires, adding a low-rate reading clears it."""
        base = datetime(2026, 2, 14, 12, 0)
        # First: trigger the leak
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
            (100.060, base + timedelta(minutes=15)),
        ]

        result = service._check_sustained_consumption()
        assert result is not None

        # Now: add a reading with very low consumption (below threshold)
        service.rate_history.append(
            (100.061, base + timedelta(minutes=20))  # 0.012 m^3/h
        )

        result = service._check_sustained_consumption()
        assert result is None

    def test_leak_warning_in_current_state(self, service):
        """current_state['leak_warning'] is True when leak is active."""
        base = datetime(2026, 2, 14, 12, 0)
        service.rate_history = [
            (100.000, base),
            (100.020, base + timedelta(minutes=5)),
            (100.040, base + timedelta(minutes=10)),
            (100.060, base + timedelta(minutes=15)),
        ]

        # Simulate what process_reading does
        leak_msg = service._check_sustained_consumption()
        service.leak_warning = leak_msg is not None
        service.current_state['leak_warning'] = service.leak_warning

        assert service.leak_warning is True
        assert service.current_state['leak_warning'] is True

    def test_leak_warning_reset_on_reset_previous_value(self, service):
        """reset_previous_value() clears leak_warning flag."""
        # Simulate an active leak warning
        service.leak_warning = True
        service.current_state['leak_warning'] = True

        service.reset_previous_value()

        assert service.leak_warning is False
        assert service.current_state['leak_warning'] is False
        # Also verify rate_history was cleared
        assert service.rate_history == []
