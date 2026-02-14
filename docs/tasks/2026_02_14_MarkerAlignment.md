# Marker-Based Image Alignment

## Goal
Replace the `_align_with_markers()` stub in `watermeter_service.py` with a working implementation that uses template matching to detect marker positions and an affine transform to correct camera drift.

## Plan
1. Add template caching (`_marker_templates`, `_load_marker_templates()`, `invalidate_marker_cache()`)
2. Replace `_align_with_markers()` with template matching + `estimateAffinePartial2D`
3. Wire cache invalidation in `routes/roi.py` (save_markers, delete_markers)
4. Write unit tests with real cv2 + synthetic images

## Progress
- [ ] Step 1: Template caching in WatermeterService
- [ ] Step 2: Replace _align_with_markers
- [ ] Step 3: Cache invalidation in roi.py
- [ ] Step 4: Unit tests
- [ ] Step 5: Run tests, commit

## Done Criteria
- `python -m pytest tests/unit/ tests/regression/ --tb=short -q` passes
- Alignment corrects small shifts, returns original on any failure
