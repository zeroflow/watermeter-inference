"""Tests for the arrow calibration API routes."""

import json
from unittest.mock import MagicMock, patch


def _setup(mock_service, tmp_path, mode="calibrated"):
    cal = tmp_path / "cal.json"
    mock_service.config = {
        "alignment": {"archive_dir": str(tmp_path / "archive")},
        "inference": {"arrows_mode": mode, "calibrated_arrows": {"calibration_file": str(cal)}},
    }
    return cal


def test_calibrate_endpoint_no_frames(test_client, mock_service, tmp_path):
    # the class the route module catches (other test modules may reload watermeter.arrow_calibration)
    from watermeter.routes.calibration import CalibrationError

    cal = _setup(mock_service, tmp_path)
    cal.write_text('{"old": true}')
    with patch(
        "watermeter.routes.calibration.calibrate_from_archive", side_effect=CalibrationError("no aligned frames")
    ):
        resp = test_client.post("/api/arrows/calibrate")
    assert resp.status_code == 422
    assert "no aligned frames" in resp.json()["message"]
    assert json.loads(cal.read_text()) == {"old": True}  # previous calibration untouched


def test_calibrate_endpoint_success_saves_and_reloads(test_client, mock_service, tmp_path):
    cal = _setup(mock_service, tmp_path)
    inference = MagicMock()
    with (
        patch("watermeter.routes.calibration.calibrate_from_archive", return_value=({}, {"frames": 42})) as run,
        patch("watermeter.routes.calibration.save_calibration") as save,
        patch("watermeter.routes.calibration.get_inference_service", return_value=inference),
    ):
        resp = test_client.post("/api/arrows/calibrate", json={"max_frames": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["reloaded"] is True and body["report"] == {"frames": 42}
    assert run.call_args.args[2] == 50
    save.assert_called_once()
    assert save.call_args.args[0] == cal
    inference.reload_models.assert_called_once_with(mock_service.config)


def test_calibrate_endpoint_no_reload_in_other_modes(test_client, mock_service, tmp_path):
    _setup(mock_service, tmp_path, mode="opencv")
    inference = MagicMock()
    with (
        patch("watermeter.routes.calibration.calibrate_from_archive", return_value=({}, {})),
        patch("watermeter.routes.calibration.save_calibration"),
        patch("watermeter.routes.calibration.get_inference_service", return_value=inference),
    ):
        resp = test_client.post("/api/arrows/calibrate")
    assert resp.status_code == 200 and resp.json()["reloaded"] is False
    inference.reload_models.assert_not_called()


def test_calibrate_endpoint_rejects_tiny_max_frames(test_client, mock_service, tmp_path):
    _setup(mock_service, tmp_path)
    assert test_client.post("/api/arrows/calibrate", json={"max_frames": 1}).status_code == 422


def test_get_calibration_404(test_client, mock_service, tmp_path):
    _setup(mock_service, tmp_path)
    assert test_client.get("/api/arrows/calibration").status_code == 404


def test_get_calibration_returns_file(test_client, mock_service, tmp_path):
    cal = _setup(mock_service, tmp_path)
    cal.write_text('{"version": 1, "dials": {}}')
    resp = test_client.get("/api/arrows/calibration")
    assert resp.status_code == 200 and resp.json()["calibration"]["version"] == 1
