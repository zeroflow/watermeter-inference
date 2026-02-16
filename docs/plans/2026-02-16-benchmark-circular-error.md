# Benchmark Circular Error Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the linear error calculation in benchmark and mislabel-scan so regression models correctly handle the 9.9→0.0 dial wraparound.

**Architecture:** Extract a shared `circular_error(pred, true, period=10.0)` helper into `training_core.py`, then replace all 4 inline `abs(p - t)` occurrences in `training_manager.py` and `routes/models.py` with calls to it. TDD — tests first, then implementation.

**Tech Stack:** Python, numpy, pytest

---

### Background: The Bug

Arrow dials form a circle: 0.0 → 9.9 → 0.0. The values wrap around with period 10.0. Currently, all regression error calculations use `abs(pred - true)`, which computes the **linear** distance. Example:

- pred=0.1, true=9.9 → `abs(0.1 - 9.9) = 9.8` (WRONG — actual distance is 0.2)
- pred=9.8, true=0.2 → `abs(9.8 - 0.2) = 9.6` (WRONG — actual distance is 0.4)

The fix: `min(abs(p - t), 10.0 - abs(p - t))` — take the shorter path around the circle.

### Bug Locations (4 total)

| # | File | Line | Context |
|---|------|------|---------|
| 1 | `watermeter/training_manager.py` | 1148 | Benchmark: "correct" counting (`< 0.5` threshold) |
| 2 | `watermeter/training_manager.py` | 1172 | Benchmark: error list for MAE/RMSE/within-half/within-one |
| 3 | `watermeter/training_manager.py` | 1186 | Benchmark: per-class MAE computation |
| 4 | `watermeter/routes/models.py` | 490 | Mislabel scan: regression tolerance check |

---

### Task 1: Write unit tests for `circular_error()` helper

**Files:**
- Create: `tests/unit/test_circular_error.py`

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_circular_error.py -v`
Expected: FAIL with `ImportError: cannot import name 'circular_error'`

**Step 3: Commit**

```bash
git add tests/unit/test_circular_error.py
git commit -m "claude: add failing tests for circular_error helper"
```

---

### Task 2: Implement `circular_error()` helper

**Files:**
- Modify: `watermeter/training_core.py` (add function after `regression_predict` at line 278)

**Step 1: Implement the helper**

Add after `regression_predict()` (line 278) in `watermeter/training_core.py`:

```python
def circular_error(pred: float, true: float, period: float = 10.0) -> float:
    """Compute shortest-path error on a circular scale.

    For dial values that wrap around (e.g., 9.9 → 0.0), this returns the
    shorter of the two possible distances around the circle.

    Args:
        pred: Predicted value.
        true: True/expected value.
        period: Full period of the circular scale (default 10.0 for dial 0.0-9.9).

    Returns:
        Shortest circular distance between pred and true.
    """
    diff = abs(pred - true)
    return min(diff, period - diff)
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_circular_error.py -v`
Expected: All 9 tests PASS

**Step 3: Commit**

```bash
git add watermeter/training_core.py
git commit -m "claude: add circular_error helper for dial wraparound"
```

---

### Task 3: Write tests for mislabel scan wraparound

**Files:**
- Modify: `tests/unit/test_mislabel.py` (add test method after `test_arrows_regression_tolerance` around line 140)

**Step 1: Write the failing test**

Add a new test method to the `TestScanMislabeled` class in `tests/unit/test_mislabel.py`. Follow the existing pattern from `test_arrows_regression_tolerance` (line 107):

```python
    def test_arrows_regression_wraparound(self, tmp_path):
        """Regression arrows: wraparound case 9.9 vs 0.1 should NOT be a suspect."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "arrows" / "ground_truth"
        _make_gt_image(gt_base / "9.9", "wrap_close.jpg")
        _make_gt_image(gt_base / "9.9", "wrap_far.jpg")

        mock_regressor = MagicMock(spec=['predict', 'predict_detailed',
                                         'preprocess', 'compiled',
                                         'resolution', 'label_config_tag',
                                         'model_path'])

        def side_effect(path):
            fname = Path(path).name
            if fname == "wrap_close.jpg":
                # 0.1 is within 0.2 of 9.9 circularly -- should NOT be a suspect
                return {'class': '0.1', 'confidence': 0.80}
            else:
                # 7.0 is 2.9 away from 9.9 -- should be a suspect
                return {'class': '7.0', 'confidence': 0.65}

        mock_regressor.predict.side_effect = side_effect
        mock_regressor.resolution = 128

        with patch('watermeter.routes.models.get_inference_service') as mock_svc:
            mock_svc.return_value.arrows_model = mock_regressor
            mock_svc.return_value.digits_model = None
            result = scan_mislabeled("arrows", str(tmp_path))

        # Only the far one should be a suspect
        assert result["total_scanned"] == 2
        assert len(result["suspects"]) == 1
        assert result["suspects"][0]["filename"] == "wrap_far.jpg"
        assert result["suspects"][0]["current_label"] == "9.9"
        assert result["suspects"][0]["predicted_label"] == "7.0"
```

**Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_mislabel.py::TestScanMislabeled::test_arrows_regression_wraparound -v`
Expected: FAIL — `wrap_close.jpg` is wrongly flagged as suspect because `abs(0.1 - 9.9) = 9.8 >= 0.5`

**Step 3: Commit**

```bash
git add tests/unit/test_mislabel.py
git commit -m "claude: add failing test for mislabel scan wraparound"
```

---

### Task 4: Fix mislabel scan — use `circular_error()`

**Files:**
- Modify: `watermeter/routes/models.py:490`

**Step 1: Add import at top of file**

Add `circular_error` to the imports from `training_core` in `watermeter/routes/models.py`. Find the existing import line (should be near the top) and add `circular_error`. If there's no existing import from `training_core`, add:

```python
from watermeter.training_core import circular_error
```

**Step 2: Fix the linear error at line 490**

Replace line 490:
```python
                    is_match = abs(pred_val - folder_val) < 0.5
```
with:
```python
                    is_match = circular_error(pred_val, folder_val) < 0.5
```

**Step 3: Run the wraparound test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_mislabel.py::TestScanMislabeled::test_arrows_regression_wraparound -v`
Expected: PASS

**Step 4: Run all mislabel tests to check for regressions**

Run: `.venv/bin/python -m pytest tests/unit/test_mislabel.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add watermeter/routes/models.py
git commit -m "claude: fix mislabel scan to use circular error for dial wraparound"
```

---

### Task 5: Fix benchmark error calculations — use `circular_error()`

**Files:**
- Modify: `watermeter/training_manager.py:1148,1172,1186`

**Step 1: Add import at top of file**

In `watermeter/training_manager.py`, find the existing imports from `training_core` and add `circular_error`:

```python
from watermeter.training_core import circular_error
```

(There should already be imports from `training_core` — add `circular_error` to the existing import line.)

**Step 2: Fix line 1148 — "correct" counting**

Replace:
```python
                    if abs(pred_value - expected_value) < 0.5:
```
with:
```python
                    if circular_error(pred_value, expected_value) < 0.5:
```

**Step 3: Fix line 1172 — error list**

Replace:
```python
            errors = [abs(p - t) for p, t in all_predictions]
```
with:
```python
            errors = [circular_error(p, t) for p, t in all_predictions]
```

**Step 4: Fix line 1186 — per-class MAE**

Replace:
```python
                class_errors[key].append(abs(pred_val - true_val))
```
with:
```python
                class_errors[key].append(circular_error(pred_val, true_val))
```

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add watermeter/training_manager.py
git commit -m "claude: fix benchmark to use circular error for dial wraparound"
```

---

### Task 6: Update codebase map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Add `circular_error()` to the codebase map**

Find the `training_core.py` section in `docs/codebase_map.md` and add the new function after `regression_predict`:

```markdown
  - `circular_error(pred, true, period=10.0)` — L[line]: Shortest-path error on circular dial scale
```

(Use the actual line number where the function was added.)

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with circular_error helper"
```
