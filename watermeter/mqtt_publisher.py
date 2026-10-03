"""MQTT Publisher — Home Assistant integration for the watermeter service.

Encapsulates the HA entity registry, MQTT client lifecycle, and all
publish/callback methods that were previously on WatermeterService.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Home Assistant MQTT Discovery — Entity Registry
# ---------------------------------------------------------------------------
# Each dict describes one HA entity that belongs to the "watermeter_ai" device.
# `publish_discovery()` iterates over this list to emit discovery messages.
#
# Keys:
#   object_id          – used in discovery topic and as the JSON payload key
#   name               – friendly name shown in HA
#   component          – "sensor" or "binary_sensor"
#   icon               – MDI icon string
#   device_class       – (optional) HA device class
#   state_class        – (optional) HA state class
#   unit_of_measurement – (optional)
#   entity_category    – (optional) "diagnostic" (HA rejects "config" on read-only sensors)
#   state_topic_key    – "main" (default) or "training_stats"
#   options            – (optional) list of enum options (for enum sensors)
# ---------------------------------------------------------------------------
_HA_ENTITIES = [
    # ── Primary entities (published every reading) ─────────────────────────
    {
        "object_id": "water_usage",
        "name": "Water Usage",
        "component": "sensor",
        "icon": "mdi:water",
        "device_class": "water",
        "state_class": "total_increasing",
        "unit_of_measurement": "m\u00b3",
    },
    {
        "object_id": "water_usage_raw",
        "name": "Water Usage Raw",
        "component": "sensor",
        "icon": "mdi:water-outline",
        "device_class": "water",
        "state_class": "total",
        "unit_of_measurement": "m\u00b3",
    },
    {
        "object_id": "leak_warning",
        "name": "Leak Warning",
        "component": "binary_sensor",
        "icon": "mdi:water-alert",
        "device_class": "moisture",
    },
    # ── Diagnostic entities ────────────────────────────────────────────────
    {
        "object_id": "min_confidence",
        "name": "Minimum Confidence",
        "component": "sensor",
        "icon": "mdi:percent-circle",
        "state_class": "measurement",
        "unit_of_measurement": "%",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "status",
        "name": "Status",
        "component": "sensor",
        "icon": "mdi:information-outline",
        "entity_category": "diagnostic",
        "device_class": "enum",
        "options": [
            "idle",
            "ok",
            "warning",
            "error",
            "no_models",
            "pending_confirmation",
            "processing",
            "timeout",
            "rejected",
        ],
    },
    {
        "object_id": "consecutive_rejections",
        "name": "Consecutive Rejections",
        "component": "sensor",
        "icon": "mdi:close-octagon-outline",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "pipeline_status",
        "name": "Pipeline Status",
        "component": "sensor",
        "icon": "mdi:pipe-valve",
        "device_class": "enum",
        "options": ["OK", "DEGRADED", "REJECTED", "FAILED", "STALE"],
        "entity_category": "diagnostic",
    },
    {
        "object_id": "consecutive_alignment_failures",
        "name": "Consecutive Alignment Failures",
        "component": "sensor",
        "icon": "mdi:image-broken-variant",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "unlabeled_digits",
        "name": "Unlabeled Images (Digits)",
        "component": "sensor",
        "icon": "mdi:image-edit-outline",
        "state_class": "measurement",
        "unit_of_measurement": "images",
        "entity_category": "diagnostic",
        "state_topic_key": "training_stats",
    },
    {
        "object_id": "unlabeled_arrows",
        "name": "Unlabeled Images (Arrows)",
        "component": "sensor",
        "icon": "mdi:image-edit-outline",
        "state_class": "measurement",
        "unit_of_measurement": "images",
        "entity_category": "diagnostic",
        "state_topic_key": "training_stats",
    },
    {
        "object_id": "training_digits",
        "name": "Training Images (Digits)",
        "component": "sensor",
        "icon": "mdi:image-check",
        "state_class": "total_increasing",
        "unit_of_measurement": "images",
        "entity_category": "diagnostic",
        "state_topic_key": "training_stats",
    },
    {
        "object_id": "training_arrows",
        "name": "Training Images (Arrows)",
        "component": "sensor",
        "icon": "mdi:image-check",
        "state_class": "total_increasing",
        "unit_of_measurement": "images",
        "entity_category": "diagnostic",
        "state_topic_key": "training_stats",
    },
    {
        "object_id": "last_rejected_value",
        "name": "Last Rejected Value",
        "component": "sensor",
        "icon": "mdi:water-remove",
        "device_class": "water",
        "unit_of_measurement": "m\u00b3",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "last_rejected_reason",
        "name": "Last Rejected Reason",
        "component": "sensor",
        "icon": "mdi:alert-circle-outline",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "average_rate",
        "name": "Average Rate",
        "component": "sensor",
        "icon": "mdi:speedometer",
        "state_class": "measurement",
        "unit_of_measurement": "m\u00b3/h",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "last_update",
        "name": "Last Update",
        "component": "sensor",
        "icon": "mdi:clock-outline",
        "device_class": "timestamp",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "mqtt_connected",
        "name": "MQTT Connected",
        "component": "binary_sensor",
        "device_class": "connectivity",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "processing",
        "name": "Processing",
        "component": "binary_sensor",
        "device_class": "running",
        "entity_category": "diagnostic",
    },
    # ── Timing entities ────────────────────────────────────────────────────
    {
        "object_id": "inference_duration",
        "name": "Inference Duration",
        "component": "sensor",
        "icon": "mdi:timer-outline",
        "device_class": "duration",
        "state_class": "measurement",
        "unit_of_measurement": "ms",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "processing_duration",
        "name": "Processing Duration",
        "component": "sensor",
        "icon": "mdi:timer",
        "device_class": "duration",
        "state_class": "measurement",
        "unit_of_measurement": "s",
        "entity_category": "diagnostic",
    },
    # ── Model entities ─────────────────────────────────────────────────────
    {
        "object_id": "active_digits_model",
        "name": "Active Digits Model",
        "component": "sensor",
        "icon": "mdi:brain",
        "entity_category": "diagnostic",
    },
    {
        "object_id": "active_arrows_model",
        "name": "Active Arrows Model",
        "component": "sensor",
        "icon": "mdi:brain",
        "entity_category": "diagnostic",
    },
]


class MqttPublisher:
    """Manages the paho MQTT client, HA discovery, and all publish/callback logic.

    Args:
        config: The full service config dict.
        meter_state: MeterState instance (reads previous_value, current_state, etc.)
        rate_tracker: RateTracker instance for average_rate_per_hour.
        confirmation_manager: ConfirmationManager instance for get_config() / handle_response().
        on_trigger: Callback invoked when a trigger MQTT message arrives.
            Signature: ``() -> None`` (fires process_reading via the service).
        on_reset: Callback invoked when a reset MQTT message arrives.
            Signature: ``() -> None`` (fires reset_previous_value via the service).
        get_active_model_fn: Optional callable(model_type: str) -> Optional[str].
        get_inference_duration_fn: Optional callable() -> Optional[int].
        get_processing_duration_fn: Optional callable() -> Optional[float].
    """

    def __init__(
        self,
        config: dict,
        meter_state,
        rate_tracker,
        confirmation_manager,
        on_trigger: Callable,
        on_reset: Callable,
        get_active_model_fn: Optional[Callable] = None,
        get_inference_duration_fn: Optional[Callable] = None,
        get_processing_duration_fn: Optional[Callable] = None,
    ) -> None:
        self.config = config
        self._meter_state = meter_state
        self._rate_tracker = rate_tracker
        self._confirmation_manager = confirmation_manager
        self._on_trigger = on_trigger
        self._on_reset = on_reset
        self._get_active_model_fn = get_active_model_fn
        self._get_inference_duration_fn = get_inference_duration_fn
        self._get_processing_duration_fn = get_processing_duration_fn

        self.mqtt_client = None
        self.loop = None
        self._reconnecting = False  # guard against concurrent reconnect loops

    # ── State accessors ─────────────────────────────────────────────────────

    @property
    def trigger_mode(self) -> str:
        """Trigger mode from config."""
        return self.config.get("trigger", {}).get("mode", "mqtt")

    # ── Publish methods ─────────────────────────────────────────────────────

    async def publish_to_mqtt(
        self,
        value: float,
        warnings: List[str],
        predictions: Dict,
        *,
        leak_warning: bool = False,
        raw_value: Optional[float] = None,
    ) -> None:
        """Publish a full-state JSON payload to the shared HA state topic."""
        if not self._meter_state.ha_publish_enabled:
            logger.info("HA publishing disabled - skipping MQTT publish")
            return

        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping publish")
            return

        ha_config = self.config["homeassistant"]

        # Minimum confidence across all predictions (percent)
        if predictions:
            min_conf = min(p["confidence"] for p in predictions.values()) * 100
            min_conf = round(min_conf, 1)
        else:
            min_conf = None

        # Average consumption rate
        avg_rate = self._rate_tracker.average_rate_per_hour

        # Last rejected reason — may be a list; join if so
        rejected_reasons = self._meter_state.current_state.get("last_rejected_reasons") or []
        if isinstance(rejected_reasons, list):
            last_rejected_reason = "; ".join(rejected_reasons) if rejected_reasons else ""
        else:
            last_rejected_reason = str(rejected_reasons)

        # Timing / model info via optional callbacks
        inference_dur = self._get_inference_duration_fn() if self._get_inference_duration_fn else None
        processing_dur = self._get_processing_duration_fn() if self._get_processing_duration_fn else None
        digits_model = self._get_active_model_fn("digits") if self._get_active_model_fn else None
        arrows_model = self._get_active_model_fn("arrows") if self._get_active_model_fn else None

        payload = {
            "water_usage": round(value, 4),
            "water_usage_raw": round(raw_value, 6) if raw_value is not None else round(value, 4),
            "leak_warning": leak_warning,
            "min_confidence": min_conf,
            "status": self._meter_state.current_state.get("status", "idle"),
            "consecutive_rejections": self._meter_state.consecutive_rejections,
            "last_rejected_value": self._meter_state.current_state.get("last_rejected_value"),
            "last_rejected_reason": last_rejected_reason,
            "average_rate": round(avg_rate, 4) if avg_rate is not None else None,
            "last_update": datetime.now().astimezone().isoformat(),
            "mqtt_connected": True,
            "processing": False,
            "inference_duration": inference_dur,
            "processing_duration": processing_dur,
            "active_digits_model": digits_model,
            "active_arrows_model": arrows_model,
            # Pipeline-health fields (Task 4): keep dashboard + HA in lockstep.
            "pipeline_status": self._meter_state.current_state.get("pipeline_status", "OK"),
            "consecutive_alignment_failures": self._meter_state.current_state.get("consecutive_alignment_failures", 0),
            "last_alignment_error": self._meter_state.current_state.get("last_alignment_error"),
        }

        topic = ha_config["publish_topic"]
        self.mqtt_client.publish(topic, json.dumps(payload), qos=2, retain=True)
        logger.info(f"Published to MQTT: {topic}")

    def publish_training_stats(self) -> None:
        """Publish training data statistics to HA via MQTT (slow cadence)."""
        if not self._meter_state.ha_publish_enabled:
            return
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return

        ha_config = self.config["homeassistant"]
        training_path = Path(self.config.get("low_confidence", {}).get("save_path", "/training"))

        digits_input = training_path / "digits" / "input"
        arrows_input = training_path / "arrows" / "input"
        digits_gt = training_path / "digits" / "ground_truth"
        arrows_gt = training_path / "arrows" / "ground_truth"

        payload = {
            "unlabeled_digits": (sum(1 for _ in digits_input.glob("*.jpg")) if digits_input.exists() else 0),
            "unlabeled_arrows": (sum(1 for _ in arrows_input.glob("*.jpg")) if arrows_input.exists() else 0),
            "training_digits": (
                sum(sum(1 for _ in d.glob("*.jpg")) for d in digits_gt.iterdir() if d.is_dir())
                if digits_gt.exists()
                else 0
            ),
            "training_arrows": (
                sum(sum(1 for _ in d.glob("*.jpg")) for d in arrows_gt.iterdir() if d.is_dir())
                if arrows_gt.exists()
                else 0
            ),
        }

        topic = ha_config["publish_topic"] + "/training_stats"
        self.mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=True)
        logger.info(f"Published training stats to {topic}")

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT Discovery messages for all entities."""
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping discovery")
            return

        if not self._meter_state.ha_publish_enabled:
            logger.warning("Home Assistant publishing not enabled - skipping discovery")
            return

        ha_config = self.config["homeassistant"]
        discovery_prefix = ha_config["discovery_prefix"]
        main_state_topic = ha_config["publish_topic"]
        training_stats_topic = f"{main_state_topic}/training_stats"

        # Shared device block — identical in every discovery message
        device_block = {
            "identifiers": ["watermeter_ai"],
            "name": ha_config["device"]["name"],
            "manufacturer": ha_config["device"]["manufacturer"],
            "model": ha_config["device"]["model"],
        }

        for entity in _HA_ENTITIES:
            component = entity["component"]
            object_id = entity["object_id"]

            # Determine state topic
            topic_key = entity.get("state_topic_key", "main")
            state_topic = training_stats_topic if topic_key == "training_stats" else main_state_topic

            # Build discovery payload
            payload: Dict = {
                "name": entity["name"],
                "unique_id": f"watermeter_ai_{object_id}",
                "state_topic": state_topic,
                "value_template": "{{ value_json." + object_id + " }}",
                "device": device_block,
            }

            # Optional fields
            for key in ("device_class", "state_class", "unit_of_measurement", "icon", "entity_category", "options"):
                if key in entity:
                    payload[key] = entity[key]

            # Binary sensor specifics
            if component == "binary_sensor":
                payload["payload_on"] = True
                payload["payload_off"] = False

            # Discovery topic: {prefix}/{component}/watermeter_ai/{object_id}/config
            discovery_topic = f"{discovery_prefix}/{component}/watermeter_ai/{object_id}/config"
            self.mqtt_client.publish(discovery_topic, json.dumps(payload), qos=1, retain=True)

        logger.info(f"Published MQTT Discovery for {len(_HA_ENTITIES)} entities")

    # ── MQTT Callbacks ──────────────────────────────────────────────────────

    def on_connect(self, client, userdata, connect_flags, reason_code, properties):
        """MQTT connect callback (paho v2 API)."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            mqtt_config = self.config["mqtt"]

            # Subscribe to trigger topic only in mqtt/both mode
            if self.trigger_mode in ("mqtt", "both"):
                client.subscribe(mqtt_config["trigger_topic"], qos=2)
                logger.info(f"Subscribed to {mqtt_config['trigger_topic']}")
            else:
                logger.info("Cyclic-only mode — skipping trigger topic subscription")

            # Always subscribe to reset and HA status
            client.subscribe(mqtt_config["reset_topic"], qos=2)
            client.subscribe("homeassistant/status", qos=1)
            logger.info(f"Subscribed to {mqtt_config['reset_topic']}")
            logger.info("Subscribed to homeassistant/status")

            # Subscribe to confirmation response topic if enabled (BL-07)
            conf_config = self._confirmation_manager.get_config()
            if conf_config["enabled"]:
                client.subscribe(conf_config["response_topic"], qos=2)
                logger.info(f"Subscribed to {conf_config['response_topic']}")

            # Publish discovery on connect, then send initial training stats
            self.publish_discovery()
            self.publish_training_stats()
        else:
            logger.error(f"MQTT connection failed: {reason_code}")

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        """MQTT disconnect callback (paho v2 API)."""
        if reason_code == 0:
            logger.info("Disconnected from MQTT broker (clean)")
            return

        logger.warning(f"Disconnected from MQTT broker: {reason_code}")

        # Guard: only one reconnect loop at a time
        if self._reconnecting:
            logger.debug("Reconnect loop already active — skipping")
            return

        self._reconnecting = True
        thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        thread.start()

    def _reconnect_loop(self):
        """Reconnect to broker with exponential backoff (5s -> 120s cap)."""
        delay = 5
        try:
            while True:
                time.sleep(delay)
                try:
                    self.mqtt_client.reconnect()
                    logger.info("Reconnected to MQTT broker")
                    return
                except (OSError, ConnectionRefusedError) as exc:
                    logger.warning(f"MQTT reconnect failed ({exc}), retrying in {delay}s")
                    delay = min(delay * 2, 120)
        finally:
            self._reconnecting = False

    def on_message(self, client, userdata, msg):
        """MQTT message callback."""
        mqtt_config = self.config["mqtt"]
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        logger.info(f"MQTT message: {topic} = {payload}")

        if topic == mqtt_config["trigger_topic"]:
            if payload == mqtt_config["trigger_payload"]:
                logger.info("Trigger received - starting processing")
                # Start processing in background from MQTT thread
                if self.loop:
                    asyncio.run_coroutine_threadsafe(self._on_trigger(), self.loop)
                else:
                    logger.error("Event loop not available - cannot process reading")

        elif topic == mqtt_config["reset_topic"]:
            # Route through event loop to avoid mutating shared state from MQTT thread
            if self.loop:
                self.loop.call_soon_threadsafe(self._on_reset)
            else:
                self._on_reset()

        elif topic == self._confirmation_manager.get_config()["response_topic"]:
            # Route through event loop to keep state mutations on the main thread
            if self.loop:
                self.loop.call_soon_threadsafe(
                    self._confirmation_manager.handle_response,
                    payload,
                    self.loop,
                    self.publish_to_mqtt,
                )
            else:
                self._confirmation_manager.handle_response(
                    payload,
                    loop=self.loop,
                    publish_fn=self.publish_to_mqtt,
                )

        elif topic == "homeassistant/status":
            if payload == "online":
                logger.info("Home Assistant came online - republishing discovery")
                self.publish_discovery()

    # ── Client lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialize and start MQTT client.

        Connects to the MQTT broker. If the broker is unreachable the app
        continues without MQTT — paho's background loop will keep retrying.
        """
        # Capture the event loop for MQTT callbacks
        try:
            self.loop = asyncio.get_running_loop()
            logger.info("Event loop captured for MQTT callbacks")
        except RuntimeError:
            logger.warning("No running event loop - MQTT triggers may not work")

        mqtt_config = self.config["mqtt"]

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

        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect

        logger.info(f"Connecting to MQTT broker {mqtt_config['broker']}:{mqtt_config['port']}")
        try:
            self.mqtt_client.connect(mqtt_config["broker"], mqtt_config["port"], mqtt_config["keepalive"])
        except (OSError, ConnectionRefusedError) as exc:
            logger.warning(
                f"MQTT broker not reachable ({exc}). " f"App continues without MQTT — will reconnect automatically."
            )

        # Configure paho's built-in reconnect backoff as belt-and-suspenders
        self.mqtt_client.reconnect_delay_set(min_delay=5, max_delay=120)

        # Start loop in background thread (handles reconnect automatically)
        self.mqtt_client.loop_start()
        logger.info("MQTT client started")

    def stop(self) -> None:
        """Stop MQTT client."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("MQTT client stopped")
