"""Tests for arrow dial calibration geometry (ticks, pivot, parallax, offsets, measurement)."""

import importlib
import sys

import numpy as np
import pytest

from tests.unit.synthetic_dial import CENTRE, SIZE, render_dial

ROIS = {
    "analog_1": {"x": 0.695, "y": 0.465, "width": 0.153, "height": 0.172},
    "analog_2": {"x": 0.613, "y": 0.655, "width": 0.15, "height": 0.17},
    "analog_3": {"x": 0.455, "y": 0.725, "width": 0.15, "height": 0.17},
    "analog_4": {"x": 0.255, "y": 0.64, "width": 0.15, "height": 0.17},
}
ARROW_IDS = ["analog_1", "analog_2", "analog_3", "analog_4"]


@pytest.fixture(scope="module")
def cv2():
    """Swap the conftest cv2 mock for the real cv2 while this module runs."""
    mock_cv2 = sys.modules.pop("cv2", None)
    import cv2 as real

    sys.modules["cv2"] = real
    for name in ("watermeter.opencv_arrows", "watermeter.arrow_calibration"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    yield real
    if mock_cv2 is not None:
        sys.modules["cv2"] = mock_cv2
    for name in ("watermeter.opencv_arrows", "watermeter.arrow_calibration"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture(scope="module")
def ac(cv2):
    import watermeter.arrow_calibration as mod

    return mod


def _true_dial(ac, shift=(12.0, -15.0), offset=0.0):
    """DialCalibration matching the synthetic renderer exactly."""
    return ac.DialCalibration(
        roi=ROIS["analog_2"],
        crop_size=SIZE,
        centre=CENTRE,
        ellipse_axes=(2 * 117.0, 2 * 117.0 * 0.96),
        ellipse_angle=0.0,
        tick_angles=[i * 36.0 for i in range(10)],
        tick_indices=list(range(10)),
        pivot=(CENTRE[0] + shift[0], CENTRE[1] + shift[1]),
        pivot_source="needle_axes",
        offset=offset,
        n_ticks=10,
        n_frames=0,
    )


def _circ_err(a, b):
    return abs((a - b + 5) % 10 - 5)


def test_detect_ticks_finds_ring(ac, cv2):
    bg = render_dial(cv2, None)
    ticks, ellipse = ac.detect_ticks(bg)
    assert len(ticks) == 10
    assert abs(ellipse[0][0] - CENTRE[0]) < 1.0
    assert abs(ellipse[0][1] - CENTRE[1]) < 1.0


def test_detect_ticks_raises_without_ticks(ac, cv2):
    with pytest.raises(ac.CalibrationError):
        ac.detect_ticks(render_dial(cv2, None, ticks=False))


def test_needle_axes_pivot_recovers_offset_pivot(ac, cv2):
    shift = (12.0, -15.0)
    masks = [ac.needle_mask(render_dial(cv2, v, shift=shift, seed=i)) for i, v in enumerate(np.linspace(0, 9.2, 12))]
    pivot, cond = ac.needle_axes_pivot(masks)
    assert cond < 20
    assert np.hypot(pivot[0] - CENTRE[0] - shift[0], pivot[1] - CENTRE[1] - shift[1]) < 1.5


def test_needle_axes_pivot_ill_conditioned_for_static_needle(ac, cv2):
    masks = [ac.needle_mask(render_dial(cv2, 5.0 + 0.02 * i, seed=i)) for i in range(10)]
    _, cond = ac.needle_axes_pivot(masks)
    assert cond > 20


def test_parallax_fit_predicts_fourth_dial(ac):
    vp, s = np.array([1000.0, 900.0]), 0.08
    pos = [np.array(p) for p in ([1500.0, 1000.0], [1300.0, 1400.0], [1000.0, 1500.0], [600.0, 1300.0])]
    samples = [(p, s * (vp - p)) for p in pos[1:]]
    predict = ac.fit_parallax(samples, image_centre=np.array([950.0, 950.0]))
    assert np.allclose(predict(pos[0]), s * (vp - pos[0]), atol=0.5)


def test_parallax_fit_single_dial_uses_image_centre(ac):
    centre = np.array([1000.0, 1000.0])
    p = np.array([1300.0, 1400.0])
    predict = ac.fit_parallax([(p, 0.05 * (centre - p))], image_centre=centre)
    q = np.array([600.0, 1300.0])
    assert np.allclose(predict(q), 0.05 * (centre - q), atol=0.5)


@pytest.mark.parametrize("value", [0.35, 3.47, 7.92])
def test_measure_accuracy_with_parallax(ac, cv2, value):
    dial = _true_dial(ac)
    measured, conf = ac.measure(ac.needle_mask(render_dial(cv2, value, shift=(12.0, -15.0))), dial)
    assert _circ_err(measured, value) < 0.03
    assert conf > 0.9


def test_measure_applies_offset_and_wraps(ac, cv2):
    dial = _true_dial(ac, offset=0.1)
    measured, _ = ac.measure(ac.needle_mask(render_dial(cv2, 9.95, shift=(12.0, -15.0))), dial)
    assert 0.0 <= measured < 10.0
    assert _circ_err(measured, 0.05) < 0.03


def test_measure_empty_mask(ac):
    value, conf = ac.measure(np.zeros((SIZE[1], SIZE[0]), bool), _true_dial(ac))
    assert value is None and conf == 0.0


def _coupled(n, offsets=(0.0, 0.0, 0.0, 0.0)):
    """n readings of 4 geared dials (slow -> fast), plus per-dial offsets."""
    total = np.linspace(5.3, 5.9, n)  # in units of the slowest dial
    vals = np.stack([(total * 10**k) % 10 for k in range(4)], axis=1)
    return (vals + np.asarray(offsets)) % 10


def test_fit_offsets_recovers_injected_offsets(ac):
    vals = _coupled(200, offsets=(0.05, -0.08, 0.04, 0.0))
    applied, raw = ac.fit_offsets(vals)
    assert np.allclose(applied, [-0.05, 0.08, -0.04, 0.0], atol=0.01)
    assert ac.cross_arrow_mae((vals + np.asarray(applied)) % 10) < 0.005


def test_fit_offsets_clamps_large_offsets(ac):
    applied, raw = ac.fit_offsets(_coupled(200, offsets=(0.0, 0.3, 0.0, 0.0)))
    assert applied[1] == 0.0
    assert abs(raw[1] + 0.3) < 0.01


def _synthetic_session(cv2, n=40):
    """Crops for 4 dials over a slow consumption run; dial 1 (slowest) barely moves."""
    shifts = {
        "analog_1": (-18.0, 0.0),
        "analog_2": (-13.0, -15.0),
        "analog_3": (-1.0, -20.0),
        "analog_4": (13.0, -13.0),
    }
    crops = {k: [] for k in ARROW_IDS}
    total = np.linspace(5.30, 5.60, n)
    for i, t in enumerate(total):
        for k, rid in enumerate(ARROW_IDS):
            crops[rid].append(render_dial(cv2, (t * 10**k) % 10, shift=shifts[rid], seed=i * 4 + k))
    return crops, shifts


def test_calibrate_dials_end_to_end(ac, cv2):
    crops, shifts = _synthetic_session(cv2)
    dials, report = ac.calibrate_dials(crops, ROIS, ARROW_IDS)
    assert set(dials) == set(ARROW_IDS)
    for rid in ARROW_IDS[1:]:
        assert dials[rid].pivot_source == "needle_axes"
        exp = (CENTRE[0] + shifts[rid][0], CENTRE[1] + shifts[rid][1])
        assert np.hypot(dials[rid].pivot[0] - exp[0], dials[rid].pivot[1] - exp[1]) < 2.0
        assert dials[rid].n_ticks >= 8
    assert report["calibrated"]["mae_after"] < 0.02
    assert report["frames"] == 40


def test_ill_conditioned_dial_uses_parallax(ac, cv2):
    crops, shifts = _synthetic_session(cv2)
    dials, _ = ac.calibrate_dials(crops, ROIS, ARROW_IDS)
    d1 = dials["analog_1"]
    assert d1.pivot_source == "parallax"
    # synthetic shifts are not an exact radial field, so allow a few px
    assert np.hypot(d1.pivot[0] - CENTRE[0] - shifts["analog_1"][0], d1.pivot[1] - CENTRE[1]) < 8.0


def test_save_load_roundtrip(ac, tmp_path):
    dial = _true_dial(ac, offset=0.04)
    path = tmp_path / "cal.json"
    ac.save_calibration(path, {"analog_2": dial}, {"frames": 3})
    loaded = ac.load_calibration(path)
    assert loaded["analog_2"] == dial


def test_calibrate_from_archive_spreads_frames(ac, tmp_path, monkeypatch):
    for day in ("2026-10-01", "2026-10-02"):
        (tmp_path / day).mkdir()
        for i in range(5):
            (tmp_path / day / f"{i:06d}.jpg").write_bytes(f"{day}-{i}".encode())
    seen = []

    class StubPipeline:
        REFERENCE_PATH = None

        def __init__(self, config):
            pass

        def process_whole_image(self, data):
            seen.append(data.decode())
            return None, type("A", (), {"success": False})()

    monkeypatch.setattr(ac, "ImagePipeline", StubPipeline)
    with pytest.raises(ac.CalibrationError, match="no aligned frames"):
        ac.calibrate_from_archive({"detection": {"analogs": {"rois": []}}}, tmp_path, max_frames=4)
    # evenly spread over the whole archive (more needle rotation -> better pivot fit), oldest + newest included
    assert seen == ["2026-10-01-0", "2026-10-01-3", "2026-10-02-1", "2026-10-02-4"]


def test_pivot_override_manual_source(ac, cv2):
    crops, shifts = _synthetic_session(cv2)
    true_pivot = [CENTRE[0] + shifts["analog_1"][0], CENTRE[1] + shifts["analog_1"][1]]
    overrides = {"analog_1": {"pivot": true_pivot, "roi": ROIS["analog_1"]}}
    dials, report = ac.calibrate_dials(crops, ROIS, ARROW_IDS, pivot_overrides=overrides)
    assert dials["analog_1"].pivot_source == "manual"
    assert dials["analog_1"].pivot == tuple(true_pivot)
    assert report["dials"]["analog_1"]["pivot_source"] == "manual"
    assert report["calibrated"]["mae_after"] < 0.02


def test_stale_override_ignored(ac, cv2):
    crops, _ = _synthetic_session(cv2)
    moved = dict(ROIS["analog_1"], x=0.5)
    overrides = {"analog_1": {"pivot": [10.0, 10.0], "roi": moved}}
    dials, report = ac.calibrate_dials(crops, ROIS, ARROW_IDS, pivot_overrides=overrides)
    assert dials["analog_1"].pivot_source == "parallax"
    assert report["dials"]["analog_1"]["override_ignored"] is True


def test_progress_callback_called(ac, cv2):
    crops, _ = _synthetic_session(cv2, n=12)
    calls = []
    try:
        ac.calibrate_dials(crops, ROIS, ARROW_IDS, progress=lambda *a: calls.append(a))
    except ac.CalibrationError:
        pass  # too little rotation in 12 frames is fine here; progress must still be reported
    measure = [c for c in calls if c[0] == "measure"]
    assert measure and measure[-1][1:] == (4, 4)


def test_archive_progress_reports_align(ac, tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    for i in range(3):
        (tmp_path / "d" / f"{i}.jpg").write_bytes(b"x")

    class StubPipeline:
        def __init__(self, config):
            pass

        def process_whole_image(self, data):
            return None, type("A", (), {"success": False})()

    monkeypatch.setattr(ac, "ImagePipeline", StubPipeline)
    calls = []
    with pytest.raises(ac.CalibrationError):
        ac.calibrate_from_archive({}, tmp_path, progress=lambda *a: calls.append(a))
    assert calls == [("align", 1, 3), ("align", 2, 3), ("align", 3, 3)]


def test_overrides_roundtrip(ac, tmp_path):
    path = tmp_path / "cal.json"
    assert ac.load_pivot_overrides(path) == {}
    ov = {"analog_1": {"pivot": [127.0, 134.5], "roi": ROIS["analog_1"]}}
    ac.save_calibration(path, {"analog_2": _true_dial(ac)}, {}, pivot_overrides=ov)
    assert ac.load_pivot_overrides(path) == ov
    assert set(ac.load_calibration(path)) == {"analog_2"}


def test_render_overlay_shape_and_draws(ac, cv2):
    crop = render_dial(cv2, 3.3, shift=(12.0, -15.0))
    out = ac.render_overlay(crop, _true_dial(ac))
    assert out.shape == crop.shape
    assert np.count_nonzero(np.any(out != crop, axis=2)) > 500


def test_render_overlay_without_dial_returns_crop(ac, cv2):
    crop = render_dial(cv2, 3.3)
    assert np.array_equal(ac.render_overlay(crop, None), crop)


def test_no_needle_movement_fails_instead_of_degrading(ac, cv2):
    """All needles static -> no pivot can be measured -> refuse rather than write a scale-centre calibration."""
    crops = {k: [render_dial(cv2, 3.0 + 0.001 * i, shift=(10.0, -12.0), seed=i) for i in range(25)] for k in ARROW_IDS}
    with pytest.raises(ac.CalibrationError, match="needles"):
        ac.calibrate_dials(crops, ROIS, ARROW_IDS)


def test_calibration_created_at_has_timezone(ac, tmp_path):
    import json as _json
    from datetime import datetime as _dt

    ac.save_calibration(tmp_path / "c.json", {"analog_2": _true_dial(ac)}, {})
    assert _dt.fromisoformat(_json.loads((tmp_path / "c.json").read_text())["created_at"]).tzinfo is not None
