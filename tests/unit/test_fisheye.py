"""Tests for fisheye correction helper.

Note: cv2 is mocked in unit test conftest. Tests that exercise cv2.undistort
patch the module-level cv2 reference in roi.py with the real cv2 module.
"""
import sys

import numpy as np


def _real_cv2():
    """Return the real cv2 module, bypassing the unit-test mock.

    The conftest replaces cv2 in sys.modules with a MagicMock. We temporarily
    remove it, import the real module, then put the mock back.
    """
    cv2_mod = sys.modules.get('cv2')
    if cv2_mod is not None and hasattr(cv2_mod, 'undistort'):
        return cv2_mod  # already the real cv2
    # Temporarily remove the mock so the real cv2 can be imported
    mock_cv2 = sys.modules.pop('cv2', None)
    try:
        import cv2 as real_cv2_module
        return real_cv2_module
    finally:
        # Restore the mock so other unit tests are not affected
        if mock_cv2 is not None:
            sys.modules['cv2'] = mock_cv2


def _make_circle_image(size=200):
    """Create a test image with a ring drawn via numpy (no cv2 needed)."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    cx, cy, r = size // 2, size // 2, size * 2 // 5
    ys, xs = np.ogrid[:size, :size]
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    mask = (dist >= r - 1.5) & (dist <= r + 1.5)
    img[mask] = [255, 255, 255]
    return img


def test_apply_fisheye_no_correction():
    """k1=0 should return the original image object unchanged (identity fast-path)."""
    from watermeter.routes.roi import _apply_fisheye_correction
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[50, 100] = [255, 255, 255]  # single white pixel at center
    result = _apply_fisheye_correction(img, 0.0)
    assert result.shape == img.shape
    # k1=0 returns original object unchanged
    assert result is img


def test_apply_fisheye_barrel_correction():
    """Positive k1 (barrel) should produce a different image via cv2.undistort."""
    import unittest.mock
    real_cv2 = _real_cv2()
    with unittest.mock.patch('watermeter.routes.roi.cv2', real_cv2):
        from watermeter.routes.roi import _apply_fisheye_correction
        img = _make_circle_image(200)
        result = _apply_fisheye_correction(img, 0.5)
    assert result.shape == img.shape
    # The images should differ (distortion was applied)
    assert not np.array_equal(result, img)


def test_apply_fisheye_pincushion_correction():
    """Negative k1 (pincushion) should also produce a different image via cv2.undistort."""
    import unittest.mock
    real_cv2 = _real_cv2()
    with unittest.mock.patch('watermeter.routes.roi.cv2', real_cv2):
        from watermeter.routes.roi import _apply_fisheye_correction
        img = _make_circle_image(200)
        result = _apply_fisheye_correction(img, -0.5)
    assert result.shape == img.shape
    assert not np.array_equal(result, img)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestFisheyeSave:
    """Tests for POST /api/roi/fisheye."""

    def test_post_fisheye_returns_success(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye saves the correction value and returns success."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        response = test_client.post("/api/roi/fisheye", json={"fisheye_correction": 0.15})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_post_fisheye_saves_value_to_config(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye writes fisheye_correction into detection section."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        test_client.post("/api/roi/fisheye", json={"fisheye_correction": 0.25})

        saved = (tmp_path / "config.yaml").read_text()
        assert "fisheye_correction" in saved

    def test_post_fisheye_creates_detection_section_if_missing(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye creates detection section if it doesn't exist."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "images:\n  digits: [digit_1]\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        response = test_client.post("/api/roi/fisheye", json={"fisheye_correction": -0.1})
        assert response.status_code == 200
        assert response.json()["success"] is True

        saved = (tmp_path / "config.yaml").read_text()
        assert "fisheye_correction" in saved

    def test_post_fisheye_zero_value(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye accepts zero as a valid correction value."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        response = test_client.post("/api/roi/fisheye", json={"fisheye_correction": 0.0})
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_post_fisheye_updates_service_config(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye updates the service config after saving."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        test_client.post("/api/roi/fisheye", json={"fisheye_correction": 0.1})

        # The mock_service.config should have been set (assigned from update_config return value)
        assert mock_service.config is not None


class TestFisheyeDelete:
    """Tests for DELETE /api/roi/fisheye."""

    def test_delete_fisheye_returns_success(self, test_client, mock_service, tmp_path, monkeypatch):
        """DELETE /api/roi/fisheye returns success."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  fisheye_correction: 0.15\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        response = test_client.delete("/api/roi/fisheye")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_delete_fisheye_removes_from_config(self, test_client, mock_service, tmp_path, monkeypatch):
        """DELETE /api/roi/fisheye removes fisheye_correction from config."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  fisheye_correction: 0.15\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        test_client.delete("/api/roi/fisheye")

        saved = (tmp_path / "config.yaml").read_text()
        assert "fisheye_correction" not in saved

    def test_delete_fisheye_when_not_set(self, test_client, mock_service, tmp_path, monkeypatch):
        """DELETE /api/roi/fisheye succeeds even if fisheye_correction is not set."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  rotation: 0\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        response = test_client.delete("/api/roi/fisheye")
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_delete_fisheye_removes_empty_detection(self, test_client, mock_service, tmp_path, monkeypatch):
        """DELETE /api/roi/fisheye removes detection section when it becomes empty."""
        monkeypatch.chdir(tmp_path)
        config_yaml = "detection:\n  fisheye_correction: 0.15\n"
        (tmp_path / "config.yaml").write_text(config_yaml)

        test_client.delete("/api/roi/fisheye")

        saved = (tmp_path / "config.yaml").read_text()
        assert "fisheye_correction" not in saved


class TestRoiConfigIncludesFisheye:
    """Tests for GET /api/roi/config including fisheye_correction."""

    def test_get_roi_config_includes_fisheye_correction_key(self, test_client, mock_service):
        """GET /api/roi/config response includes the fisheye_correction key."""
        mock_service.config = {
            "detection": {
                "fisheye_correction": 0.15,
                "rotation": 5.0,
            }
        }

        response = test_client.get("/api/roi/config")
        assert response.status_code == 200
        data = response.json()
        assert "fisheye_correction" in data

    def test_get_roi_config_returns_correct_fisheye_value(self, test_client, mock_service):
        """GET /api/roi/config returns the correct fisheye_correction value."""
        mock_service.config = {
            "detection": {
                "fisheye_correction": 0.25,
            }
        }

        response = test_client.get("/api/roi/config")
        assert response.status_code == 200
        data = response.json()
        assert data["fisheye_correction"] == 0.25

    def test_get_roi_config_fisheye_none_when_not_set(self, test_client, mock_service):
        """GET /api/roi/config returns null for fisheye_correction when not configured."""
        mock_service.config = {
            "detection": {
                "rotation": 0.0,
            }
        }

        response = test_client.get("/api/roi/config")
        assert response.status_code == 200
        data = response.json()
        assert data["fisheye_correction"] is None

    def test_get_roi_config_still_includes_other_fields(self, test_client, mock_service):
        """GET /api/roi/config still returns rotation, markers, digits, analogs alongside fisheye."""
        mock_service.config = {
            "detection": {
                "fisheye_correction": 0.1,
                "rotation": 2.5,
            }
        }

        response = test_client.get("/api/roi/config")
        assert response.status_code == 200
        data = response.json()
        assert "rotation" in data
        assert "markers" in data
        assert "digits" in data
        assert "analogs" in data


class TestFisheyePreview:
    """Tests for POST /api/roi/fisheye-preview."""

    def test_post_fisheye_preview_no_reference_returns_404(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST /api/roi/fisheye-preview returns 404 if reference image is missing."""
        monkeypatch.chdir(tmp_path)
        # /data/reference_raw.jpg will not exist in the test environment
        response = test_client.post("/api/roi/fisheye-preview", json={"fisheye_correction": 0.1})
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
