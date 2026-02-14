"""Unit tests for BL-05: Cross-arrow consistency signal.

Tests the _check_cross_arrow_consistency() method directly, and its integration
into the correct_predictions() correction engine (Signal 4).

Note: watermeter.watermeter_service is mocked at module level in the unit test
conftest.  We temporarily remove the mock to import the real class.
"""

import sys

# ---------------------------------------------------------------------------
# Load the REAL WatermeterService class (bypassing the conftest mock).
# ---------------------------------------------------------------------------
_ws_mock_backup = {}
for _name in ['watermeter_service', 'watermeter.watermeter_service']:
    if _name in sys.modules:
        _ws_mock_backup[_name] = sys.modules.pop(_name)

from watermeter.watermeter_service import WatermeterService  # noqa: E402

for _name, _mock in _ws_mock_backup.items():
    sys.modules[_name] = _mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(config=None):
    """Create a minimal WatermeterService without calling __init__."""
    svc = object.__new__(WatermeterService)
    svc.config = config or {
        'images': {'process_separate': False},
        'detection': {
            'digits': {'count': 3},
            'analogs': {'count': 4},
        },
        'correction': {
            'enabled': True,
            'confidence_threshold': 0.7,
            'min_signal_agreement': 1,  # 1 for testing cross-arrow signal in isolation
            'min_alternative_confidence': 0.05,
            'rate_tolerance_factor': 3.0,
            'max_corrections_per_reading': 2,
            'top_k': 3,
            'cross_arrow_confidence_gate': 0.8,
        },
        'plausibility': {
            'rate_history_size': 25,
        },
    }
    svc.previous_value = None
    svc.last_update_time = None
    svc.rate_history = []
    svc.leak_warning = False
    return svc


# ---------------------------------------------------------------------------
# TestCheckCrossArrowConsistency — direct method tests (5 tests)
# ---------------------------------------------------------------------------

class TestCheckCrossArrowConsistency:
    """Tests for _check_cross_arrow_consistency() called directly."""

    def test_cross_arrow_improves_consistency(self):
        """analog_1 solidly at 3.0 -> analog_2 should be in lower half.

        analog_2 currently reads 7.0 (upper half -- wrong).
        Replacing with 2.0 (lower half) should return True.
        """
        svc = make_service()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.95, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = svc._check_cross_arrow_consistency(predictions, 'analog_2', '2.0')
        assert result is True

    def test_cross_arrow_no_improvement_when_consistent(self):
        """analog_1 solidly at 3.0 -> analog_2 reads 2.0 (correct lower half).

        Replacing with 7.0 (upper half) would make it worse -> False.
        """
        svc = make_service()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.95, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = svc._check_cross_arrow_consistency(predictions, 'analog_2', '7.0')
        assert result is False

    def test_cross_arrow_skipped_low_confidence(self):
        """Constraining arrow (analog_1) at 0.50 confidence -- below gate of 0.8.

        Even though analog_2 is in the wrong half, signal should not fire.
        """
        svc = make_service()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.50, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = svc._check_cross_arrow_consistency(predictions, 'analog_2', '2.0')
        assert result is False

    def test_cross_arrow_skipped_for_digit(self):
        """Previous position is a digit (digit_3), not an arrow -> False.

        Cross-arrow consistency only applies to arrow-arrow pairs.
        """
        svc = make_service()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '3.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        # analog_1's predecessor is digit_3 (a digit, not an arrow)
        result = svc._check_cross_arrow_consistency(predictions, 'analog_1', '2.0')
        assert result is False

    def test_cross_arrow_skipped_first_position(self):
        """First position (digit_1) has no predecessor -> False."""
        svc = make_service()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.95, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = svc._check_cross_arrow_consistency(predictions, 'digit_1', '2')
        assert result is False


# ---------------------------------------------------------------------------
# TestCrossArrowIntegration — through correct_predictions() (3 tests)
# ---------------------------------------------------------------------------

class TestCrossArrowIntegration:
    """Tests for Signal 4 through the full correct_predictions() engine."""

    def test_cross_arrow_signal_in_correction_engine(self):
        """Cross-arrow signal fires as Signal 4, correcting analog_2.

        Setup: min_signal_agreement=1, no previous_value, no rate_history.
        analog_1 at 3.0 (high conf) constrains analog_2 to lower half.
        analog_2 reads 7.0 (upper half -- wrong) with alternative 2.0 (lower half).
        Signal 4 (and possibly Signal 3) fire -> correction applied.
        Expect analog_2 corrected to 2.0.
        """
        svc = make_service()

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.95, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows',
                         'image_bytes': b'',
                         'top_k': [
                             {'class': '7.0', 'confidence': 0.40},
                             {'class': '2.0', 'confidence': 0.35},
                             {'class': '8.0', 'confidence': 0.10},
                         ]},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [3.0, 7.0, 4.0, 1.0]}
        raw_total = 135.3741

        corrections = svc.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) >= 1
        assert predictions['analog_2']['class'] == '2.0'

    def test_cross_arrow_config_gate_respected(self):
        """Raising the confidence gate to 0.95 prevents Signal 4 from firing.

        With min_signal_agreement=2, Signal 3 alone (score=1) does not suffice.
        Signal 4 would bring score to 2, but the gate blocks it because the
        constraining arrow (analog_1) has confidence 0.90, below the 0.95 gate.
        No previous_value, no rate_history -> Signals 1 and 2 don't fire.
        Result: no correction applied.
        """
        svc = make_service()
        svc.config['correction']['cross_arrow_confidence_gate'] = 0.95
        svc.config['correction']['min_signal_agreement'] = 2

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows',
                         'image_bytes': b'',
                         'top_k': [
                             {'class': '7.0', 'confidence': 0.40},
                             {'class': '2.0', 'confidence': 0.35},
                             {'class': '8.0', 'confidence': 0.10},
                         ]},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [3.0, 7.0, 4.0, 1.0]}
        raw_total = 135.3741

        corrections = svc.correct_predictions(predictions, raw_total, raw_values)
        # Signal 3 alone gives score=1, below min_signal_agreement=2.
        # Signal 4 blocked by gate (0.90 < 0.95). No other signals fire.
        assert len(corrections) == 0
        assert predictions['analog_2']['class'] == '7.0'  # unchanged

    def test_cross_arrow_transitive_propagation(self):
        """Corrections propagate transitively through the in-place update.

        analog_1 at 3.0 (high conf) constrains analog_2 to lower half.
        analog_2 reads 7.0 -> corrected to 2.0 (in-place).
        After correction, analog_2 reads 2.0 and constrains analog_3 to lower half.
        analog_3 reads 8.0 -> should be corrected to 3.0.

        Gate set to 0.3 so corrected analog_2 (confidence 0.35) passes the gate.
        """
        svc = make_service()
        svc.config['correction']['cross_arrow_confidence_gate'] = 0.3
        svc.config['correction']['max_corrections_per_reading'] = 3

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.95, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows',
                         'image_bytes': b'',
                         'top_k': [
                             {'class': '7.0', 'confidence': 0.40},
                             {'class': '2.0', 'confidence': 0.35},
                         ]},
            'analog_3': {'id': 'analog_3', 'class': '8.0', 'confidence': 0.40, 'model': 'arrows',
                         'image_bytes': b'',
                         'top_k': [
                             {'class': '8.0', 'confidence': 0.40},
                             {'class': '3.0', 'confidence': 0.35},
                         ]},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [3.0, 7.0, 8.0, 1.0]}
        raw_total = 135.3781

        corrections = svc.correct_predictions(predictions, raw_total, raw_values)

        # analog_2 should be corrected to 2.0
        assert predictions['analog_2']['class'] == '2.0'
        # analog_3 should be transitively corrected to 3.0
        assert predictions['analog_3']['class'] == '3.0'
        assert len(corrections) >= 2
