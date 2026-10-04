# Calibrated Arrows Mode — Design + Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `inference.arrows_mode: calibrated` that reads the arrow dials ~6× more precisely than `opencv`. It measures the needle tip around the needle's real image pivot and interpolates between the detected scale ticks. The calibration is computed once from the raw-image archive.

**Architecture:** `arrow_calibration.py` holds the pure geometry: tick detection, pivot estimation, the parallax model, offset self-calibration, the measurement, and JSON (de)serialisation. `calibrated_arrows.py` is the runtime detector, with the same predict interface as `OpenCVArrowDetector`. It needs the ROI id, which `InferenceService` now passes through. Calibration runs via `POST /api/arrows/calibrate` or `scripts/calibrate_arrows.py`, writes `/data/arrow_calibration.json`, and hot-reloads.

**Tech Stack:** NumPy, OpenCV, FastAPI. No new dependencies.

**Spec:** this document (Background + Design sections).

## Background: investigation 2026-10-04

The investigation used 875 archived frames × 4 dials. It used no labels. The metric was cross-arrow consistency: `wrap(10·frac(a_i) − a_{i+1}) / 10`, in slow-arrow units. Offsets were fitted on day 1 and the reported numbers are on day 2.

| Method | MAE | P95 | > 0.1 |
|---|---|---|---|
| `opencv` (prod: crop centre, 100-bin histogram peak) | 0.146 | 0.33 | ~50 % |
| best ML regressor (resnet18) | 0.133 | 0.34 | ~45 % |
| tip pixels around the true pivot, tick interpolation | 0.054 | 0.14 | 13 % |
| + outermost 5 % only + self-calibrated offsets | **0.022** | **0.07** | **0.4 %** |

The root cause is parallax. The needle sits above the dial and the camera looks at it obliquely. As a result the needle pivot in the image is 15–20 px away from both the crop centre and the tick-ring centre. The three measurable shift vectors point to one common point, the camera's nadir. The shift is radial: `shift = s · (V − pos)`.

## Design

### Calibration (per analog ROI, in crop pixel coordinates)

1. **Crops:** up to `max_frames` archive frames, spread evenly over the whole archive (more needle rotation means a better-conditioned pivot fit), run through `ImagePipeline.process_whole_image`. These are the same crops that production sees.
2. **Background:** the per-pixel median over frames, ignoring needle pixels. The colour mask is dilated by 7 px first.
3. **Ticks:**
   - Find dark, elongated blobs (adaptive threshold) whose long axis points radially at a common centre.
   - Reject outliers against a fitted ellipse.
   - At least 5 ticks are required; otherwise the dial fails calibration.
   - The ellipse gives the rectification matrix `M` (circle-ise around the ellipse centre).
   - Tick angles are measured in rectified space around the ellipse centre. Index 0 is the tick nearest 12 o'clock; the others get `round(Δangle / 36)`.
4. **Pivot:**
   - Fit the needle's principal axis per frame. The pivot is the least-squares intersection of all axes.
   - If `cond(A) > 20` (the needle did not rotate enough, e.g. the 0.1 m³ dial), the axes cannot fix the pivot.
5. **Parallax model:**
   - Fit it from the dials with a good pivot. Shift = pivot − tick centre; position = ROI centre in full-image px.
   - With ≥2 good dials, fit `V` and `s` by least squares. With 1, fix `V` = image centre and fit `s` only.
   - It predicts the pivot of the ill-conditioned dials (`pivot_source: parallax`).
   - Without any good dial, fall back to the tick centre (`pivot_source: tick_centre`, logged as degraded).
6. **Offsets:**
   - Measure all frames with the fresh calibration (offset 0).
   - The fastest dial keeps offset 0. Going slow-ward, each dial gets the median cross-arrow residual.
   - Only offsets with |o| ≤ 0.2 are applied; larger ones are reported and set to 0.
   - Offsets are only fitted with ≥ 30 frames where all dials were measured.
7. **Report:** per-dial ticks, pivot source, offset, and frames used. It also gives the in-sample cross-arrow MAE before and after offsets, for each of `opencv` and `calibrated`.

### Measurement (per reading)

1. Take the HSV colour mask (the `opencv_arrows` thresholds), apply a 3×3 open, and keep the largest component.
2. Compute `q = M · (p − pivot)` and the radius `r = |q|`. Keep the tip pixels: `r ≥ percentile(r, tip_percentile)`, where `tip_percentile` defaults to 95.
3. Take the r-weighted circular mean angle. The confidence is the resultant length R.
4. Map the angle to a value by piecewise-linear interpolation between the tick angles (unwrapped from tick 0), then add the offset, mod 10.
5. Return `{"class": "3.47", "confidence": R, "value": 3.4712}`. The class has 2 decimals. Downstream code uses `float(class)`, and `calculate_total` takes it as-is because there is no `bin_width`.

### Fallbacks (never fail harder than `opencv`)

- The calibration file is missing or unreadable, the ROI id is unknown, or no `image_id` was passed (e.g. the ROI setup preview). In all these cases, delegate to `OpenCVArrowDetector` with the same HSV settings. Warn once per id.
- The crop size differs from the calibrated size by more than 2 px, or the dial's ROI in the config differs from the calibrated ROI. The calibration is stale: fall back and warn.
- No colour pixels: `{"class": "NaN", "confidence": 0.0}`, the same as `opencv`.

### Config

```yaml
inference:
  arrows_mode: "calibrated"        # "model" | "opencv" | "calibrated"
  opencv_arrows: {...}             # HSV thresholds, shared by opencv + calibrated
  calibrated_arrows:
    calibration_file: "/data/arrow_calibration.json"
    tip_percentile: 95             # outermost 5 % of needle pixels = tip
```

### Data collection

The training labels are floored 0.1 classes. `DataCollector.collect` floors arrow labels to 0.1 (e.g. `3.47` becomes `3.4`), so the 100-class layout and the quotas stay intact.

## Global Constraints

- Python 3.10 compatible, Black at 120 chars, and ruff clean (`uvx ruff check watermeter/ tests/`).
- Every new config field goes into the `config_utils.py` schema.
- Production (:8001) is never touched; manual verification happens on the debug container (:8002).
- No real IPs, hostnames or home paths in committed files.

## Review Focus

1. **The ROIs are edited after calibration.** The old geometry must not be applied silently; expect a fallback to `opencv` with a warning. *Test: `test_stale_roi_falls_back` (Task 2).*
2. **The slow dial barely moves during the archive window.** The pivot must come from the parallax model, not from noise. *Test: `test_ill_conditioned_dial_uses_parallax` (Task 1).*
3. **The archive is empty, or alignment fails on all frames.** The endpoint should return a clear 400/422 message and leave the old calibration file untouched. *Test: `test_calibrate_endpoint_no_frames` (Task 4).*
4. **The needle is near 0/9.9.** The value must wrap correctly, with no 9.99→0.0 jump error in the interpolation. *Test: `test_wraparound_near_zero` (Task 2).*
5. **The ROI preview has no image_id.** Expect the opencv fallback result rather than an exception. *Test: `test_no_image_id_falls_back` (Task 2).*

---

### Task 1: Calibration geometry (`arrow_calibration.py`)

**Files:**
- Create: `watermeter/arrow_calibration.py`
- Test: `tests/unit/test_arrow_calibration.py` (with its own synthetic dial renderer and the real-cv2 fixture, like `test_opencv_arrows.py`)

**Interfaces (produces):**
- `class CalibrationError(Exception)`
- `@dataclass DialCalibration`. Fields:
  - `roi: dict`, `crop_size: tuple[int, int]` (w, h)
  - `centre: tuple[float, float]`, `ellipse_axes: tuple[float, float]`, `ellipse_angle: float`
  - `tick_angles: list[float]`, `tick_indices: list[int]`
  - `pivot: tuple[float, float]`, `pivot_source: str`, `offset: float = 0.0`
  - `n_ticks: int`, `n_frames: int`
- Methods: `rect_matrix() -> np.ndarray`, `angle_to_value(angle_deg) -> float`, `to_dict()`, `from_dict(d)`
- `needle_mask(img_bgr, hue_ranges, saturation_min, value_min) -> np.ndarray` (bool, largest component)
- `measure(mask, dial, tip_percentile=95.0) -> tuple[float | None, float]` → (value incl. offset, R)
- `background(crops, hue_ranges, saturation_min, value_min) -> np.ndarray`
- `detect_ticks(bg) -> tuple[np.ndarray, tuple]` (ticks Nx2, cv2 ellipse); raises `CalibrationError` with fewer than 5 ticks
- `needle_axes_pivot(masks) -> tuple[np.ndarray, float]` → (pivot, condition number)
- `fit_parallax(samples: list[tuple[np.ndarray, np.ndarray]], image_centre) -> Callable[[np.ndarray], np.ndarray]` (position → shift)
- `fit_offsets(values: np.ndarray, max_abs=0.2) -> tuple[list[float], list[float]]` → (applied, raw); columns slow → fast
- `cross_arrow_mae(values) -> float`
- `calibrate_dials(crops_by_id, rois, arrow_ids, hue_ranges, saturation_min, value_min, tip_percentile) -> tuple[dict[str, DialCalibration], dict]` (calibrations, report)
- `save_calibration(path, dials, report)`, `load_calibration(path) -> dict[str, DialCalibration]`

**Steps:**
- [ ] Write the failing tests with the synthetic renderer. The renderer draws 10 dark radial ticks on a slightly elliptical ring around `centre`, plus a red lancet needle rotating around `pivot = centre + shift`. Tests:
  - `test_detect_ticks_finds_ring`: 10 ticks, centre within 1 px.
  - `test_needle_axes_pivot_recovers_offset_pivot`: 12 needle angles, pivot within 1.5 px, cond < 20.
  - `test_parallax_fit_predicts_fourth_dial`: shifts generated from `s·(V − pos)`.
  - `test_measure_accuracy_with_parallax`: values 0.35/3.47/7.92, |err| < 0.03 using the true calibration.
  - `test_fit_offsets_recovers_injected_offsets`: also checks the clamp at 0.2.
  - `test_calibrate_dials_end_to_end`: 4 synthetic dials, dial 1 barely rotating.
  - `test_ill_conditioned_dial_uses_parallax`: dial 1 gets `pivot_source == "parallax"` and pivot within 3 px.
  - `test_save_load_roundtrip`.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_arrow_calibration.py -q` and expect an ImportError.
- [ ] Implement the module (algorithms as in Design; code ported from the investigation scripts).
- [ ] Run the tests until they pass, then run ruff and black.
- [ ] Commit `claude: arrow calibration geometry (ticks, pivot, parallax, offsets)`.

### Task 2: Runtime detector + inference wiring + config

**Files:**
- Create: `watermeter/calibrated_arrows.py`
- Modify: `watermeter/inference.py` (`_create_arrows_backend`, `predict_from_bytes`/`predict_detailed_from_bytes` get `image_id: str | None = None`)
- Modify: `watermeter/watermeter_service.py` (`run_inference` passes `image_id`), `watermeter/oneshot.py`
- Modify: `watermeter/config_utils.py` (enum + `calibrated_arrows` schema), `config.yaml` (commented example)
- Modify: `watermeter/data_collector.py` (floor arrow labels to 0.1)
- Test: `tests/unit/test_calibrated_arrows.py`, `tests/unit/test_data_collector.py`, `tests/unit/test_config_utils.py`

**Interfaces:**
- Consumes: Task 1's `DialCalibration`, `needle_mask`, `measure`, `load_calibration`
- Produces:
  - `CalibratedArrowDetector(calibrations: dict[str, DialCalibration], rois: dict[str, dict] | None, hue_ranges, saturation_min, value_min, tip_percentile=95.0)`
  - Methods: `predict(image_path, image_id=None)`, `predict_from_bytes(image_bytes, image_id=None)`, `predict_detailed(...)`, `predict_detailed_from_bytes(image_bytes, top_k=3, image_id=None)`
  - `CalibratedArrowDetector.from_config(config) -> CalibratedArrowDetector`

**Steps:**
- [ ] Write the failing tests:
  - `test_calibrated_reading_precise`: synthetic dial, class has 2 decimals, |err| < 0.03.
  - `test_no_image_id_falls_back`: result equals the `OpenCVArrowDetector` result.
  - `test_unknown_id_falls_back`
  - `test_stale_roi_falls_back`: config ROI differs from the calibrated ROI.
  - `test_crop_size_mismatch_falls_back`
  - `test_wraparound_near_zero`: values 9.97 and 0.02.
  - `test_missing_calibration_file_falls_back`: `from_config` with a nonexistent path.
  - `test_inference_service_passes_image_id`
  - `test_collect_floors_arrow_label` (data collector)
  - `test_schema_accepts_calibrated_mode` (config)
- [ ] Run the tests and expect them to fail.
- [ ] Implement the detector, the wiring, the schema, the config example and the label flooring.
- [ ] Run `.venv/bin/python -m pytest tests/unit tests/regression -q` and ruff.
- [ ] Commit `claude: arrows_mode calibrated — runtime detector + wiring`.

### Task 3: Calibration from the raw archive + CLI script

**Files:**
- Modify: `watermeter/arrow_calibration.py`. Add `calibrate_from_archive(config, archive_dir, max_frames=300, reference_path=None) -> tuple[dict, dict]`. It picks frames spread evenly over the archive, then crops them via `ImagePipeline` and calls `calibrate_dials`. It raises `CalibrationError("no aligned frames")` when there are none.
- Create: `scripts/calibrate_arrows.py` (`--config --archive-dir --out [--reference] [--max-frames]`; prints the report)
- Test: `tests/unit/test_arrow_calibration.py::test_calibrate_from_archive_spreads_frames`, using a stub pipeline.

**Steps:**
- [ ] Write the failing test, implement, and get it to pass.
- [ ] Run the script on the host against `data_debug/raw_archive` with `config_debug/config.yaml`, and compare the report with the investigation numbers.
- [ ] Commit `claude: arrow calibration from raw archive + CLI`.

### Task 4: API endpoints

**Files:**
- Create: `watermeter/routes/calibration.py`
  - `POST /api/arrows/calibrate {max_frames?: int}`: runs `asyncio.to_thread(calibrate_from_archive)`, saves the file, and calls `reload_models` when the mode is `calibrated`. It returns the report.
  - `GET /api/arrows/calibration`: returns the stored calibration, or 404.
- Modify: `watermeter/app.py` (include the router), `docs/` API mention, `backlog.md` (correct BL-77 and add a BL entry for this work)
- Test: `tests/unit/test_api_routes.py`-style tests: `test_calibrate_endpoint_no_frames` (422, file untouched), `test_calibrate_endpoint_success` (patched `calibrate_from_archive`), `test_get_calibration_404`.

**Steps:**
- [ ] Write the failing tests, implement, and get them to pass.
- [ ] Run the full unit + regression suite and ruff.
- [ ] Commit `claude: calibration API endpoints`.

### Task 5: Verify on debug container

- [ ] Run `./debug.sh --detach`. Call `POST localhost:8002/api/arrows/calibrate`, set `arrows_mode: calibrated` in `config_debug`, trigger readings, and check the logs and readings for plausibility.
- [ ] Push to `origin` and `gitea`.
