# WatermeterService God Object Refactoring Phase 2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract rate tracking, plausibility checking, and leak detection into focused classes, with RateTracker as the shared state object.

**Architecture:** RateTracker owns the ring buffer (rate_history) and provides an explicit mutation API. LeakDetector and PlausibilityChecker depend on RateTracker for read access. The correction engine receives RateTracker as a read-only dependency. WatermeterService delegates to all three new classes via thin wrappers, keeping existing tests and API routes unchanged. Confirmation handlers continue to mutate rate_history through RateTracker's explicit pop/replace API (Phase 3 will extract confirmation itself).

**Tech Stack:** Python 3.12, pytest

---

## Architecture Details

### Extraction Order (matters due to shared state)

1. **RateTracker** (extract first -- owns the ring buffer)
   - Encapsulates: `rate_history` list, `rate_history_size`, `_add_to_rate_history()`, `_calculate_average_rate_per_hour()`
   - Provides explicit mutation API: `add(value, timestamp=None)`, `pop_last()`, `replace_last(value, timestamp)`, `reset()`, `seed(value, timestamp)`, `average_rate_per_hour` property, `history` read-only property, `size` property

2. **LeakDetector** (depends on RateTracker)
   - Encapsulates: `_check_sustained_consumption()` method
   - Takes `RateTracker` + config in constructor

3. **PlausibilityChecker** (depends on RateTracker)
   - Encapsulates: `check_consistency()` and `validate_plausibility()`
   - Takes config in constructor
   - `check_consistency` is a pure function (config + predictions only)
   - `validate_plausibility` receives `previous_value`, `last_update_time`, and `rate_tracker` as parameters

### Design Decisions

- `previous_value` and `last_update_time` stay on WatermeterService (Phase 3 MeterState candidates)
- `check_consistency` could be standalone but PlausibilityChecker groups it logically
- Correction engine's dependency on rate_tracker is read-only (`_estimate_expected_range` only reads `average_rate_per_hour`) -- receives rate_tracker but never mutates
- Confirmation handlers mutate rate_history via `rate_tracker.pop_last()` / `rate_tracker.replace_last()` -- these stay on WatermeterService (Phase 3)
- `set_manual_value` uses `rate_tracker.seed()` to reset + add one entry
- `reset_previous_value` uses `rate_tracker.reset()` to clear all entries
- `reload_config` syncs `rate_tracker.max_size` and `leak_detector.config` / `plausibility_checker.config`

### All rate_history Touchpoints (current code)

| Location | Line | Operation | Phase 2 Migration |
|----------|------|-----------|-------------------|
| `__init__` | L293-294 | Initialize `rate_history=[]`, `rate_history_size` | `self._rate_tracker = RateTracker(size)` |
| `validate_plausibility` | L554 | `_add_to_rate_history(new_value)` on first reading | `rate_tracker.add(new_value)` inside PlausibilityChecker |
| `validate_plausibility` | L583-584 | `len(self.rate_history) >= 2`, `_calculate_average_rate_per_hour()` | `rate_tracker.average_rate_per_hour` |
| `_add_to_rate_history` | L603-608 | Append + trim | Becomes `RateTracker.add()` |
| `_calculate_average_rate_per_hour` | L610-625 | Compute from history endpoints | Becomes `RateTracker.average_rate_per_hour` |
| `_check_sustained_consumption` | L642-645 | Read `self.rate_history` tail | `rate_tracker.history` |
| `_do_confirmation_timeout` | L812-813 | `self.rate_history.pop()` | `rate_tracker.pop_last()` |
| `_handle_confirmation_response` reject | L880-881 | `self.rate_history.pop()` | `rate_tracker.pop_last()` |
| `_handle_confirmation_response` correct | L914-915 | `self.rate_history[-1] = (val, ts)` | `rate_tracker.replace_last(val, ts)` |
| `_estimate_expected_range` | L972 | `len(self.rate_history) < 3` | `len(rate_tracker) < 3` |
| `_estimate_expected_range` | L974 | `_calculate_average_rate_per_hour()` | `rate_tracker.average_rate_per_hour` |
| `process_reading` | L1386 | `self._add_to_rate_history(total_value)` | `rate_tracker.add(total_value)` |
| `publish_to_mqtt` | L1566 | `self._calculate_average_rate_per_hour()` | `rate_tracker.average_rate_per_hour` |
| `reset_previous_value` | L1604 | `self.rate_history = []` | `rate_tracker.reset()` |
| `set_manual_value` | L1643 | `self.rate_history = [(value, now)]` | `rate_tracker.seed(value, now)` |
| `reload_config` | L1970 | Sync `rate_history_size` | `rate_tracker.max_size = new_size` |

---

## Task 1: Extract RateTracker class

**Agent:** senior-dev
**Files:**
- Create: `watermeter/rate_tracker.py`
- Create: `tests/unit/test_rate_tracker.py`
- Modify: `watermeter/watermeter_service.py`

### Step 1: Write tests

Create `tests/unit/test_rate_tracker.py`:

```python
"""Unit tests for RateTracker — ring buffer for meter reading rate history."""

from datetime import datetime, timedelta

import pytest

from watermeter.rate_tracker import RateTracker


class TestRateTrackerInit:
    """Test initialization and basic properties."""

    def test_default_size(self):
        rt = RateTracker()
        assert rt.max_size == 5
        assert len(rt) == 0
        assert rt.history == []

    def test_custom_size(self):
        rt = RateTracker(max_size=10)
        assert rt.max_size == 10

    def test_history_is_readonly_copy(self):
        rt = RateTracker()
        rt.add(100.0)
        history = rt.history
        history.append((999.0, datetime.now()))
        assert len(rt) == 1  # original unchanged


class TestRateTrackerAdd:
    """Test add() method."""

    def test_add_single(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        assert len(rt) == 1
        assert rt.history[0][0] == 100.0

    def test_add_with_explicit_timestamp(self):
        rt = RateTracker(max_size=5)
        ts = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, ts)
        assert rt.history[0] == (100.0, ts)

    def test_add_auto_timestamp(self):
        rt = RateTracker(max_size=5)
        before = datetime.now()
        rt.add(100.0)
        after = datetime.now()
        ts = rt.history[0][1]
        assert before <= ts <= after

    def test_add_trims_to_max_size(self):
        rt = RateTracker(max_size=3)
        for i in range(5):
            rt.add(float(i))
        assert len(rt) == 3
        # Should keep the last 3: 2.0, 3.0, 4.0
        values = [v for v, _ in rt.history]
        assert values == [2.0, 3.0, 4.0]


class TestRateTrackerMutations:
    """Test pop_last, replace_last, reset, seed."""

    def test_pop_last(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        rt.pop_last()
        assert len(rt) == 1
        assert rt.history[0][0] == 100.0

    def test_pop_last_empty(self):
        """pop_last on empty tracker is a no-op."""
        rt = RateTracker(max_size=5)
        rt.pop_last()  # should not raise
        assert len(rt) == 0

    def test_replace_last(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        ts = datetime(2026, 2, 16, 14, 0)
        rt.replace_last(125.0, ts)
        assert len(rt) == 1
        assert rt.history[0] == (125.0, ts)

    def test_replace_last_empty(self):
        """replace_last on empty tracker is a no-op."""
        rt = RateTracker(max_size=5)
        rt.replace_last(125.0, datetime.now())  # should not raise
        assert len(rt) == 0

    def test_reset(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        rt.reset()
        assert len(rt) == 0
        assert rt.history == []

    def test_seed(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        rt.add(101.0)
        ts = datetime(2026, 2, 16, 14, 0)
        rt.seed(200.0, ts)
        assert len(rt) == 1
        assert rt.history[0] == (200.0, ts)


class TestRateTrackerAverageRate:
    """Test average_rate_per_hour property."""

    def test_empty_returns_none(self):
        rt = RateTracker(max_size=5)
        assert rt.average_rate_per_hour is None

    def test_single_entry_returns_none(self):
        rt = RateTracker(max_size=5)
        rt.add(100.0)
        assert rt.average_rate_per_hour is None

    def test_two_entries(self):
        rt = RateTracker(max_size=5)
        base = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, base)
        rt.add(101.0, base + timedelta(hours=1))
        rate = rt.average_rate_per_hour
        assert rate is not None
        assert abs(rate - 1.0) < 1e-6

    def test_steady_rate(self):
        rt = RateTracker(max_size=10)
        base = datetime(2026, 2, 16, 12, 0)
        for i in range(5):
            rt.add(100.0 + i * 0.5, base + timedelta(hours=i))
        # 2.0 m3 over 4 hours = 0.5 m3/h
        rate = rt.average_rate_per_hour
        assert rate is not None
        assert abs(rate - 0.5) < 1e-6

    def test_zero_time_diff_returns_none(self):
        """Two entries at exact same time -> None."""
        rt = RateTracker(max_size=5)
        ts = datetime(2026, 2, 16, 12, 0)
        rt.add(100.0, ts)
        rt.add(101.0, ts)
        assert rt.average_rate_per_hour is None


class TestRateTrackerMaxSize:
    """Test max_size property setter."""

    def test_set_max_size(self):
        rt = RateTracker(max_size=5)
        rt.max_size = 10
        assert rt.max_size == 10

    def test_shrink_max_size_trims(self):
        rt = RateTracker(max_size=10)
        for i in range(8):
            rt.add(float(i))
        rt.max_size = 3
        assert len(rt) == 3
        values = [v for v, _ in rt.history]
        assert values == [5.0, 6.0, 7.0]
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_rate_tracker.py -v`
**Expected:** FAIL with `ModuleNotFoundError: No module named 'watermeter.rate_tracker'`

### Step 2: Implement RateTracker

Create `watermeter/rate_tracker.py`:

```python
"""Rate history ring buffer for watermeter plausibility and leak detection.

Encapsulates the list of (value, timestamp) tuples that was previously
spread across WatermeterService as `rate_history`, `rate_history_size`,
`_add_to_rate_history()`, and `_calculate_average_rate_per_hour()`.
"""

from datetime import datetime
from typing import List, Optional, Tuple


class RateTracker:
    """Fixed-size ring buffer of (value, timestamp) meter readings.

    Provides explicit mutation methods so callers cannot accidentally
    corrupt the history list.

    Args:
        max_size: Maximum number of entries to retain. Oldest entries
            are dropped when the buffer is full.  Defaults to 5.
    """

    def __init__(self, max_size: int = 5) -> None:
        self._history: List[Tuple[float, datetime]] = []
        self._max_size = max_size

    # -- Properties ----------------------------------------------------------

    @property
    def history(self) -> List[Tuple[float, datetime]]:
        """Return a shallow copy of the history list (read-only view)."""
        return list(self._history)

    @property
    def max_size(self) -> int:
        """Maximum number of entries retained."""
        return self._max_size

    @max_size.setter
    def max_size(self, value: int) -> None:
        self._max_size = value
        self._trim()

    @property
    def average_rate_per_hour(self) -> Optional[float]:
        """Calculate average rate per hour from oldest to newest entry.

        Returns None if fewer than 2 entries or zero time span.
        """
        if len(self._history) < 2:
            return None

        oldest_value, oldest_time = self._history[0]
        newest_value, newest_time = self._history[-1]

        time_diff = (newest_time - oldest_time).total_seconds()
        if time_diff <= 0:
            return None

        value_diff = newest_value - oldest_value
        return (value_diff / time_diff) * 3600

    # -- Mutation API --------------------------------------------------------

    def add(self, value: float, timestamp: Optional[datetime] = None) -> None:
        """Append a reading and trim to max_size.

        Args:
            value: Meter reading value.
            timestamp: When the reading was taken.  Defaults to now.
        """
        if timestamp is None:
            timestamp = datetime.now()
        self._history.append((value, timestamp))
        self._trim()

    def pop_last(self) -> None:
        """Remove the most recent entry (e.g. on reject/timeout)."""
        if self._history:
            self._history.pop()

    def replace_last(self, value: float, timestamp: datetime) -> None:
        """Replace the most recent entry (e.g. on user correction)."""
        if self._history:
            self._history[-1] = (value, timestamp)

    def reset(self) -> None:
        """Clear all history entries."""
        self._history.clear()

    def seed(self, value: float, timestamp: datetime) -> None:
        """Clear history and start fresh with a single entry.

        Used by manual value set to establish a known baseline.
        """
        self._history.clear()
        self._history.append((value, timestamp))

    # -- Dunder methods ------------------------------------------------------

    def __len__(self) -> int:
        return len(self._history)

    def __repr__(self) -> str:
        return f"RateTracker(max_size={self._max_size}, entries={len(self._history)})"

    # -- Internal ------------------------------------------------------------

    def _trim(self) -> None:
        """Drop oldest entries if over max_size."""
        if len(self._history) > self._max_size:
            self._history = self._history[-self._max_size:]
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_rate_tracker.py -v`
**Expected:** All PASS

### Step 3: Wire RateTracker into WatermeterService

In `watermeter/watermeter_service.py`:

1. Add import at top (line ~27):
```python
from .rate_tracker import RateTracker
```

2. In `__init__` (around L292-294), replace:
```python
        # Rate history for plausibility checks (list of (value, timestamp) tuples)
        self.rate_history: List[Tuple[float, datetime]] = []
        self.rate_history_size = self.config["plausibility"].get("rate_history_size", 5)
```
with:
```python
        # Rate history for plausibility checks
        self._rate_tracker = RateTracker(
            max_size=self.config["plausibility"].get("rate_history_size", 5)
        )
```

3. Add backward-compatible properties right after `__init__` (before the image pipeline delegation section):
```python
    # -- Rate tracker backward-compatible properties -------------------------
    # These properties keep existing code (confirmation handlers, tests that
    # set service.rate_history directly) working during the migration.
    # Phase 3 will remove these when confirmation is extracted.

    @property
    def rate_history(self) -> list:
        return self._rate_tracker._history

    @rate_history.setter
    def rate_history(self, value: list) -> None:
        self._rate_tracker._history = value

    @property
    def rate_history_size(self) -> int:
        return self._rate_tracker.max_size

    @rate_history_size.setter
    def rate_history_size(self, value: int) -> None:
        self._rate_tracker.max_size = value
```

4. Replace `_add_to_rate_history` method (L603-608):
```python
    def _add_to_rate_history(self, value: float) -> None:
        """Add a reading to rate history."""
        self._rate_tracker.add(value)
```

5. Replace `_calculate_average_rate_per_hour` method (L610-625):
```python
    def _calculate_average_rate_per_hour(self) -> Optional[float]:
        """Calculate average rate per hour from history."""
        return self._rate_tracker.average_rate_per_hour
```

6. In `_do_confirmation_timeout` (L812-813), replace:
```python
        if self.rate_history:
            self.rate_history.pop()
```
with:
```python
        self._rate_tracker.pop_last()
```

7. In `_handle_confirmation_response` reject branch (L880-881), replace:
```python
            if self.rate_history:
                self.rate_history.pop()
```
with:
```python
            self._rate_tracker.pop_last()
```

8. In `_handle_confirmation_response` correct branch (L914-915), replace:
```python
            if self.rate_history:
                self.rate_history[-1] = (corrected_value, self.last_update_time)
```
with:
```python
            self._rate_tracker.replace_last(corrected_value, self.last_update_time)
```

9. In `reset_previous_value` (L1604), replace:
```python
        self.rate_history = []
```
with:
```python
        self._rate_tracker.reset()
```

10. In `set_manual_value` (L1643), replace:
```python
        self.rate_history = [(value, now)]
```
with:
```python
        self._rate_tracker.seed(value, now)
```

11. In `reload_config` (L1970), replace:
```python
        self.rate_history_size = new_config.get("plausibility", {}).get("rate_history_size", 5)
```
with:
```python
        self._rate_tracker.max_size = new_config.get("plausibility", {}).get("rate_history_size", 5)
```

**Run:** `.venv/bin/python -m pytest tests/ -x -q`
**Expected:** All 351 tests PASS. The backward-compatible `rate_history` and `rate_history_size` properties ensure all existing tests that set `svc.rate_history = [...]` or read `svc.rate_history` continue working unchanged.

### Step 4: Commit

```bash
git add watermeter/rate_tracker.py tests/unit/test_rate_tracker.py watermeter/watermeter_service.py
git commit -m "claude: extract RateTracker class from rate_history management (BL-30 Phase 2)"
```

---

## Task 2: Extract LeakDetector class

**Agent:** junior-dev
**Files:**
- Create: `watermeter/leak_detector.py`
- Create: `tests/unit/test_leak_detector.py`
- Modify: `watermeter/watermeter_service.py`

### Step 1: Write tests

Create `tests/unit/test_leak_detector.py`:

```python
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
        assert "m\u00b3/h" in result

    def test_warning_clears_after_low_rate_reading(self, detector, rate_tracker):
        base = datetime(2026, 2, 14, 12, 0)
        rate_tracker.add(100.000, base)
        rate_tracker.add(100.020, base + timedelta(minutes=5))
        rate_tracker.add(100.040, base + timedelta(minutes=10))
        rate_tracker.add(100.060, base + timedelta(minutes=15))
        assert detector.check() is not None

        rate_tracker.add(100.061, base + timedelta(minutes=20))
        assert detector.check() is None
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_leak_detector.py -v`
**Expected:** FAIL with `ModuleNotFoundError: No module named 'watermeter.leak_detector'`

### Step 2: Implement LeakDetector

Create `watermeter/leak_detector.py`:

```python
"""Leak detection via sustained consumption monitoring.

Extracted from WatermeterService._check_sustained_consumption().
Checks whether the last N consecutive readings all show a consumption
rate above a configurable threshold.
"""

import logging
from typing import Optional

from .rate_tracker import RateTracker

logger = logging.getLogger(__name__)


class LeakDetector:
    """Detects sustained consumption that may indicate a leak.

    Args:
        rate_tracker: Shared RateTracker instance for reading history.
        config: Full application config dict (reads plausibility section).
    """

    def __init__(self, rate_tracker: RateTracker, config: dict) -> None:
        self._rate_tracker = rate_tracker
        self.config = config

    def check(self) -> Optional[str]:
        """Check if the last N consecutive readings all show rate above threshold.

        Returns:
            Warning message string if sustained consumption detected, None otherwise.
        """
        plausibility_config = self.config["plausibility"]

        if not plausibility_config.get("enable_leak_detection", True):
            return None

        threshold = plausibility_config.get("sustained_rate_threshold", 0.05)
        min_readings = plausibility_config.get("sustained_rate_readings", 3)

        history = self._rate_tracker.history
        if len(history) < min_readings + 1:
            return None

        tail = history[-(min_readings + 1):]

        for i in range(len(tail) - 1):
            val_prev, ts_prev = tail[i]
            val_curr, ts_curr = tail[i + 1]

            time_diff_s = (ts_curr - ts_prev).total_seconds()
            if time_diff_s <= 0:
                return None

            rate_per_hour = ((val_curr - val_prev) / time_diff_s) * 3600

            if rate_per_hour < threshold:
                return None

        total_time_s = (tail[-1][1] - tail[0][1]).total_seconds()
        total_time_min = total_time_s / 60
        avg_rate = ((tail[-1][0] - tail[0][0]) / total_time_s) * 3600

        return (
            f"Sustained consumption: {avg_rate:.3f} m\u00b3/h over {total_time_min:.0f} min "
            f"({min_readings} consecutive readings above {threshold} m\u00b3/h)"
        )
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_leak_detector.py -v`
**Expected:** All PASS

### Step 3: Wire LeakDetector into WatermeterService

In `watermeter/watermeter_service.py`:

1. Add import at top:
```python
from .leak_detector import LeakDetector
```

2. In `__init__`, after `self._rate_tracker` creation (around L300), add:
```python
        # Leak detection
        self._leak_detector = LeakDetector(
            rate_tracker=self._rate_tracker,
            config=self.config,
        )
```

3. Replace `_check_sustained_consumption` method body (L627-667) with delegation:
```python
    def _check_sustained_consumption(self) -> Optional[str]:
        """Check if the last N consecutive readings all show rate above threshold."""
        return self._leak_detector.check()
```

4. In `reload_config`, add after the `_rate_tracker.max_size` line:
```python
        self._leak_detector.config = new_config
```

**Run:** `.venv/bin/python -m pytest tests/ -x -q`
**Expected:** All 351 tests PASS. The existing `test_leak_detection.py` tests still work because they call `service._check_sustained_consumption()` which delegates to the new class, and they set `service.rate_history` which works through the backward-compatible property.

### Step 4: Commit

```bash
git add watermeter/leak_detector.py tests/unit/test_leak_detector.py watermeter/watermeter_service.py
git commit -m "claude: extract LeakDetector class from _check_sustained_consumption (BL-30 Phase 2)"
```

---

## Task 3: Extract PlausibilityChecker class

**Agent:** senior-dev
**Files:**
- Create: `watermeter/plausibility.py`
- Create: `tests/unit/test_plausibility.py`
- Modify: `watermeter/watermeter_service.py`

### Step 1: Write tests

Create `tests/unit/test_plausibility.py`:

```python
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
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.9, 'model': 'digits'},
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
            'digit_3': {'id': 'digit_3', 'class': '5', 'confidence': 0.9, 'model': 'digits'},
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
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_plausibility.py -v`
**Expected:** FAIL with `ModuleNotFoundError: No module named 'watermeter.plausibility'`

### Step 2: Implement PlausibilityChecker

Create `watermeter/plausibility.py`:

```python
"""Plausibility checking for watermeter readings.

Extracted from WatermeterService.check_consistency() and
WatermeterService.validate_plausibility().
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .position_utils import get_position_ids
from .rate_tracker import RateTracker

logger = logging.getLogger(__name__)


class PlausibilityChecker:
    """Validates meter readings for consistency and plausibility.

    Args:
        config: Full application config dict.
        rate_tracker: Shared RateTracker instance for rate-based checks.
    """

    def __init__(self, config: dict, rate_tracker: RateTracker) -> None:
        self.config = config
        self._rate_tracker = rate_tracker

    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """Check consistency between adjacent positions.

        A position with fractional part >= 0.4 (representing .5) should have
        the next position's integer part >= 5 (upper half of dial).

        Args:
            predictions: Dict of prediction results keyed by position ID.

        Returns:
            List of warning messages.
        """
        warnings = []

        if not self.config["plausibility"].get("enable_consistency_check", True):
            return warnings

        digit_ids, arrow_ids = get_position_ids(self.config)
        all_ids = digit_ids + arrow_ids

        all_values = []
        for image_id in all_ids:
            if image_id in predictions:
                pred = predictions[image_id]
                if pred["class"] not in ["NAN", "ERROR"]:
                    if pred["model"] == "digits":
                        all_values.append((image_id, int(pred["class"])))
                    else:
                        all_values.append((image_id, float(pred["class"])))

        for i in range(len(all_values) - 1):
            current_id, current_val = all_values[i]
            next_id, next_val = all_values[i + 1]

            current_frac = current_val % 1
            current_has_half = current_frac >= 0.4

            next_int_part = int(next_val)
            next_is_upper_half = next_int_part >= 5

            if current_has_half != next_is_upper_half:
                msg = f"{current_id}={current_val} (half={current_has_half}) vs {next_id}={next_val} (upper={next_is_upper_half})"
                warnings.append(msg)
                logger.warning(f"Consistency check: {msg}")

        return warnings

    def validate_plausibility(
        self,
        new_value: float,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
    ) -> Tuple[bool, List[str]]:
        """Validate plausibility of a new reading.

        Args:
            new_value: New meter reading.
            previous_value: Last accepted meter reading, or None.
            last_update_time: Timestamp of last accepted reading, or None.

        Returns:
            (is_valid, warnings) tuple.
        """
        warnings = []
        config = self.config["plausibility"]

        if previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._rate_tracker.add(new_value)
            return True, warnings

        # Reverse detection
        if config["enable_reverse_detection"]:
            if new_value < previous_value:
                msg = f"Reverse detected: {previous_value:.4f} \u2192 {new_value:.4f}"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m\u00b3 (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                return False, warnings

            # Rate per hour - check against history if available
            if last_update_time:
                time_diff = (datetime.now() - last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config["max_rate_per_hour"]:
                        if len(self._rate_tracker) >= 2:
                            avg_rate = self._rate_tracker.average_rate_per_hour
                            if avg_rate is not None and avg_rate > config["max_rate_per_hour"]:
                                msg = f"Rate per hour too high: {rate_per_hour:.2f} m\u00b3/h (avg: {avg_rate:.2f}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.error(msg)
                                return False, warnings
                            else:
                                msg = f"Rate spike: {rate_per_hour:.2f} m\u00b3/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m\u00b3/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        return True, warnings
```

**Run:** `.venv/bin/python -m pytest tests/unit/test_plausibility.py -v`
**Expected:** All PASS

### Step 3: Wire PlausibilityChecker into WatermeterService

In `watermeter/watermeter_service.py`:

1. Add import at top:
```python
from .plausibility import PlausibilityChecker
```

2. In `__init__`, after `self._leak_detector` creation, add:
```python
        # Plausibility checking
        self._plausibility_checker = PlausibilityChecker(
            config=self.config,
            rate_tracker=self._rate_tracker,
        )
```

3. Replace `check_consistency` method body (L485-536) with delegation:
```python
    def check_consistency(self, predictions: Dict[str, Dict]) -> List[str]:
        """Check consistency between adjacent positions."""
        return self._plausibility_checker.check_consistency(predictions)
```

4. Replace `validate_plausibility` method body (L538-601) with delegation:
```python
    def validate_plausibility(self, new_value: float) -> Tuple[bool, List[str]]:
        """Validate plausibility of new reading."""
        return self._plausibility_checker.validate_plausibility(
            new_value=new_value,
            previous_value=self.previous_value,
            last_update_time=self.last_update_time,
        )
```

5. In `reload_config`, add after the `_leak_detector.config` line:
```python
        self._plausibility_checker.config = new_config
```

**Run:** `.venv/bin/python -m pytest tests/ -x -q`
**Expected:** All 351 tests PASS. Existing tests call `service.check_consistency()` and `service.validate_plausibility()` which delegate to the new class. The signature of `validate_plausibility` on WatermeterService stays `(self, new_value)` -- the delegation wrapper passes `previous_value` and `last_update_time` from `self`.

### Step 4: Commit

```bash
git add watermeter/plausibility.py tests/unit/test_plausibility.py watermeter/watermeter_service.py
git commit -m "claude: extract PlausibilityChecker class with check_consistency and validate_plausibility (BL-30 Phase 2)"
```

---

## Task 4: Wire correction engine to use RateTracker

**Agent:** junior-dev
**Files:**
- Modify: `watermeter/watermeter_service.py`

This is a rewiring task, not an extraction. The correction engine stays on WatermeterService (Phase 3 or later), but `_estimate_expected_range` should use `self._rate_tracker` directly instead of the backward-compat properties.

### Step 1: Update _estimate_expected_range

In `watermeter/watermeter_service.py`, replace the `_estimate_expected_range` method (around L970-987):

Replace:
```python
    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
        """Estimate plausible range for next reading based on rate history."""
        if self.previous_value is None or len(self.rate_history) < 3:
            return None
        avg_rate = self._calculate_average_rate_per_hour()
```

With:
```python
    def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
        """Estimate plausible range for next reading based on rate history."""
        if self.previous_value is None or len(self._rate_tracker) < 3:
            return None
        avg_rate = self._rate_tracker.average_rate_per_hour
```

The rest of the method stays the same.

### Step 2: Update publish_to_mqtt

Replace the `avg_rate` line in `publish_to_mqtt` (around L1566):

Replace:
```python
        avg_rate = self._calculate_average_rate_per_hour()
```

With:
```python
        avg_rate = self._rate_tracker.average_rate_per_hour
```

### Step 3: Update _should_request_confirmation

Replace the `avg_rate` line in `_should_request_confirmation` (around L713):

Replace:
```python
            avg_rate = self._calculate_average_rate_per_hour()
```

With:
```python
            avg_rate = self._rate_tracker.average_rate_per_hour
```

### Step 4: Update process_reading

Replace in `process_reading` (around L1386):

Replace:
```python
                    self._add_to_rate_history(total_value)
```

With:
```python
                    self._rate_tracker.add(total_value)
```

### Step 5: Remove delegation wrappers

Now that all internal callers use `self._rate_tracker` directly, remove the thin delegation methods:

- Remove `_add_to_rate_history` method entirely
- Remove `_calculate_average_rate_per_hour` method entirely

Also update `validate_plausibility` in PlausibilityChecker (`watermeter/plausibility.py`) -- it already uses `self._rate_tracker.add()` directly, so no change needed there.

**Note:** Keep the backward-compatible `rate_history` and `rate_history_size` properties on WatermeterService. These are needed by existing tests that set `svc.rate_history = [...]` directly (test_leak_detection.py, test_value_correction.py, test_confirmation.py). They will be removed in Phase 3 when those tests are updated.

**Run:** `.venv/bin/python -m pytest tests/ -x -q`
**Expected:** All 351 tests PASS. No external code calls `_add_to_rate_history` or `_calculate_average_rate_per_hour` (both were private). The backward-compat properties keep test fixtures working.

### Step 6: Commit

```bash
git add watermeter/watermeter_service.py
git commit -m "claude: rewire correction engine and MQTT to use RateTracker directly (BL-30 Phase 2)"
```

---

## Task 5: Update codebase map and backlog

**Agent:** junior-dev
**Files:**
- Modify: `docs/codebase_map.md`
- Modify: `backlog.md`

### Step 1: Update codebase map

Add new sections to `docs/codebase_map.md` after the `position_utils.py` section:

```markdown
### `rate_tracker.py` (105 lines) -- Rate history ring buffer

**class `RateTracker`** L18:
- `__init__(max_size)` L18
- Properties: `history` L23, `max_size` L28, `average_rate_per_hour` L37
- Mutation: `add(value, timestamp)` L54, `pop_last()` L64, `replace_last(value, timestamp)` L69, `reset()` L74, `seed(value, timestamp)` L79

### `leak_detector.py` (70 lines) -- Sustained consumption (leak) detection

**class `LeakDetector`** L16:
- `__init__(rate_tracker, config)` L24
- `check()` L27 -- returns warning string or None

### `plausibility.py` (120 lines) -- Consistency and plausibility checking

**class `PlausibilityChecker`** L17:
- `__init__(config, rate_tracker)` L25
- `check_consistency(predictions)` L30 -- adjacent half/upper check
- `validate_plausibility(new_value, previous_value, last_update_time)` L73 -- reverse, rate limit, rate spike
```

Update the `watermeter_service.py` section to reflect removed methods:
- Remove `_add_to_rate_history`, `_calculate_average_rate_per_hour` from the Rate/leak line
- Update Rate/leak line to: `_check_sustained_consumption()` L627 (delegates to LeakDetector)
- Update Validation line to: `check_consistency()` L485 (delegates to PlausibilityChecker), `validate_plausibility()` L538 (delegates to PlausibilityChecker)
- Add note about `_rate_tracker`, `_leak_detector`, `_plausibility_checker` attributes

### Step 2: Update backlog

Update BL-30 in `backlog.md`:

Replace the Phase 1 note:
```
  - Phase 1 done: image_pipeline, low_confidence, scheduling, position_utils extracted
  - Phase 2 planned: plausibility, correction
  - Phase 3 planned: confirmation, mqtt, manual_control
```

With:
```
  - Phase 1 done: image_pipeline, low_confidence, scheduling, position_utils extracted
  - Phase 2 done: rate_tracker, leak_detector, plausibility extracted; correction rewired
  - Phase 3 planned: confirmation, mqtt, manual_control, MeterState
```

### Step 3: Commit

```bash
git add docs/codebase_map.md backlog.md
git commit -m "claude: update codebase map and backlog for Phase 2 extractions (BL-30)"
```

---

## Summary

| Task | New File | Tests | Lines Extracted |
|------|----------|-------|-----------------|
| 1. RateTracker | `watermeter/rate_tracker.py` | `tests/unit/test_rate_tracker.py` | ~40 lines (init + 2 methods) |
| 2. LeakDetector | `watermeter/leak_detector.py` | `tests/unit/test_leak_detector.py` | ~40 lines (1 method) |
| 3. PlausibilityChecker | `watermeter/plausibility.py` | `tests/unit/test_plausibility.py` | ~115 lines (2 methods) |
| 4. Rewire correction | (modify only) | (existing tests) | 0 (rewiring only) |
| 5. Docs | (modify only) | N/A | N/A |

**Expected net reduction in watermeter_service.py:** ~195 lines removed, ~25 lines added (delegation + properties) = ~170 lines net reduction.

**Backward compatibility:** All 351 existing tests continue to pass at every step. The `rate_history` and `rate_history_size` backward-compatible properties on WatermeterService keep test fixtures that directly set `svc.rate_history = [...]` working. These properties will be removed in Phase 3 when test fixtures are updated to use RateTracker directly.
