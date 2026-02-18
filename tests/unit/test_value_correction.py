"""Unit tests for BL-04: Value correction engine.

Tests predict_detailed on Classifier, helper methods on CorrectionEngine,
consistency improvement checks, signal scoring, and the correction engine.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from watermeter.correction import CorrectionEngine
from watermeter.rate_tracker import RateTracker
from watermeter.meter_state import MeterState


# ---------------------------------------------------------------------------
# Standalone predict / predict_detailed implementations for testing.
#
# We cannot import Classifier from watermeter.inference because the module
# has side effects (reads config.yaml, initialises OpenVINO) that fail on
# the host.  Instead we replicate the tiny algorithm here so we can verify
# correctness of the softmax + top-K logic without any heavyweight deps.
# ---------------------------------------------------------------------------

def _predict(classes, logits):
    """Replicate Classifier.predict() logic on raw logits."""
    logits = logits - logits.max()
    probs = np.exp(logits) / np.exp(logits).sum()
    idx = probs.argmax()
    return {'class': classes[idx], 'confidence': float(probs[idx])}


def _predict_detailed(classes, logits, top_k=3):
    """Replicate Classifier.predict_detailed() logic on raw logits."""
    logits = logits - logits.max()
    probs = np.exp(logits) / np.exp(logits).sum()
    k = min(top_k, len(classes))
    top_indices = probs.argsort()[::-1][:k]
    return [
        {'class': classes[idx], 'confidence': float(probs[idx])}
        for idx in top_indices
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine(config=None):
    """Create a minimal CorrectionEngine for testing."""
    config = config or {
        'images': {'process_separate': False},
        'detection': {
            'digits': {'count': 3},
            'analogs': {'count': 4},
        },
        'correction': {
            'enabled': True,
            'confidence_threshold': 0.7,
            'min_signal_agreement': 2,
            'min_alternative_confidence': 0.05,
            'rate_tolerance_factor': 3.0,
            'max_corrections_per_reading': 2,
            'top_k': 3,
        },
        'plausibility': {
            'rate_history_size': 25,
        },
    }
    rate_tracker = RateTracker(max_size=25)
    state = MeterState()
    return CorrectionEngine(config=config, rate_tracker=rate_tracker, meter_state=state)


# ---------------------------------------------------------------------------
# TestPredictDetailed
# ---------------------------------------------------------------------------

class TestPredictDetailed:
    """Tests for predict_detailed() softmax + top-K logic.

    NOTE: These tests verify the algorithm logic in isolation using standalone
    _predict / _predict_detailed functions, NOT the actual Classifier class from
    watermeter.inference (which has OpenVINO dependencies unavailable on host).
    They validate the mathematical correctness of softmax + top-K, but do not
    test the real Classifier.predict_detailed() implementation.
    """

    DIGIT_CLASSES = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'NAN']

    # Logits: class '5' (idx 5) highest, '3' (idx 3) second, '7' (idx 7) third
    DIGIT_LOGITS = np.array(
        [0.1, 0.2, 0.3, 1.5, 0.4, 2.0, 0.15, 1.2, 0.05, 0.1, -0.5],
        dtype=np.float32,
    )

    def test_predict_detailed_returns_top_k(self):
        """predict_detailed returns K entries sorted by confidence descending."""
        results = _predict_detailed(self.DIGIT_CLASSES, self.DIGIT_LOGITS, top_k=3)

        assert len(results) == 3
        # Confidences must be in descending order
        confs = [r['confidence'] for r in results]
        assert confs == sorted(confs, reverse=True)

        # Top-1 should be class '5' (index 5, highest logit=2.0)
        assert results[0]['class'] == '5'
        # Top-2 should be class '3' (index 3, logit=1.5)
        assert results[1]['class'] == '3'
        # Top-3 should be class '7' (index 7, logit=1.2)
        assert results[2]['class'] == '7'

    def test_predict_detailed_top1_matches_predict(self):
        """predict_detailed()[0] matches predict() output."""
        detailed = _predict_detailed(self.DIGIT_CLASSES, self.DIGIT_LOGITS, top_k=3)
        single = _predict(self.DIGIT_CLASSES, self.DIGIT_LOGITS)

        assert detailed[0]['class'] == single['class']
        assert abs(detailed[0]['confidence'] - single['confidence']) < 1e-6

    def test_predict_detailed_confidences_sum_lte_1(self):
        """Top-K confidences are softmax probabilities and must sum to <= 1.0."""
        results = _predict_detailed(self.DIGIT_CLASSES, self.DIGIT_LOGITS, top_k=5)

        total = sum(r['confidence'] for r in results)
        assert total <= 1.0 + 1e-7  # Allow floating point epsilon


# ---------------------------------------------------------------------------
# TestHelperMethods
# ---------------------------------------------------------------------------

class TestHelperMethods:
    """Tests for CorrectionEngine helper methods."""

    def test_estimate_expected_range_no_history(self):
        """No rate_history -> returns None."""
        engine = make_engine()
        engine._meter_state.previous_value = 100.0
        # Empty rate_history (less than 3)
        assert engine._estimate_expected_range() is None

    def test_estimate_expected_range_sufficient_history(self):
        """3+ entries with positive rate -> returns (min, max) tuple."""
        engine = make_engine()
        engine._meter_state.previous_value = 100.0
        now = datetime.now()
        engine._meter_state.last_update_time = now - timedelta(hours=1)

        # Build rate_history with 4 entries showing steady consumption
        base = now - timedelta(hours=4)
        engine._rate_tracker._history = [
            (97.0, base),
            (98.0, base + timedelta(hours=1)),
            (99.0, base + timedelta(hours=2)),
            (100.0, base + timedelta(hours=3)),
        ]

        result = engine._estimate_expected_range()
        assert result is not None
        min_exp, max_exp = result
        assert min_exp == engine._meter_state.previous_value
        assert max_exp > min_exp

    def test_recalculate_digit_replacement(self):
        """Replace digit_2 from '3' to '4' in 3-digit setup. Total changes by +10."""
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 7.0, 4.0, 1.0]}

        original = engine._recalculate_with_replacement(predictions, 'digit_2', '3', raw_values)
        replaced = engine._recalculate_with_replacement(predictions, 'digit_2', '4', raw_values)

        assert abs(replaced - original - 10.0) < 1e-9

    def test_recalculate_arrow_replacement(self):
        """Replace analog_1 from '3.0' to '7.0'. Total changes by +0.4."""
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [3.0, 7.0, 4.0, 1.0]}

        original = engine._recalculate_with_replacement(predictions, 'analog_1', '3.0', raw_values)
        replaced = engine._recalculate_with_replacement(predictions, 'analog_1', '7.0', raw_values)

        # floor(3.0)*0.1 = 0.3, floor(7.0)*0.1 = 0.7 -> delta = 0.4
        assert abs(replaced - original - 0.4) < 1e-9

    def test_get_ordered_position_ids(self):
        """Returns correct order for 3 digits + 4 arrows."""
        engine = make_engine()
        ids = engine._get_ordered_position_ids()
        assert ids == ['digit_1', 'digit_2', 'digit_3',
                       'analog_1', 'analog_2', 'analog_3', 'analog_4']


# ---------------------------------------------------------------------------
# TestConsistencyImprovement
# ---------------------------------------------------------------------------

class TestConsistencyImprovement:
    """Tests for _check_consistency_improvement()."""

    def test_consistency_improvement_detected(self):
        """Replacing a position fixes a consistency violation -> returns True.

        Setup: analog_1 reads '3.0' (frac=0.0, has_half=False).
        Next position analog_2 reads '7.0' (int=7, upper=True).
        has_half=False != upper=True -> VIOLATION.

        Replacement: analog_2 from '7.0' to '2.0' (int=2, upper=False).
        has_half=False == upper=False -> no violation.
        So replacing analog_2 fixes the violation.
        """
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.90, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = engine._check_consistency_improvement(predictions, 'analog_2', '2.0')
        assert result is True

    def test_no_consistency_improvement(self):
        """Position has no violation -> returns False.

        Setup: analog_1 reads '3.0' (frac=0.0, has_half=False).
        Next position analog_2 reads '2.0' (int=2, upper=False).
        has_half=False == upper=False -> NO VIOLATION.
        Replacing analog_2 with '4.0' still no violation, but there was no
        violation to fix, so improvement = False.
        """
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.90, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '3.0', 'confidence': 0.90, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows'},
        }

        result = engine._check_consistency_improvement(predictions, 'analog_2', '4.0')
        assert result is False


# ---------------------------------------------------------------------------
# TestSignalScoring
# ---------------------------------------------------------------------------

class TestSignalScoring:
    """Tests for individual signal scoring inside correct_predictions.

    These tests use correct_predictions with min_signal_agreement=1 to
    isolate individual signals.
    """

    def _make_signal_engine(self, min_signal_agreement=1):
        """Engine with min_signal_agreement=1 so a single signal triggers correction."""
        engine = make_engine()
        engine.config['correction']['min_signal_agreement'] = min_signal_agreement
        return engine

    def test_signal_previous_value_backward(self):
        """raw_total < previous_value, alt fixes it -> gets signal -> correction."""
        engine = self._make_signal_engine(min_signal_agreement=1)
        engine._meter_state.previous_value = 145.0

        # raw reading: digit_1=1, digit_2=3, digit_3=5 -> 135.xxxx (< 145)
        # alt for digit_2: '4' -> 145.xxxx (>= 145) -> signal fires
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': '4', 'confidence': 0.35},
                            {'class': '2', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '7.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 7.0, 4.0, 1.0]}
        raw_total = 135.2741  # < 145

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) >= 1
        assert predictions['digit_2']['class'] == '4'

    def test_signal_previous_value_forward(self):
        """raw_total >= previous_value -> no backward signal -> no correction (with 1 signal min)."""
        engine = self._make_signal_engine(min_signal_agreement=1)
        engine._meter_state.previous_value = 130.0  # raw_total >= previous_value

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': '4', 'confidence': 0.35},
                            {'class': '2', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241  # >= 130 -> no backward violation

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        # No backward violation, no expected_range, no consistency violation -> no signal
        assert len(corrections) == 0
        assert predictions['digit_2']['class'] == '3'  # unchanged

    def test_signal_expected_rate_in_range(self):
        """Alt brings total into expected range when original is outside -> signal fires."""
        engine = self._make_signal_engine(min_signal_agreement=1)
        engine._meter_state.previous_value = 135.0
        now = datetime.now()
        engine._meter_state.last_update_time = now - timedelta(hours=1)

        # Rate history: ~1 m3/h over 4 hours
        base = now - timedelta(hours=4)
        engine._rate_tracker._history = [
            (131.0, base),
            (132.0, base + timedelta(hours=1)),
            (133.0, base + timedelta(hours=2)),
            (135.0, base + timedelta(hours=3)),
        ]

        # raw_total = 125.2741 -> below expected range (min=135.0)
        # alt '4' for digit_2 -> 145.2741 -> within range [135.0, 135+1*3=138.0]?
        # Actually avg_rate ~= (135-131)/3h = 1.333/h, hours_elapsed=1,
        # expected_delta=1.333, max=135+1.333*3=139.0
        # alt '3' -> 135.2741 (in range), alt '4' -> 145.2741 (out of range)
        # We need raw_total to be OUTSIDE the range; alt puts it back IN.
        # raw_total with digit_2='2' -> 125.2741 (below 135). Alt '3' -> 135.2741 (in range).
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '2', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '2', 'confidence': 0.40},
                            {'class': '3', 'confidence': 0.35},
                            {'class': '1', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 2, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 125.2241  # outside expected range [135, 139]

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        # Both signal 1 (backward) and signal 2 (rate range) should fire for alt '3'
        # but we only need at least one correction
        assert len(corrections) >= 1
        assert predictions['digit_2']['class'] == '3'

    def test_signal_adjacent_consistency(self):
        """Alt fixes consistency violation -> signal fires.

        analog_1='3.0' (frac=0.0, has_half=False), analog_2='7.0' (int=7, upper=True).
        Violation: False != True.
        Alt for analog_2: '2.0' (upper=False) -> fixes it.
        """
        engine = self._make_signal_engine(min_signal_agreement=1)

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

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) >= 1
        assert predictions['analog_2']['class'] == '2.0'


# ---------------------------------------------------------------------------
# TestCorrectionEngine
# ---------------------------------------------------------------------------

class TestCorrectionEngine:
    """Tests for the full correct_predictions() method."""

    def _make_predictions(self):
        """Standard 3-digit + 4-arrow prediction set."""
        return {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': '4', 'confidence': 0.35},
                            {'class': '2', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }

    def test_no_correction_when_disabled(self):
        """correction.enabled = False -> returns empty list."""
        engine = make_engine()
        engine.config['correction']['enabled'] = False

        predictions = self._make_predictions()
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert corrections == []

    def test_no_correction_all_confident(self):
        """All positions above threshold -> returns empty list."""
        engine = make_engine()
        predictions = self._make_predictions()
        # Set all confidences above threshold (0.7)
        for pred in predictions.values():
            pred['confidence'] = 0.95

        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert corrections == []

    def test_correction_applied_two_signals(self):
        """Low conf + 2 signals (backward + rate) -> correction applied."""
        engine = make_engine()
        engine._meter_state.previous_value = 145.0
        now = datetime.now()
        engine._meter_state.last_update_time = now - timedelta(hours=1)

        # Rate history: ~1.33 m3/h
        base = now - timedelta(hours=4)
        engine._rate_tracker._history = [
            (141.0, base),
            (142.33, base + timedelta(hours=1)),
            (143.66, base + timedelta(hours=2)),
            (145.0, base + timedelta(hours=3)),
        ]

        # raw_total = 135.xxxx (< 145, backward + out of rate range)
        # alt '4' for digit_2 -> 145.xxxx (>= 145, in rate range)
        predictions = self._make_predictions()
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) >= 1
        assert predictions['digit_2']['class'] == '4'
        assert predictions['digit_2']['corrected_from'] == '3'
        assert predictions['digit_2']['correction_signals'] >= 2

    def test_no_correction_single_signal(self):
        """Only 1 signal (below min=2) -> no correction."""
        engine = make_engine()
        engine.config['correction']['min_signal_agreement'] = 2
        engine._meter_state.previous_value = 145.0
        # No rate_history -> no signal 2. No consistency violation -> no signal 3.
        # Only signal 1 (backward) fires.

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': '4', 'confidence': 0.35},
                            {'class': '2', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241  # < 145

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) == 0
        assert predictions['digit_2']['class'] == '3'  # unchanged

    def test_correction_never_to_nan(self):
        """NAN alternative is filtered out even if signals agree."""
        engine = make_engine()
        engine.config['correction']['min_signal_agreement'] = 1
        engine._meter_state.previous_value = 145.0

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': 'NAN', 'confidence': 0.35},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) == 0
        assert predictions['digit_2']['class'] == '3'  # NAN filtered, no valid alt

    def test_max_corrections_limit(self):
        """max_corrections=1, 2 low-conf positions -> only 1 corrected."""
        engine = make_engine()
        engine.config['correction']['max_corrections_per_reading'] = 1
        engine.config['correction']['min_signal_agreement'] = 1
        engine._meter_state.previous_value = 145.0  # raw_total < previous_value

        # raw reading: digits [1, 3, 3] -> 133 + arrows -> 133.2241 (< 145)
        # digit_2 alt '4' -> 143.2241 (still < 145, but closer)
        # digit_2 alt '5' -> 153.2241 (>= 145, signal fires!)
        # digit_3 alt '5' -> 135.2241 (still < 145)
        # digit_3 alt '4' -> 134.2241 (still < 145)
        # Only digit_2 actually has an alt that fixes the backward reading.
        # We need BOTH positions to have viable alts. Let's use:
        # digit_2 class='3' alt '5' -> 153.xxx (>= 145)
        # digit_3 class='3' alt '5' -> 135.xxx... no, still < 145.
        # Better approach: use previous_value = 135.0, raw digits [1,2,4] = 124
        # digit_2 alt '3' -> 134 (still < 135)... no.
        # Simplest: use consistency signal instead.
        # analog_1='3.0', analog_2='7.0' -> violation (has_half=False vs upper=True)
        # analog_1='3.0', analog_2 alt='2.0' -> fixes it -> signal fires
        # Also make analog_3 low conf with a consistency violation from analog_2.
        # analog_2='7.0', analog_3='4.0'. 7.0%1=0, has_half=False, int(4)<5, upper=False.
        # has_half=False == upper=False -> no violation. So need different setup.
        #
        # Two positions with consistency violations:
        # analog_1='3.0' (frac=0,has_half=F), analog_2='7.0' -> violation (F != T)
        # analog_2='7.0' (frac=0,has_half=F), analog_3='6.0' -> violation (F != T)
        # Fix analog_2 '7.0' -> '2.0': now analog_1='3.0' vs analog_2='2.0' OK (F==F)
        #   and analog_2='2.0' vs analog_3='6.0' -> violation! (F != T) still bad.
        # Fix analog_3 '6.0' -> '3.0': analog_2='7.0' vs analog_3='3.0' -> F vs F OK.
        #   and analog_3='3.0' vs analog_4='1.0' -> F vs F OK. Fixes it!
        # So both analog_2 and analog_3 have consistency fixes available.
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
            'analog_3': {'id': 'analog_3', 'class': '6.0', 'confidence': 0.40, 'model': 'arrows',
                         'image_bytes': b'',
                         'top_k': [
                             {'class': '6.0', 'confidence': 0.40},
                             {'class': '3.0', 'confidence': 0.35},
                             {'class': '9.0', 'confidence': 0.10},
                         ]},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.80, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [3.0, 7.0, 6.0, 1.0]}
        raw_total = 135.3761

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) == 1  # max_corrections=1

    def test_correction_warning_format(self):
        """Verify warning string format contains position, old/new class, confidences, signals."""
        engine = make_engine()
        engine.config['correction']['min_signal_agreement'] = 1
        engine._meter_state.previous_value = 145.0

        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits',
                        'image_bytes': b''},
            'digit_2': {'id': 'digit_2', 'class': '3', 'confidence': 0.40, 'model': 'digits',
                        'image_bytes': b'',
                        'top_k': [
                            {'class': '3', 'confidence': 0.40},
                            {'class': '4', 'confidence': 0.35},
                            {'class': '2', 'confidence': 0.10},
                        ]},
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.90, 'model': 'digits',
                        'image_bytes': b''},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_2': {'id': 'analog_2', 'class': '2.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_3': {'id': 'analog_3', 'class': '4.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
            'analog_4': {'id': 'analog_4', 'class': '1.0', 'confidence': 0.90, 'model': 'arrows',
                         'image_bytes': b''},
        }
        raw_values = {'digits': [1, 3, 5], 'arrows': [2.0, 2.0, 4.0, 1.0]}
        raw_total = 135.2241

        corrections = engine.correct_predictions(predictions, raw_total, raw_values)
        assert len(corrections) == 1

        msg = corrections[0]
        assert 'digit_2' in msg            # position ID
        assert '3' in msg                  # old class
        assert '4' in msg                  # new class
        assert '0.40' in msg               # old confidence
        assert '0.35' in msg               # new confidence
        assert 'signals=' in msg           # signal count label
