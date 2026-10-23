"""Tests for the failure-only sliding-window login throttle."""

import ast
import importlib
import threading
from pathlib import Path

import pytest


class FakeClock:
    """Manually advanced clock."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(scope="module")
def ratelimit():
    """The module under test, imported lazily."""
    return importlib.import_module("watermeter.ratelimit")


@pytest.fixture
def clock():
    """Fake monotonic clock."""
    return FakeClock()


@pytest.fixture
def limiter(ratelimit, clock):
    """Limiter with the default Frigate windows and a fake clock."""
    return ratelimit.FailureLimiter(clock=clock)


def test_default_windows(ratelimit):
    """The documented numbers: 1 in 1 s, 5 in 60 s, 20 in 3600 s."""
    assert ratelimit.FailureLimiter.DEFAULT_WINDOWS == ((1, 1), (60, 5), (3600, 20))


def test_fresh_key_is_allowed(limiter):
    """A key without failures is never throttled."""
    assert limiter.retry_after("203.0.113.5") == 0


def test_one_failure_blocks_for_one_second(limiter, clock):
    """After one failure the key waits 1 s, then is allowed."""
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 1
    clock.advance(0.4)
    assert limiter.retry_after("a") == 1
    clock.advance(0.6)
    assert limiter.retry_after("a") == 0


def test_five_failures_in_a_minute_block_until_the_oldest_leaves_the_window(limiter, clock):
    """Failures 2 s apart trip the 60 s window; the wait ends when the oldest one is 60 s old."""
    for _ in range(5):
        limiter.record_failure("a")
        clock.advance(2)
    clock.advance(-1)  # 9 s after the first failure, 1 s after the last
    wait = limiter.retry_after("a")
    assert isinstance(wait, int)
    assert wait == 51
    clock.advance(50)
    assert limiter.retry_after("a") == 1
    clock.advance(1)
    assert limiter.retry_after("a") == 0


def test_four_failures_in_a_minute_do_not_trip_the_minute_window(limiter, clock):
    """Only the 1 s window applies below five failures."""
    for _ in range(4):
        limiter.record_failure("a")
        clock.advance(2)
    assert limiter.retry_after("a") == 0


def test_twenty_failures_in_an_hour_block_for_the_rest_of_the_hour(limiter, clock):
    """Failures 61 s apart stay under the minute limit but trip the hourly one."""
    start = clock.now
    for _ in range(20):
        limiter.record_failure("a")
        clock.advance(61)
    assert limiter.retry_after("a") == 3600 - 20 * 61
    clock.now = start + 3599
    assert limiter.retry_after("a") == 1
    clock.now = start + 3600
    assert limiter.retry_after("a") == 0


def test_wait_is_an_int_of_at_least_one_while_blocked(limiter, clock):
    """Fractional remaining waits round up."""
    limiter.record_failure("a")
    for step in (0.0, 0.25, 0.5, 0.99):
        clock.now = 1000.0 + step
        wait = limiter.retry_after("a")
        assert isinstance(wait, int) and wait >= 1


def test_identical_failures_each_count(ratelimit, clock):
    """Two failures at the same instant are two entries."""
    limiter = ratelimit.FailureLimiter(windows=((60, 2),), clock=clock)
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 60


def test_reset_clears_only_that_key(limiter):
    """A successful login for one client leaves the others throttled."""
    limiter.record_failure("a")
    limiter.record_failure("b")
    limiter.reset("a")
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("b") == 1


def test_reset_of_unknown_key_is_a_noop(limiter):
    """Resetting a key that never failed does not raise."""
    limiter.reset("never-seen")
    assert limiter.retry_after("never-seen") == 0


def test_keys_are_independent(limiter):
    """One client's failures do not throttle another."""
    for _ in range(3):
        limiter.record_failure("a")
    assert limiter.retry_after("b") == 0


def test_concurrent_failures_are_all_recorded(ratelimit, clock):
    """8 threads x 25 failures on one key trip a 200-failure window exactly."""
    limiter = ratelimit.FailureLimiter(windows=((3600, 200),), clock=clock)
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(25):
            limiter.record_failure("shared")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert limiter.retry_after("shared") == 3600

    fewer = ratelimit.FailureLimiter(windows=((3600, 200),), clock=clock)
    for _ in range(199):
        fewer.record_failure("shared")
    assert fewer.retry_after("shared") == 0


def test_key_count_is_bounded_and_oldest_latest_failure_is_evicted(ratelimit, clock):
    """1100 keys leave 1024; the keys whose latest failure is oldest go first."""
    limiter = ratelimit.FailureLimiter(windows=((3600, 1),), clock=clock)
    for index in range(1100):
        limiter.record_failure(f"k{index}")
        clock.advance(1)
    assert len(limiter) == 1024
    assert limiter.retry_after("k0") == 0
    assert limiter.retry_after("k75") == 0
    assert limiter.retry_after("k76") > 0
    assert limiter.retry_after("k1099") > 0


def test_a_repeated_failure_refreshes_the_key_before_eviction(ratelimit, clock):
    """Eviction looks at the latest failure of a key, not its first."""
    limiter = ratelimit.FailureLimiter(windows=((3600, 1),), max_keys=3, clock=clock)
    for key in ("a", "b", "c"):
        limiter.record_failure(key)
        clock.advance(1)
    limiter.record_failure("a")
    clock.advance(1)
    limiter.record_failure("d")
    assert len(limiter) == 3
    assert limiter.retry_after("b") == 0
    for key in ("a", "c", "d"):
        assert limiter.retry_after(key) > 0


def test_failures_expire_from_memory_after_the_longest_window(ratelimit, clock):
    """Old entries are pruned when the key fails again."""
    limiter = ratelimit.FailureLimiter(windows=((10, 2),), clock=clock)
    limiter.record_failure("a")
    clock.advance(3600)
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 0


def test_nothing_is_persisted(ratelimit, clock, tmp_path, monkeypatch):
    """Throttling is memory only: no file is created and the module has no file I/O (no permanent lockout)."""
    monkeypatch.chdir(tmp_path)
    limiter = ratelimit.FailureLimiter(clock=clock)
    for _ in range(30):
        limiter.record_failure("a")
    assert list(tmp_path.iterdir()) == []

    tree = ast.parse(Path(ratelimit.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open"
    assert imported <= {"collections", "math", "threading", "time", "typing"}, sorted(imported)


def test_reserve_books_the_attempt_atomically(limiter):
    """A burst of parallel attempts cannot all pass the check before any failure is booked."""
    granted = [limiter.reserve("a") for _ in range(3)]
    assert granted[0][0] == 0 and granted[0][1] is not None
    assert all(wait >= 1 and stamp is None for wait, stamp in granted[1:])


def test_reserve_is_race_free_across_threads(ratelimit, clock):
    """Concurrent reservations never exceed the window limit."""
    limiter = ratelimit.FailureLimiter(windows=((3600, 20),), clock=clock)
    results = []
    lock = threading.Lock()

    def worker():
        for _ in range(10):
            wait, _ = limiter.reserve("a")
            with lock:
                results.append(wait)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(0) == 20


def test_release_gives_back_only_that_attempt(limiter, clock):
    """Releasing a reservation removes that one booking and keeps earlier failures."""
    limiter.record_failure("a")
    clock.advance(2)
    _, stamp = limiter.reserve("a")
    limiter.release("a", stamp)
    clock.advance(2)
    _, stamp = limiter.reserve("a")
    limiter.release("a", stamp)
    clock.advance(30)
    for _ in range(3):
        limiter.record_failure("a")
        clock.advance(2)
    # 4 real failures inside 60 s, the 2 released reservations do not count.
    assert limiter.retry_after("a") == 0
    limiter.release("unknown", 1.0)
