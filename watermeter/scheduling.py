"""Scheduling manager for cyclic and periodic tasks.

Manages two async background loops:
- Cyclic loop: triggers reading processing at a configured interval
- Stats loop: publishes training statistics periodically
"""

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SchedulingManager:
    """Manages cyclic processing and periodic stats publishing.

    This class handles two independent background tasks:
    1. Cyclic loop: calls process_fn at cyclic_interval
    2. Stats loop: calls stats_fn every 5 minutes

    The callbacks are passed in the constructor to avoid tight coupling
    with WatermeterService.
    """

    def __init__(
        self,
        cyclic_interval: int,
        process_fn: Callable,
        stats_fn: Callable,
    ):
        """Initialize the scheduling manager.

        Args:
            cyclic_interval: Interval in seconds for cyclic loop
            process_fn: Async callback for process_reading()
            stats_fn: Callback for publish_training_stats()
        """
        self.cyclic_interval = cyclic_interval
        self._process_fn = process_fn  # callback: async process_reading()
        self._stats_fn = stats_fn      # callback: publish_training_stats()
        self._cyclic_task: Optional[asyncio.Task] = None
        self._stats_task: Optional[asyncio.Task] = None

    async def _cyclic_loop(self):
        """Periodically trigger process_reading at the configured interval."""
        logger.info(f"Cyclic trigger loop started (interval={self.cyclic_interval}s)")
        try:
            while True:
                await asyncio.sleep(self.cyclic_interval)
                logger.info("Cyclic trigger — starting reading")
                await self._process_fn()
        except asyncio.CancelledError:
            logger.info("Cyclic trigger loop cancelled")

    def start_cyclic_loop(self):
        """Start the cyclic trigger background task."""
        if self._cyclic_task is not None:
            logger.warning("Cyclic loop already running")
            return
        self._cyclic_task = asyncio.create_task(self._cyclic_loop())
        logger.info("Cyclic trigger loop task created")

    def stop_cyclic_loop(self):
        """Cancel the cyclic trigger background task."""
        if self._cyclic_task is not None:
            self._cyclic_task.cancel()
            self._cyclic_task = None
            logger.info("Cyclic trigger loop stopped")

    async def _stats_loop(self) -> None:
        """Periodically publish training data stats to HA (every 5 minutes)."""
        logger.info("Training stats publish loop started (interval=300s)")
        try:
            while True:
                await asyncio.sleep(300)
                try:
                    await asyncio.get_running_loop().run_in_executor(None, self._stats_fn)
                except Exception:
                    logger.exception("Error publishing training stats")
        except asyncio.CancelledError:
            logger.info("Training stats publish loop cancelled")

    def start_stats_loop(self) -> None:
        """Start the periodic training stats background task."""
        if self._stats_task is not None:
            logger.warning("Stats loop already running")
            return
        self._stats_task = asyncio.create_task(self._stats_loop())
        logger.info("Training stats loop task created")

    def stop_stats_loop(self) -> None:
        """Cancel the periodic training stats background task."""
        if self._stats_task is not None:
            self._stats_task.cancel()
            self._stats_task = None
            logger.info("Training stats loop stopped")
