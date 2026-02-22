"""ROI configuration routes: rotation, markers, digits, analogs."""

import base64
import logging
from pathlib import Path
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .. import config_utils, watermeter_service
from ..image_pipeline import apply_fisheye_correction
from ..inference import get_inference_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Pydantic models ---


class RotationSubmission(BaseModel):
    rotation: float


class FisheyeSubmission(BaseModel):
    fisheye_correction: float


class RoiBounds(BaseModel):
    x: float
    y: float
    width: float
    height: float


class MarkersSubmission(BaseModel):
    markers: list[RoiBounds]


class DigitsSubmission(BaseModel):
    count: int
    rois: list[RoiBounds]


class AnalogsSubmission(BaseModel):
    count: int
    rois: list[RoiBounds]


# --- Helper ---



def _load_corrected_reference():
    """Load reference image with fisheye correction and rotation applied.

    Returns (img, height, width) or None.
    """
    reference_path = Path("/data/reference_raw.jpg")
    if not reference_path.exists():
        return None

    img = cv2.imread(str(reference_path))
    if img is None:
        return None

    height, width = img.shape[:2]

    service = watermeter_service.get_service()
    detection = service.config.get("detection", {})

    # 1. Fisheye correction (before rotation)
    fisheye_k1 = detection.get("fisheye_correction", 0)
    if fisheye_k1 != 0:
        img = apply_fisheye_correction(img, fisheye_k1)

    # 2. Rotation
    rotation = detection.get("rotation", 0)
    if rotation != 0:
        center = (width / 2, height / 2)
        matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
        img = cv2.warpAffine(img, matrix, (width, height))

    return img, height, width


# --- Routes ---


@router.post(
    "/api/roi/fetch-image",
    tags=["ROI Setup"],
    summary="Fetch reference image",
    description="Fetch the reference image from configured remote URL and save locally for ROI configuration",
)
async def fetch_roi_reference_image():
    """Fetch the reference image from remote URL and save locally."""
    try:
        service = watermeter_service.get_service()
        image_src = service.config.get("images", {}).get("src", "")

        if not image_src:
            return JSONResponse({"success": False, "message": "No image source configured"}, status_code=400)

        # Fetch image from remote URL
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(image_src)
            response.raise_for_status()

        # Save to /data/reference_raw.jpg
        data_path = Path("/data")
        data_path.mkdir(parents=True, exist_ok=True)
        reference_path = data_path / "reference_raw.jpg"

        with open(reference_path, "wb") as f:
            f.write(response.content)

        logger.info(f"Reference image saved: {reference_path}")

        return JSONResponse({"success": True, "message": "Image fetched successfully"})

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching reference image: {e}")
        return JSONResponse({"success": False, "message": f"HTTP error: {str(e)}"}, status_code=502)
    except Exception as e:
        logger.error(f"Error fetching reference image: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/roi/image-source",
    tags=["ROI Setup"],
    summary="Set image source URL",
    description="Validate and save the camera image source URL, fetch a reference image",
)
async def set_image_source(request: Request):
    """Validate image source URL, fetch image, save to config."""
    try:
        data = await request.json()
        url = data.get("url", "").strip()

        if not url:
            return JSONResponse({"success": False, "message": "URL is required"}, status_code=400)

        # Basic URL validation
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return JSONResponse({"success": False, "message": "Invalid URL format"}, status_code=400)

        # Try to fetch the image
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            return JSONResponse({"success": False, "message": f"Could not reach device: {e}"})

        # Validate it looks like an image (check content-type or magic bytes)
        content_type = resp.headers.get("content-type", "")
        if not (content_type.startswith("image/") or resp.content[:3] in [b'\xff\xd8\xff', b'\x89PN']):
            return JSONResponse({"success": False, "message": "URL did not return an image"})

        # Save image
        Path("/data").mkdir(parents=True, exist_ok=True)
        Path("/data/reference_raw.jpg").write_bytes(resp.content)

        # Update config
        config_path = Path("config.yaml")

        host = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            host += f":{parsed.port}"

        def _update(config):
            config.setdefault("images", {})["src"] = url
            config.setdefault("aiote", {})["host"] = host

        config = config_utils.update_config(config_path, _update)

        # Reload config in service
        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Image source saved: {url}")

        return JSONResponse({"success": True, "message": "Image source saved successfully"})

    except Exception as e:
        logger.error(f"Error setting image source: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/roi/reference-image",
    tags=["ROI Setup"],
    summary="Get reference image",
    description="Serve the locally stored reference image for ROI drawing interface",
)
async def get_roi_reference_image():
    """Serve the locally stored reference image."""
    reference_path = Path("/data/reference_raw.jpg")

    if not reference_path.exists():
        return JSONResponse(
            {"success": False, "message": "Reference image not found. Click 'Reload' to fetch it."}, status_code=404
        )

    return FileResponse(reference_path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get(
    "/api/roi/config",
    tags=["ROI Setup"],
    summary="Get ROI configuration",
    description="Get the current ROI configuration (rotation, markers, digits, analogs)",
)
async def get_roi_config():
    """Get the current ROI configuration."""
    service = watermeter_service.get_service()
    detection = service.config.get("detection", {})
    return JSONResponse(
        {
            "fisheye_correction": detection.get("fisheye_correction"),
            "rotation": detection.get("rotation"),
            "markers": detection.get("markers"),
            "digits": detection.get("digits"),
            "analogs": detection.get("analogs"),
        }
    )


@router.post(
    "/api/roi/rotation",
    tags=["ROI Setup"],
    summary="Save rotation",
    description="Save image rotation angle to configuration",
)
async def save_rotation(submission: RotationSubmission):
    """Save rotation value to config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["rotation"] = round(submission.rotation, 4)

        config = config_utils.update_config(config_path, _update)

        # Reload config in service
        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Rotation saved: {submission.rotation}")

        return JSONResponse({"success": True, "message": f"Rotation saved: {submission.rotation}"})

    except Exception as e:
        logger.error(f"Error saving rotation: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/roi/rotation",
    tags=["ROI Setup"],
    summary="Delete rotation",
    description="Remove rotation configuration (reset to 0 degrees)",
)
async def delete_rotation():
    """Delete rotation value from config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" in config and "rotation" in config["detection"]:
                del config["detection"]["rotation"]
                if not config["detection"]:
                    del config["detection"]

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info("Rotation deleted")

        return JSONResponse({"success": True, "message": "Rotation deleted"})

    except Exception as e:
        logger.error(f"Error deleting rotation: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post("/api/roi/fisheye", tags=["ROI Setup"], summary="Save fisheye correction")
async def save_fisheye(submission: FisheyeSubmission):
    """Save fisheye correction coefficient to config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["fisheye_correction"] = round(submission.fisheye_correction, 4)

        config = config_utils.update_config(config_path, _update)
        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Fisheye correction saved: {submission.fisheye_correction}")
        return JSONResponse({"success": True, "message": f"Fisheye correction saved: {submission.fisheye_correction}"})

    except Exception as e:
        logger.error(f"Error saving fisheye correction: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete("/api/roi/fisheye", tags=["ROI Setup"], summary="Delete fisheye correction")
async def delete_fisheye():
    """Remove fisheye correction from config."""
    try:
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" in config and "fisheye_correction" in config["detection"]:
                del config["detection"]["fisheye_correction"]
                if not config["detection"]:
                    del config["detection"]

        config = config_utils.update_config(config_path, _update)
        service = watermeter_service.get_service()
        service.config = config

        logger.info("Fisheye correction deleted")
        return JSONResponse({"success": True, "message": "Fisheye correction deleted"})

    except Exception as e:
        logger.error(f"Error deleting fisheye correction: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post("/api/roi/fisheye-preview", tags=["ROI Setup"], summary="Preview fisheye correction")
async def fisheye_preview(submission: FisheyeSubmission):
    """Return the reference image with fisheye correction applied as JPEG."""
    try:
        reference_path = Path("/data/reference_raw.jpg")
        if not reference_path.exists():
            return JSONResponse({"success": False, "message": "No reference image"}, status_code=404)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({"success": False, "message": "Cannot read reference image"}, status_code=500)

        if submission.fisheye_correction != 0:
            img = apply_fisheye_correction(img, submission.fisheye_correction)

        _, buffer = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        encoded = base64.b64encode(buffer).decode('utf-8')
        return JSONResponse({"success": True, "image": f"data:image/jpeg;base64,{encoded}"})

    except Exception as e:
        logger.error(f"Error generating fisheye preview: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/roi/markers",
    tags=["ROI Setup"],
    summary="Save alignment markers",
    description="Save marker box coordinates to config and extract marker reference images",
)
async def save_markers(submission: MarkersSubmission):
    """Save marker boxes to config and extract marker images."""
    try:
        result = _load_corrected_reference()
        if result is None:
            return JSONResponse(
                {"success": False, "message": "Reference image not found or failed to load"}, status_code=400
            )

        img, height, width = result

        # Extract and save marker images
        data_path = Path("/data")
        markers_data = []

        for i, marker in enumerate(submission.markers):
            # Convert normalized coords to pixels
            px_x = int(marker.x * width)
            px_y = int(marker.y * height)
            px_w = int(marker.width * width)
            px_h = int(marker.height * height)

            # Clamp to image bounds
            px_x = max(0, min(px_x, width - 1))
            px_y = max(0, min(px_y, height - 1))
            px_w = min(px_w, width - px_x)
            px_h = min(px_h, height - px_y)

            # Extract ROI
            roi = img[px_y : px_y + px_h, px_x : px_x + px_w]

            # Save marker image
            marker_path = data_path / f"marker_{i + 1}.jpg"
            cv2.imwrite(str(marker_path), roi)

            markers_data.append(
                {
                    "x": round(marker.x, 4),
                    "y": round(marker.y, 4),
                    "width": round(marker.width, 4),
                    "height": round(marker.height, 4),
                }
            )

        # Save to config
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["markers"] = markers_data

        config = config_utils.update_config(config_path, _update)

        # Reload config in service
        service = watermeter_service.get_service()
        service.config = config
        service.invalidate_marker_cache()

        logger.info(f"Markers saved: {len(markers_data)} markers")

        return JSONResponse({"success": True, "message": f"Saved {len(markers_data)} markers"})

    except Exception as e:
        logger.error(f"Error saving markers: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/roi/markers",
    tags=["ROI Setup"],
    summary="Delete markers",
    description="Delete all marker configurations and reference images",
)
async def delete_markers():
    """Delete markers from config."""
    try:
        config_path = Path("config.yaml")

        # Read marker count before deleting (for image cleanup)
        existing = config_utils.load_config(config_path)
        marker_count = len(existing.get("detection", {}).get("markers", []))

        def _update(config):
            if "detection" in config and "markers" in config["detection"]:
                del config["detection"]["markers"]

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config
        service.invalidate_marker_cache()

        # Delete marker images
        for i in range(1, marker_count + 1):
            marker_path = Path(f"/data/marker_{i}.jpg")
            if marker_path.exists():
                marker_path.unlink()

        logger.info("Markers deleted")

        return JSONResponse({"success": True, "message": "Markers deleted"})

    except Exception as e:
        logger.error(f"Error deleting markers: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/roi/marker-image/{marker_id}",
    tags=["ROI Setup"],
    summary="Get marker image",
    description="Serve a specific marker reference image by ID",
)
async def get_marker_image(marker_id: int):
    """Serve a marker image."""
    marker_path = Path(f"/data/marker_{marker_id}.jpg")

    if not marker_path.exists():
        return JSONResponse({"success": False, "message": f"Marker {marker_id} image not found"}, status_code=404)

    return FileResponse(marker_path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post(
    "/api/roi/digits",
    tags=["ROI Setup"],
    summary="Save digit ROIs",
    description="Save digit region coordinates to config and extract digit preview images",
)
async def save_digits(submission: DigitsSubmission):
    """Save digit ROIs to config and extract digit images."""
    try:
        result = _load_corrected_reference()
        if result is None:
            return JSONResponse(
                {"success": False, "message": "Reference image not found or failed to load"}, status_code=400
            )

        img, height, width = result

        # Extract and save digit images
        data_path = Path("/data")
        rois_data = []

        for i, roi in enumerate(submission.rois):
            px_x = int(roi.x * width)
            px_y = int(roi.y * height)
            px_w = int(roi.width * width)
            px_h = int(roi.height * height)

            px_x = max(0, min(px_x, width - 1))
            px_y = max(0, min(px_y, height - 1))
            px_w = min(px_w, width - px_x)
            px_h = min(px_h, height - px_y)

            roi_img = img[px_y : px_y + px_h, px_x : px_x + px_w]

            digit_path = data_path / f"digit_{i + 1}.jpg"
            cv2.imwrite(str(digit_path), roi_img)

            rois_data.append(
                {
                    "x": round(roi.x, 4),
                    "y": round(roi.y, 4),
                    "width": round(roi.width, 4),
                    "height": round(roi.height, 4),
                }
            )

        # Save to config
        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["digits"] = {"count": submission.count, "rois": rois_data}

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Digits saved: {submission.count} digits")

        return JSONResponse({"success": True, "message": f"Saved {submission.count} digit ROIs"})

    except Exception as e:
        logger.error(f"Error saving digits: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/roi/digits",
    tags=["ROI Setup"],
    summary="Delete digit ROIs",
    description="Delete all digit ROI configurations and preview images",
)
async def delete_digits():
    """Delete digit ROIs from config."""
    try:
        config_path = Path("config.yaml")
        existing = config_utils.load_config(config_path)
        count = 0
        if "detection" in existing and "digits" in existing["detection"]:
            count = existing["detection"]["digits"].get("count", 0)

        def _update(config):
            if "detection" in config and "digits" in config["detection"]:
                del config["detection"]["digits"]

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        for i in range(1, count + 1):
            digit_path = Path(f"/data/digit_{i}.jpg")
            if digit_path.exists():
                digit_path.unlink()

        logger.info("Digits deleted")

        return JSONResponse({"success": True, "message": "Digits deleted"})

    except Exception as e:
        logger.error(f"Error deleting digits: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/roi/digit-image/{digit_id}",
    tags=["ROI Setup"],
    summary="Get digit preview image",
    description="Serve a specific digit preview image by ID",
)
async def get_digit_image(digit_id: int):
    """Serve a digit image."""
    digit_path = Path(f"/data/digit_{digit_id}.jpg")

    if not digit_path.exists():
        return JSONResponse({"success": False, "message": f"Digit {digit_id} image not found"}, status_code=404)

    return FileResponse(digit_path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post(
    "/api/roi/digit-preview",
    tags=["ROI Setup"],
    summary="Preview digit inference",
    description="Run inference on a single digit ROI and return the prediction for validation",
)
async def preview_digit_inference(submission: RoiBounds):
    """Run inference on a single ROI and return the prediction."""
    try:
        result = _load_corrected_reference()
        if result is None:
            return JSONResponse(
                {"success": False, "message": "Reference image not found or failed to load"}, status_code=400
            )

        img, height, width = result

        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y : px_y + px_h, px_x : px_x + px_w]

        # Encode the cropped ROI image
        _, buffer = cv2.imencode(".jpg", roi_img)
        image_base64 = base64.b64encode(buffer).decode("utf-8")

        # Check if digits model is available
        if get_inference_service().get_classifier("digits") is None:
            return JSONResponse({"success": True, "no_model": True, "image_base64": image_base64})

        # Run inference
        inference_result = get_inference_service().predict_from_bytes("digits", bytes(buffer))
        prediction = inference_result["class"]
        confidence = inference_result["confidence"]

        return JSONResponse(
            {"success": True, "prediction": prediction, "confidence": confidence, "image_base64": image_base64}
        )

    except Exception as e:
        logger.error(f"Error in digit preview: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/roi/analogs",
    tags=["ROI Setup"],
    summary="Save analog dial ROIs",
    description="Save analog dial region coordinates to config and extract preview images",
)
async def save_analogs(submission: AnalogsSubmission):
    """Save analog ROIs to config and extract analog images."""
    try:
        result = _load_corrected_reference()
        if result is None:
            return JSONResponse(
                {"success": False, "message": "Reference image not found or failed to load"}, status_code=400
            )

        img, height, width = result

        data_path = Path("/data")
        rois_data = []

        for i, roi in enumerate(submission.rois):
            px_x = int(roi.x * width)
            px_y = int(roi.y * height)
            px_w = int(roi.width * width)
            px_h = int(roi.height * height)

            px_x = max(0, min(px_x, width - 1))
            px_y = max(0, min(px_y, height - 1))
            px_w = min(px_w, width - px_x)
            px_h = min(px_h, height - px_y)

            roi_img = img[px_y : px_y + px_h, px_x : px_x + px_w]

            analog_path = data_path / f"analog_{i + 1}.jpg"
            cv2.imwrite(str(analog_path), roi_img)

            rois_data.append(
                {
                    "x": round(roi.x, 4),
                    "y": round(roi.y, 4),
                    "width": round(roi.width, 4),
                    "height": round(roi.height, 4),
                }
            )

        config_path = Path("config.yaml")

        def _update(config):
            if "detection" not in config:
                config["detection"] = {}
            config["detection"]["analogs"] = {"count": submission.count, "rois": rois_data}

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Analogs saved: {submission.count} analogs")

        return JSONResponse({"success": True, "message": f"Saved {submission.count} analog ROIs"})

    except Exception as e:
        logger.error(f"Error saving analogs: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/roi/analogs",
    tags=["ROI Setup"],
    summary="Delete analog dial ROIs",
    description="Delete all analog dial ROI configurations and preview images",
)
async def delete_analogs():
    """Delete analog ROIs from config."""
    try:
        config_path = Path("config.yaml")
        existing = config_utils.load_config(config_path)
        count = 0
        if "detection" in existing and "analogs" in existing["detection"]:
            count = existing["detection"]["analogs"].get("count", 0)

        def _update(config):
            if "detection" in config and "analogs" in config["detection"]:
                del config["detection"]["analogs"]

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        for i in range(1, count + 1):
            analog_path = Path(f"/data/analog_{i}.jpg")
            if analog_path.exists():
                analog_path.unlink()

        logger.info("Analogs deleted")

        return JSONResponse({"success": True, "message": "Analogs deleted"})

    except Exception as e:
        logger.error(f"Error deleting analogs: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/roi/analog-image/{analog_id}",
    tags=["ROI Setup"],
    summary="Get analog dial preview image",
    description="Serve a specific analog dial preview image by ID",
)
async def get_analog_image(analog_id: int):
    """Serve an analog image."""
    analog_path = Path(f"/data/analog_{analog_id}.jpg")

    if not analog_path.exists():
        return JSONResponse({"success": False, "message": f"Analog {analog_id} image not found"}, status_code=404)

    return FileResponse(analog_path, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post(
    "/api/roi/analog-preview",
    tags=["ROI Setup"],
    summary="Preview analog dial inference",
    description="Run inference on a single analog dial ROI and return the prediction for validation",
)
async def preview_analog_inference(submission: RoiBounds):
    """Run inference on a single analog ROI and return the prediction."""
    try:
        result = _load_corrected_reference()
        if result is None:
            return JSONResponse(
                {"success": False, "message": "Reference image not found or failed to load"}, status_code=400
            )

        img, height, width = result

        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y : px_y + px_h, px_x : px_x + px_w]

        # Encode the cropped ROI image
        _, buffer = cv2.imencode(".jpg", roi_img)
        image_base64 = base64.b64encode(buffer).decode("utf-8")

        # Check if arrows model is available
        if get_inference_service().get_classifier("arrows") is None:
            return JSONResponse({"success": True, "no_model": True, "image_base64": image_base64})

        # Run inference
        inference_result = get_inference_service().predict_from_bytes("arrows", bytes(buffer))
        prediction = inference_result["class"]
        confidence = inference_result["confidence"]

        return JSONResponse(
            {"success": True, "prediction": prediction, "confidence": confidence, "image_base64": image_base64}
        )

    except Exception as e:
        logger.error(f"Error in analog preview: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)
