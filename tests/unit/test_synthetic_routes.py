"""Unit tests for synthetic data generation API routes."""

from unittest.mock import MagicMock


class TestSyntheticRoutes:
    """Tests for /api/synthetic/* endpoints."""

    def _reset_status(self):
        """Reset module-level generation status between tests."""
        import watermeter.routes.synthetic as synthetic_module

        synthetic_module._generation_status.update(
            {
                "running": False,
                "job_id": None,
                "progress": 0,
                "total": 0,
                "message": "Idle",
                "type": None,
            }
        )

    def test_generate_endpoint_exists(self, test_client, monkeypatch):
        self._reset_status()
        import watermeter.routes.synthetic as synthetic_module

        mock_tm = MagicMock()
        mock_tm.active_training_job = None
        monkeypatch.setattr(synthetic_module, "get_training_manager", lambda: mock_tm)

        response = test_client.post(
            "/api/synthetic/generate",
            json={
                "type": "digits",
                "count_per_class": 5,
                "seed": 42,
            },
        )
        assert response.status_code in (200, 409)

    def test_generate_returns_job_id(self, test_client, monkeypatch):
        self._reset_status()
        import watermeter.routes.synthetic as synthetic_module

        mock_tm = MagicMock()
        mock_tm.active_training_job = None
        monkeypatch.setattr(synthetic_module, "get_training_manager", lambda: mock_tm)

        response = test_client.post(
            "/api/synthetic/generate",
            json={
                "type": "digits",
                "count_per_class": 5,
                "seed": 42,
            },
        )
        data = response.json()
        if response.status_code == 200:
            assert "job_id" in data
            assert data["success"] is True

    def test_generate_invalid_type(self, test_client):
        self._reset_status()
        response = test_client.post(
            "/api/synthetic/generate",
            json={
                "type": "invalid",
                "count_per_class": 5,
                "seed": 42,
            },
        )
        assert response.status_code == 422

    def test_status_endpoint_exists(self, test_client):
        self._reset_status()
        response = test_client.get("/api/synthetic/status")
        assert response.status_code == 200

    def test_status_returns_fields(self, test_client):
        self._reset_status()
        response = test_client.get("/api/synthetic/status")
        data = response.json()
        assert "running" in data
        assert "job_id" in data
        assert "progress" in data
        assert "message" in data

    def test_delete_endpoint_exists(self, test_client, monkeypatch):
        self._reset_status()
        import watermeter.routes.synthetic as synthetic_module

        mock_gen = MagicMock()
        mock_gen.delete_synthetic.return_value = 0
        monkeypatch.setattr(synthetic_module, "SyntheticGenerator", lambda base_dir: mock_gen)

        response = test_client.delete("/api/synthetic/digits")
        assert response.status_code == 200

    def test_delete_returns_count(self, test_client, monkeypatch):
        self._reset_status()
        import watermeter.routes.synthetic as synthetic_module

        mock_gen = MagicMock()
        mock_gen.delete_synthetic.return_value = 42
        monkeypatch.setattr(synthetic_module, "SyntheticGenerator", lambda base_dir: mock_gen)

        response = test_client.delete("/api/synthetic/digits")
        data = response.json()
        assert "deleted" in data
        assert data["deleted"] == 42

    def test_delete_invalid_type(self, test_client):
        self._reset_status()
        response = test_client.delete("/api/synthetic/invalid")
        assert response.status_code == 400

    def test_generate_conflict_when_running(self, test_client, monkeypatch):
        """POST /generate returns 409 when generation is already running."""
        import watermeter.routes.synthetic as synthetic_module

        synthetic_module._generation_status["running"] = True

        response = test_client.post(
            "/api/synthetic/generate",
            json={
                "type": "digits",
                "count_per_class": 5,
                "seed": 42,
            },
        )
        assert response.status_code == 409
        self._reset_status()

    def test_generate_conflict_when_training(self, test_client, monkeypatch):
        """POST /generate returns 409 when training is active."""
        self._reset_status()
        import watermeter.routes.synthetic as synthetic_module

        mock_job = MagicMock()
        mock_job.status.value = "running"
        mock_tm = MagicMock()
        mock_tm.active_training_job = mock_job
        monkeypatch.setattr(synthetic_module, "get_training_manager", lambda: mock_tm)

        response = test_client.post(
            "/api/synthetic/generate",
            json={
                "type": "digits",
                "count_per_class": 5,
                "seed": 42,
            },
        )
        assert response.status_code == 409

    def test_delete_conflict_when_running(self, test_client, monkeypatch):
        """DELETE returns 409 when generation is running."""
        import watermeter.routes.synthetic as synthetic_module

        synthetic_module._generation_status["running"] = True

        response = test_client.delete("/api/synthetic/digits")
        assert response.status_code == 409
        self._reset_status()

    def test_get_annotations_endpoint(self, test_client):
        response = test_client.get("/api/synthetic/annotations")
        assert response.status_code == 200
        data = response.json()
        assert "annotations" in data
        assert "photos" in data

    def test_save_annotation(self, test_client, tmp_path, monkeypatch):
        import watermeter.routes.synthetic as synthetic_module

        # Patch Path to use tmp_path for the input dir
        input_dir = tmp_path / "arrows" / "input"
        input_dir.mkdir(parents=True)
        (input_dir / "test.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        original_path = synthetic_module.Path

        class MockPath(type(original_path("/"))):
            def __new__(cls, *args, **kwargs):
                if args and args[0] == "/training/arrows/input":
                    return original_path(str(input_dir))
                return original_path(*args, **kwargs)

        monkeypatch.setattr(synthetic_module, "Path", MockPath)

        response = test_client.post(
            "/api/synthetic/annotate",
            json={"filename": "test.jpg", "arrow_class": "9.0"},
        )
        assert response.status_code == 200

    def test_save_annotation_missing_fields(self, test_client):
        response = test_client.post(
            "/api/synthetic/annotate",
            json={"filename": "test.jpg"},
        )
        assert response.status_code == 400
