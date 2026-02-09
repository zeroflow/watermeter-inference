"""
FastAPI Web Application for Water Meter Dashboard
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import random
import shutil
import httpx
import yaml
import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List
import logging

from watermeter_service import get_service
from inference import digits_classifier, arrows_classifier, get_inference_service
from model_manager import get_model_manager
from training_manager import get_training_manager
import config_utils

logger = logging.getLogger(__name__)


class TrainingSubmission(BaseModel):
    """Request model for training submissions."""
    id: str
    image_base64: str
    model: str
    next_image_base64: str = None  # Optional: next dial image for annotation help


class LabelSubmission(BaseModel):
    """Request model for label submissions."""
    filename: str
    model_type: str
    label: str


class DeleteSubmission(BaseModel):
    """Request model for delete submissions."""
    filename: str
    model_type: str


class RotationSubmission(BaseModel):
    """Request model for rotation config."""
    rotation: float


class MarkerBox(BaseModel):
    """Single marker box with normalized coordinates (0-1)."""
    x: float
    y: float
    width: float
    height: float


class MarkersSubmission(BaseModel):
    """Request model for markers config."""
    markers: list[MarkerBox]


class DigitRoi(BaseModel):
    """Single digit ROI with normalized coordinates (0-1)."""
    x: float
    y: float
    width: float
    height: float


class DigitsSubmission(BaseModel):
    """Request model for digits config."""
    count: int
    rois: list[DigitRoi]


class SingleRoiSubmission(BaseModel):
    """Request model for single ROI inference preview."""
    x: float
    y: float
    width: float
    height: float


class AnalogRoi(BaseModel):
    """Single analog ROI with normalized coordinates (0-1)."""
    x: float
    y: float
    width: float
    height: float


class AnalogsSubmission(BaseModel):
    """Request model for analog ROIs config."""
    count: int
    rois: list[AnalogRoi]


class ConfigSaveSubmission(BaseModel):
    """Request model for config save."""
    content: str
    save_option: str = "saveonly"  # "saveonly" or "restart"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI app."""
    # Startup
    logger.info("Starting Water Meter Dashboard...")
    service = get_service()
    service.start_mqtt()
    logger.info("Water Meter Dashboard started")

    # Initial reading on startup
    logger.info("Triggering initial reading...")
    asyncio.create_task(service.process_reading())

    yield

    # Shutdown
    logger.info("Shutting down Water Meter Dashboard...")
    service = get_service()
    service.stop_mqtt()
    logger.info("Water Meter Dashboard stopped")


# Create FastAPI app
app = FastAPI(
    title="Water Meter AI Dashboard",
    description="OpenVINO-based water meter reading with live dashboard",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/status")
async def get_status():
    """Get current status as JSON."""
    service = get_service()
    return JSONResponse(service.current_state)


@app.get("/api/status/html", response_class=HTMLResponse)
async def get_status_html(request: Request):
    """Get current status as HTML fragment for HTMX."""
    service = get_service()
    state = service.current_state

    return templates.TemplateResponse(
        "status_fragment.html",
        {
            "request": request,
            **state  # Unpack all state variables
        }
    )


@app.post("/api/trigger")
async def trigger_reading():
    """Manually trigger a new reading."""
    service = get_service()

    if service.current_state['processing']:
        return JSONResponse(
            {"message": "Processing already in progress"},
            status_code=409
        )

    # Start processing in background
    asyncio.create_task(service.process_reading())

    return JSONResponse({"message": "Reading triggered successfully"})


@app.post("/api/reset")
async def reset_previous_value():
    """Reset the previous value."""
    service = get_service()
    service.reset_previous_value()
    return JSONResponse({"message": "Previous value reset successfully"})


@app.post("/api/toggle-ha-publish")
async def toggle_ha_publish(enabled: bool):
    """Toggle Home Assistant MQTT publishing."""
    service = get_service()
    service.toggle_ha_publish(enabled)
    status = "enabled" if enabled else "disabled"
    return JSONResponse({"message": f"Home Assistant publishing {status}"})


@app.post("/api/submit-training")
async def submit_for_training(submission: TrainingSubmission):
    """Submit an image for manual training/correction."""
    try:
        service = get_service()

        # Get save path from config
        save_path = service.config.get('low_confidence', {}).get('save_path', '/training/')

        # Create directory structure: save_path/{model}/
        model_dir = Path(save_path) / submission.model / 'input'
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
            "message": f"Bild gespeichert: {filename}",
            "path": str(filepath)
        })

    except Exception as e:
        logger.error(f"Error submitting for training: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Fehler beim Speichern: {str(e)}"
        }, status_code=500)


@app.get("/label", response_class=HTMLResponse)
async def label_page(request: Request):
    """Render the labeling interface page."""
    return templates.TemplateResponse("label.html", {"request": request})


@app.get("/roi-config", response_class=HTMLResponse)
async def roi_config_page(request: Request):
    """Render the ROI configuration page."""
    return templates.TemplateResponse("roi_config.html", {"request": request})


@app.get("/config-editor", response_class=HTMLResponse)
async def config_editor_page(request: Request):
    """Render the config editor page with Monaco editor."""
    return templates.TemplateResponse("config_editor.html", {"request": request})


@app.get("/api/config")
async def get_config():
    """Get the current config as YAML string (with comments preserved)."""
    try:
        config_path = Path("config.yaml")
        if not config_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Config file not found"
            }, status_code=404)

        # Read raw file to preserve comments
        content = config_path.read_text(encoding='utf-8')

        return JSONResponse({
            "success": True,
            "content": content
        })

    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/config/save")
async def save_config(submission: ConfigSaveSubmission):
    """
    Save config with comment preservation.

    Args:
        submission: Contains 'content' (YAML string) and 'save_option' ("saveonly" or "restart")
    """
    try:
        # Validate the YAML first
        validation = config_utils.validate_config(submission.content)
        if not validation['valid']:
            return JSONResponse({
                "success": False,
                "message": f"Invalid config: {validation['error']}"
            }, status_code=400)

        # Parse to verify it's valid YAML (ruamel.yaml preserves comments)
        config = config_utils.load_config_string(submission.content)

        # Save to file
        config_path = Path("config.yaml")
        config_utils.save_config(config, config_path)

        logger.info(f"Config saved (option: {submission.save_option})")

        # If restart requested, we'd need to trigger a service reload
        # For now, just inform the user they need to restart manually
        message = "Config saved successfully"
        if submission.save_option == "restart":
            message += ". Please restart the service to apply changes."

        return JSONResponse({
            "success": True,
            "message": message
        })

    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.get("/api/config/schema.json")
async def get_config_schema():
    """Get JSON schema for config validation (used by Monaco editor)."""
    return JSONResponse(config_utils.get_config_schema())


@app.post("/api/roi/fetch-image")
async def fetch_roi_reference_image():
    """Fetch the reference image from remote URL and save locally."""
    try:
        service = get_service()
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


@app.get("/api/roi/reference-image")
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


@app.get("/api/roi/config")
async def get_roi_config():
    """Get the current ROI configuration."""
    service = get_service()
    detection = service.config.get('detection', {})
    return JSONResponse({
        "rotation": detection.get('rotation'),
        "markers": detection.get('markers'),
        "digits": detection.get('digits'),
        "analogs": detection.get('analogs')
    })


@app.post("/api/roi/rotation")
async def save_rotation(submission: RotationSubmission):
    """Save rotation value to config."""
    try:
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        # Ensure detection section exists
        if 'detection' not in config:
            config['detection'] = {}

        config['detection']['rotation'] = submission.rotation

        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        # Reload config in service
        service = get_service()
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


@app.delete("/api/roi/rotation")
async def delete_rotation():
    """Delete rotation value from config."""
    try:
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' in config and 'rotation' in config['detection']:
            del config['detection']['rotation']

            # Remove detection section if empty
            if not config['detection']:
                del config['detection']

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            # Reload config in service
            service = get_service()
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


@app.post("/api/roi/markers")
async def save_markers(submission: MarkersSubmission):
    """Save marker boxes to config and extract marker images."""
    try:
        # Load reference image
        reference_path = Path('/data/reference_raw.jpg')
        if not reference_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Reference image not found"
            }, status_code=400)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({
                "success": False,
                "message": "Failed to load reference image"
            }, status_code=500)

        height, width = img.shape[:2]

        # Apply rotation if configured
        service = get_service()
        rotation = service.config.get('detection', {}).get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))

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
                'x': marker.x,
                'y': marker.y,
                'width': marker.width,
                'height': marker.height
            })

        # Save to config
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' not in config:
            config['detection'] = {}

        config['detection']['markers'] = markers_data

        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        # Reload config in service
        service.config = config

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


@app.delete("/api/roi/markers")
async def delete_markers():
    """Delete markers from config."""
    try:
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' in config and 'markers' in config['detection']:
            del config['detection']['markers']

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            service = get_service()
            service.config = config

        # Delete marker images
        for i in range(1, 3):
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


@app.get("/api/roi/marker-image/{marker_id}")
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


@app.post("/api/roi/digits")
async def save_digits(submission: DigitsSubmission):
    """Save digit ROIs to config and extract digit images."""
    try:
        # Load reference image
        reference_path = Path('/data/reference_raw.jpg')
        if not reference_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Reference image not found"
            }, status_code=400)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({
                "success": False,
                "message": "Failed to load reference image"
            }, status_code=500)

        height, width = img.shape[:2]

        # Apply rotation if configured
        service = get_service()
        rotation = service.config.get('detection', {}).get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))

        # Extract and save digit images
        data_path = Path('/data')
        rois_data = []

        for i, roi in enumerate(submission.rois):
            # Convert normalized coords to pixels
            px_x = int(roi.x * width)
            px_y = int(roi.y * height)
            px_w = int(roi.width * width)
            px_h = int(roi.height * height)

            # Clamp to image bounds
            px_x = max(0, min(px_x, width - 1))
            px_y = max(0, min(px_y, height - 1))
            px_w = min(px_w, width - px_x)
            px_h = min(px_h, height - px_y)

            # Extract ROI
            roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

            # Save digit image
            digit_path = data_path / f'digit_{i + 1}.jpg'
            cv2.imwrite(str(digit_path), roi_img)

            rois_data.append({
                'x': roi.x,
                'y': roi.y,
                'width': roi.width,
                'height': roi.height
            })

        # Save to config
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' not in config:
            config['detection'] = {}

        config['detection']['digits'] = {
            'count': submission.count,
            'rois': rois_data
        }

        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

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


@app.delete("/api/roi/digits")
async def delete_digits():
    """Delete digit ROIs from config."""
    try:
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' in config and 'digits' in config['detection']:
            count = config['detection']['digits'].get('count', 0)
            del config['detection']['digits']

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            service = get_service()
            service.config = config

            # Delete digit images
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


@app.get("/api/roi/digit-image/{digit_id}")
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


@app.post("/api/roi/digit-preview")
async def preview_digit_inference(submission: SingleRoiSubmission):
    """Run inference on a single ROI and return the prediction."""
    try:
        import tempfile

        # Load reference image
        reference_path = Path('/data/reference_raw.jpg')
        if not reference_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Reference image not found"
            }, status_code=400)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({
                "success": False,
                "message": "Failed to load reference image"
            }, status_code=500)

        height, width = img.shape[:2]

        # Apply rotation if configured
        service = get_service()
        rotation = service.config.get('detection', {}).get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))

        # Extract ROI
        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

        # Save to temp file for inference
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name
            cv2.imwrite(temp_path, roi_img)

        try:
            result = digits_classifier.predict(temp_path)
            prediction = result['class']
            confidence = result['confidence']
        finally:
            Path(temp_path).unlink(missing_ok=True)

        # Encode ROI image as base64 for preview
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


@app.post("/api/roi/analogs")
async def save_analogs(submission: AnalogsSubmission):
    """Save analog ROIs to config and extract analog images."""
    try:
        # Load reference image
        reference_path = Path('/data/reference_raw.jpg')
        if not reference_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Reference image not found"
            }, status_code=400)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({
                "success": False,
                "message": "Failed to load reference image"
            }, status_code=500)

        height, width = img.shape[:2]

        # Apply rotation if configured
        service = get_service()
        rotation = service.config.get('detection', {}).get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))

        # Extract and save analog images
        data_path = Path('/data')
        rois_data = []

        for i, roi in enumerate(submission.rois):
            # Convert normalized coords to pixels
            px_x = int(roi.x * width)
            px_y = int(roi.y * height)
            px_w = int(roi.width * width)
            px_h = int(roi.height * height)

            # Clamp to image bounds
            px_x = max(0, min(px_x, width - 1))
            px_y = max(0, min(px_y, height - 1))
            px_w = min(px_w, width - px_x)
            px_h = min(px_h, height - px_y)

            # Extract ROI
            roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

            # Save analog image
            analog_path = data_path / f'analog_{i + 1}.jpg'
            cv2.imwrite(str(analog_path), roi_img)

            rois_data.append({
                'x': roi.x,
                'y': roi.y,
                'width': roi.width,
                'height': roi.height
            })

        # Save to config
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' not in config:
            config['detection'] = {}

        config['detection']['analogs'] = {
            'count': submission.count,
            'rois': rois_data
        }

        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

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


@app.delete("/api/roi/analogs")
async def delete_analogs():
    """Delete analog ROIs from config."""
    try:
        config_path = Path('config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        if 'detection' in config and 'analogs' in config['detection']:
            count = config['detection']['analogs'].get('count', 0)
            del config['detection']['analogs']

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            service = get_service()
            service.config = config

            # Delete analog images
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


@app.get("/api/roi/analog-image/{analog_id}")
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


@app.post("/api/roi/analog-preview")
async def preview_analog_inference(submission: SingleRoiSubmission):
    """Run inference on a single analog ROI and return the prediction."""
    try:
        import tempfile

        # Load reference image
        reference_path = Path('/data/reference_raw.jpg')
        if not reference_path.exists():
            return JSONResponse({
                "success": False,
                "message": "Reference image not found"
            }, status_code=400)

        img = cv2.imread(str(reference_path))
        if img is None:
            return JSONResponse({
                "success": False,
                "message": "Failed to load reference image"
            }, status_code=500)

        height, width = img.shape[:2]

        # Apply rotation if configured
        service = get_service()
        rotation = service.config.get('detection', {}).get('rotation', 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))

        # Extract ROI
        px_x = int(submission.x * width)
        px_y = int(submission.y * height)
        px_w = int(submission.width * width)
        px_h = int(submission.height * height)

        px_x = max(0, min(px_x, width - 1))
        px_y = max(0, min(px_y, height - 1))
        px_w = min(px_w, width - px_x)
        px_h = min(px_h, height - px_y)

        roi_img = img[px_y:px_y + px_h, px_x:px_x + px_w]

        # Save to temp file for inference
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name
            cv2.imwrite(temp_path, roi_img)

        try:
            result = arrows_classifier.predict(temp_path)
            prediction = result['class']
            confidence = result['confidence']
        finally:
            Path(temp_path).unlink(missing_ok=True)

        # Encode ROI image as base64 for preview
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


@app.get("/api/label/next-image")
async def get_next_unlabeled_image():
    """Get the next unlabeled image, prioritizing digits over arrows."""
    service = get_service()
    training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

    # Collect all unlabeled images, excluding _next.jpg helper images
    digits_images = [p for p in (training_path / 'digits' / 'input').glob('*.jpg')
                     if not p.name.endswith('_next.jpg')]
    arrows_images = [p for p in (training_path / 'arrows' / 'input').glob('*.jpg')
                     if not p.name.endswith('_next.jpg')]

    # Prioritize digits, then arrows; randomize within each category
    if digits_images:
        random.shuffle(digits_images)
        image_path = digits_images[0]
        model_type = 'digits'
    elif arrows_images:
        random.shuffle(arrows_images)
        image_path = arrows_images[0]
        model_type = 'arrows'
    else:
        return JSONResponse({
            "has_images": False,
            "message": "No unlabeled images available"
        })

    # Read and encode image
    image_data = image_path.read_bytes()
    image_base64 = base64.b64encode(image_data).decode('utf-8')

    # Check for corresponding _next.jpg helper image
    next_image_base64 = None
    next_image_path = image_path.with_name(image_path.stem + '_next.jpg')
    if next_image_path.exists():
        next_image_data = next_image_path.read_bytes()
        next_image_base64 = base64.b64encode(next_image_data).decode('utf-8')

    # Count remaining images
    remaining_digits = len(digits_images)
    remaining_arrows = len(arrows_images)

    response_data = {
        "has_images": True,
        "filename": image_path.name,
        "model_type": model_type,
        "image_base64": image_base64,
        "remaining": {
            "digits": remaining_digits,
            "arrows": remaining_arrows,
            "total": remaining_digits + remaining_arrows
        }
    }

    if next_image_base64:
        response_data["next_image_base64"] = next_image_base64

    return JSONResponse(response_data)


@app.post("/api/label/submit")
async def submit_label(submission: LabelSubmission):
    """Submit a label and move the image to the ground truth folder."""
    try:
        service = get_service()
        training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

        # Source path
        source_path = training_path / submission.model_type / 'input' / submission.filename

        if not source_path.exists():
            return JSONResponse({
                "success": False,
                "message": f"Image not found: {submission.filename}"
            }, status_code=404)

        # Validate label
        if submission.model_type == 'digits':
            # Valid labels: 0-9, NAN
            valid_labels = [str(i) for i in range(10)] + ['NAN']
            if submission.label not in valid_labels:
                return JSONResponse({
                    "success": False,
                    "message": f"Invalid label for digits: {submission.label}. Must be 0-9 or NAN"
                }, status_code=400)
            label_folder = submission.label

        elif submission.model_type == 'arrows':
            # Valid labels: decimal 0.0 to 9.9 (e.g., "1.2", "6.9", "3.3")
            try:
                # Try parsing as float (decimal format)
                label_value = float(submission.label)
                if label_value < 0.0 or label_value > 9.9:
                    raise ValueError("Out of range")
                # Format to one decimal place
                label_folder = f"{label_value:.1f}"
            except ValueError:
                # Fallback: try old integer format (0-99) for backward compatibility
                try:
                    value = int(submission.label)
                    if value < 0 or value > 99:
                        raise ValueError("Out of range")
                    # Convert to decimal format: 12 -> 1.2
                    label_value = value / 10.0
                    label_folder = f"{label_value:.1f}"
                except ValueError:
                    return JSONResponse({
                        "success": False,
                        "message": f"Invalid label for arrows: {submission.label}. Must be 0.0-9.9 (e.g., 1.2, 6.9)"
                    }, status_code=400)
        else:
            return JSONResponse({
                "success": False,
                "message": f"Invalid model type: {submission.model_type}"
            }, status_code=400)

        # Destination path
        dest_dir = training_path / submission.model_type / 'ground_truth' / label_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / submission.filename

        # Move file
        shutil.move(str(source_path), str(dest_path))

        logger.info(f"Labeled image moved: {source_path} -> {dest_path}")

        # Delete corresponding _next.jpg helper image if it exists
        next_image_path = source_path.with_name(source_path.stem + '_next.jpg')
        if next_image_path.exists():
            next_image_path.unlink()
            logger.info(f"Deleted helper image: {next_image_path}")

        return JSONResponse({
            "success": True,
            "message": f"Image labeled as {label_folder}",
            "label": label_folder
        })

    except Exception as e:
        logger.error(f"Error submitting label: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/label/delete")
async def delete_image(submission: DeleteSubmission):
    """Delete an image (garbage/unusable)."""
    try:
        service = get_service()
        training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

        # Source path
        source_path = training_path / submission.model_type / 'input' / submission.filename

        if not source_path.exists():
            return JSONResponse({
                "success": False,
                "message": f"Image not found: {submission.filename}"
            }, status_code=404)

        # Delete file
        source_path.unlink()

        logger.info(f"Deleted garbage image: {source_path}")

        # Delete corresponding _next.jpg helper image if it exists
        next_image_path = source_path.with_name(source_path.stem + '_next.jpg')
        if next_image_path.exists():
            next_image_path.unlink()
            logger.info(f"Deleted helper image: {next_image_path}")

        return JSONResponse({
            "success": True,
            "message": f"Image deleted: {submission.filename}"
        })

    except Exception as e:
        logger.error(f"Error deleting image: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.get("/health")
def health():
    """Health check endpoint."""
    service = get_service()
    return {
        "status": "ok",
        "mqtt_connected": service.mqtt_client.is_connected() if service.mqtt_client else False,
        "last_update": service.current_state.get('last_update'),
        "processing": service.current_state['processing']
    }


# ============================================================================
# Training API Endpoints
# ============================================================================

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


@app.get("/api/training/status")
async def get_training_status():
    """Get the status of the active training job."""
    training_mgr = get_training_manager()
    training_status = training_mgr.get_training_status()
    benchmark_status = training_mgr.get_benchmark_status()

    return JSONResponse({
        "training": training_status,
        "benchmark": benchmark_status
    })


@app.post("/api/training/start")
async def start_training(config: TrainingConfig):
    """Start a new training job."""
    try:
        training_mgr = get_training_manager()
        job_id = training_mgr.start_training(config.dict())

        return JSONResponse({
            "success": True,
            "job_id": job_id,
            "message": "Training started successfully"
        })

    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=409)
    except Exception as e:
        logger.error(f"Error starting training: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/training/cancel")
async def cancel_training(job_id: str):
    """Cancel a running training job."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.cancel_training(job_id)

        if success:
            return JSONResponse({
                "success": True,
                "message": "Training cancellation requested"
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


@app.get("/api/training/logs/{job_id}")
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


@app.get("/api/training/progress/{job_id}")
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


# ============================================================================
# Model Management API Endpoints
# ============================================================================

@app.get("/api/models")
async def list_models(model_type: str = None):
    """
    List all models, optionally filtered by type.

    Query params:
        model_type: "digits" or "arrows" (optional)
    """
    try:
        model_mgr = get_model_manager()

        if model_type:
            models = model_mgr.list_models(model_type)
            return JSONResponse({
                "success": True,
                "models": models
            })
        else:
            # Get both types
            digits_models = model_mgr.list_models("digits")
            arrows_models = model_mgr.list_models("arrows")

            return JSONResponse({
                "success": True,
                "models": {
                    "digits": digits_models,
                    "arrows": arrows_models
                }
            })

    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.get("/api/models/{model_type}/{model_id}")
async def get_model_details(model_type: str, model_id: str):
    """Get detailed information about a specific model."""
    try:
        model_mgr = get_model_manager()
        model = model_mgr.get_model(model_type, model_id)

        if model:
            return JSONResponse({
                "success": True,
                "model": model
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Model not found"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error getting model details: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/models/{model_type}/{model_id}/activate")
async def activate_model(model_type: str, model_id: str):
    """Activate a model (set it as the active model in config and reload)."""
    try:
        model_mgr = get_model_manager()
        config_path = Path("config.yaml")

        # Activate in config
        success = model_mgr.activate_model(model_type, model_id, config_path)

        if not success:
            return JSONResponse({
                "success": False,
                "message": "Failed to activate model"
            }, status_code=500)

        # Reload config
        with open(config_path, 'r') as f:
            new_config = yaml.safe_load(f)

        # Hot-reload inference service
        inference_svc = get_inference_service()
        inference_svc.reload_models(new_config)

        # Update watermeter service config
        service = get_service()
        service.config = new_config

        logger.info(f"Model activated and reloaded: {model_type}/{model_id}")

        return JSONResponse({
            "success": True,
            "message": f"Model {model_id} activated successfully"
        })

    except Exception as e:
        logger.error(f"Error activating model: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/models/{model_type}/{model_id}/archive")
async def archive_model(model_type: str, model_id: str):
    """Archive a model (mark as archived in metadata)."""
    try:
        model_mgr = get_model_manager()
        success = model_mgr.archive_model(model_type, model_id)

        if success:
            return JSONResponse({
                "success": True,
                "message": f"Model {model_id} archived successfully"
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Model not found"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error archiving model: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.delete("/api/models/{model_type}/{model_id}")
async def delete_model(model_type: str, model_id: str):
    """Delete a model and all its files."""
    try:
        model_mgr = get_model_manager()
        success = model_mgr.delete_model(model_type, model_id)

        if success:
            return JSONResponse({
                "success": True,
                "message": f"Model {model_id} deleted successfully"
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Model not found"
            }, status_code=404)

    except Exception as e:
        logger.error(f"Error deleting model: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.post("/api/models/{model_type}/{model_id}/benchmark")
async def start_benchmark(model_type: str, model_id: str):
    """Start a benchmark job for a specific model."""
    try:
        training_mgr = get_training_manager()
        job_id = training_mgr.start_benchmark(model_type, model_id)

        return JSONResponse({
            "success": True,
            "job_id": job_id,
            "message": "Benchmark started successfully"
        })

    except RuntimeError as e:
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=409)
    except Exception as e:
        logger.error(f"Error starting benchmark: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@app.get("/api/training-data/stats")
async def get_training_data_stats():
    """Get statistics about available training data."""
    try:
        service = get_service()
        training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

        stats = {
            "digits": {},
            "arrows": {}
        }

        # Count images per class for digits
        digits_gt_path = training_path / 'digits' / 'ground_truth'
        if digits_gt_path.exists():
            for class_dir in digits_gt_path.iterdir():
                if class_dir.is_dir():
                    count = len(list(class_dir.glob('*.jpg')))
                    stats["digits"][class_dir.name] = count

        # Count images per class for arrows
        arrows_gt_path = training_path / 'arrows' / 'ground_truth'
        if arrows_gt_path.exists():
            for class_dir in arrows_gt_path.iterdir():
                if class_dir.is_dir():
                    count = len(list(class_dir.glob('*.jpg')))
                    stats["arrows"][class_dir.name] = count

        # Count unlabeled images
        digits_input = training_path / 'digits' / 'input'
        arrows_input = training_path / 'arrows' / 'input'

        unlabeled = {
            "digits": len(list(digits_input.glob('*.jpg'))) if digits_input.exists() else 0,
            "arrows": len(list(arrows_input.glob('*.jpg'))) if arrows_input.exists() else 0
        }

        return JSONResponse({
            "success": True,
            "ground_truth": stats,
            "unlabeled": unlabeled
        })

    except Exception as e:
        logger.error(f"Error getting training data stats: {e}")
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


if __name__ == "__main__":
    import uvicorn
    import yaml

    # Load config for port
    with open("config.yaml", 'r') as f:
        config = yaml.safe_load(f)

    host = config['dashboard']['host']
    port = config['dashboard']['port']

    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
