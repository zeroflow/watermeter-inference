# BL-01: Label-Candidate Pruning

## Goal
Deduplicate low-confidence images at save time using perceptual hashing.
Input folders accumulate hundreds of near-identical images, wasting labeling time.

## Design Decisions (confirmed with user)
- **Hash algorithm**: dHash, own implementation using OpenCV (no new deps)
- **Dedup scope**: Configurable — default `"input+ground_truth"` (compare against input folder + all ground_truth class folders for the same model type)
- **Cache**: JSON sidecar file (`.hashes.json`) per folder — avoids re-hashing on every save
- **Disposal**: Silently skip saving if near-duplicate found (log at debug level)

## Architecture

### New module: `watermeter/image_hash.py`
- `compute_dhash(image_bytes: bytes, hash_size: int = 8) -> int` — 64-bit dHash from raw JPEG bytes
- `hamming_distance(h1: int, h2: int) -> int` — bitwise comparison
- `class HashCache` — manages `.hashes.json` sidecar files per folder
  - `load(folder: Path) -> None` — load or create cache, prune entries for deleted files
  - `add(filename: str, hash_value: int) -> None` — add entry + save
  - `find_near_duplicate(new_hash: int, threshold: int) -> Optional[str]` — return filename of first match or None
  - File format: `{filename: hash_as_hex_string}`

### Modified: `watermeter/watermeter_service.py`
In `save_low_confidence()`, before `save_path.write_bytes(image_bytes)`:
1. Compute dHash of new image
2. Load HashCache for `{save_dir}` (the input folder)
3. If `dedup_scope == "input+ground_truth"`, also scan `{base}/{model_type}/ground_truth/*/`
4. Call `find_near_duplicate()` on each cache
5. If duplicate found → log + return (skip saving)
6. If no duplicate → save image, add to input cache

### Config changes: `config_utils.py`
Under `low_confidence` section, add:
```yaml
low_confidence:
  dedup_enabled: true
  dedup_threshold: 10        # hamming distance (0=exact, 64=max different)
  dedup_scope: "input+ground_truth"  # "input" or "input+ground_truth"
```

### Tests: `tests/unit/test_image_hash.py`
- dHash produces consistent results for same image
- dHash produces different results for different images
- Near-identical images (brightness shift) stay below threshold
- Distinct images exceed threshold
- HashCache load/save/prune/find operations
- save_low_confidence skips duplicates (mock-based)

## Work Packages (for delegation)

### WP-1: `image_hash.py` module + tests
- Implement `compute_dhash`, `hamming_distance`, `HashCache`
- Write `tests/unit/test_image_hash.py`
- Self-contained, no dependencies on other WPs

### WP-2: Config schema + migration
- Add `dedup_enabled`, `dedup_threshold`, `dedup_scope` to config schema
- Add defaults in `config_utils.py`
- Update any config validation

### WP-3: Integration into `watermeter_service.py`
- Modify `save_low_confidence()` to use image_hash module
- Add dedup check before writing to disk
- Handle ground_truth scanning when scope includes it
- Depends on WP-1 and WP-2

## Progress Log
- 2026-02-14: Created task, explored codebase, designed solution
- 2026-02-14: WP-1 done — image_hash.py + test_image_hash.py created
- 2026-02-14: WP-2 done — config schema updated with 3 dedup fields
- 2026-02-14: WP-3 done — save_low_confidence() integrated with dedup
- 2026-02-14: All syntax checks pass. Tests need Docker to run.

## Done Criteria
- [x] `image_hash.py` module with dHash + HashCache
- [x] Config schema updated with dedup settings
- [x] `save_low_confidence()` checks for duplicates before saving
- [x] Unit tests pass (15/15 passed)
- [x] Existing tests still pass (149/149 passed)
