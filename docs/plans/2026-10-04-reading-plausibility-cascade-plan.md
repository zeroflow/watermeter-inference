# Reading Plausibility & Cascaded Total — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop readings getting stuck behind a wrong baseline, and compute the meter total carry-aware, so that small backward jitter, rolled-over dials and `NAN` digits no longer produce rejected or wrong readings.

**Architecture:**
- `position_utils.calculate_total` becomes a carry-aware cascade (finest dial → coarsest → digits). It uses the last accepted value as context and returns `None` when a reading cannot be resolved.
- `PlausibilityChecker` gains a structured `evaluate()` with a jitter band and a re-anchoring tracker (`reanchor.py`).
- `WatermeterService` adopts the baseline `evaluate()` returns, and clamps everything it publishes to a persisted high-water mark, because HA treats `water_usage` as `total_increasing`.

**Tech Stack:** Python 3.10+, pytest, jsonschema (config schema), ruamel.yaml (config). No new dependencies.

**Spec:** `docs/plans/2026-10-04-reading-plausibility-cascade-design.md`

## Global Constraints

- Production (port 8001, container `watermeter-dashboard-prod`): never touch it. Validate only on the debug container (port 8002).
- Python style: Black, line length 120; `uvx ruff check watermeter/ tests/` must pass.
- Black is applied **only to files this plan creates or that were black-clean at HEAD**. Several repo files are not black-clean, so never run `uvx black watermeter/ tests/` repo-wide. Check first with `git show HEAD:<file> | uvx black -q --check -`.
- New config fields go into the `config_utils.py` schema and the shipped `config.yaml`, with comments.
- Defaults:

  | Field | Default |
  |---|---|
  | `plausibility.reverse_tolerance` | `0.002` |
  | `plausibility.reanchor_after` | `6` (`0` disables) |
  | `plausibility.reanchor_max_spread` | `0.01` |
  | `ROLL_WINDOW` (constant) | `0.2` |
  | `PAIR_INCONSISTENCY` (constant) | `0.35` (diagnostic note only, never rejects) |

- `NAN` is never counted as 0. An `ERROR` arrow or digit makes the reading invalid.
- `water_usage` published to HA never decreases, except through `/api/reset` or a manual set.
- Commits: message prefix `claude: `, end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit to `main`; push to `origin` and `gitea` at the end.
- Run tests with `.venv/bin/python -m pytest tests/unit tests/regression -q`. `tests/unit/test_mqtt_reconnect.py` is a known pre-existing flake; it fails intermittently on HEAD too.

## Review Focus

1. **Digits-only meter (no arrows).** The last digit stepping 5→6 must be accepted as 6, not held at the previous integer. Test in Task 3.
2. **Wrong high baseline, upward "correction".** P = 57.0 (wrong), true reading 56.95 with D = 056: the total must be 56.95, so re-anchoring can fire, not 57.95. Test in Task 3.
3. **Counter overflow.** P = 999.95 with arrows wrapping, D = `000` on a 3-digit meter: the total must be 0.0x (I mod 1000), not 1000.0x. Test in Task 3.
4. **Unresolvable reading after a long stuck period.** The `STUCK` warning message must not crash when the total is `None` (it formats with `:.4f` today). Test in Task 6.
5. **Container restart during a re-anchor hold.** After a restart, the first publish must still be ≥ the old peak, because the high-water mark is persisted. Test in Task 5.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `watermeter/reanchor.py` | Create | `ReanchorTracker`: pure detector for N consistent lower readings |
| `watermeter/plausibility.py` | Modify | `PlausibilityResult`; `evaluate()` with jitter band and re-anchoring; `validate_plausibility()` becomes a wrapper |
| `watermeter/position_utils.py` | Modify | `resolve_arrows()`, `resolve_digits()`, carry-aware `calculate_total(config, predictions, previous_value=None)`, which also returns `raw_total` and `notes` |
| `watermeter/correction.py` | Modify | `_recalculate_with_replacement` delegates to `calculate_total` (no previous value) |
| `watermeter/confirmation.py` | Modify | `_compute_raw_total` reads `raw_values["raw_total"]` |
| `watermeter/persistence.py` | Modify | `StateStore.save(..., published_value=None)`, `load_published_value()` |
| `watermeter/meter_state.py` | Modify | `published_high_water` field |
| `watermeter/watermeter_service.py` | Modify | Previous value into `calculate_total`; `evaluate_plausibility`; adopt the baseline; handle an unresolvable total; high-water clamp in `publish_to_mqtt`; reset/manual set |
| `watermeter/oneshot.py` | Modify | Exit 1 when the total is unresolvable |
| `watermeter/config_utils.py`, `config.yaml` | Modify | New plausibility fields |
| `scripts/replay_plausibility.py` | Create | Replay logged totals through old vs new plausibility |
| `scripts/replay_cascade.py` | Create | Replay archived frames: floor vs cascade totals (run in the debug container) |
| `backlog.md`, `CLAUDE.md` | Modify | Backlog items for deferred work; correct the "sin/cos regressor" claim |

---

### Task 1: ReanchorTracker

**Files:**
- Create: `watermeter/reanchor.py`
- Test: `tests/unit/test_reanchor.py`

**Interfaces:**
- Produces:
  - `ReanchorTracker(required: int, max_spread: float, tolerance: float)`
  - `.add(value: float) -> bool`
  - `.reset() -> None`
  - `.required: int`
  - `.values: list[float]` (read-only use in tests)

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for ReanchorTracker — detects N consistent lower readings."""

from watermeter.reanchor import ReanchorTracker


def make(required=3, max_spread=0.01, tolerance=0.002):
    return ReanchorTracker(required=required, max_spread=max_spread, tolerance=tolerance)


def test_fires_after_required_consistent_values():
    t = make()
    assert t.add(56.5903) is False
    assert t.add(56.5903) is False
    assert t.add(56.5904) is True


def test_spread_beyond_max_restarts_sequence():
    t = make()
    t.add(56.50)
    t.add(56.50)
    assert t.add(56.52) is False  # spread 0.02 > 0.01 -> restart with 56.52
    assert t.values == [56.52]


def test_decrease_beyond_tolerance_restarts_sequence():
    t = make()
    t.add(56.505)
    t.add(56.505)
    assert t.add(56.500) is False  # 0.005 drop > tolerance 0.002
    assert t.values == [56.500]


def test_decrease_within_tolerance_keeps_sequence():
    t = make()
    t.add(56.5005)
    t.add(56.5000)  # 0.0005 drop <= tolerance
    assert t.add(56.5001) is True


def test_reset_clears_sequence():
    t = make()
    t.add(1.0)
    t.add(1.0)
    t.reset()
    assert t.values == []
    assert t.add(1.0) is False


def test_required_exposed():
    assert make(required=6).required == 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_reanchor.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'watermeter.reanchor'`

- [ ] **Step 3: Write the implementation**

```python
"""Re-anchoring detector: recognises a run of consistent readings below a stuck baseline.

Pure logic, no I/O. The plausibility checker feeds it every reverse-rejected value;
once ``required`` consecutive values agree (small spread, non-decreasing within
``tolerance``) the baseline is considered wrong and may be moved to the new value.
"""

from typing import List


class ReanchorTracker:
    def __init__(self, required: int, max_spread: float, tolerance: float) -> None:
        self.required = required
        self.max_spread = max_spread
        self.tolerance = tolerance
        self.values: List[float] = []

    def add(self, value: float) -> bool:
        """Record a reverse-rejected value; return True once the run is long enough."""
        if self.values:
            candidate = self.values + [value]
            spread_ok = max(candidate) - min(candidate) <= self.max_spread
            monotonic_ok = value >= self.values[-1] - self.tolerance
            self.values = candidate if (spread_ok and monotonic_ok) else [value]
        else:
            self.values = [value]
        return len(self.values) >= self.required

    def reset(self) -> None:
        self.values = []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_reanchor.py -q`
Expected: `6 passed`

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check watermeter/reanchor.py tests/unit/test_reanchor.py
uvx black watermeter/reanchor.py tests/unit/test_reanchor.py
git add watermeter/reanchor.py tests/unit/test_reanchor.py
git commit -m "claude: add ReanchorTracker for stuck-baseline recovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Plausibility — jitter band, re-anchoring, config fields

**Files:**
- Modify: `watermeter/plausibility.py` (whole `validate_plausibility`; add `PlausibilityResult`, `evaluate`, `reset_reanchor`)
- Modify: `watermeter/config_utils.py` (the `"plausibility"` schema block, around line 388)
- Modify: `config.yaml` (the `plausibility:` section, around line 102)
- Test: `tests/unit/test_plausibility.py` (append), `tests/unit/test_config_utils.py` (append)

**Interfaces:**
- Consumes: `ReanchorTracker` (Task 1).
- Produces:
  - `PlausibilityResult(is_valid: bool, warnings: List[str], baseline: Optional[float], reanchored: bool)`
  - `PlausibilityChecker.evaluate(new_value: float, previous_value: Optional[float], last_update_time: Optional[datetime]) -> PlausibilityResult`
  - `PlausibilityChecker.reset_reanchor() -> None`
  - `validate_plausibility(...)` keeps returning `(bool, List[str])`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_plausibility.py`)

```python
class TestEvaluateJitterAndReanchor:
    """Jitter band (reverse_tolerance) and re-anchoring after consistent lower readings."""

    @pytest.fixture
    def cfg(self, config):
        config['plausibility'].update({'reverse_tolerance': 0.002, 'reanchor_after': 3, 'reanchor_max_spread': 0.01})
        return config

    @pytest.fixture
    def chk(self, cfg, rate_tracker):
        return PlausibilityChecker(config=cfg, rate_tracker=rate_tracker)

    def test_small_decrease_is_held_not_rejected(self, chk):
        r = chk.evaluate(56.5990, 56.5999, datetime.now())
        assert r.is_valid is True
        assert r.baseline == 56.5999
        assert any("jitter" in w for w in r.warnings)

    def test_decrease_at_tolerance_boundary_is_held(self, chk):
        r = chk.evaluate(0.998, 1.0, datetime.now())  # exactly 0.002 lower (1.0 - 0.002 == 0.998 in float)
        assert r.is_valid is True
        assert r.baseline == 1.0

    def test_decrease_beyond_tolerance_rejected(self, chk):
        r = chk.evaluate(56.5903, 56.5999, datetime.now())
        assert r.is_valid is False
        assert any("Reverse" in w for w in r.warnings)

    def test_forward_reading_baseline_is_new_value(self, chk):
        r = chk.evaluate(56.6010, 56.5999, datetime.now() - timedelta(hours=1))
        assert r.is_valid is True
        assert r.baseline == 56.6010
        assert r.reanchored is False

    def test_reanchors_after_consistent_lower_readings(self, chk, rate_tracker):
        rate_tracker.add(56.59)
        rate_tracker.add(56.5999)
        results = [chk.evaluate(56.5003, 56.5999, datetime.now()) for _ in range(3)]
        assert [r.is_valid for r in results] == [False, False, True]
        last = results[-1]
        assert last.reanchored is True
        assert last.baseline == 56.5003
        assert any("Re-anchoring" in w for w in last.warnings)
        assert len(rate_tracker) == 0  # rate history reset on re-anchor

    def test_inconsistent_lower_readings_do_not_reanchor(self, chk):
        values = [56.50, 56.53, 56.50, 56.53]
        results = [chk.evaluate(v, 56.5999, datetime.now()) for v in values]
        assert not any(r.is_valid for r in results)

    def test_accepted_reading_resets_reanchor_sequence(self, chk):
        chk.evaluate(56.5003, 56.5999, datetime.now())
        chk.evaluate(56.5003, 56.5999, datetime.now())
        chk.evaluate(56.6100, 56.5999, datetime.now() - timedelta(hours=1))  # accepted
        r = chk.evaluate(56.5003, 56.6100, datetime.now())
        assert r.is_valid is False

    def test_reanchor_disabled_with_zero(self, chk, cfg):
        cfg['plausibility']['reanchor_after'] = 0
        results = [chk.evaluate(56.5003, 56.5999, datetime.now()) for _ in range(10)]
        assert not any(r.is_valid for r in results)

    def test_missing_new_keys_use_defaults(self, checker):
        # fixture config has no reverse_tolerance/reanchor keys -> defaults 0.002 / 6 / 0.01
        r = checker.evaluate(99.999, 100.0, datetime.now())
        assert r.is_valid is True and r.baseline == 100.0

    def test_validate_plausibility_wrapper_still_returns_tuple(self, chk):
        assert chk.validate_plausibility(56.5990, 56.5999, datetime.now())[0] is True
```

Append to `tests/unit/test_config_utils.py`:

```python
class TestPlausibilityReanchorSchema:
    BASE = {
        "images": {"digits": [], "arrows": []},
        "mqtt": {"broker": "x", "port": 1883},
        "inference": {"confidence_threshold": 0.5},
    }

    def _validate(self, plaus):
        return validate_config_schema({**self.BASE, "plausibility": plaus})

    def test_new_fields_pass(self):
        result = self._validate({"reverse_tolerance": 0.002, "reanchor_after": 6, "reanchor_max_spread": 0.01})
        assert result["valid"] is True, result["error"]

    def test_negative_tolerance_fails(self):
        result = self._validate({"reverse_tolerance": -0.1})
        assert result["valid"] is False
        assert "-0.1" in result["error"]

    def test_negative_reanchor_after_fails(self):
        result = self._validate({"reanchor_after": -1})
        assert result["valid"] is False
        assert "-1" in result["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_plausibility.py tests/unit/test_config_utils.py -q`
Expected: the new plausibility tests fail with `AttributeError: 'PlausibilityChecker' object has no attribute 'evaluate'`. In the schema tests, `test_negative_tolerance_fails` and `test_negative_reanchor_after_fails` fail because the fields have no `minimum` yet; `test_new_fields_pass` already passes because the plausibility block allows extra keys. Confirm with `grep -n '"plausibility"' -A3 watermeter/config_utils.py`.

- [ ] **Step 3: Implement `evaluate` in `watermeter/plausibility.py`**

Add the imports and the dataclass at the top of the module:

```python
from dataclasses import dataclass, field

from .reanchor import ReanchorTracker


@dataclass
class PlausibilityResult:
    """Outcome of ``PlausibilityChecker.evaluate``.

    ``baseline`` is the value to adopt as the new previous value when ``is_valid``:
    the new reading, the held previous value (jitter), or the re-anchored reading.
    """

    is_valid: bool
    warnings: List[str] = field(default_factory=list)
    baseline: Optional[float] = None
    reanchored: bool = False
```

In `PlausibilityChecker.__init__`, add:

```python
        self._reanchor: Optional[ReanchorTracker] = None
        self._reanchor_params: Optional[Tuple[int, float, float]] = None
```

Replace the whole body of `validate_plausibility` with the wrapper below, and add the new methods:

```python
    def validate_plausibility(
        self,
        new_value: float,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
    ) -> Tuple[bool, List[str]]:
        """Backward-compatible wrapper around :meth:`evaluate`."""
        result = self.evaluate(new_value, previous_value, last_update_time)
        return result.is_valid, result.warnings

    def reset_reanchor(self) -> None:
        """Forget any collected re-anchor candidates (on accept, reset, manual set)."""
        if self._reanchor is not None:
            self._reanchor.reset()

    def _get_reanchor_tracker(self, config: dict, tolerance: float) -> Optional[ReanchorTracker]:
        required = int(config.get("reanchor_after", 6))
        if required <= 0:
            return None
        params = (required, float(config.get("reanchor_max_spread", 0.01)), tolerance)
        if self._reanchor is None or self._reanchor_params != params:
            self._reanchor = ReanchorTracker(required=params[0], max_spread=params[1], tolerance=params[2])
            self._reanchor_params = params
        return self._reanchor

    def evaluate(
        self,
        new_value: float,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
    ) -> PlausibilityResult:
        """Validate a new reading; return validity, warnings and the baseline to adopt."""
        warnings: List[str] = []
        config = self.config["plausibility"]

        if previous_value is None:
            logger.info("No previous value - accepting first reading")
            self._rate_tracker.add(new_value)
            return PlausibilityResult(True, warnings, baseline=new_value)

        tolerance = float(config.get("reverse_tolerance", 0.002))

        # Reverse detection with jitter band and re-anchoring
        if config["enable_reverse_detection"] and new_value < previous_value:
            if new_value >= previous_value - tolerance:
                msg = f"Minor reverse (jitter): {previous_value:.4f} → {new_value:.4f}, holding previous value"
                warnings.append(msg)
                logger.info(msg)
                self.reset_reanchor()
                return PlausibilityResult(True, warnings, baseline=previous_value)

            tracker = self._get_reanchor_tracker(config, tolerance)
            if tracker is not None and tracker.add(new_value):
                msg = (
                    f"Re-anchoring baseline {previous_value:.4f} → {new_value:.4f} "
                    f"after {tracker.required} consistent readings"
                )
                logger.warning(msg)
                tracker.reset()
                self._rate_tracker.reset()
                return PlausibilityResult(True, [msg], baseline=new_value, reanchored=True)

            msg = f"Reverse detected: {previous_value:.4f} → {new_value:.4f}"
            warnings.append(msg)
            logger.error(msg)
            return PlausibilityResult(False, warnings)

        # Rate check
        if config["enable_rate_limit"]:
            value_diff = new_value - previous_value

            # Max rate per reading - immediate rejection (time-independent)
            if value_diff > config["max_rate_per_reading"]:
                msg = f"Change per reading too high: {value_diff:.4f} m³ (max: {config['max_rate_per_reading']})"
                warnings.append(msg)
                logger.error(msg)
                self.reset_reanchor()
                return PlausibilityResult(False, warnings)

            # Rate per hour - check against history if available
            if last_update_time:
                time_diff = (datetime.now() - last_update_time).total_seconds()
                if time_diff > 0:
                    rate_per_hour = (value_diff / time_diff) * 3600
                    if rate_per_hour > config["max_rate_per_hour"]:
                        if len(self._rate_tracker) >= 2:
                            avg_rate = self._rate_tracker.average_rate_per_hour
                            if avg_rate is not None and avg_rate > config["max_rate_per_hour"]:
                                msg = f"Rate per hour too high: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.error(msg)
                                self.reset_reanchor()
                                return PlausibilityResult(False, warnings)
                            else:
                                msg = f"Rate spike: {rate_per_hour:.2f} m³/h (avg: {avg_rate:.2f if avg_rate else 'N/A'}, max: {config['max_rate_per_hour']})"
                                warnings.append(msg)
                                logger.warning(msg)
                        else:
                            msg = f"Rate per hour high (no history): {rate_per_hour:.2f} m³/h (max: {config['max_rate_per_hour']})"
                            warnings.append(msg)
                            logger.warning(msg)

        self.reset_reanchor()
        return PlausibilityResult(True, warnings, baseline=new_value)
```

The rate-check block is today's code verbatim, apart from the `reset_reanchor()` and `PlausibilityResult` returns. Keep its existing f-strings unchanged, including the pre-existing `avg_rate:.2f if ...` expression.

- [ ] **Step 4: Add the schema fields** in `watermeter/config_utils.py`, inside the `"plausibility"` → `"properties"` dict, after `"rate_history_size"`:

```python
                "reverse_tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Decreases up to this many m³ are treated as jitter (held, not rejected)",
                },
                "reanchor_after": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Re-anchor baseline after this many consistent lower readings (0 = off)",
                },
                "reanchor_max_spread": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Max spread (m³) of the lower readings that may trigger re-anchoring",
                },
```

In `config.yaml`, under `plausibility:` after `max_rate_per_reading`:

```yaml
  reverse_tolerance: 0.002     # m³ - smaller decreases are jitter: previous value is held, not rejected
  reanchor_after: 6            # re-anchor baseline after N consistent lower readings (0 = never)
  reanchor_max_spread: 0.01    # m³ - max spread of those readings
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_plausibility.py tests/unit/test_config_utils.py -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit** (`plausibility.py`: black only if it was black-clean at HEAD; see Global Constraints)

```bash
uvx ruff check watermeter/ tests/
git add watermeter/plausibility.py watermeter/config_utils.py config.yaml tests/unit/test_plausibility.py tests/unit/test_config_utils.py
git commit -m "claude: plausibility jitter band and re-anchoring after consistent lower readings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Carry-aware `calculate_total`

**Files:**
- Modify: `watermeter/position_utils.py` (replace `calculate_total`, add helpers)
- Modify: `watermeter/oneshot.py:117-121`
- Test: `tests/unit/test_calculate_total.py` (create), `tests/unit/test_oneshot.py` (append one test)

**Interfaces:**
- Produces:
  - `ROLL_WINDOW: float = 0.2`
  - `PAIR_INCONSISTENCY: float = 0.35`
  - `arrow_pair_deviations(arrows: List[float], ints: List[int]) -> List[float]`: deviation of dial `i` from `int[i] + res[i+1]/10` (circular, period 10), one entry per adjacent pair, 0.1 dial first.
  - `resolve_arrows(arrows: List[float]) -> List[int]`: carry-aware integer per dial, 0.1 dial first.
  - `resolve_digits(raw_digits: List[Optional[int]], frac: float, previous_value: Optional[float], has_arrows: bool, notes: List[str]) -> Optional[List[int]]`
  - `calculate_total(config: Dict, predictions: Dict[str, Dict], previous_value: Optional[float] = None) -> Tuple[Optional[float], Dict]`
    - `raw_values` keys: `"digits": List[int]` (resolved), `"arrows": List[float]`, `"notes": List[str]`, `"raw_total": Optional[float]`
    - The total is rounded to `len(arrows)` decimals.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_calculate_total.py`)

```python
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
```

Append to `tests/unit/test_oneshot.py`, inside the class that holds `test_returns_zero_on_success`. Mirror that test's setup exactly: same helpers, same patches. The only change is that the inference mock returns `NAN` for a digit:

```python
    async def test_returns_one_when_total_unresolvable(self, tmp_path):
        """A NAN digit without previous value makes the reading unresolvable -> exit 1."""
        # Copy the arrange block of test_returns_zero_on_success, but make the
        # inference mock's predict_from_bytes return {"class": "NAN", "confidence": 0.9}.
```

When implementing, open `test_returns_zero_on_success` (`tests/unit/test_oneshot.py:142`) and copy its arrange/act lines verbatim. Set `predict_from_bytes.return_value = {"class": "NAN", "confidence": 0.9}`, configure a digits ROI so that digit predictions exist, and assert the result `== 1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_calculate_total.py tests/unit/test_oneshot.py -q`
Expected: `ImportError: cannot import name 'resolve_arrows'`; the oneshot test fails (returns 0, or crashes formatting `None`).

- [ ] **Step 3: Implement in `watermeter/position_utils.py`.** Replace `calculate_total` and add the helpers below it (add `import math` and `Optional` to the imports):

```python
# A wheel showing the next/previous integer is only an early/late roll when the
# arrow fraction is this close to the wrap (0.1 dial at >= 8 or < 2).
ROLL_WINDOW = 0.2


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def resolve_arrows(arrows: List[float]) -> List[int]:
    """Carry-aware integer per dial (0.1 dial first), resolved finest to coarsest.

    Each dial should read ``int + resolved_finer / 10``; using the *resolved* finer
    value (not its raw reading) keeps a just-rolled-over dial from dragging the
    next coarser one down by a unit.
    """
    if not arrows:
        return []
    ints = [0] * len(arrows)
    resolved = arrows[-1]
    ints[-1] = int(math.floor(arrows[-1])) % 10
    for i in range(len(arrows) - 2, -1, -1):
        ints[i] = _round_half_up(arrows[i] - resolved / 10) % 10
        resolved = ints[i] + resolved / 10
    return ints


# Diagnostic only: a dial deviating this much from its cascade expectation contradicts its finer neighbour.
PAIR_INCONSISTENCY = 0.35


def arrow_pair_deviations(arrows: List[float], ints: List[int]) -> List[float]:
    """Circular deviation of each dial from ``int[i] + resolved_finer / 10``, one per adjacent pair."""
    deviations: List[float] = []
    resolved = arrows[-1] if arrows else 0.0
    for i in range(len(arrows) - 2, -1, -1):
        expected = ints[i] + resolved / 10
        diff = abs(arrows[i] - expected) % 10
        deviations.append(min(diff, 10 - diff))
        resolved = expected
    return list(reversed(deviations))


def _to_digits(value: int, count: int) -> List[int]:
    value %= 10**count
    return [int(c) for c in str(value).zfill(count)]


def _matches(raw_digits: List[Optional[int]], candidate: List[int]) -> bool:
    return all(r is None or r == c for r, c in zip(raw_digits, candidate))


def resolve_digits(
    raw_digits: List[Optional[int]],
    frac: float,
    previous_value: Optional[float],
    has_arrows: bool,
    notes: List[str],
) -> Optional[List[int]]:
    """Resolve the integer part. ``None`` entries are NAN digits. Returns None if unresolvable."""
    count = len(raw_digits)
    if count == 0:
        return []
    has_nan = any(d is None for d in raw_digits)

    if previous_value is None or not has_arrows:
        if has_nan:
            notes.append("unresolved NAN digit without carry context")
            return None
        return [int(d) for d in raw_digits if d is not None]

    p_int = int(math.floor(previous_value))
    p_frac = previous_value - p_int
    expected = p_int + 1 if frac < p_frac - 0.5 else p_int

    chosen: Optional[int] = None
    if _matches(raw_digits, _to_digits(expected, count)):
        chosen = expected
    elif frac >= 1 - ROLL_WINDOW and _matches(raw_digits, _to_digits(expected + 1, count)):
        chosen = expected
    elif frac < ROLL_WINDOW and expected > 0 and _matches(raw_digits, _to_digits(expected - 1, count)):
        chosen = expected

    if chosen is not None:
        resolved = _to_digits(chosen, count)
        if resolved != raw_digits:
            shown = "".join("?" if d is None else str(d) for d in raw_digits)
            notes.append(f"integer part from carry context: {shown} → {''.join(map(str, resolved))}")
        return resolved
    if has_nan:
        notes.append("NAN digit inconsistent with previous value")
        return None
    return [int(d) for d in raw_digits if d is not None]


def calculate_total(
    config: Dict, predictions: Dict[str, Dict], previous_value: Optional[float] = None
) -> Tuple[Optional[float], Dict]:
    """
    Calculate the meter total, carry-aware (see docs/plans/2026-10-04-reading-plausibility-cascade-design.md).

    Shared implementation used by WatermeterService, CorrectionEngine and the one-shot CLI.

    Args:
        config: The watermeter configuration dict.
        predictions: Position ID -> prediction dict with at least "class".
        previous_value: Last accepted reading, used to resolve NAN digits and rolling wheels.

    Returns:
        (total or None if unresolvable,
         {"digits": resolved digits, "arrows": raw arrow floats, "notes": [...], "raw_total": float or None})
    """
    digit_ids, arrow_ids = get_position_ids(config)
    notes: List[str] = []
    invalid = False

    raw_digits: List[Optional[int]] = []
    for image_id in digit_ids:
        if image_id not in predictions:
            continue
        cls = predictions[image_id]["class"]
        if cls == "ERROR":
            notes.append(f"{image_id}: inference error")
            invalid = True
        elif cls == "NAN":
            logger.warning(f"{image_id} is NAN (wheel between digits)")
            raw_digits.append(None)
        else:
            raw_digits.append(int(cls))

    arrows: List[float] = []
    used_arrow_ids: List[str] = []
    for image_id in arrow_ids:
        if image_id not in predictions:
            continue
        cls = predictions[image_id]["class"]
        if cls == "ERROR":
            notes.append(f"{image_id}: inference error")
            invalid = True
        else:
            arrows.append(float(cls))
            used_arrow_ids.append(image_id)

    def unresolved() -> Tuple[None, Dict]:
        logger.warning(f"Reading unresolved: {'; '.join(notes)}")
        return None, {"digits": [], "arrows": arrows, "notes": notes, "raw_total": None}

    if invalid:
        return unresolved()

    ints = resolve_arrows(arrows)
    for i, dev in enumerate(arrow_pair_deviations(arrows, ints)):
        if dev > PAIR_INCONSISTENCY:
            notes.append(f"{used_arrow_ids[i]}/{used_arrow_ids[i + 1]} inconsistent ({dev:.2f})")
    frac = sum(v * 10 ** (-(i + 1)) for i, v in enumerate(ints))
    digits = resolve_digits(raw_digits, frac, previous_value, bool(arrows), notes)
    if digits is None:
        return unresolved()

    integer = sum(d * 10 ** (len(digits) - 1 - i) for i, d in enumerate(digits))
    total = round(integer + frac, len(arrows))
    if arrows:
        coarse = sum(v * 10 ** (-(i + 1)) for i, v in enumerate(ints[:-1]))
        raw_total = integer + coarse + arrows[-1] * 10 ** (-len(arrows))
    else:
        raw_total = float(integer)

    logger.info(f"Calculated total: {total:.4f} m³")
    return total, {"digits": digits, "arrows": arrows, "notes": notes, "raw_total": raw_total}
```

- [ ] **Step 4: Update `watermeter/oneshot.py`.** Replace the two lines after `# Step 7: Calculate total`:

```python
    # Step 7: Calculate total (no previous value in one-shot mode)
    total, raw_values = _calculate_total(config, predictions)
    if total is None:
        print(f"Meter reading unresolved: {'; '.join(raw_values['notes'])}")
        return 1

    print(f"Meter reading: {total:.4f} m³  (digits={raw_values['digits']}, arrows={raw_values['arrows']})")
    return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_calculate_total.py tests/unit/test_oneshot.py tests/unit/test_position_utils.py -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
uvx ruff check watermeter/ tests/
git add watermeter/position_utils.py watermeter/oneshot.py tests/unit/test_calculate_total.py tests/unit/test_oneshot.py
git commit -m "claude: carry-aware calculate_total (cascade, pair consistency, NAN from context, fixed raw total)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Correction engine and confirmation use the shared total

**Files:**
- Modify: `watermeter/correction.py:49-78` (`_recalculate_with_replacement`)
- Modify: `watermeter/confirmation.py:408-418` (`_compute_raw_total`)
- Test: `tests/unit/test_value_correction.py` (adjust one comment, add two tests)

**Interfaces:**
- Consumes: `calculate_total(config, predictions, previous_value=None)` (Task 3).
- Produces: `_recalculate_with_replacement(...) -> Optional[float]`. It returns `None` when the replacement makes the reading unresolvable; callers already compare against ranges, so treat `None` as "no improvement".

- [ ] **Step 1: Write the failing tests** (append inside the class holding `test_recalculate_arrow_replacement`)

```python
    def test_recalculate_uses_carry_aware_cascade(self):
        """Replacing the 0.01 dial with a rolled-over value changes the 0.1 dial's integer via the cascade."""
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '0', 'confidence': 0.95, 'model': 'digits'},
            'digit_2': {'id': 'digit_2', 'class': '5', 'confidence': 0.95, 'model': 'digits'},
            'digit_3': {'id': 'digit_3', 'class': '6', 'confidence': 0.95, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '5.2', 'confidence': 0.80, 'model': 'arrows'},
            'analog_2': {'id': 'analog_2', 'class': '9.7', 'confidence': 0.40, 'model': 'arrows'},
            'analog_3': {'id': 'analog_3', 'class': '0.3', 'confidence': 0.80, 'model': 'arrows'},
            'analog_4': {'id': 'analog_4', 'class': '3.2', 'confidence': 0.80, 'model': 'arrows'},
        }
        assert engine._recalculate_with_replacement(predictions, 'analog_2', '9.7', {}) == pytest.approx(56.5003)

    def test_recalculate_unresolvable_returns_none(self):
        engine = make_engine()
        predictions = {
            'digit_1': {'id': 'digit_1', 'class': '1', 'confidence': 0.95, 'model': 'digits'},
            'analog_1': {'id': 'analog_1', 'class': '2.0', 'confidence': 0.80, 'model': 'arrows'},
        }
        assert engine._recalculate_with_replacement(predictions, 'digit_1', 'NAN', {}) is None
```

Check that `pytest` is imported at the top of `tests/unit/test_value_correction.py`; add `import pytest` if it is missing. Also check `make_engine()`'s config: if it does not configure `detection.digits.count: 3` / `analogs.count: 4` (or the matching `images` IDs), the new tests need predictions shaped to that config. Read `make_engine` before writing them and adapt the IDs.

In `test_recalculate_arrow_replacement`, change the comment `# floor(3.0)*0.1 = 0.3, floor(7.0)*0.1 = 0.7 -> delta = 0.4` to `# cascade: 0.1 dial resolves to 2 (3.0 - 0.741) vs 6 (7.0 - 0.741) -> delta = 0.4`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_value_correction.py -q`
Expected: `test_recalculate_uses_carry_aware_cascade` fails (`56.5903 != 56.5003`); `test_recalculate_unresolvable_returns_none` fails (returns a float).

- [ ] **Step 3: Implement.** In `watermeter/correction.py`, replace the body of `_recalculate_with_replacement` with:

```python
    def _recalculate_with_replacement(
        self, predictions: Dict[str, Dict], replace_id: str, replace_class: str, raw_values: Dict
    ) -> Optional[float]:
        """Calculate the hypothetical total with one position replaced (None if unresolvable).

        Deliberately without previous-value context: carry context would mask the
        replacement being evaluated.
        """
        replaced = {pid: (dict(p, **{"class": replace_class}) if pid == replace_id else p) for pid, p in predictions.items()}
        total, _ = calculate_total(self.config, replaced)
        return total
```

Add `from .position_utils import calculate_total, get_position_ids` (replacing the existing `get_position_ids` import). At the call site (`correction.py:251`), skip alternatives whose total is `None`. Right after `total_with_alt = self._recalculate_with_replacement(...)`, add:

```python
                if total_with_alt is None:
                    continue
```

Make sure `continue` sits in the loop over alternatives; check the indentation at line 251.

In `watermeter/confirmation.py`, replace the body of the static `_compute_raw_total` with:

```python
    @staticmethod
    def _compute_raw_total(raw_values: Dict) -> Optional[float]:
        """High-precision total computed by position_utils.calculate_total."""
        return raw_values.get("raw_total")
```

Make sure `Optional` is imported in `confirmation.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_value_correction.py tests/unit/test_confirmation.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uvx ruff check watermeter/ tests/
git add watermeter/correction.py watermeter/confirmation.py tests/unit/test_value_correction.py
git commit -m "claude: correction and confirmation reuse carry-aware calculate_total

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Persisted high-water mark for published values

**Files:**
- Modify: `watermeter/persistence.py` (`StateStore.save`, new `load_published_value`)
- Modify: `watermeter/meter_state.py` (field and reset)
- Modify: `watermeter/watermeter_service.py`:
  - `__init__` persistence load (~line 72)
  - `publish_to_mqtt` (~line 1035)
  - `reset_previous_value` (~line 1047)
  - `set_manual_value` publish call (~line 1124)
- Test: `tests/unit/test_persistence.py` (append), `tests/unit/test_service_reading_recovery.py` (create; also used by Task 6)

**Interfaces:**
- Produces:
  - `StateStore.save(previous_value, last_update_time, published_value: Optional[float] = None)`. With `None`, an already-stored `published_value` is preserved.
  - `StateStore.load_published_value() -> Optional[float]`
  - `MeterState.published_high_water: Optional[float]`
  - `WatermeterService.publish_to_mqtt(value, warnings, predictions, *, leak_warning=False, raw_value=None, allow_decrease=False)`

- [ ] **Step 1: Write the failing persistence tests** (append to `tests/unit/test_persistence.py`; check the file's existing import of `StateStore` and reuse it)

```python
class TestPublishedHighWater:
    def test_round_trip(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(56.5003, datetime(2026, 10, 4, 8, 0), published_value=56.5999)
        assert StateStore(str(tmp_path / "state.json")).load_published_value() == 56.5999

    def test_save_without_published_preserves_it(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(56.5003, datetime(2026, 10, 4, 8, 0), published_value=56.5999)
        store.save(56.5010, datetime(2026, 10, 4, 8, 1))
        assert store.load_published_value() == 56.5999
        assert store.load()[0] == 56.5010

    def test_old_state_file_has_no_published_value(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"previous_value": 1.0, "last_update_time": null}')
        assert StateStore(str(path)).load_published_value() is None
```

Add `from datetime import datetime` if the file does not import it.

- [ ] **Step 2: Create `tests/unit/test_service_reading_recovery.py`** with the module-import scaffolding copied from `tests/unit/test_service_fail_closed.py`:
  - copy lines 1–66 (the docstring plus the `sys.modules` dance up to the `MeterState` import), adjusting the docstring;
  - copy the restore block at the bottom of that file, from `import watermeter as _wm_pkg` to the end, verbatim.

Between them, add the helper and the high-water tests:

```python
from watermeter.persistence import StateStore  # noqa: E402, I001
from watermeter.plausibility import PlausibilityChecker  # noqa: E402, I001
from watermeter.rate_tracker import RateTracker  # noqa: E402, I001

PLAUSIBILITY = {
    "enable_reverse_detection": True,
    "enable_rate_limit": True,
    "max_rate_per_reading": 0.15,
    "max_rate_per_hour": 1.5,
    "enable_consistency_check": False,
    "reverse_tolerance": 0.002,
    "reanchor_after": 6,
    "reanchor_max_spread": 0.01,
}


def _make_publish_service(tmp_path=None):
    svc = object.__new__(WatermeterService)
    svc._state = MeterState(ha_publish_enabled=True)
    svc._mqtt = MagicMock()
    svc._mqtt.publish_to_mqtt = AsyncMock()
    svc._state_store = StateStore(str(tmp_path / "state.json")) if tmp_path else None
    return svc


def _published(svc):
    return svc._mqtt.publish_to_mqtt.call_args.args[0]


def test_publish_never_decreases(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(56.5003, [], {}))
    assert _published(svc) == 56.5999


def test_publish_increases_past_high_water(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(56.6010, [], {}))
    assert _published(svc) == 56.6010


def test_allow_decrease_overrides_high_water(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(40.0, [], {}, allow_decrease=True))
    assert _published(svc) == 40.0
    assert svc._state.published_high_water == 40.0


def test_high_water_survives_restart(tmp_path):
    """Review focus 5: after a restart the first publish is still >= the old peak."""
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    restarted = _make_publish_service(tmp_path)
    restarted._state.published_high_water = restarted._state_store.load_published_value()
    asyncio.run(restarted.publish_to_mqtt(56.5003, [], {}))
    assert _published(restarted) == 56.5999


def test_meter_state_reset_clears_high_water():
    state = MeterState()
    state.published_high_water = 1.0
    state.reset()
    assert state.published_high_water is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_persistence.py tests/unit/test_service_reading_recovery.py -q`
Expected: `TypeError: save() got an unexpected keyword argument 'published_value'`, `AttributeError ... load_published_value`, and failing publish assertions.

- [ ] **Step 4: Implement `StateStore`.** In `watermeter/persistence.py`, change `save` and add `load_published_value`:

```python
    def save(
        self,
        previous_value: Optional[float],
        last_update_time: Optional[datetime],
        published_value: Optional[float] = None,
    ) -> None:
        """Save state to disk. ``published_value`` None keeps the stored high-water mark."""
        try:
            if published_value is None:
                published_value = self.load_published_value()
            state = {
                "previous_value": previous_value,
                "last_update_time": last_update_time.isoformat() if last_update_time else None,
                "published_value": published_value,
            }
            # (unchanged atomic temp-file write follows)
```

Keep the existing temp-file/rename code below it unchanged. Then add:

```python
    def load_published_value(self) -> Optional[float]:
        """Highest value ever published to HA (high-water mark), or None."""
        try:
            if not self.file_path.exists():
                return None
            with open(self.file_path, "r") as f:
                return json.load(f).get("published_value")
        except Exception as e:
            logger.error(f"Failed to load published value: {e}")
            return None
```

- [ ] **Step 5: Implement `MeterState`.** In `__init__`, add `self.published_high_water: Optional[float] = None` after `last_update_time`. In `reset()`, add `self.published_high_water = None`.

- [ ] **Step 6: Implement the service.**

In `__init__`, right after `self._state.previous_value, self._state.last_update_time = self.state_store.load()`:

```python
            self._state.published_high_water = self.state_store.load_published_value()
```

Replace `publish_to_mqtt`:

```python
    async def publish_to_mqtt(
        self,
        value: float,
        warnings: List[str],
        predictions: Dict,
        *,
        leak_warning: bool = False,
        raw_value: Optional[float] = None,
        allow_decrease: bool = False,
    ) -> None:
        """Delegate to MqttPublisher, clamped to the published high-water mark.

        HA treats ``water_usage`` as ``total_increasing``: any decrease looks like a
        meter reset. Only a manual set (``allow_decrease``) or /api/reset may go down.
        """
        high_water = self._state.published_high_water
        if allow_decrease or high_water is None or value > high_water:
            high_water = value
            self._state.published_high_water = value
            if self.state_store:
                self.state_store.save(self.previous_value, self.last_update_time, published_value=value)
        await self._mqtt.publish_to_mqtt(
            high_water, warnings, predictions, leak_warning=leak_warning, raw_value=raw_value
        )
```

In `reset_previous_value`, next to `self.previous_value = None`, add `self._state.published_high_water = None`. `state_store.clear()` already deletes the file.

In `set_manual_value`, add `allow_decrease=True` to the `self.publish_to_mqtt(...)` call.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_persistence.py tests/unit/test_service_reading_recovery.py tests/unit/test_persistence_failures.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
uvx ruff check watermeter/ tests/
git add watermeter/persistence.py watermeter/meter_state.py watermeter/watermeter_service.py tests/unit/test_persistence.py tests/unit/test_service_reading_recovery.py
git commit -m "claude: never publish a decreasing water_usage (persisted high-water mark)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Wire the cascade and the plausibility result into `process_reading`

**Files:**
- Modify: `watermeter/watermeter_service.py`:
  - `calculate_total` wrapper (~438)
  - `validate_plausibility` wrapper (~456): add `evaluate_plausibility`
  - `process_reading` steps 3–5 (~770–785)
  - accept path (~855–915)
  - reject path (~930–970)
  - `_compute_raw_total` (~1020)
  - `reset_previous_value`, `set_manual_value`
- Modify: `tests/unit/test_service_fail_closed.py`, `tests/unit/test_consecutive_failures.py` (mock `evaluate_plausibility` instead of `validate_plausibility`)
- Test: `tests/unit/test_service_reading_recovery.py` (append)

**Interfaces:**
- Consumes:
  - `calculate_total(config, predictions, previous_value)` (Task 3)
  - `PlausibilityResult`, `PlausibilityChecker.evaluate`, `reset_reanchor` (Task 2)
  - the high-water `publish_to_mqtt` (Task 5)
- Produces: `WatermeterService.evaluate_plausibility(new_value: float) -> PlausibilityResult`

- [ ] **Step 1: Write the failing service tests** (append to `tests/unit/test_service_reading_recovery.py`, above the restore block)

```python
def _make_reading_service(previous_value):
    """Service with real calculate_total + PlausibilityChecker; fetch/inference/publish mocked."""
    svc = object.__new__(WatermeterService)
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {},
        "inference": {"confidence_threshold": 0.6},
        "low_confidence": {"save_path": "/tmp/lc"},
        "plausibility": dict(PLAUSIBILITY),
        "homeassistant": {"enabled": False},
        "detection": {"digits": {"count": 3}, "analogs": {"count": 4}},
    }
    svc._state = MeterState(ha_publish_enabled=False)
    svc._confirmation_manager = MagicMock()
    svc._confirmation_manager._pending_confirmation = None
    svc.processing_lock = asyncio.Lock()
    svc._failure_store = MagicMock()
    svc._metrics = MagicMock()
    svc.consecutive_alignment_failures = 0
    svc._stale_notified = False
    svc._data_collector = None
    svc._rate_tracker = RateTracker(max_size=25)
    svc._plausibility_checker = PlausibilityChecker(config=svc.config, rate_tracker=svc._rate_tracker)
    svc._state_store = None
    align_ok = AlignmentResult(success=True, image=MagicMock(), marker_confidences=[0.9])
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=({"x": (b"", "digits")}, align_ok))
    svc.fetch_whole_image = AsyncMock(return_value=b"jpeg")
    svc.publish_to_mqtt = AsyncMock()
    svc._archive_raw_image = MagicMock()
    svc._notify_stale = MagicMock()
    svc.correct_predictions = MagicMock(return_value=[])
    svc.check_consistency = MagicMock(return_value=[])
    svc._check_sustained_consumption = MagicMock(return_value=None)
    svc._should_request_confirmation = MagicMock(return_value=None)
    svc.previous_value = previous_value
    return svc


def _live_predictions(digits=("0", "5", "6"), arrows=("5.2", "9.7", "0.3", "3.2")):
    p = {}
    for i, d in enumerate(digits):
        p[f"digit_{i + 1}"] = {"id": f"digit_{i + 1}", "class": d, "confidence": 0.99, "model": "digits", "image_bytes": b""}
    for i, a in enumerate(arrows):
        p[f"analog_{i + 1}"] = {"id": f"analog_{i + 1}", "class": a, "confidence": 0.9, "model": "arrows", "image_bytes": b""}
    return p


def test_live_stuck_sequence_reanchors_after_six_readings():
    """2026-10-04: baseline 56.5999 wrong, true 56.5003 -> 5 rejections, re-anchor on the 6th."""
    svc = _make_reading_service(previous_value=56.5999)
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    statuses = []
    for _ in range(6):
        asyncio.run(svc.process_reading())
        statuses.append(svc.current_state["status"])
    assert statuses[:5] == ["rejected"] * 5
    assert statuses[5] == "warning"
    assert svc.previous_value == pytest.approx(56.5003)
    assert svc.consecutive_rejections == 0
    assert svc.publish_to_mqtt.call_args.args[0] == pytest.approx(56.5003)


def test_jitter_holds_previous_value():
    svc = _make_reading_service(previous_value=56.5004)
    svc.run_inference = AsyncMock(return_value=_live_predictions())  # reads 56.5003
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "warning"
    assert svc.previous_value == pytest.approx(56.5004)


def test_unresolvable_reading_is_rejected_without_crash():
    """Review focus 4: NAN that can't be resolved -> rejected; STUCK message must not crash on None."""
    svc = _make_reading_service(previous_value=56.5000)
    svc.consecutive_rejections = 10  # beyond max -> STUCK message path
    svc.run_inference = AsyncMock(return_value=_live_predictions(digits=("0", "8", "NAN"), arrows=("1.0", "0.0", "0.0", "0.0")))
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    assert any("inconsistent" in w for w in svc.current_state["last_rejected_reasons"])
    assert any("STUCK" in w for w in svc.current_state["warnings"])


def test_reset_clears_reanchor_candidates():
    svc = _make_reading_service(previous_value=56.5999)
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    for _ in range(5):  # one short of re-anchoring
        asyncio.run(svc.process_reading())
    svc._plausibility_checker.reset_reanchor()
    svc.previous_value = 56.5999
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
```

Add `import pytest` at the top of the file if it is not there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_service_reading_recovery.py -q`
Expected: the live sequence never re-anchors (status stays `rejected`, or the total is 56.5903); `test_unresolvable…` fails.

- [ ] **Step 3: Implement the service wrappers.** Replace the bodies:

```python
    def calculate_total(self, predictions: Dict[str, Dict]) -> Tuple[Optional[float], Dict]:
        """Carry-aware total using the last accepted value as context (None if unresolvable)."""
        return _calculate_total_impl(self.config, predictions, previous_value=self.previous_value)

    def evaluate_plausibility(self, new_value: float) -> PlausibilityResult:
        """Plausibility verdict plus the baseline to adopt (held / new / re-anchored value)."""
        return self._plausibility_checker.evaluate(new_value, self.previous_value, self.last_update_time)
```

Keep the existing `validate_plausibility` wrapper unchanged; other code may still use it. Add `from .plausibility import PlausibilityChecker, PlausibilityResult` (extend the existing import).

Replace `_compute_raw_total`'s body with `return raw_values.get("raw_total")` and its return annotation with `Optional[float]`.

- [ ] **Step 4: Implement `process_reading` steps 3–5.** Replace the block from `# 3. Calculate total` through `all_warnings = correction_warnings + consistency_warnings + plausibility_warnings` with:

```python
                # 3. Calculate total (carry-aware; None when the reading can't be resolved)
                total_value, raw_values = self.calculate_total(predictions)
                correction_warnings: List[str] = []
                consistency_warnings: List[str] = []
                resolution_notes = list(raw_values.get("notes", []))

                if total_value is None:
                    plausibility = PlausibilityResult(
                        is_valid=False, warnings=resolution_notes or ["Reading could not be resolved"]
                    )
                    resolution_notes = []  # already carried as rejection reasons
                else:
                    # 3b. Value correction (BL-04)
                    correction_warnings = self.correct_predictions(predictions, total_value, raw_values)
                    if correction_warnings:
                        total_value, raw_values = self.calculate_total(predictions)
                        resolution_notes = list(raw_values.get("notes", []))
                        logger.info(f"Recalculated total after {len(correction_warnings)} correction(s): {total_value}")

                    # 4. Consistency check
                    consistency_warnings = self.check_consistency(predictions)

                    # 5. Plausibility check
                    if total_value is None:
                        plausibility = PlausibilityResult(is_valid=False, warnings=resolution_notes)
                        resolution_notes = []
                    else:
                        plausibility = self.evaluate_plausibility(total_value)

                is_valid = plausibility.is_valid
                plausibility_warnings = plausibility.warnings
                all_warnings = resolution_notes + correction_warnings + consistency_warnings + plausibility_warnings
```

- [ ] **Step 5: Implement the accept path.** In the `if is_valid:` branch:
  - Directly after `prev_time_before = self.last_update_time`, add:

    ```python
                    accepted_value = plausibility.baseline if plausibility.baseline is not None else total_value
    ```
  - Replace every use of `total_value` inside the `if is_valid:` branch with `accepted_value`. That covers `self.previous_value = …`, `self._rate_tracker.add(…)`, `current_state["total_value"]`, `current_state["last_published_value"]`, the `logger.info(f"✓ Reading accepted…")`, `_should_request_confirmation(…)`, `"value": …` in `_pending_confirmation`, `_publish_confirmation_request(…)` and `publish_to_mqtt(…)`.
  - Check the result with `grep -n "total_value" watermeter/watermeter_service.py`: inside the accept branch, only the line computing `accepted_value` may still reference it.

- [ ] **Step 6: Implement the reject path.** In the `else:` (rejected) branch, replace the stuck message's formatting:

```python
                    shown = f"{total_value:.4f}" if total_value is not None else "unresolved"
```

Then use `{shown}` instead of `{total_value:.4f}` in both `logger.error(f"✗ Reading rejected: …")` and `stuck_msg`. Change `self.current_state["last_rejected_reasons"] = plausibility_warnings` so that it keeps using `plausibility_warnings`, which now holds the resolution notes for unresolvable readings.

- [ ] **Step 7: Reset the re-anchor state on reset and manual set.** In `reset_previous_value` and `set_manual_value`, after the `consecutive_rejections = 0` line, add:

```python
        if hasattr(self, "_plausibility_checker"):
            self._plausibility_checker.reset_reanchor()
```

The `hasattr` guard is needed because tests build services with `object.__new__` and no checker.

- [ ] **Step 8: Update existing tests that mock the old call.**
  - `tests/unit/test_consecutive_failures.py:172`: replace `svc.validate_plausibility = MagicMock(return_value=(True, []))` with `svc.evaluate_plausibility = MagicMock(return_value=PlausibilityResult(True, [], baseline=1.234))`.
  - `tests/unit/test_service_fail_closed.py:201` and `:242`: replace `svc.validate_plausibility = MagicMock()` with `svc.evaluate_plausibility = MagicMock()`; keep any `assert_not_called` lines, renamed accordingly.
  - `tests/unit/test_service_fail_closed.py:269`: replace with `svc.evaluate_plausibility = MagicMock(return_value=PlausibilityResult(False, ["Reverse detected: 56.3682 → 56.3624"]))`.
  - In both files, add `from watermeter.plausibility import PlausibilityResult  # noqa: E402, I001` next to the other late imports.
  - Their mocked `calculate_total` return values (`(56.3624, {})` and similar) still work: `raw_values.get("notes", [])` handles `{}`.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest tests/unit tests/regression -q`
Expected: all pass, apart from the known `test_mqtt_reconnect` flake. Rerun that once if it fails.

- [ ] **Step 10: Commit**

```bash
uvx ruff check watermeter/ tests/
git add watermeter/watermeter_service.py tests/unit/test_service_reading_recovery.py tests/unit/test_service_fail_closed.py tests/unit/test_consecutive_failures.py
git commit -m "claude: process_reading uses carry-aware total, jitter hold and re-anchoring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Replay validation, docs, backlog, push

**Files:**
- Create: `scripts/replay_plausibility.py`, `scripts/replay_cascade.py`
- Modify: `backlog.md`, `CLAUDE.md`

- [ ] **Step 1: Write `scripts/replay_plausibility.py`**

```python
"""Replay logged totals through old (strict) vs new (jitter + re-anchor) plausibility.

Usage:
    .venv/bin/python scripts/replay_plausibility.py --logs data_debug/watermeter.log* --config config_debug/config.yaml

Reads every "Calculated total: X m³" line in chronological order (log files sorted
oldest first) and feeds them through PlausibilityChecker.evaluate with a simulated
baseline and published high-water mark. Reports acceptance rate, re-anchor count,
the largest accepted downward step of the baseline, and of the published value
(must be 0).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.plausibility import PlausibilityChecker  # noqa: E402
from watermeter.rate_tracker import RateTracker  # noqa: E402

LINE = re.compile(r"^(\S+ \S+),\d+ .*Calculated total: ([\d.]+) m")


def read_totals(paths: list[Path]) -> list[tuple[datetime, float]]:
    rows = []
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            m = LINE.match(line)
            if m:
                rows.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), float(m.group(2))))
    return sorted(rows)


def replay(rows, plausibility_cfg: dict) -> dict:
    config = {"plausibility": plausibility_cfg}
    checker = PlausibilityChecker(config=config, rate_tracker=RateTracker(max_size=25))
    previous, last_time, high_water = None, None, None
    accepted = reanchors = 0
    max_baseline_drop = max_published_drop = 0.0
    for ts, value in rows:
        result = checker.evaluate(value, previous, last_time)
        if not result.is_valid:
            continue
        accepted += 1
        reanchors += int(result.reanchored)
        if previous is not None and result.baseline < previous and not result.reanchored:
            max_baseline_drop = max(max_baseline_drop, previous - result.baseline)
        previous, last_time = result.baseline, ts
        published = previous if high_water is None else max(high_water, previous)
        if high_water is not None:
            max_published_drop = max(max_published_drop, high_water - published)
        high_water = published
    return {
        "readings": len(rows),
        "accepted": accepted,
        "accept_rate_pct": round(100 * accepted / max(1, len(rows)), 1),
        "reanchors": reanchors,
        "max_baseline_drop_without_reanchor": round(max_baseline_drop, 4),
        "max_published_drop": round(max_published_drop, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()

    base = dict(load_config(args.config).get("plausibility", {}))
    rows = read_totals(args.logs)
    old = replay(rows, {**base, "reverse_tolerance": 0.0, "reanchor_after": 0})
    new = replay(rows, {**base, "reverse_tolerance": 0.002, "reanchor_after": 6, "reanchor_max_spread": 0.01})
    print(f"old (strict):          {old}")
    print(f"new (jitter+reanchor): {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: the rate-per-hour check uses `datetime.now()` internally, so with historic timestamps it effectively never fires during the replay. Both runs are affected equally; the comparison stays fair.

- [ ] **Step 2: Run it and record the numbers**

Run:
```bash
.venv/bin/python scripts/replay_plausibility.py --logs data_debug/watermeter.log* --config config_debug/config.yaml
```

Expected:
- the new acceptance rate is far above the old one (old ≈ 13.8%);
- `max_published_drop` is `0.0` in the new run;
- `max_baseline_drop_without_reanchor` is ≤ `0.002`.

If `max_baseline_drop_without_reanchor` exceeds 0.002, stop and investigate: that is a bug in Task 2.

- [ ] **Step 3: Write `scripts/replay_cascade.py`.** It runs inside the debug container, which has OpenVINO and the models:

```python
"""Replay archived raw frames: legacy floor total vs carry-aware cascade total.

Run inside the DEBUG container (has OpenVINO + models), never production:
    docker exec watermeter-dashboard-debug python /app/scripts/replay_cascade.py \
        --archive-dir /data/raw_archive --config /config/config.yaml

For each frame (chronological): align + crop, infer, then compute the legacy total
(floor per arrow, NAN->0) and the cascade total (previous = last cascade total).
Reports how often they differ, NAN/unresolved counts, and backward steps
(> 0.002 m³) in each sequence — fewer backward steps means fewer rejections.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.image_pipeline import ImagePipeline  # noqa: E402
from watermeter.inference import get_inference_service  # noqa: E402
from watermeter.position_utils import calculate_total, get_position_ids  # noqa: E402


def legacy_total(config: dict, predictions: dict) -> float:
    digit_ids, arrow_ids = get_position_ids(config)
    digits = [int(predictions[i]["class"]) if predictions[i]["class"] not in ("NAN", "ERROR") else 0 for i in digit_ids]
    arrows = [float(predictions[i]["class"]) if predictions[i]["class"] != "ERROR" else 0.0 for i in arrow_ids]
    total = sum(d * 10 ** (len(digits) - 1 - i) for i, d in enumerate(digits))
    return round(total + sum(int(a) * 10 ** (-(i + 1)) for i, a in enumerate(arrows)), len(arrows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    pipeline = ImagePipeline(config)
    inference = get_inference_service()
    inference.initialize(config)

    prev_legacy = prev_cascade = None
    stats = {"frames": 0, "differ": 0, "nan_frames": 0, "unresolved": 0, "back_legacy": 0, "back_cascade": 0}
    examples = []
    for jpg in sorted(args.archive_dir.rglob("*.jpg")):
        rois, alignment = pipeline.process_whole_image(jpg.read_bytes())
        if not alignment.success or not rois:
            continue
        predictions = {}
        for image_id, (roi_bytes, model_type) in rois.items():
            result = inference.predict_from_bytes(model_type, roi_bytes)
            predictions[image_id] = {"id": image_id, "class": result["class"], "model": model_type}
        stats["frames"] += 1
        stats["nan_frames"] += int(any(p["class"] == "NAN" for p in predictions.values()))

        legacy = legacy_total(config, predictions)
        cascade, _ = calculate_total(config, predictions, previous_value=prev_cascade)
        if cascade is None:
            stats["unresolved"] += 1
            continue
        if abs(cascade - legacy) > 1e-9:
            stats["differ"] += 1
            if len(examples) < 15:
                examples.append((jpg.name, legacy, cascade))
        if prev_legacy is not None and legacy < prev_legacy - 0.002:
            stats["back_legacy"] += 1
        if prev_cascade is not None and cascade < prev_cascade - 0.002:
            stats["back_cascade"] += 1
        prev_legacy, prev_cascade = legacy, max(prev_cascade or cascade, cascade)

    print(stats)
    for name, legacy, cascade in examples:
        print(f"  {name}: legacy {legacy:.4f}  cascade {cascade:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Rebuild the debug container and run the cascade replay**

```bash
./debug.sh --detach
docker exec watermeter-dashboard-debug python /app/scripts/replay_cascade.py --archive-dir /data/raw_archive --config /config/config.yaml
```

The script must be in the image. If `/app/scripts` does not exist, check the Dockerfile's `COPY` lines. In that case, copy the script in with `docker cp scripts/replay_cascade.py watermeter-dashboard-debug:/app/scripts/` and run it from there. Do not change the Dockerfile for this.

Expected: `back_cascade` < `back_legacy`. Inspect the `examples` by eye against a few frames, cropping as in `scripts/replay_alignment_compare.py`.

- [ ] **Step 5: Docs and backlog.**
  - In `CLAUDE.md`, under "Reading pipeline" step 4, replace `Arrows: a classifier or a sin/cos regressor.` with `Arrows: a classifier or a single-output regressor (sigmoid·10; not circular).`
  - Add after step 5's first line: `Total: carry-aware cascade (position_utils.calculate_total) using the previous value; unresolvable readings are rejected.`
  - In `backlog.md`, add these under the matching sections, then set `Next ID: BL-80`:
    - `BL-76` `idea`: **sin/cos arrow regressor**. Training uses MSE on sigmoid, so 9.9↔0.0 is not adjacent; use a 2-output sin/cos head with a circular loss and `training_mode: continuous_sincos`.
    - `BL-77` `idea`: **OpenCV arrows: detect the dial centre per crop.** Today the crop centre is assumed, so any ROI offset becomes an angle error.
    - `BL-78` `idea`: **Held-out hand-labelled test set for this meter.** Digit accuracy figures are on training data; newer arrow labels are biased by OpenCV pre-labelling.
    - `BL-79` `idea`: **Correct DATA_PROVENANCE.md.** Most ground truth looks like upstream (jomjol-style) data, not self-collected.

- [ ] **Step 6: Full verification, commit, push**

```bash
.venv/bin/python -m pytest tests/unit tests/regression -q
uvx ruff check watermeter/ tests/ scripts/replay_plausibility.py scripts/replay_cascade.py
git add scripts/replay_plausibility.py scripts/replay_cascade.py backlog.md CLAUDE.md
git commit -m "claude: replay scripts for plausibility/cascade, backlog + docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push origin main && git push gitea main
```

Then wait for CI (`gh run list --branch main --limit 2`) and confirm `CI success`.
