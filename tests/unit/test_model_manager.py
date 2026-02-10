"""Unit tests for model_manager.py - ModelManager."""

import json

import pytest

from model_manager import ModelManager


@pytest.fixture
def mm(tmp_path):
    """ModelManager with temporary base path."""
    return ModelManager(models_base_path=str(tmp_path / "models"))


def _create_model(mm: ModelManager, model_type: str, model_id: str,
                  metadata: dict = None, create_files: bool = True):
    """Helper to create a model directory with metadata and optional .xml/.bin files."""
    model_dir = mm.models_base_path / model_type / model_id
    model_dir.mkdir(parents=True, exist_ok=True)

    meta = metadata or {"name": model_id, "created_at": "2025-01-01T00:00:00"}
    with open(model_dir / "metadata.json", 'w') as f:
        json.dump(meta, f)

    if create_files:
        (model_dir / f"{model_id}.xml").write_text("<xml/>")
        (model_dir / f"{model_id}.bin").write_bytes(b"\x00")


class TestModelManagerValidation:
    """Tests for model ID and type validation."""

    def test_valid_model_id(self, mm):
        ModelManager._validate_model_id("model_abc_123")  # should not raise

    def test_reject_path_traversal_dotdot(self, mm):
        with pytest.raises(ValueError):
            ModelManager._validate_model_id("../etc/passwd")

    def test_reject_path_traversal_slash(self, mm):
        with pytest.raises(ValueError):
            ModelManager._validate_model_id("foo/bar")

    def test_reject_backslash(self, mm):
        with pytest.raises(ValueError):
            ModelManager._validate_model_id("foo\\bar")

    def test_reject_empty(self, mm):
        with pytest.raises(ValueError):
            ModelManager._validate_model_id("")

    def test_reject_dot(self, mm):
        with pytest.raises(ValueError):
            ModelManager._validate_model_id(".")

    def test_invalid_model_type(self, mm):
        with pytest.raises(ValueError, match="Invalid model type"):
            mm.list_models("invalid_type")


class TestModelManagerCRUD:
    """Tests for list/get/save/delete operations."""

    def test_list_empty(self, mm):
        assert mm.list_models("digits") == []

    def test_list_models(self, mm):
        _create_model(mm, "digits", "model_a", {"created_at": "2025-01-01"})
        _create_model(mm, "digits", "model_b", {"created_at": "2025-06-01"})

        models = mm.list_models("digits")
        assert len(models) == 2
        # Newest first
        assert models[0]['id'] == 'model_b'
        assert models[1]['id'] == 'model_a'

    def test_list_ignores_non_dirs(self, mm):
        _create_model(mm, "digits", "model_a")
        # Create a stray file in the digits dir
        (mm.models_base_path / "digits" / "stray.txt").write_text("junk")

        models = mm.list_models("digits")
        assert len(models) == 1

    def test_list_ignores_dirs_without_metadata(self, mm):
        model_dir = mm.models_base_path / "digits" / "no_meta"
        model_dir.mkdir(parents=True)

        models = mm.list_models("digits")
        assert len(models) == 0

    def test_get_model(self, mm):
        _create_model(mm, "arrows", "model_x")

        model = mm.get_model("arrows", "model_x")
        assert model is not None
        assert model['id'] == 'model_x'
        assert model['model_type'] == 'arrows'
        assert model['files_exist'] is True

    def test_get_model_missing_files(self, mm):
        _create_model(mm, "digits", "model_y", create_files=False)

        model = mm.get_model("digits", "model_y")
        assert model is not None
        assert model['files_exist'] is False

    def test_get_model_nonexistent(self, mm):
        assert mm.get_model("digits", "nope") is None

    def test_save_metadata(self, mm):
        result = mm.save_metadata("digits", "new_model", {"accuracy": 0.95})
        assert result is True

        model = mm.get_model("digits", "new_model")
        assert model['accuracy'] == 0.95

    def test_delete_model(self, mm):
        _create_model(mm, "digits", "to_delete")
        assert mm.delete_model("digits", "to_delete") is True
        assert mm.get_model("digits", "to_delete") is None

    def test_delete_nonexistent(self, mm):
        assert mm.delete_model("digits", "nope") is False

    def test_get_model_metadata_fallback(self, mm):
        """get_model_metadata returns empty dict for nonexistent model."""
        assert mm.get_model_metadata("digits", "nope") == {}


class TestModelManagerActivation:
    """Tests for model activation and active model detection."""

    def test_get_active_model_from_config(self, mm):
        config = {
            'inference': {
                'digits_model': '/app/models/digits/model_abc/model_abc.xml',
                'arrows_model': '/app/models/arrows/model_xyz/model_xyz.xml',
            }
        }
        assert mm.get_active_model("digits", config) == "model_abc"
        assert mm.get_active_model("arrows", config) == "model_xyz"

    def test_get_active_model_empty_config(self, mm):
        assert mm.get_active_model("digits", {}) is None
        assert mm.get_active_model("digits", {'inference': {}}) is None

    def test_get_active_model_invalid_type(self, mm):
        assert mm.get_active_model("bogus", {'inference': {}}) is None

    def test_get_active_model_non_xml(self, mm):
        config = {'inference': {'digits_model': '/app/models/digits/model_abc/model_abc.onnx'}}
        assert mm.get_active_model("digits", config) is None

    def test_activate_model(self, mm, tmp_path):
        _create_model(mm, "digits", "model_act")

        # Write a config file
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "images:\n  digits: []\n  arrows: []\n"
            "mqtt:\n  broker: x\n  port: 1883\n"
            "inference:\n  confidence_threshold: 0.5\n"
        )

        result = mm.activate_model("digits", "model_act", config_file)
        assert result is True

        # Verify config was updated
        from config_utils import load_config
        config = load_config(config_file)
        assert "model_act" in config['inference']['digits_model']

    def test_activate_nonexistent_model(self, mm, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("inference:\n  confidence_threshold: 0.5\n")

        result = mm.activate_model("digits", "nope", config_file)
        assert result is False


class TestModelManagerHelpers:
    """Tests for helper methods."""

    def test_create_model_id(self, mm):
        mid = mm.create_model_id("resnext50", 32, 11, 42, timestamp="20250601_120000")
        assert mid == "model_resnext50_c11_r32_s42_20250601_120000"

    def test_create_model_id_auto_timestamp(self, mm):
        mid = mm.create_model_id("resnet18", 64, 100, 1)
        assert mid.startswith("model_resnet18_c100_r64_s1_")
        assert len(mid) > len("model_resnet18_c100_r64_s1_")

    def test_get_model_path(self, mm):
        _create_model(mm, "digits", "model_p")
        path = mm.get_model_path("digits", "model_p")
        assert path is not None
        assert path.suffix == '.xml'

    def test_get_model_path_nonexistent(self, mm):
        assert mm.get_model_path("digits", "nope") is None

    def test_archive_model(self, mm):
        _create_model(mm, "digits", "model_arch")
        assert mm.archive_model("digits", "model_arch") is True

        model = mm.get_model("digits", "model_arch")
        assert model['status'] == 'archived'

    def test_archive_nonexistent(self, mm):
        assert mm.archive_model("digits", "nope") is False

    def test_refresh_is_noop(self, mm):
        mm.refresh()  # should not raise

    def test_types_are_isolated(self, mm):
        """Digits and arrows are separate namespaces."""
        _create_model(mm, "digits", "same_id")
        _create_model(mm, "arrows", "same_id")

        assert len(mm.list_models("digits")) == 1
        assert len(mm.list_models("arrows")) == 1
        assert mm.get_model("digits", "same_id")['model_type'] == 'digits'
        assert mm.get_model("arrows", "same_id")['model_type'] == 'arrows'
