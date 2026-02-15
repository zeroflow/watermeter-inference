"""Core service routes: status, trigger, reset, health, training submission."""

import asyncio
import base64
from datetime import datetime
import logging
import math
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import watermeter_service
from ..app import safe_subpath

logger = logging.getLogger(__name__)

router = APIRouter()

# prevent fire-and-forget tasks from being garbage-collected
_background_tasks: set = set()


def _create_background_task(coro):
    """Create an asyncio task and prevent it from being garbage-collected."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


class TrainingSubmission(BaseModel):
    """Request model for training submissions."""
    id: str
    image_base64: str
    model: str
    next_image_base64: str = None  # Optional: next dial image for annotation help


class SetValueRequest(BaseModel):
    """Request model for manually setting the meter value (BL-15)."""
    value: float


@router.get(
    "/api/status",
    tags=["Status & Reading"],
    summary="Get meter status",
    description="Get current meter reading, processing state, and system status as JSON"
)
async def get_status():
    """Get current status as JSON."""
    service = watermeter_service.get_service()
    state = dict(service.current_state)
    state.pop("request", None)  # Remove if leaked from TemplateResponse context
    return JSONResponse(state)


@router.post(
    "/api/trigger",
    tags=["Status & Reading"],
    summary="Trigger reading",
    description="Manually trigger a new meter reading cycle"
)
async def trigger_reading():
    """Manually trigger a new reading."""
    service = watermeter_service.get_service()

    if service.current_state['processing']:
        return JSONResponse(
            {"message": "Processing already in progress"},
            status_code=409
        )

    # Start processing in background
    _create_background_task(service.process_reading())

    return JSONResponse({"message": "Reading triggered successfully"})


@router.post(
    "/api/reset",
    tags=["Status & Reading"],
    summary="Reset previous value",
    description="Reset the stored previous meter value used for change detection"
)
async def reset_previous_value():
    """Reset the previous value."""
    service = watermeter_service.get_service()
    service.reset_previous_value()
    return JSONResponse({"message": "Previous value reset successfully"})


@router.post(
    "/api/set-value",
    tags=["Status & Reading"],
    summary="Set meter value manually",
    description="Manually set the meter value (e.g., after physical meter replacement or correction)"
)
async def set_value(request: SetValueRequest):
    """Manually set the meter value (BL-15)."""
    if request.value < 0:
        return JSONResponse(
            {"success": False, "message": "Value must be >= 0"},
            status_code=400
        )
    if math.isnan(request.value) or math.isinf(request.value):
        return JSONResponse(
            {"success": False, "message": "Value must be a finite number"},
            status_code=400
        )
    if request.value >= 1_000_000:
        return JSONResponse(
            {"success": False, "message": "Value must be less than 1,000,000"},
            status_code=400
        )

    service = watermeter_service.get_service()

    if service.current_state.get('processing'):
        return JSONResponse(
            {"success": False, "message": "Cannot set value while a reading is in progress. Please try again."},
            status_code=409
        )

    mqtt_published = service.set_manual_value(request.value)
    return JSONResponse({
        "success": True,
        "message": f"Meter set to {request.value:.4f} m\u00b3",
        "value": round(request.value, 4),
        "mqtt_published": mqtt_published
    })


@router.post(
    "/api/toggle-ha-publish",
    tags=["Status & Reading"],
    summary="Toggle Home Assistant publishing",
    description="Enable or disable MQTT publishing to Home Assistant"
)
async def toggle_ha_publish(enabled: bool):
    """Toggle Home Assistant MQTT publishing."""
    service = watermeter_service.get_service()
    service.toggle_ha_publish(enabled)
    status = "enabled" if enabled else "disabled"
    return JSONResponse({"message": f"Home Assistant publishing {status}"})


@router.post(
    "/api/submit-training",
    tags=["Status & Reading"],
    summary="Submit image for training",
    description="Submit a low-confidence reading image to the training input folder for later labeling"
)
async def submit_for_training(submission: TrainingSubmission):
    """Submit an image for manual training/correction."""
    try:
        service = watermeter_service.get_service()

        # Get save path from config
        save_path = service.config.get('low_confidence', {}).get('save_path', '/training/')

        # Create directory structure: save_path/{model}/
        model_dir = safe_subpath(Path(save_path), submission.model, 'input')
        model_dir.mkdir(parents=True, exist_ok=True)

        # Decode base64 image
        image_data = base64.b64decode(submission.image_base64)

        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{submission.id}_{timestamp}.jpg"
        filepath = model_dir / filename

        # Save image
        with open(filepath, 'wb') as f:
            f.write(image_data)

        logger.info(f"Training image saved: {filepath}")

        # Save next dial reference image if provided (for annotation help)
        if submission.next_image_base64:
            next_image_data = base64.b64decode(submission.next_image_base64)
            next_filename = f"{submission.id}_{timestamp}_next.jpg"
            next_filepath = model_dir / next_filename
            with open(next_filepath, 'wb') as f:
                f.write(next_image_data)
            logger.info(f"Next dial reference image saved: {next_filepath}")

        return JSONResponse({
            "success": True,
            "message": f"Image saved: {filename}",
            "path": str(filepath)
        })

    except Exception as e:
        logger.error(f"Error submitting for training: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error saving: {str(e)}"
        }, status_code=500)


@router.get(
    "/api/confirmation/status",
    tags=["Status & Reading"],
    summary="Get confirmation status",
    description="Get the current pending confirmation status for critical reading changes"
)
async def confirmation_status():
    """Get the current pending confirmation status, if any."""
    service = watermeter_service.get_service()
    pending = service.get_confirmation_status()
    return JSONResponse({
        "pending": pending is not None,
        "details": pending,
    })


@router.get(
    "/health",
    tags=["Status & Reading"],
    summary="Health check",
    description="Health check endpoint for monitoring system availability"
)
def health():
    """Health check endpoint."""
    service = watermeter_service.get_service()
    return {
        "status": "ok",
        "mqtt_connected": service.mqtt_client.is_connected() if service.mqtt_client else False,
        "last_update": service.current_state.get('last_update'),
        "processing": service.current_state['processing']
    }
