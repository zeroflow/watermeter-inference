# Backlog

Format: `BL-{id}` | status: `idea` → `planned` → `in-progress` → `done`

## Data Quality

- **BL-01** `planned` — **Label-candidate pruning**: deduplicate low-confidence images inline at save time
  - **Trigger**: in `save_low_confidence()`, before writing to disk, compare against existing images in the same ROI's input folder
  - **Scope**: per ROI position only (digit_1 vs digit_1, analog_2 vs analog_2)
  - **Method**: perceptual hash (e.g. average hash or dHash) — fast, works well for small grayscale crops
  - **Disposal**: silently skip saving if a near-duplicate already exists (no skipped folder)
  - **Motivation**: input folders grow to 200+ images, most near-identical — wastes significant labeling time
- **BL-02** `idea` — **Ground-truth pruning**: also remove near-duplicate images from ground truth sets
- **BL-03** `idea` — **Ground-truth rework**: move problematic/mislabeled images back into labeling pipeline

## Algorithm / Inference

- **BL-04** `idea` — **Value deduction from rules**: deduce correct meter value using plausibility rules (e.g. monotonic increase, rate limits)
- **BL-05** `idea` — **Better arrow classes**: combine 1.0 + 0.5 + 0.1 arrow readings for more precise values; cross-check between arrows (e.g. arrow1=1.0/1.5/1.6 + arrow2=6.2 → consistent). Cave: parallax error skews images
- **BL-06** `idea` — **Warnings on constant use**: detect and warn when meter reading hasn't changed over time

## User Interaction

- **BL-07** `idea` — **Ask user via Telegram**: send uncertain readings for human verification (e.g. "this value seems high — is it correct?")
