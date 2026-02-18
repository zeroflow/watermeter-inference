"""BL-04: Auto-correction of low-confidence predictions using contextual signals.

Extracted from watermeter_service.py (Phase 4 refactoring).
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .position_utils import get_position_ids
from .rate_tracker import RateTracker
from .meter_state import MeterState

logger = logging.getLogger(__name__)


class CorrectionEngine:
    """BL-04: Auto-correction of low-confidence predictions using contextual signals."""

    def __init__(self, config: dict, rate_tracker: RateTracker, meter_state: MeterState) -> None:
        self.config = config
        self._rate_tracker = rate_tracker
        self._meter_state = meter_state

    def _get_ordered_position_ids(self) -> List[str]:
        """Return position IDs in order: digit_1, ..., analog_1, ... (most to least significant)."""
        digit_ids, arrow_ids = get_position_ids(self.config)
        return digit_ids + arrow_ids

    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
        """Estimate plausible range for next reading based on rate history."""
        if self._meter_state.previous_value is None or len(self._rate_tracker) < 3:
            return None
        avg_rate = self._rate_tracker.average_rate_per_hour
        if avg_rate is None or avg_rate <= 0:
            return None
        hours_elapsed = 0.0
        if self._meter_state.last_update_time:
            hours_elapsed = (datetime.now() - self._meter_state.last_update_time).total_seconds() / 3600
        if hours_elapsed <= 0:
            return None
        expected_delta = avg_rate * hours_elapsed
        config = self.config.get("correction", {})
        tolerance = config.get("rate_tolerance_factor", 3.0)
        min_expected = self._meter_state.previous_value
        max_expected = self._meter_state.previous_value + expected_delta * tolerance
        return (min_expected, max_expected)

    def _recalculate_with_replacement(
        self, predictions: Dict[str, Dict], replace_id: str, replace_class: str, raw_values: Dict
    ) -> float:
        """Calculate hypothetical total with one position replaced."""
        digit_ids, arrow_ids = get_position_ids(self.config)

        digits = []
        for image_id in digit_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls not in ("NAN", "ERROR"):
                    digits.append(int(cls))
                else:
                    digits.append(0)

        arrows = []
        for image_id in arrow_ids:
            if image_id in predictions:
                cls = replace_class if image_id == replace_id else predictions[image_id]["class"]
                if cls != "ERROR":
                    arrows.append(float(cls))
                else:
                    arrows.append(0.0)

        total = 0.0
        for i, digit in enumerate(digits):
            total += digit * 10 ** (len(digits) - 1 - i)
        for i, arrow in enumerate(arrows):
            total += int(arrow) * 10 ** (-(i + 1))
        return total

    def _check_consistency_improvement(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """Check if replacing a position fixes a consistency violation with adjacent positions."""
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False
        idx = position_ids.index(replace_id)

        def get_value(pid, override_id=None, override_class=None):
            if pid not in predictions:
                return None
            cls = override_class if pid == override_id else predictions[pid]["class"]
            if cls in ("NAN", "ERROR"):
                return None
            return float(cls) if predictions[pid]["model"] == "arrows" else int(cls)

        def has_violation(val_a, val_b):
            """Check half/upper consistency between adjacent positions."""
            frac = val_a % 1
            has_half = frac >= 0.4
            upper = int(val_b) >= 5
            return has_half != upper

        current_violations = 0
        replacement_violations = 0

        # Check pair with previous position (idx-1, idx)
        if idx > 0:
            prev_id = position_ids[idx - 1]
            prev_val = get_value(prev_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if prev_val is not None and curr_val is not None:
                if has_violation(prev_val, curr_val):
                    current_violations += 1
                if alt_val is not None and has_violation(prev_val, alt_val):
                    replacement_violations += 1

        # Check pair with next position (idx, idx+1)
        if idx < len(position_ids) - 1:
            next_id = position_ids[idx + 1]
            next_val = get_value(next_id)
            curr_val = get_value(replace_id)
            alt_val = get_value(replace_id, replace_id, replace_class)
            if next_val is not None and curr_val is not None:
                if has_violation(curr_val, next_val):
                    current_violations += 1
                if alt_val is not None and has_violation(alt_val, next_val):
                    replacement_violations += 1

        return current_violations > 0 and replacement_violations < current_violations

    def _check_cross_arrow_consistency(self, predictions: Dict[str, Dict], replace_id: str, replace_class: str) -> bool:
        """
        Check if replacing an arrow position improves cross-arrow consistency
        with the adjacent more-significant arrow.

        Only applies to arrow-arrow pairs. Uses the constraining arrow's
        confidence as a gate: only fires when the constraining arrow is confident.
        """
        position_ids = self._get_ordered_position_ids()
        if replace_id not in position_ids:
            return False

        idx = position_ids.index(replace_id)
        if idx == 0:
            return False

        prev_id = position_ids[idx - 1]
        if prev_id not in predictions:
            return False

        # Both must be arrows
        if predictions[prev_id]["model"] != "arrows" or predictions[replace_id]["model"] != "arrows":
            return False

        # Confidence gate
        config = self.config.get("correction", {})
        confidence_gate = config.get("cross_arrow_confidence_gate", 0.8)
        if predictions[prev_id]["confidence"] < confidence_gate:
            return False

        prev_class = predictions[prev_id]["class"]
        if prev_class in ("NAN", "ERROR"):
            return False

        curr_class = predictions[replace_id]["class"]
        if curr_class in ("NAN", "ERROR"):
            return False

        # Determine expected half for the less-significant arrow based on
        # the constraining arrow's fractional position.
        # frac < 0.5 -> needle in lower part of dial -> next dial in lower half (0-4)
        # frac >= 0.5 -> needle in upper part -> next dial in upper half (5-9)
        prev_value = float(prev_class)
        prev_frac = prev_value - int(prev_value)
        expected_lower_half = prev_frac < 0.5

        curr_int = int(float(curr_class))
        alt_int = int(float(replace_class))

        curr_in_expected = (curr_int < 5) == expected_lower_half
        alt_in_expected = (alt_int < 5) == expected_lower_half

        return (not curr_in_expected) and alt_in_expected

    def correct_predictions(self, predictions: Dict[str, Dict], raw_total: float, raw_values: Dict) -> List[str]:
        """
        Correct low-confidence predictions using contextual signals.
        Modifies predictions dict in-place. Returns correction warning strings.
        """
        config = self.config.get("correction", {})
        if not config.get("enabled", False):
            return []

        correction_threshold = config.get("confidence_threshold", 0.7)
        min_signal_agreement = config.get("min_signal_agreement", 2)
        min_alternative_confidence = config.get("min_alternative_confidence", 0.05)
        max_corrections = config.get("max_corrections_per_reading", 2)
        corrections = []

        position_ids = self._get_ordered_position_ids()

        # Safety: if all positions are high-confidence, don't touch anything
        all_confident = all(
            predictions[pid]["confidence"] >= correction_threshold for pid in position_ids if pid in predictions
        )
        if all_confident:
            return []

        expected_range = self._estimate_expected_range()

        # Meter rollover guard
        skip_previous_value_signal = False
        if self._meter_state.previous_value is not None:
            digit_ids = [pid for pid in position_ids if pid.startswith("digit_")]
            if digit_ids:
                digit_count = len(digit_ids)
                max_meter = 10**digit_count
                all_near_zero = all(
                    int(predictions[pid]["class"]) <= 1
                    for pid in digit_ids
                    if pid in predictions and predictions[pid]["class"] not in ("NAN", "ERROR")
                )
                if all_near_zero and self._meter_state.previous_value > 0.9 * max_meter:
                    skip_previous_value_signal = True

        for pid in position_ids:
            if len(corrections) >= max_corrections:
                break
            if pid not in predictions:
                continue

            pred = predictions[pid]
            if pred["confidence"] >= correction_threshold:
                continue

            top_k = pred.get("top_k", [])
            if not top_k or len(top_k) < 2:
                continue

            alternatives = [
                alt for alt in top_k[1:] if alt["confidence"] >= min_alternative_confidence and alt["class"] != "NAN"
            ]
            if not alternatives:
                continue

            best_alt = None
            best_score = 0

            for alt in alternatives:
                score = 0
                total_with_alt = self._recalculate_with_replacement(predictions, pid, alt["class"], raw_values)

                # Signal 1: Previous value constraint
                if not skip_previous_value_signal and self._meter_state.previous_value is not None:
                    if raw_total < self._meter_state.previous_value and total_with_alt >= self._meter_state.previous_value:
                        score += 1

                # Signal 2: Expected rate
                if expected_range is not None:
                    min_exp, max_exp = expected_range
                    if (raw_total < min_exp or raw_total > max_exp) and min_exp <= total_with_alt <= max_exp:
                        score += 1

                # Signal 3: Adjacent position consistency
                if self._check_consistency_improvement(predictions, pid, alt["class"]):
                    score += 1

                # Signal 4: Cross-arrow consistency (BL-05)
                if self._check_cross_arrow_consistency(predictions, pid, alt["class"]):
                    score += 1

                if score > best_score:
                    best_score = score
                    best_alt = alt

            if best_alt is not None and best_score >= min_signal_agreement:
                old_class = pred["class"]
                old_conf = pred["confidence"]
                pred["class"] = best_alt["class"]
                pred["confidence"] = best_alt["confidence"]
                pred["corrected_from"] = old_class
                pred["corrected_confidence"] = old_conf
                pred["correction_signals"] = best_score

                msg = (
                    f"Corrected {pid}: {old_class}\u2192{best_alt['class']} "
                    f"(conf={old_conf:.2f}\u2192{best_alt['confidence']:.2f}, "
                    f"signals={best_score}/{min_signal_agreement})"
                )
                corrections.append(msg)
                logger.info(msg)

        return corrections
