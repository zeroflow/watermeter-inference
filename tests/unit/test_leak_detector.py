"""Unit tests for LeakDetector — sustained consumption (leak) detection.

Tests the extracted LeakDetector class directly, without WatermeterService.
"""

from datetime import datetime, timedelta

import pytest

from watermeter.leak_detector import LeakDetector
from watermeter.rate_tracker import RateTracker


@pytest.fixture
def rate_tracker():
    return RateTracker(max_size=25)


@pytest.fixture
def config():
    return {
        'plausibility': {
            'enable_leak_detection': True,
            'sustained_rate_threshold': 0.05,
            'sustained_rate_readings': 3,
        },
    }


@pytest.fixture
def detector(rate_tracker, config):
    return LeakDetector(rate_tracker=rate_tracker, config=config)


class TestLeakDetectorInit:
    def test_creates_with_dependencies(self, rate_tracker, config):
        d = LeakDetector(rate_tracker=rate_tracker, config=config)
        assert d._rate_tracker is rate_tracker
        assert d.config is config


class TestLeakDetectorCheck:
    def test_no_warning_insufficient_history(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))

        assert detector.check() is None

    def test_no_warning_rate_below_threshold(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.001, base + timedelta(minutes=5))
        rate_tracker.add(100.002, base + timedelta(minutes=10))
        rate_tracker.add(100.003, base + timedelta(minutes=15))

        assert detector.check() is None

    def test_warning_all_above_threshold(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))
        rate_tracker.add(100.060, base + timedelta(minutes=15))

        result = detector.check()
        assert result is not None
        assert "Sustained consumption" in result

    def test_no_warning_one_interval_below(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.021, base + timedelta(minutes=10))
        rate_tracker.add(100.041, base + timedelta(minutes=15))

        assert detector.check() is None

    def test_no_warning_feature_disabled(self, detector, rate_tracker):
        detector.config['plausibility']['enable_leak_detection'] = False

        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))
        rate_tracker.add(100.060, base + timedelta(minutes=15))

        assert detector.check() is None

    def test_warning_message_format(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))
        rate_tracker.add(100.060, base + timedelta(minutes=15))

        result = detector.check()
        assert result is not None
        assert "0.240" in result
        assert "15" in result
        assert "3" in result
        assert "0.05" in result
        assert "m³/h" in result

    def test_warning_clears_after_low_rate_reading(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))
        rate_tracker.add(100.060, base + timedelta(minutes=15))
        assert detector.check() is not None

        rate_tracker.add(100.061, base + timedelta(minutes=20))
        assert detector.check() is None
