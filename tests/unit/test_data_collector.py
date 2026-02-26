"""Unit tests for DataCollector — quota counter logic and persistence."""

import json

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
