"""
Water Meter AI Service
Combines image fetching, OpenVINO inference, consistency checks, and MQTT publishing
"""

import asyncio
import logging
import logging.handlers
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import base64

import cv2
import numpy as np
import httpx
import yaml
from .inference import get_inference_service
from .persistence import StateStore, FailureStore
from .image_pipeline import AlignmentResult, ImagePipeline
from .position_utils import calculate_total as _calculate_total_impl, get_position_ids
from .low_confidence_capture import LowConfidenceCapture
from .scheduling import SchedulingManager
from .rate_tracker import RateTracker
from .leak_detector import LeakDetector
from .plausibility import PlausibilityChecker
from .meter_state import MeterState
from .confirmation import ConfirmationManager
from .mqtt_publisher import MqttPublisher
from .correction import CorrectionEngine
from .data_collector import DataCollector

logger = logging.getLogger(__name__)


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

        # Configure file logging if specified in config
        log_file = self.config["logging"].get("file")
        if log_file:
            log_format = self.config["logging"].get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
            file_handler.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(file_handler)

        # State management — grouped in MeterState data container
        ha_enabled: bool = self.config["homeassistant"]["enabled"]
        self._state = MeterState(ha_publish_enabled=ha_enabled)

        # Persistence
        persistence_config = self.config.get("persistence", {})
        if persistence_config.get("enabled", False):
            self.state_store = StateStore(persistence_config["state_file"])
            # Load previous state
            self._state.previous_value, self._state.last_update_time = self.state_store.load()
            # Populate last_published from persisted state (BL-14)
            if self._state.previous_value is not None:
                self._state.current_state["last_published_value"] = self._state.previous_value
                self._state.current_state["last_published_timestamp"] = (
                    self._state.last_update_time.strftime("%H:%M") if self._state.last_update_time else None
                )
                # Full ISO timestamp for age computation in _derive_pipeline_status (Issue #2)
                self._state.current_state["last_published_iso"] = (
                    self._state.last_update_time.isoformat() if self._state.last_update_time else None
                )
        else:
            self.state_store = None
            logger.info("Persistence disabled")

        # Failure history (ring buffer of last N pipeline failures).
        # Co-located with the state file so /data is the single persistence root.
        state_file_path = persistence_config.get("state_file", "/data/state.json")
        failure_history_path = Path(state_file_path).parent / "failure_history.json"
        self._failure_store = FailureStore(failure_history_path)

        # Consecutive alignment failures (separate from consecutive_rejections,
        # which counts plausibility-rejected readings).  Initialized to 0;
        # increments on every fail-closed alignment, resets on success.
        self.consecutive_alignment_failures = 0
        # Edge-trigger latch for the STALE-pipeline MQTT notification.  Set to
        # True the first time the failure counter crosses the configured
        # threshold; cleared on the next successful alignment so a future STALE
        # episode triggers a fresh notification.
        self._stale_notified = False

        # Low confidence capture
        self._low_confidence = LowConfidenceCapture(self.config)

        # Data collection mode
        dc_config = self.config.get("inference", {}).get("data_collection", {})
        if dc_config.get("enabled", False):
            save_path = self.config["low_confidence"]["save_path"]
            state_file = persistence_config.get("state_file", "/data/state.json")
            data_dir = str(Path(state_file).parent)
            self._data_collector = DataCollector(dc_config, save_path=save_path, data_dir=data_dir)
            logger.info("Data collection mode enabled (quota: %d per class)", dc_config["quota_per_class"])
        else:
            self._data_collector = None

        # Rate history for plausibility checks
        self._rate_tracker = RateTracker(max_size=self.config["plausibility"].get("rate_history_size", 5))

        # Leak detection
        self._leak_detector = LeakDetector(
            rate_tracker=self._rate_tracker,
            config=self.config,
        )

        # Plausibility checking
        self._plausibility_checker = PlausibilityChecker(
            config=self.config,
            rate_tracker=self._rate_tracker,
        )

        # consecutive_rejections, max_consecutive_rejections, and leak_warning
        # are stored in self._state (MeterState); see @property forwarding below.

        # Trigger mode
        trigger_config = self.config.get("trigger", {})
        self.trigger_mode = trigger_config.get("mode", "mqtt")
        self.cyclic_interval = trigger_config.get("cyclic_interval", 300)

        # Async lock for processing
        self.processing_lock = asyncio.Lock()

        # Image pipeline (fetching, rotation, alignment, ROI extraction)
        self._image_pipeline = ImagePipeline(self.config)

        # Scheduling manager (cyclic loop and stats publishing)
        self._scheduler = SchedulingManager(
            cyclic_interval=self.cyclic_interval,
            process_fn=self.process_reading,
            stats_fn=self.publish_training_stats,
        )

        # Confirmation manager (BL-07)
        self._confirmation_manager = ConfirmationManager(
            config=self.config,
            rate_tracker=self._rate_tracker,
            meter_state=self._state,
            state_store=self.state_store,
        )

        # MQTT publisher (owns mqtt_client, loop, HA discovery)
        self._mqtt = MqttPublisher(
            config=self.config,
            meter_state=self._state,
            rate_tracker=self._rate_tracker,
            confirmation_manager=self._confirmation_manager,
            on_trigger=self.process_reading,
            on_reset=self.reset_previous_value,
            get_active_model_fn=self._get_active_model_name,
            get_inference_duration_fn=lambda: self._last_inference_duration_ms,
            get_processing_duration_fn=lambda: self._last_processing_duration_s,
        )

        # Correction engine (BL-04)
        self._correction = CorrectionEngine(
            config=self.config,
            rate_tracker=self._rate_tracker,
            meter_state=self._state,
        )

        # Timing instrumentation (populated by process_reading, published via MQTT)
        self._last_inference_duration_ms: Optional[int] = None
        self._last_processing_duration_s: Optional[float] = None

        # Raw-image archive retention sweep state (see _archive_raw_image).
        # Tracks the date of the last sweep so we only walk the archive dir once per day.
        self._last_sweep_date = None

        logger.info(f"WatermeterService initialized (trigger_mode={self.trigger_mode})")

    # ── MeterState forwarding properties ────────────────────────────────────
    # These bridge the gap left by d2a0705 which moved state fields into
    # self._state (MeterState) without adding @property forwarding on the
    # service itself.  All external call sites and internal methods that
    # reference self.xxx continue to work through these properties.

    @property
    def current_state(self) -> Dict:
        """Getter-only: dict is mutated in-place, never replaced."""
        return self._state.current_state

    @current_state.setter
    def current_state(self, value: Dict) -> None:
        self._state.current_state = value

    @property
    def previous_value(self) -> Optional[float]:
        return self._state.previous_value

    @previous_value.setter
    def previous_value(self, value: Optional[float]) -> None:
        self._state.previous_value = value

    @property
    def last_update_time(self):
        return self._state.last_update_time

    @last_update_time.setter
    def last_update_time(self, value) -> None:
        self._state.last_update_time = value

    @property
    def consecutive_rejections(self) -> int:
        return self._state.consecutive_rejections

    @consecutive_rejections.setter
    def consecutive_rejections(self, value: int) -> None:
        self._state.consecutive_rejections = value

    @property
    def max_consecutive_rejections(self) -> int:
        """Read-only: set once in MeterState.__init__, never reassigned."""
        return self._state.max_consecutive_rejections

    @property
    def leak_warning(self) -> bool:
        return self._state.leak_warning

    @leak_warning.setter
    def leak_warning(self, value: bool) -> None:
        self._state.leak_warning = value

    @property
    def ha_publish_enabled(self) -> bool:
        return self._state.ha_publish_enabled

    @ha_publish_enabled.setter
    def ha_publish_enabled(self, value: bool) -> None:
        self._state.ha_publish_enabled = value

    @property
    def state_store(self):
        return self._state_store

    @state_store.setter
    def state_store(self, value) -> None:
        self._state_store = value
        if hasattr(self, "_confirmation_manager"):
            self._confirmation_manager._state_store = value

    # ── MQTT delegation properties ──────────────────────────────────────────

    @property
    def mqtt_client(self):
        return self._mqtt.mqtt_client

    @mqtt_client.setter
    def mqtt_client(self, value):
        self._mqtt.mqtt_client = value

    @property
    def loop(self):
        return self._mqtt.loop

    @loop.setter
    def loop(self, value):
        self._mqtt.loop = value

    # ── rate_history backward-compat property ───────────────────────────────

    @property
    def rate_history(self):
        """Backward-compat view of _rate_tracker._history (mutable list)."""
        return self._rate_tracker._history

    @rate_history.setter
    def rate_history(self, value):
        """Allow tests to assign a list directly to rate_history."""
        self._rate_tracker._history = list(value)

    # ── MQTT message/connect delegation ─────────────────────────────────────

    def on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties):
        """Delegate MQTT connect handling to MqttPublisher."""
        self._mqtt.on_connect(client, userdata, connect_flags, reason_code, properties)

    def on_mqtt_message(self, client, userdata, msg):
        """Delegate MQTT message handling to MqttPublisher."""
        self._mqtt.on_message(client, userdata, msg)

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

    def process_whole_image(self, image_bytes: bytes) -> Tuple[Optional[Dict[str, Tuple[bytes, str]]], AlignmentResult]:
        """Process whole image: rotation, marker alignment, ROI extraction.

        Returns ``(images_dict_or_None, AlignmentResult)``. On alignment failure
        the images dict is ``None`` and callers MUST short-circuit before
        running inference.
        """
        self._image_pipeline.config = self.config
        return self._image_pipeline.process_whole_image(image_bytes)

    def invalidate_marker_cache(self):
        """Clear cached marker templates so they are reloaded on next alignment."""
        self._image_pipeline.invalidate_marker_cache()

    def _archive_raw_image(self, image_bytes: bytes) -> None:
        """Persist a raw fetched image for offline confidence analysis. Best-effort, never raises.

        After writing, runs a max-age sweep over the archive directory at most once
        per day to delete dated subdirs older than ``alignment.archive_max_age_days``
        (default 7). Sweep failures are logged but never propagate.
        """
        try:
            archive_dir = Path(self.config.get("alignment", {}).get("archive_dir", "/data/raw_archive"))
            now = datetime.now()
            day_dir = archive_dir / now.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            path = day_dir / f"{now.strftime('%H%M%S_%f')}.jpg"
            path.write_bytes(image_bytes)
        except Exception as e:
            logger.warning(f"Failed to archive raw image: {e}")
            return

        # Best-effort retention sweep — only runs on the first archive write of a new day.
        # Any error here must not affect the archive write or the caller.
        today = now.date()
        if self._last_sweep_date is None or self._last_sweep_date != today:
            try:
                self._sweep_old_archive_dirs(archive_dir)
            except Exception as e:
                logger.warning(f"Archive retention sweep failed: {e}")
            # Mark sweep attempted regardless of success — avoids re-walking on every call after a failure.
            self._last_sweep_date = today

    def _sweep_old_archive_dirs(self, archive_dir: Path) -> None:
        """Delete per-day subdirectories older than ``archive_max_age_days``.

        Subdirs whose name does not parse as ``%Y-%m-%d`` are left untouched so we
        never delete unknown content. Errors per-subdir are swallowed via
        ``shutil.rmtree(..., ignore_errors=True)``.
        """
        max_age_days = int(self.config.get("alignment", {}).get("archive_max_age_days", 7))
        cutoff = datetime.now().date() - timedelta(days=max_age_days)
        for sub in archive_dir.iterdir():
            if not sub.is_dir():
                continue
            try:
                sub_date = datetime.strptime(sub.name, "%Y-%m-%d").date()
            except ValueError:
                continue  # leave non-dated subdirs alone
            if sub_date < cutoff:
                shutil.rmtree(sub, ignore_errors=True)

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

        for image_id, (image_bytes, image_class) in images.items():
            # Run prediction via inference service (supports hot-reload)
            try:
                correction_config = self.config.get("correction", {})
                if correction_config.get("enabled", False):
                    top_k_count = correction_config.get("top_k", 3)
                    top_k_results = get_inference_service().predict_detailed_from_bytes(
                        image_class, image_bytes, top_k=top_k_count
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
                    result = get_inference_service().predict_from_bytes(image_class, image_bytes)
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

        logger.info(f"Inference completed for {len(predictions)} images")
        return predictions

    def calculate_total(self, predictions: Dict[str, Dict]) -> Tuple[float, Dict]:
        """
        Calculate total water meter reading from predictions.

        Delegates to the shared standalone implementation in position_utils.

        Args:
            predictions: Dict of prediction results

        Returns:
            (total_value, raw_values_dict)
        """
        return _calculate_total_impl(self.config, predictions)

    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """Check consistency between adjacent positions."""
        return self._plausibility_checker.check_consistency(predictions)

    def validate_plausibility(self, new_value: float) -> Tuple[bool, List[str]]:
        """Validate plausibility of new reading."""
        return self._plausibility_checker.validate_plausibility(
            new_value=new_value,
            previous_value=self.previous_value,
            last_update_time=self.last_update_time,
        )

    def _check_sustained_consumption(self) -> Optional[str]:
        """Check if the last N consecutive readings all show rate above threshold."""
        return self._leak_detector.check()

    # ── User Confirmation (BL-07) — delegated to ConfirmationManager ───────

    @property
    def _pending_confirmation(self) -> Optional[Dict]:
        return self._confirmation_manager._pending_confirmation

    @_pending_confirmation.setter
    def _pending_confirmation(self, value: Optional[Dict]) -> None:
        self._confirmation_manager._pending_confirmation = value

    @property
    def _confirmation_timer(self) -> Optional[threading.Timer]:
        return self._confirmation_manager._confirmation_timer

    @_confirmation_timer.setter
    def _confirmation_timer(self, value: Optional[threading.Timer]) -> None:
        self._confirmation_manager._confirmation_timer = value

    def _get_confirmation_config(self) -> Dict:
        """Delegate to ConfirmationManager.get_config()."""
        return self._confirmation_manager.get_config()

    def _should_request_confirmation(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict]
    ) -> Optional[str]:
        """Delegate to ConfirmationManager.should_request()."""
        return self._confirmation_manager.should_request(total_value, warnings, predictions)

    def _publish_confirmation_request(
        self, total_value: float, warnings: List[str], predictions: Dict[str, Dict], reason: str
    ) -> None:
        """Delegate to ConfirmationManager.publish_request()."""
        self._confirmation_manager.publish_request(
            total_value,
            warnings,
            predictions,
            reason,
            mqtt_client=self.mqtt_client,
            loop=self.loop,
        )

    def _cancel_confirmation_timer(self) -> None:
        """Delegate to ConfirmationManager.cancel_timer()."""
        self._confirmation_manager.cancel_timer()

    def _confirmation_timeout(self) -> None:
        """Route the timeout to the event loop, or execute directly if no loop."""
        if self.loop:
            self.loop.call_soon_threadsafe(self._do_confirmation_timeout)
        else:
            self._do_confirmation_timeout()

    def _do_confirmation_timeout(self) -> None:
        """Delegate to ConfirmationManager._do_timeout()."""
        self._confirmation_manager._do_timeout(self.loop)

    def _handle_confirmation_response(self, payload: str) -> None:
        """Delegate to ConfirmationManager.handle_response()."""
        self._confirmation_manager.handle_response(
            payload,
            loop=self.loop,
            publish_fn=self.publish_to_mqtt,
        )

    def get_confirmation_status(self) -> Optional[Dict]:
        """Delegate to ConfirmationManager.get_status()."""
        return self._confirmation_manager.get_status()

    # ── Value Correction Engine (BL-04) — delegates to CorrectionEngine ────

    def correct_predictions(self, predictions: Dict[str, Dict], raw_total: float, raw_values: Dict) -> List[str]:
        """Delegate to CorrectionEngine."""
        return self._correction.correct_predictions(predictions, raw_total, raw_values)

    async def save_low_confidence(
        self, image_id: str, image_bytes: bytes, prediction: Dict, next_image_bytes: bytes = None
    ) -> None:
        """Delegate to LowConfidenceCapture."""
        return await self._low_confidence.save_low_confidence(image_id, image_bytes, prediction, next_image_bytes)

    def _notify_stale(self) -> None:
        """One-shot notification when pipeline transitions into STALE (Task 4).

        Publishes a JSON payload to a dedicated HA event topic.  HA automations
        can forward this to push notifications, email, etc.  Best-effort: never
        raises — notification failure must not block reading processing.

        The latch (``self._stale_notified``) is managed by the caller so this
        helper stays idempotent if invoked directly.
        """
        try:
            if self.mqtt_client is None:
                logger.error(
                    f"STALE pipeline (no MQTT): consecutive_alignment_failures="
                    f"{self.consecutive_alignment_failures}"
                )
                return
            ha_cfg = self.config.get("homeassistant", {})
            topic = ha_cfg.get("event_topic", f"{ha_cfg.get('publish_topic', 'watermeter')}/event")
            payload = {
                "event": "pipeline_stale",
                "consecutive_alignment_failures": self.consecutive_alignment_failures,
                "last_alignment_error": self.current_state.get("last_alignment_error"),
                "timestamp": datetime.now().isoformat(),
            }
            self.mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=False)
            logger.info(f"Pipeline STALE notification published to {topic}")
        except Exception as e:
            logger.error(f"Failed to publish STALE notification: {e}")

    def _derive_pipeline_status(self) -> None:
        """Compute pipeline_status, last_valid_reading_age_seconds, and is_live.

        Mutates self.current_state in-place. Idempotent — safe to call multiple
        times in the same processing cycle.

        Status policy (Issue #2):
          - STALE     — consecutive_alignment_failures >= max_consecutive_failures
          - FAILED    — most recent status is alignment_failed or inference_failed
          - DEGRADED  — at least one consecutive failure but below threshold
          - OK        — no failures and last status is healthy

        is_live is True only when pipeline_status == "OK".
        """
        # Task 4 will add alignment.max_consecutive_failures to config.yaml.
        # Default to 10 here so this helper is usable before Task 4 lands.
        max_failures = self.config.get("alignment", {}).get("max_consecutive_failures", 10)
        consecutive = self.consecutive_alignment_failures
        last_status = self.current_state.get("status")

        if consecutive >= max_failures:
            pipeline_status = "STALE"
        elif last_status in ("alignment_failed", "inference_failed", "error"):
            # 'error' is the catch-all status set by process_reading's outer
            # exception handler — same intent as the structured failure modes,
            # so it must surface as FAILED to keep the badge consistent with
            # the inner status pill.
            pipeline_status = "FAILED"
        elif consecutive > 0:
            pipeline_status = "DEGRADED"
        else:
            pipeline_status = "OK"

        # Level-tie the STALE-notified latch to the derived status.
        # If we're no longer STALE (recovered, or threshold was raised), re-arm
        # the latch so the next STALE crossing produces a fresh notification.
        if pipeline_status != "STALE":
            self._stale_notified = False

        self.current_state["pipeline_status"] = pipeline_status
        self.current_state["consecutive_alignment_failures"] = consecutive

        # Age of last successful reading. Read the full ISO timestamp from
        # last_published_iso (the HH:MM string in last_published_timestamp
        # has no date and can't be parsed as ISO — see Issue #2 fix).
        last_pub_iso = self.current_state.get("last_published_iso")
        age_seconds: Optional[int] = None
        if last_pub_iso:
            try:
                if isinstance(last_pub_iso, str):
                    ts = datetime.fromisoformat(last_pub_iso)
                else:
                    ts = last_pub_iso
                age_seconds = max(0, int((datetime.now() - ts).total_seconds()))
            except (ValueError, TypeError):
                age_seconds = None
        self.current_state["last_valid_reading_age_seconds"] = age_seconds

        self.current_state["is_live"] = pipeline_status == "OK"

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
                self.current_state["warnings"] = [
                    "No inference models loaded. Train or import models via the Training page."
                ]
                return self.current_state

            # If no digit ROIs are configured, skip processing (fresh install)
            digit_rois = self.config.get("detection", {}).get("digits", {}).get("rois", [])
            if not digit_rois:
                logger.info("Skipping reading -- no digit ROIs configured yet")
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
                    # Optional: archive raw image for offline confidence analysis
                    alignment_cfg = self.config.get("alignment", {})
                    if alignment_cfg.get("archive_raw_images", False):
                        await asyncio.get_running_loop().run_in_executor(None, self._archive_raw_image, whole_image)
                    images, alignment = await asyncio.get_running_loop().run_in_executor(
                        None, self.process_whole_image, whole_image
                    )
                    # Fail-closed: if alignment failed, persist + short-circuit before inference.
                    if not alignment.success:
                        self._failure_store.record_failure(
                            reason=alignment.error_reason,
                            stage="alignment",
                            failed_marker=alignment.failed_marker,
                            marker_confidences=alignment.marker_confidences,
                        )
                        self.consecutive_alignment_failures += 1
                        self.current_state["status"] = "alignment_failed"
                        self.current_state["last_alignment_error"] = alignment.error_reason
                        self.current_state["last_alignment_timestamp"] = datetime.now().isoformat()
                        logger.error(
                            f"Reading discarded: alignment failed ({alignment.error_reason}, "
                            f"failed_marker={alignment.failed_marker}, "
                            f"confidences={alignment.marker_confidences})"
                        )
                        self._derive_pipeline_status()

                        # Edge-trigger STALE notification: fire exactly once on
                        # the boundary, latched until alignment recovers (Task 4).
                        max_fail = self.config.get("alignment", {}).get("max_consecutive_failures", 10)
                        if self.consecutive_alignment_failures >= max_fail and not self._stale_notified:
                            self._notify_stale()
                            self._stale_notified = True
                        return self.current_state

                    # Reset counter and STALE latch only on alignment success.
                    self.consecutive_alignment_failures = 0
                    self._stale_notified = False

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
                    # Data collection (saves regardless of confidence)
                    if self._data_collector is not None:
                        self._data_collector.collect(
                            pred["model"],  # "digits" or "arrows"
                            pred["id"],  # e.g., "analog_1", "digit_2"
                            pred["class"],  # predicted class string
                            pred["image_bytes"],
                        )

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
                    self._rate_tracker.add(total_value)

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
                    # Full ISO timestamp for age computation (Issue #2)
                    self.current_state["last_published_iso"] = self.last_update_time.isoformat()
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
                            total_value,
                            all_warnings,
                            predictions,
                            leak_warning=self.leak_warning,
                            raw_value=raw_total,
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
                    # Use the last accepted value for water_usage but still
                    # publish the raw inference result in water_usage_raw.
                    if self.previous_value is not None:
                        raw_total = self._compute_raw_total(raw_values)
                        await self.publish_to_mqtt(
                            self.previous_value,
                            all_warnings,
                            predictions,
                            leak_warning=self.leak_warning,
                            raw_value=raw_total,
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
                # Derive pipeline_status / is_live so the dashboard reflects the
                # latest health on every cycle (Issue #2). Idempotent.
                self._derive_pipeline_status()

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
        avg_rate = self._rate_tracker.average_rate_per_hour

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
            # Pipeline-health fields (Task 4): keep dashboard + HA in lockstep.
            # NOTE: Mirror these three pipeline-health fields in
            # MqttPublisher.publish_to_mqtt. Both publish paths exist during the
            # publisher migration; remove this site once the migration completes.
            "pipeline_status": self.current_state.get("pipeline_status", "OK"),
            "consecutive_alignment_failures": self.consecutive_alignment_failures,
            "last_alignment_error": self.current_state.get("last_alignment_error"),
        }

        topic = ha_config["publish_topic"]
        self.mqtt_client.publish(topic, json.dumps(payload), qos=2, retain=True)
        logger.info(f"Published to MQTT: {topic}")

    def reset_previous_value(self) -> None:
        """Reset the previous value (for meter replacement or stuck state)."""
        logger.info("Resetting previous value, rate history, and rejection counter")
        self.previous_value = None
        self.last_update_time = None
        self._rate_tracker.reset()
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False

        # Clear last-published and last-rejected tracking (BL-14)
        self.current_state["last_published_value"] = None
        self.current_state["last_published_timestamp"] = None
        self.current_state["last_published_iso"] = None
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
        self._rate_tracker.seed(value, now)

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
        # Full ISO timestamp for age computation (Issue #2)
        self.current_state["last_published_iso"] = now.isoformat()
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []

        # Publish to MQTT via shared method (raw_value=value since no raw
        # predictions exist for a manual set)
        can_publish = self.ha_publish_enabled and self.mqtt_client and self.mqtt_client.is_connected()
        if can_publish:
            try:
                await self.publish_to_mqtt(
                    value,
                    [],
                    {},
                    leak_warning=False,
                    raw_value=value,
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

    async def _stats_loop(self) -> None:
        """Periodically publish training data stats to HA (every 5 minutes)."""
        return await self._scheduler._stats_loop()

    def start_stats_loop(self) -> None:
        """Start the periodic training stats background task."""
        return self._scheduler.start_stats_loop()

    def stop_stats_loop(self) -> None:
        """Cancel the periodic training stats background task."""
        return self._scheduler.stop_stats_loop()

    # ── MQTT delegation methods ─────────────────────────────────────────────

    def publish_discovery(self) -> None:
        """Delegate to MqttPublisher."""
        self._mqtt.publish_discovery()

    def start_mqtt(self):
        """Delegate to MqttPublisher."""
        self._mqtt.start()

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
        mqtt_changed = any(old_mqtt.get(k) != new_mqtt.get(k) for k in ("broker", "port", "username", "password"))

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
        self._rate_tracker.max_size = new_config.get("plausibility", {}).get("rate_history_size", 5)
        self.ha_publish_enabled = new_config.get("homeassistant", {}).get("enabled", True)

        # Sync config to leak detector
        self._leak_detector.config = new_config

        # Sync config to plausibility checker
        self._plausibility_checker.config = new_config

        # Sync cyclic interval to scheduler
        self._scheduler.cyclic_interval = self.cyclic_interval

        # Sync config to correction engine
        self._correction.config = new_config

        # Sync config to confirmation manager and MQTT publisher
        self._confirmation_manager.config = new_config
        self._mqtt.config = new_config

        # Reconnect MQTT if connection params changed
        if mqtt_changed and self.mqtt_client:
            logger.info("MQTT config changed — reconnecting")
            self.stop_mqtt()
            self.start_mqtt()

        logger.info("Config reloaded successfully")
        return {"config_updated": True, "mqtt_reconnected": mqtt_changed}

    def stop_mqtt(self):
        """Delegate to MqttPublisher."""
        self._mqtt.stop()

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
