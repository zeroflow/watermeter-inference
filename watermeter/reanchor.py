"""Re-anchoring detector: recognises a run of consistent readings below a stuck baseline.

Pure logic, no I/O. The plausibility checker feeds it every reverse-rejected value;
once ``required`` consecutive values agree (small spread, non-decreasing within
``tolerance``) the baseline is considered wrong and may be moved to the new value.
"""

from typing import List


class ReanchorTracker:
    def __init__(self, required: int, max_spread: float, tolerance: float) -> None:
        self.required = required
        self.max_spread = max_spread
        self.tolerance = tolerance
        self.values: List[float] = []

    def add(self, value: float) -> bool:
        """Record a reverse-rejected value; return True once the run is long enough."""
        if self.values:
            candidate = self.values + [value]
            spread_ok = max(candidate) - min(candidate) <= self.max_spread
            monotonic_ok = value >= self.values[-1] - self.tolerance
            self.values = candidate if (spread_ok and monotonic_ok) else [value]
        else:
            self.values = [value]
        return len(self.values) >= self.required

    def reset(self) -> None:
        self.values = []
