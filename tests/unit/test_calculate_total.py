"""Carry-aware total: arrows resolved finest→coarsest, digits resolved with previous-value context."""

import pytest

from watermeter.position_utils import calculate_total, resolve_arrows

CONFIG = {"images": {"process_separate": False}, "detection": {"digits": {"count": 3}, "analogs": {"count": 4}}}


def preds(digits, arrows):
    p = {}
    for i, d in enumerate(digits):
        p[f"digit_{i + 1}"] = {"id": f"digit_{i + 1}", "class": str(d), "confidence": 0.99, "model": "digits"}
    for i, a in enumerate(arrows):
        p[f"analog_{i + 1}"] = {"id": f"analog_{i + 1}", "class": str(a), "confidence": 0.9, "model": "arrows"}
    return p


class TestResolveArrows:
    def test_live_case_uses_resolved_finer_value(self):
        # 2026-10-04: 0.01 dial reads 9.7 but 0.001 dial at 0.3 shows it already rolled over
        assert resolve_arrows([5.2, 9.7, 0.3, 3.2]) == [5, 0, 0, 3]

    def test_coarse_dial_just_before_integer(self):
        assert resolve_arrows([5.95, 9.5]) == [5, 9]

    def test_coarse_dial_just_after_rollover(self):
        assert resolve_arrows([5.9, 0.2]) == [6, 0]

    def test_wrap_at_zero(self):
        assert resolve_arrows([0.05, 9.5]) == [9, 9]

    def test_finest_dial_floored(self):
        assert resolve_arrows([3.27]) == [3]

    def test_empty(self):
        assert resolve_arrows([]) == []


class TestCalculateTotalWithoutPrevious:
    def test_live_case_total_and_raw(self):
        total, raw = calculate_total(CONFIG, preds([0, 5, 6], [5.2, 9.7, 0.3, 3.2]))
        assert total == pytest.approx(56.5003)
        assert raw["raw_total"] == pytest.approx(56.50032)
        assert raw["digits"] == [0, 5, 6]

    def test_nan_without_previous_is_unresolved(self):
        total, raw = calculate_total(CONFIG, preds([0, 5, "NAN"], [5.0, 0.0, 0.0, 0.0]))
        assert total is None
        assert any("NAN" in n for n in raw["notes"])

    def test_error_arrow_is_unresolved(self):
        total, raw = calculate_total(CONFIG, preds([0, 5, 6], [5.0, "ERROR", 0.0, 0.0]))
        assert total is None
        assert raw["raw_total"] is None

    def test_error_digit_is_unresolved(self):
        total, _ = calculate_total(CONFIG, preds([0, "ERROR", 6], [5.0, 0.0, 0.0, 0.0]))
        assert total is None


class TestCalculateTotalWithPrevious:
    def test_nan_digit_filled_from_previous(self):
        total, raw = calculate_total(CONFIG, preds([0, 5, "NAN"], [5.0, 0.0, 0.3, 3.2]), previous_value=56.4990)
        assert total == pytest.approx(56.5003)
        assert raw["digits"] == [0, 5, 6]

    def test_nan_digit_after_wrap_takes_next_integer(self):
        total, _ = calculate_total(CONFIG, preds([0, 5, "NAN"], [0.1, 1.0, 0.0, 0.0]), previous_value=56.9950)
        assert total == pytest.approx(57.0100)

    def test_early_rolling_wheel_inside_window_held(self):
        # 0.1 dial at 9.2 (f=0.92 >= 0.8) but wheel already shows 7
        total, raw = calculate_total(CONFIG, preds([0, 5, 7], [9.2, 0.0, 0.0, 0.0]), previous_value=56.8900)
        assert total == pytest.approx(56.9000)
        assert any("carry context" in n for n in raw["notes"])

    def test_early_wheel_outside_window_trusts_digits(self):
        # f=0.5 is far from the wrap: digit 7 is a real jump (plausibility decides)
        total, _ = calculate_total(CONFIG, preds([0, 5, 7], [5.0, 0.0, 0.0, 0.0]), previous_value=56.4900)
        assert total == pytest.approx(57.5000)

    def test_late_rolling_wheel_inside_window_advanced(self):
        # arrows wrapped (f=0.05) but wheel still shows 6
        total, _ = calculate_total(CONFIG, preds([0, 5, 6], [0.5, 5.0, 0.0, 0.0]), previous_value=56.9900)
        assert total == pytest.approx(57.0500)

    def test_wrong_high_previous_keeps_digits(self):
        # Review focus 2: P=57.0 wrong, true 56.95 -> must stay 56.95 (re-anchor can fire), not 57.95
        total, _ = calculate_total(CONFIG, preds([0, 5, 6], [9.4, 5.0, 0.0, 0.0]), previous_value=57.0000)
        assert total == pytest.approx(56.9500)

    def test_large_real_jump_keeps_digits(self):
        total, _ = calculate_total(CONFIG, preds([0, 6, 2], [1.0, 0.0, 0.0, 0.0]), previous_value=56.5000)
        assert total == pytest.approx(62.1000)

    def test_nan_inconsistent_with_previous_is_unresolved(self):
        total, raw = calculate_total(CONFIG, preds([0, 8, "NAN"], [1.0, 0.0, 0.0, 0.0]), previous_value=56.5000)
        assert total is None
        assert any("inconsistent" in n for n in raw["notes"])

    def test_counter_overflow_wraps_modulo(self):
        # Review focus 3: 3-digit meter rolls 999.9x -> 000.0x
        total, _ = calculate_total(CONFIG, preds([0, 0, 0], [0.2, 2.0, 0.0, 0.0]), previous_value=999.9500)
        assert total == pytest.approx(0.0200)

    def test_live_case_with_stuck_previous(self):
        total, _ = calculate_total(CONFIG, preds([0, 5, 6], [5.2, 9.7, 0.3, 3.2]), previous_value=56.5999)
        assert total == pytest.approx(56.5003)


class TestOtherMeterShapes:
    def test_digits_only_meter_steps_last_digit(self):
        # Review focus 1: no arrows -> no carry context, a real step 5->6 must pass
        cfg = {"images": {"process_separate": False}, "detection": {"digits": {"count": 3}, "analogs": {"count": 0}}}
        total, _ = calculate_total(cfg, preds([0, 5, 6], []), previous_value=55.0)
        assert total == pytest.approx(56.0)

    def test_arrows_only_meter(self):
        cfg = {"images": {"process_separate": False}, "detection": {"digits": {"count": 0}, "analogs": {"count": 2}}}
        total, _ = calculate_total(cfg, preds([], [5.9, 0.2]))
        assert total == pytest.approx(0.60)


class TestPairConsistency:
    def test_live_case_pairs_are_consistent(self):
        from watermeter.position_utils import arrow_pair_deviations

        devs = arrow_pair_deviations([5.2, 9.7, 0.3, 3.2], [5, 0, 0, 3])
        assert devs == pytest.approx([0.1968, 0.332, 0.02], abs=1e-3)
        _, raw = calculate_total(CONFIG, preds([0, 5, 6], [5.2, 9.7, 0.3, 3.2]))
        assert not any("inconsistent" in n for n in raw["notes"])

    def test_inconsistent_pair_adds_note_but_keeps_total(self):
        # 0.1 dial at 4.5 while 0.01 dial at 9.0 -> expected 4.9, deviation 0.4
        total, raw = calculate_total(CONFIG, preds([0, 5, 6], [4.5, 9.0, 0.0, 0.0]))
        assert total == pytest.approx(56.49)
        assert any(n.startswith("analog_1/analog_2 inconsistent (0.40)") for n in raw["notes"])

    def test_deviation_is_circular(self):
        from watermeter.position_utils import arrow_pair_deviations

        # 0.1 dial reads 9.95, expected 0.05 (int 0 + 0.5/10) -> distance 0.1, not 9.9
        assert arrow_pair_deviations([9.95, 0.5], [0, 0]) == pytest.approx([0.1])


class TestUnreadablePositions:
    def test_arrow_nan_capitalised_is_unresolved(self):
        total, raw = calculate_total(CONFIG, preds([0, 5, 6], [5.0, "NaN", 0.0, 0.0]))
        assert total is None
        assert raw["raw_total"] is None
        assert "analog_2: no reading (NaN)" in raw["notes"]

    def test_arrow_nan_lowercase_is_unresolved(self):
        total, _ = calculate_total(CONFIG, preds([0, 5, 6], [5.0, 0.0, "nan", 0.0]))
        assert total is None

    def test_digit_garbage_is_unresolved(self):
        total, raw = calculate_total(CONFIG, preds([0, "X", 6], [5.0, 0.0, 0.0, 0.0]))
        assert total is None
        assert "digit_2: no reading (X)" in raw["notes"]

    def test_missing_digit_is_unresolved(self):
        p = preds([0, 5, 6], [5.0, 0.0, 0.0, 0.0])
        del p["digit_2"]
        total, raw = calculate_total(CONFIG, p)
        assert total is None
        assert "digit_2: missing prediction" in raw["notes"]

    def test_missing_arrow_is_unresolved(self):
        p = preds([0, 5, 6], [5.0, 0.0, 0.0, 0.0])
        del p["analog_3"]
        total, raw = calculate_total(CONFIG, p)
        assert total is None
        assert "analog_3: missing prediction" in raw["notes"]
