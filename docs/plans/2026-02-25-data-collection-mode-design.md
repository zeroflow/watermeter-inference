# Data Collection Mode — Design

**Date:** 2026-02-25
**Status:** Approved

## Goal

Build a "collect lots of data" mode that fills the training dataset quickly with diverse images. Instead of only saving low-confidence predictions, save images across all confidence levels — but avoid collecting 500 identical images of the same reading. Use per-class-per-ROI quotas to ensure balanced, diverse coverage.

## Approach

**Separate `DataCollector` class** (Approach B). Collection mode has different semantics from low-confidence saving — it's about training diversity, not uncertainty. A separate module keeps both paths clean and testable.

## Config Schema

```yaml
inference:
  data_collection:
    enabled: false                          # Master toggle
    quota_per_class: 10                     # Max images per class per ROI
    dedup_enabled: true                     # Visual hash dedup during collection
    dedup_threshold: 10                     # Hamming distance (0-64)
    counters_file: ".collection_counts.json" # Persisted quota counters
```

- No rate limit in collection mode (dedup + quota handle throttling)
- Independent of `confidence_threshold` and `save_rate_limit`
- Config-only activation (no UI toggle)
- Applies to both digits and arrows

## `DataCollector` Class

**File:** `watermeter/data_collector.py`

### State

- In-memory counters: `{model_type: {roi_id: {class_name: count}}}`
- Persisted to `{save_path}/.collection_counts.json`
- Loaded on init, flushed on every save

### Methods

- `__init__(config, save_path)` — load config + counters from disk
- `should_collect(model_type, roi_id, predicted_class) -> bool` — check quota
- `collect(model_type, roi_id, predicted_class, image) -> bool` — full save pipeline
- `get_counts() -> dict` — return current counters (for diagnostics)
- `reset()` — clear all counters

### `collect()` Flow

1. `should_collect()` → quota full? Return False
2. Compute dHash of image
3. If dedup enabled → check against input folder hash cache → duplicate? Return False
4. Save image as `{roi_id}_{timestamp}_label={predicted_class}.jpg`
5. Update hash cache
6. Increment counter + persist to disk
7. Return True

### What It Does NOT Do

- No "next dial" reference images
- No rate limiting
- No confidence filtering

## Integration

In `watermeter_service.py` `process_reading()`:

```
per ROI:
  1. Run inference → prediction (class, confidence)
  2. IF data_collection.enabled:
       → data_collector.collect(model_type, roi_id, predicted_class, image)
  3. IF confidence < confidence_threshold:
       → save_low_confidence() (unchanged)
```

Both paths run independently. Collection mode adds to the pipeline, does not replace the existing low-confidence path.

## Counter Persistence

**File:** `{save_path}/.collection_counts.json`

```json
{
  "arrows": {
    "analog_1": {"0.0": 3, "1.3": 10},
    "analog_2": {"0.0": 5}
  },
  "digits": {
    "digit_1": {"0": 8, "1": 10}
  }
}
```

- Atomic writes (temp file + rename)
- Independent of filesystem — labeling can move images out of `input/` without affecting counters
- Reset: delete file and restart

## Dedup in Collection Mode

Reuses existing `image_hash.py` infrastructure (`compute_dhash()`, `HashCache`). Collection mode has its own configurable on/off and distance threshold, separate from the low-confidence dedup settings.

## Testing

- Unit: quota enforcement, counter persistence/load, dedup integration, atomic write
- Integration: mock inference loop, verify filenames, verify counter state
- Edge cases: restart survival, concurrent labeling, quota boundary

## Config Validation

Add validation in `config_utils.py`:
- `quota_per_class`: int, >= 1
- `dedup_threshold`: int, 0-64
- `dedup_enabled`: bool
- `counters_file`: string
- `enabled`: bool
