"""
Persistence module for storing state across restarts.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
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
