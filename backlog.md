# Backlog

Format: `BL-{id}` | status: `idea` → `planned` → `in-progress` → `done`

## Data Quality

- **BL-01** `planned` — **Label-candidate pruning**: deduplicate low-confidence images inline at save time
  - **Trigger**: in `save_low_confidence()`, before writing to disk, compare against existing images in the same ROI's input folder
  - **Scope**: per ROI position only (digit_1 vs digit_1, analog_2 vs analog_2)
  - **Method**: perceptual hash (e.g. average hash or dHash) — fast, works well for small grayscale crops
  - **Disposal**: silently skip saving if a near-duplicate already exists (no skipped folder)
  - **Motivation**: input folders grow to 200+ images, most near-identical — wastes significant labeling time
- **BL-02** `planned` — **Ground-truth pruning**: remove near-duplicate images from ground truth to reduce dataset size and improve diversity
  - **Trigger**: UI button on the training page — shows preview of what would be removed, then confirm
  - **Scope**: per class folder (e.g. `digits/ground_truth/3/`, `arrows/ground_truth/4.5/`)
  - **Method**: perceptual hash (same as BL-01), cluster near-duplicates, keep the most distinct image from each cluster
  - **Min threshold**: never prune a class below the median class size — protects thin classes relative to the dataset distribution
  - **Disposal**: delete silently on confirm
  - **Current scale**: digits ~453 images (11 classes, 19–109 each), arrows ~4200 images (100 classes, 7–125 each)
- **BL-03** `planned` — **Ground-truth rework**: benchmark-driven detection of mislabeled ground truth, bulk relabel
  - **Trigger**: UI button on training page (similar placement to BL-02 prune button) — "Find mislabeled"
  - **How it works**:
    1. Runs benchmark on current ground truth using the active model
    2. Collects per-image results: images where model prediction != folder label are suspects
    3. Shows gallery of suspect images with current label vs model prediction
    4. User confirms which to send back (select all / deselect individual)
    5. Selected images move from `ground_truth/{class}/` back to `input/`
  - **Pre-filled label**: encode original label in filename (e.g. `digit_1_20260214_label=3.jpg`) so the labeling UI can pre-fill it as suggestion
  - **Prerequisite**: requires a trained model to run benchmark — button disabled if no active model
  - **Depends on**: benchmark already computes predictions per image (training_manager.py:976-983), just needs to record image paths alongside results

## Algorithm / Inference

- **BL-04** `idea` — **Value deduction from rules**: deduce correct meter value using plausibility rules (e.g. monotonic increase, rate limits)
- **BL-05** `idea` — **Better arrow classes**: combine 1.0 + 0.5 + 0.1 arrow readings for more precise values; cross-check between arrows (e.g. arrow1=1.0/1.5/1.6 + arrow2=6.2 → consistent). Cave: parallax error skews images
- **BL-06** `idea` — **Warnings on constant use**: detect and warn when meter reading hasn't changed over time

## User Interaction

- **BL-07** `idea` — **Ask user via Telegram**: send uncertain readings for human verification (e.g. "this value seems high — is it correct?")
