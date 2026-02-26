"""Confirmation flow manager for BL-07: User confirmation via Home Assistant.

Encapsulates the 8 confirmation-related methods that were previously on
WatermeterService. Methods that need MQTT and/or the asyncio event loop
receive them as call-time parameters (not stored on self).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ConfirmationManager:
    """Manages user-confirmation state for uncertain meter readings (BL-07).

    Args:
        config: The full service config dict.
        rate_tracker: RateTracker instance for rate-history mutations.
        meter_state: MeterState instance for reading/writing shared state.
        state_store: Optional StateStore for persistence.
        logger: Optional logger; falls back to module-level logger.
    """

    def __init__(
        self,
        config: dict,
        rate_tracker,
        meter_state,
        state_store=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self._rate_tracker = rate_tracker
        self._meter_state = meter_state
        self._state_store = state_store
        self._logger = logger or logging.getLogger(__name__)

        self._pending_confirmation: Optional[Dict] = None
        self._confirmation_timer: Optional[threading.Timer] = None

    # ── Configuration ──────────────────────────────────────────────────────

    def get_config(self) -> Dict:
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

    # ── Trigger evaluation ─────────────────────────────────────────────────

    def should_request(
        self,
        total_value: float,
        warnings: List[str],
        predictions: Dict[str, Dict],
    ) -> Optional[str]:
        """Decide whether a reading needs user confirmation.

        Returns a reason string if confirmation is needed, None otherwise.
        """
        conf = self.get_config()
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
        low_conf_count = sum(
            1 for pred in predictions.values() if pred["confidence"] < threshold
        )
        if low_conf_count >= conf["min_low_confidence_positions"]:
            return f"{low_conf_count} position(s) below confidence threshold"

        # Condition 3: rate jump relative to average
        state = self._meter_state
        if state.previous_value is not None and state.last_update_time is not None:
            avg_rate = self._rate_tracker.average_rate_per_hour
            if avg_rate is not None and avg_rate > 0:
                time_diff = (datetime.now() - state.last_update_time).total_seconds()
                if time_diff > 0:
                    current_rate = (
                        (total_value - state.previous_value) / time_diff
                    ) * 3600
                    if current_rate > avg_rate * conf["max_rate_jump_factor"]:
                        return (
                            f"Rate jump: {current_rate:.3f} m\u00b3/h "
                            f"exceeds {conf['max_rate_jump_factor']}x average "
                            f"({avg_rate:.3f})"
                        )

        return None

    # ── Publishing ─────────────────────────────────────────────────────────

    def publish_request(
        self,
        total_value: float,
        warnings: List[str],
        predictions: Dict[str, Dict],
        reason: str,
        mqtt_client,
        loop,
    ) -> None:
        """Publish a confirmation request to MQTT and start the timeout timer.

        Stores the reading details in _pending_confirmation so they can be
        committed or discarded when the user responds.

        Args:
            mqtt_client: Connected paho MQTT client (or None).
            loop: asyncio event loop (or None) — used for timer routing.
        """
        conf = self.get_config()

        if not mqtt_client or not mqtt_client.is_connected():
            self._logger.warning(
                "MQTT not connected -- cannot request confirmation, auto-accepting reading"
            )
            # Clear pending state since we can't actually request confirmation
            self._pending_confirmation = None
            self._meter_state.current_state["status"] = "warning" if warnings else "ok"
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

        mqtt_client.publish(
            conf["request_topic"],
            json.dumps(payload),
            qos=1,
        )
        self._logger.info(f"Published confirmation request: {reason}")

        # Start timeout timer
        self.cancel_timer()
        timeout_seconds = conf["timeout_minutes"] * 60
        self._confirmation_timer = threading.Timer(
            timeout_seconds,
            self._timeout_sync,
            args=(loop,),
        )
        self._confirmation_timer.daemon = True
        self._confirmation_timer.start()
        self._logger.info(f"Confirmation timeout set: {conf['timeout_minutes']} min")

    # ── Timer management ───────────────────────────────────────────────────

    def cancel_timer(self) -> None:
        """Cancel the running confirmation timeout timer, if any."""
        if self._confirmation_timer is not None:
            self._confirmation_timer.cancel()
            self._confirmation_timer = None

    def _timeout_sync(self, loop) -> None:
        """Called by Timer thread -- routes to event loop for thread safety."""
        if loop:
            loop.call_soon_threadsafe(self._do_timeout, loop)
        else:
            self._do_timeout(loop)

    def _do_timeout(self, loop) -> None:
        """Actually process the timeout. Runs on the event loop thread."""
        pending = self._pending_confirmation
        if pending is None:
            return

        self._logger.warning("Confirmation timeout -- auto-rejecting reading")

        state = self._meter_state

        # Revert state to before the pending reading was applied
        state.previous_value = pending["previous_value_before"]
        state.last_update_time = pending["last_update_time_before"]

        # Persist the reverted state
        if self._state_store:
            if state.previous_value is not None and state.last_update_time is not None:
                self._state_store.save(state.previous_value, state.last_update_time)
            else:
                self._state_store.clear()

        # Remove the last entry from rate_history (it was added optimistically)
        self._rate_tracker.pop_last()

        self._pending_confirmation = None
        self._confirmation_timer = None
        state.current_state["status"] = "timeout"
        state.current_state["warnings"] = [
            "Confirmation timed out -- reading discarded"
        ]

        # Publish diagnostic state update so HA reflects the timeout
        if state.previous_value is not None and loop:
            from . import watermeter_service as _svc_mod

            # We need publish_to_mqtt — get it via the service singleton
            svc = _svc_mod.get_service()
            if svc is not None:
                raw_total = self._compute_raw_total(pending["raw_values"]) if pending.get("raw_values") else None
                asyncio.run_coroutine_threadsafe(
                    svc.publish_to_mqtt(
                        state.previous_value,
                        state.current_state["warnings"],
                        {},
                        leak_warning=state.leak_warning,
                        raw_value=raw_total,
                    ),
                    loop,
                )

        self._logger.info("Pending confirmation cleared after timeout")

    # ── Response handling ──────────────────────────────────────────────────

    def handle_response(self, payload: str, loop, publish_fn) -> None:
        """Process a user response from the confirmation response MQTT topic.

        Payloads:
            - "confirm"          -- accept the pending reading as-is
            - "reject"           -- discard the pending reading
            - "correct:{value}"  -- accept with an overridden value

        Args:
            loop: asyncio event loop for scheduling publish coroutines.
            publish_fn: Coroutine function ``publish_to_mqtt(value, warnings,
                predictions, *, leak_warning, raw_value)`` called on confirm /
                correct paths.
        """
        pending = self._pending_confirmation
        if pending is None:
            self._logger.warning(
                "Received confirmation response but nothing is pending -- ignoring"
            )
            return

        self.cancel_timer()
        payload = payload.strip()
        state = self._meter_state

        if payload == "confirm":
            self._logger.info("User confirmed the reading")
            # Reading was already optimistically applied -- just publish to HA
            raw_total = (
                self._compute_raw_total(pending["raw_values"])
                if pending.get("raw_values")
                else None
            )
            if loop:
                asyncio.run_coroutine_threadsafe(
                    publish_fn(
                        pending["value"],
                        pending["warnings"],
                        pending["predictions"],
                        leak_warning=state.leak_warning,
                        raw_value=raw_total,
                    ),
                    loop,
                )
            state.current_state["status"] = "ok"
            state.current_state["warnings"] = pending["warnings"]

        elif payload == "reject":
            self._logger.info("User rejected the reading")
            # Revert to pre-reading state
            state.previous_value = pending["previous_value_before"]
            state.last_update_time = pending["last_update_time_before"]
            if self._state_store:
                if (
                    state.previous_value is not None
                    and state.last_update_time is not None
                ):
                    self._state_store.save(
                        state.previous_value, state.last_update_time
                    )
                else:
                    self._state_store.clear()
            # Remove the optimistic rate_history entry
            self._rate_tracker.pop_last()
            state.current_state["status"] = "rejected"
            state.current_state["warnings"] = ["Reading rejected by user"]

            # Publish diagnostic state update so HA reflects the rejection
            if state.previous_value is not None and loop:
                raw_total = self._compute_raw_total(pending["raw_values"]) if pending.get("raw_values") else None
                asyncio.run_coroutine_threadsafe(
                    publish_fn(
                        state.previous_value,
                        state.current_state["warnings"],
                        {},
                        leak_warning=state.leak_warning,
                        raw_value=raw_total,
                    ),
                    loop,
                )

        elif payload.startswith("correct:"):
            corrected_str = payload[len("correct:"):]
            try:
                corrected_value = float(corrected_str)
            except ValueError:
                self._logger.error(f"Invalid corrected value: {corrected_str!r}")
                return

            self._logger.info(
                f"User corrected reading: {pending['value']:.4f} -> {corrected_value:.4f}"
            )

            # Apply the corrected value
            state.previous_value = corrected_value
            state.last_update_time = datetime.now()
            if self._state_store:
                self._state_store.save(state.previous_value, state.last_update_time)

            # Fix up rate_history: replace the last (optimistic) entry
            self._rate_tracker.replace_last(corrected_value, state.last_update_time)

            state.current_state["total_value"] = corrected_value
            state.current_state["last_update"] = state.last_update_time.isoformat()
            state.current_state["status"] = "ok"
            state.current_state["warnings"] = [
                f"User corrected: {pending['value']:.4f} -> {corrected_value:.4f}"
            ]

            # Publish the corrected value to HA
            if loop:
                asyncio.run_coroutine_threadsafe(
                    publish_fn(
                        corrected_value,
                        state.current_state["warnings"],
                        pending["predictions"],
                        leak_warning=state.leak_warning,
                        raw_value=corrected_value,
                    ),
                    loop,
                )

        else:
            self._logger.warning(f"Unknown confirmation response: {payload!r}")
            return

        self._pending_confirmation = None

    # ── Status query ───────────────────────────────────────────────────────

    def get_status(self) -> Optional[Dict]:
        """Return details of the pending confirmation, or None if nothing is pending.

        Used by the /api/confirmation/status endpoint.
        """
        pending = self._pending_confirmation
        if pending is None:
            return None

        conf = self.get_config()
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

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _compute_raw_total(raw_values: Dict) -> float:
        """Compute the unrounded total from raw digit and arrow values."""
        total = 0.0
        digits = raw_values.get("digits", [])
        arrows = raw_values.get("arrows", [])
        for i, digit in enumerate(digits):
            total += digit * (10 ** (len(digits) - 1 - i))
        for i, arrow in enumerate(arrows):
            total += arrow * (10 ** (-(i + 1)))
        return total
