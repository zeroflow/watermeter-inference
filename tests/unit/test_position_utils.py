"""Tests for position ID utility functions."""
import pytest
from watermeter.position_utils import get_position_ids


class TestGetPositionIds:
    """Test get_position_ids() with various config shapes."""

    def test_whole_image_mode_digits_and_arrows(self):
        """Whole image mode: IDs generated from detection counts."""
        config = {
            "images": {},
            "detection": {
                "digits": {"count": 3},
                "analogs": {"count": 2},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2", "digit_3"]
        assert arrow_ids == ["analog_1", "analog_2"]

    def test_whole_image_mode_digits_only(self):
        """Whole image mode with only digits configured."""
        config = {
            "images": {},
            "detection": {
                "digits": {"count": 5},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2", "digit_3", "digit_4", "digit_5"]
        assert arrow_ids == []

    def test_whole_image_mode_arrows_only(self):
        """Whole image mode with only analogs configured."""
        config = {
            "images": {},
            "detection": {
                "analogs": {"count": 4},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == []
        assert arrow_ids == ["analog_1", "analog_2", "analog_3", "analog_4"]

    def test_whole_image_mode_empty_detection(self):
        """Whole image mode with empty detection config."""
        config = {
            "images": {},
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == []
        assert arrow_ids == []

    def test_whole_image_mode_no_detection_key(self):
        """Whole image mode with no detection key at all."""
        config = {
            "images": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == []
        assert arrow_ids == []

    def test_process_separate_mode(self):
        """Process separate mode: IDs come from config arrays."""
        config = {
            "images": {
                "process_separate": True,
                "digits": ["digit_1", "digit_2", "digit_3"],
                "arrows": ["analog_1", "analog_2"],
            },
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2", "digit_3"]
        assert arrow_ids == ["analog_1", "analog_2"]

    def test_process_separate_preserves_order(self):
        """Process separate mode preserves config order."""
        config = {
            "images": {
                "process_separate": True,
                "digits": ["digit_3", "digit_1"],
                "arrows": ["analog_2", "analog_1"],
            },
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_3", "digit_1"]
        assert arrow_ids == ["analog_2", "analog_1"]

    def test_process_separate_empty_arrows(self):
        """Process separate mode with no arrows."""
        config = {
            "images": {
                "process_separate": True,
                "digits": ["digit_1"],
                "arrows": [],
            },
            "detection": {},
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1"]
        assert arrow_ids == []

    def test_process_separate_default_false(self):
        """process_separate defaults to False when not set."""
        config = {
            "images": {},
            "detection": {
                "digits": {"count": 2},
                "analogs": {"count": 1},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert digit_ids == ["digit_1", "digit_2"]
        assert arrow_ids == ["analog_1"]

    def test_return_types_are_lists(self):
        """Return values should be plain lists."""
        config = {
            "images": {},
            "detection": {
                "digits": {"count": 1},
                "analogs": {"count": 1},
            },
        }
        digit_ids, arrow_ids = get_position_ids(config)
        assert isinstance(digit_ids, list)
        assert isinstance(arrow_ids, list)
