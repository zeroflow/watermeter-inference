"""Unit tests for BL-07: User confirmation via Home Assistant.

Tests the confirmation request/response flow:
- Trigger condition evaluation (_should_request_confirmation)
- MQTT publish format (_publish_confirmation_request)
- Response handling: confirm, reject, correct
- Timeout auto-reject
- Disabled mode (no-op)
- API endpoint (/api/confirmation/status)
- State cleanup on reset

Note: watermeter.watermeter_service is mocked at module level in the unit test
conftest (it imports cv2, paho, openvino which aren't available on host).
We temporarily remove the mock to import the real WatermeterService class.
"""

import json
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load the REAL WatermeterService class (bypassing the conftest mock).
# ---------------------------------------------------------------------------
_ws_mock_backup = {}
for _name in ['watermeter_service', 'watermeter.watermeter_service']:
    if _name in sys.modules:
        _ws_mock_backup[_name] = sys.modules.pop(_name)

from watermeter.confirmation import ConfirmationManager  # noqa: E402
from watermeter.meter_state import MeterState  # noqa: E402
from watermeter.mqtt_publisher import MqttPublisher  # noqa: E402
from watermeter.rate_tracker import RateTracker  # noqa: E402
from watermeter.watermeter_service import WatermeterService  # noqa: E402

# Restore the mocks so other test files still work
for _name, _mock in _ws_mock_backup.items():
    sys.modules[_name] = _mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def service():
    """Create a minimal WatermeterService with confirmation config for testing.

    Bypasses __init__ and directly initializes the backing objects so that
    all service properties work without lazy-init shims.
    """
    svc = object.__new__(WatermeterService)

    svc.config = {
        'confirmation': {
            'enabled': True,
            'request_topic': 'watermeter/confirmation_request',
            'response_topic': 'watermeter/confirmation_response',
            'timeout_minutes': 5,
            'min_warnings': 1,
            'min_low_confidence_positions': 2,
            'max_rate_jump_factor': 3.0,
        },
        'inference': {
            'confidence_threshold': 0.8,
        },
        'plausibility': {
            'enable_leak_detection': False,
            'rate_history_size': 5,
        },
        'homeassistant': {
            'enabled': False,
            'publish_topic': 'watermeter/state',
        },
    }

    # Initialize backing objects so properties work without _ensure_* shims
    svc._state = MeterState(ha_publish_enabled=False)
    svc._rate_tracker = RateTracker(max_size=5)

    class _MqttMock:
        mqtt_client = None
        loop = None

    svc._mqtt = _MqttMock()
    svc._confirmation_manager = ConfirmationManager(
        config=svc.config,
        rate_tracker=svc._rate_tracker,
        meter_state=svc._state,
        state_store=None,
    )

    svc.rate_history = []
    svc.rate_history_size = 5
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
    svc.loop = None
    svc._pending_confirmation = None
    svc._confirmation_timer = None

    return svc


@pytest.fixture
def service_disabled():
    """Service with confirmation disabled."""
    svc = object.__new__(WatermeterService)

    svc.config = {
        'confirmation': {
            'enabled': False,
        },
        'inference': {
            'confidence_threshold': 0.8,
        },
        'plausibility': {
            'rate_history_size': 5,
        },
        'homeassistant': {
            'enabled': False,
            'publish_topic': 'watermeter/state',
        },
    }

    # Initialize backing objects so properties work without _ensure_* shims
    svc._state = MeterState(ha_publish_enabled=False)
    svc._rate_tracker = RateTracker(max_size=5)

    class _MqttMock:
        mqtt_client = None
        loop = None

    svc._mqtt = _MqttMock()
    svc._confirmation_manager = ConfirmationManager(
        config=svc.config,
        rate_tracker=svc._rate_tracker,
        meter_state=svc._state,
        state_store=None,
    )

    svc.rate_history = []
    svc.rate_history_size = 5
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
    svc.loop = None
    svc._pending_confirmation = None
    svc._confirmation_timer = None

    return svc


def _make_predictions(**overrides):
    """Helper to create a minimal predictions dict."""
    defaults = {
        'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits', 'image_bytes': b'\xff'},
        'digit_2': {'id': 'digit_2', 'class': '2', 'confidence': 0.90, 'model': 'digits', 'image_bytes': b'\xff'},
        'analog_1': {'id': 'analog_1', 'class': '5.0', 'confidence': 0.85, 'model': 'arrows', 'image_bytes': b'\xff'},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests: _should_request_confirmation
# ---------------------------------------------------------------------------

class TestShouldRequestConfirmation:
    """Tests for the trigger condition evaluator."""

    def test_returns_none_when_disabled(self, service_disabled):
        """Disabled config -> always None."""
        result = service_disabled._should_request_confirmation(
            123.4, ['some warning'], _make_predictions()
        )
        assert result is None

    def test_returns_none_no_triggers(self, service):
        """Clean reading with no warnings, high confidence -> None."""
        result = service._should_request_confirmation(
            123.4, [], _make_predictions()
        )
        assert result is None

    def test_triggers_on_warnings(self, service):
        """Reading with >= min_warnings triggers confirmation."""
        result = service._should_request_confirmation(
            123.4, ['Rate spike detected'], _make_predictions()
        )
        assert result is not None
        assert 'warning' in result.lower()

    def test_triggers_on_low_confidence(self, service):
        """Reading with >= min_low_confidence_positions triggers."""
        preds = _make_predictions(
            digit_1={'id': 'digit_1', 'class': '1', 'confidence': 0.50, 'model': 'digits', 'image_bytes': b'\xff'},
            digit_2={'id': 'digit_2', 'class': '2', 'confidence': 0.60, 'model': 'digits', 'image_bytes': b'\xff'},
        )
        result = service._should_request_confirmation(123.4, [], preds)
        assert result is not None
        assert 'confidence' in result.lower()

    def test_no_trigger_single_low_confidence(self, service):
        """Only 1 low-confidence position, threshold is 2 -> no trigger."""
        preds = _make_predictions(
            digit_1={'id': 'digit_1', 'class': '1', 'confidence': 0.50, 'model': 'digits', 'image_bytes': b'\xff'},
        )
        result = service._should_request_confirmation(123.4, [], preds)
        assert result is None

    def test_triggers_on_rate_jump(self, service):
        """Rate jump exceeding max_rate_jump_factor * average triggers."""
        now = datetime.now()
        service.previous_value = 100.0
        # Set last_update_time to 5 minutes ago so the rate calculation works
        service.last_update_time = now - timedelta(minutes=5)
        # History establishes avg rate of ~0.24 m^3/h
        service.rate_history = [
            (99.980, now - timedelta(minutes=15)),
            (100.000, now - timedelta(minutes=5)),
        ]

        # Jump: new value = 102.0, elapsed 5 min -> rate ~ 24 m^3/h
        # Average rate from history is ~0.24 m^3/h -> 24 >> 3 * 0.24
        result = service._should_request_confirmation(102.0, [], _make_predictions())

        assert result is not None
        assert 'rate' in result.lower() or 'jump' in result.lower()

    def test_skips_if_already_pending(self, service):
        """If a confirmation is already pending, don't stack another."""
        service._pending_confirmation = {'value': 123.0, 'timestamp': datetime.now()}
        result = service._should_request_confirmation(
            124.0, ['some warning'], _make_predictions()
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _publish_confirmation_request
# ---------------------------------------------------------------------------

class TestPublishConfirmationRequest:
    """Tests for publishing confirmation requests to MQTT."""

    def test_publishes_to_correct_topic(self, service):
        """Request is published to the configured request_topic."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        preds = _make_predictions()
        service._publish_confirmation_request(123.456, ['warn'], preds, 'test reason')

        mock_client.publish.assert_called_once()
        args, kwargs = mock_client.publish.call_args
        assert args[0] == 'watermeter/confirmation_request'

    def test_payload_contains_required_fields(self, service):
        """Published payload contains value, reason, warnings, positions, timestamp."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        preds = _make_predictions()
        service._publish_confirmation_request(123.456, ['warn1'], preds, 'test reason')

        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])

        assert payload['value'] == 123.456
        assert payload['reason'] == 'test reason'
        assert payload['warnings'] == ['warn1']
        assert 'positions' in payload
        assert 'timestamp' in payload
        assert 'digit_1' in payload['positions']

    def test_does_not_overwrite_pending_state(self, service):
        """_publish_confirmation_request does NOT set _pending_confirmation (caller does)."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        # Pre-set pending state (as the caller process_reading would)
        caller_pending = {
            'value': 123.456,
            'warnings': ['w'],
            'predictions': _make_predictions(),
            'reason': 'reason',
            'timestamp': datetime.now(),
            'previous_value_before': 100.0,
            'last_update_time_before': datetime(2026, 2, 14, 10, 0),
        }
        service._pending_confirmation = caller_pending

        preds = _make_predictions()
        service._publish_confirmation_request(123.456, ['w'], preds, 'reason')

        # Should still be the exact same object set by the caller
        assert service._pending_confirmation is caller_pending
        assert service._pending_confirmation['previous_value_before'] == 100.0

    def test_starts_timeout_timer(self, service):
        """A daemon timer is started after publishing."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        service.mqtt_client = mock_client

        preds = _make_predictions()
        service._publish_confirmation_request(123.456, [], preds, 'reason')

        assert service._confirmation_timer is not None
        assert service._confirmation_timer.daemon is True
        # Clean up
        service._cancel_confirmation_timer()

    def test_no_publish_without_mqtt_clears_pending(self, service):
        """If MQTT is not connected, auto-accepts: clears pending and sets status."""
        service.mqtt_client = None
        # Simulate caller having set pending state
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': [],
            'predictions': _make_predictions(),
            'reason': 'reason',
            'timestamp': datetime.now(),
            'previous_value_before': 100.0,
            'last_update_time_before': None,
        }
        preds = _make_predictions()
        service._publish_confirmation_request(123.456, [], preds, 'reason')

        # Should have cleared pending state (auto-accept)
        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'ok'

    def test_no_publish_without_mqtt_warns_with_warnings(self, service):
        """If MQTT is not connected and there are warnings, status is 'warning'."""
        service.mqtt_client = None
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': ['spike'],
            'predictions': _make_predictions(),
            'reason': 'reason',
            'timestamp': datetime.now(),
            'previous_value_before': 100.0,
            'last_update_time_before': None,
        }
        preds = _make_predictions()
        service._publish_confirmation_request(123.456, ['spike'], preds, 'reason')

        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'warning'


# ---------------------------------------------------------------------------
# Tests: _handle_confirmation_response
# ---------------------------------------------------------------------------

class TestHandleConfirmationResponse:
    """Tests for handling user responses."""

    def _setup_pending(self, service, value=123.456):
        """Helper to set up a pending confirmation."""
        service.previous_value = value
        service.last_update_time = datetime.now()
        service.rate_history = [(value, datetime.now())]
        service._pending_confirmation = {
            'value': value,
            'warnings': ['test warning'],
            'predictions': _make_predictions(),
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 123.0,
            'last_update_time_before': datetime(2026, 2, 14, 12, 0),
        }
        # Set up a timer to cancel
        service._confirmation_timer = threading.Timer(300, lambda: None)
        service._confirmation_timer.daemon = True
        service._confirmation_timer.start()

    def test_confirm_clears_pending(self, service):
        """'confirm' clears pending and sets status to ok."""
        self._setup_pending(service)
        service._handle_confirmation_response('confirm')

        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'ok'

    def test_confirm_preserves_value(self, service):
        """'confirm' keeps the optimistically applied value."""
        self._setup_pending(service, value=123.456)
        service._handle_confirmation_response('confirm')

        assert service.previous_value == 123.456

    def test_confirm_schedules_ha_publish(self, service):
        """'confirm' schedules MQTT publish to HA via the event loop."""
        mock_loop = MagicMock()
        service.loop = mock_loop
        self._setup_pending(service)
        service._handle_confirmation_response('confirm')

        # asyncio.run_coroutine_threadsafe uses call_soon_threadsafe internally
        assert mock_loop.call_soon_threadsafe.called

    def test_reject_reverts_value(self, service):
        """'reject' reverts previous_value to the snapshot before the reading."""
        self._setup_pending(service)
        service._handle_confirmation_response('reject')

        assert service.previous_value == 123.0
        assert service.last_update_time == datetime(2026, 2, 14, 12, 0)

    def test_reject_removes_rate_history_entry(self, service):
        """'reject' pops the last optimistic rate_history entry."""
        self._setup_pending(service)
        original_len = len(service.rate_history)
        service._handle_confirmation_response('reject')

        assert len(service.rate_history) == original_len - 1

    def test_reject_clears_pending(self, service):
        """'reject' clears pending and sets status to rejected."""
        self._setup_pending(service)
        service._handle_confirmation_response('reject')

        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'rejected'

    def test_correct_applies_new_value(self, service):
        """'correct:125.0' applies the corrected value."""
        self._setup_pending(service, value=123.456)
        service._handle_confirmation_response('correct:125.0')

        assert service.previous_value == 125.0
        assert service.current_state['total_value'] == 125.0

    def test_correct_updates_rate_history(self, service):
        """'correct:125.0' replaces the last rate_history entry."""
        self._setup_pending(service, value=123.456)
        service._handle_confirmation_response('correct:125.0')

        assert service.rate_history[-1][0] == 125.0

    def test_correct_clears_pending(self, service):
        """'correct:125.0' clears pending and sets status ok."""
        self._setup_pending(service)
        service._handle_confirmation_response('correct:125.0')

        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'ok'

    def test_correct_invalid_value_ignores(self, service):
        """'correct:abc' logs error but does not crash; pending remains."""
        self._setup_pending(service)
        service._handle_confirmation_response('correct:abc')

        # Pending should NOT be cleared (invalid response)
        assert service._pending_confirmation is not None

    def test_unknown_payload_ignored(self, service):
        """Unknown payload is logged but pending is not cleared."""
        self._setup_pending(service)
        service._handle_confirmation_response('something_unknown')

        assert service._pending_confirmation is not None

    def test_response_without_pending_ignored(self, service):
        """Response when nothing is pending is a no-op."""
        service._pending_confirmation = None
        # Should not raise
        service._handle_confirmation_response('confirm')


# ---------------------------------------------------------------------------
# Tests: _confirmation_timeout
# ---------------------------------------------------------------------------

class TestConfirmationTimeout:
    """Tests for the auto-reject timeout."""

    def test_timeout_reverts_state(self, service):
        """Timeout reverts previous_value and clears pending."""
        service.previous_value = 123.456
        service.last_update_time = datetime.now()
        service.rate_history = [(123.456, datetime.now())]
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': [],
            'predictions': {},
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 123.0,
            'last_update_time_before': datetime(2026, 2, 14, 12, 0),
        }
        service._confirmation_timer = None

        service._confirmation_timeout()

        assert service.previous_value == 123.0
        assert service.last_update_time == datetime(2026, 2, 14, 12, 0)
        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'timeout'

    def test_timeout_pops_rate_history(self, service):
        """Timeout removes the last optimistic rate_history entry."""
        service.rate_history = [(100.0, datetime.now()), (123.456, datetime.now())]
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': [],
            'predictions': {},
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 100.0,
            'last_update_time_before': datetime(2026, 2, 14, 12, 0),
        }
        service._confirmation_timer = None

        service._confirmation_timeout()
        assert len(service.rate_history) == 1
        assert service.rate_history[0][0] == 100.0

    def test_timeout_noop_when_nothing_pending(self, service):
        """Timeout with no pending confirmation is a no-op."""
        service._pending_confirmation = None
        service._confirmation_timeout()
        # Should not raise or change state

    def test_timeout_routes_through_event_loop(self, service):
        """_confirmation_timeout routes to event loop via call_soon_threadsafe."""
        mock_loop = MagicMock()
        service.loop = mock_loop

        service._confirmation_timeout()

        mock_loop.call_soon_threadsafe.assert_called_once_with(service._do_confirmation_timeout)

    def test_timeout_with_none_previous_value_clears_store(self, service):
        """Timeout when previous_value_before is None calls state_store.clear()."""
        mock_store = MagicMock()
        service.state_store = mock_store
        service.previous_value = 123.456
        service.last_update_time = datetime.now()
        service.rate_history = [(123.456, datetime.now())]
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': [],
            'predictions': {},
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': None,
            'last_update_time_before': None,
        }
        service._confirmation_timer = None

        service._do_confirmation_timeout()

        assert service.previous_value is None
        mock_store.clear.assert_called_once()
        mock_store.save.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: get_confirmation_status
# ---------------------------------------------------------------------------

class TestGetConfirmationStatus:
    """Tests for the status query method (used by the API endpoint)."""

    def test_returns_none_when_no_pending(self, service):
        """No pending -> None."""
        assert service.get_confirmation_status() is None

    def test_returns_details_when_pending(self, service):
        """Pending -> dict with value, reason, seconds_remaining."""
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': ['w1'],
            'predictions': {},
            'reason': 'test reason',
            'timestamp': datetime.now(),
            'previous_value_before': 123.0,
            'last_update_time_before': None,
        }

        status = service.get_confirmation_status()
        assert status is not None
        assert status['value'] == 123.456
        assert status['reason'] == 'test reason'
        assert status['timeout_minutes'] == 5
        assert 'seconds_remaining' in status
        assert status['seconds_remaining'] > 0

    def test_seconds_remaining_decreases(self, service):
        """seconds_remaining reflects elapsed time."""
        two_min_ago = datetime.now() - timedelta(minutes=2)
        service._pending_confirmation = {
            'value': 100.0,
            'warnings': [],
            'predictions': {},
            'reason': 'test',
            'timestamp': two_min_ago,
            'previous_value_before': None,
            'last_update_time_before': None,
        }

        status = service.get_confirmation_status()
        # 5 min timeout - 2 min elapsed = ~3 min remaining = ~180s
        assert 170 <= status['seconds_remaining'] <= 190


# ---------------------------------------------------------------------------
# Tests: reset clears confirmation
# ---------------------------------------------------------------------------

class TestResetClearsConfirmation:
    """Verify that reset_previous_value clears any pending confirmation."""

    def test_reset_clears_pending(self, service):
        """reset_previous_value clears _pending_confirmation."""
        service._pending_confirmation = {'value': 100.0, 'timestamp': datetime.now()}
        service._confirmation_timer = threading.Timer(300, lambda: None)
        service._confirmation_timer.daemon = True
        service._confirmation_timer.start()

        service.reset_previous_value()

        assert service._pending_confirmation is None
        assert service._confirmation_timer is None


# ---------------------------------------------------------------------------
# Tests: MQTT message routing
# ---------------------------------------------------------------------------

class TestMqttMessageRouting:
    """Tests that confirmation responses are handled by _handle_confirmation_response."""

    def test_confirmation_response_routed(self, service):
        """'confirm' payload clears pending and sets status ok."""
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': [],
            'predictions': _make_predictions(),
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 123.0,
            'last_update_time_before': None,
        }
        service._confirmation_timer = None

        service._handle_confirmation_response('confirm')

        assert service._pending_confirmation is None
        assert service.current_state['status'] == 'ok'


class TestConfirmationRespectsHighWater:
    """Final review I4: MQTT confirmation replies go through the service's clamped publish."""

    RESPONSE_TOPIC = 'watermeter/confirmation_response'

    def _wire(self, service, tmp_path, high_water=125.0, pending_value=123.456):
        from unittest.mock import AsyncMock

        from watermeter.persistence import StateStore

        service._mqtt = MagicMock()  # raw publisher underneath WatermeterService.publish_to_mqtt
        service._mqtt.publish_to_mqtt = AsyncMock()
        service.state_store = StateStore(str(tmp_path / 'state.json'))
        service._state.published_high_water = high_water
        service.previous_value = pending_value
        service.last_update_time = datetime.now()
        service.rate_history = [(pending_value, datetime.now())]
        service._pending_confirmation = {
            'value': pending_value,
            'warnings': ['test warning'],
            'predictions': _make_predictions(),
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 123.0,
            'last_update_time_before': datetime(2026, 2, 14, 12, 0),
        }
        publisher = MqttPublisher(
            config={'mqtt': {'trigger_topic': 'watermeter/trigger', 'reset_topic': 'watermeter/reset'}},
            meter_state=service._state,
            rate_tracker=service._rate_tracker,
            confirmation_manager=service._confirmation_manager,
            on_trigger=MagicMock(),
            on_reset=MagicMock(),
            publish_fn=service.publish_to_mqtt,
        )
        return publisher

    def _reply(self, publisher, payload):
        import asyncio

        async def run():
            publisher.loop = asyncio.get_running_loop()
            msg = MagicMock()
            msg.topic = self.RESPONSE_TOPIC
            msg.payload = payload.encode()
            publisher.on_message(None, None, msg)
            for _ in range(5):
                await asyncio.sleep(0)

        asyncio.run(run())

    def test_confirm_is_clamped_to_high_water(self, service, tmp_path):
        publisher = self._wire(service, tmp_path, high_water=125.0, pending_value=123.456)
        self._reply(publisher, 'confirm')
        assert service._mqtt.publish_to_mqtt.call_args.args[0] == 125.0

    def test_correct_lowers_and_persists_high_water(self, service, tmp_path):
        publisher = self._wire(service, tmp_path, high_water=125.0, pending_value=123.456)
        self._reply(publisher, 'correct:120.0')
        assert service._mqtt.publish_to_mqtt.call_args.args[0] == 120.0
        assert service._state.published_high_water == 120.0
        assert service.state_store.load_published_value() == 120.0

    def test_correct_without_mqtt_still_lowers_high_water(self, service, tmp_path):
        """Like a manual set, the user's correction is authoritative even when MQTT is down."""
        self._wire(service, tmp_path, high_water=125.0, pending_value=123.456)
        service._mqtt.loop = None
        service._handle_confirmation_response('correct:120.0')
        assert service._state.published_high_water == 120.0
        assert service.state_store.load_published_value() == 120.0


# ---------------------------------------------------------------------------
# Tests: on_mqtt_connect subscribes to response topic
# ---------------------------------------------------------------------------

class TestMqttConnectSubscription:
    """Tests that MqttPublisher.on_connect subscribes to the confirmation response topic."""

    def _make_publisher(self, conf_config):
        """Create a minimal MqttPublisher mock for on_connect testing."""
        publisher = MagicMock()
        publisher.trigger_mode = 'mqtt'
        publisher.config = {
            'mqtt': {
                'trigger_topic': 'watermeter/status',
                'reset_topic': 'watermeter/reset',
            },
        }
        publisher._confirmation_manager.get_config.return_value = conf_config
        publisher.publish_discovery = MagicMock()
        publisher.publish_training_stats = MagicMock()
        return publisher

    def test_subscribes_when_enabled(self):
        """MqttPublisher.on_connect subscribes to response topic when confirmation is enabled."""
        publisher = self._make_publisher({
            'enabled': True,
            'response_topic': 'watermeter/confirmation_response',
        })
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 0, None)

        subscribed_topics = [c[0][0] for c in client.subscribe.call_args_list]
        assert 'watermeter/confirmation_response' in subscribed_topics

    def test_does_not_subscribe_when_disabled(self):
        """MqttPublisher.on_connect skips response topic when confirmation is disabled."""
        publisher = self._make_publisher({'enabled': False})
        client = MagicMock()

        MqttPublisher.on_connect(publisher, client, None, None, 0, None)

        subscribed_topics = [c[0][0] for c in client.subscribe.call_args_list]
        assert 'watermeter/confirmation_response' not in subscribed_topics


# ---------------------------------------------------------------------------
# Tests: disabled mode is completely transparent
# ---------------------------------------------------------------------------

class TestDisabledMode:
    """When confirmation is disabled, the pipeline must be unaffected."""

    def test_should_request_always_none(self, service_disabled):
        """_should_request_confirmation always returns None when disabled."""
        # Even with warnings that would normally trigger
        result = service_disabled._should_request_confirmation(
            100.0, ['warn1', 'warn2', 'warn3'], _make_predictions()
        )
        assert result is None

    def test_get_status_none(self, service_disabled):
        """get_confirmation_status returns None when disabled."""
        assert service_disabled.get_confirmation_status() is None


# ---------------------------------------------------------------------------
# Tests: process_reading skips when confirmation is pending
# ---------------------------------------------------------------------------

class TestProcessReadingSkipsPending:
    """Verify that process_reading returns early when a confirmation is pending."""

    @pytest.mark.asyncio
    async def test_skips_when_pending(self, service):
        """process_reading returns current_state without processing when pending."""
        import asyncio
        service.processing_lock = asyncio.Lock()

        service._pending_confirmation = {
            'value': 123.0,
            'warnings': [],
            'predictions': {},
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': 100.0,
            'last_update_time_before': None,
        }
        original_status = service.current_state['status']

        result = await service.process_reading()

        # Should return current_state without changing status
        assert result is service.current_state
        # Status should NOT have changed to 'processing'
        assert service.current_state['status'] == original_status
        # pending should still be set (untouched)
        assert service._pending_confirmation is not None


# ---------------------------------------------------------------------------
# Tests: reject with None previous_value calls state_store.clear()
# ---------------------------------------------------------------------------

class TestRejectWithNonePreviousValue:
    """Verify reject path clears state_store when previous_value_before is None."""

    def test_reject_with_none_clears_store(self, service):
        """Reject when previous_value_before is None calls state_store.clear()."""
        mock_store = MagicMock()
        service.state_store = mock_store
        service.previous_value = 123.456
        service.last_update_time = datetime.now()
        service.rate_history = [(123.456, datetime.now())]
        service._pending_confirmation = {
            'value': 123.456,
            'warnings': ['test'],
            'predictions': _make_predictions(),
            'reason': 'test',
            'timestamp': datetime.now(),
            'previous_value_before': None,
            'last_update_time_before': None,
        }
        service._confirmation_timer = None

        service._handle_confirmation_response('reject')

        assert service.previous_value is None
        mock_store.clear.assert_called_once()
        mock_store.save.assert_not_called()
        assert service.current_state['status'] == 'rejected'
