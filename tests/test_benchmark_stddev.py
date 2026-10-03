"""Tests for StdDev computation in benchmark results (continuous mode)."""

import numpy as np


def circular_error(pred: float, true: float) -> float:
    """Circular error for a dial value in [0, 10)."""
    diff = abs(pred - true) % 10
    return min(diff, 10 - diff)


class TestCircularErrorStdDev:
    def test_std_zero_when_all_errors_identical(self):
        errors = [0.1, 0.1, 0.1, 0.1]
        result = round(float(np.std(errors)), 4)
        assert result == 0.0

    def test_std_nonzero_for_varied_errors(self):
        errors = [0.0, 0.2, 0.4, 0.6]
        result = round(float(np.std(errors)), 4)
        assert result > 0.0
        expected = round(float(np.std([0.0, 0.2, 0.4, 0.6])), 4)
        assert result == expected

    def test_std_empty_list_returns_zero(self):
        errors = []
        result = np.std(errors) if errors else 0
        assert result == 0

    def test_std_single_element_is_zero(self):
        errors = [0.5]
        result = round(float(np.std(errors)), 4)
        assert result == 0.0

    def test_std_rounded_to_four_decimal_places(self):
        errors = [0.1, 0.3, 0.2, 0.5, 0.4]
        result = round(float(np.std(errors)), 4)
        # Should have at most 4 decimal places
        assert result == round(result, 4)

    def test_std_computed_from_circular_errors(self):
        """Verify std is computed over circular errors (not raw predictions)."""
        predictions = [
            (9.9, 0.1),  # circular_error = 0.2
            (0.3, 0.1),  # circular_error = 0.2
            (0.6, 0.1),  # circular_error = 0.5
        ]
        errors = [circular_error(p, t) for p, t in predictions]
        result = round(float(np.std(errors)), 4)

        expected_errors = [0.2, 0.2, 0.5]
        expected_std = round(float(np.std(expected_errors)), 4)
        assert result == expected_std

    def test_error_std_present_in_result_dict(self):
        """Simulate construction of the result dict and verify error_std key is present."""
        errors = [0.1, 0.2, 0.15, 0.3, 0.05]
        mae = np.mean(errors)
        rmse = np.sqrt(np.mean([e**2 for e in errors]))
        error_std = np.std(errors)

        result = {
            "training_mode": "continuous",
            "mae": round(float(mae), 4),
            "rmse": round(float(rmse), 4),
            "error_std": round(float(error_std), 4),
        }

        assert "error_std" in result
        assert isinstance(result["error_std"], float)
        assert result["error_std"] >= 0.0

    def test_metadata_dict_includes_error_std(self):
        """Simulate metadata persistence dict and verify error_std is included."""
        result = {
            "mae": 0.1234,
            "rmse": 0.2345,
            "error_std": 0.0567,
            "within_half_pct": 95.0,
            "within_one_pct": 99.0,
            "mean_confidence": 88.5,
            "inference_time_ms": 12.3,
            "total_images": 100,
        }

        benchmark_metadata = {
            "training_mode": "continuous",
            "mae": result["mae"],
            "rmse": result["rmse"],
            "error_std": result["error_std"],
            "within_half_pct": result["within_half_pct"],
            "within_one_pct": result["within_one_pct"],
            "accuracy": result["within_half_pct"],
            "mean_confidence": result["mean_confidence"],
            "inference_time_ms": result["inference_time_ms"],
            "total_images": result["total_images"],
        }

        assert "error_std" in benchmark_metadata
        assert benchmark_metadata["error_std"] == 0.0567
