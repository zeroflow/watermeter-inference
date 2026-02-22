"""Unit tests for MQTT reconnection logic."""

import threading
import time
from unittest.mock import MagicMock, patch


class TestMqttReconnect:
    """Tests for MQTT reconnect-on-disconnect behavior."""

    def _make_publisher(self):
        """Create an MqttPublisher with minimal mocked dependencies."""
        from watermeter.mqtt_publisher import MqttPublisher

        config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "trigger",
                "trigger_payload": "go",
                "reset_topic": "reset",
            },
            "trigger": {"mode": "mqtt"},
            "homeassistant": {"enabled": False},
        }
        publisher = MqttPublisher(
            config=config,
            meter_state=MagicMock(),
            rate_tracker=MagicMock(),
            confirmation_manager=MagicMock(get_config=MagicMock(return_value={"enabled": False})),
            on_trigger=MagicMock(),
            on_reset=MagicMock(),
            get_active_model_fn=MagicMock(return_value=("none", "none")),
            get_inference_duration_fn=MagicMock(return_value=None),
            get_processing_duration_fn=MagicMock(return_value=None),
        )
        publisher.mqtt_client = MagicMock()
        return publisher

    def test_unclean_disconnect_schedules_reconnect(self):
        """on_disconnect with reason_code != 0 must attempt reconnect."""
        publisher = self._make_publisher()
        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False  # non-zero
        reason_code.__str__ = lambda self: "Unspecified error"

        with patch("watermeter.mqtt_publisher.time.sleep"):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.2)

        publisher.mqtt_client.reconnect.assert_called()

    def test_clean_disconnect_does_not_reconnect(self):
        """on_disconnect with reason_code == 0 must NOT attempt reconnect."""
        publisher = self._make_publisher()
        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: other == 0  # is zero
        reason_code.__str__ = lambda self: "Success"

        publisher.on_disconnect(
            client=publisher.mqtt_client,
            userdata=None,
            disconnect_flags=MagicMock(),
            reason_code=reason_code,
            properties=None,
        )

        time.sleep(0.1)
        publisher.mqtt_client.reconnect.assert_not_called()

    def test_reconnect_retries_on_failure(self):
        """Reconnect loop retries with backoff when reconnect() raises."""
        publisher = self._make_publisher()
        publisher.mqtt_client.reconnect.side_effect = [OSError("refused"), None]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "Unspecified error"

        with patch("watermeter.mqtt_publisher.time.sleep"):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.3)

        assert publisher.mqtt_client.reconnect.call_count >= 2

    def test_reconnect_uses_exponential_backoff(self):
        """Reconnect delays double on each failure up to max."""
        publisher = self._make_publisher()
        sleep_calls = []

        publisher.mqtt_client.reconnect.side_effect = [
            OSError("fail1"),
            OSError("fail2"),
            OSError("fail3"),
            OSError("fail4"),
            None,
        ]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("watermeter.mqtt_publisher.time.sleep", side_effect=mock_sleep):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.3)

        assert len(sleep_calls) >= 4
        assert sleep_calls[0] == 5
        assert sleep_calls[1] == 10
        assert sleep_calls[2] == 20
        assert sleep_calls[3] == 40

    def test_reconnect_caps_at_120_seconds(self):
        """Backoff is capped at 120 seconds."""
        publisher = self._make_publisher()
        sleep_calls = []

        publisher.mqtt_client.reconnect.side_effect = [OSError("fail")] * 10 + [None]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("watermeter.mqtt_publisher.time.sleep", side_effect=mock_sleep):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.5)

        assert max(sleep_calls) <= 120

    def test_no_concurrent_reconnect_loops(self):
        """Multiple disconnect events must not spawn multiple reconnect loops."""
        publisher = self._make_publisher()

        call_count = {"value": 0}
        original_reconnect = publisher.mqtt_client.reconnect

        def counting_reconnect():
            call_count["value"] += 1
            time.sleep(0.1)

        publisher.mqtt_client.reconnect = MagicMock(side_effect=counting_reconnect)

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        with patch("watermeter.mqtt_publisher.time.sleep"):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.5)

        assert publisher.mqtt_client.reconnect.call_count <= 2

    def test_start_configures_reconnect_delay(self):
        """start() must call reconnect_delay_set on the paho client."""
        from enum import IntEnum

        import watermeter.mqtt_publisher as _mqtt_pub_module

        class CallbackAPIVersion(IntEnum):
            VERSION1 = 1
            VERSION2 = 2

        publisher = self._make_publisher()
        publisher.mqtt_client = None  # will be created in start()

        mock_mqtt = MagicMock()
        mock_mqtt.CallbackAPIVersion = CallbackAPIVersion
        mock_instance = MagicMock()
        mock_mqtt.Client.return_value = mock_instance
        mock_instance.connect.return_value = None

        with patch.object(_mqtt_pub_module, "mqtt", mock_mqtt):
            publisher.start()

            mock_instance.reconnect_delay_set.assert_called_once_with(
                min_delay=5, max_delay=120
            )
