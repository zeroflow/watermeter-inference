"""ROI configuration routes: rotation, markers, digits, analogs."""

import base64
import logging
from pathlib import Path

import cv2
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from .. import watermeter_service, config_utils
from ..inference import get_inference_service

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Pydantic models ---

class RotationSubmission(BaseModel):
    rotation: float


class MarkerBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class MarkersSubmission(BaseModel):
    markers: list[MarkerBox]


class DigitRoi(BaseModel):
    x: float
    y: float
    width: float
    height: float


class DigitsSubmission(BaseModel):
    count: int
    rois: list[DigitRoi]


class SingleRoiSubmission(BaseModel):
    x: float
    y: float
    width: float
    height: float


class AnalogRoi(BaseModel):
    x: float
    y: float
    width: float
    height: float


class AnalogsSubmission(BaseModel):
    count: int
    rois: list[AnalogRoi]


# --- Helper ---

def _load_rotated_reference():
    """Load reference image with rotation applied. Returns (img, height, width) or None."""
    reference_path = Path('/data/reference_raw.jpg')
    if not reference_path.exists():
        return None

    img = cv2.imread(str(reference_path))
    if img is None:
        return None

    height, width = img.shape[:2]

    service = watermeter_service.get_service()
    rotation = service.config.get('detection', {}).get('rotation', 0)
    if rotation != 0:
        center = (width / 2, height / 2)
        matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
        img = cv2.warpAffine(img, matrix, (width, height))

    return img, height, width


# --- Routes ---

@router.post("/api/roi/fetch-image")
async def fetch_roi_reference_image():
    """Fetch the reference image from remote URL and save locally."""
    try:
        service = watermeter_service.get_service()
        image_src = service.config.get('images', {}).get('src', '')

        if not image_src:
            return JSONResponse({
                "success": False,
                "message": "No image source configured"
            }, status_code=400)

        # Fetch image from remote URL
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(image_src)
            response.raise_for_status()

        # Save to /data/reference_raw.jpg
        data_path = Path('/data')
        data_path.mkdir(parents=True, exist_ok=True)
        reference_path = data_path / 'reference_raw.jpg'

        with open(reference_path, 'wb') as f:
            f.write(response.content)

        logger.info(f"Reference image saved: {reference_path}")

        return JSONResponse({
            "success": True,
            "message": "Image fetched successfully"
        })

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching reference image: {e}")
        return JSONResponse({
            "success": False,
            "message": f"HTTP error: {str(e)}"
        }, status_code=502)
    except Exception as e:
        logger.error(f"Error fetching reference image: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/roi/reference-image")
async def get_roi_reference_image():
    """Serve the locally stored reference image."""
    reference_path = Path('/data/reference_raw.jpg')

    if not reference_path.exists():
        return JSONResponse({
            "success": False,
            "message": "Reference image not found. Click 'Reload' to fetch it."
        }, status_code=404)

    return FileResponse(
        reference_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"}
    )


@router.get("/api/roi/config")
async def get_roi_config():
    """Get the current ROI configuration."""
    service = watermeter_service.get_service()
    detection = service.config.get('detection', {})
    return JSONResponse({
        "rotation": detection.get('rotation'),
        "markers": detection.get('markers'),
        "digits": detection.get('digits'),
        "analogs": detection.get('analogs')
    })


@router.post("/api/roi/rotation")
async def save_rotation(submission: RotationSubmission):
    """Save rotation value to config."""
    try:
        config_path = Path('config.yaml')

        def _update(config):
            if 'detection' not in config:
                config['detection'] = {}
            config['detection']['rotation'] = round(submission.rotation, 4)

        config = config_utils.update_config(config_path, _update)

        # Reload config in service
        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Rotation saved: {submission.rotation}")

        return JSONResponse({
            "success": True,
            "message": f"Rotation saved: {submission.rotation}"
        })

    except Exception as e:
        logger.error(f"Error saving rotation: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.delete("/api/roi/rotation")
async def delete_rotation():
    """Delete rotation value from config."""
    try:
        config_path = Path('config.yaml')

        def _update(config):
            if 'detection' in config and 'rotation' in config['detection']:
                del config['detection']['rotation']
                if not config['detection']:
                    del config['detection']

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info("Rotation deleted")

        return JSONResponse({
            "success": True,
            "message": "Rotation deleted"
        })

    except Exception as e:
        logger.error(f"Error deleting rotation: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.post("/api/roi/markers")
async def save_markers(submission: MarkersSubmission):
    """Save marker boxes to config and extract marker images."""
    try:
        result = _load_rotated_reference()
        if result is None:
            return JSONResponse({
                "success": False,
                "message": "Reference image not found or failed to load"
            }, status_code=400)

        img, height, width = result

        # Extract and save marker images
        data_path = Path('/data')
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
            roi = img[px_y:px_y + px_h, px_x:px_x + px_w]

            # Save marker image
            marker_path = data_path / f'marker_{i + 1}.jpg'
            cv2.imwrite(str(marker_path), roi)

            markers_data.append({
                'x': round(marker.x, 4),
                'y': round(marker.y, 4),
                'width': round(marker.width, 4),
                'height': round(marker.height, 4)
            })

        # Save to config
        config_path = Path('config.yaml')

        def _update(config):
            if 'detection' not in config:
                config['detection'] = {}
            config['detection']['markers'] = markers_data

        config = config_utils.update_config(config_path, _update)

        # Reload config in service
        service = watermeter_service.get_service()
        service.config = config
        service.invalidate_marker_cache()

        logger.info(f"Markers saved: {len(markers_data)} markers")

        return JSONResponse({
            "success": True,
            "message": f"Saved {len(markers_data)} markers"
        })

    except Exception as e:
        logger.error(f"Error saving markers: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.delete("/api/roi/markers")
async def delete_markers():
    """Delete markers from config."""
    try:
        config_path = Path('config.yaml')

        # Read marker count before deleting (for image cleanup)
        existing = config_utils.load_config(config_path)
        marker_count = len(existing.get('detection', {}).get('markers', []))

        def _update(config):
            if 'detection' in config and 'markers' in config['detection']:
                del config['detection']['markers']

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config
        service.invalidate_marker_cache()

        # Delete marker images
        for i in range(1, marker_count + 1):
            marker_path = Path(f'/data/marker_{i}.jpg')
            if marker_path.exists():
                marker_path.unlink()

        logger.info("Markers deleted")

        return JSONResponse({
            "success": True,
            "message": "Markers deleted"
        })

    except Exception as e:
        logger.error(f"Error deleting markers: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/roi/marker-image/{marker_id}")
async def get_marker_image(marker_id: int):
    """Serve a marker image."""
    marker_path = Path(f'/data/marker_{marker_id}.jpg')

    if not marker_path.exists():
        return JSONResponse({
            "success": False,
            "message": f"Marker {marker_id} image not found"
        }, status_code=404)

    return FileResponse(
        marker_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"}
    )


@router.post("/api/roi/digits")
async def save_digits(submission: DigitsSubmission):
    """Save digit ROIs to config and extract digit images."""
    try:
        result = _load_rotated_reference()
        if result is None:
            return JSONResponse({
                "success": False,
                "message": "Reference image not found or failed to load"
            }, status_code=400)

        img, height, width = result

        # Extract and save digit images
        data_path = Path('/data')
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

            roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

            digit_path = data_path / f'digit_{i + 1}.jpg'
            cv2.imwrite(str(digit_path), roi_img)

            rois_data.append({
                'x': round(roi.x, 4),
                'y': round(roi.y, 4),
                'width': round(roi.width, 4),
                'height': round(roi.height, 4)
            })

        # Save to config
        config_path = Path('config.yaml')

        def _update(config):
            if 'detection' not in config:
                config['detection'] = {}
            config['detection']['digits'] = {
                'count': submission.count,
                'rois': rois_data
            }

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Digits saved: {submission.count} digits")

        return JSONResponse({
            "success": True,
            "message": f"Saved {submission.count} digit ROIs"
        })

    except Exception as e:
        logger.error(f"Error saving digits: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.delete("/api/roi/digits")
async def delete_digits():
    """Delete digit ROIs from config."""
    try:
        config_path = Path('config.yaml')
        existing = config_utils.load_config(config_path)
        count = 0
        if 'detection' in existing and 'digits' in existing['detection']:
            count = existing['detection']['digits'].get('count', 0)

        def _update(config):
            if 'detection' in config and 'digits' in config['detection']:
                del config['detection']['digits']

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        for i in range(1, count + 1):
            digit_path = Path(f'/data/digit_{i}.jpg')
            if digit_path.exists():
                digit_path.unlink()

        logger.info("Digits deleted")

        return JSONResponse({
            "success": True,
            "message": "Digits deleted"
        })

    except Exception as e:
        logger.error(f"Error deleting digits: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/roi/digit-image/{digit_id}")
async def get_digit_image(digit_id: int):
    """Serve a digit image."""
    digit_path = Path(f'/data/digit_{digit_id}.jpg')

    if not digit_path.exists():
        return JSONResponse({
            "success": False,
            "message": f"Digit {digit_id} image not found"
        }, status_code=404)

    return FileResponse(
        digit_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"}
    )


@router.post("/api/roi/digit-preview")
async def preview_digit_inference(submission: SingleRoiSubmission):
    """Run inference on a single ROI and return the prediction."""
    try:
        import tempfile

        result = _load_rotated_reference()
        if result is None:
            return JSONResponse({
                "success": False,
                "message": "Reference image not found or failed to load"
            }, status_code=400)

        img, height, width = result

        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name
            cv2.imwrite(temp_path, roi_img)

        try:
            inference_result = get_inference_service().predict('digits', temp_path)
            prediction = inference_result['class']
            confidence = inference_result['confidence']
        finally:
            Path(temp_path).unlink(missing_ok=True)

        _, buffer = cv2.imencode('.jpg', roi_img)
        image_base64 = base64.b64encode(buffer).decode('utf-8')

        return JSONResponse({
            "success": True,
            "prediction": prediction,
            "confidence": confidence,
            "image_base64": image_base64
        })

    except Exception as e:
        logger.error(f"Error in digit preview: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.post("/api/roi/analogs")
async def save_analogs(submission: AnalogsSubmission):
    """Save analog ROIs to config and extract analog images."""
    try:
        result = _load_rotated_reference()
        if result is None:
            return JSONResponse({
                "success": False,
                "message": "Reference image not found or failed to load"
            }, status_code=400)

        img, height, width = result

        data_path = Path('/data')
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

            roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

            analog_path = data_path / f'analog_{i + 1}.jpg'
            cv2.imwrite(str(analog_path), roi_img)

            rois_data.append({
                'x': round(roi.x, 4),
                'y': round(roi.y, 4),
                'width': round(roi.width, 4),
                'height': round(roi.height, 4)
            })

        config_path = Path('config.yaml')

        def _update(config):
            if 'detection' not in config:
                config['detection'] = {}
            config['detection']['analogs'] = {
                'count': submission.count,
                'rois': rois_data
            }

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        logger.info(f"Analogs saved: {submission.count} analogs")

        return JSONResponse({
            "success": True,
            "message": f"Saved {submission.count} analog ROIs"
        })

    except Exception as e:
        logger.error(f"Error saving analogs: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.delete("/api/roi/analogs")
async def delete_analogs():
    """Delete analog ROIs from config."""
    try:
        config_path = Path('config.yaml')
        existing = config_utils.load_config(config_path)
        count = 0
        if 'detection' in existing and 'analogs' in existing['detection']:
            count = existing['detection']['analogs'].get('count', 0)

        def _update(config):
            if 'detection' in config and 'analogs' in config['detection']:
                del config['detection']['analogs']

        config = config_utils.update_config(config_path, _update)

        service = watermeter_service.get_service()
        service.config = config

        for i in range(1, count + 1):
            analog_path = Path(f'/data/analog_{i}.jpg')
            if analog_path.exists():
                analog_path.unlink()

        logger.info("Analogs deleted")

        return JSONResponse({
            "success": True,
            "message": "Analogs deleted"
        })

    except Exception as e:
        logger.error(f"Error deleting analogs: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.get("/api/roi/analog-image/{analog_id}")
async def get_analog_image(analog_id: int):
    """Serve an analog image."""
    analog_path = Path(f'/data/analog_{analog_id}.jpg')

    if not analog_path.exists():
        return JSONResponse({
            "success": False,
            "message": f"Analog {analog_id} image not found"
        }, status_code=404)

    return FileResponse(
        analog_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"}
    )


@router.post("/api/roi/analog-preview")
async def preview_analog_inference(submission: SingleRoiSubmission):
    """Run inference on a single analog ROI and return the prediction."""
    try:
        import tempfile

        result = _load_rotated_reference()
        if result is None:
            return JSONResponse({
                "success": False,
                "message": "Reference image not found or failed to load"
            }, status_code=400)

        img, height, width = result

        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name
            cv2.imwrite(temp_path, roi_img)

        try:
            inference_result = get_inference_service().predict('arrows', temp_path)
            prediction = inference_result['class']
            confidence = inference_result['confidence']
        finally:
            Path(temp_path).unlink(missing_ok=True)

        _, buffer = cv2.imencode('.jpg', roi_img)
        image_base64 = base64.b64encode(buffer).decode('utf-8')

        return JSONResponse({
            "success": True,
            "prediction": prediction,
            "confidence": confidence,
            "image_base64": image_base64
        })

    except Exception as e:
        logger.error(f"Error in analog preview: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)
