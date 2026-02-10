"""Unit tests for pure/testable functions across modules.

Tests functions that don't require Docker or heavy deps:
- app.safe_subpath
- training_manager._generate_arrow_classes / _round_to_arrow_class
"""

import math
from pathlib import Path

import pytest


# === safe_subpath (from app.py) ===
# We re-implement it here since app.py can't be imported directly (cv2/openvino deps).
# The regression test validates it matches the real implementation.

def safe_subpath(base: Path, *parts: str) -> Path:
    """Join path parts to base and verify the result stays inside base (prevents path traversal)."""
    resolved = (base / Path(*parts)).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError(f"Path traversal detected: {'/'.join(parts)}")
    return resolved


class TestSafeSubpath:
    def test_simple_join(self, tmp_path):
        result = safe_subpath(tmp_path, "foo", "bar.txt")
        assert result == (tmp_path / "foo" / "bar.txt").resolve()

    def test_single_part(self, tmp_path):
        result = safe_subpath(tmp_path, "file.txt")
        assert result == (tmp_path / "file.txt").resolve()

    def test_path_traversal_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal detected"):
            safe_subpath(tmp_path, "..", "etc", "passwd")

    def test_dotdot_in_middle(self, tmp_path):
        # tmp_path / "a" / ".." resolves back to tmp_path, which is still inside base
        result = safe_subpath(tmp_path, "a", "..", "b.txt")
        assert result == (tmp_path / "b.txt").resolve()

    def test_deep_traversal_raises(self, tmp_path):
        with pytest.raises(ValueError):
            safe_subpath(tmp_path, "..", "..", "..", "etc", "shadow")

    def test_absolute_path_in_parts(self, tmp_path):
        # Path("/etc") would override the base in Path joining
        with pytest.raises(ValueError):
            safe_subpath(tmp_path, "/etc/passwd")


# === TrainingManager arrow helpers ===
# These are instance methods but purely functional.
# TrainingManager.__init__ tries to create /app/models/ via ModelManager singleton,
# so we patch model_manager to use a temp dir.

from unittest.mock import patch
from training_manager import TrainingManager


@pytest.fixture
def tm(tmp_path):
    with patch('model_manager._model_manager', None):
        with patch('model_manager.ModelManager.__init__', lambda self, **kw: setattr(self, 'models_base_path', tmp_path)):
            return TrainingManager()


class TestGenerateArrowClasses:
    def test_10_classes(self, tm):
        classes = tm._generate_arrow_classes(10)
        assert len(classes) == 10
        assert classes == [str(i) for i in range(10)]

    def test_20_classes(self, tm):
        classes = tm._generate_arrow_classes(20)
        assert len(classes) == 20
        assert classes[0] == "0.0"
        assert classes[1] == "0.5"
        assert classes[-1] == "9.5"

    def test_50_classes(self, tm):
        classes = tm._generate_arrow_classes(50)
        assert len(classes) == 50
        assert classes[0] == "0.0"
        assert classes[1] == "0.2"
        assert classes[-1] == "9.8"

    def test_100_classes(self, tm):
        classes = tm._generate_arrow_classes(100)
        assert len(classes) == 100
        assert classes[0] == "0.0"
        assert classes[1] == "0.1"
        assert classes[-1] == "9.9"

    def test_invalid_raises(self, tm):
        with pytest.raises(ValueError, match="Unsupported num_classes"):
            tm._generate_arrow_classes(7)

    def test_all_classes_unique(self, tm):
        for n in [10, 20, 50, 100]:
            classes = tm._generate_arrow_classes(n)
            assert len(set(classes)) == n, f"Duplicate classes for num_classes={n}"


class TestRoundToArrowClass:
    """Test rounding of ground truth values to arrow class labels."""

    def test_10_floor(self, tm):
        assert tm._round_to_arrow_class(3.7, 10) == "3"
        assert tm._round_to_arrow_class(0.0, 10) == "0"
        assert tm._round_to_arrow_class(9.9, 10) == "9"

    def test_10_wrap(self, tm):
        assert tm._round_to_arrow_class(10.0, 10) == "0"

    def test_20_half_steps(self, tm):
        assert tm._round_to_arrow_class(3.7, 20) == "3.5"
        assert tm._round_to_arrow_class(3.2, 20) == "3.0"
        assert tm._round_to_arrow_class(0.0, 20) == "0.0"

    def test_50_fifth_steps(self, tm):
        assert tm._round_to_arrow_class(3.67, 50) == "3.6"
        assert tm._round_to_arrow_class(3.19, 50) == "3.0"

    def test_100_tenth_steps(self, tm):
        assert tm._round_to_arrow_class(3.67, 100) == "3.6"
        assert tm._round_to_arrow_class(3.61, 100) == "3.6"
        assert tm._round_to_arrow_class(0.05, 100) == "0.0"

    def test_100_wrap(self, tm):
        assert tm._round_to_arrow_class(10.0, 100) == "0.0"

    def test_invalid_raises(self, tm):
        with pytest.raises(ValueError):
            tm._round_to_arrow_class(5.0, 7)

    def test_round_class_is_in_generated_classes(self, tm):
        """Every rounded value should be a valid class label."""
        for num_classes in [10, 20, 50, 100]:
            valid_classes = set(tm._generate_arrow_classes(num_classes))
            for v in [0.0, 1.5, 3.14, 5.0, 7.77, 9.9]:
                result = tm._round_to_arrow_class(v, num_classes)
                assert result in valid_classes, \
                    f"_round_to_arrow_class({v}, {num_classes})={result} not in valid classes"
