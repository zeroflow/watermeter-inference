"""
Persistence module for storing state across restarts.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class StateStore:
    """Simple JSON-based state persistence."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, previous_value: Optional[float], last_update_time: Optional[datetime]) -> None:
        """Save state to disk."""
        try:
            state = {
                "previous_value": previous_value,
                "last_update_time": last_update_time.isoformat() if last_update_time else None,
            }

            # Write to temp file then rename for atomic save (prevents corruption on crash)
            fd, tmp_path = tempfile.mkstemp(dir=self.file_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp_path, self.file_path)
            except BaseException:
                os.unlink(tmp_path)
                raise

            logger.debug(f"State saved to {self.file_path}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def load(self) -> tuple[Optional[float], Optional[datetime]]:
        """Load state from disk."""
        try:
            if not self.file_path.exists():
                logger.info(f"No state file found at {self.file_path}")
                return None, None

            with open(self.file_path, "r") as f:
                state = json.load(f)

            previous_value = state.get("previous_value")
            last_update_str = state.get("last_update_time")
            last_update_time = datetime.fromisoformat(last_update_str) if last_update_str else None

            logger.info(f"State loaded: previous_value={previous_value}, last_update={last_update_time}")
            return previous_value, last_update_time

        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None, None

    def clear(self) -> None:
        """Clear persisted state."""
        try:
            if self.file_path.exists():
                self.file_path.unlink()
                logger.info(f"State cleared from {self.file_path}")
        except Exception as e:
            logger.error(f"Failed to clear state: {e}")


class FailureStore:
    """Ring-buffer of recent pipeline failures, persisted as JSON.

    Schema::

        {
            "records": [
                {
                    "timestamp": iso8601,
                    "reason": str,
                    "stage": "alignment" | "inference",
                    "failed_marker": int | None,
                    "marker_confidences": list[float],
                },
                ...
            ]
        }

    The last ``MAX_RECORDS`` records are retained; older entries are dropped
    on each write.
    """

    MAX_RECORDS = 50

    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def record_failure(
        self,
        *,
        reason: str,
        stage: str = "alignment",
        failed_marker: Optional[int] = None,
        marker_confidences: Optional[List[float]] = None,
    ) -> None:
        """Append a failure record and persist atomically (ring-buffered)."""
        try:
            records = self._load()["records"]
            records.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "reason": reason,
                    "stage": stage,
                    "failed_marker": failed_marker,
                    "marker_confidences": list(marker_confidences or []),
                }
            )
            records = records[-self.MAX_RECORDS :]
            self._save({"records": records})
        except Exception as e:  # never let persistence failure break the pipeline
            logger.error(f"Failed to record failure: {e}")

    def last_failure(self) -> Optional[dict]:
        """Return the most recent failure record, or None if empty."""
        records = self._load()["records"]
        return records[-1] if records else None

    def all_failures(self) -> List[dict]:
        """Return a copy of all currently retained failure records."""
        return list(self._load()["records"])

    def clear(self) -> None:
        """Delete the on-disk failure history."""
        try:
            if self.file_path.exists():
                self.file_path.unlink()
        except Exception as e:
            logger.error(f"Failed to clear failure store: {e}")

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {"records": []}
        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "records" not in data:
                return {"records": []}
            if not isinstance(data["records"], list):
                return {"records": []}
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"FailureStore: failed to load {self.file_path}, resetting history: {e}")
            return {"records": []}

    def _save(self, data: dict) -> None:
        # Atomic write: tempfile in same directory + os.replace.
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
