"""Failure-only sliding-window throttle for login attempts (AUTH-11).

Counts only failed password or token checks, per client key, over three windows (the Frigate numbers). Everything
lives in memory: nothing is written to disk, so the owner can never be locked out permanently and a restart clears it.
"""

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable


class FailureLimiter:
    """Sliding windows of failure timestamps per key, bounded in keys and in entries per key.

    Args:
        windows: ``(seconds, max_failures)`` pairs. A key is blocked while any window holds ``max_failures`` or more.
        max_keys: Most keys remembered. The key whose latest failure is oldest is dropped first.
        clock: Monotonic time source; injected by the tests.
    """

    DEFAULT_WINDOWS = ((1, 1), (60, 5), (3600, 20))

    def __init__(
        self,
        windows: Iterable[tuple[int, int]] = DEFAULT_WINDOWS,
        max_keys: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._windows = tuple(windows)
        self._horizon = max(seconds for seconds, _ in self._windows)
        self._keep = max(limit for _, limit in self._windows)
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def retry_after(self, key: str) -> int:
        """Whole seconds to wait before ``key`` may try again; 0 when it is not throttled."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            blocked = False
            wait = 0.0
            for seconds, limit in self._windows:
                recent = [stamp for stamp in hits if now - stamp < seconds]
                if len(recent) >= limit:
                    blocked = True
                    # The window frees up when its limit-th newest failure turns seconds old.
                    wait = max(wait, recent[-limit] + seconds - now)
        return max(1, math.ceil(wait)) if blocked else 0

    def record_failure(self, key: str) -> None:
        """Add one failure for ``key`` (identical failures count separately)."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque(maxlen=self._keep)
                self._hits[key] = hits
            hits.append(now)
            while hits and now - hits[0] >= self._horizon:
                hits.popleft()
            self._hits.move_to_end(key)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)

    def reset(self, key: str) -> None:
        """Forget the failures of ``key`` (after a successful login). Unknown keys are ignored."""
        with self._lock:
            self._hits.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)
