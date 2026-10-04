"""Unit tests for ReanchorTracker — detects N consistent lower readings."""

from watermeter.reanchor import ReanchorTracker


def make(required=3, max_spread=0.01, tolerance=0.002):
    return ReanchorTracker(required=required, max_spread=max_spread, tolerance=tolerance)


def test_fires_after_required_consistent_values():
    t = make()
    assert t.add(56.5903) is False
    assert t.add(56.5903) is False
    assert t.add(56.5904) is True


def test_spread_beyond_max_restarts_sequence():
    t = make()
    t.add(56.50)
    t.add(56.50)
    assert t.add(56.52) is False  # spread 0.02 > 0.01 -> restart with 56.52
    assert t.values == [56.52]


def test_decrease_beyond_tolerance_restarts_sequence():
    t = make()
    t.add(56.505)
    t.add(56.505)
    assert t.add(56.500) is False  # 0.005 drop > tolerance 0.002
    assert t.values == [56.500]


def test_decrease_within_tolerance_keeps_sequence():
    t = make()
    t.add(56.5005)
    t.add(56.5000)  # 0.0005 drop <= tolerance
    assert t.add(56.5001) is True


def test_reset_clears_sequence():
    t = make()
    t.add(1.0)
    t.add(1.0)
    t.reset()
    assert t.values == []
    assert t.add(1.0) is False


def test_required_exposed():
    assert make(required=6).required == 6
