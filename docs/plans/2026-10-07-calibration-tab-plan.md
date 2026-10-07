# Calibration Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Calib" tab (visible only in `arrows_mode: calibrated`) to collect calibration frames with a time or frame target, auto-calibrate at the target, inspect a per-dial overlay preview, and override the needle pivot by click.

**Architecture:**
- `CalibrationSession` (new module) owns the collection state (`/data/calibration_collection.json`), the frame directory (`/data/calibration_frames/`) and the background calibration job.
- `WatermeterService` holds it and feeds it every whole image after alignment.
- `routes/calibration.py` exposes status, collect, run, preview and pivot endpoints. `routes/config.py` gets the arrows-mode switch.
- The UI is a Jinja page with vanilla JS polling. The nav item is gated by a Jinja global.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, OpenCV/NumPy, pytest.

**Spec:** `docs/plans/2026-10-07-calibration-tab-design.md`

## Global Constraints

- Python 3.10 compatible. Black at 120 chars for new or touched-new files only (the repo is not globally black-formatted). `uvx ruff check watermeter/ tests/` must stay clean.
- Never touch production (:8001). Manual checks run on the debug container (:8002).
- Theming goes through the CSS variables in `style.css`. Avoid inline styles in new markup.
- Build filesystem paths from user input with validation: `roi_id` must match `^analog_\d+$`.
- No real IPs, hostnames or home paths in committed files.
- Commit messages start with `claude: ` and end with the Co-Authored-By line. Push to `origin` and `gitea`.

## Review Focus

1. **Restart mid-job:** a job left `running` in the state file must become `failed` on load, never stay stuck. *Test: `test_running_job_marked_failed_on_load` (Task 2).*
2. **Unaligned frames:** they are stored but must not count toward the target, or a misaligned camera would "finish" collecting with useless frames. *Test: `test_unaligned_frames_not_counted` (Task 2).*
3. **Pivot override after a ROI edit:** it must be ignored, not applied to the wrong crop. *Test: `test_stale_override_ignored` (Task 1).*
4. **Preview before any reading or calibration:** expect 404, or the plain crop without an overlay; never a 500. *Tests: `test_preview_404_without_reading`, `test_preview_plain_without_calibration` (Task 3).*
5. **Nav rendering with a mocked or absent service** (unit test stubs): the template must not crash. *Test: `test_nav_hidden_when_not_calibrated` (Task 4).*

---

### Task 1: `arrow_calibration` — overrides, progress, overlay

**Files:**
- Modify: `watermeter/arrow_calibration.py`
- Test: `tests/unit/test_arrow_calibration.py`

**Interfaces (produces):**
- `calibrate_dials(..., pivot_overrides: dict | None = None, progress: Callable[[str, int, int], None] | None = None)`. An override has the form `{roi_id: {"pivot": [x, y], "roi": {...}}}`. A matching override gives `pivot_source == "manual"` and counts as a parallax sample. A stale one gives `report["dials"][id]["override_ignored"] = True`.
- `calibrate_from_archive(config, archive_dir, max_frames=300, reference_path=None, pivot_overrides=None, progress=None)`: stage `"align"` once per frame, stage `"measure"` once per dial.
- `save_calibration(path, dials, report, pivot_overrides=None)` stores `"pivot_overrides"`. `load_pivot_overrides(path) -> dict` returns `{}` for a missing file or a missing key.
- `render_overlay(crop_bgr, dial: DialCalibration | None, hue_ranges=None, saturation_min=50, value_min=50, tip_percentile=95.0) -> np.ndarray` (BGR, same size).

**Steps:**
- [ ] Write the failing tests: `test_pivot_override_manual_source`, `test_stale_override_ignored`, `test_progress_callback_called`, `test_overrides_roundtrip`, `test_render_overlay_shape_and_draws`, `test_render_overlay_without_dial_returns_crop`.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_arrow_calibration.py -q` and expect failures.
- [ ] Implement.
- [ ] Run the tests and ruff until they pass, then commit `claude: arrow calibration — pivot overrides, progress, overlay`.

### Task 2: `CalibrationSession`

**Files:**
- Create: `watermeter/calibration_session.py`
- Test: `tests/unit/test_calibration_session.py`

**Interfaces (produces):**
- `class SessionError(Exception)` maps to 400. `class SessionBusy(Exception)` maps to 409.
- Limits: `MIN_FRAMES = 20`, `MAX_FRAMES_TARGET = 5000`, `MAX_HOURS_TARGET = 336`.
- `CalibrationSession(data_dir: Path, get_config: Callable[[], dict], on_calibrated: Callable[[dict], None] | None = None, clock=datetime.now, calibrate_fn=calibrate_from_archive, start_thread=True)`
- Methods:
  - `start(target_type: str, value: float)`, `stop()`
  - `on_frame(image_bytes: bytes, aligned: bool)`
  - `run()`: starts the job thread; with `start_thread=False` it runs synchronously, for tests
  - `status() -> dict` (state + `frames_on_disk`)
  - `set_pivot_override(roi_id, x, y)`, `clear_pivot_override(roi_id)`: both call `run()`
  - `pending_overrides` (property)
- Paths: `self.state_path = data_dir / "calibration_collection.json"`, `self.frames_dir = data_dir / "calibration_frames"`, calibration file via `calibration_path(config)`.

**Steps:**
- [ ] Write the failing tests:
  - `test_start_validates_target`
  - `test_start_rejects_process_separate`
  - `test_start_clears_frames_and_persists`
  - `test_frames_target_reached_auto_runs`
  - `test_hours_target_reached_auto_runs` (fake clock)
  - `test_unaligned_frames_not_counted`
  - `test_stop_keeps_frames`
  - `test_state_survives_reload`
  - `test_running_job_marked_failed_on_load`
  - `test_run_requires_min_frames`
  - `test_run_busy_rejected`
  - `test_failed_run_keeps_calibration_file`
  - `test_successful_run_saves_and_calls_back`
  - `test_pivot_override_triggers_run_with_overrides`
  - `test_clear_override`
- [ ] Run and expect failures. Implement. Run until passing. Commit `claude: calibration collection session`.

### Task 3: Service wiring + API

**Files:**
- Modify: `watermeter/watermeter_service.py`. It creates `self.calibration_session` (data dir `/data`; `on_calibrated` reloads the inference backend when the mode is calibrated). After alignment in `process_reading`, it calls `on_frame(whole_image, alignment.success)` in the executor, wrapped in try/except so the reading never fails because of it.
- Modify: `watermeter/routes/calibration.py` (new endpoints), `watermeter/routes/config.py` (`POST /api/config/arrows-mode`)
- Test: `tests/unit/test_calibration_routes.py`, `tests/unit/test_config_routes_arrows_mode.py`

**Interfaces:** consumes the Task 2 session via `watermeter_service.get_service().calibration_session`, and the Task 1 `render_overlay`. The preview crop is the base64 from `service.current_state["predictions"]` for that id.

**Steps:**
- [ ] Write the failing route tests:
  - `test_status_returns_session_and_calibration`
  - `test_collect_start_400_409`
  - `test_collect_stop`
  - `test_run_400_409`
  - `test_preview_404_without_reading`
  - `test_preview_plain_without_calibration`
  - `test_preview_overlay_with_calibration`
  - `test_preview_rejects_bad_roi_id`
  - `test_pivot_set_and_clear`
  - `test_pivot_outside_crop_400`
  - `test_arrows_mode_switch_writes_and_reloads`
  - `test_arrows_mode_rejects_unknown`
- [ ] Implement, run `tests/unit tests/regression`, ruff. Commit `claude: calibration API (session, preview, pivot) + arrows-mode switch`.

### Task 4: UI

**Files:**
- Modify: `watermeter/routes/pages.py` (Jinja global `arrows_mode()`; `GET /calibration`), `watermeter/templates/_nav.html` (gated item), `watermeter/templates/roi_config.html` + `watermeter/static/roi-config.js` (mode select in the Analogs step)
- Create: `watermeter/templates/calibration.html`, `watermeter/static/calibration.js`
- Modify: `watermeter/static/style.css` (calibration styles)
- Test: `tests/unit/test_calibration_page.py`, using the real templates via a dedicated Jinja2Templates and the `arrows_mode` global

**Steps:**
- [ ] Write the failing tests:
  - `test_nav_shows_calib_only_when_calibrated`
  - `test_nav_hidden_when_not_calibrated` (also with a broken or absent service)
  - `test_calibration_page_renders_both_modes`
- [ ] Implement the templates and JS. Run tests and ruff. Commit `claude: calibration tab UI + arrows-mode select on ROI page`.

### Task 5: Verify, docs, push

- [ ] `./debug.sh --detach`, then on :8002:
  - Start a session with a frame target of 25 (cyclic 60 s) and let it auto-calibrate.
  - Set a pivot, then reset it to automatic.
  - Take Playwright screenshots (light/dark, desktop/phone) and check the layout.
- [ ] README (feature + endpoints) and backlog (BL-83 open items → done, new BL if needed). Commit, push both remotes, check CI.
