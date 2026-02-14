"""Training and benchmark routes."""

import logging
from typing import List

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..training_manager import get_training_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class TrainingConfig(BaseModel):
    """Request model for training configuration."""
    model_type: str  # "digits" or "arrows"
    architecture: str  # e.g., "resnext50_32x4d"
    resolution: int  # e.g., 128
    seeds: List[int]  # e.g., [42, 67, 69]
    epochs: int = 20
    batch_size: int = 16
    step_size: float = 1.0  # For arrows only
    notes: str = ""
    auto_benchmark: bool = True


@router.get("/api/training/status")
async def get_training_status():
    """Get the status of the active training job, benchmark, and queue."""
    training_mgr = get_training_manager()
    training_status = training_mgr.get_training_status()
    benchmark_status = training_mgr.get_benchmark_status()
    queue = training_mgr.get_queue()

    return JSONResponse({
        "training": training_status,
        "benchmark": benchmark_status,
        "queue": queue,
        "auto_benchmark_pending": len(training_mgr._auto_benchmark_pending)
    })


@router.post("/api/training/start")
async def start_training(config: TrainingConfig):
    """Start a new training job or queue it if one is already running."""
    try:
        training_mgr = get_training_manager()
        result = training_mgr.start_training(config.dict())

        return JSONResponse({
            "success": True,
            "job_id": result['job_id'],
            "queued": result['queued'],
            "queue_position": result['queue_position'],
            "message": "Training queued" if result['queued'] else "Training started successfully"
        })

    except Exception as e:
        logger.error(f"Error starting training: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.post("/api/training/cancel")
async def cancel_training(job_id: str, clear_queue: bool = True):
    """Cancel a running training job and optionally clear the queue."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.cancel_training(job_id, clear_queue=clear_queue)

        if success:
            return JSONResponse({
                "success": True,
                "message": "Training cancellation requested" + (" (queue cleared)" if clear_queue else "")
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Training job not found or already completed"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error cancelling training: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.delete("/api/training/queue/{index}")
async def remove_from_queue(index: int):
    """Remove a specific item from the training queue."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.remove_from_queue(index)
        if success:
            return JSONResponse({"success": True, "message": "Removed from queue"})
        else:
            return JSONResponse({"success": False, "message": "Invalid queue index"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.delete("/api/training/queue")
async def clear_queue():
    """Clear the entire training queue."""
    try:
        training_mgr = get_training_manager()
        count = training_mgr.clear_queue()
        return JSONResponse({"success": True, "message": f"Removed {count} items from queue"})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.post("/api/benchmark/cancel")
async def cancel_benchmark(job_id: str):
    """Cancel a running benchmark job."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.cancel_benchmark(job_id)

        if success:
            return JSONResponse({
                "success": True,
                "message": "Benchmark cancellation requested"
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Benchmark job not found or already completed"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error cancelling benchmark: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/training/logs/{job_id}")
async def get_training_logs(job_id: str):
    """Get logs for a specific training job."""
    try:
        training_mgr = get_training_manager()
        logs = training_mgr.get_job_logs(job_id)

        return JSONResponse({
            "success": True,
            "logs": logs
        })

    except Exception as e:
        logger.error(f"Error getting training logs: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/training/progress/{job_id}")
async def get_training_progress(job_id: str):
    """Get progress for a specific training job."""
    try:
        training_mgr = get_training_manager()
        status = training_mgr.get_training_status()

        if status and status['job_id'] == job_id:
            return JSONResponse({
                "success": True,
                "progress": status['progress']
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Training job not found"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error getting training progress: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)
