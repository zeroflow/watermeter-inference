# BL-02: Ground-Truth Pruning

## Goal

Remove near-duplicate images from ground truth class folders to reduce dataset size
and improve training diversity. Expose the functionality as a two-step API
(preview + confirm) so the UI can show what would be removed before committing.

## Architecture

### API Endpoints

Both endpoints live in `watermeter/routes/models.py` (alongside the existing
`/api/training-data/stats` and `/api/training-data/dedup` endpoints).

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/api/training-data/prune/preview` | `{"type": "digits"}` or `{"type": "arrows"}` | Per-class counts, total removable, median threshold |
| `POST` | `/api/training-data/prune/confirm` | `{"type": "digits"}` or `{"type": "arrows"}` | Counts of deleted files per class |

### Pruning Algorithm

1. For each class folder under `ground_truth/{class}/`:
   a. Compute dHash for every `.jpg` image (using `HashCache.scan_and_update()`)
   b. Cluster images by hash similarity using single-linkage clustering:
      - Two images are "similar" if hamming distance <= threshold (default 10)
      - Walk through images, merge into existing cluster if any member is within threshold
   c. From each cluster with 2+ images, keep the most distinct image
      (highest average hamming distance to all other images in the cluster)
   d. Mark remaining cluster members as prune candidates

2. Median protection: compute median class size across all classes.
   For any class whose post-prune size would drop below the median,
   reduce the number of deletions to stay at or above the median.
   This protects thin classes from being hollowed out.

3. Preview returns the candidate list (stored in-memory keyed by type);
   confirm reads the stored list and deletes the files.

### Core Functions (in `watermeter/image_hash.py`)

- `cluster_images_by_hash(hashes: Dict[str, int], threshold: int) -> List[List[str]]`
  Cluster filenames by hash similarity.

- `select_prune_candidates(clusters: List[List[str]], hashes: Dict[str, int]) -> List[str]`
  From each multi-image cluster, keep most distinct, return the rest.

- `compute_prune_preview(gt_base: Path, threshold: int) -> Dict`
  Full scan: hash all classes, cluster, apply median protection, return preview.

- `confirm_prune(gt_base: Path, preview: Dict) -> Dict`
  Delete the files listed in the preview, rebuild hash caches.

## Work Packages

### WP-1: Pruning Logic (`image_hash.py`)
- `cluster_images_by_hash()` -- single-linkage clustering by hamming distance
- `select_prune_candidates()` -- pick least-distinct from each cluster
- `compute_prune_preview()` -- orchestrate scan, apply median protection
- `confirm_prune()` -- delete files, update hash caches

### WP-2: API Endpoints (`routes/models.py`)
- `POST /api/training-data/prune/preview`
- `POST /api/training-data/prune/confirm`
- In-memory storage of last preview result (keyed by type)
- Input validation (type must be "digits" or "arrows")

### WP-3: Unit Tests (`tests/unit/test_prune.py`)
- Test clustering logic with known hash distances
- Test median protection (thin class not pruned below median)
- Test preview generation with mock filesystem
- Test confirm deletes correct files
- Test API endpoints via TestClient

## Done Criteria

- [x] `POST /api/training-data/prune/preview` returns correct per-class counts
- [x] Median protection prevents thin classes from dropping below median
- [x] `POST /api/training-data/prune/confirm` deletes exactly the previewed files
- [x] Hash caches are rebuilt after pruning
- [x] All unit tests pass (`python -m pytest tests/unit/ tests/regression/ --tb=short -q`)
- [x] No regressions in existing tests

## Progress Log

- 2026-02-15: Task created, architecture designed
- 2026-02-15: WP-1 complete -- 4 functions added to image_hash.py (cluster, select, preview, confirm)
- 2026-02-15: WP-2 complete -- 2 API endpoints added to routes/models.py
- 2026-02-15: WP-3 complete -- 29 unit tests in test_prune.py, all passing
- 2026-02-15: Full suite 241/241 passed, 0 regressions
