"""Unit tests for the pipeline metrics module."""

from datetime import datetime, timedelta

import pytest

from watermeter.metrics import PipelineMetrics


def test_counters_start_at_zero(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    snap = m.snapshot()
    assert snap["readings_total"] == {"ok": 0, "alignment_failed": 0, "inference_failed": 0}
    assert snap["marker_detection_failures_total"] == {"M1": 0, "M2": 0}


def test_record_reading_increments_status_counter(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    m.record_reading(status="ok")
    m.record_reading(status="ok")
    m.record_reading(status="alignment_failed")
    snap = m.snapshot()
    assert snap["readings_total"]["ok"] == 2
    assert snap["readings_total"]["alignment_failed"] == 1


def test_record_marker_failure_per_marker(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    m.record_marker_failure(marker_index=1)
    m.record_marker_failure(marker_index=2)
    m.record_marker_failure(marker_index=1)
    snap = m.snapshot()
    assert snap["marker_detection_failures_total"]["M1"] == 2
    assert snap["marker_detection_failures_total"]["M2"] == 1


def test_record_marker_confidence_rolling_median(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    for c in [0.7, 0.8, 0.9, 0.95, 0.6]:
        m.record_marker_confidence(marker_index=1, confidence=c)
    snap = m.snapshot()
    assert snap["marker_match_confidence"]["M1"]["count"] == 5
    assert snap["marker_match_confidence"]["M1"]["median"] == 0.8


def test_failure_rate_window(tmp_path):
    m = PipelineMetrics(tmp_path / "metrics.json")
    now = datetime.now()
    # 4 successes + 1 failure in the last hour
    m._record_event_at(now - timedelta(minutes=10), status="ok")
    m._record_event_at(now - timedelta(minutes=20), status="ok")
    m._record_event_at(now - timedelta(minutes=30), status="ok")
    m._record_event_at(now - timedelta(minutes=40), status="ok")
    m._record_event_at(now - timedelta(minutes=50), status="alignment_failed")
    # Old events outside the 1h window must not count toward 1h rate
    m._record_event_at(now - timedelta(hours=3), status="alignment_failed")
    snap = m.snapshot()
    assert snap["failure_rate_1h"] == pytest.approx(1 / 5)
    assert snap["failure_rate_24h"] == pytest.approx(2 / 6)


def test_persist_across_instances(tmp_path):
    path = tmp_path / "metrics.json"
    m = PipelineMetrics(path)
    m.record_reading(status="ok")
    m.record_marker_confidence(marker_index=2, confidence=0.91)
    m2 = PipelineMetrics(path)
    snap = m2.snapshot()
    assert snap["readings_total"]["ok"] == 1
    assert snap["marker_match_confidence"]["M2"]["count"] == 1
