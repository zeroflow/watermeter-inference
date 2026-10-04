"""Arrow calibration routes (arrows_mode: calibrated)."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import watermeter_service
from ..arrow_calibration import CalibrationError, calibrate_from_archive, save_calibration
from ..calibrated_arrows import calibration_path
from ..inference import get_inference_service

logger = logging.getLogger(__name__)

router = APIRouter()

_calibration_lock = asyncio.Lock()


class CalibrateRequest(BaseModel):
    """Optional parameters for a calibration run."""

    max_frames: int = Field(300, ge=20, le=2000, description="Archive frames to use, spread over the archive")


@router.post(
    "/api/arrows/calibrate",
    tags=["Models"],
    summary="Calibrate arrow dials",
    description=(
        "Compute the per-dial geometry for arrows_mode 'calibrated' (scale ticks, needle pivot incl. "
        "parallax, offsets) from the raw-image archive (alignment.archive_raw_images). Writes the "
        "calibration file and hot-reloads when arrows_mode is 'calibrated'. Takes about a minute."
    ),
)
async def calibrate_arrows(request: Optional[CalibrateRequest] = None):
    """Run the calibration in a worker thread and return its report."""
    if _calibration_lock.locked():
        return JSONResponse({"success": False, "message": "Calibration already running"}, status_code=409)
    async with _calibration_lock:
        config = watermeter_service.get_service().config
        try:
            path = calibration_path(config)
        except ValueError as e:
            return JSONResponse({"success": False, "message": str(e)}, status_code=400)
        archive_dir = Path(config.get("alignment", {}).get("archive_dir", "/data/raw_archive"))
        max_frames = (request or CalibrateRequest()).max_frames
        try:
            dials, report = await asyncio.to_thread(calibrate_from_archive, config, archive_dir, max_frames)
        except CalibrationError as e:
            logger.warning(f"Arrow calibration failed: {e}")
            return JSONResponse({"success": False, "message": str(e)}, status_code=422)
        except Exception as e:
            logger.error(f"Arrow calibration error: {e}")
            return JSONResponse({"success": False, "message": f"Error: {e}"}, status_code=500)

        save_calibration(path, dials, report)
        reloaded = config.get("inference", {}).get("arrows_mode") == "calibrated"
        if reloaded:
            get_inference_service().reload_models(config)
        logger.info(f"Arrow calibration written to {path} ({sorted(dials)}), reloaded={reloaded}")
        return JSONResponse({"success": True, "calibration_file": str(path), "reloaded": reloaded, "report": report})


@router.get(
    "/api/arrows/calibration",
    tags=["Models"],
    summary="Current arrow calibration",
    description="Return the stored arrow calibration (geometry per dial plus the report of the run).",
)
async def get_arrow_calibration():
    """Return the calibration file contents, or 404 if none was computed yet."""
    try:
        path = calibration_path(watermeter_service.get_service().config)
    except ValueError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)
    if not path.exists():
        return JSONResponse({"success": False, "message": f"No calibration at {path}"}, status_code=404)
    try:
        return JSONResponse({"success": True, "calibration": json.loads(path.read_text())})
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Unreadable calibration: {e}"}, status_code=500)
