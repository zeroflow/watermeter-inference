"""Tests for the calibrated arrow detector and its InferenceService wiring."""

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.unit.synthetic_dial import CENTRE, SIZE, render_dial

SHIFT = (12.0, -15.0)
ROI = {"x": 0.613, "y": 0.655, "width": 0.15, "height": 0.17}
_MODULES = ("watermeter.opencv_arrows", "watermeter.arrow_calibration", "watermeter.calibrated_arrows")


@pytest.fixture(scope="module")
def cv2():
    """Swap the conftest cv2 mock for the real cv2 while this module runs."""
    mock_cv2 = sys.modules.pop("cv2", None)
    import cv2 as real

    sys.modules["cv2"] = real
    for name in _MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    yield real
    if mock_cv2 is not None:
        sys.modules["cv2"] = mock_cv2
    for name in _MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture(scope="module")
def mods(cv2):
    import watermeter.arrow_calibration as ac
    import watermeter.calibrated_arrows as ca
    import watermeter.opencv_arrows as oa

    return ac, ca, oa


def _dial(ac, roi=ROI):
    return ac.DialCalibration(
        roi=dict(roi),
        crop_size=SIZE,
        centre=CENTRE,
        ellipse_axes=(2 * 117.0, 2 * 117.0 * 0.96),
        ellipse_angle=0.0,
        tick_angles=[i * 36.0 for i in range(10)],
        tick_indices=list(range(10)),
        pivot=(CENTRE[0] + SHIFT[0], CENTRE[1] + SHIFT[1]),
        pivot_source="needle_axes",
        n_ticks=10,
    )


def _jpeg(cv2, img):
    return cv2.imencode(".png", img)[1].tobytes()  # lossless: keep the synthetic geometry exact


def _err(a, b):
    return abs((a - b + 5) % 10 - 5)


@pytest.mark.parametrize("value", [1.23, 4.56, 8.04])
def test_calibrated_reading_precise(mods, cv2, value):
    ac, ca, _ = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)}, rois={"analog_2": ROI})
    res = det.predict_from_bytes(_jpeg(cv2, render_dial(cv2, value, shift=SHIFT)), image_id="analog_2")
    assert len(res["class"].split(".")[1]) == 2
    assert _err(float(res["class"]), value) < 0.03
    assert abs(res["value"] - float(res["class"])) < 0.006
    assert res["confidence"] > 0.9


def test_calibrated_beats_opencv_under_parallax(mods, cv2):
    ac, ca, oa = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    errs_cal, errs_cv = [], []
    for v in np.linspace(0.25, 9.75, 12):
        data = _jpeg(cv2, render_dial(cv2, v, shift=SHIFT))
        errs_cal.append(_err(float(det.predict_from_bytes(data, image_id="analog_2")["class"]), v))
        errs_cv.append(_err(float(oa.OpenCVArrowDetector().predict_from_bytes(data)["class"]), v))
    assert max(errs_cal) < 0.03
    assert np.mean(errs_cv) > 3 * np.mean(errs_cal)


@pytest.mark.parametrize("value", [9.97, 0.02])
def test_wraparound_near_zero(mods, cv2, value):
    ac, ca, _ = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    res = det.predict_from_bytes(_jpeg(cv2, render_dial(cv2, value, shift=SHIFT)), image_id="analog_2")
    assert 0.0 <= float(res["class"]) < 10.0
    assert _err(float(res["class"]), value) < 0.03


def _opencv_result(oa, cv2, data):
    return oa.OpenCVArrowDetector().predict_from_bytes(data)


def test_no_image_id_falls_back(mods, cv2):
    ac, ca, oa = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    data = _jpeg(cv2, render_dial(cv2, 3.3, shift=SHIFT))
    assert det.predict_from_bytes(data) == _opencv_result(oa, cv2, data)


def test_unknown_id_falls_back_and_warns_once(mods, cv2, caplog):
    ac, ca, oa = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    data = _jpeg(cv2, render_dial(cv2, 3.3, shift=SHIFT))
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert det.predict_from_bytes(data, image_id="analog_9") == _opencv_result(oa, cv2, data)
    assert sum("analog_9" in r.getMessage() for r in caplog.records) == 1


def test_stale_roi_falls_back(mods, cv2):
    ac, ca, oa = mods
    moved = dict(ROI, x=ROI["x"] + 0.01)
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)}, rois={"analog_2": moved})
    assert det.calibrated_ids == []
    data = _jpeg(cv2, render_dial(cv2, 3.3, shift=SHIFT))
    assert det.predict_from_bytes(data, image_id="analog_2") == _opencv_result(oa, cv2, data)


def test_crop_size_mismatch_falls_back(mods, cv2):
    ac, ca, oa = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    img = cv2.resize(render_dial(cv2, 3.3, shift=SHIFT), (200, 200))
    data = _jpeg(cv2, img)
    assert det.predict_from_bytes(data, image_id="analog_2") == _opencv_result(oa, cv2, data)


def test_no_needle_is_nan(mods, cv2):
    ac, ca, _ = mods
    det = ca.CalibratedArrowDetector({"analog_2": _dial(ac)})
    res = det.predict_from_bytes(_jpeg(cv2, render_dial(cv2, None)), image_id="analog_2")
    assert res == {"class": "NaN", "confidence": 0.0}


def test_missing_calibration_file_falls_back(mods, tmp_path, monkeypatch):
    _, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    cfg = {
        "images": {"process_separate": False},
        "detection": {"analogs": {"rois": [ROI]}},
        "inference": {"arrows_mode": "calibrated", "calibrated_arrows": {"calibration_file": str(tmp_path / "x.json")}},
    }
    det = ca.CalibratedArrowDetector.from_config(cfg)
    assert det.calibrated_ids == []


def test_from_config_loads_file_and_settings(mods, tmp_path, monkeypatch):
    ac, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    path = tmp_path / "cal.json"
    ac.save_calibration(path, {"analog_1": _dial(ac)}, {})
    cfg = {
        "images": {"process_separate": False},
        "detection": {"analogs": {"rois": [ROI]}},
        "inference": {
            "opencv_arrows": {"saturation_min": 60},
            "calibrated_arrows": {"calibration_file": str(path), "tip_percentile": 90},
        },
    }
    det = ca.CalibratedArrowDetector.from_config(cfg)
    assert det.calibrated_ids == ["analog_1"]
    assert det.tip_percentile == 90 and det.saturation_min == 60


@pytest.mark.parametrize("name", ["../etc/x.json", "/etc/cal.json", "cal.txt"])
def test_calibration_path_must_stay_in_data_dir(mods, tmp_path, monkeypatch, name):
    _, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    with pytest.raises(ValueError):
        ca.calibration_path({"inference": {"calibrated_arrows": {"calibration_file": name}}})


def test_calibration_path_relative_name_and_default(mods, tmp_path, monkeypatch):
    _, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    cfg = {"inference": {"calibrated_arrows": {"calibration_file": "cal.json"}}}
    assert ca.calibration_path(cfg) == (tmp_path / "cal.json").resolve()


def test_traversal_calibration_file_falls_back(mods, tmp_path, monkeypatch):
    _, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    cfg = {"inference": {"calibrated_arrows": {"calibration_file": "/etc/passwd.json"}}}
    assert ca.CalibratedArrowDetector.from_config(cfg).calibrated_ids == []


def _load_real_inference(cv2):
    """Real watermeter.inference (conftest mocks it); OpenVINO faked."""
    path = Path(__file__).resolve().parents[2] / "watermeter" / "inference.py"
    spec = importlib.util.spec_from_file_location("_inference_calibrated", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ov = MagicMock()
    mod.cv2 = cv2
    return mod


def test_inference_service_passes_image_id(mods, cv2, tmp_path, monkeypatch):
    ac, ca, _ = mods
    monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)
    inference = _load_real_inference(cv2)
    path = tmp_path / "cal.json"
    ac.save_calibration(path, {"analog_2": _dial(ac)}, {})
    cfg = {
        "images": {"process_separate": False},
        "detection": {"analogs": {"rois": [ROI, ROI]}},
        "inference": {
            "arrows_mode": "calibrated",
            "calibrated_arrows": {"calibration_file": str(path)},
            "digits_model": str(tmp_path / "missing.xml"),
            "digits_classes": [],
            "digits_resolution": 32,
        },
    }
    svc = inference.InferenceService()
    svc.initialize(cfg)
    data = _jpeg(cv2, render_dial(cv2, 6.66, shift=SHIFT))
    res = svc.predict_from_bytes("arrows", data, image_id="analog_2")
    assert _err(float(res["class"]), 6.66) < 0.03
    detailed = svc.predict_detailed_from_bytes("arrows", data, top_k=3, image_id="analog_2")
    assert detailed == [res]
