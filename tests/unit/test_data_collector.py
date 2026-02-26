"""Unit tests for DataCollector — quota counter logic and persistence."""

import json
from unittest.mock import patch, MagicMock

from watermeter.data_collector import DataCollector

DEFAULT_CONFIG = {
    "enabled": True,
    "quota_per_class": 10,
    "dedup_enabled": False,
    "dedup_threshold": 0.95,
    "counters_file": ".collection_counts.json",
}


def _make_collector(tmp_path, config=None):
    """Helper to create a DataCollector with sensible defaults."""
    cfg = config or DEFAULT_CONFIG.copy()
    return DataCollector(config=cfg, save_path=str(tmp_path))


class TestQuotaEnforcement:
    """Tests for should_collect quota logic."""

    def test_should_collect_under_quota(self, tmp_path):
        """Fresh collector should allow collection (count 0 < quota 10)."""
        dc = _make_collector(tmp_path)
        assert dc.should_collect("arrows", "analog_1", "0.0") is True

    def test_should_collect_at_quota(self, tmp_path):
        """When counter equals quota, should_collect returns False."""
        dc = _make_collector(tmp_path)
        # Manually set counter to quota
        dc._counters["arrows"]["analog_1"]["0.0"] = 10
        assert dc.should_collect("arrows", "analog_1", "0.0") is False

    def test_should_collect_above_quota(self, tmp_path):
        """When counter exceeds quota (edge case), should_collect returns False."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 15
        assert dc.should_collect("arrows", "analog_1", "0.0") is False

    def test_should_collect_one_below_quota(self, tmp_path):
        """When counter is one below quota, should_collect returns True."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 9
        assert dc.should_collect("arrows", "analog_1", "0.0") is True

    def test_should_collect_different_classes_independent(self, tmp_path):
        """Filling one class to quota doesn't affect other classes."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 10  # at quota
        # Different class on same ROI should still be collectible
        assert dc.should_collect("arrows", "analog_1", "1.3") is True

    def test_should_collect_different_rois_independent(self, tmp_path):
        """Filling one ROI to quota doesn't affect other ROIs."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 10  # at quota
        # Same class on different ROI should still be collectible
        assert dc.should_collect("arrows", "analog_2", "0.0") is True

    def test_should_collect_different_model_types_independent(self, tmp_path):
        """Filling one model type doesn't affect other model types."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 10  # at quota
        # Same ROI+class on different model type should still be collectible
        assert dc.should_collect("digits", "analog_1", "0.0") is True

    def test_should_collect_disabled(self, tmp_path):
        """When collection is disabled, should_collect returns False."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["enabled"] = False
        dc = _make_collector(tmp_path, config=cfg)
        assert dc.should_collect("arrows", "analog_1", "0.0") is False

    def test_get_counts_returns_copy(self, tmp_path):
        """Modifying the returned dict must not affect internal state."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 5

        counts = dc.get_counts()
        # Mutate the returned copy
        counts["arrows"]["analog_1"]["0.0"] = 999

        # Internal state unchanged
        assert dc._counters["arrows"]["analog_1"]["0.0"] == 5

    def test_get_counts_empty(self, tmp_path):
        """Fresh collector returns empty dict for counts."""
        dc = _make_collector(tmp_path)
        assert dc.get_counts() == {}

    def test_reset_clears_counters(self, tmp_path):
        """After reset, all quotas should be available again."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 10
        dc._save_counters()

        dc.reset()

        assert dc.should_collect("arrows", "analog_1", "0.0") is True
        assert dc.get_counts() == {}


class TestCounterPersistence:
    """Tests for counter save/load lifecycle."""

    def test_counters_persist_across_instances(self, tmp_path):
        """Save counters, create new instance, counters should be loaded."""
        dc1 = _make_collector(tmp_path)
        dc1._counters["arrows"]["analog_1"]["0.0"] = 3
        dc1._counters["arrows"]["analog_1"]["1.3"] = 10
        dc1._counters["digits"]["digit_1"]["0"] = 8
        dc1._save_counters()

        # New instance should load persisted counters
        dc2 = _make_collector(tmp_path)
        counts = dc2.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 3
        assert counts["arrows"]["analog_1"]["1.3"] == 10
        assert counts["digits"]["digit_1"]["0"] == 8

    def test_counters_file_is_valid_json(self, tmp_path):
        """Saved file should be valid JSON with expected structure."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 3
        dc._counters["digits"]["digit_1"]["0"] = 8
        dc._save_counters()

        counters_path = tmp_path / ".collection_counts.json"
        assert counters_path.exists()

        with open(counters_path) as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert data["arrows"]["analog_1"]["0.0"] == 3
        assert data["digits"]["digit_1"]["0"] == 8

    def test_corrupted_counters_file_handled(self, tmp_path):
        """Invalid JSON in counters file should result in fresh start."""
        counters_path = tmp_path / ".collection_counts.json"
        counters_path.write_text("{invalid json!!!")

        dc = _make_collector(tmp_path)
        # Should start fresh, not crash
        assert dc.get_counts() == {}
        assert dc.should_collect("arrows", "analog_1", "0.0") is True

    def test_missing_counters_file_starts_fresh(self, tmp_path):
        """Missing counters file should start with empty counters."""
        dc = _make_collector(tmp_path)
        assert dc.get_counts() == {}

    def test_reset_removes_persistence_file(self, tmp_path):
        """After reset, the persistence file should be deleted."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 5
        dc._save_counters()

        counters_path = tmp_path / ".collection_counts.json"
        assert counters_path.exists()

        dc.reset()
        assert not counters_path.exists()

    def test_reset_no_file_does_not_raise(self, tmp_path):
        """Reset when no persistence file exists should not raise."""
        dc = _make_collector(tmp_path)
        dc.reset()  # should not raise

    def test_atomic_write_produces_no_tmp_files(self, tmp_path):
        """After save, no .tmp files should remain."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 5
        dc._save_counters()

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_save_converts_defaultdicts_to_regular_dicts(self, tmp_path):
        """Saved JSON should use regular dicts, not defaultdict representations."""
        dc = _make_collector(tmp_path)
        dc._counters["arrows"]["analog_1"]["0.0"] = 3
        dc._save_counters()

        counters_path = tmp_path / ".collection_counts.json"
        raw_content = counters_path.read_text()
        # defaultdict would serialize as "defaultdict(<class 'int'>, ...)" if not converted
        assert "defaultdict" not in raw_content

    def test_loaded_counters_are_defaultdicts(self, tmp_path):
        """After loading, counters should be defaultdicts for auto-vivification."""
        dc1 = _make_collector(tmp_path)
        dc1._counters["arrows"]["analog_1"]["0.0"] = 3
        dc1._save_counters()

        dc2 = _make_collector(tmp_path)
        # Accessing a new key on loaded counters should work (defaultdict behavior)
        assert dc2._counters["arrows"]["analog_1"]["new_class"] == 0
        assert dc2._counters["new_model"]["new_roi"]["new_class"] == 0


class TestCollect:
    """Tests for the collect() method — image saving, dedup, and counter integration."""

    def test_collect_saves_image_and_increments_counter(self, tmp_path):
        """Save one image, verify file exists in {model_type}/input/, counter = 1."""
        dc = _make_collector(tmp_path)
        image_bytes = b"fake jpeg content"

        result = dc.collect("arrows", "analog_1", "0.0", image_bytes)

        assert result is True
        # Check counter was incremented
        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 1
        # Check file was saved in correct directory
        input_dir = tmp_path / "arrows" / "input"
        assert input_dir.is_dir()
        saved_files = list(input_dir.glob("*.jpg"))
        assert len(saved_files) == 1
        # Filename should contain roi_id and label
        fname = saved_files[0].name
        assert "analog_1" in fname
        assert "_label=0.0" in fname
        # File content should match
        assert saved_files[0].read_bytes() == image_bytes

    def test_collect_respects_quota(self, tmp_path):
        """quota=2, save 3 images, third returns False, counter stays at 2."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["quota_per_class"] = 2
        dc = DataCollector(config=cfg, save_path=str(tmp_path))

        assert dc.collect("arrows", "analog_1", "0.0", b"img1") is True
        assert dc.collect("arrows", "analog_1", "0.0", b"img2") is True
        assert dc.collect("arrows", "analog_1", "0.0", b"img3") is False

        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 2

    @patch("watermeter.data_collector.HashCache")
    @patch("watermeter.data_collector.compute_dhash")
    def test_collect_dedup_blocks_identical_image(self, mock_dhash, mock_cache_cls, tmp_path):
        """dedup_enabled=True, save same image_bytes twice, second returns False."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["dedup_enabled"] = True
        cfg["dedup_threshold"] = 10
        dc = DataCollector(config=cfg, save_path=str(tmp_path))

        # Both calls return the same hash
        mock_dhash.return_value = 0xABCD1234
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        # First call: no duplicate found; second call: duplicate found
        mock_cache.find_near_duplicate.side_effect = [None, "existing_file.jpg"]

        assert dc.collect("arrows", "analog_1", "0.0", b"same_image") is True
        assert dc.collect("arrows", "analog_1", "0.0", b"same_image") is False

        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 1

    @patch("watermeter.data_collector.HashCache")
    @patch("watermeter.data_collector.compute_dhash")
    def test_collect_dedup_allows_different_images(self, mock_dhash, mock_cache_cls, tmp_path):
        """dedup_enabled=True, threshold=0, two different images both save."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["dedup_enabled"] = True
        cfg["dedup_threshold"] = 0
        dc = DataCollector(config=cfg, save_path=str(tmp_path))

        # Return different hashes for different images
        mock_dhash.side_effect = [0x1111, 0x2222]
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        # Neither finds a duplicate
        mock_cache.find_near_duplicate.return_value = None

        assert dc.collect("arrows", "analog_1", "0.0", b"image_1") is True
        assert dc.collect("arrows", "analog_1", "0.0", b"image_2") is True

        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 2

    def test_collect_persists_counter_on_save(self, tmp_path):
        """Save image, create new DataCollector instance, counter survives."""
        cfg = DEFAULT_CONFIG.copy()
        dc1 = DataCollector(config=cfg, save_path=str(tmp_path))

        dc1.collect("arrows", "analog_1", "0.0", b"image data")

        # New instance should load persisted counter
        dc2 = DataCollector(config=cfg, save_path=str(tmp_path))
        counts = dc2.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 1

    def test_collect_creates_directory_structure(self, tmp_path):
        """Verify {model_type}/input/ directory is created."""
        dc = _make_collector(tmp_path)

        dc.collect("digits", "digit_1", "5", b"image data")

        assert (tmp_path / "digits" / "input").is_dir()

    @patch("watermeter.data_collector.compute_dhash", return_value=None)
    def test_collect_with_invalid_image_still_saves(self, mock_dhash, tmp_path):
        """Pass invalid bytes, dedup_enabled=True, should still save (dedup skipped)."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["dedup_enabled"] = True
        cfg["dedup_threshold"] = 10
        dc = DataCollector(config=cfg, save_path=str(tmp_path))

        result = dc.collect("arrows", "analog_1", "0.0", b"not a jpeg")

        assert result is True
        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 1
        # File should be saved even though it's not valid image data
        input_dir = tmp_path / "arrows" / "input"
        saved_files = list(input_dir.glob("*.jpg"))
        assert len(saved_files) == 1

    def test_collect_dedup_disabled_saves_identical(self, tmp_path):
        """dedup_enabled=False, same bytes twice, both save (counter=2)."""
        cfg = DEFAULT_CONFIG.copy()
        cfg["dedup_enabled"] = False
        dc = DataCollector(config=cfg, save_path=str(tmp_path))

        assert dc.collect("arrows", "analog_1", "0.0", b"same_image") is True
        assert dc.collect("arrows", "analog_1", "0.0", b"same_image") is True

        counts = dc.get_counts()
        assert counts["arrows"]["analog_1"]["0.0"] == 2
