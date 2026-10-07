"""Tests for the calibration tab API: session control, preview, pivot override, arrows-mode switch."""

import base64
import importlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.unit.synthetic_dial import CENTRE, SIZE, render_dial

ROI = {"x": 0.613, "y": 0.655, "width": 0.15, "height": 0.17}
_CV2_MODULES = (
    "watermeter.opencv_arrows",
    "watermeter.arrow_calibration",
    "watermeter.calibrated_arrows",
)


@pytest.fixture(scope="module")
def cv2():
    """Real cv2 for the preview route: reload keeps the module dicts the route's imported helpers refer to."""
    mock_cv2 = sys.modules.pop("cv2", None)
    import cv2 as real

    sys.modules["cv2"] = real
    for name in _CV2_MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    yield real
    if mock_cv2 is not None:
        sys.modules["cv2"] = mock_cv2
    for name in _CV2_MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


class FakeCalibrate:
    def __init__(self):
        self.calls = []

    def __call__(self, config, archive_dir, max_frames=300, reference_path=None, pivot_overrides=None, progress=None):
        self.calls.append(pivot_overrides)
        return {}, {"frames": 1}


@pytest.fixture
def setup(cv2, test_client, mock_service, tmp_path, monkeypatch):
    import watermeter.calibrated_arrows as ca
    from watermeter.calibration_session import CalibrationSession

    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    mock_service.config = {
        "images": {"process_separate": False},
        "detection": {"analogs": {"rois": [ROI, ROI]}},
        "inference": {"arrows_mode": "calibrated", "calibrated_arrows": {"calibration_file": "cal.json"}},
    }
    fake = FakeCalibrate()
    mock_service.calibration_session = CalibrationSession(
        tmp_path, get_config=lambda: mock_service.config, calibrate_fn=fake, start_thread=False
    )
    return {"client": test_client, "service": mock_service, "fake": fake, "dir": tmp_path, "ca": ca}


def _set_crop(service, roi_id="analog_2", value=3.3):
    import cv2 as real

    crop = render_dial(real, value, shift=(12.0, -15.0))
    b64 = base64.b64encode(real.imencode(".png", crop)[1].tobytes()).decode()
    service.current_state = {"predictions": [{"id": roi_id, "class": "3.30", "image_base64": b64}]}
    return crop


def _write_calibration(env, roi=ROI):
    from watermeter.arrow_calibration import DialCalibration, save_calibration

    dial = DialCalibration(
        roi=dict(roi),
        crop_size=SIZE,
        centre=CENTRE,
        ellipse_axes=(234.0, 224.64),
        ellipse_angle=0.0,
        tick_angles=[i * 36.0 for i in range(10)],
        tick_indices=list(range(10)),
        pivot=(CENTRE[0] + 12.0, CENTRE[1] - 15.0),
        pivot_source="parallax",
        n_ticks=10,
    )
    save_calibration(env["dir"] / "cal.json", {"analog_2": dial}, {"opencv": {"mae": 0.13}})


def _feed(env, n=20):
    s = env["service"].calibration_session
    if not s.status()["collecting"]:
        s.start("frames", 100)
    for _ in range(n):
        s.on_frame(b"jpg", True)


def test_status_returns_session_and_calibration(setup):
    _write_calibration(setup)
    _set_crop(setup["service"])
    body = setup["client"].get("/api/calibration/status").json()
    assert body["arrows_mode"] == "calibrated"
    assert body["session"]["collecting"] is False and body["session"]["min_frames"] == 20
    dial = body["calibration"]["dials"]["analog_2"]
    assert dial["pivot_source"] == "parallax" and dial["stale"] is False and dial["value"] == "3.30"
    assert body["calibration"]["report"]["opencv"]["mae"] == 0.13


def test_status_without_calibration(setup):
    body = setup["client"].get("/api/calibration/status").json()
    assert body["calibration"] is None


def test_status_marks_stale_dial(setup):
    _write_calibration(setup, roi=dict(ROI, x=0.1))
    body = setup["client"].get("/api/calibration/status").json()
    assert body["calibration"]["dials"]["analog_2"]["stale"] is True


def test_collect_start_400_409(setup):
    c = setup["client"]
    assert c.post("/api/calibration/collect/start", json={"type": "frames", "value": 5}).status_code == 400
    assert c.post("/api/calibration/collect/start", json={"type": "frames", "value": 50}).status_code == 200
    assert c.post("/api/calibration/collect/start", json={"type": "hours", "value": 2}).status_code == 409


def test_collect_stop(setup):
    c = setup["client"]
    assert c.post("/api/calibration/collect/stop").status_code == 409
    c.post("/api/calibration/collect/start", json={"type": "hours", "value": 2})
    assert c.post("/api/calibration/collect/stop").status_code == 200
    assert setup["service"].calibration_session.status()["collecting"] is False


def test_run_400_409(setup):
    c = setup["client"]
    assert c.post("/api/calibration/run").status_code == 400
    _feed(setup)
    assert c.post("/api/calibration/run").status_code == 200
    assert setup["service"].calibration_session.status()["job"]["state"] == "done"
    setup["service"].calibration_session._state["job"]["state"] = "running"
    assert c.post("/api/calibration/run").status_code == 409


def test_preview_404_without_reading(setup):
    setup["service"].current_state = {}
    assert setup["client"].get("/api/calibration/preview/analog_2.jpg").status_code == 404


def test_preview_plain_without_calibration(setup, cv2):
    crop = _set_crop(setup["service"])
    resp = setup["client"].get("/api/calibration/preview/analog_2.jpg")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == crop.shape
    assert np.mean(np.abs(img.astype(int) - crop.astype(int))) < 3  # JPEG noise only, no overlay


def test_preview_overlay_with_calibration(setup, cv2):
    _write_calibration(setup)
    crop = _set_crop(setup["service"])
    resp = setup["client"].get("/api/calibration/preview/analog_2.jpg")
    img = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert np.count_nonzero(np.abs(img.astype(int) - crop.astype(int)).max(axis=2) > 60) > 300


def test_preview_rejects_bad_roi_id(setup):
    assert setup["client"].get("/api/calibration/preview/..%2Fetc.jpg").status_code in (400, 404)
    assert setup["client"].get("/api/calibration/preview/digit_1.jpg").status_code == 400


def test_pivot_set_and_clear(setup):
    _feed(setup)
    _set_crop(setup["service"])
    c = setup["client"]
    resp = c.post("/api/calibration/pivot/analog_2", json={"x": 130.0, "y": 120.0})
    assert resp.status_code == 200
    assert setup["fake"].calls[-1] == {"analog_2": {"pivot": [130.0, 120.0], "roi": ROI}}
    assert c.delete("/api/calibration/pivot/analog_2").status_code == 200
    assert setup["fake"].calls[-1] == {}


def test_pivot_outside_crop_400(setup):
    _feed(setup)
    _set_crop(setup["service"])
    resp = setup["client"].post("/api/calibration/pivot/analog_2", json={"x": 500.0, "y": 10.0})
    assert resp.status_code == 400
    assert setup["fake"].calls == []


def test_pivot_without_crop_400(setup):
    setup["service"].current_state = {}
    assert setup["client"].post("/api/calibration/pivot/analog_2", json={"x": 1.0, "y": 1.0}).status_code == 400


def test_arrows_mode_switch_writes_and_reloads(test_client, mock_service, tmp_path):
    (tmp_path / "config.yaml").write_text(
        "images:\n  digits: [digit_1]\n  arrows: [analog_1]\n"
        "mqtt:\n  broker: localhost\n  port: 1883\n"
        "inference:\n  confidence_threshold: 0.6\n  arrows_mode: model  # backend\n"
    )
    mock_service.reload_config = MagicMock()
    resp = test_client.post("/api/config/arrows-mode", json={"mode": "calibrated"})
    assert resp.status_code == 200
    text = (tmp_path / "config.yaml").read_text()
    assert "arrows_mode: calibrated" in text and "# backend" in text  # comment preserved
    assert mock_service.reload_config.call_args.args[0]["inference"]["arrows_mode"] == "calibrated"


def test_arrows_mode_rejects_unknown(test_client, mock_service):
    with patch("watermeter.config_utils.update_config") as upd:
        assert test_client.post("/api/config/arrows-mode", json={"mode": "magic"}).status_code == 400
    upd.assert_not_called()
