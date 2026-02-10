"""Unit tests for persistence.py - StateStore."""

import json
from datetime import datetime

import pytest

from persistence import StateStore


class TestStateStore:
    """Tests for StateStore save/load/clear cycle."""

    def test_save_and_load(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        now = datetime(2025, 6, 15, 12, 30, 0)
        store.save(123.456, now)

        value, ts = store.load()
        assert value == 123.456
        assert ts == now

    def test_load_no_file(self, tmp_path):
        store = StateStore(str(tmp_path / "nonexistent.json"))
        value, ts = store.load()
        assert value is None
        assert ts is None

    def test_save_none_values(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(None, None)

        value, ts = store.load()
        assert value is None
        assert ts is None

    def test_clear(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(42.0, datetime.now())
        store.clear()

        value, ts = store.load()
        assert value is None
        assert ts is None

    def test_clear_no_file(self, tmp_path):
        """Clear on nonexistent file should not raise."""
        store = StateStore(str(tmp_path / "nope.json"))
        store.clear()  # should not raise

    def test_atomic_write(self, tmp_path):
        """Verify save uses atomic write (no .tmp files left behind)."""
        store = StateStore(str(tmp_path / "state.json"))
        store.save(1.0, datetime.now())

        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "state.json"

    def test_corrupted_file_returns_none(self, tmp_path):
        """Loading a corrupted JSON file should return (None, None)."""
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json {{{")

        store = StateStore(str(state_file))
        value, ts = store.load()
        assert value is None
        assert ts is None

    def test_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "state.json"
        store = StateStore(str(deep_path))
        store.save(1.0, datetime.now())

        value, ts = store.load()
        assert value == 1.0

    def test_round_trip_preserves_precision(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(0.123456789, datetime(2025, 1, 1, 0, 0, 0, 123456))

        value, ts = store.load()
        assert value == 0.123456789
        assert ts.microsecond == 123456

    def test_overwrite_existing(self, tmp_path):
        store = StateStore(str(tmp_path / "state.json"))
        store.save(1.0, datetime(2025, 1, 1))
        store.save(2.0, datetime(2025, 6, 1))

        value, ts = store.load()
        assert value == 2.0
        assert ts == datetime(2025, 6, 1)
