"""MeterState — pure data container for WatermeterService instance variables.

Groups the scattered state fields that were previously set directly on
WatermeterService into a single object. No business logic lives here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional


class MeterState:
    """Pure data container holding per-reading state for the meter service."""

    def __init__(self, ha_publish_enabled: bool = False) -> None:
        self.previous_value: Optional[float] = None
        self.last_update_time: Optional[datetime] = None
        self.consecutive_rejections: int = 0
        self.max_consecutive_rejections: int = 5
        self.leak_warning: bool = False
        self.ha_publish_enabled: bool = ha_publish_enabled
        self.current_state: Dict = {
            "total_value": None,
            "unit": "m³",
            "last_update": None,
            "status": "idle",
            "warnings": [],
            "predictions": [],
            "processing": False,
            "ha_publish_enabled": ha_publish_enabled,
            "leak_warning": False,
            "last_published_value": None,
            "last_published_timestamp": None,
            "last_rejected_value": None,
            "last_rejected_timestamp": None,
            "last_rejected_reasons": [],
        }

    def reset(self) -> None:
        """Clear published/rejected tracking fields and reset counters."""
        self.previous_value = None
        self.last_update_time = None
        self.consecutive_rejections = 0
        self.leak_warning = False
        self.current_state["leak_warning"] = False
        self.current_state["last_published_value"] = None
        self.current_state["last_published_timestamp"] = None
        self.current_state["last_rejected_value"] = None
        self.current_state["last_rejected_timestamp"] = None
        self.current_state["last_rejected_reasons"] = []
