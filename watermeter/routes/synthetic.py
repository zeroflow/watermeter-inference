"""Synthetic data generation routes."""

import logging
import threading
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ..synthetic_generator import SyntheticGenerator
from ..training_manager import get_training_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level state for background generation
_generation_lock = threading.Lock()
_generation_status = {
    "running": False,
    "job_id": None,
    "progress": 0,
    "total": 0,
    "message": "Idle",
    "type": None,
}


class SyntheticConfig(BaseModel):
    type: str
    count_per_class: int = 500
    seed: int = 42

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        if v not in ("digits", "arrows", "both"):
            raise ValueError("type must be 'digits', 'arrows', or 'both'")
        return v

    @field_validator("count_per_class")
    @classmethod
    def validate_count(cls, v):
        if v < 1 or v > 10000:
            raise ValueError("count_per_class must be between 1 and 10000")
        return v


def _run_generation(config: SyntheticConfig, job_id: str):
    """Background thread target for synthetic data generation."""
    global _generation_status
    try:
        gen = SyntheticGenerator(base_dir="/training")

        def on_progress(current, total, message):
            _generation_status.update(
                {
                    "progress": current,
                    "total": total,
                    "message": message,
                }
            )

        stats = gen.generate(
            type=config.type,
            count_per_class=config.count_per_class,
            seed=config.seed,
            progress_callback=on_progress,
        )
        _generation_status.update(
            {
                "running": False,
                "message": f"Complete: {stats}",
            }
        )
    except Exception as e:
        logger.exception("Synthetic generation failed")
        _generation_status.update(
            {
                "running": False,
                "message": f"Error: {e}",
            }
        )


@router.post(
    "/api/synthetic/generate",
    tags=["Synthetic Data"],
    summary="Start synthetic data generation",
)
async def generate_synthetic(config: SyntheticConfig):
    """Start background synthetic data generation."""
    global _generation_status

    if _generation_status["running"]:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "Generation already running"},
        )

    tm = get_training_manager()
    if tm.active_training_job and tm.active_training_job.status.value == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "Training is running"},
        )

    job_id = f"synth_{uuid.uuid4().hex[:8]}"
    _generation_status.update(
        {
            "running": True,
            "job_id": job_id,
            "progress": 0,
            "total": 0,
            "message": "Starting...",
            "type": config.type,
        }
    )

    thread = threading.Thread(target=_run_generation, args=(config, job_id), daemon=True)
    thread.start()

    return JSONResponse(content={"success": True, "job_id": job_id})


@router.get(
    "/api/synthetic/status",
    tags=["Synthetic Data"],
    summary="Get synthetic generation status",
)
async def get_synthetic_status():
    """Get the current status of synthetic data generation."""
    return JSONResponse(content=_generation_status)


@router.delete(
    "/api/synthetic/{type}",
    tags=["Synthetic Data"],
    summary="Delete synthetic images",
)
async def delete_synthetic(type: str):
    """Delete all synthetic images for the specified type."""
    if type not in ("digits", "arrows", "both"):
        raise HTTPException(status_code=400, detail="type must be 'digits', 'arrows', or 'both'")

    if _generation_status["running"]:
        raise HTTPException(status_code=409, detail="Cannot delete while generation is running")

    gen = SyntheticGenerator(base_dir="/training")
    deleted = gen.delete_synthetic(type=type)

    return JSONResponse(content={"success": True, "deleted": deleted, "type": type})
