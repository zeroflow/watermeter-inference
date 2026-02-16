"""Unit tests for PlausibilityChecker — consistency and plausibility validation.

Tests the extracted PlausibilityChecker class directly.
"""

from datetime import datetime, timedelta

import pytest

from watermeter.plausibility import PlausibilityChecker
from watermeter.rate_tracker import RateTracker


@pytest.fixture
def config():
    return {
        'images': {'process_separate': False},
        'detection': {
            'digits': {'count': 3},
            'analogs': {'count': 4},
        },
        'plausibility': {
            'enable_reverse_detection': True,
            'enable_rate_limit': True,
            'max_rate_per_reading': 1.0,
            'max_rate_per_hour': 5.0,
            'enable_consistency_check': True,
        },
    }


@pytest.fixture
def rate_tracker():
    return RateTracker(max_size=25)


@pytest.fixture
def checker(config, rate_tracker):
    return PlausibilityChecker(config=config, rate_tracker=rate_tracker)


# ---------------------------------------------------------------------------
# check_consistency tests
# ---------------------------------------------------------------------------

class TestCheckConsistency:
    def test_no_warnings_consistent(self, checker):
        """All positions consistent -> empty warnings."""
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.9, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.9, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '2', 'confidence': 0.9, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.9, 'model': 'arrows'},
        }
        warnings = checker.check_consistency(predictions)
        assert warnings == []

    def test_inconsistency_detected(self, checker):
        """Half/upper mismatch -> warning."""
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.9, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.9, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '2', 'confidence': 0.9, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.9, 'model': 'arrows'},
        }
        warnings = checker.check_consistency(predictions)
        assert len(warnings) == 1
        assert "analog_1" in warnings[0]
        assert "analog_2" in warnings[0]

    def test_consistency_disabled(self, checker):
        """enable_consistency_check=False -> empty warnings."""
        checker.config['plausibility']['enable_consistency_check'] = False
        predictions = {
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.9, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.9, 'model': 'arrows'},
        }
        assert checker.check_consistency(predictions) == []

    def test_nan_and_error_skipped(self, checker):
        """NAN/ERROR positions are skipped."""
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': 'NAN', 'confidence': 0.1, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.9, 'model': 'digits'},
        }
        warnings = checker.check_consistency(predictions)
        assert warnings == []


# ---------------------------------------------------------------------------
# validate_plausibility tests
# ---------------------------------------------------------------------------

class TestValidatePlausibility:
    def test_first_reading_accepted(self, checker, rate_tracker):
        """No previous value -> accept and add to rate history."""
        is_valid, warnings = checker.validate_plausibility(
            new_value=100.0, previous_value=None, last_update_time=None,
        )
        assert is_valid is True
        assert warnings == []
        assert len(rate_tracker) == 1

    def test_reverse_detected(self, checker):
        """New value < previous -> reject."""
        is_valid, warnings = checker.validate_plausibility(
            new_value=99.0, previous_value=100.0, last_update_time=datetime.now(),
        )
        assert is_valid is False
        assert any("Reverse" in w for w in warnings)

    def test_reverse_detection_disabled(self, checker):
        """Reverse disabled -> accept even if new < previous."""
        checker.config['plausibility']['enable_reverse_detection'] = False
        is_valid, warnings = checker.validate_plausibility(
            new_value=99.0, previous_value=100.0, last_update_time=datetime.now(),
        )
        assert is_valid is True

    def test_rate_per_reading_exceeded(self, checker):
        """Change per reading exceeds max -> reject."""
        is_valid, warnings = checker.validate_plausibility(
            new_value=102.0, previous_value=100.0, last_update_time=datetime.now(),
        )
        assert is_valid is False
        assert any("per reading" in w for w in warnings)

    def test_rate_per_hour_spike_warning(self, checker, rate_tracker):
        """Rate per hour exceeded but no history -> warn but accept."""
        now = datetime.now()
        is_valid, warnings = checker.validate_plausibility(
            new_value=100.8, previous_value=100.0,
            last_update_time=now - timedelta(minutes=5),
        )
        # 0.8 in 5 min = 9.6/h > 5.0 max, but no history -> warn + accept
        assert is_valid is True
        assert any("high" in w.lower() for w in warnings)

    def test_normal_reading_accepted(self, checker, rate_tracker):
        """Normal forward reading within limits -> accept."""
        now = datetime.now()
        is_valid, warnings = checker.validate_plausibility(
            new_value=100.5, previous_value=100.0,
            last_update_time=now - timedelta(hours=1),
        )
        assert is_valid is True

    def test_rate_check_disabled(self, checker):
        """Rate limit disabled -> large jump accepted."""
        checker.config['plausibility']['enable_rate_limit'] = False
        now = datetime.now()
        is_valid, warnings = checker.validate_plausibility(
            new_value=200.0, previous_value=100.0,
            last_update_time=now - timedelta(hours=1),
        )
        assert is_valid is True
        assert warnings == []
