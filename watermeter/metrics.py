"""Pipeline observability metrics, persisted as JSON.

No new dependencies (no prometheus_client). Designed for a single-instance
service where the dashboard reads a snapshot via /api/metrics every few
seconds. All writes are atomic (tmp + rename).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class PipelineMetrics:
    """In-process pipeline metrics with JSON-on-disk persistence.

    Counters: readings by status, marker-detection failures per marker.
    Sketches: rolling window of last N marker-match confidences per marker.
    Events:   24h ring of (timestamp, status) used for failure-rate windows.
    """

    ROLLING_CONFIDENCE_WINDOW = 100  # last 100 samples per marker
    EVENT_RETENTION_HOURS = 25  # keep 25h of events for 24h-rate calculations

    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._load()
        self._readings_total: Dict[str, int] = data.get(
            "readings_total", {"ok": 0, "alignment_failed": 0, "inference_failed": 0}
        )
        # Defensive: ensure all expected keys exist even if old payload was partial.
        for key in ("ok", "alignment_failed", "inference_failed"):
            self._readings_total.setdefault(key, 0)

        self._marker_failures_total: Dict[str, int] = data.get("marker_detection_failures_total", {"M1": 0, "M2": 0})
        for key in ("M1", "M2"):
            self._marker_failures_total.setdefault(key, 0)

        persisted_samples = data.get("confidence_samples", {})
        self._confidence_samples: Dict[str, Deque[float]] = {
            "M1": deque(persisted_samples.get("M1", []), maxlen=self.ROLLING_CONFIDENCE_WINDOW),
            "M2": deque(persisted_samples.get("M2", []), maxlen=self.ROLLING_CONFIDENCE_WINDOW),
        }
        # Events: list of {ts: iso, status: str}
        self._events: List[dict] = data.get("events", []) or []
        self._prune_old_events()

    # --- Recorders ---
    def record_reading(self, *, status: str) -> None:
        """Increment the status counter and append an event. Unknown statuses are silently ignored."""
        if status not in self._readings_total:
            return
        self._readings_total[status] += 1
        self._record_event_at(datetime.now(), status=status)
        self._save()

    def record_marker_failure(self, *, marker_index: int) -> None:
        """Increment the per-marker failure counter. Unknown indices silently ignored."""
        key = f"M{marker_index}"
        if key not in self._marker_failures_total:
            return
        self._marker_failures_total[key] += 1
        self._save()

    def record_marker_confidence(self, *, marker_index: int, confidence: float) -> None:
        """Append a confidence sample to the rolling window. Unknown indices silently ignored."""
        key = f"M{marker_index}"
        if key not in self._confidence_samples:
            return
        self._confidence_samples[key].append(float(confidence))
        self._save()

    def _record_event_at(self, ts: datetime, *, status: str) -> None:
        """Internal: append an event with a specific timestamp (used by tests for backfill)."""
        self._events.append({"ts": ts.isoformat(), "status": status})
        self._prune_old_events()

    # --- Snapshot ---
    def snapshot(self) -> dict:
        confidence_summary: Dict[str, dict] = {}
        for key, samples in self._confidence_samples.items():
            samples_list = list(samples)
            confidence_summary[key] = {
                "count": len(samples_list),
                "median": statistics.median(samples_list) if samples_list else None,
                "min": min(samples_list) if samples_list else None,
                "max": max(samples_list) if samples_list else None,
                "mean": statistics.fmean(samples_list) if samples_list else None,
            }
        return {
            "readings_total": dict(self._readings_total),
            "marker_detection_failures_total": dict(self._marker_failures_total),
            "marker_match_confidence": confidence_summary,
            "failure_rate_1h": self._failure_rate(timedelta(hours=1)),
            "failure_rate_24h": self._failure_rate(timedelta(hours=24)),
            "snapshot_timestamp": datetime.now().isoformat(),
        }

    # --- Internal ---
    def _failure_rate(self, window: timedelta) -> Optional[float]:
        cutoff = datetime.now() - window
        in_window = []
        for e in self._events:
            try:
                if datetime.fromisoformat(e["ts"]) >= cutoff:
                    in_window.append(e)
            except (KeyError, TypeError, ValueError):
                continue
        if not in_window:
            return 0.0
        failures = sum(1 for e in in_window if e.get("status") != "ok")
        return failures / len(in_window)

    def _prune_old_events(self) -> None:
        cutoff = datetime.now() - timedelta(hours=self.EVENT_RETENTION_HOURS)
        kept: List[dict] = []
        for e in self._events:
            try:
                if datetime.fromisoformat(e["ts"]) >= cutoff:
                    kept.append(e)
            except (KeyError, TypeError, ValueError):
                # Drop malformed events.
                continue
        self._events = kept

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"PipelineMetrics: failed to load {self.file_path}, resetting: {e}")
            return {}

    def _save(self) -> None:
        data = {
            "readings_total": self._readings_total,
            "marker_detection_failures_total": self._marker_failures_total,
            "confidence_samples": {k: list(v) for k, v in self._confidence_samples.items()},
            "events": self._events,
        }
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self.file_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, self.file_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:  # never let persistence failure break the pipeline
            logger.error(f"PipelineMetrics: failed to save {self.file_path}: {e}")
