"""Unit tests for BL-03: mislabel detection (scan + confirm)."""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper to create ground truth fixtures
# ---------------------------------------------------------------------------

def _make_gt_image(class_dir: Path, filename: str = "img_001.jpg"):
    """Create a minimal fake jpg file in a ground truth class directory."""
    class_dir.mkdir(parents=True, exist_ok=True)
    (class_dir / filename).write_bytes(b'\xff\xd8\xff\xe0fake_jpeg_data')
    return class_dir / filename


# ---------------------------------------------------------------------------
# Tests for scan_mislabeled (pure logic, mocked inference)
# ---------------------------------------------------------------------------

class TestScanMislabeled:
    """Test the scan_mislabeled() function."""

    def test_empty_ground_truth(self, tmp_path):
        """Empty or nonexistent GT directory returns zero suspects."""
        from watermeter.routes.models import scan_mislabeled

        # Mock inference service
        mock_svc = MagicMock()
        mock_classifier = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("digits", tmp_path)

        assert result["total_scanned"] == 0
        assert result["total_suspects"] == 0
        assert result["suspects"] == []

    def test_all_correct_predictions(self, tmp_path):
        """When model agrees with all labels, no suspects."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        _make_gt_image(gt_base / "3", "a.jpg")
        _make_gt_image(gt_base / "7", "b.jpg")

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']

        def side_effect(path):
            folder = Path(path).parent.name
            return {'class': folder, 'confidence': 0.95}

        mock_classifier.predict.side_effect = side_effect

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("digits", tmp_path)

        assert result["total_scanned"] == 2
        assert result["total_suspects"] == 0

    def test_detects_mislabeled_digits(self, tmp_path):
        """Suspects are collected when prediction != folder label."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        _make_gt_image(gt_base / "3", "correct.jpg")
        _make_gt_image(gt_base / "3", "wrong.jpg")
        _make_gt_image(gt_base / "5", "also_correct.jpg")

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']

        def side_effect(path):
            fname = Path(path).name
            if fname == "wrong.jpg":
                return {'class': '8', 'confidence': 0.72}
            return {'class': Path(path).parent.name, 'confidence': 0.95}

        mock_classifier.predict.side_effect = side_effect

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("digits", tmp_path)

        assert result["total_scanned"] == 3
        assert result["total_suspects"] == 1

        suspect = result["suspects"][0]
        assert suspect["current_label"] == "3"
        assert suspect["predicted_label"] == "8"
        assert suspect["confidence"] == 0.72
        assert suspect["filename"] == "wrong.jpg"
        assert "image_base64" in suspect

    def test_arrows_regression_tolerance(self, tmp_path):
        """Regression arrows: within 0.5 dial positions is NOT a suspect."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "arrows" / "ground_truth"
        _make_gt_image(gt_base / "3.5", "close.jpg")
        _make_gt_image(gt_base / "3.5", "far.jpg")

        # Create a mock regressor (no 'classes' attr)
        mock_regressor = MagicMock(spec=['predict', 'predict_detailed',
                                         'preprocess', 'compiled',
                                         'resolution', 'label_config_tag',
                                         'model_path'])

        def side_effect(path):
            fname = Path(path).name
            if fname == "close.jpg":
                # 3.3 is within 0.5 of 3.5 -- should NOT be a suspect
                return {'class': '3.3', 'confidence': 0.80}
            else:
                # 5.1 is far from 3.5 -- should be a suspect
                return {'class': '5.1', 'confidence': 0.65}

        mock_regressor.predict.side_effect = side_effect

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_regressor

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("arrows", tmp_path)

        assert result["total_scanned"] == 2
        assert result["total_suspects"] == 1
        assert result["suspects"][0]["filename"] == "far.jpg"
        assert result["suspects"][0]["current_label"] == "3.5"
        assert result["suspects"][0]["predicted_label"] == "5.1"

    def test_arrows_regression_wraparound(self, tmp_path):
        """Regression arrows: wraparound case 9.9 vs 0.1 should NOT be a suspect."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "arrows" / "ground_truth"
        _make_gt_image(gt_base / "9.9", "wrap_close.jpg")
        _make_gt_image(gt_base / "9.9", "wrap_far.jpg")

        mock_regressor = MagicMock(spec=['predict', 'predict_detailed',
                                         'preprocess', 'compiled',
                                         'resolution', 'label_config_tag',
                                         'model_path'])

        def side_effect(path):
            fname = Path(path).name
            if fname == "wrap_close.jpg":
                # 0.1 is within 0.2 of 9.9 circularly -- should NOT be a suspect
                return {'class': '0.1', 'confidence': 0.80}
            else:
                # 7.0 is 2.9 away from 9.9 -- should be a suspect
                return {'class': '7.0', 'confidence': 0.65}

        mock_regressor.predict.side_effect = side_effect
        mock_regressor.resolution = 128

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_regressor

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("arrows", tmp_path)

        # Only the far one should be a suspect
        assert result["total_scanned"] == 2
        assert len(result["suspects"]) == 1
        assert result["suspects"][0]["filename"] == "wrap_far.jpg"
        assert result["suspects"][0]["current_label"] == "9.9"
        assert result["suspects"][0]["predicted_label"] == "7.0"

    def test_arrows_classification_exact_match(self, tmp_path):
        """Classification arrows: exact string match required."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "arrows" / "ground_truth"
        _make_gt_image(gt_base / "4.0", "img.jpg")

        mock_classifier = MagicMock()
        mock_classifier.classes = [f"{i/10:.1f}" for i in range(100)]

        # Predict "4.5" but folder is "4.0" -- mismatch
        mock_classifier.predict.return_value = {'class': '4.5', 'confidence': 0.88}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("arrows", tmp_path)

        assert result["total_suspects"] == 1
        assert result["suspects"][0]["predicted_label"] == "4.5"

    def test_no_active_model_raises(self, tmp_path):
        """When no model is loaded, scan should raise RuntimeError."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        _make_gt_image(gt_base / "0", "a.jpg")

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = None

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            with pytest.raises(RuntimeError, match="No active"):
                scan_mislabeled("digits", tmp_path)

    def test_prediction_error_skips_image(self, tmp_path):
        """If predict() raises for one image, it's skipped (not a suspect)."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        _make_gt_image(gt_base / "1", "bad.jpg")
        _make_gt_image(gt_base / "1", "good.jpg")

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']

        def side_effect(path):
            if "bad.jpg" in path:
                raise ValueError("corrupt image")
            return {'class': '1', 'confidence': 0.99}

        mock_classifier.predict.side_effect = side_effect

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("digits", tmp_path)

        # bad.jpg was skipped, good.jpg was correct
        assert result["total_scanned"] == 2
        assert result["total_suspects"] == 0

    def test_suspect_has_base64_thumbnail(self, tmp_path):
        """Suspect entry includes base64 image data."""
        from watermeter.routes.models import scan_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        img_path = _make_gt_image(gt_base / "2", "img.jpg")

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']
        mock_classifier.predict.return_value = {'class': '9', 'confidence': 0.60}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch('watermeter.routes.models.get_inference_service', return_value=mock_svc):
            result = scan_mislabeled("digits", tmp_path)

        suspect = result["suspects"][0]
        assert len(suspect["image_base64"]) > 0
        # Should be valid base64
        import base64
        decoded = base64.b64decode(suspect["image_base64"])
        assert decoded == img_path.read_bytes()


# ---------------------------------------------------------------------------
# Tests for confirm_mislabeled (file move logic)
# ---------------------------------------------------------------------------

class TestConfirmMislabeled:
    """Test the confirm_mislabeled() function."""

    def test_moves_file_to_input(self, tmp_path):
        """Selected file is moved from ground_truth to input with label hint."""
        from watermeter.routes.models import confirm_mislabeled

        gt_dir = tmp_path / "digits" / "ground_truth" / "3"
        gt_dir.mkdir(parents=True)
        (gt_dir / "suspect.jpg").write_bytes(b'image_data')

        result = confirm_mislabeled("digits", tmp_path, [str(gt_dir / "suspect.jpg")])

        assert result["moved_count"] == 1
        assert result["error_count"] == 0

        # File should be gone from ground truth
        assert not (gt_dir / "suspect.jpg").exists()

        # File should exist in input with label hint
        input_dir = tmp_path / "digits" / "input"
        moved_files = list(input_dir.glob("*.jpg"))
        assert len(moved_files) == 1
        assert moved_files[0].name == "suspect_label=3.jpg"

    def test_moves_multiple_files(self, tmp_path):
        """Multiple files from different classes can be moved."""
        from watermeter.routes.models import confirm_mislabeled

        gt_base = tmp_path / "digits" / "ground_truth"
        (gt_base / "3").mkdir(parents=True)
        (gt_base / "7").mkdir(parents=True)
        (gt_base / "3" / "a.jpg").write_bytes(b'img_a')
        (gt_base / "7" / "b.jpg").write_bytes(b'img_b')

        selected = [
            str(gt_base / "3" / "a.jpg"),
            str(gt_base / "7" / "b.jpg"),
        ]

        result = confirm_mislabeled("digits", tmp_path, selected)

        assert result["moved_count"] == 2
        input_dir = tmp_path / "digits" / "input"
        names = sorted(f.name for f in input_dir.glob("*.jpg"))
        assert names == ["a_label=3.jpg", "b_label=7.jpg"]

    def test_nonexistent_file_is_error(self, tmp_path):
        """If a selected file doesn't exist, it's counted as an error."""
        from watermeter.routes.models import confirm_mislabeled

        # Path must be within ground_truth to pass traversal check
        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)
        nonexistent = str(gt_dir / "img.jpg")

        result = confirm_mislabeled("digits", tmp_path, [nonexistent])

        assert result["moved_count"] == 0
        assert result["error_count"] == 1
        assert "not found" in result["errors"][0].lower()

    def test_empty_selection(self, tmp_path):
        """Empty selection list produces zero moves."""
        from watermeter.routes.models import confirm_mislabeled

        result = confirm_mislabeled("digits", tmp_path, [])

        assert result["moved_count"] == 0
        assert result["error_count"] == 0

    def test_avoids_overwrite(self, tmp_path):
        """If a file with the same label-hint name already exists, a counter suffix is added."""
        from watermeter.routes.models import confirm_mislabeled

        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        input_dir = tmp_path / "digits" / "input"
        input_dir.mkdir(parents=True)

        # Pre-existing file in input with the exact label-hint name
        (input_dir / "img_label=5.jpg").write_bytes(b'existing')

        (gt_dir / "img.jpg").write_bytes(b'new_image')

        result = confirm_mislabeled("digits", tmp_path, [str(gt_dir / "img.jpg")])

        assert result["moved_count"] == 1
        # Should have created img_label=5_1.jpg instead of overwriting
        assert (input_dir / "img_label=5.jpg").exists()  # original still there
        assert (input_dir / "img_label=5_1.jpg").exists()  # new file with counter

    def test_arrows_label_hint_with_decimals(self, tmp_path):
        """Arrow labels (e.g. '4.5') are correctly encoded in filename."""
        from watermeter.routes.models import confirm_mislabeled

        gt_dir = tmp_path / "arrows" / "ground_truth" / "4.5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "arrow_img.jpg").write_bytes(b'arrow_data')

        result = confirm_mislabeled("arrows", tmp_path, [str(gt_dir / "arrow_img.jpg")])

        assert result["moved_count"] == 1
        input_dir = tmp_path / "arrows" / "input"
        moved_files = list(input_dir.glob("*.jpg"))
        assert len(moved_files) == 1
        assert moved_files[0].name == "arrow_img_label=4.5.jpg"

    def test_creates_input_dir_if_missing(self, tmp_path):
        """Input directory is created if it doesn't exist."""
        from watermeter.routes.models import confirm_mislabeled

        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)
        (gt_dir / "img.jpg").write_bytes(b'data')

        # input dir does not exist yet
        assert not (tmp_path / "digits" / "input").exists()

        result = confirm_mislabeled("digits", tmp_path, [str(gt_dir / "img.jpg")])

        assert result["moved_count"] == 1
        assert (tmp_path / "digits" / "input").exists()

    def test_confirm_mislabeled_blocks_path_traversal(self, tmp_path):
        """Paths outside ground_truth should be blocked and counted as errors."""
        from watermeter.routes.models import confirm_mislabeled

        # Create a file outside the expected ground_truth directory
        outside_dir = tmp_path / "other"
        outside_dir.mkdir(parents=True)
        outside_file = outside_dir / "secret.jpg"
        outside_file.write_bytes(b'secret_data')

        # Also create the ground_truth dir so the function doesn't fail
        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)

        result = confirm_mislabeled("digits", tmp_path, [str(outside_file)])

        assert result["moved_count"] == 0
        assert result["error_count"] == 1
        assert "outside ground truth" in result["errors"][0].lower()
        # The file outside ground_truth must still exist
        assert outside_file.exists()


# ---------------------------------------------------------------------------
# Tests for _make_thumbnail_base64
# ---------------------------------------------------------------------------

class TestMakeThumbnailBase64:
    """Test the base64 thumbnail helper."""

    def test_valid_file(self, tmp_path):
        from watermeter.routes.models import _make_thumbnail_base64
        import base64

        img = tmp_path / "test.jpg"
        img.write_bytes(b'\xff\xd8\xff\xe0some_jpeg')

        result = _make_thumbnail_base64(img)
        assert len(result) > 0
        assert base64.b64decode(result) == b'\xff\xd8\xff\xe0some_jpeg'

    def test_missing_file(self, tmp_path):
        from watermeter.routes.models import _make_thumbnail_base64

        result = _make_thumbnail_base64(tmp_path / "nonexistent.jpg")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests for API endpoints
# ---------------------------------------------------------------------------

class TestMislabelAPIEndpoints:
    """Test /api/training-data/mislabel/scan and /confirm endpoints."""

    def test_scan_invalid_type(self, test_client):
        """Scan with invalid type returns 400."""
        resp = test_client.post(
            "/api/training-data/mislabel/scan",
            json={"type": "invalid"},
        )
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_confirm_invalid_type(self, test_client):
        """Confirm with invalid type returns 400."""
        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "invalid", "selected": []},
        )
        assert resp.status_code == 400

    def test_confirm_missing_selected(self, test_client):
        """Confirm without 'selected' field returns 400."""
        # Clear any stale scans
        import watermeter.routes.models as models_mod
        models_mod._mislabel_scans.clear()

        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits"},
        )
        assert resp.status_code == 400

    def test_confirm_without_scan(self, test_client):
        """Confirm without prior scan returns 409."""
        import watermeter.routes.models as models_mod
        models_mod._mislabel_scans.clear()

        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": ["/some/path.jpg"]},
        )
        assert resp.status_code == 409
        assert "scan" in resp.json()["message"].lower()

    def test_confirm_empty_selection(self, test_client):
        """Confirm with empty selection returns success with zero moves."""
        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["moved_count"] == 0

    def test_confirm_rejects_paths_not_in_scan(self, test_client):
        """Confirm rejects paths that weren't in the scan results."""
        import watermeter.routes.models as models_mod
        models_mod._mislabel_scans["digits"] = {
            "suspects": [
                {"path": "/training/digits/ground_truth/3/real.jpg"}
            ],
            "total_scanned": 10,
            "total_suspects": 1,
        }

        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": ["/some/other/path.jpg"]},
        )
        assert resp.status_code == 400
        assert "not in the scan" in resp.json()["message"].lower()

    def test_scan_endpoint_returns_structure(self, test_client, tmp_path):
        """Scan endpoint returns expected JSON structure."""
        # Set up fake GT
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        _make_gt_image(gt_base / "0", "a.jpg")

        import watermeter.routes.models as models_mod

        # Configure training path
        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
            }
        }

        # Mock inference to return correct prediction (no suspects)
        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']
        mock_classifier.predict.return_value = {'class': '0', 'confidence': 0.99}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch.object(models_mod, 'get_inference_service', return_value=mock_svc):
            resp = test_client.post(
                "/api/training-data/mislabel/scan",
                json={"type": "digits"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "suspects" in data
        assert "total_scanned" in data
        assert "total_suspects" in data
        assert data["total_scanned"] == 1
        assert data["total_suspects"] == 0

    def test_scan_stores_result_for_confirm(self, test_client, tmp_path):
        """After scan, the result is stored so confirm can validate paths."""
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        img_path = _make_gt_image(gt_base / "3", "suspect.jpg")

        import watermeter.routes.models as models_mod
        models_mod._mislabel_scans.clear()

        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
            }
        }

        # Mock inference to return wrong prediction (creates a suspect)
        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']
        mock_classifier.predict.return_value = {'class': '8', 'confidence': 0.70}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        with patch.object(models_mod, 'get_inference_service', return_value=mock_svc):
            resp = test_client.post(
                "/api/training-data/mislabel/scan",
                json={"type": "digits"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_suspects"] == 1

        # Verify scan is stored
        assert "digits" in models_mod._mislabel_scans
        assert len(models_mod._mislabel_scans["digits"]["suspects"]) == 1

    def test_full_scan_then_confirm_workflow(self, test_client, tmp_path):
        """Full workflow: scan detects suspect, confirm moves it to input."""
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        img_path = _make_gt_image(gt_base / "3", "mislabeled.jpg")

        import watermeter.routes.models as models_mod
        models_mod._mislabel_scans.clear()

        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
            }
        }

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']
        mock_classifier.predict.return_value = {'class': '8', 'confidence': 0.70}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        # Step 1: Scan
        with patch.object(models_mod, 'get_inference_service', return_value=mock_svc):
            resp = test_client.post(
                "/api/training-data/mislabel/scan",
                json={"type": "digits"},
            )
        assert resp.status_code == 200
        scan_data = resp.json()
        suspect_path = scan_data["suspects"][0]["path"]

        # Step 2: Confirm
        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": [suspect_path]},
        )
        assert resp.status_code == 200
        confirm_data = resp.json()
        assert confirm_data["moved_count"] == 1

        # File should be gone from ground_truth
        assert not img_path.exists()

        # File should be in input with label hint
        input_dir = tmp_path / "training" / "digits" / "input"
        moved = list(input_dir.glob("*.jpg"))
        assert len(moved) == 1
        assert "_label=3" in moved[0].name

    def test_confirm_clears_scan(self, test_client, tmp_path):
        """After confirm, the stored scan is cleared."""
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        img_path = _make_gt_image(gt_base / "1", "img.jpg")

        import watermeter.routes.models as models_mod

        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
            }
        }

        mock_classifier = MagicMock()
        mock_classifier.classes = [str(i) for i in range(10)] + ['NAN']
        mock_classifier.predict.return_value = {'class': '9', 'confidence': 0.55}

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = mock_classifier

        # Scan
        with patch.object(models_mod, 'get_inference_service', return_value=mock_svc):
            test_client.post("/api/training-data/mislabel/scan", json={"type": "digits"})

        suspect_path = models_mod._mislabel_scans["digits"]["suspects"][0]["path"]

        # Confirm
        test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": [suspect_path]},
        )

        # Second confirm should fail -- scan was cleared
        resp = test_client.post(
            "/api/training-data/mislabel/confirm",
            json={"type": "digits", "selected": [suspect_path]},
        )
        assert resp.status_code == 409

    def test_scan_no_active_model(self, test_client, tmp_path):
        """Scan when no model is loaded returns 409."""
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        _make_gt_image(gt_base / "0", "a.jpg")

        import watermeter.routes.models as models_mod

        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
            }
        }

        mock_svc = MagicMock()
        mock_svc.get_classifier.return_value = None

        with patch.object(models_mod, 'get_inference_service', return_value=mock_svc):
            resp = test_client.post(
                "/api/training-data/mislabel/scan",
                json={"type": "digits"},
            )

        assert resp.status_code == 409
        assert "no active" in resp.json()["message"].lower()
