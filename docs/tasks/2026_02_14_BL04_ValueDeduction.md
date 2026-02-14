# BL-04: Value Deduction from Rules

## Goal

Correct misread positions using temporal and spatial context, via a confidence-weighted
correction engine inserted between `calculate_total()` and `check_consistency()` in
`process_reading()`. The engine uses four signals (previous value, expected rate, adjacent
position consistency, model softmax top-K) to identify likely misreads and replace them with
better candidates from the softmax distribution. All corrections are flagged as warnings for
full transparency.

**Philosophy**: Conservative. It is better to NOT correct than to introduce a wrong value.
The engine should only act when there is strong, convergent evidence from multiple signals
that the top-1 prediction is wrong, AND a clearly better alternative exists in the softmax
distribution.

## Architecture

### Correction Algorithm Overview

The correction pipeline runs after `calculate_total()` produces a raw value from top-1
predictions. It inspects each position's confidence, compares the raw value against temporal
context (previous value, expected rate) and spatial context (adjacent positions), and decides
whether to replace any position's prediction with an alternative from its softmax top-K.

```
process_reading()
  1. fetch images
  2. run_inference()  -> predictions (with top_k if correction enabled)
  3. calculate_total() -> raw total, raw_values
  4. correct_predictions()  <-- NEW (BL-04)
  5. recalculate total if corrections were made
  6. check_consistency()
  7. validate_plausibility()
  8. handle low confidence
  9. update state
```

The correction operates on the `predictions` dict in-place, updating `class` and `confidence`
fields for corrected positions. It returns a list of correction warning strings.

### Signal 1: Previous Value Constraint

**Principle**: A water meter only goes forward. The new reading must be >= `self.previous_value`.

**How it constrains**: If `previous_value = 347.1234` and the raw reading is `337.xxxx`, then
digit_1 reading of `3` might be wrong -- it should probably be `3` (same) or higher. But since
the hundreds digit went from `3` to `3` while the tens digit went from `4` to `3`, the reading
went backwards. The correction engine checks whether replacing a low-confidence position with
a higher alternative from its softmax resolves the backward jump.

**Signal strength**: Strong when `previous_value` is available. No signal on first reading
(when `self.previous_value is None`).

**Edge case -- meter rollover**: When a 3-digit meter rolls from 999 to 000, the new value
is legitimately lower than previous. The correction engine must NOT "correct" this rollover.
Guard: if all digit positions read 0 or near-0 AND previous_value is near the max for the
digit count (e.g. > 900 for 3-digit), skip the previous-value signal entirely.

### Signal 2: Expected Rate

**Principle**: Consumption follows a roughly predictable pattern. Using `rate_history`, we can
estimate an expected range for the next reading.

**How it constrains**: Calculate `expected_next = previous_value + avg_rate_per_hour * hours_elapsed`.
Define a plausible window: `[previous_value, expected_next * rate_tolerance_factor]`. Readings
far outside this window are suspect.

**Signal strength**: Weak when rate_history has < 3 entries. Moderate otherwise. This signal
should never act alone -- it only adds weight to other signals.

**Implementation**:
```python
def _estimate_expected_range(self) -> Optional[Tuple[float, float]]:
    """
    Estimate plausible range for next reading based on rate history.

    Returns:
        (min_expected, max_expected) or None if insufficient history.
    """
    if self.previous_value is None or len(self.rate_history) < 3:
        return None

    avg_rate = self._calculate_average_rate_per_hour()
    if avg_rate is None or avg_rate <= 0:
        return None

    hours_elapsed = 0.0
    if self.last_update_time:
        hours_elapsed = (datetime.now() - self.last_update_time).total_seconds() / 3600

    if hours_elapsed <= 0:
        return None

    expected_delta = avg_rate * hours_elapsed
    config = self.config.get('correction', {})
    tolerance = config.get('rate_tolerance_factor', 3.0)

    min_expected = self.previous_value  # Meter only goes up
    max_expected = self.previous_value + expected_delta * tolerance

    return (min_expected, max_expected)
```

### Signal 3: Adjacent Position Consistency

**Principle**: On a mechanical water meter, when an arrow dial reads X.5 (the needle is
between two digits, in the upper half), the next-more-significant position must be >= 5.
This is because the gear mechanism means the next dial has completed at least half its rotation.

**How it constrains**: For each adjacent pair `(position_i, position_i+1)` from least
significant to most significant:
- If `position_i` has a fractional part >= 0.4 (representing the "upper half" / transition
  zone), then `position_i+1` (the next dial to the right in the predictions order) should
  have integer value >= 5.
- If `position_i` has fractional part < 0.4 (solidly in lower half), then `position_i+1`
  should be < 5.

This is the same logic already in `check_consistency()` (lines 508-568), but here we use it
as a corrective signal rather than just a warning. If the consistency check fails and one of
the two positions has low confidence, we look at its softmax for an alternative that restores
consistency.

**Signal strength**: Strong when it agrees with Signal 1 or Signal 2. On its own, moderate --
the "half" boundary is fuzzy (an arrow near exactly X.0 or X.5 is ambiguous).

**Important**: Only arrows have sub-integer values. Digits are always integers. The consistency
signal applies to (arrow, next_position) pairs and (arrow, arrow) pairs, not (digit, digit)
pairs.

### Signal 4: Model Softmax (Top-K)

**Principle**: The model's softmax distribution contains more information than just the argmax.
If the top-1 class has 0.42 confidence but the 2nd-best has 0.38, the model is nearly
equally uncertain between the two. If context favors the 2nd-best, we should use it.

**How it provides alternatives**: `predict_detailed()` returns the top-K classes with their
probabilities. The correction engine considers these alternatives as replacement candidates.

**Candidate filtering**:
- Only consider alternatives with confidence >= `min_alternative_confidence` (default: 0.05).
  Below this, the model has essentially no support for the class.
- For digits: alternatives are other digits 0-9 (NAN is never a valid correction target).
- For arrows: alternatives are other arrow positions 0.0-9.9.

**How predict_detailed() works**:
```python
def predict_detailed(self, image_path, top_k=3):
    """
    Run inference and return top-K predictions with softmax probabilities.

    Args:
        image_path: Path to the input image.
        top_k: Number of top predictions to return (default 3).

    Returns:
        List of {'class': str, 'confidence': float} dicts, sorted by
        confidence descending. Length is min(top_k, num_classes).
    """
    img = self.preprocess(image_path)
    result = self.compiled([img])[self.compiled.output(0)][0]
    result = result - result.max()
    probs = np.exp(result) / np.exp(result).sum()

    # Get top-K indices
    top_indices = probs.argsort()[::-1][:top_k]

    return [
        {'class': self.classes[idx], 'confidence': float(probs[idx])}
        for idx in top_indices
    ]
```

### Correction Decision Logic

The correction engine evaluates each position and decides whether to replace its prediction.
The core principle: **a correction only happens when the position is low-confidence AND
multiple signals agree on a better alternative**.

**Pseudocode for `correct_predictions()`**:

```python
def correct_predictions(
    self,
    predictions: Dict[str, Dict],
    raw_total: float,
    raw_values: Dict
) -> List[str]:
    """
    Attempt to correct low-confidence predictions using contextual signals.

    Modifies predictions dict in-place for corrected positions.

    Args:
        predictions: Dict mapping position ID to prediction dict.
                     Each pred has 'class', 'confidence', 'model', 'top_k' (if available).
        raw_total: The total value from calculate_total() before correction.
        raw_values: The raw_values dict from calculate_total().

    Returns:
        List of correction warning strings.
    """
    config = self.config.get('correction', {})
    if not config.get('enabled', False):
        return []

    correction_threshold = config.get('confidence_threshold', 0.7)
    min_signal_agreement = config.get('min_signal_agreement', 2)
    min_alternative_confidence = config.get('min_alternative_confidence', 0.05)
    corrections = []

    # Build ordered position list (most significant to least significant)
    position_ids = self._get_ordered_position_ids()

    # Quick exit: if all positions are above correction_threshold, don't touch anything
    all_confident = all(
        predictions[pid]['confidence'] >= correction_threshold
        for pid in position_ids
        if pid in predictions
    )
    if all_confident:
        return []

    # Get contextual signals
    expected_range = self._estimate_expected_range()

    # Evaluate each position
    for pid in position_ids:
        if pid not in predictions:
            continue

        pred = predictions[pid]

        # Skip high-confidence positions (safety rule)
        if pred['confidence'] >= correction_threshold:
            continue

        # Skip positions without top_k data
        top_k = pred.get('top_k', [])
        if not top_k or len(top_k) < 2:
            continue

        # Get alternatives (exclude current top-1 and NAN)
        alternatives = [
            alt for alt in top_k[1:]  # Skip top-1 (already current)
            if alt['confidence'] >= min_alternative_confidence
            and alt['class'] != 'NAN'
        ]
        if not alternatives:
            continue

        # Score each alternative
        best_alt = None
        best_score = 0

        for alt in alternatives:
            score = 0

            # Signal 1: Previous value constraint
            if self.previous_value is not None:
                total_with_alt = self._recalculate_with_replacement(
                    predictions, pid, alt['class'], raw_values
                )
                if total_with_alt >= self.previous_value:
                    # Check if original went backwards
                    if raw_total < self.previous_value:
                        score += 1  # Alt fixes a backward reading

            # Signal 2: Expected rate
            if expected_range is not None:
                total_with_alt = self._recalculate_with_replacement(
                    predictions, pid, alt['class'], raw_values
                )
                min_exp, max_exp = expected_range
                if min_exp <= total_with_alt <= max_exp:
                    # Check if original was outside range
                    if raw_total < min_exp or raw_total > max_exp:
                        score += 1  # Alt is within expected range

            # Signal 3: Adjacent position consistency
            consistency_improvement = self._check_consistency_improvement(
                predictions, pid, alt['class']
            )
            if consistency_improvement:
                score += 1  # Alt fixes a consistency violation

            # Track best alternative
            if score > best_score:
                best_score = score
                best_alt = alt

        # Apply correction only if enough signals agree
        if best_alt is not None and best_score >= min_signal_agreement:
            old_class = pred['class']
            old_conf = pred['confidence']
            pred['class'] = best_alt['class']
            pred['confidence'] = best_alt['confidence']
            pred['corrected_from'] = old_class
            pred['corrected_confidence'] = old_conf
            pred['correction_signals'] = best_score

            msg = (
                f"Corrected {pid}: {old_class}\u2192{best_alt['class']} "
                f"(conf={old_conf:.2f}\u2192{best_alt['confidence']:.2f}, "
                f"signals={best_score}/{min_signal_agreement})"
            )
            corrections.append(msg)

    return corrections
```

**Safety checks (summary)**:
1. **Feature gate**: `correction.enabled` must be True (default: False -- opt-in).
2. **All-confident guard**: If every position exceeds `correction_threshold`, exit immediately.
3. **Minimum signal agreement**: At least `min_signal_agreement` (default: 2) signals must
   favor the alternative before we apply it.
4. **No NAN corrections**: Never correct a position TO 'NAN'. (Correcting FROM 'NAN' is
   also excluded because NAN positions are already handled as errors in calculate_total.)
5. **One correction per pass**: Process positions from most to least significant. Each
   correction immediately updates the predictions dict, so subsequent positions see the
   corrected state. Limit to at most 2 corrections per reading (configurable via
   `max_corrections_per_reading`).
6. **Meter rollover guard**: If digits suggest a rollover (all near-0 and previous was near-max),
   skip Signal 1 (previous value constraint) entirely.

### Helper Methods

```python
def _get_ordered_position_ids(self) -> List[str]:
    """
    Return position IDs in order: digit_1, digit_2, ..., analog_1, analog_2, ...
    (Most significant to least significant.)
    """
    process_separate = self.config['images'].get('process_separate', False)

    if process_separate:
        digit_ids = self.config['images']['digits']
        arrow_ids = self.config['images']['arrows']
    else:
        detection = self.config.get('detection', {})
        digit_count = detection.get('digits', {}).get('count', 0)
        analog_count = detection.get('analogs', {}).get('count', 0)
        digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
        arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

    return digit_ids + arrow_ids


def _recalculate_with_replacement(
    self,
    predictions: Dict[str, Dict],
    replace_id: str,
    replace_class: str,
    raw_values: Dict
) -> float:
    """
    Calculate what the total would be if replace_id's class were replace_class.

    Does NOT modify predictions -- computes hypothetically.
    Uses the same positional arithmetic as calculate_total().
    """
    position_ids = self._get_ordered_position_ids()
    process_separate = self.config['images'].get('process_separate', False)

    if process_separate:
        digit_ids = self.config['images']['digits']
        arrow_ids = self.config['images']['arrows']
    else:
        detection = self.config.get('detection', {})
        digit_count = detection.get('digits', {}).get('count', 0)
        analog_count = detection.get('analogs', {}).get('count', 0)
        digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
        arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

    digits = []
    arrows = []

    for image_id in digit_ids:
        if image_id in predictions:
            cls = replace_class if image_id == replace_id else predictions[image_id]['class']
            if cls not in ('NAN', 'ERROR'):
                digits.append(int(cls))
            else:
                digits.append(0)

    for image_id in arrow_ids:
        if image_id in predictions:
            cls = replace_class if image_id == replace_id else predictions[image_id]['class']
            if cls != 'ERROR':
                arrows.append(float(cls))
            else:
                arrows.append(0.0)

    total = 0.0
    for i, digit in enumerate(digits):
        multiplier = 10 ** (len(digits) - 1 - i)
        total += digit * multiplier

    for i, arrow in enumerate(arrows):
        multiplier = 10 ** (-(i + 1))
        total += int(arrow) * multiplier

    return total


def _check_consistency_improvement(
    self,
    predictions: Dict[str, Dict],
    replace_id: str,
    replace_class: str
) -> bool:
    """
    Check if replacing replace_id with replace_class resolves a consistency
    violation that exists with the current prediction.

    Returns True if:
    - Current prediction creates a consistency violation with an adjacent position, AND
    - The replacement resolves it (or at least doesn't create a new one).
    """
    position_ids = self._get_ordered_position_ids()

    if replace_id not in position_ids:
        return False

    idx = position_ids.index(replace_id)

    # Check pairs involving this position
    def has_violation(pid_a, val_a, pid_b, val_b):
        """Check half/upper consistency between adjacent positions."""
        frac = val_a % 1
        has_half = frac >= 0.4
        upper = int(val_b) >= 5
        return has_half != upper

    def get_value(pid, override_id=None, override_class=None):
        if pid not in predictions:
            return None
        cls = override_class if pid == override_id else predictions[pid]['class']
        if cls in ('NAN', 'ERROR'):
            return None
        if predictions[pid]['model'] == 'digits':
            return int(cls) if pid != override_id else int(cls)
        else:
            return float(cls)

    # Check pair with previous position (idx-1, idx)
    current_violations = 0
    replacement_violations = 0

    if idx > 0:
        prev_id = position_ids[idx - 1]
        prev_val = get_value(prev_id)
        curr_val = get_value(replace_id)
        alt_val = get_value(replace_id, replace_id, replace_class)

        if prev_val is not None and curr_val is not None:
            if has_violation(prev_id, prev_val, replace_id, curr_val):
                current_violations += 1
            if alt_val is not None and has_violation(prev_id, prev_val, replace_id, alt_val):
                replacement_violations += 1

    # Check pair with next position (idx, idx+1)
    if idx < len(position_ids) - 1:
        next_id = position_ids[idx + 1]
        next_val = get_value(next_id)
        curr_val = get_value(replace_id)
        alt_val = get_value(replace_id, replace_id, replace_class)

        if next_val is not None and curr_val is not None:
            if has_violation(replace_id, curr_val, next_id, next_val):
                current_violations += 1
            if alt_val is not None and has_violation(replace_id, alt_val, next_id, next_val):
                replacement_violations += 1

    # Improvement = had violations that are now resolved
    return current_violations > 0 and replacement_violations < current_violations
```

### Config Changes

New top-level section `correction` in `config.yaml` (after `plausibility`):

```yaml
# Value Correction (BL-04)
correction:
  enabled: false                    # Opt-in; disabled by default for safety
  confidence_threshold: 0.7         # Only consider correcting positions below this confidence
  min_signal_agreement: 2           # Minimum number of signals that must agree on an alternative
  min_alternative_confidence: 0.05  # Ignore softmax alternatives below this probability
  rate_tolerance_factor: 3.0        # Expected rate window: avg_rate * this factor
  max_corrections_per_reading: 2    # Maximum positions to correct in a single reading
  top_k: 3                         # Number of softmax alternatives to consider
```

Schema addition in `config_utils.py` CONFIG_SCHEMA (new top-level property alongside
`plausibility`, `low_confidence`, etc.):

```python
"correction": {
    "type": "object",
    "description": "Value correction using temporal and spatial context (BL-04)",
    "properties": {
        "enabled": {
            "type": "boolean",
            "description": "Enable confidence-weighted value correction",
            "default": False
        },
        "confidence_threshold": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Only correct positions with confidence below this threshold"
        },
        "min_signal_agreement": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "description": "Minimum number of contextual signals that must agree to apply correction"
        },
        "min_alternative_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Minimum softmax probability for an alternative to be considered"
        },
        "rate_tolerance_factor": {
            "type": "number",
            "minimum": 1.0,
            "description": "Multiplier for expected rate to define plausible window"
        },
        "max_corrections_per_reading": {
            "type": "integer",
            "minimum": 1,
            "maximum": 7,
            "description": "Maximum number of positions to correct per reading"
        },
        "top_k": {
            "type": "integer",
            "minimum": 2,
            "maximum": 10,
            "description": "Number of softmax alternatives to retrieve from model"
        }
    }
}
```

### Warning/Transparency

Every correction produces a warning string appended to `all_warnings` in `process_reading()`.
Format:

```
Corrected digit_3: 3->9 (conf=0.42->0.38, signals=2/2)
```

This flows to:
- **Dashboard**: rendered in the warnings list (existing template iterates `warnings`).
- **MQTT payload**: included in the `warnings` array attribute (already published).
- **Logs**: logged at WARNING level.

The `predictions` list in `current_state` will include extra fields for corrected positions:
- `corrected_from`: original class before correction
- `corrected_confidence`: original confidence before correction
- `correction_signals`: number of agreeing signals

This allows the UI team to highlight corrected positions (e.g. with a different color or icon)
without any backend template changes.

## Work Packages

### WP1: predict_detailed() on Classifier

**File**: `watermeter/inference.py`

**Change 1**: Add `predict_detailed()` method to `Classifier` class (after `predict()`, line 48):

```python
def predict_detailed(self, image_path, top_k=3):
    """
    Run inference and return top-K predictions with softmax probabilities.

    Args:
        image_path: Path to the input image.
        top_k: Number of top predictions to return.

    Returns:
        List of {'class': str, 'confidence': float} dicts, sorted by
        confidence descending. Length is min(top_k, len(self.classes)).
    """
    img = self.preprocess(image_path)
    result = self.compiled([img])[self.compiled.output(0)][0]
    result = result - result.max()
    probs = np.exp(result) / np.exp(result).sum()

    k = min(top_k, len(self.classes))
    top_indices = probs.argsort()[::-1][:k]

    return [
        {'class': self.classes[idx], 'confidence': float(probs[idx])}
        for idx in top_indices
    ]
```

**Change 2**: Add `predict_detailed()` method to `InferenceService` class (after `predict()`,
line 171):

```python
def predict_detailed(self, model_type: str, image_path: str, top_k: int = 3) -> list:
    """
    Run inference and return top-K predictions.

    Args:
        model_type: "digits" or "arrows"
        image_path: Path to image file
        top_k: Number of top predictions to return

    Returns:
        List of {'class': str, 'confidence': float} dicts
    """
    with self._lock:
        if model_type == 'digits':
            return self._digits_classifier.predict_detailed(image_path, top_k)
        elif model_type == 'arrows':
            return self._arrows_classifier.predict_detailed(image_path, top_k)
        else:
            raise ValueError(f"Unknown model type: {model_type}")
```

**Design note**: `predict_detailed()` shares the preprocessing and softmax code with
`predict()`. To avoid duplication we could refactor into a shared internal method. However,
keeping them separate avoids any risk of breaking the hot path (`predict()` is called on every
reading; `predict_detailed()` only when correction is enabled). The duplication is minimal
(5 lines of softmax + argmax). Decision: accept the duplication for safety.

### WP2: Enhanced run_inference

**File**: `watermeter/watermeter_service.py`

**Method**: `run_inference()` (line 387)

**Change**: When `correction.enabled` is True, call `predict_detailed()` instead of
`predict()` and store the top-K list in the prediction dict under a `top_k` key.

Modified block inside the try (replacing lines 412-420):

```python
try:
    correction_config = self.config.get('correction', {})
    if correction_config.get('enabled', False):
        top_k_count = correction_config.get('top_k', 3)
        top_k_results = get_inference_service().predict_detailed(
            image_class, str(temp_path), top_k=top_k_count
        )
        # Top-1 result for backward compatibility
        result = top_k_results[0]
        predictions[image_id] = {
            'id': image_id,
            'class': result['class'],
            'confidence': result['confidence'],
            'model': image_class,
            'image_bytes': image_bytes,
            'top_k': top_k_results,
        }
    else:
        result = get_inference_service().predict(image_class, str(temp_path))
        predictions[image_id] = {
            'id': image_id,
            'class': result['class'],
            'confidence': result['confidence'],
            'model': image_class,
            'image_bytes': image_bytes,
        }
    logger.debug(f"{image_id}: {result['class']} ({result['confidence']:.3f})")
```

**Impact**: When correction is disabled (default), `run_inference()` is identical to current
behavior. No performance impact. When enabled, each position makes one inference call that
returns slightly more data (top-K instead of top-1), using the same underlying model forward
pass.

### WP3: Correction engine

**File**: `watermeter/watermeter_service.py`

**New methods** on `WatermeterService`:

1. `correct_predictions(self, predictions, raw_total, raw_values) -> List[str]`
   - Core correction algorithm as described in "Correction Decision Logic" above
   - Insert after `check_consistency()` method (after line 568)

2. `_estimate_expected_range(self) -> Optional[Tuple[float, float]]`
   - Compute expected value range from rate history
   - Insert after `_calculate_average_rate_per_hour()` (after line 657)

3. `_get_ordered_position_ids(self) -> List[str]`
   - Return position IDs in significance order
   - Insert as helper near `_estimate_expected_range`

4. `_recalculate_with_replacement(self, predictions, replace_id, replace_class, raw_values) -> float`
   - Hypothetical total with one position replaced
   - Insert as helper near `_estimate_expected_range`

5. `_check_consistency_improvement(self, predictions, replace_id, replace_class) -> bool`
   - Check if replacement fixes an adjacent consistency violation
   - Insert as helper near `_estimate_expected_range`

**Lines affected**: New code inserted after line 568 (after `check_consistency`) and after
line 657 (after `_calculate_average_rate_per_hour`). No existing lines are modified in this WP.

### WP4: Integration in process_reading

**File**: `watermeter/watermeter_service.py`

**Method**: `process_reading()` (line 790)

**Integration point**: After line 835 (`total_value, raw_values = self.calculate_total(predictions)`)
and before line 838 (`consistency_warnings = self.check_consistency(predictions)`).

Insert:

```python
# 3b. Value correction (BL-04)
correction_warnings = self.correct_predictions(predictions, total_value, raw_values)
if correction_warnings:
    # Recalculate total after corrections
    total_value, raw_values = self.calculate_total(predictions)
    logger.info(f"Recalculated total after {len(correction_warnings)} correction(s): {total_value:.4f}")
```

Then at line ~843 (where `all_warnings` is assembled), prepend correction warnings:

```python
all_warnings = correction_warnings + consistency_warnings + plausibility_warnings
```

**Note on ordering**: Correction warnings go first because they are the most actionable
information for the user -- "we changed something" is more important than "positions are
inconsistent".

**Note on `top_k` in current_state**: The `predictions` list serialized to `current_state`
(lines 895-904) will automatically include the extra `corrected_from`, `corrected_confidence`,
and `correction_signals` keys for corrected positions. The `top_k` key is intentionally NOT
included in the serialized output (it's only used internally by the correction engine), and
`image_bytes` is already excluded. Update the serialization to exclude `top_k`:

```python
self.current_state['predictions'] = [
    {
        'id': pred['id'],
        'class': pred['class'],
        'confidence': pred['confidence'],
        'model': pred['model'],
        'image_base64': base64.b64encode(pred['image_bytes']).decode('utf-8'),
        # Include correction metadata if present
        **(
            {
                'corrected_from': pred['corrected_from'],
                'corrected_confidence': pred['corrected_confidence'],
                'correction_signals': pred['correction_signals'],
            }
            if 'corrected_from' in pred
            else {}
        ),
    }
    for pred in predictions.values()
]
```

### WP5: Config schema + defaults

**Files**:
- `watermeter/config_utils.py` -- add `correction` property to `CONFIG_SCHEMA["properties"]`
  (after the `plausibility` block, around line 370)
- `config.yaml` -- add `correction:` section (after `plausibility:` section, around line 131)

**Changes in config_utils.py**: Add the full schema block shown in "Config Changes" section
above as a new key `"correction"` in `CONFIG_SCHEMA["properties"]`.

**Changes in config.yaml**: Add the YAML block shown in "Config Changes" section above.
Key: `enabled: false` by default -- the feature is opt-in.

### WP6: Unit tests

**File**: `tests/unit/test_value_correction.py` (new file)

**Test approach**: Create a minimal `WatermeterService` instance via `object.__new__()` (same
pattern as `test_leak_detection.py`), set up synthetic predictions and config, and test each
component in isolation.

**Test cases**:

#### Classifier.predict_detailed tests

1. **`test_predict_detailed_returns_top_k`**: Mock a Classifier with known output logits,
   verify `predict_detailed()` returns K entries sorted by confidence descending.

2. **`test_predict_detailed_top1_matches_predict`**: Verify that `predict_detailed()[0]`
   matches the output of `predict()` for the same input.

3. **`test_predict_detailed_confidences_sum_to_1`**: Top-K confidences should sum to <= 1.0
   (they won't sum to exactly 1.0 because we only return K out of N classes).

#### Signal tests (on WatermeterService)

4. **`test_estimate_expected_range_no_history`**: With empty rate_history, returns None.

5. **`test_estimate_expected_range_sufficient_history`**: With 3+ entries, returns
   (min, max) tuple where min = previous_value and max > min.

6. **`test_signal_previous_value_backward`**: Raw total < previous_value. Alternative that
   produces total >= previous_value should get +1 signal score.

7. **`test_signal_previous_value_forward`**: Raw total >= previous_value. Signal does not
   contribute (no violation to fix).

8. **`test_signal_expected_rate_in_range`**: Alternative brings total into expected range
   when original was outside. Should get +1.

9. **`test_signal_adjacent_consistency`**: Arrow reads X.6 but next position reads 3 (< 5).
   Alternative arrow X.2 fixes consistency. Should get +1.

#### Correction engine integration tests

10. **`test_no_correction_when_disabled`**: `correction.enabled = False`. Returns empty list.

11. **`test_no_correction_all_confident`**: All positions above threshold. Returns empty list.

12. **`test_correction_applied_two_signals`**: One position has low confidence, two signals
    agree on alternative. Verify prediction dict is updated and warning string returned.

13. **`test_no_correction_single_signal`**: Only one signal agrees (below min_signal_agreement).
    No correction applied.

14. **`test_correction_never_to_nan`**: Alternative with class 'NAN' is never chosen even if
    signals agree.

15. **`test_max_corrections_limit`**: Multiple low-confidence positions but
    `max_corrections_per_reading` is 1. Only one gets corrected.

16. **`test_correction_warning_format`**: Verify warning string contains position ID,
    old class, new class, confidence values, and signal count.

#### Rollover edge case

17. **`test_meter_rollover_not_corrected`**: previous_value = 998, raw reading = 002.
    Signal 1 (previous value) should be skipped. No correction applied.

#### _recalculate_with_replacement tests

18. **`test_recalculate_digit_replacement`**: Replace digit_2 from '3' to '4'. Verify total
    changes by exactly 10.

19. **`test_recalculate_arrow_replacement`**: Replace analog_1 from '3.0' to '7.0'. Verify
    total changes by 0.4 (from floor(3)=3 to floor(7)=7, times 0.1).

#### _check_consistency_improvement tests

20. **`test_consistency_improvement_detected`**: Position has violation with neighbor; alt
    fixes it. Returns True.

21. **`test_no_consistency_improvement`**: Position has no violation. Returns False.

## Open Items

- **Meter rollover detection**: The guard checks if all digits are near-0 and previous is
  near-max. This heuristic may need tuning for meters with more than 3 digits. For now,
  "near-max" is defined as previous_value > 0.9 * (10^digit_count). Watch for false
  positives in production.

- **Performance**: `predict_detailed()` reuses the same forward pass as `predict()` -- no
  extra inference cost. The top-K sort is O(N log K) where N is class count (max 100 for
  arrows). Negligible.

- **Interaction with BL-05 (cross-arrow consistency)**: BL-05 depends on BL-04's framework.
  The correction engine's signal scoring is extensible -- BL-05 will add a 5th signal
  (cross-arrow agreement). No architectural changes needed in BL-04 to support this.

## Progress Log

- 2026-02-14: Task document created. Architecture designed with four signals, correction
  decision logic, config schema, and 21 unit test cases across 6 work packages.

## Done Criteria

- [ ] WP1: `predict_detailed()` added to `Classifier` and `InferenceService` in `inference.py`
- [ ] WP2: `run_inference()` calls `predict_detailed()` when correction is enabled, stores `top_k`
- [ ] WP3: `correct_predictions()` and all helper methods implemented in `watermeter_service.py`
- [ ] WP4: `process_reading()` calls correction engine between `calculate_total` and `check_consistency`
- [ ] WP5: Config schema in `config_utils.py` + defaults in `config.yaml`
- [ ] WP6: All unit tests pass (`python -m pytest tests/unit/test_value_correction.py -v`)
- [ ] Full test suite passes (`python -m pytest tests/unit/ tests/regression/ --tb=short -q`)
- [ ] `backlog.md` BL-04 status updated to `in-progress`
