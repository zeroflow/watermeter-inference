"""Unit tests for RateTracker — ring buffer for meter reading rate history."""

from datetime import datetime, timedelta

import pytest

from watermeter.rate_tracker import RateTracker


class TestRateTrackerInit:
    """Test initialization and basic properties."""

    def test_default_size(self):
        rt = RateTracker()
        assert rt.max_size == 5
        assert len(rt) == 0
        assert rt.history == []

    def test_custom_size(self):
        rt = RateTracker(max_size=10)
        assert rt.max_size == 10

    def test_history_is_readonly_copy(self):
        rt = RateTracker()
        rt.add(100.0)
        history = rt.history
        history.append((999.0, datetime.now()))
        assert len(rt) == 1  # original unchanged


class TestRateTrackerAdd:
    """Test add() method."""

    def test_add_single(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        assert len(rt) == 1
        assert rt.history[0][0] == 100.0

    def test_add_with_explicit_timestamp(self):
        rt = RateTracker(max_size=5)
        ts = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, ts)
        assert rt.history[0] == (100.0, ts)

    def test_add_auto_timestamp(self):
        rt = RateTracker(max_size=5)
        before = datetime.now()
        rt.add(100.0)
        after = datetime.now()
        ts = rt.history[0][1]
        assert before <= ts <= after

    def test_add_trims_to_max_size(self):
        rt = RateTracker(max_size=3)
        for i in range(5):
            rt.add(float(i))
        assert len(rt) == 3
        # Should keep the last 3: 2.0, 3.0, 4.0
        values = [v for v, _ in rt.history]
        assert values == [2.0, 3.0, 4.0]


class TestRateTrackerMutations:
    """Test pop_last, replace_last, reset, seed."""

    def test_pop_last(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        rt.pop_last()
        assert len(rt) == 1
        assert rt.history[0][0] == 100.0

    def test_pop_last_empty(self):
        """pop_last on empty tracker is a no-op."""
        rt = RateTracker(max_size=5)
        rt.pop_last()  # should not raise
        assert len(rt) == 0

    def test_replace_last(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        ts = datetime(2026, 2, 16, 14, 0)
        rt.replace_last(125.0, ts)
        assert len(rt) == 1
        assert rt.history[0] == (125.0, ts)

    def test_replace_last_empty(self):
        """replace_last on empty tracker is a no-op."""
        rt = RateTracker(max_size=5)
        rt.replace_last(125.0, datetime.now())  # should not raise
        assert len(rt) == 0

    def test_reset(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        rt.reset()
        assert len(rt) == 0
        assert rt.history == []

    def test_seed(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        ts = datetime(2026, 2, 16, 14, 0)
        rt.seed(200.0, ts)
        assert len(rt) == 1
        assert rt.history[0] == (200.0, ts)


class TestRateTrackerAverageRate:
    """Test average_rate_per_hour property."""

    def test_empty_returns_none(self):
        rt = RateTracker(max_size=5)
        assert rt.average_rate_per_hour is None

    def test_single_entry_returns_none(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        assert rt.average_rate_per_hour is None

    def test_two_entries(self):
        rt = RateTracker(max_size=5)
        base = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, base)
        rt.add(101.0, base + timedelta(hours=1))
        rate = rt.average_rate_per_hour
        assert rate is not None
        assert abs(rate - 1.0) < 1e-6

    def test_steady_rate(self):
        rt = RateTracker(max_size=10)
        base = datetime(2026, 2, 16, 12, 0)
        for i in range(5):
            rt.add(100.0 + i * 0.5, base + timedelta(hours=i))
        # 2.0 m3 over 4 hours = 0.5 m3/h
        rate = rt.average_rate_per_hour
        assert rate is not None
        assert abs(rate - 0.5) < 1e-6

    def test_zero_time_diff_returns_none(self):
        """Two entries at exact same time -> None."""
        rt = RateTracker(max_size=5)
        ts = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, ts)
        rt.add(101.0, ts)
        assert rt.average_rate_per_hour is None


class TestRateTrackerMaxSize:
    """Test max_size property setter."""

    def test_set_max_size(self):
        rt = RateTracker(max_size=5)
        rt.max_size = 10
        assert rt.max_size == 10

    def test_shrink_max_size_trims(self):
        rt = RateTracker(max_size=10)
        for i in range(8):
            rt.add(float(i))
        rt.max_size = 3
        assert len(rt) == 3
        values = [v for v, _ in rt.history]
        assert values == [5.0, 6.0, 7.0]
