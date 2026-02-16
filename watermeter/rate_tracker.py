"""Rate history ring buffer for watermeter plausibility and leak detection.

Encapsulates the list of (value, timestamp) tuples that was previously
spread across WatermeterService as `rate_history`, `rate_history_size`,
`_add_to_rate_history()`, and `_calculate_average_rate_per_hour()`.
"""

from datetime import datetime
from typing import List, Optional, Tuple


class RateTracker:
    """Fixed-size ring buffer of (value, timestamp) meter readings.

    Provides explicit mutation methods so callers cannot accidentally
    corrupt the history list.

    Args:
        max_size: Maximum number of entries to retain. Oldest entries
            are dropped when the buffer is full.  Defaults to 5.
    """

    def __init__(self, max_size: int = 5) -> None:
        self._history: List[Tuple[float, datetime]] = []
        self._max_size = max_size

    # -- Properties ----------------------------------------------------------

    @property
    def history(self) -> List[Tuple[float, datetime]]:
        """Return a shallow copy of the history list (read-only view)."""
        return list(self._history)

    @property
    def max_size(self) -> int:
        """Maximum number of entries retained."""
        return self._max_size

    @max_size.setter
    def max_size(self, value: int) -> None:
        self._max_size = value
        self._trim()

    @property
    def average_rate_per_hour(self) -> Optional[float]:
        """Calculate average rate per hour from oldest to newest entry.

        Returns None if fewer than 2 entries or zero time span.
        """
        if len(self._history) < 2:
            return None

        oldest_value, oldest_time = self._history[0]
        newest_value, newest_time = self._history[-1]

        time_diff = (newest_time - oldest_time).total_seconds()
        if time_diff <= 0:
            return None

        value_diff = newest_value - oldest_value
        return (value_diff / time_diff) * 3600

    # -- Mutation API --------------------------------------------------------

    def add(self, value: float, timestamp: Optional[datetime] = None) -> None:
        """Append a reading and trim to max_size.

        Args:
            value: Meter reading value.
            timestamp: When the reading was taken.  Defaults to now.
        """
        if timestamp is None:
            timestamp = datetime.now()
        self._history.append((value, timestamp))
        self._trim()

    def pop_last(self) -> None:
        """Remove the most recent entry (e.g. on reject/timeout)."""
        if self._history:
            self._history.pop()

    def replace_last(self, value: float, timestamp: datetime) -> None:
        """Replace the most recent entry (e.g. on user correction)."""
        if self._history:
            self._history[-1] = (value, timestamp)

    def reset(self) -> None:
        """Clear all history entries."""
        self._history.clear()

    def seed(self, value: float, timestamp: datetime) -> None:
        """Clear history and start fresh with a single entry.

        Used by manual value set to establish a known baseline.
        """
        self._history.clear()
        self._history.append((value, timestamp))

    # -- Dunder methods ------------------------------------------------------

    def __len__(self) -> int:
        return len(self._history)

    def __repr__(self) -> str:
        return f"RateTracker(max_size={self._max_size}, entries={len(self._history)})"

    # -- Internal ------------------------------------------------------------

    def _trim(self) -> None:
        """Drop oldest entries if over max_size."""
        if len(self._history) > self._max_size:
            self._history = self._history[-self._max_size:]
