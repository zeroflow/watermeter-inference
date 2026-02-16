"""
Water Meter AI Service
Combines image fetching, OpenVINO inference, consistency checks, and MQTT publishing
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import base64

import cv2
import numpy as np
import httpx
import yaml
import paho.mqtt.client as mqtt
from .inference import get_inference_service
from .persistence import StateStore
from .image_pipeline import ImagePipeline
from .position_utils import get_position_ids
from .low_confidence_capture import LowConfidenceCapture
from .scheduling import SchedulingManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
#   entity_category    – (optional) "diagnostic" or "config"
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
        "options": ["idle", "ok", "warning", "error", "no_models", "pending_confirmation", "processing", "timeout", "rejected"],
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
    {
        "object_id": "confirmation_pending",
        "name": "Confirmation Pending",
        "component": "binary_sensor",
        "icon": "mdi:human-greeting-proximity",
        "entity_category": "diagnostic",
    },
    # ── Timing entities (values populated in WP2) ─────────────────────────
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
    # ── Config entities ────────────────────────────────────────────────────
    {
        "object_id": "active_digits_model",
        "name": "Active Digits Model",
        "component": "sensor",
        "icon": "mdi:brain",
        "entity_category": "config",
    },
    {
        "object_id": "active_arrows_model",
        "name": "Active Arrows Model",
        "component": "sensor",
        "icon": "mdi:brain",
        "entity_category": "config",
    },
]


class WatermeterService:
    """Main service for watermeter reading and inference."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the watermeter service."""
        # Load configuration
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # Update logging level from config
        log_level = getattr(logging, self.config["logging"]["level"])
        logging.getLogger().setLevel(log_level)

        # State management
        self.previous_value: Optional[float] = None
        self.last_update_time: Optional[datetime] = None
        self.ha_publish_enabled: bool = self.config["homeassistant"]["enabled"]
        self.current_state: Dict = {
            "total_value": None,
            "unit": "m³",
            "last_update": None,
            "status": "idle",
            "warnings": [],
            "predictions": [],
            "processing": False,
            "ha_publish_enabled": self.ha_publish_enabled,
            "leak_warning": False,
            "last_published_value": None,
            "last_published_timestamp": None,
            "last_rejected_value": None,
            "last_rejected_timestamp": None,
            "last_rejected_reasons": [],
        }

        # Persistence
        persistence_config = self.config.get("persistence", {})
        if persistence_config.get("enabled", False):
            self.state_store = StateStore(persistence_config["state_file"])
            # Load previous state
            self.previous_value, self.last_update_time = self.state_store.load()
            # Populate last_published from persisted state (BL-14)
            if self.previous_value is not None:
                self.current_state["last_published_value"] = self.previous_value
                self.current_state["last_published_timestamp"] = (
                    self.last_update_time.strftime("%H:%M") if self.last_update_time else None
                )
        else:
            self.state_store = None
            logger.info("Persistence disabled")

        # Low confidence capture
        self._low_confidence = LowConfidenceCapture(self.config)

        # Rate history for plausibility checks (list of (value, timestamp) tuples)
        self.rate_history: List[Tuple[float, datetime]] = []
        self.rate_history_size = self.config["plausibility"].get("rate_history_size", 5)

        # Consecutive rejection tracking for stuck state detection
        self.consecutive_rejections = 0
        self.max_consecutive_rejections = 5  # Warn user after this many rejections

        # Leak detection state
        self.leak_warning: bool = False

        # Trigger mode
        trigger_config = self.config.get("trigger", {})
        self.trigger_mode = trigger_config.get("mode", "mqtt")
        self.cyclic_interval = trigger_config.get("cyclic_interval", 300)

        # Async lock for processing
        self.processing_lock = asyncio.Lock()

        # Event loop reference for MQTT callbacks
        self.loop = None

        # MQTT client
        self.mqtt_client = None

        # Image pipeline (fetching, rotation, alignment, ROI extraction)
        self._image_pipeline = ImagePipeline(self.config)

        # Scheduling manager (cyclic loop and stats publishing)
        self._scheduler = SchedulingManager(
            cyclic_interval=self.cyclic_interval,
            process_fn=self.process_reading,
            stats_fn=self.publish_training_stats,
        )

        # Confirmation state (BL-07)
        self._pending_confirmation: Optional[Dict] = None
        self._confirmation_timer: Optional[threading.Timer] = None

        # Timing instrumentation (populated by process_reading, published via MQTT)
        self._last_inference_duration_ms: Optional[int] = None
        self._last_processing_duration_s: Optional[float] = None

        logger.info(f"WatermeterService initialized (trigger_mode={self.trigger_mode})")

    # ── Image pipeline delegation ──────────────────────────────────────────
    # These methods delegate to ImagePipeline. Config is synced before each
    # call so that external code that sets `service.config = ...` directly
    # (e.g. routes/roi.py) is picked up by the pipeline.

    async def fetch_images(self) -> Dict[str, Tuple[bytes, str]]:
        """Fetch all images from AI-on-the-edge device."""
        self._image_pipeline.config = self.config
        return await self._image_pipeline.fetch_images()

    async def fetch_whole_image(self) -> Optional[bytes]:
        """Fetch the whole source image from AI-on-the-edge device."""
        self._image_pipeline.config = self.config
        return await self._image_pipeline.fetch_whole_image()

    def process_whole_image(self, image_bytes: bytes) -> Dict[str, Tuple[bytes, str]]:
        """Process whole image: rotation, marker alignment, ROI extraction."""
        self._image_pipeline.config = self.config
        return self._image_pipeline.process_whole_image(image_bytes)

    def invalidate_marker_cache(self):
        """Clear cached marker templates so they are reloaded on next alignment."""
        self._image_pipeline.invalidate_marker_cache()

    async def run_inference(self, images: Dict[str, Tuple[bytes, str]]) -> Dict[str, Dict]:
        """
        Run inference on all images.

        Args:
            images: Dict mapping ID to (image_bytes, image_class)

        Returns:
            Dict mapping ID to prediction result
        """
        predictions = {}

        logger.info(f"Running inference on {len(images)} images")

        # Create temporary directory for images
        import tempfile

        temp_dir = Path(tempfile.mkdtemp())

        try:
            for image_id, (image_bytes, image_class) in images.items():
                # Save image temporarily
                temp_path = temp_dir / f"{image_id}.jpg"
                temp_path.write_bytes(image_bytes)

                # Run prediction via inference service (supports hot-reload)
                try:
                    correction_config = self.config.get("correction", {})
                    if correction_config.get("enabled", False):
                        top_k_count = correction_config.get("top_k", 3)
                        top_k_results = get_inference_service().predict_detailed(
                            image_class, str(temp_path), top_k=top_k_count
                        )
                        result = top_k_results[0]
                        predictions[image_id] = {
                            "id": image_id,
                            "class": result["class"],
                            "confidence": result["confidence"],
                            "model": image_class,
                            "image_bytes": image_bytes,
                            "top_k": top_k_results,
                        }
                    else:
                        result = get_inference_service().predict(image_class, str(temp_path))
                        predictions[image_id] = {
                            "id": image_id,
                            "class": result["class"],
                            "confidence": result["confidence"],
                            "model": image_class,
                            "image_bytes": image_bytes,
                        }
                    logger.debug(f"{image_id}: {result['class']} ({result['confidence']:.3f})")
                except Exception as e:
                    logger.error(f"Inference failed for {image_id}: {e}")
                    predictions[image_id] = {
                        "id": image_id,
                        "class": "ERROR",
                        "confidence": 0.0,
                        "model": image_class,
                        "image_bytes": image_bytes,
                        "error": str(e),
                    }
        finally:
            # Cleanup temp files
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info(f"Inference completed for {len(predictions)} images")
        return predictions

    def calculate_total(self, predictions: Dict[str, Dict]) -> Tuple[float, Dict]:
        """
        Calculate total water meter reading from predictions.

        Args:
            predictions: Dict of prediction results

        Returns:
            (total_value, raw_values_dict)
        """
        # Determine digit and arrow IDs based on processing mode
        digit_ids, arrow_ids = get_position_ids(self.config)

        # Collect predictions in order
        digits = []
        arrows = []

        for image_id in digit_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] != "NAN" and pred["class"] != "ERROR":
                    digits.append(int(pred["class"]))
                else:
                    logger.warning(f"{image_id} has invalid class: {pred['class']}")
                    digits.append(0)  # Default to 0

        for image_id in arrow_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] != "ERROR":
                    arrows.append(float(pred["class"]))
                else:
                    logger.warning(f"{image_id} has error")
                    arrows.append(0.0)

        # Calculate total with dynamic multipliers based on count
        total = 0.0

        # Digits contribution: first digit has highest place value
        for i, digit in enumerate(digits):
            multiplier = 10 ** (len(digits) - 1 - i)
            total += digit * multiplier

        # Arrows contribution (use floor of value)
        for i, arrow in enumerate(arrows):
            multiplier = 10 ** (-(i + 1))  # 0.1, 0.01, 0.001, ...
            total += int(arrow) * multiplier

        raw_values = {"digits": digits, "arrows": arrows}

        logger.info(f"Calculated total: {total:.4f} m³")
        return total, raw_values

    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """
        Check consistency between adjacent positions.
        A position with .5 (half) should have next position >= 5.

        Args:
            predictions: Dict of prediction results

        Returns:
            List of warning messages
        """
        warnings = []

        # Check if consistency check is enabled
        if not self.config["plausibility"].get("enable_consistency_check", True):
            return warnings

        # Determine IDs based on processing mode
        digit_ids, arrow_ids = get_position_ids(self.config)
        all_ids = digit_ids + arrow_ids

        # Collect all values in order
        all_values = []

        for image_id in all_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] not in ["NAN", "ERROR"]:
                    if pred["model"] == "digits":
                        all_values.append((image_id, int(pred["class"])))
                    else:
                        all_values.append((image_id, float(pred["class"])))

        # Check consistency
        for i in range(len(all_values) - 1):
            current_id, current_val = all_values[i]
            next_id, next_val = all_values[i + 1]

            # Check if current has fractional part >= 0.4 (represents .5)
            current_frac = current_val % 1
            current_has_half = current_frac >= 0.4

            # Check if next is in upper half (>= 5)
            next_int_part = int(next_val)
            next_is_upper_half = next_int_part >= 5

            if current_has_half != next_is_upper_half:
                msg = f"{current_id}={current_val} (half={current_has_half}) vs {next_id}={next_val} (upper={next_is_upper_half})"
                warnings.append(msg)
                logger.warning(f"Consistency check: {msg}")

        return warnings

    def validate_plausibility(self, new_value: float) -> Tuple[bool, List[str]]:
        """
        Validate plausibility of new reading.

        Args:
            new_value: New meter reading

        Returns:
            (is_valid, warnings)
        """
        warnings = []
        config = self.config["plausibility"]

        # Check if we have a previous value
        if self.previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._add_to_rate_history(new_value)
            return True, warnings

        # Reverse detection
        if config["enable_reverse_detection"]:
            if new_value < self.previous_value:
                msg = f"Reverse detected: {self.previous_value:.4f} → {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - self.previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

            # Rate per hour - check against history if available
            if self.last_update_time:
                time_diff = (datetime.now() - self.last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config["max_rate_per_hour"]:
                        # If we have history, verify the rate is consistently high
                        if len(self.rate_history) >= 2:
                            avg_rate = self._calculate_average_rate_per_hour()
                            if avg_rate is not None and avg_rate > config["max_rate_per_hour"]:
                                msg = f"Rate per hour too high: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.error(msg)
                                return False, warnings
                            else:
                                # Single spike, warn but accept
                                msg = f"Rate spike: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            # No history yet, warn but accept
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        return True, warnings

    def _add_to_rate_history(self, value: float) -> None:
        """Add a reading to rate history."""
        self.rate_history.append((value, datetime.now()))
        # Keep only the last N readings
        if len(self.rate_history) > self.rate_history_size:
            self.rate_history = self.rate_history[-self.rate_history_size :]

    def _calculate_average_rate_per_hour(self) -> Optional[float]:
        """Calculate average rate per hour from history."""
        if len(self.rate_history) < 2:
            return None

        # Calculate rate from oldest to newest in history
        oldest_value, oldest_time = self.rate_history[0]
        newest_value, newest_time = self.rate_history[-1]

        time_diff = (newest_time - oldest_time).total_seconds()
        if time_diff <= 0:
            return None

        value_diff = newest_value - oldest_value
        rate_per_hour = (value_diff / time_diff) * 3600
        return rate_per_hour

    def _check_sustained_consumption(self) -> Optional[str]:
        """
        Check if the last N consecutive readings all show rate above threshold.

        Returns:
            Warning message string if leak detected, None otherwise.
        """
        config = self.config["plausibility"]

        if not config.get("enable_leak_detection", True):
            return None

        threshold = config.get("sustained_rate_threshold", 0.05)
        min_readings = config.get("sustained_rate_readings", 3)

        if len(self.rate_history) < min_readings + 1:
            return None

        tail = self.rate_history[-(min_readings + 1) :]

        for i in range(len(tail) - 1):
            val_prev, ts_prev = tail[i]
            val_curr, ts_curr = tail[i + 1]

            time_diff_s = (ts_curr - ts_prev).total_seconds()
            if time_diff_s <= 0:
                return None

            rate_per_hour = ((val_curr - val_prev) / time_diff_s) * 3600

            if rate_per_hour < threshold:
                return None

        total_time_s = (tail[-1][1] - tail[0][1]).total_seconds()
        total_time_min = total_time_s / 60
        avg_rate = ((tail[-1][0] - tail[0][0]) / total_time_s) * 3600

        return (
            f"Sustained consumption: {avg_rate:.3f} m\u00b3/h over {total_time_min:.0f} min "
            f"({min_readings} consecutive readings above {threshold} m\u00b3/h)"
        )

    # ── User Confirmation (BL-07) ───────────────────────────────────────

    def _get_confirmation_config(self) -> Dict:
        """Return the confirmation config with defaults applied."""
        defaults = {
            "enabled": False,
            "request_topic": "watermeter/confirmation_request",
            "response_topic": "watermeter/confirmation_response",
            "timeout_minutes": 5,
            "min_warnings": 1,
            "min_low_confidence_positions": 2,
            "max_rate_jump_factor": 3.0,
        }
        user = self.config.get("confirmation", {})
        return {**defaults, **user}

    def _should_request_confirmation(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict]
    ) -> Optional[str]:
        """
        Decide whether a reading needs user confirmation.

        Returns a reason string if confirmation is needed, None otherwise.
        """
        conf = self._get_confirmation_config()
        if not conf["enabled"]:
            return None

        # Don't stack confirmations -- if one is already pending, skip
        if self._pending_confirmation is not None:
            return None

        # Condition 1: reading has enough warnings
        if len(warnings) >= conf["min_warnings"]:
            return f"{len(warnings)} warning(s) on this reading"

        # Condition 2: enough positions below confidence threshold
        threshold = self.config["inference"]["confidence_threshold"]
        low_conf_count = sum(1 for pred in predictions.values() if pred["confidence"] < threshold)
        if low_conf_count >= conf["min_low_confidence_positions"]:
            return f"{low_conf_count} position(s) below confidence threshold"

        # Condition 3: rate jump relative to average
        if self.previous_value is not None and self.last_update_time is not None:
            avg_rate = self._calculate_average_rate_per_hour()
            if avg_rate is not None and avg_rate > 0:
                time_diff = (datetime.now() - self.last_update_time).total_seconds()
                if time_diff > 0:
                    current_rate = ((total_value - self.previous_value) / time_diff) * 3600
                    if current_rate > avg_rate * conf["max_rate_jump_factor"]:
                        return (
                            f"Rate jump: {current_rate:.3f} m\u00b3/h "
                            f"exceeds {conf['max_rate_jump_factor']}x average ({avg_rate:.3f})"
                        )

        return None

    def _publish_confirmation_request(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict], reason: str
    ) -> None:
        """
        Publish a confirmation request to MQTT and start the timeout timer.

        Stores the reading details in _pending_confirmation so they can be
        committed or discarded when the user responds.
        """
        conf = self._get_confirmation_config()

        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT not connected -- cannot request confirmation, auto-accepting reading")
            # Clear pending state since we can't actually request confirmation
            self._pending_confirmation = None
            self.current_state["status"] = "warning" if warnings else "ok"
            return

        # Build compact per-position confidence map
        confidences = {
            pred["id"]: {
                "class": pred["class"],
                "confidence": round(pred["confidence"], 3),
            }
            for pred in predictions.values()
        }

        payload = {
            "value": round(total_value, 4),
            "reason": reason,
            "warnings": warnings,
            "positions": confidences,
            "timestamp": datetime.now().isoformat(),
        }

        # NOTE: _pending_confirmation is set by the caller (process_reading)
        # with the correct pre-reading snapshots. Do NOT overwrite it here.

        self.mqtt_client.publish(
            conf["request_topic"],
            json.dumps(payload),
            qos=1,
        )
        logger.info(f"Published confirmation request: {reason}")

        # Start timeout timer
        self._cancel_confirmation_timer()
        timeout_seconds = conf["timeout_minutes"] * 60
        self._confirmation_timer = threading.Timer(timeout_seconds, self._confirmation_timeout)
        self._confirmation_timer.daemon = True
        self._confirmation_timer.start()
        logger.info(f"Confirmation timeout set: {conf['timeout_minutes']} min")

    def _cancel_confirmation_timer(self) -> None:
        """Cancel the running confirmation timeout timer, if any."""
        if self._confirmation_timer is not None:
            self._confirmation_timer.cancel()
            self._confirmation_timer = None

    def _confirmation_timeout(self) -> None:
        """Called by Timer thread -- routes to event loop for thread safety."""
        if self.loop:
            self.loop.call_soon_threadsafe(self._do_confirmation_timeout)
        else:
            self._do_confirmation_timeout()

    def _do_confirmation_timeout(self) -> None:
        """Actually process the timeout. Runs on the event loop thread."""
        pending = self._pending_confirmation
        if pending is None:
            return

        logger.warning("Confirmation timeout -- auto-rejecting reading")

        # Revert state to before the pending reading was applied
        self.previous_value = pending["previous_value_before"]
        self.last_update_time = pending["last_update_time_before"]

        # Persist the reverted state
        if self.state_store:
            if self.previous_value is not None and self.last_update_time is not None:
                self.state_store.save(self.previous_value, self.last_update_time)
            else:
                self.state_store.clear()

        # Remove the last entry from rate_history (it was added optimistically)
        if self.rate_history:
            self.rate_history.pop()

        self._pending_confirmation = None
        self._confirmation_timer = None
        self.current_state["status"] = "timeout"
        self.current_state["warnings"] = ["Confirmation timed out -- reading discarded"]

        # Publish diagnostic state update so HA reflects the timeout
        if self.previous_value is not None and self.loop:
            asyncio.run_coroutine_threadsafe(
                self.publish_to_mqtt(
                    self.previous_value,
                    self.current_state["warnings"],
                    {},
                    leak_warning=self.leak_warning,
                ),
                self.loop,
            )

        logger.info("Pending confirmation cleared after timeout")

    def _handle_confirmation_response(self, payload: str) -> None:
        """
        Process a user response from the confirmation response MQTT topic.

        Payloads:
            - "confirm"        -- accept the pending reading as-is
            - "reject"         -- discard the pending reading
            - "correct:{value}" -- accept with an overridden value
        """
        pending = self._pending_confirmation
        if pending is None:
            logger.warning("Received confirmation response but nothing is pending -- ignoring")
            return

        self._cancel_confirmation_timer()
        payload = payload.strip()

        if payload == "confirm":
            logger.info("User confirmed the reading")
            # Reading was already optimistically applied -- just publish to HA
            raw_total = self._compute_raw_total(pending["raw_values"]) if pending.get("raw_values") else None
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.publish_to_mqtt(
                        pending["value"],
                        pending["warnings"],
                        pending["predictions"],
                        leak_warning=self.leak_warning,
                        raw_value=raw_total,
                    ),
                    self.loop,
                )
            self.current_state["status"] = "ok"
            self.current_state["warnings"] = pending["warnings"]

        elif payload == "reject":
            logger.info("User rejected the reading")
            # Revert to pre-reading state
            self.previous_value = pending["previous_value_before"]
            self.last_update_time = pending["last_update_time_before"]
            if self.state_store:
                if self.previous_value is not None and self.last_update_time is not None:
                    self.state_store.save(self.previous_value, self.last_update_time)
                else:
                    self.state_store.clear()
            # Remove the optimistic rate_history entry
            if self.rate_history:
                self.rate_history.pop()
            self.current_state["status"] = "rejected"
            self.current_state["warnings"] = ["Reading rejected by user"]

            # Publish diagnostic state update so HA reflects the rejection
            if self.previous_value is not None and self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.publish_to_mqtt(
                        self.previous_value,
                        self.current_state["warnings"],
                        {},
                        leak_warning=self.leak_warning,
                    ),
                    self.loop,
                )

        elif payload.startswith("correct:"):
            corrected_str = payload[len("correct:") :]
            try:
                corrected_value = float(corrected_str)
            except ValueError:
                logger.error(f"Invalid corrected value: {corrected_str!r}")
                return

            logger.info(f"User corrected reading: {pending['value']:.4f} -> {corrected_value:.4f}")

            # Apply the corrected value
            self.previous_value = corrected_value
            self.last_update_time = datetime.now()
            if self.state_store:
                self.state_store.save(self.previous_value, self.last_update_time)

            # Fix up rate_history: replace the last (optimistic) entry
            if self.rate_history:
                self.rate_history[-1] = (corrected_value, self.last_update_time)

            self.current_state["total_value"] = corrected_value
            self.current_state["last_update"] = self.last_update_time.isoformat()
            self.current_state["status"] = "ok"
            self.current_state["warnings"] = [f"User corrected: {pending['value']:.4f} -> {corrected_value:.4f}"]

            # Publish the corrected value to HA
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self.publish_to_mqtt(
                        corrected_value,
                        self.current_state["warnings"],
                        pending["predictions"],
                        leak_warning=self.leak_warning,
                        raw_value=corrected_value,
                    ),
                    self.loop,
                )
        else:
            logger.warning(f"Unknown confirmation response: {payload!r}")
            return

        self._pending_confirmation = None

    def get_confirmation_status(self) -> Optional[Dict]:
        """
        Return details of the pending confirmation, or None if nothing is pending.

        Used by the /api/confirmation/status endpoint.
        """
        pending = self._pending_confirmation
        if pending is None:
            return None

        conf = self._get_confirmation_config()
        elapsed = (datetime.now() - pending["timestamp"]).total_seconds()
        timeout_seconds = conf["timeout_minutes"] * 60

        return {
            "value": round(pending["value"], 4),
            "reason": pending["reason"],
            "warnings": pending["warnings"],
            "timestamp": pending["timestamp"].isoformat(),
            "timeout_minutes": conf["timeout_minutes"],
            "seconds_remaining": max(0, int(timeout_seconds - elapsed)),
        }

    # ── Value Correction Engine (BL-04) ─────────────────────────────────

    def _get_ordered_position_ids(self) -> List[str]:
        """Return position IDs in order: digit_1, ..., analog_1, ... (most to least significant)."""
        digit_ids, arrow_ids = get_position_ids(self.config)
        return digit_ids + arrow_ids

    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
        """Estimate plausible range for next reading based on rate history."""
        if self.previous_value is None or len(self.rate_history) < 3:
            return None
        avg_rate = self._calculate_average_rate_per_hour()
        if avg_rate is None or avg_rate <= 0:
            return None
        hours_elapsed = 0.0
        if self.last_update_time:
            hours_elapsed = (datetime.now() - self.last_update_time).total_seconds() / 3600
        if hours_elapsed <= 0:
            return None
        expected_delta = avg_rate * hours_elapsed
        config = self.config.get("correction", {})
        tolerance = config.get("rate_tolerance_factor", 3.0)
        min_expected = self.previous_value
        max_expected = self.previous_value + expected_delta * tolerance
        return (min_expected, max_expected)

    def _recalculate_with_replacement(
        self, predictions: Dict[str, Dict], replace_id: str, replace_class: str, raw_values: Dict
    ) -> float:
        """Calculate hypothetical total with one position replaced."""
        digit_ids, arrow_ids = get_position_ids(self.config)

        digits = []
        for image_id in digit_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls not in ("NAN", "ERROR"):
                    digits.append(int(cls))
                else:
                    digits.append(0)

        arrows = []
        for image_id in arrow_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls != "ERROR":
                    arrows.append(float(cls))
                else:
                    arrows.append(0.0)

        total = 0.0
        for i, digit in enumerate(digits):
            total += digit * 10 ** (len(digits) - 1 - i)
        for i, arrow in enumerate(arrows):
            total += int(arrow) * 10 ** (-(i + 1))
        return total

    def _check_consistency_improvement(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """Check if replacing a position fixes a consistency violation with adjacent positions."""
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False
        idx = position_ids.index(replace_id)

        def get_value(pid, override_id=None, override_class=None):
            if pid not in predictions:
                return None
            cls = override_class if pid == override_id else predictions[pid]["class"]
            if cls in ("NAN", "ERROR"):
                return None
            return float(cls) if predictions[pid]["model"] == "arrows" else int(cls)

        def has_violation(val_a, val_b):
            """Check half/upper consistency between adjacent positions."""
            frac = val_a % 1
            has_half = frac >= 0.4
            upper = int(val_b) >= 5
            return has_half != upper

        current_violations = 0
        replacement_violations = 0

        # Check pair with previous position (idx-1, idx)
        if idx > 0:
            prev_id = position_ids[idx - 1]
            prev_val = get_value(prev_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if prev_val is not None and curr_val is not None:
                if has_violation(prev_val, curr_val):
                    current_violations += 1
                if alt_val is not None and has_violation(prev_val, alt_val):
                    replacement_violations += 1

        # Check pair with next position (idx, idx+1)
        if idx < len(position_ids) - 1:
            next_id = position_ids[idx + 1]
            next_val = get_value(next_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if next_val is not None and curr_val is not None:
                if has_violation(curr_val, next_val):
                    current_violations += 1
                if alt_val is not None and has_violation(alt_val, next_val):
                    replacement_violations += 1

        return current_violations > 0 and replacement_violations < current_violations

    def _check_cross_arrow_consistency(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """
        Check if replacing an arrow position improves cross-arrow consistency
        with the adjacent more-significant arrow.

        Only applies to arrow-arrow pairs. Uses the constraining arrow's
        confidence as a gate: only fires when the constraining arrow is confident.
        """
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False

        idx = position_ids.index(replace_id)
        if idx == 0:
            return False

        prev_id = position_ids[idx - 1]
        if prev_id not in predictions:
            return False

        # Both must be arrows
        if predictions[prev_id]["model"] != "arrows" or predictions[replace_id]["model"] != "arrows":
            return False

        # Confidence gate
        config = self.config.get("correction", {})
        confidence_gate = config.get("cross_arrow_confidence_gate", 0.8)
        if predictions[prev_id]["confidence"] < confidence_gate:
            return False

        prev_class = predictions[prev_id]["class"]
        if prev_class in ("NAN", "ERROR"):
            return False

        curr_class = predictions[replace_id]["class"]
        if curr_class in ("NAN", "ERROR"):
            return False

        # Determine expected half for the less-significant arrow based on
        # the constraining arrow's fractional position.
        # frac < 0.5 → needle in lower part of dial → next dial in lower half (0-4)
        # frac >= 0.5 → needle in upper part → next dial in upper half (5-9)
        prev_value = float(prev_class)
        prev_frac = prev_value - int(prev_value)
        expected_lower_half = prev_frac < 0.5

        curr_int = int(float(curr_class))
        alt_int = int(float(replace_class))

        curr_in_expected = (curr_int < 5) == expected_lower_half
        alt_in_expected = (alt_int < 5) == expected_lower_half

        return (not curr_in_expected) and alt_in_expected

    def correct_predictions(self, predictions: Dict[str, Dict], raw_total: float, raw_values: Dict) -> List[str]:
        """
        Correct low-confidence predictions using contextual signals.
        Modifies predictions dict in-place. Returns correction warning strings.
        """
        config = self.config.get("correction", {})
        if not config.get("enabled", False):
            return []

        correction_threshold = config.get("confidence_threshold", 0.7)
        min_signal_agreement = config.get("min_signal_agreement", 2)
        min_alternative_confidence = config.get("min_alternative_confidence", 0.05)
        max_corrections = config.get("max_corrections_per_reading", 2)
        corrections = []

        position_ids = self._get_ordered_position_ids()

        # Safety: if all positions are high-confidence, don't touch anything
        all_confident = all(
            predictions[pid]["confidence"] >= correction_threshold for pid in position_ids if pid in predictions
        )
        if all_confident:
            return []

        expected_range = self._estimate_expected_range()

        # Meter rollover guard
        skip_previous_value_signal = False
        if self.previous_value is not None:
            digit_ids = [pid for pid in position_ids if pid.startswith("digit_")]
            if digit_ids:
                digit_count = len(digit_ids)
                max_meter = 10**digit_count
                all_near_zero = all(
                    int(predictions[pid]["class"]) <= 1
                    for pid in digit_ids
                    if pid in predictions and predictions[pid]["class"] not in ("NAN", "ERROR")
                )
                if all_near_zero and self.previous_value > 0.9 * max_meter:
                    skip_previous_value_signal = True

        for pid in position_ids:
            if len(corrections) >= max_corrections:
                break
            if pid not in predictions:
                continue

            pred = predictions[pid]
            if pred["confidence"] >= correction_threshold:
                continue

            top_k = pred.get("top_k", [])
            if not top_k or len(top_k) < 2:
                continue

            alternatives = [
                alt for alt in top_k[1:] if alt["confidence"] >= min_alternative_confidence and alt["class"] != "NAN"
            ]
            if not alternatives:
                continue

            best_alt = None
            best_score = 0

            for alt in alternatives:
                score = 0
                total_with_alt = self._recalculate_with_replacement(predictions, pid, alt["class"], raw_values)

                # Signal 1: Previous value constraint
                if not skip_previous_value_signal and self.previous_value is not None:
                    if raw_total < self.previous_value and total_with_alt >= self.previous_value:
                        score += 1

                # Signal 2: Expected rate
                if expected_range is not None:
                    min_exp, max_exp = expected_range
                    if (raw_total < min_exp or raw_total > max_exp) and min_exp <= total_with_alt <= max_exp:
                        score += 1

                # Signal 3: Adjacent position consistency
                if self._check_consistency_improvement(predictions, pid, alt["class"]):
                    score += 1

                # Signal 4: Cross-arrow consistency (BL-05)
                if self._check_cross_arrow_consistency(predictions, pid, alt["class"]):
                    score += 1

                if score > best_score:
                    best_score = score
                    best_alt = alt

            if best_alt is not None and best_score >= min_signal_agreement:
                old_class = pred["class"]
                old_conf = pred["confidence"]
                pred["class"] = best_alt["class"]
                pred["confidence"] = best_alt["confidence"]
                pred["corrected_from"] = old_class
                pred["corrected_confidence"] = old_conf
                pred["correction_signals"] = best_score

                msg = (
                    f"Corrected {pid}: {old_class}\u2192{best_alt['class']} "
                    f"(conf={old_conf:.2f}\u2192{best_alt['confidence']:.2f}, "
                    f"signals={best_score}/{min_signal_agreement})"
                )
                corrections.append(msg)
                logger.info(msg)

        return corrections

    async def save_low_confidence(
        self, image_id: str, image_bytes: bytes, prediction: Dict, next_image_bytes: bytes = None
    ) -> None:
        """Delegate to LowConfidenceCapture."""
        return await self._low_confidence.save_low_confidence(image_id, image_bytes, prediction, next_image_bytes)

    async def process_reading(self) -> Dict:
        """
        Main processing workflow:
        1. Fetch images
        2. Run inference
        3. Calculate value
        4. Consistency check
        5. Plausibility check
        6. Handle low confidence
        7. Update state

        Returns:
            Current state dict
        """
        async with self.processing_lock:
            # If a confirmation is pending, skip processing new readings
            if self._pending_confirmation is not None:
                logger.info("Skipping reading -- confirmation pending for previous reading")
                return self.current_state

            # If no inference models are loaded, exit early
            if not get_inference_service().models_loaded:
                self.current_state["status"] = "no_models"
                self.current_state["warnings"] = ["No inference models loaded. Train or import models via the Training page."]
                return self.current_state

            t_start = time.monotonic()
            try:
                self.current_state["processing"] = True
                self.current_state["warnings"] = []
                self.current_state["status"] = "processing"

                logger.info("=" * 60)
                logger.info("Starting new water meter reading")
                logger.info("=" * 60)

                # 1. Fetch images (separate or whole image mode)
                process_separate = self.config["images"].get("process_separate", False)

                if process_separate:
                    # Fetch individual images from AI-on-the-edge
                    images = await self.fetch_images()
                    if not images:
                        raise Exception("No images fetched")
                else:
                    # Fetch whole image and extract ROIs
                    whole_image = await self.fetch_whole_image()
                    if not whole_image:
                        raise Exception("Failed to fetch whole image")
                    images = self.process_whole_image(whole_image)
                    if not images:
                        raise Exception("No ROIs extracted from whole image")

                # 2. Run inference
                t_inf = time.monotonic()
                predictions = await self.run_inference(images)
                self._last_inference_duration_ms = round((time.monotonic() - t_inf) * 1000)

                # 3. Calculate total
                total_value, raw_values = self.calculate_total(predictions)

                # 3b. Value correction (BL-04)
                correction_warnings = self.correct_predictions(predictions, total_value, raw_values)
                if correction_warnings:
                    total_value, raw_values = self.calculate_total(predictions)
                    logger.info(f"Recalculated total after {len(correction_warnings)} correction(s): {total_value:.4f}")

                # 4. Consistency check
                consistency_warnings = self.check_consistency(predictions)

                # 5. Plausibility check
                is_valid, plausibility_warnings = self.validate_plausibility(total_value)

                all_warnings = correction_warnings + consistency_warnings + plausibility_warnings

                # Leak detection (sustained consumption check)
                leak_msg = self._check_sustained_consumption()
                self.leak_warning = leak_msg is not None
                self.current_state["leak_warning"] = self.leak_warning
                if leak_msg:
                    all_warnings.append(leak_msg)
                    logger.warning(leak_msg)

                # 6. Handle low confidence images
                threshold = self.config["inference"]["confidence_threshold"]
                low_conf_config = self.config["low_confidence"]

                # Get arrow IDs list for finding "next" arrow
                _, arrow_ids = get_position_ids(self.config)

                for pred in predictions.values():
                    if pred["confidence"] < threshold:
                        logger.warning(f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']:.3f})")

                        # For arrows, try to get the next smaller dial's image
                        next_image_bytes = None
                        if pred["model"] == "arrows" and pred["id"] in arrow_ids:
                            idx = arrow_ids.index(pred["id"])
                            if idx + 1 < len(arrow_ids):
                                next_arrow_id = arrow_ids[idx + 1]
                                if next_arrow_id in predictions:
                                    next_image_bytes = predictions[next_arrow_id]["image_bytes"]
                                    logger.debug(f"Including next dial {next_arrow_id} for annotation help")

                        # Save low confidence images if enabled
                        await self.save_low_confidence(pred["id"], pred["image_bytes"], pred, next_image_bytes)
                        # Add warning if enabled
                        if low_conf_config.get("warn_enabled", True):
                            all_warnings.append(
                                f"Low confidence: {pred['id']} = {pred['class']} ({pred['confidence']*100:.1f}%)"
                            )

                # 7. Update state
                # Always store predictions (even on error) so images are displayed
                self.current_state["predictions"] = [
                    {
                        "id": pred["id"],
                        "class": pred["class"],
                        "confidence": pred["confidence"],
                        "model": pred["model"],
                        "image_base64": base64.b64encode(pred["image_bytes"]).decode("utf-8"),
                        # Include correction metadata if present
                        **(
                            {
                                "corrected_from": pred["corrected_from"],
                                "corrected_confidence": pred["corrected_confidence"],
                                "correction_signals": pred["correction_signals"],
                            }
                            if "corrected_from" in pred
                            else {}
                        ),
                    }
                    for pred in predictions.values()
                ]

                if is_valid:
                    # Snapshot state before applying (needed for confirmation revert)
                    prev_value_before = self.previous_value
                    prev_time_before = self.last_update_time

                    self.previous_value = total_value
                    self.last_update_time = datetime.now()
                    self.consecutive_rejections = 0  # Reset rejection counter

                    # Add to rate history for plausibility checks
                    self._add_to_rate_history(total_value)

                    # Persist state to disk
                    if self.state_store:
                        self.state_store.save(self.previous_value, self.last_update_time)

                    self.current_state["total_value"] = total_value
                    self.current_state["last_update"] = self.last_update_time.isoformat()
                    self.current_state["status"] = "warning" if all_warnings else "ok"
                    self.current_state["warnings"] = all_warnings

                    # Track last-published / clear last-rejected (BL-14)
                    self.current_state["last_published_value"] = total_value
                    self.current_state["last_published_timestamp"] = self.last_update_time.strftime("%H:%M")
                    self.current_state["last_rejected_value"] = None
                    self.current_state["last_rejected_timestamp"] = None
                    self.current_state["last_rejected_reasons"] = []

                    logger.info(f"✓ Reading accepted: {total_value:.4f} m³")

                    # Check if user confirmation is needed (BL-07)
                    confirmation_reason = self._should_request_confirmation(
                        total_value,
                        all_warnings,
                        predictions,
                    )
                    if confirmation_reason:
                        # Hold HA publish -- store snapshots for revert on reject/timeout
                        self._pending_confirmation = {
                            "value": total_value,
                            "raw_values": raw_values,
                            "warnings": all_warnings,
                            "predictions": predictions,
                            "reason": confirmation_reason,
                            "timestamp": datetime.now(),
                            "previous_value_before": prev_value_before,
                            "last_update_time_before": prev_time_before,
                        }
                        self._publish_confirmation_request(
                            total_value,
                            all_warnings,
                            predictions,
                            confirmation_reason,
                        )
                        self.current_state["status"] = "pending_confirmation"
                        logger.info(f"Reading held for confirmation: {confirmation_reason}")
                    else:
                        # Normal path -- publish immediately
                        raw_total = self._compute_raw_total(raw_values)
                        await self.publish_to_mqtt(
                            total_value, all_warnings, predictions,
                            leak_warning=self.leak_warning, raw_value=raw_total,
                        )
                else:
                    self.consecutive_rejections += 1
                    self.current_state["status"] = "error"
                    self.current_state["warnings"] = all_warnings
                    self.current_state["total_value"] = total_value

                    # Track last-rejected state (BL-14)
                    self.current_state["last_rejected_value"] = total_value
                    self.current_state["last_rejected_timestamp"] = datetime.now().strftime("%H:%M")
                    self.current_state["last_rejected_reasons"] = plausibility_warnings

                    logger.error(
                        f"✗ Reading rejected: {total_value:.4f} m³ (consecutive: {self.consecutive_rejections})"
                    )

                    # Check for stuck state
                    if self.consecutive_rejections >= self.max_consecutive_rejections:
                        stuck_msg = (
                            f"STUCK: {self.consecutive_rejections} consecutive rejections. "
                            f"Previous value: {self.previous_value:.4f}, Current: {total_value:.4f}. "
                            f"Consider using /reset if previous value is incorrect."
                        )
                        all_warnings.append(stuck_msg)
                        self.current_state["warnings"] = all_warnings
                        logger.error(stuck_msg)

                    # Publish diagnostic state update so HA sees rejection
                    # info immediately (consecutive_rejections, last_rejected_*).
                    # Use the last accepted value -- the rejected reading must
                    # NOT change water_usage / water_usage_raw.
                    if self.previous_value is not None:
                        await self.publish_to_mqtt(
                            self.previous_value, all_warnings, predictions,
                            leak_warning=self.leak_warning,
                        )

                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error during processing: {e}", exc_info=True)
                self.current_state["status"] = "error"
                self.current_state["warnings"] = [str(e)]
                # Try to save predictions if we got that far
                try:
                    if "predictions" in locals() and predictions:
                        self.current_state["predictions"] = [
                            {
                                "id": pred["id"],
                                "class": pred["class"],
                                "confidence": pred["confidence"],
                                "model": pred["model"],
                                "image_base64": base64.b64encode(pred["image_bytes"]).decode("utf-8"),
                            }
                            for pred in predictions.values()
                        ]
                except Exception as pred_err:
                    logger.error(f"Could not save predictions after error: {pred_err}")
            finally:
                self._last_processing_duration_s = round(time.monotonic() - t_start, 2)
                self.current_state["processing"] = False

        return self.current_state

    def _get_active_model_name(self, model_type: str) -> Optional[str]:
        """Return the active model directory name for a model type, or None."""
        from .model_manager import get_model_manager

        try:
            return get_model_manager().get_active_model(model_type, self.config)
        except Exception:
            return None

    def _compute_raw_total(self, raw_values: Dict) -> float:
        """Compute the unrounded total from raw digit and arrow values.

        Unlike ``calculate_total`` which floors each arrow value, this uses
        the continuous arrow predictions to produce a higher-precision reading.
        """
        total = 0.0
        digits = raw_values.get("digits", [])
        arrows = raw_values.get("arrows", [])
        for i, digit in enumerate(digits):
            total += digit * (10 ** (len(digits) - 1 - i))
        for i, arrow in enumerate(arrows):
            total += arrow * (10 ** (-(i + 1)))
        return total

    async def publish_to_mqtt(
        self,
        value: float,
        warnings: List[str],
        predictions: Dict,
        *,
        leak_warning: bool = False,
        raw_value: Optional[float] = None,
    ) -> None:
        """Publish a full-state JSON payload to the shared HA state topic.

        The payload keys match the ``object_id`` values in ``_HA_ENTITIES`` so
        that each entity's ``value_template`` can extract its own value.

        Args:
            value: Meter reading (floored / rounded).
            warnings: List of warning strings.
            predictions: Prediction results dict (keyed by image id).
            leak_warning: Whether a leak is currently detected.
            raw_value: Unrounded meter reading.  Falls back to *value* if not
                provided (e.g. manual-set path where no raw value exists).
        """
        if not self.ha_publish_enabled:
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
        avg_rate = self._calculate_average_rate_per_hour()

        # Last rejected reason — may be a list; join if so
        rejected_reasons = self.current_state.get("last_rejected_reasons") or []
        if isinstance(rejected_reasons, list):
            last_rejected_reason = "; ".join(rejected_reasons) if rejected_reasons else ""
        else:
            last_rejected_reason = str(rejected_reasons)

        payload = {
            "water_usage": round(value, 4),
            "water_usage_raw": round(raw_value, 6) if raw_value is not None else round(value, 4),
            "leak_warning": leak_warning,
            "min_confidence": min_conf,
            "status": self.current_state.get("status", "idle"),
            "consecutive_rejections": self.consecutive_rejections,
            "last_rejected_value": self.current_state.get("last_rejected_value"),
            "last_rejected_reason": last_rejected_reason,
            "average_rate": round(avg_rate, 4) if avg_rate is not None else None,
            "last_update": datetime.now().isoformat(),
            "mqtt_connected": True,
            "processing": False,
            "confirmation_pending": self._pending_confirmation is not None,
            "inference_duration": self._last_inference_duration_ms,
            "processing_duration": self._last_processing_duration_s,
            "active_digits_model": self._get_active_model_name("digits"),
            "active_arrows_model": self._get_active_model_name("arrows"),
        }

        topic = ha_config["publish_topic"]
        self.mqtt_client.publish(topic, json.dumps(payload), qos=2, retain=True)
        logger.info(f"Published to MQTT: {topic}")

    def reset_previous_value(self) -> None:
        """Reset the previous value (for meter replacement or stuck state)."""
        logger.info("Resetting previous value, rate history, and rejection counter")
        self.previous_value = None
        self.last_update_time = None
        self.rate_history = []
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False

        # Clear last-published and last-rejected tracking (BL-14)
        self.current_state["last_published_value"] = None
        self.current_state["last_published_timestamp"] = None
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []

        # Clear any pending confirmation (BL-07)
        self._cancel_confirmation_timer()
        self._pending_confirmation = None

        # Clear persisted state
        if self.state_store:
            self.state_store.clear()

    async def set_manual_value(self, value: float) -> bool:
        """Manually set the meter value (BL-15).

        Updates all internal state as if a reading was accepted, seeds rate
        history so the next automated reading doesn't trigger a false spike,
        and publishes the value to MQTT.

        Returns:
            True if value was published to MQTT, False otherwise
        """
        logger.info(f"Manual meter set: {value:.4f} m³")

        now = datetime.now()

        # Update core state
        self.previous_value = value
        self.last_update_time = now

        # Clear and re-seed rate history
        self.rate_history = [(value, now)]

        # Reset plausibility tracking
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False

        # Cancel any pending confirmation (BL-07)
        self._cancel_confirmation_timer()
        self._pending_confirmation = None

        # Persist state
        if self.state_store:
            self.state_store.save(self.previous_value, self.last_update_time)

        # Update current_state for UI
        self.current_state["total_value"] = value
        self.current_state["last_update"] = now.isoformat()
        self.current_state["status"] = "ok"
        self.current_state["warnings"] = []

        # Update last-published / clear last-rejected (BL-14)
        self.current_state["last_published_value"] = value
        self.current_state["last_published_timestamp"] = now.strftime("%H:%M")
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []

        # Publish to MQTT via shared method (raw_value=value since no raw
        # predictions exist for a manual set)
        can_publish = (
            self.ha_publish_enabled
            and self.mqtt_client
            and self.mqtt_client.is_connected()
        )
        if can_publish:
            try:
                await self.publish_to_mqtt(
                    value, [], {}, leak_warning=False, raw_value=value,
                )
                logger.info(f"Published manual value {value:.4f} to MQTT")
                return True
            except Exception as e:
                logger.error(f"Failed to publish manual value to MQTT: {e}")
                return False
        elif self.ha_publish_enabled:
            logger.warning("MQTT not connected, manual value not published to Home Assistant")

        return False

    def toggle_ha_publish(self, enabled: bool) -> None:
        """Toggle Home Assistant MQTT publishing."""
        self.ha_publish_enabled = enabled
        self.current_state["ha_publish_enabled"] = enabled
        logger.info(f"Home Assistant publishing {'enabled' if enabled else 'disabled'}")

    def publish_training_stats(self) -> None:
        """Publish training data statistics to HA via MQTT (slow cadence).

        Counts files in the training directories (unlabeled input and labeled
        ground truth) and publishes the counts to the ``training_stats`` topic
        so HA entities 7-10 receive updated values.
        """
        if not self.ha_publish_enabled:
            return
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return

        ha_config = self.config["homeassistant"]
        training_path = Path(
            self.config.get("low_confidence", {}).get("save_path", "/training")
        )

        digits_input = training_path / "digits" / "input"
        arrows_input = training_path / "arrows" / "input"
        digits_gt = training_path / "digits" / "ground_truth"
        arrows_gt = training_path / "arrows" / "ground_truth"

        payload = {
            "unlabeled_digits": (
                len(list(digits_input.glob("*.jpg"))) if digits_input.exists() else 0
            ),
            "unlabeled_arrows": (
                len(list(arrows_input.glob("*.jpg"))) if arrows_input.exists() else 0
            ),
            "training_digits": (
                sum(len(list(d.glob("*.jpg"))) for d in digits_gt.iterdir() if d.is_dir())
                if digits_gt.exists()
                else 0
            ),
            "training_arrows": (
                sum(len(list(d.glob("*.jpg"))) for d in arrows_gt.iterdir() if d.is_dir())
                if arrows_gt.exists()
                else 0
            ),
        }

        topic = ha_config["publish_topic"] + "/training_stats"
        self.mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=True)
        logger.info(f"Published training stats to {topic}")

    async def _stats_loop(self) -> None:
        """Periodically publish training data stats to HA (every 5 minutes)."""
        return await self._scheduler._stats_loop()

    def start_stats_loop(self) -> None:
        """Start the periodic training stats background task."""
        return self._scheduler.start_stats_loop()

    def stop_stats_loop(self) -> None:
        """Cancel the periodic training stats background task."""
        return self._scheduler.stop_stats_loop()

    def publish_discovery(self) -> None:
        """Publish Home Assistant MQTT Discovery messages for all entities.

        Iterates over the module-level ``_HA_ENTITIES`` registry and publishes
        one discovery config per entity.  All entities share the same ``device``
        block so Home Assistant groups them into a single device.
        """
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            logger.warning("MQTT client not connected - skipping discovery")
            return

        if not self.ha_publish_enabled:
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
            for key in ("device_class", "state_class", "unit_of_measurement",
                        "icon", "entity_category", "options"):
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

    # MQTT Callbacks
    def on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties):
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
            conf_config = self._get_confirmation_config()
            if conf_config["enabled"]:
                client.subscribe(conf_config["response_topic"], qos=2)
                logger.info(f"Subscribed to {conf_config['response_topic']}")

            # Publish discovery on connect, then send initial training stats
            self.publish_discovery()
            self.publish_training_stats()
        else:
            logger.error(f"MQTT connection failed: {reason_code}")

    def on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        """MQTT disconnect callback (paho v2 API)."""
        if reason_code == 0:
            logger.info("Disconnected from MQTT broker (clean)")
        else:
            logger.warning(f"Disconnected from MQTT broker: {reason_code} — will reconnect automatically")

    def on_mqtt_message(self, client, userdata, msg):
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
                    asyncio.run_coroutine_threadsafe(self.process_reading(), self.loop)
                else:
                    logger.error("Event loop not available - cannot process reading")

        elif topic == mqtt_config["reset_topic"]:
            # Route through event loop to avoid mutating shared state from MQTT thread
            if self.loop:
                self.loop.call_soon_threadsafe(self.reset_previous_value)
            else:
                self.reset_previous_value()

        elif topic == self._get_confirmation_config()["response_topic"]:
            # Route through event loop to keep state mutations on the main thread
            if self.loop:
                self.loop.call_soon_threadsafe(self._handle_confirmation_response, payload)
            else:
                self._handle_confirmation_response(payload)

        elif topic == "homeassistant/status":
            if payload == "online":
                logger.info("Home Assistant came online - republishing discovery")
                self.publish_discovery()

    def start_mqtt(self):
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

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect

        logger.info(f"Connecting to MQTT broker {mqtt_config['broker']}:{mqtt_config['port']}")
        try:
            self.mqtt_client.connect(mqtt_config["broker"], mqtt_config["port"], mqtt_config["keepalive"])
        except (OSError, ConnectionRefusedError) as exc:
            logger.warning(
                f"MQTT broker not reachable ({exc}). " f"App continues without MQTT — will reconnect automatically."
            )

        # Start loop in background thread (handles reconnect automatically)
        self.mqtt_client.loop_start()
        logger.info("MQTT client started")

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

        # Sync config to image pipeline
        self._image_pipeline.config = new_config

        # Sync config to low confidence capture
        self._low_confidence.config = new_config

        # Re-sync cached scalars
        trigger_config = new_config.get("trigger", {})
        self.trigger_mode = trigger_config.get("mode", "mqtt")
        self.cyclic_interval = trigger_config.get("cyclic_interval", 300)
        self.rate_history_size = new_config.get("plausibility", {}).get("rate_history_size", 5)
        self.ha_publish_enabled = new_config.get("homeassistant", {}).get("enabled", True)

        # Sync cyclic interval to scheduler
        self._scheduler.cyclic_interval = self.cyclic_interval

        # Reconnect MQTT if connection params changed
        if mqtt_changed and self.mqtt_client:
            logger.info("MQTT config changed — reconnecting")
            self.stop_mqtt()
            self.start_mqtt()

        logger.info("Config reloaded successfully")
        return {"config_updated": True, "mqtt_reconnected": mqtt_changed}

    def stop_mqtt(self):
        """Stop MQTT client."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logger.info("MQTT client stopped")

    async def _cyclic_loop(self):
        """Periodically trigger process_reading at the configured interval."""
        return await self._scheduler._cyclic_loop()

    def start_cyclic_loop(self):
        """Start the cyclic trigger background task."""
        return self._scheduler.start_cyclic_loop()

    def stop_cyclic_loop(self):
        """Cancel the cyclic trigger background task."""
        return self._scheduler.stop_cyclic_loop()


# Global service instance
service: Optional[WatermeterService] = None


def get_service() -> WatermeterService:
    """Get or create the global service instance."""
    global service
    if service is None:
        service = WatermeterService()
    return service
