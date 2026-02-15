# BL-03: Ground-truth rework -- benchmark-driven mislabel detection

## Goal
Detect mislabeled images in ground truth by running the active model's inference on all GT images and flagging where the model's prediction disagrees with the folder label. Provide an API to scan for suspects and confirm which ones to send back to the input folder for relabeling.

## Plan

### WP1: Scan endpoint [DONE]
- `POST /api/training-data/mislabel/scan` accepts `{"type": "digits"|"arrows"}`
- Loads the active model via `InferenceService`
- Iterates all ground truth class folders, runs `predict()` on each image
- Collects suspects (prediction != folder label)
- For arrows regression: uses tolerance of 0.5 dial positions
- Returns JSON with suspects list (path, current_label, predicted_label, confidence, base64 thumbnail)
- Stores suspects in memory for confirm step

### WP2: Confirm endpoint [DONE]
- `POST /api/training-data/mislabel/confirm` accepts `{"type": ..., "selected": [paths]}`
- Validates selected paths against last scan result
- Moves selected images from `ground_truth/{class}/` to `input/`
- Renames with label hint: `{original_stem}_label={class}.jpg`
- Handles filename collisions with counter suffix
- Returns count of moved files

### WP3: Unit tests [DONE]
- 28 tests covering:
  - `scan_mislabeled()`: empty GT, all correct, detect mislabeled digits, arrows regression tolerance, arrows classification, no active model, prediction errors, base64 thumbnails
  - `confirm_mislabeled()`: move to input, multiple files, nonexistent files, empty selection, overwrite avoidance, arrow labels with decimals, auto-create input dir
  - `_make_thumbnail_base64()`: valid file, missing file
  - API endpoints: invalid type, missing fields, confirm without scan, empty selection, path validation, full workflow, scan clears on confirm, no active model

## Progress log
- 2026-02-15: Implemented all 3 WPs. 269/269 tests pass (28 new + 241 existing).

## Open items
- None

## Done criteria
- [x] Both endpoints functional
- [x] Unit tests pass (28 tests)
- [x] Existing test suite unbroken (269 total, 0 failures)
