"""Plausibility checking for watermeter readings.

Extracted from WatermeterService.check_consistency() and
WatermeterService.validate_plausibility().
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .position_utils import get_position_ids
from .rate_tracker import RateTracker
from .reanchor import ReanchorTracker

logger = logging.getLogger(__name__)


@dataclass
class PlausibilityResult:
    """Outcome of ``PlausibilityChecker.evaluate``.

    ``baseline`` is the value to adopt as the new previous value when ``is_valid``:
    the new reading, the held previous value (jitter), or the re-anchored reading.
    """

    is_valid: bool
    warnings: List[str] = field(default_factory=list)
    baseline: Optional[float] = None
    reanchored: bool = False


class PlausibilityChecker:
    """Validates meter readings for consistency and plausibility.

    Args:
        config: Full application config dict.
        rate_tracker: Shared RateTracker instance for rate-based checks.
    """

    def __init__(self, config: dict, rate_tracker: RateTracker) -> None:
        self.config = config
        self._rate_tracker = rate_tracker
        self._reanchor: Optional[ReanchorTracker] = None
        self._reanchor_params: Optional[Tuple[int, float, float]] = None

    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """Check consistency between adjacent positions.

        A position with fractional part >= 0.4 (representing .5) should have
        the next position's integer part >= 5 (upper half of dial).

        Args:
            predictions: Dict of prediction results keyed by position ID.

        Returns:
            List of warning messages.
        """
        warnings = []

        if not self.config["plausibility"].get("enable_consistency_check", True):
            return warnings

        digit_ids, arrow_ids = get_position_ids(self.config)
        all_ids = digit_ids + arrow_ids

        all_values = []
        for image_id in all_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] not in ["NAN", "ERROR"]:
                    if pred["model"] == "digits":
                        all_values.append((image_id, int(pred["class"])))
                    else:
                        all_values.append((image_id, float(pred["class"])))

        for i in range(len(all_values) - 1):
            current_id, current_val = all_values[i]
            next_id, next_val = all_values[i + 1]

            current_frac = current_val % 1
            current_has_half = current_frac >= 0.4

            next_int_part = int(next_val)
            next_is_upper_half = next_int_part >= 5

            if current_has_half != next_is_upper_half:
                msg = f"{current_id}={current_val} (half={current_has_half}) vs {next_id}={next_val} (upper={next_is_upper_half})"
                warnings.append(msg)
                logger.warning(f"Consistency check: {msg}")

        return warnings

    def validate_plausibility(
        self,
        new_value: float,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
    ) -> Tuple[bool, List[str]]:
        """Backward-compatible wrapper around :meth:`evaluate`."""
        result = self.evaluate(new_value, previous_value, last_update_time)
        return result.is_valid, result.warnings

    def reset_reanchor(self) -> None:
        """Forget any collected re-anchor candidates (on accept, reset, manual set)."""
        if self._reanchor is not None:
            self._reanchor.reset()

    def _get_reanchor_tracker(self, config: dict, tolerance: float) -> Optional[ReanchorTracker]:
        required = int(config.get("reanchor_after", 6))
        if required <= 0:
            return None
        params = (required, float(config.get("reanchor_max_spread", 0.01)), tolerance)
        if self._reanchor is None or self._reanchor_params != params:
            self._reanchor = ReanchorTracker(required=params[0], max_spread=params[1], tolerance=params[2])
            self._reanchor_params = params
        return self._reanchor

    def evaluate(
        self,
        new_value: float,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
    ) -> PlausibilityResult:
        """Validate a new reading; return validity, warnings and the baseline to adopt."""
        warnings: List[str] = []
        config = self.config["plausibility"]

        if previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._rate_tracker.add(new_value)
            return PlausibilityResult(True, warnings, baseline=new_value)

        tolerance = float(config.get("reverse_tolerance", 0.002))

        # Reverse detection with jitter band and re-anchoring
        if config["enable_reverse_detection"] and new_value < previous_value:
            if new_value >= previous_value - tolerance:
                msg = f"Minor reverse (jitter): {previous_value:.4f} → {new_value:.4f}, holding previous value"
                warnings.append(msg)
                logger.info(msg)
                self.reset_reanchor()
                return PlausibilityResult(True, warnings, baseline=previous_value)

            tracker = self._get_reanchor_tracker(config, tolerance)
            if tracker is not None and tracker.add(new_value):
                msg = (
                    f"Re-anchoring baseline {previous_value:.4f} → {new_value:.4f} "
                    f"after {tracker.required} consistent readings"
                )
                logger.warning(msg)
                tracker.reset()
                self._rate_tracker.reset()
                return PlausibilityResult(True, [msg], baseline=new_value, reanchored=True)

            msg = f"Reverse detected: {previous_value:.4f} → {new_value:.4f}"
            warnings.append(msg)
            logger.error(msg)
            return PlausibilityResult(False, warnings)

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                self.reset_reanchor()
                return PlausibilityResult(False, warnings)

            # Rate per hour - check against history if available
            if last_update_time:
                time_diff = (datetime.now() - last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config["max_rate_per_hour"]:
                        if len(self._rate_tracker) >= 2:
                            avg_rate = self._rate_tracker.average_rate_per_hour
                            if avg_rate is not None and avg_rate > config["max_rate_per_hour"]:
                                msg = f"Rate per hour too high: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.error(msg)
                                self.reset_reanchor()
                                return PlausibilityResult(False, warnings)
                            else:
                                msg = f"Rate spike: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        self.reset_reanchor()
        return PlausibilityResult(True, warnings, baseline=new_value)
