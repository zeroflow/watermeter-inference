"""Tests for the calibration collection session (collect frames, auto-calibrate, pivot overrides)."""

import json
from datetime import datetime, timedelta

import pytest

from watermeter.calibration_session import (
    MIN_FRAMES,
    CalibrationSession,
    SessionBusy,
    SessionError,
)

ROI = {"x": 0.6, "y": 0.6, "width": 0.15, "height": 0.17}


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 7, 10, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class FakeCalibrate:
    """Stands in for calibrate_from_archive; records calls, returns or raises."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, config, archive_dir, max_frames=300, reference_path=None, pivot_overrides=None, progress=None):
        self.calls.append({"archive_dir": archive_dir, "pivot_overrides": pivot_overrides})
        if progress:
            progress("align", 1, 1)
        if self.error:
            raise self.error
        return {}, {"frames": 1}


def _config(tmp_path, **images):
    return {
        "images": {"process_separate": False, **images},
        "detection": {"analogs": {"rois": [ROI]}},
        "inference": {
            "arrows_mode": "calibrated",
            "calibrated_arrows": {"calibration_file": str(tmp_path / "cal.json")},
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    import watermeter.calibrated_arrows as ca

    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    cfg = _config(tmp_path)
    clock = FakeClock()
    cal = FakeCalibrate()
    done = []

    def make(**kw):
        return CalibrationSession(
            tmp_path,
            get_config=lambda: cfg,
            on_calibrated=lambda c: done.append(c),
            clock=clock,
            calibrate_fn=kw.pop("calibrate_fn", cal),
            start_thread=False,
            **kw,
        )

    return {"make": make, "cfg": cfg, "clock": clock, "cal": cal, "done": done, "dir": tmp_path}


def _feed(session, n, aligned=True):
    for _ in range(n):
        session.on_frame(b"\xff\xd8frame", aligned)


@pytest.mark.parametrize("target,value", [("frames", 5), ("frames", 6000), ("hours", 0), ("hours", 400), ("bogus", 10)])
def test_start_validates_target(env, target, value):
    with pytest.raises(SessionError):
        env["make"]().start(target, value)


def test_start_rejects_process_separate(env):
    env["cfg"]["images"]["process_separate"] = True
    with pytest.raises(SessionError, match="whole-image"):
        env["make"]().start("frames", 50)


def test_start_clears_frames_and_persists(env):
    s = env["make"]()
    s.frames_dir.mkdir(parents=True)
    (s.frames_dir / "old.jpg").write_bytes(b"x")
    s.start("frames", 50)
    assert list(s.frames_dir.glob("*.jpg")) == []
    st = json.loads(s.state_path.read_text())
    assert st["collecting"] is True and st["target"] == {"type": "frames", "value": 50} and st["frames"] == 0


def test_start_while_collecting_is_busy(env):
    s = env["make"]()
    s.start("frames", 50)
    with pytest.raises(SessionBusy):
        s.start("frames", 50)


def test_frames_target_reached_auto_runs(env):
    s = env["make"]()
    s.start("frames", MIN_FRAMES)
    _feed(s, MIN_FRAMES - 1)
    assert s.status()["collecting"] is True and env["cal"].calls == []
    _feed(s, 1)
    st = s.status()
    assert st["collecting"] is False
    assert st["job"]["state"] == "done"
    assert len(env["cal"].calls) == 1 and env["cal"].calls[0]["archive_dir"] == s.frames_dir
    assert len(list(s.frames_dir.glob("*.jpg"))) == MIN_FRAMES


def test_hours_target_reached_auto_runs(env):
    s = env["make"]()
    s.start("hours", 2)
    _feed(s, MIN_FRAMES)
    assert s.status()["collecting"] is True
    env["clock"].advance(hours=2, seconds=1)
    _feed(s, 1)
    assert s.status()["collecting"] is False and s.status()["job"]["state"] == "done"


def test_hours_target_with_too_few_frames_fails_job(env):
    s = env["make"]()
    s.start("hours", 1)
    _feed(s, 3)
    env["clock"].advance(hours=2)
    _feed(s, 1)
    st = s.status()
    assert st["collecting"] is False and st["job"]["state"] == "failed" and "frames" in st["job"]["message"]


def test_unaligned_frames_not_counted(env):
    s = env["make"]()
    s.start("frames", MIN_FRAMES)
    _feed(s, MIN_FRAMES, aligned=False)
    st = s.status()
    assert st["frames"] == 0 and st["collecting"] is True
    assert st["frames_on_disk"] == MIN_FRAMES  # still stored: the calibration run re-aligns


def test_frames_ignored_when_not_collecting(env):
    s = env["make"]()
    _feed(s, 3)
    assert s.status()["frames_on_disk"] == 0


def test_stop_keeps_frames(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, 5)
    s.stop()
    assert s.status()["collecting"] is False and s.status()["frames_on_disk"] == 5
    with pytest.raises(SessionBusy):
        s.stop()


def test_state_survives_reload(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, 7)
    s2 = env["make"]()
    st = s2.status()
    assert st["collecting"] is True and st["frames"] == 7 and st["target"]["value"] == 100


def test_running_job_marked_failed_on_load(env):
    s = env["make"]()
    state = json.loads(s.state_path.read_text()) if s.state_path.exists() else s.status()
    state["job"] = {"state": "running", "started_at": "2026-10-07T09:00:00"}
    s.state_path.write_text(json.dumps(state))
    st = env["make"]().status()
    assert st["job"]["state"] == "failed" and "restart" in st["job"]["message"]


def test_run_requires_min_frames(env):
    s = env["make"]()
    with pytest.raises(SessionError):
        s.run()


def test_run_busy_rejected(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s._state["job"]["state"] = "running"
    with pytest.raises(SessionBusy):
        s.run()


def test_failed_run_keeps_calibration_file(env):
    from watermeter.arrow_calibration import CalibrationError

    cal_file = env["dir"] / "cal.json"
    cal_file.write_text('{"old": true}')
    s = env["make"](calibrate_fn=FakeCalibrate(error=CalibrationError("only 2 ticks")))
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s.run()
    st = s.status()
    assert st["job"]["state"] == "failed" and "only 2 ticks" in st["job"]["message"]
    assert json.loads(cal_file.read_text()) == {"old": True}
    assert env["done"] == []


def test_successful_run_saves_and_calls_back(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s.run()
    doc = json.loads((env["dir"] / "cal.json").read_text())
    assert doc["version"] == 1 and doc["report"] == {"frames": 1}
    assert env["done"] == [env["cfg"]]
    assert s.status()["job"]["progress"] == {"stage": "align", "done": 1, "total": 1}


def test_pivot_override_triggers_run_with_overrides(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s.set_pivot_override("analog_1", 120.5, 133.0)
    assert env["cal"].calls[-1]["pivot_overrides"] == {"analog_1": {"pivot": [120.5, 133.0], "roi": ROI}}
    doc = json.loads((env["dir"] / "cal.json").read_text())
    assert doc["pivot_overrides"]["analog_1"]["pivot"] == [120.5, 133.0]
    assert s.status()["pending_overrides"] == {}


def test_pivot_override_unknown_roi(env):
    s = env["make"]()
    with pytest.raises(SessionError):
        s.set_pivot_override("analog_9", 1, 1)


def test_clear_override(env):
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s.set_pivot_override("analog_1", 120.5, 133.0)
    s.clear_pivot_override("analog_1")
    assert env["cal"].calls[-1]["pivot_overrides"] == {}
    assert json.loads((env["dir"] / "cal.json").read_text())["pivot_overrides"] == {}


def test_timestamps_carry_timezone(env):
    """The container runs in UTC; the browser must not read naive timestamps as local time."""
    s = env["make"]()
    s.start("frames", 100)
    _feed(s, 1)
    st = s.status()
    for ts in (st["started_at"], st["last_frame_at"]):
        assert datetime.fromisoformat(ts).tzinfo is not None


# --- final review fixes ---------------------------------------------------------------------------


def test_target_reached_during_running_job_does_not_clobber_it(env):
    s = env["make"]()
    s.start("frames", MIN_FRAMES + 1)
    _feed(s, MIN_FRAMES)
    s._state["job"]["state"] = "running"  # a manual "Calibrate now" is in progress
    _feed(s, 1)  # target reached -> run() is busy
    assert s.status()["job"]["state"] == "running"
    assert env["cal"].calls == []


def test_on_calibrated_gets_the_live_config(env, tmp_path):
    live = {"cfg": env["cfg"]}
    seen = []
    switched = dict(env["cfg"], inference=dict(env["cfg"]["inference"], arrows_mode="opencv"))

    def calibrate(config, archive_dir, **kw):
        live["cfg"] = switched  # user switches the mode while the job runs
        return {}, {}

    s = CalibrationSession(
        tmp_path,
        get_config=lambda: live["cfg"],
        on_calibrated=seen.append,
        clock=env["clock"],
        calibrate_fn=calibrate,
        start_thread=False,
    )
    s.start("frames", 100)
    _feed(s, MIN_FRAMES)
    s.run()
    assert seen == [switched]


def test_collection_capped_by_stored_frames(env, monkeypatch):
    import watermeter.calibration_session as cs

    monkeypatch.setattr(cs, "MAX_FRAMES_TARGET", MIN_FRAMES + 2)
    s = env["make"]()
    s.start("frames", MIN_FRAMES + 2)
    _feed(s, MIN_FRAMES + 2, aligned=False)  # camera moved: nothing aligns, target never reached
    st = s.status()
    assert st["collecting"] is False
    assert "limit" in st["stop_reason"]


def test_collection_capped_by_max_duration(env):
    s = env["make"]()
    s.start("frames", 5000)
    _feed(s, MIN_FRAMES)
    env["clock"].advance(hours=337)
    _feed(s, 1)
    st = s.status()
    assert st["collecting"] is False and "limit" in st["stop_reason"]
    assert st["job"]["state"] == "done"  # enough frames: calibrate with what was collected


def test_pivot_override_not_stored_without_frames(env):
    s = env["make"]()
    with pytest.raises(SessionError):
        s.set_pivot_override("analog_1", 10, 10)
    assert s.status()["pending_overrides"] == {}
    with pytest.raises(SessionError):
        s.clear_pivot_override("analog_1")
    assert s.status()["pending_overrides"] == {}
