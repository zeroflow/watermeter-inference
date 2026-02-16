"""Tests for circular_error helper function."""
import pytest


class TestCircularError:
    """Test circular_error() with dial wraparound (period=10.0)."""

    def test_no_wraparound_small_diff(self):
        """Normal case: 3.0 vs 3.2 = 0.2."""
        from watermeter.training_core import circular_error
        assert circular_error(3.0, 3.2) == pytest.approx(0.2)

    def test_no_wraparound_larger_diff(self):
        """Normal case: 2.0 vs 5.0 = 3.0 (shorter than going around)."""
        from watermeter.training_core import circular_error
        assert circular_error(2.0, 5.0) == pytest.approx(3.0)

    def test_wraparound_close(self):
        """Wraparound: 9.9 vs 0.1 = 0.2 (not 9.8)."""
        from watermeter.training_core import circular_error
        assert circular_error(9.9, 0.1) == pytest.approx(0.2)

    def test_wraparound_reverse(self):
        """Wraparound: 0.1 vs 9.9 = 0.2 (symmetric)."""
        from watermeter.training_core import circular_error
        assert circular_error(0.1, 9.9) == pytest.approx(0.2)

    def test_wraparound_wider(self):
        """Wraparound: 9.5 vs 0.5 = 1.0 (not 9.0)."""
        from watermeter.training_core import circular_error
        assert circular_error(9.5, 0.5) == pytest.approx(1.0)

    def test_exact_match(self):
        """Exact match: 5.0 vs 5.0 = 0.0."""
        from watermeter.training_core import circular_error
        assert circular_error(5.0, 5.0) == pytest.approx(0.0)

    def test_max_distance(self):
        """Max distance on circle: 0.0 vs 5.0 = 5.0."""
        from watermeter.training_core import circular_error
        assert circular_error(0.0, 5.0) == pytest.approx(5.0)

    def test_halfway_around_is_same_either_way(self):
        """At exactly half-period, both paths are equal: 0.0 vs 5.0 = 5.0."""
        from watermeter.training_core import circular_error
        assert circular_error(0.0, 5.0) == pytest.approx(5.0)
        assert circular_error(5.0, 0.0) == pytest.approx(5.0)

    def test_custom_period(self):
        """Custom period: e.g. period=360 for degrees."""
        from watermeter.training_core import circular_error
        assert circular_error(350.0, 10.0, period=360.0) == pytest.approx(20.0)
