"""Unit tests for bulk training data operations (move-to-input, delete)."""

import pytest

from watermeter.routes.models import bulk_delete_logic, bulk_move_to_input_logic


@pytest.fixture
def training_tree(tmp_path):
    """Create a realistic ground_truth + input directory tree."""
    gt_dir = tmp_path / "digits" / "ground_truth" / "3"
    gt_dir.mkdir(parents=True)
    input_dir = tmp_path / "digits" / "input"
    input_dir.mkdir(parents=True)

    # Create 3 fake JPEG files
    for i in range(3):
        (gt_dir / f"img_{i}.jpg").write_bytes(b"\xff\xd8fake")

    return tmp_path


class TestBulkMoveToInputLogic:
    def test_moves_files_to_input(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg", "img_1.jpg"],
        )
        assert result["moved_count"] == 2
        assert result["error_count"] == 0
        assert result["errors"] == []
        input_dir = training_tree / "digits" / "input"
        assert (input_dir / "img_0.jpg").exists()
        assert (input_dir / "img_1.jpg").exists()
        gt_dir = training_tree / "digits" / "ground_truth" / "3"
        assert not (gt_dir / "img_0.jpg").exists()
        assert not (gt_dir / "img_1.jpg").exists()
        assert (gt_dir / "img_2.jpg").exists()

    def test_missing_file_returns_error(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["nonexistent.jpg"],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 1
        assert "not found" in result["errors"][0].lower()

    def test_path_traversal_blocked(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["../../etc/passwd"],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 1

    def test_empty_filenames_returns_zero(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=[],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 0

    def test_creates_input_dir_if_missing(self, tmp_path):
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "a.jpg").write_bytes(b"\xff\xd8fake")
        result = bulk_move_to_input_logic(
            training_path=tmp_path,
            model_type="digits",
            class_name="5",
            filenames=["a.jpg"],
        )
        assert result["moved_count"] == 1
        assert (tmp_path / "digits" / "input" / "a.jpg").exists()


class TestBulkDeleteLogic:
    def test_deletes_files(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg", "img_1.jpg"],
        )
        assert result["deleted_count"] == 2
        assert result["error_count"] == 0
        assert result["errors"] == []
        gt_dir = training_tree / "digits" / "ground_truth" / "3"
        assert not (gt_dir / "img_0.jpg").exists()
        assert not (gt_dir / "img_1.jpg").exists()
        assert (gt_dir / "img_2.jpg").exists()

    def test_missing_file_returns_error(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["ghost.jpg"],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 1

    def test_path_traversal_blocked(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["../../../etc/passwd"],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 1

    def test_empty_filenames_returns_zero(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=[],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 0
