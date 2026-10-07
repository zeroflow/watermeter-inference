"""Calibration frame collection + background calibration job for ``arrows_mode: calibrated``.

A session collects raw whole images into its own directory until a target (hours or aligned frames)
is reached, then runs the arrow calibration in a background thread. Raw frames (not crops) are kept,
so a ROI edit does not invalidate the collection. State survives restarts via a JSON file.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from watermeter.arrow_calibration import calibrate_from_archive, load_pivot_overrides, save_calibration
from watermeter.calibrated_arrows import analog_rois_from_config, calibration_path

logger = logging.getLogger(__name__)

MIN_FRAMES = 20
MAX_FRAMES_TARGET = 5000
MAX_HOURS_TARGET = 336  # 14 days
MAX_CALIBRATION_FRAMES = 300  # frames used per calibration run (spread over the collection)
ROI_ID_RE = re.compile(r"^analog_\d+$")


class SessionError(Exception):
    """Invalid request (HTTP 400)."""


class SessionBusy(Exception):
    """Conflicting state: already collecting / job running / not collecting (HTTP 409)."""


def _idle_job() -> dict:
    return {"state": "idle", "started_at": None, "finished_at": None, "message": None, "progress": None}


def _initial_state() -> dict:
    return {
        "collecting": False,
        "started_at": None,
        "target": None,
        "frames": 0,
        "last_frame_at": None,
        "job": _idle_job(),
        "pending_overrides": {},
    }


class CalibrationSession:
    """Owns the collection state, the frame directory and the calibration job."""

    def __init__(
        self,
        data_dir,
        get_config: Callable[[], dict],
        on_calibrated: Callable[[dict], None] | None = None,
        clock: Callable[[], datetime] = datetime.now,
        calibrate_fn=calibrate_from_archive,
        start_thread: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.state_path = self.data_dir / "calibration_collection.json"
        self.frames_dir = self.data_dir / "calibration_frames"
        self._get_config = get_config
        self._on_calibrated = on_calibrated
        self._clock = clock
        self._calibrate = calibrate_fn
        self._start_thread = start_thread
        self._lock = threading.RLock()
        self._state = self._load()

    # --- persistence ------------------------------------------------------------------------------

    def _load(self) -> dict:
        state = _initial_state()
        try:
            state.update(json.loads(self.state_path.read_text()))
        except FileNotFoundError:
            return state
        except (OSError, ValueError) as e:
            logger.warning(f"Calibration session state unreadable ({e}) -- starting fresh")
            return _initial_state()
        if (state.get("job") or {}).get("state") == "running":
            state["job"] = {**_idle_job(), "state": "failed", "message": "interrupted by restart"}
            self._state = state
            self._save()
        return state

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, indent=2))
        os.replace(tmp, self.state_path)

    def _now_iso(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    # --- collection -------------------------------------------------------------------------------

    def start(self, target_type: str, value) -> None:
        """Start a new collection (deletes the previous session's frames)."""
        if target_type == "frames":
            if not MIN_FRAMES <= int(value) <= MAX_FRAMES_TARGET or int(value) != value:
                raise SessionError(f"frame target must be {MIN_FRAMES}-{MAX_FRAMES_TARGET}")
            value = int(value)
        elif target_type == "hours":
            if not 1 <= float(value) <= MAX_HOURS_TARGET:
                raise SessionError(f"hour target must be 1-{MAX_HOURS_TARGET}")
            value = float(value)
        else:
            raise SessionError("target type must be 'hours' or 'frames'")
        if self._get_config().get("images", {}).get("process_separate", False):
            raise SessionError("calibration needs whole-image mode (images.process_separate: false)")
        with self._lock:
            if self._state["collecting"]:
                raise SessionBusy("already collecting")
            if self._state["job"]["state"] == "running":
                raise SessionBusy("calibration running")
            if self.frames_dir.exists():
                for f in self.frames_dir.glob("*.jpg"):
                    f.unlink()
            self._state.update(
                collecting=True,
                started_at=self._now_iso(),
                target={"type": target_type, "value": value},
                frames=0,
                last_frame_at=None,
            )
            self._save()
        logger.info(f"Calibration collection started (target {value} {target_type})")

    def stop(self) -> None:
        """End collecting, keep the frames."""
        with self._lock:
            if not self._state["collecting"]:
                raise SessionBusy("not collecting")
            self._state["collecting"] = False
            self._save()
        logger.info("Calibration collection stopped")

    def _target_reached(self) -> bool:
        target = self._state["target"] or {}
        if target.get("type") == "frames":
            return self._state["frames"] >= target["value"]
        if target.get("type") == "hours":
            started = datetime.fromisoformat(self._state["started_at"])
            return self._clock() - started >= timedelta(hours=target["value"])
        return False

    def on_frame(self, image_bytes: bytes, aligned: bool) -> None:
        """Store one raw whole image while collecting; unaligned frames are kept but not counted."""
        with self._lock:
            if not self._state["collecting"]:
                return
            self.frames_dir.mkdir(parents=True, exist_ok=True)
            stem = self._clock().strftime("%Y%m%d-%H%M%S-%f")
            target, n = self.frames_dir / f"{stem}.jpg", 1
            while target.exists():  # same timestamp (coarse clock): keep both
                target, n = self.frames_dir / f"{stem}_{n}.jpg", n + 1
            target.write_bytes(image_bytes)
            if aligned:
                self._state["frames"] += 1
            self._state["last_frame_at"] = self._now_iso()
            reached = self._target_reached()
            if reached:
                self._state["collecting"] = False
            self._save()
        if reached:
            logger.info("Calibration collection target reached -- starting calibration")
            try:
                self.run()
            except (SessionError, SessionBusy) as e:
                with self._lock:
                    self._state["job"] = {**_idle_job(), "state": "failed", "message": str(e)}
                    self._save()
                logger.warning(f"Automatic calibration not started: {e}")

    # --- calibration job --------------------------------------------------------------------------

    def frames_on_disk(self) -> int:
        return len(list(self.frames_dir.glob("*.jpg"))) if self.frames_dir.exists() else 0

    def run(self) -> None:
        """Start the calibration job (background thread unless start_thread=False)."""
        with self._lock:
            if self._state["job"]["state"] == "running":
                raise SessionBusy("calibration already running")
            n = self.frames_on_disk()
            if n < MIN_FRAMES:
                raise SessionError(f"need at least {MIN_FRAMES} collected frames, have {n}")
            self._state["job"] = {**_idle_job(), "state": "running", "started_at": self._now_iso()}
            self._save()
        if self._start_thread:
            threading.Thread(target=self._job, name="arrow-calibration", daemon=True).start()
        else:
            self._job()

    def _progress(self, stage: str, done: int, total: int) -> None:
        with self._lock:  # in memory only; persisted at the next state change
            self._state["job"]["progress"] = {"stage": stage, "done": done, "total": total}

    def _merged_overrides(self, path: Path) -> dict:
        merged = load_pivot_overrides(path)
        for rid, ov in self._state["pending_overrides"].items():
            if ov is None:
                merged.pop(rid, None)
            else:
                merged[rid] = ov
        return merged

    def _job(self) -> None:
        config = self._get_config()
        try:
            path = calibration_path(config)
            with self._lock:
                overrides = self._merged_overrides(path)
            dials, report = self._calibrate(
                config,
                self.frames_dir,
                max_frames=MAX_CALIBRATION_FRAMES,
                pivot_overrides=overrides,
                progress=self._progress,
            )
            save_calibration(path, dials, report, pivot_overrides=overrides)
        except Exception as e:  # CalibrationError, ValueError (path), I/O: the old calibration stays
            logger.warning(f"Arrow calibration failed: {e}")
            with self._lock:
                self._state["job"].update(state="failed", finished_at=self._now_iso(), message=str(e))
                self._save()
            return
        with self._lock:
            self._state["pending_overrides"] = {}
            self._state["job"].update(state="done", finished_at=self._now_iso(), message=None)
            self._save()
        logger.info(f"Arrow calibration written to {path}")
        if self._on_calibrated:
            try:
                self._on_calibrated(config)
            except Exception as e:
                logger.error(f"Reload after calibration failed: {e}")

    # --- manual pivots ----------------------------------------------------------------------------

    def _check_roi_id(self, roi_id: str) -> dict:
        rois = analog_rois_from_config(self._get_config()) or {}
        if not ROI_ID_RE.match(roi_id) or roi_id not in rois:
            raise SessionError(f"unknown analog ROI {roi_id!r}")
        return rois[roi_id]

    def set_pivot_override(self, roi_id: str, x: float, y: float) -> None:
        """Set a manual needle pivot (crop px) for a dial and recalibrate."""
        roi = self._check_roi_id(roi_id)
        with self._lock:
            if self._state["job"]["state"] == "running":
                raise SessionBusy("calibration running")
            self._state["pending_overrides"][roi_id] = {"pivot": [float(x), float(y)], "roi": dict(roi)}
            self._save()
        self.run()

    def clear_pivot_override(self, roi_id: str) -> None:
        """Back to the automatic pivot for a dial and recalibrate."""
        self._check_roi_id(roi_id)
        with self._lock:
            if self._state["job"]["state"] == "running":
                raise SessionBusy("calibration running")
            self._state["pending_overrides"][roi_id] = None
            self._save()
        self.run()

    @property
    def pending_overrides(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state["pending_overrides"])

    def status(self) -> dict:
        with self._lock:
            st = copy.deepcopy(self._state)
        st["frames_on_disk"] = self.frames_on_disk()
        st["min_frames"] = MIN_FRAMES
        return st
