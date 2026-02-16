"""Plausibility checking for watermeter readings.

Extracted from WatermeterService.check_consistency() and
WatermeterService.validate_plausibility().
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .position_utils import get_position_ids
from .rate_tracker import RateTracker

logger = logging.getLogger(__name__)


class PlausibilityChecker:
    """Validates meter readings for consistency and plausibility.

    Args:
        config: Full application config dict.
        rate_tracker: Shared RateTracker instance for rate-based checks.
    """

    def __init__(self, config: dict, rate_tracker: RateTracker) -> None:
        self.config = config
        self._rate_tracker = rate_tracker

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
        """Validate plausibility of a new reading.

        Args:
            new_value: New meter reading.
            previous_value: Last accepted meter reading, or None.
            last_update_time: Timestamp of last accepted reading, or None.

        Returns:
            (is_valid, warnings) tuple.
        """
        warnings = []
        config = self.config["plausibility"]

        if previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._rate_tracker.add(new_value)
            return True, warnings

        # Reverse detection
        if config["enable_reverse_detection"]:
            if new_value < previous_value:
                msg = f"Reverse detected: {previous_value:.4f} → {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

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
                                return False, warnings
                            else:
                                msg = f"Rate spike: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        return True, warnings
