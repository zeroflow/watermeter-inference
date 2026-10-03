"""HA discovery/state payload constraints that Home Assistant enforces."""

import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock

from watermeter.mqtt_publisher import _HA_ENTITIES, MqttPublisher


def test_read_only_entities_never_use_config_category():
    # HA refuses to add sensor/binary_sensor entities with entity_category "config".
    for entity in _HA_ENTITIES:
        if entity["component"] in ("sensor", "binary_sensor"):
            assert entity.get("entity_category") != "config", entity["object_id"]


def test_timestamp_entities_publish_timezone_aware_values():
    # HA rejects device_class "timestamp" states without timezone information.
    timestamp_ids = [e["object_id"] for e in _HA_ENTITIES if e.get("device_class") == "timestamp"]
    assert "last_update" in timestamp_ids

    meter_state = MagicMock(current_state={}, consecutive_rejections=0, ha_publish_enabled=True)
    rate_tracker = MagicMock(average_rate_per_hour=None)
    publisher = MqttPublisher(
        {"homeassistant": {"publish_topic": "test/state"}},
        meter_state,
        rate_tracker,
        MagicMock(),
        on_trigger=MagicMock(),
        on_reset=MagicMock(),
    )
    publisher.mqtt_client = MagicMock()
    publisher.mqtt_client.is_connected.return_value = True

    asyncio.run(publisher.publish_to_mqtt(1.0, [], {}))

    payload = json.loads(publisher.mqtt_client.publish.call_args[0][1])
    for oid in timestamp_ids:
        assert datetime.fromisoformat(payload[oid]).tzinfo is not None, oid
