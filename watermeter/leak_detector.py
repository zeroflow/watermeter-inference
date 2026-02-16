"""Leak detection via sustained consumption monitoring.

Extracted from WatermeterService._check_sustained_consumption().
Checks whether the last N consecutive readings all show a consumption
rate above a configurable threshold.
"""

import logging
from typing import Optional

from .rate_tracker import RateTracker

logger = logging.getLogger(__name__)


class LeakDetector:
    """Detects sustained consumption that may indicate a leak.

    Args:
        rate_tracker: Shared RateTracker instance for reading history.
        config: Full application config dict (reads plausibility section).
    """

    def __init__(self, rate_tracker: RateTracker, config: dict) -> None:
        self._rate_tracker = rate_tracker
        self.config = config

    def check(self) -> Optional[str]:
        """Check if the last N consecutive readings all show rate above threshold.

        Returns:
            Warning message string if sustained consumption detected, None otherwise.
        """
        plausibility_config = self.config["plausibility"]

        if not plausibility_config.get("enable_leak_detection", True):
            return None

        threshold = plausibility_config.get("sustained_rate_threshold", 0.05)
        min_readings = plausibility_config.get("sustained_rate_readings", 3)

        history = self._rate_tracker.history
        if len(history) < min_readings + 1:
            return None

        tail = history[-(min_readings + 1):]

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
            f"Sustained consumption: {avg_rate:.3f} m³/h over {total_time_min:.0f} min "
            f"({min_readings} consecutive readings above {threshold} m³/h)"
        )
