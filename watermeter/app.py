"""
FastAPI Web Application for Water Meter Dashboard

This module creates the FastAPI app and includes all route modules.
Route handlers are organized in watermeter/routes/.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .image_hash import purge_duplicates
from .watermeter_service import get_service

logger = logging.getLogger(__name__)


def safe_subpath(base: Path, *parts: str) -> Path:
    """Join path parts to base and verify the result stays inside base (prevents path traversal)."""
    resolved = (base / Path(*parts)).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError(f"Path traversal detected: {'/'.join(parts)}")
    return resolved


# prevent fire-and-forget tasks from being garbage-collected
_background_tasks: set = set()


def _create_background_task(coro):
    """Create an asyncio task and prevent it from being garbage-collected."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI app."""
    # Startup
    logger.info("Starting Water Meter Dashboard...")
    service = get_service()
    trigger_mode = service.trigger_mode

    # Start MQTT in all modes (needed for publishing + reset even in cyclic mode)
    service.start_mqtt()

    # Start cyclic loop if configured
    if trigger_mode in ('cyclic', 'both'):
        service.start_cyclic_loop()

    logger.info(f"Water Meter Dashboard started (trigger_mode={trigger_mode})")

    # Purge duplicate images from input folders on startup
    lc_config = service.config.get('low_confidence', {})
    if lc_config.get('dedup_enabled', True):
        training_path = Path(lc_config.get('save_path', '/training'))
        threshold = lc_config.get('dedup_threshold', 10)
        scope = lc_config.get('dedup_scope', 'input+ground_truth')
        for model_type in ('digits', 'arrows'):
            input_dir = training_path / model_type / 'input'
            gt_dirs = None
            if scope == 'input+ground_truth':
                gt_base = training_path / model_type / 'ground_truth'
                if gt_base.is_dir():
                    gt_dirs = [d for d in gt_base.iterdir() if d.is_dir()]
            purge_duplicates(input_dir, threshold, gt_dirs)

    # Initial reading on startup
    logger.info("Triggering initial reading...")
    _create_background_task(service.process_reading())

    yield

    # Shutdown
    logger.info("Shutting down Water Meter Dashboard...")
    service = get_service()
    service.stop_cyclic_loop()
    service.stop_mqtt()
    logger.info("Water Meter Dashboard stopped")


# Define OpenAPI tags for grouping endpoints
tags_metadata = [
    {
        "name": "Pages",
        "description": "HTML page routes for the web interface",
    },
    {
        "name": "Status & Reading",
        "description": "Meter reading status, manual triggering, and value management",
    },
    {
        "name": "Configuration",
        "description": "Configuration file management (read, edit, validate)",
    },
    {
        "name": "ROI Setup",
        "description": "Region of Interest configuration (rotation, markers, digits, analog dials)",
    },
    {
        "name": "Labeling",
        "description": "Manual labeling interface for training data",
    },
    {
        "name": "Training",
        "description": "Model training job management and queue operations",
    },
    {
        "name": "Models",
        "description": "Model management, activation, archival, and deletion",
    },
    {
        "name": "Benchmarking",
        "description": "Model benchmarking against ground truth datasets",
    },
    {
        "name": "Training Data",
        "description": "Training data statistics, deduplication, pruning, and mislabel detection",
    },
]

# Create FastAPI app
app = FastAPI(
    title="AI Water Meter API",
    description="OpenVINO-based AI water meter reading system with automated training, "
                "benchmarking, and live dashboard. Supports both analog dial and digital "
                "display recognition with active learning workflows.",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata
)

# Mount static files relative to this package
_pkg_dir = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_pkg_dir / "static")), name="static")

# Include route modules (imported here to avoid circular imports at module level;
# safe_subpath and other shared objects are defined above these imports)
from .routes.pages import router as pages_router      # noqa: E402
from .routes.service import router as service_router   # noqa: E402
from .routes.config import router as config_router     # noqa: E402
from .routes.roi import router as roi_router           # noqa: E402
from .routes.label import router as label_router       # noqa: E402
from .routes.training import router as training_router # noqa: E402
from .routes.models import router as models_router     # noqa: E402

app.include_router(pages_router)
app.include_router(service_router)
app.include_router(config_router)
app.include_router(roi_router)
app.include_router(label_router)
app.include_router(training_router)
app.include_router(models_router)


def main():
    """Run the application directly (e.g. python -m watermeter)."""
    import uvicorn
    import yaml as _yaml

    # Load config for port
    with open("config.yaml", 'r') as f:
        _config = _yaml.safe_load(f)

    host = _config['dashboard']['host']
    port = _config['dashboard']['port']

    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run("watermeter.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
