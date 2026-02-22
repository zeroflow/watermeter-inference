"""Unit tests for FastAPI API routes (app.py).

These tests use mocked services so they can run without Docker.
They test HTTP-level behavior: status codes, response structure, validation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestConfigEndpoints:
    """Tests for /api/config/* endpoints."""

    def test_get_config(self, test_client):
        resp = test_client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert 'content' in data
        assert 'mqtt' in data['content']

    def test_get_config_schema(self, test_client):
        resp = test_client.get("/api/config/schema.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert 'properties' in schema
        assert 'images' in schema['properties']

    def test_save_valid_config(self, test_client):
        valid_yaml = (
            "images:\n  digits: [d1]\n  arrows: [a1]\n"
            "mqtt:\n  broker: x\n  port: 1883\n"
            "inference:\n  confidence_threshold: 0.5\n"
        )
        resp = test_client.post("/api/config/save", json={
            "content": valid_yaml,
            "save_option": "saveonly"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True

    def test_save_invalid_yaml(self, test_client):
        resp = test_client.post("/api/config/save", json={
            "content": "{{invalid yaml",
            "save_option": "saveonly"
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data['success'] is False

    def test_save_missing_required_sections(self, test_client):
        resp = test_client.post("/api/config/save", json={
            "content": "aiote:\n  host: x\n",
            "save_option": "saveonly"
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data['success'] is False
        assert 'Missing required' in data['message']

    def test_save_config_reloads_service(self, test_client, mock_service):
        """Saving config should call reload_config on the service."""
        valid_yaml = (
            "images:\n  source: url\n"
            "mqtt:\n  broker: localhost\n  port: 1883\n"
            "inference:\n  confidence_threshold: 0.5\n"
        )

        mock_service.reload_config.return_value = {"mqtt_reconnected": False}

        response = test_client.post(
            "/api/config/save",
            json={"content": valid_yaml, "save_option": "saveonly"},
        )

        assert response.status_code == 200
        mock_service.reload_config.assert_called_once()


class TestTrainingStatus:
    """Tests for /api/training/status endpoint."""

    def test_training_status(self, test_client, monkeypatch):
        mock_tm = MagicMock()
        mock_tm.get_training_status.return_value = {"status": "idle"}
        mock_tm.get_benchmark_status.return_value = {"status": "idle"}
        mock_tm.get_queue.return_value = []

        import watermeter.routes.training as training_module
        monkeypatch.setattr(training_module, "get_training_manager", lambda: mock_tm)

        resp = test_client.get("/api/training/status")
        assert resp.status_code == 200
        data = resp.json()
        assert 'training' in data
        assert 'benchmark' in data
        assert 'queue' in data


class TestModelEndpoints:
    """Tests for /api/models/* endpoints."""

    def test_list_models_by_type(self, test_client, monkeypatch):
        mock_mm = MagicMock()
        mock_mm.list_models.return_value = [
            {"id": "model_a", "created_at": "2025-01-01"}
        ]

        import watermeter.routes.models as models_module
        monkeypatch.setattr(models_module, "get_model_manager", lambda: mock_mm)

        resp = test_client.get("/api/models?model_type=digits")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['models']) == 1

    def test_get_model_details(self, test_client, monkeypatch):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = {"id": "model_a", "accuracy": 0.95}

        import watermeter.routes.models as models_module
        monkeypatch.setattr(models_module, "get_model_manager", lambda: mock_mm)

        resp = test_client.get("/api/models/digits/model_a")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['model']['accuracy'] == 0.95

    def test_get_model_not_found(self, test_client, monkeypatch):
        mock_mm = MagicMock()
        mock_mm.get_model.return_value = None

        import watermeter.routes.models as models_module
        monkeypatch.setattr(models_module, "get_model_manager", lambda: mock_mm)

        resp = test_client.get("/api/models/digits/nonexistent")
        assert resp.status_code == 404

    def test_delete_model(self, test_client, monkeypatch):
        mock_mm = MagicMock()
        mock_mm.delete_model.return_value = True

        import watermeter.routes.models as models_module
        monkeypatch.setattr(models_module, "get_model_manager", lambda: mock_mm)

        resp = test_client.delete("/api/models/digits/model_a")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True


class TestLabelValidation:
    """Tests for label validation in /api/label/submit.

    Since the endpoint requires a real file to exist, we test label validation
    by examining the response for bad labels (which fails before file check)
    and by creating temp files for the happy path.
    """

    def test_invalid_model_type(self, test_client, mock_service, tmp_path):
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        # Create the file so we get past the "not found" check to the validation
        input_dir = tmp_path / "invalid" / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "test.jpg").write_bytes(b"\xff\xd8")

        resp = test_client.post("/api/label/submit", json={
            "filename": "test.jpg",
            "model_type": "invalid",
            "label": "5"
        })
        assert resp.status_code == 400

    def test_digits_valid_labels(self, test_client, mock_service, tmp_path):
        """Valid digit labels: 0-9 and NAN."""
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}

        # Create a source image
        input_dir = tmp_path / "digits" / "input"
        input_dir.mkdir(parents=True)

        for label in ['0', '5', '9', 'NAN']:
            img_file = input_dir / f"test_{label}.jpg"
            img_file.write_bytes(b"\xff\xd8")  # minimal JPEG header

            resp = test_client.post("/api/label/submit", json={
                "filename": f"test_{label}.jpg",
                "model_type": "digits",
                "label": label
            })
            assert resp.status_code == 200, f"Label '{label}' should be valid, got {resp.status_code}"

    def test_digits_reject_invalid(self, test_client, mock_service, tmp_path):
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        input_dir = tmp_path / "digits" / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "test.jpg").write_bytes(b"\xff\xd8")

        for bad_label in ['10', '-1', 'nan', 'N', 'abc', '']:
            resp = test_client.post("/api/label/submit", json={
                "filename": "test.jpg",
                "model_type": "digits",
                "label": bad_label
            })
            assert resp.status_code == 400, f"Label '{bad_label}' should be rejected"

    def test_arrows_decimal_labels(self, test_client, mock_service, tmp_path):
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        input_dir = tmp_path / "arrows" / "input"
        input_dir.mkdir(parents=True)

        for label in ['0.0', '1.5', '5.3', '9.9']:
            img_file = input_dir / f"test_{label}.jpg"
            img_file.write_bytes(b"\xff\xd8")

            resp = test_client.post("/api/label/submit", json={
                "filename": f"test_{label}.jpg",
                "model_type": "arrows",
                "label": label
            })
            assert resp.status_code == 200, f"Arrow label '{label}' should be valid"

    def test_arrows_legacy_integer_labels(self, test_client, mock_service, tmp_path):
        """Legacy format: integers 0-99 converted to decimal (12 -> 1.2)."""
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        input_dir = tmp_path / "arrows" / "input"
        input_dir.mkdir(parents=True)

        (input_dir / "test.jpg").write_bytes(b"\xff\xd8")
        resp = test_client.post("/api/label/submit", json={
            "filename": "test.jpg",
            "model_type": "arrows",
            "label": "12"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['label'] == '1.2'

    def test_arrows_reject_out_of_range(self, test_client, mock_service, tmp_path):
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        input_dir = tmp_path / "arrows" / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "test.jpg").write_bytes(b"\xff\xd8")

        for bad_label in ['10.0', '-1.0', 'abc']:
            resp = test_client.post("/api/label/submit", json={
                "filename": "test.jpg",
                "model_type": "arrows",
                "label": bad_label
            })
            assert resp.status_code == 400, f"Arrow label '{bad_label}' should be rejected"

    def test_file_not_found(self, test_client, mock_service, tmp_path):
        mock_service.config['low_confidence'] = {'save_path': str(tmp_path)}
        # Don't create the input directory
        resp = test_client.post("/api/label/submit", json={
            "filename": "ghost.jpg",
            "model_type": "digits",
            "label": "5"
        })
        assert resp.status_code == 404


class TestArchiveEndpoint:
    """Tests for POST /api/models/{type}/{id}/archive."""

    def test_archive_success(self, test_client):
        """Archive endpoint returns success when model exists."""
        mock_mm = MagicMock()
        mock_mm.archive_model.return_value = True
        with patch("watermeter.routes.models.get_model_manager", return_value=mock_mm):
            resp = test_client.post("/api/models/digits/test-model/archive")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_archive_not_found(self, test_client):
        """Archive endpoint returns 404 when model not found."""
        mock_mm = MagicMock()
        mock_mm.archive_model.return_value = False
        with patch("watermeter.routes.models.get_model_manager", return_value=mock_mm):
            resp = test_client.post("/api/models/digits/nonexistent/archive")

        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False


class TestNextImageLabelHint:
    """Tests for _label=X hint parsing in GET /api/label/next-image."""

    def test_returns_label_hint_from_filename(self, test_client, tmp_path):
        """When filename contains _label=X, response includes label_hint."""
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        (digits_input / "img001_label=5.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"low_confidence": {"save_path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_images"] is True
        assert data["label_hint"] == "5"

    def test_no_label_hint_when_absent(self, test_client, tmp_path):
        """When filename has no _label=X pattern, label_hint is null."""
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        (digits_input / "img001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"low_confidence": {"save_path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_images"] is True
        assert data["label_hint"] is None

    def test_label_hint_decimal_for_arrows(self, test_client, tmp_path):
        """Arrow images with _label=4.5 return decimal hint."""
        arrows_input = tmp_path / "arrows" / "input"
        arrows_input.mkdir(parents=True)
        (arrows_input / "dial_label=4.5.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"low_confidence": {"save_path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["label_hint"] == "4.5"


class TestTrainingDataImages:
    """Tests for training data image browsing endpoints."""

    def test_list_images_for_class(self, test_client, tmp_path):
        """GET /api/training-data/images returns filenames for a class."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "img001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        (gt_dir / "img002.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        (gt_dir / "img003.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get(
                "/api/training-data/images",
                params={"type": "digits", "class_name": "5", "offset": "0", "limit": "50"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 3
        assert len(data["filenames"]) == 3

    def test_list_images_pagination(self, test_client, tmp_path):
        """Pagination works with offset and limit."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        for i in range(10):
            (gt_dir / f"img{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get(
                "/api/training-data/images",
                params={"type": "digits", "class_name": "5", "offset": "0", "limit": "3"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert len(data["filenames"]) == 3

    def test_list_images_invalid_type(self, test_client):
        """Invalid type returns 400."""
        resp = test_client.get(
            "/api/training-data/images",
            params={"type": "invalid", "class_name": "5"},
        )
        assert resp.status_code == 400

    def test_serve_image(self, test_client, tmp_path):
        """GET /api/training-data/image serves a JPEG file."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        img_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        (gt_dir / "img001.jpg").write_bytes(img_bytes)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/img001.jpg")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_serve_image_not_found(self, test_client, tmp_path):
        """Missing image returns 404."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/nonexistent.jpg")

        assert resp.status_code == 404

    def test_serve_image_path_traversal(self, test_client, tmp_path):
        """Path traversal attempt returns 400."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/..%2F..%2Fetc%2Fpasswd")

        assert resp.status_code == 400


class TestDedupEndpoint:
    """Tests for POST /api/training-data/dedup."""

    def test_dedup_returns_results(self, test_client, tmp_path):
        """Dedup endpoint returns per-type counts."""
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        arrows_input = tmp_path / "arrows" / "input"
        arrows_input.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {
                    "save_path": str(tmp_path),
                    "dedup_threshold": 10,
                    "dedup_scope": "input",
                }
            }
            with patch("watermeter.routes.models.purge_duplicates") as mock_purge:
                mock_purge.return_value = 3
                resp = test_client.post("/api/training-data/dedup", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "digits" in data["results"]
        assert "arrows" in data["results"]
