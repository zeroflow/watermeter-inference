# Reference Folder for Photo-based Generation — Design

**Date:** 2026-02-17
**Status:** Approved
**Goal:** Replace the annotations.json approach with an ImageFolder-style `reference/` directory for photo-based synthetic data generation.

## Structure

```
arrows/reference/{class}/photo.jpg    e.g. arrows/reference/9.0/analog_1.jpg
digits/reference/{class}/photo.jpg    e.g. digits/reference/3/digit_2.jpg
```

Same convention as `ground_truth/` — the folder name IS the class label.

## Behavior

- `SyntheticGenerator` checks for `reference/` directory
- For each image found, builds an `ArrowCompositor`
- Multiple photos → multiple compositors, randomly selected per generated image
- Fallback: no `reference/` → programmatic renderer (ArrowRenderer)

## Removed

- `annotations.json` approach
- `GET/POST /api/synthetic/annotate` endpoints
- Annotation UI in training.html

## No UI needed

Users place reference images manually via CLI/file manager.
