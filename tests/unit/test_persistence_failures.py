"""Unit tests for FailureStore (ring-buffered failure history)."""

from watermeter.persistence import FailureStore


def test_empty_store_has_no_failures(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    assert store.last_failure() is None
    assert store.all_failures() == []


def test_record_and_retrieve(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    store.record_failure(
        reason="low_confidence",
        stage="alignment",
        failed_marker=2,
        marker_confidences=[0.7, 0.3],
    )
    last = store.last_failure()
    assert last["reason"] == "low_confidence"
    assert last["stage"] == "alignment"
    assert last["failed_marker"] == 2
    assert last["marker_confidences"] == [0.7, 0.3]
    assert "timestamp" in last


def test_ring_buffer_caps_at_max(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    for i in range(60):
        store.record_failure(reason=f"r{i}")
    all_records = store.all_failures()
    assert len(all_records) == FailureStore.MAX_RECORDS
    # Oldest 10 evicted; r10..r59 remain.
    assert all_records[0]["reason"] == "r10"
    assert all_records[-1]["reason"] == "r59"


def test_persists_across_instances(tmp_path):
    store = FailureStore(tmp_path / "fail.json")
    store.record_failure(reason="transform_failed")
    store2 = FailureStore(tmp_path / "fail.json")
    assert store2.last_failure()["reason"] == "transform_failed"


def test_corrupt_file_resets_safely(tmp_path):
    path = tmp_path / "fail.json"
    path.write_text("not json")
    store = FailureStore(path)
    assert store.all_failures() == []
    store.record_failure(reason="ok")
    assert store.last_failure()["reason"] == "ok"
