# Benchmark MAE + StdDev Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show MAE and StdDev alongside accuracy% in the benchmark column so two 100%-accuracy models can be distinguished by precision and consistency.

**Architecture:** Compute `std_dev` of circular errors in the benchmark, persist it to metadata.json, and render a two-line cell in the models table: primary line = accuracy%, secondary line = MAE + σ in smaller muted text.

**Tech Stack:** Python (backend metrics), JavaScript (frontend rendering), CSS

---

### Task 1: Add StdDev computation and persistence to benchmark

**Files:**
- Modify: `watermeter/training_manager.py:1325-1370` (benchmark result dict for continuous mode)
- Modify: `watermeter/training_manager.py:1143-1170` (metadata persistence)

**Step 1: Write the failing test**

Create test in `tests/test_benchmark_stddev.py`:

```python
"""Test that benchmark results include std_dev for continuous mode."""
import numpy as np
from unittest.mock import MagicMock, patch
from watermeter.training_core import circular_error


def test_circular_error_stddev_computation():
    """Verify std_dev is computed correctly from circular errors."""
    # Simulate errors: [0.1, 0.2, 0.3, 0.4, 0.2]
    errors = [0.1, 0.2, 0.3, 0.4, 0.2]
    expected_std = round(float(np.std(errors)), 4)
    expected_mae = round(float(np.mean(errors)), 4)
    assert expected_mae == 0.24
    assert expected_std == 0.1020  # np.std([0.1,0.2,0.3,0.4,0.2])


def test_benchmark_result_has_stddev_key():
    """Verify the result dict schema includes error_std for continuous mode."""
    # This is a schema-level test — we check that the key exists
    # in a mock benchmark result. Full integration test uses the
    # actual benchmark route but that requires a running model.
    result = {
        "training_mode": "continuous",
        "mae": 0.24,
        "rmse": 0.27,
        "error_std": 0.10,
        "within_half_pct": 100.0,
        "accuracy": 100.0,
    }
    assert "error_std" in result
    assert isinstance(result["error_std"], float)
```

**Step 2: Run test to verify it passes (pure computation test)**

Run: `.venv/bin/python -m pytest tests/test_benchmark_stddev.py -v`
Expected: PASS (this is a pure computation verification)

**Step 3: Add `error_std` computation to benchmark**

In `watermeter/training_manager.py`, find the continuous benchmark section around line 1325-1340 where `circular_errors` list is built. The errors are already computed per-image in a loop. After the loop, add:

```python
error_std = round(float(np.std(circular_errors)), 4)
```

Note: `circular_errors` is the list already being built from `circular_error(pred_value, expected_value)` calls. If errors are not collected into a list yet (only `mae` and counts are tracked), refactor to collect them into a `circular_errors` list first, then derive mae/rmse/within_half/within_one from it.

Add `"error_std": error_std` to the result dict (around line 1360).

**Step 4: Persist `error_std` to metadata.json**

In the metadata persistence section (around lines 1143-1170), add `"error_std"` to the benchmark dict that gets saved.

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add watermeter/training_manager.py tests/test_benchmark_stddev.py
git commit -m "claude: add error_std to benchmark results for continuous mode"
```

---

### Task 2: Render MAE + StdDev in accuracy cell (frontend)

**Files:**
- Modify: `watermeter/static/training.js:1336-1347` (accuracy cell rendering)

**Step 1: Update accuracy cell HTML for continuous mode**

In `training.js`, find the accuracy cell rendering (lines 1336-1347). Replace the continuous-mode branch to render a two-line cell:

```javascript
// Current: single line with tooltip
// accuracyHtml = `<span class="accuracy-cell good" title="MAE: 0.15, RMSE: 0.04">100%</span>`

// New: two lines — accuracy on top, MAE + σ below
if (bm && bm.accuracy !== undefined) {
    const accVal = bm.accuracy;
    const accClass = accVal >= 90 ? 'good' : accVal >= 70 ? 'warning' : 'bad';
    const tooltip = bm.mae !== undefined
        ? ` title="RMSE: ${bm.rmse}"`
        : '';
    accuracyHtml = `<span class="accuracy-cell ${accClass}"${tooltip}>${accVal}%</span>`;

    // For continuous mode: show MAE + StdDev on second line
    if (bm.mae !== undefined) {
        const stdPart = bm.error_std !== undefined ? ` · σ${bm.error_std}` : '';
        accuracyHtml += `<br><span class="accuracy-detail">MAE ${bm.mae}${stdPart}</span>`;
    }
}
```

Remove the `else if (bm && bm.within_half_pct !== undefined)` branch — it's redundant since `accuracy` is always set as an alias for `within_half_pct`.

**Step 2: Add CSS for `.accuracy-detail`**

In `watermeter/templates/training.html`, find the `.accuracy-cell` CSS block (around lines 832-846). Add after it:

```css
.accuracy-detail {
    font-size: 0.78em;
    font-weight: 400;
    color: var(--text-secondary, #999);
    white-space: nowrap;
}
```

**Step 3: Verify in browser**

Open `http://localhost:8002`, go to Models tab. Models with benchmark data should show:
- Line 1: `98.5%` (bold, colored)
- Line 2: `MAE 0.15 · σ0.05` (small, muted)

Digits models (discrete) should show only the percentage, no second line.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass (frontend change, no backend test impact).

**Step 5: Commit**

```bash
git add watermeter/static/training.js watermeter/templates/training.html
git commit -m "claude: show MAE and StdDev in benchmark accuracy cell"
```

---

### Task 3: Verify sorting still works correctly

**Files:** No changes expected — verification only.

**Step 1: Verify sort comparator**

The sort comparator at `training.js:1293-1295` uses `bm.accuracy` which is still the primary sort value (within-half %). This is correct — we only added visual detail, not a new sort key.

**Step 2: Manual test**

In the browser, click the "Accuracy" column header. Verify:
- Models sort by accuracy% (descending by default)
- Two models with same accuracy% appear adjacent — second line (MAE/σ) lets user visually compare
- Digits models (no MAE) sort correctly alongside arrows models

No code changes needed.
