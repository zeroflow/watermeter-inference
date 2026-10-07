# Calibration Tab — Design

Status: approved in chat on 2026-10-07. It builds on `docs/plans/2026-10-04-calibrated-arrows.md` (BL-83).

## Goal

A user can run the arrow calibration for `arrows_mode: calibrated` end-to-end in the web UI, without YAML or API calls. That means switching the mode on, collecting calibration frames, calibrating, checking the result per dial, and correcting a needle pivot by hand.

## Agreed requirements

- **Navigation:** a "Calib" tab that is only visible when `inference.arrows_mode == "calibrated"`. The mode is switched on the ROI page, in the Analogs step.
- **Collection:** start/stop with a target given either as a duration (hours) or as a number of frames. When the target is reached, collection stops and the calibration starts automatically. A "Calibrate now" button remains.
- **Collection runs server-side:** it continues with the browser closed and survives a restart.
- **Result per dial:**
  - a preview with an overlay: ticks, ellipse, pivot, tip pixels, value
  - key figures: error before/after, pivot source
  - the pivot can be corrected by clicking into the preview
- **Storage approach A:** a dedicated collection session with its own frame directory. The user config is never rewritten for collecting.

## Backend

### `watermeter/calibration_session.py` — `CalibrationSession`

The `WatermeterService` holds it, like its other focused modules.

**State.** It is persisted in `/data/calibration_collection.json` and rewritten atomically on every change:

```json
{
  "collecting": true,
  "started_at": "2026-10-07T10:00:00",
  "target": {"type": "hours", "value": 24},
  "frames": 312,
  "last_frame_at": "2026-10-07T15:12:00",
  "job": {"state": "idle|running|done|failed", "started_at": null, "finished_at": null,
          "message": null, "progress": {"stage": "align|measure", "done": 0, "total": 0}}
}
```

- **Frames:** `/data/calibration_frames/<YYYYmmdd-HHMMSS-ffffff>.jpg` holds raw whole images, never crops, so a ROI edit does not invalidate the collection. The 7-day raw-archive sweep does not touch this directory.
- **Targets:**
  - `hours` must be between 1 and 336 (14 days).
  - `frames` must be between 20 and 5000.
  - The hours target counts from `started_at`.
- **`start(target_type, value)`:**
  - Raises `SessionError` (→ 400) on an invalid target or with `images.process_separate: true`.
  - Raises `SessionBusy` (→ 409) while collecting or while a job runs.
  - Otherwise it empties `calibration_frames/` and resets the counters.
- **`stop()`:** ends collecting and keeps the frames.
- **`on_frame(image_bytes, aligned: bool)`:**
  - Called from `process_reading` in the executor, after alignment, in whole-image mode only.
  - When collecting, it writes the frame. Only aligned frames count toward the target, but all frames are written.
  - When the target is reached, it calls `stop()` and then `run()`.
- **`run()`:**
  - Raises `SessionBusy` if a job is running.
  - Raises `SessionError` if fewer than 20 frames are present.
  - Starts a daemon thread: `calibrate_from_archive(config, frames_dir, max_frames=300, pivot_overrides=…, progress=cb)`, then `save_calibration`, then `get_inference_service().reload_models(config)` if the mode is calibrated.
  - The job is set to `done`, or to `failed` with the `CalibrationError` text; on failure the old file stays untouched.
- **Restart:** a job that is `running` when the state is loaded is set to `failed` with "interrupted by restart". Collecting continues.
- **Config source:** the session reads the live config through a callable (`lambda: service.config`), because the ROI routes replace `service.config`.

### `arrow_calibration.py` extensions

- **`calibrate_dials(..., pivot_overrides=None, progress=None)`:**
  - `pivot_overrides` has the form `{roi_id: {"pivot": [x, y], "roi": {...}}}`.
  - A matching override (same ROI) replaces the estimated pivot with `pivot_source: "manual"`, and counts as a good dial for the parallax model.
  - An override whose ROI differs is ignored and reported under `report["dials"][id]["override_ignored"]`.
  - The `progress(stage, done, total)` callback is optional.
- **`calibrate_from_archive(..., pivot_overrides=None, progress=None)`:** passes both through and reports the `align` stage per frame.
- **Persistence:** the calibration file stores `pivot_overrides`, top level, version stays 1. `load_pivot_overrides(path) -> dict` returns `{}` when there is none.

### Overlay preview

`render_overlay(crop_bgr, dial, mask, value) -> np.ndarray` in `arrow_calibration.py` draws:
- the tick centroids and the ellipse
- the pivot as a cross, coloured by source
- the needle tip pixels, and the line from the pivot towards the tip
- the value text

The crop comes from the last reading: `service.current_state["predictions"][i]["image_base64"]` for that roi id, which the service already keeps. Without a calibration for that dial, the plain crop is returned.

### Endpoints (`routes/calibration.py`)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/calibration/status` | session state + job + summary of the current calibration (per dial: source, ticks, offset, stale/failed flags, current value) + `arrows_mode` |
| POST | `/api/calibration/collect/start` | body `{type: "hours"\|"frames", value}`; 400 invalid, 409 busy |
| POST | `/api/calibration/collect/stop` | 409 when not collecting |
| POST | `/api/calibration/run` | 400 when < 20 frames, 409 when busy |
| GET | `/api/calibration/preview/{roi_id}.jpg` | overlay JPEG; roi_id is validated against `analog_<n>`; 404 when no crop yet |
| POST | `/api/calibration/pivot/{roi_id}` | body `{x, y}` in crop px; 400 outside the crop; stores the override and starts `run()` (409 when busy) |
| DELETE | `/api/calibration/pivot/{roi_id}` | removes the override and starts `run()` |
| POST | `/api/config/arrows-mode` | body `{mode: "model"\|"opencv"\|"calibrated"}`; writes via `config_utils.update_config`, then `service.reload_config` (which reloads the arrow backend) |

Overrides are kept in the session state until the next successful run writes them into the calibration file. The first source is the calibration file, the second the pending overrides in the session state.

The existing `POST /api/arrows/calibrate` and `GET /api/arrows/calibration` stay unchanged (debug archive, script use).

## UI

- **Nav** (`_nav.html`): a "Calib" item with a crosshair icon, rendered only when the Jinja global `arrows_mode()` returns `"calibrated"`. The global reads `watermeter_service.get_service().config` and is registered on the shared `templates` env in `routes/pages.py`.
- **ROI page, Analogs step:** a select "Arrow reading: ML model / OpenCV / Calibrated" with one hint line each.
  - It calls `POST /api/config/arrows-mode`.
  - After switching to calibrated, it shows the link "Not calibrated yet → Calibration tab".
- **`/calibration`** (`calibration.html` + `static/calibration.js`, vanilla JS, CSS variables in `style.css`, no inline styles):
  1. **Status:** calibrated date, active dials, error opencv → calibrated, warnings for stale or failed dials. When the mode is not calibrated, the page shows only a hint and a link to the ROI page.
  2. **Collect frames:**
     - When idle: a radio button for duration [h] or frame count, plus Start.
     - When collecting: a progress bar (frames/target or elapsed/target), elapsed time and Stop, plus a hint that the needles must move.
  3. **Calibrate:**
     - The "Calibrate now" button is disabled when < 20 frames or while busy.
     - Shows job progress (stage, done/total) and the last error message.
  4. **Dials:** one card per dial with:
     - the preview image (cache-busted on every status change), current value, pivot source badge (measured / parallax / manual / scale centre = warning), ticks and offset
     - "Set pivot": click into the image, a marker appears, "Apply" sends it. "Automatic" removes a manual override.
- **Polling:** status every 3 s while collecting or a job runs, otherwise every 30 s. Buttons are disabled while a job runs.

## Error handling

| Situation | Behaviour |
|---|---|
| Restart while collecting | state reloaded, collecting continues |
| Restart during a job | job → failed "interrupted by restart" |
| Calibration fails | job failed with message; previous calibration file untouched |
| One dial fails | listed as failed on its card; runtime falls back to opencv for it |
| Frame alignment fails | frame stored but not counted toward the target |
| Disk | limits 5000 frames / 14 days; a new session deletes old frames |
| `process_separate: true` | start → 400 with explanation |
| Pivot outside the crop | 400 |
| Mode switched away during a session | collecting continues; tab hidden; `/calibration` shows the hint |
| Double start / run | 409 |

## Testing

- `tests/unit/test_calibration_session.py`:
  - state persistence and reload
  - frame target and hours target (fake clock)
  - auto-run at the target (mocked `calibrate_from_archive`)
  - failed run keeps the file
  - a running job is marked failed when the state is loaded
  - concurrent run rejected
  - overrides passed through
  - unaligned frames not counted
- `tests/unit/test_arrow_calibration.py`:
  - `pivot_overrides` gives a manual source and a precise synthetic reading
  - a stale override is ignored
  - the progress callback is called
  - `render_overlay` returns an image of the crop size
- Route tests: every new endpoint including 400/404/409; preview with and without calibration.
- Page tests:
  - the nav item appears only in calibrated mode
  - `/calibration` renders in both modes
  - `arrows-mode` writes the config and calls reload
- Manual (debug container :8002):
  - a session with a frame target of 25 runs to the auto-calibration
  - set and reset a pivot
  - Playwright screenshots, light + dark, desktop + phone width

## Out of scope

- Angle-dependent (harmonic) correction (BL-84).
- Calibrating from the general raw archive via the UI (the API remains).
- Automatic periodic recalibration.
