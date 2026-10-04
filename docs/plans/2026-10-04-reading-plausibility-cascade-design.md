# Reading plausibility & cascaded total — design

Date: 2026-10-04 · Status: draft for review

## Problem

Research on 2026-10-03 analysed about 29.6k debug readings from 2026-06-08 to 2026-07-12. The main error source is the post-processing, not the models.

- **Only 13.8% of readings were accepted.** 25.4k were rejected as "Reverse detected", 24.5k of them while the service was stuck.
- **Stuck baseline.** `plausibility.validate_plausibility` rejects *any* decrease (zero tolerance) and has no recovery path. One slightly-too-high accepted value becomes the baseline. Every true reading below it is then rejected until the meter catches up or someone calls `/api/reset`.
  - Live example from 2026-10-04: 56.5999 was accepted at 06:17 UTC. After that, the true reading 56.5903 was rejected on every cycle.
- **No carry logic.** `position_utils.calculate_total` floors each arrow on its own (`int(arrow)`). A ±0.1–0.2 error near an integer flips the result by a full unit; this hits 17% of arrow reads on the fair subset. Digits are never checked against the 0.1 arrow.
- **NAN becomes 0.** A digit classified as `NAN` (wheel mid-roll) is silently counted as 0. That produced 2,621 replacements and every drop of ≥ 1 m³.

## Goals / non-goals

**Goals:**
- Small backward jitter no longer causes rejections.
- A wrong baseline recovers automatically.
- Arrows and digits are combined with carry awareness.
- `NAN` is resolved from context or the reading is rejected. It is never treated as 0.
- Home Assistant's `water_usage` never decreases (`state_class: total_increasing`).

**Non-goals:**
- sin/cos arrow regressor. It goes to the backlog: training uses plain MSE on a sigmoid, so 9.9→0.0 is not learned as adjacent.
- Recovering from a stuck state caused by an upward jump (e.g. after downtime). `/api/reset` stays the tool for that.
- Changing `correction.py`'s signal logic. Only its total recalculation is aligned with the new cascade.

## Part A — plausibility: reverse tolerance and re-anchoring

### A1. Reverse tolerance

New config field: `plausibility.reverse_tolerance` (m³), default `0.002`.

How `validate_plausibility` treats a new value compared with the previous one:

| Case | Condition | Outcome |
|---|---|---|
| Normal | `new >= prev` | Unchanged behaviour |
| Jitter | `prev - tolerance <= new < prev` | Valid, with warning `Minor reverse (jitter) …, holding previous value`. Does not count as a rejection. |
| Reverse | `new < prev - tolerance` | Rejected as today (`Reverse detected`) |

On acceptance the service sets the baseline to `max(prev, new)`, so jitter never lowers the baseline. The rate tracker receives the same held value.

### A2. Re-anchoring after consistent lower readings

New module `watermeter/reanchor.py` with `ReanchorTracker(required: int, max_spread: float, tolerance: float)`. It is pure, has no I/O, and is unit-testable:
- `add(value) -> bool` collects reverse-rejected values. It returns `True` once `required` consecutive values qualify. A sequence qualifies when:
  - `max - min <= max_spread`, and
  - it is non-decreasing within `tolerance` (the service passes `reverse_tolerance`).
- A value that breaks consistency restarts the sequence with that value.
- `reset()` is called on every accepted reading, after any non-reverse rejection, and on `/api/reset`.

New config fields:
- `plausibility.reanchor_after` (int), default `6`. `0` disables re-anchoring.
- `plausibility.reanchor_max_spread` (m³), default `0.01`.

What the service does when the tracker fires:
- Logs at WARNING: `Re-anchoring baseline X → Y after N consistent readings`, and adds the message to the reading's warnings, so it reaches HA via MQTT.
- Sets `previous_value = Y`.
- Resets the rate tracker. Otherwise rates go negative.
- Resets `consecutive_rejections` and persists state.
- Treats the reading as accepted, status `warning`.

### A3. Monotonic publishing (high-water mark)

HA reads any decrease of a `total_increasing` sensor as a meter reset.

- **Published value:** The value sent as `water_usage` becomes `max(published_high_water, previous_value)`.
  - `published_high_water` is the highest value ever published.
  - After a re-anchor, HA sees a flat line until the true value overtakes the old peak.
  - `water_usage_raw` is unchanged.
- **Persistence:** `StateStore` persists `published_value` next to `previous_value`.
  - `save(previous_value, last_update_time, published_value=None)` takes it as a new keyword argument.
  - A new method `load_published_value()` reads it back; `load()` keeps its signature.
  - Old state files without the key load as `None`, and the high-water mark then starts at `previous_value`.
- **Reset:** `/api/reset` clears the high-water mark. A meter replacement legitimately goes down.

## Part B — cascaded total

`calculate_total(config, predictions, previous_value=None)` stays pure. It returns `(total: Optional[float], raw_values)`. `raw_values` keeps `digits` (now the *resolved* digits) and `arrows`, and gains `notes: list[str]`, which describes any resolution applied.

### B1. Arrows, finest to coarsest

Let `a[0..n-1]` be the continuous arrow values, 0.1 dial first, each in [0, 10).

```
int[n-1] = floor(a[n-1])
for i = n-2 .. 0:
    int[i] = round(a[i] - a[i+1] / 10) mod 10
```

The idea is that a dial should read `int + finer/10`. Some examples:

| `a[i]` | `a[i+1]` | Today, `floor` | Cascade |
|---|---|---|---|
| 5.95 | 9.5 | 5 | 5 |
| 5.9 | 0.2 (just rolled) | 5 ✗ | 6 ✓ |
| 0.05 | 9.5 | 0 ✗ | 9 ✓ |

The cascade tolerates errors of up to ±0.5 dial units on each dial. An arrow with `ERROR` makes the reading invalid (`total = None`) instead of counting as 0.0.

### B2. Integer part (digits) with previous-value context

Let `D` be the classifier digits. `NAN` is a wildcard, `ERROR` makes the reading invalid. Let `f` be the resolved arrow fraction `Σ int[i]·10^-(i+1)`.

1. **No `previous_value`** (first reading, or after reset):
   - Use `D` as is.
   - Any `NAN` makes the reading invalid (`total = None`, note `"unresolved NAN digit without previous value"`).
2. **With `previous_value` P:**
   1. Let `p_int = floor(P)` and `p_frac = P - p_int`.
   2. Arrow-derived integer: `I = p_int + 1` if `f < p_frac - 0.5` (the fraction wrapped past 0), else `I = p_int`.
      - The threshold is half a unit because `max_rate_per_reading` (0.15) is far below 0.5, so a fraction drop of more than 0.5 can only mean a wrap.
   3. If `D`, treating `NAN` as a wildcard, matches one of `I-1, I, I+1` (zero-padded to the digit count), use `I`, with note `"integer part from carry context"` whenever it differs from the classifier reading.
      - This covers early or late rolling wheels and `NAN`.
   4. Otherwise, with no `NAN`, use `D`. This is a large real jump, e.g. after downtime, and plausibility decides.
   5. Otherwise the reading is invalid.

Note on the earlier draft: `transition_start` (8.0) is dropped. With the previous value available, B2 resolves early and late wheel rolls without a per-meter threshold. Without a previous value, a threshold cannot decide between the old and the new digit either.

### B3. Callers

- **`WatermeterService.process_reading`:**
  - Passes `self.previous_value`.
  - If `total is None`, the reading is rejected: it counts as a rejection, the status is `rejected`, and the reasons are the notes. Plausibility is skipped.
  - Notes are appended to the warnings.
- **`oneshot.py`:** Without a previous value it prints the notes and exits 1 if `total is None`.
- **`CorrectionEngine._recalculate_with_replacement`:** Delegates to `calculate_total` with the replaced prediction, so hypothetical totals use the same cascade instead of a duplicated flooring loop.
- **`_compute_raw_total`:** Receives the resolved digits, so a `NAN` no longer shows up as 0.

## Config & schema

New `plausibility` fields, all in the `config_utils.py` schema and documented in the shipped `config.yaml`:

| Field | Default |
|---|---|
| `reverse_tolerance` | `0.002` |
| `reanchor_after` | `6` |
| `reanchor_max_spread` | `0.01` |

## Testing

TDD with real code and no mocks of the logic under test.

- **`tests/unit/test_reanchor.py`:**
  - The tracker fires after N values.
  - Spread and monotonicity break the sequence.
  - `reset()` clears it.
  - `reanchor_after: 0` disables re-anchoring.
- **`tests/unit/test_plausibility.py`:** Extended with the jitter band, its boundaries, and that a reverse beyond the tolerance still rejects.
- **`tests/unit/test_calculate_total.py`:**
  - The B1 table cases, plus the wrap at the 0.1 dial.
  - `NAN` without a previous value makes the reading invalid; with a previous value it is resolved.
  - An early-rolled and a late-rolled wheel.
  - A large jump keeps `D`.
  - `ERROR` makes the reading invalid.
- **Service-level tests** in the existing style, which mocks the fetch:
  - Jitter is held.
  - The live sequence `56.5999` then 6× `56.5903` re-anchors, while the published value stays 56.5999 until it is overtaken.
  - The high-water mark survives a `StateStore` round trip.
  - `/api/reset` clears the high-water mark.
- **Regression:**
  - `scripts/replay_plausibility.py` replays the logged totals (debug logs) through the old and new plausibility settings and reports the acceptance rate, the re-anchor count, and the largest accepted downward step, which must be ≤ `reverse_tolerance` except at re-anchors.
  - The cascade (B) is replayed on the archived raw frames inside the debug container (port 8002), comparing old and new totals.

## Rollout

- Code defaults equal the shipped defaults. Existing installs get the tolerance and re-anchoring on update, because this fixes the stuck state for everyone.
- `reanchor_after: 0` restores today's strict behaviour.
- The debug container runs it first. Production (8001) is not touched.
