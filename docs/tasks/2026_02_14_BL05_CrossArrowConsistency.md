# BL-05: Cross-Arrow Consistency

## Goal

Add a cross-arrow consistency signal to BL-04's correction engine. On a mechanical water
meter, adjacent arrow dials are geared together: the position of a more-significant arrow
constrains which half of the dial the less-significant arrow should be in. BL-04's Signal 3
(`_check_consistency_improvement`) already checks the binary half/upper relationship between
immediate neighbors. BL-05 goes further by using the **exact arrow reading** of the
more-significant dial to **filter the candidate set** for the less-significant dial, narrowing
alternatives more precisely than the existing binary check.

**Constraint**: This feature only works between arrow positions (analog_N to analog_N+1). It
does not apply to digit-digit or digit-arrow pairs, which are already handled by Signal 3.

## Architecture

### How it extends BL-04

BL-05 plugs into `correct_predictions()` as **Signal 4** (a new signal alongside the existing
three). It scores each alternative candidate for a low-confidence arrow position by checking
whether the candidate is consistent with the reading of the adjacent more-significant arrow.

The modification is minimal:
1. Add a new method `_check_cross_arrow_consistency()` to `WatermeterService`.
2. In the alternative-scoring loop inside `correct_predictions()`, call this method and
   add +1 to the score if the alternative satisfies the cross-arrow constraint.
3. Bump `min_signal_agreement` max in the config schema from 4 to 5 (one more signal).

No changes to the correction framework structure, no new config section, no new API endpoints.

### Cross-Arrow Consistency Rule

**The mechanical relationship**: On a water meter, arrow dial N drives arrow dial N+1 via a
10:1 gear ratio. When dial N's needle points to X (integer), dial N+1 has completed a full
rotation and is near 0. As dial N moves from X toward X+1, dial N+1 sweeps from 0 through 9.

**The constraint**: Given `analog_i` reads value A and `analog_{i+1}` reads value B:
- The fractional progress of `analog_i` through its current integer position tells us roughly
  where `analog_{i+1}` should be.
- Specifically, if A's integer part is solidly at X (the needle is not near a transition),
  then B should be in the lower half (0-4). If A is near a transition (needle between X and
  X+1), then B should be in the upper half (5-9).

**Wait -- doesn't Signal 3 already do this?** Yes, Signal 3's `has_violation()` checks
exactly this binary half/upper relationship. But Signal 3 only checks the current prediction
vs. one adjacent neighbor. BL-05's added value is:

1. **Candidate filtering with finer granularity**: With the current 1.0-step arrow model
   (classes `0.0` through `9.0`), all arrows report integer positions, so fractional
   information is limited. But the model's softmax distribution still tells us something:
   if `analog_1` has high confidence at `3.0`, the needle is solidly at 3 (not transitioning).
   This means `analog_2` should be in the lower half. If `analog_1` has *low* confidence
   with `3.0` and `4.0` close in softmax, the needle is near the transition, meaning
   `analog_2` could be in either half. **BL-05 uses the confidence of the constraining
   arrow (not just its class) to modulate signal strength.**

2. **Transitive constraint across non-adjacent arrows**: `analog_1` constrains `analog_2`,
   and `analog_2` constrains `analog_3`. If corrections have already been applied to
   `analog_2` (earlier in the same pass), the corrected value propagates to constrain
   `analog_3`. BL-04 already processes positions in order and updates predictions in-place,
   so this transitivity comes for free -- but it only works if we add the cross-arrow signal
   that propagates the constraint.

### Current limitation: 1.0-step arrow classes

The arrow model currently classifies into `['0.0', '1.0', ..., '9.0']` -- integer positions
only. This means:
- We cannot distinguish "needle solidly at 3" from "needle at 3.2" from "needle at 3.8".
- The half/upper constraint degrades to: if `analog_i` reads `X.0`, then `analog_{i+1}`
  should be in the lower half (0-4). But we don't know if `analog_i` is *really* at X.0 or
  somewhere between X.0 and X+1.0.
- **Mitigation**: Use the constraining arrow's confidence as a proxy for how close it is to
  the reported class. High confidence = solidly at X.0 = strong constraint. Low confidence =
  ambiguous = weak/no constraint.

When BL-08 (regression mode) lands, arrow readings will have continuous values (e.g., 3.7),
making the cross-arrow constraint much more precise. BL-05 is designed to work with both
discrete and continuous arrow values.

### Implementation approach

Add as Signal 4 inside the existing `correct_predictions()` loop. This is the natural
insertion point because:
- It scores alternatives using the same mechanism as Signals 1-3.
- It benefits from in-place correction propagation (earlier corrections inform later ones).
- It respects the same `min_signal_agreement` threshold -- it never acts alone.

**New method**: `_check_cross_arrow_consistency()`

```python
def _check_cross_arrow_consistency(
    self,
    predictions: Dict[str, Dict],
    replace_id: str,
    replace_class: str
) -> bool:
    """
    Check if replacing an arrow position with an alternative improves
    cross-arrow consistency with the adjacent more-significant arrow.

    Only applies to arrow-arrow pairs (analog_N, analog_N+1).
    Uses the constraining arrow's confidence to modulate signal strength:
    only fires when the constraining arrow has high confidence (solidly at
    its reported position, not transitioning).

    Returns True if:
    - replace_id is an arrow position (analog_*)
    - The adjacent more-significant position is also an arrow
    - That arrow has high confidence (>= cross_arrow_confidence_gate)
    - The current prediction violates the expected half constraint, AND
    - The replacement satisfies it (or at least improves it)
    """
    position_ids = self._get_ordered_position_ids()
    if replace_id not in position_ids:
        return False

    idx = position_ids.index(replace_id)

    # Only applies when the more-significant neighbor is also an arrow
    if idx == 0:
        return False

    prev_id = position_ids[idx - 1]
    if prev_id not in predictions:
        return False

    # Both must be arrows
    if predictions[prev_id]['model'] != 'arrows' or predictions[replace_id]['model'] != 'arrows':
        return False

    # Confidence gate: only constrain when the more-significant arrow is confident
    config = self.config.get('correction', {})
    confidence_gate = config.get('cross_arrow_confidence_gate', 0.8)
    if predictions[prev_id]['confidence'] < confidence_gate:
        return False  # Constraining arrow is ambiguous, can't trust its position

    prev_class = predictions[prev_id]['class']
    if prev_class in ('NAN', 'ERROR'):
        return False

    prev_value = float(prev_class)
    prev_int = int(prev_value)

    # Determine expected half for the less-significant arrow:
    # If constraining arrow is solidly at an integer (high confidence),
    # the less-significant arrow should be in the LOWER half (0-4).
    # Why: the constraining arrow reads X.0, meaning the less-significant
    # dial has not yet pushed it past X -- so less-significant is near 0.
    expected_lower_half = True  # Confident at integer => next dial in lower half

    curr_class = predictions[replace_id]['class']
    alt_class = replace_class

    if curr_class in ('NAN', 'ERROR'):
        return False

    curr_int = int(float(curr_class))
    alt_int = int(float(alt_class))

    curr_in_expected = (curr_int < 5) == expected_lower_half
    alt_in_expected = (alt_int < 5) == expected_lower_half

    # Improvement: current violates, alternative satisfies
    return (not curr_in_expected) and alt_in_expected
```

**Integration in `correct_predictions()`**: Add after the Signal 3 block:

```python
# Signal 4: Cross-arrow consistency (BL-05)
if self._check_cross_arrow_consistency(predictions, pid, alt['class']):
    score += 1
```

### Config changes

Minimal -- one new optional parameter under the existing `correction` section:

```yaml
correction:
  # ... existing BL-04 fields ...
  cross_arrow_confidence_gate: 0.8  # Only use cross-arrow signal when constraining arrow >= this
```

Schema addition (one new property in the existing `correction` schema in `config_utils.py`):

```python
"cross_arrow_confidence_gate": {
    "type": "number",
    "minimum": 0,
    "maximum": 1,
    "description": "Confidence gate for cross-arrow consistency: only constrain when the more-significant arrow exceeds this threshold"
}
```

### Parallax caveat

Outer dial positions on the meter face are photographed at a steeper angle, which can make
the needle appear shifted. This means the cross-arrow constraint may be less reliable for
the outermost arrows (analog_3, analog_4). The confidence gate already provides natural
protection: parallax-affected readings tend to have lower model confidence, so the signal
will not fire for uncertain constraining arrows. No additional parallax-specific logic is
needed in the first implementation.

## Work Packages

### WP1: Cross-arrow signal + config

**Files**:
- `watermeter/watermeter_service.py` -- add `_check_cross_arrow_consistency()` method, add
  Signal 4 call in `correct_predictions()`, add `cross_arrow_confidence_gate` config read
- `watermeter/config_utils.py` -- add `cross_arrow_confidence_gate` to `correction` schema
- `config.yaml` -- add `cross_arrow_confidence_gate: 0.8` under `correction:`

**Details**:

1. Add `_check_cross_arrow_consistency()` method after `_check_consistency_improvement()`
   (after line 839).

2. In `correct_predictions()`, add Signal 4 after the Signal 3 block (after line 928):
   ```python
   # Signal 4: Cross-arrow consistency (BL-05)
   if self._check_cross_arrow_consistency(predictions, pid, alt['class']):
       score += 1
   ```

3. Add schema property and config default.

### WP2: Unit tests

**File**: `tests/unit/test_cross_arrow_consistency.py` (new file)

**Test approach**: Same pattern as `test_value_correction.py` -- create a minimal
`WatermeterService` via `object.__new__()`, set up synthetic predictions and config.

**Test cases**:

1. **`test_cross_arrow_improves_consistency`**: analog_1 reads `3.0` at 0.95 confidence.
   analog_2 reads `7.0` (upper half -- wrong, should be lower since analog_1 is solidly
   at 3). Alternative `2.0` (lower half) should return True.

2. **`test_cross_arrow_no_improvement_when_already_consistent`**: analog_1 reads `3.0` at
   0.95, analog_2 reads `2.0` (lower half -- correct). Alternative `7.0` should return
   False (would make it worse).

3. **`test_cross_arrow_skipped_low_confidence_constrainer`**: analog_1 reads `3.0` but at
   only 0.5 confidence (below gate). Even though analog_2 is in the wrong half, the signal
   should not fire (returns False) because the constraining arrow is uncertain.

4. **`test_cross_arrow_skipped_for_digit_positions`**: digit_3 and analog_1 pair. Signal
   should not fire (only applies to arrow-arrow pairs).

5. **`test_cross_arrow_skipped_for_first_position`**: analog_1 has no more-significant arrow
   neighbor. Should return False.

6. **`test_cross_arrow_signal_in_correction_engine`**: End-to-end test through
   `correct_predictions()`. Set up analog_1 high-confidence at `3.0`, analog_2 low-confidence
   at `7.0` with `2.0` as top_k alternative. With `min_signal_agreement: 1` and only the
   cross-arrow signal active (no previous value, no rate history, no adjacent consistency
   violation), verify that analog_2 gets corrected to `2.0`.

7. **`test_cross_arrow_config_gate_respected`**: Set `cross_arrow_confidence_gate: 0.95`.
   Constraining arrow at 0.90 confidence. Signal should not fire.

8. **`test_cross_arrow_transitive_propagation`**: analog_1 at `3.0` (high conf), analog_2
   corrected from `7.0` to `2.0` (in earlier pass), analog_3 at `8.0` (low conf). After
   analog_2 correction, analog_2 reads `2.0` (lower half), so analog_3 should be in the
   lower half too. Verify that the correction propagates through the in-place update.

## Progress Log

- 2026-02-14: Task document created. Architecture designed as Signal 4 extension to BL-04's
  correction engine. Two work packages: implementation + unit tests.

## Done Criteria

- [ ] WP1: `_check_cross_arrow_consistency()` implemented in `watermeter_service.py`
- [ ] WP1: Signal 4 integrated into `correct_predictions()` scoring loop
- [ ] WP1: Config schema updated with `cross_arrow_confidence_gate`
- [ ] WP1: `config.yaml` updated with default value
- [ ] WP2: All 8 unit tests pass (`python -m pytest tests/unit/test_cross_arrow_consistency.py -v`)
- [ ] Full test suite passes (`python -m pytest tests/unit/ tests/regression/ --tb=short -q`)
- [ ] `backlog.md` BL-05 status updated to `in-progress`
