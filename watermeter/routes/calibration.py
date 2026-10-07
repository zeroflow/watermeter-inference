"""Arrow calibration routes (arrows_mode: calibrated)."""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .. import watermeter_service
from ..arrow_calibration import (
    CalibrationError,
    DialCalibration,
    _same_roi,
    calibrate_from_archive,
    decode_image,
    encode_jpeg,
    render_overlay,
    save_calibration,
)
from ..calibrated_arrows import analog_rois_from_config, calibration_path
from ..calibration_session import ROI_ID_RE, SessionBusy, SessionError
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


# --- calibration tab (collection session) --------------------------------------------------------


class CollectRequest(BaseModel):
    """Collection target: hours or aligned frames."""

    type: str = Field(..., description="'hours' or 'frames'")
    value: float = Field(..., description="target duration in hours or number of aligned frames")


class PivotRequest(BaseModel):
    """Manual needle pivot in crop pixel coordinates."""

    x: float
    y: float


def _session_call(fn, *args):
    """Run a session method and map its errors to HTTP status codes."""
    try:
        fn(*args)
    except SessionError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)
    except SessionBusy as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=409)
    return JSONResponse({"success": True, "session": watermeter_service.get_service().calibration_session.status()})


def _current_crop(service, roi_id: str):
    """Decoded ROI crop of the last reading, or None."""
    for pred in (service.current_state or {}).get("predictions", []) or []:
        if pred.get("id") == roi_id and pred.get("image_base64"):
            return decode_image(base64.b64decode(pred["image_base64"]))
    return None


def _current_class(service, roi_id: str):
    for pred in (service.current_state or {}).get("predictions", []) or []:
        if pred.get("id") == roi_id:
            return pred.get("class")
    return None


def _load_calibration_doc(config: dict):
    try:
        return json.loads(calibration_path(config).read_text())
    except (OSError, ValueError):
        return None


def _bad_roi_id(roi_id: str):
    if not ROI_ID_RE.match(roi_id):
        return JSONResponse({"success": False, "message": f"invalid ROI id {roi_id!r}"}, status_code=400)
    return None


@router.get(
    "/api/calibration/status",
    tags=["Calibration"],
    summary="Calibration tab status",
    description="Collection session, calibration job and a per-dial summary of the stored calibration.",
)
async def calibration_status():
    service = watermeter_service.get_service()
    config = service.config
    doc = _load_calibration_doc(config)
    summary = None
    if doc:
        rois = analog_rois_from_config(config) or {}
        dials = {}
        for rid, d in (doc.get("dials") or {}).items():
            dials[rid] = {
                "pivot_source": d.get("pivot_source"),
                "pivot": d.get("pivot"),
                "ticks": d.get("n_ticks"),
                "offset": d.get("offset"),
                "crop_size": d.get("crop_size"),
                "stale": rid in rois and not _same_roi(d.get("roi", {}), rois[rid]),
                "value": _current_class(service, rid),
            }
        summary = {
            "created_at": doc.get("created_at"),
            "report": doc.get("report"),
            "dials": dials,
            "pivot_overrides": doc.get("pivot_overrides") or {},
        }
    return JSONResponse(
        {
            "success": True,
            "arrows_mode": config.get("inference", {}).get("arrows_mode", "model"),
            "analog_ids": sorted((analog_rois_from_config(config) or {}).keys()),
            "session": service.calibration_session.status(),
            "calibration": summary,
        }
    )


@router.post("/api/calibration/collect/start", tags=["Calibration"], summary="Start collecting calibration frames")
async def calibration_collect_start(request: CollectRequest):
    return _session_call(watermeter_service.get_service().calibration_session.start, request.type, request.value)


@router.post("/api/calibration/collect/stop", tags=["Calibration"], summary="Stop collecting (keep frames)")
async def calibration_collect_stop():
    return _session_call(watermeter_service.get_service().calibration_session.stop)


@router.post("/api/calibration/run", tags=["Calibration"], summary="Calibrate from the collected frames now")
async def calibration_run():
    return _session_call(watermeter_service.get_service().calibration_session.run)


@router.get(
    "/api/calibration/preview/{roi_id}.jpg",
    tags=["Calibration"],
    summary="Dial preview with calibration overlay",
    description="Last reading's crop with ticks, ellipse, pivot and tip drawn (plain crop without calibration).",
)
async def calibration_preview(roi_id: str):
    bad = _bad_roi_id(roi_id)
    if bad:
        return bad
    service = watermeter_service.get_service()
    crop = _current_crop(service, roi_id)
    if crop is None:
        return JSONResponse({"success": False, "message": "no reading for this dial yet"}, status_code=404)
    config = service.config
    doc = _load_calibration_doc(config) or {}
    raw = (doc.get("dials") or {}).get(roi_id)
    dial = None
    if raw:
        dial = DialCalibration.from_dict(raw)
        rois = analog_rois_from_config(config) or {}
        h, w = crop.shape[:2]
        stale = roi_id in rois and not _same_roi(dial.roi, rois[roi_id])
        if stale or abs(w - dial.crop_size[0]) > 2 or abs(h - dial.crop_size[1]) > 2:
            dial = None
    inf = config.get("inference", {})
    color = inf.get("opencv_arrows", {}) or {}
    out = render_overlay(
        crop,
        dial,
        hue_ranges=color.get("hue_ranges"),
        saturation_min=color.get("saturation_min", 50),
        value_min=color.get("value_min", 50),
        tip_percentile=(inf.get("calibrated_arrows", {}) or {}).get("tip_percentile", 95.0),
    )
    return Response(content=encode_jpeg(out), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/api/calibration/pivot/{roi_id}", tags=["Calibration"], summary="Set a manual needle pivot")
async def calibration_set_pivot(roi_id: str, request: PivotRequest):
    bad = _bad_roi_id(roi_id)
    if bad:
        return bad
    service = watermeter_service.get_service()
    crop = _current_crop(service, roi_id)
    if crop is None:
        return JSONResponse({"success": False, "message": "no reading for this dial yet"}, status_code=400)
    h, w = crop.shape[:2]
    if not (0 <= request.x < w and 0 <= request.y < h):
        return JSONResponse({"success": False, "message": f"pivot outside the {w}x{h} crop"}, status_code=400)
    return _session_call(service.calibration_session.set_pivot_override, roi_id, request.x, request.y)


@router.delete("/api/calibration/pivot/{roi_id}", tags=["Calibration"], summary="Back to the automatic pivot")
async def calibration_clear_pivot(roi_id: str):
    bad = _bad_roi_id(roi_id)
    if bad:
        return bad
    return _session_call(watermeter_service.get_service().calibration_session.clear_pivot_override, roi_id)
