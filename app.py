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
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import logging

from watermeter_service import get_service

logger = logging.getLogger(__name__)


class TrainingSubmission(BaseModel):
    """Request model for training submissions."""
    id: str
    image_base64: str
    model: str


class LabelSubmission(BaseModel):
    """Request model for label submissions."""
    filename: str
    model_type: str
    label: str


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


@app.get("/api/label/next-image")
async def get_next_unlabeled_image():
    """Get the next unlabeled image, prioritizing digits over arrows."""
    service = get_service()
    training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

    # Collect all unlabeled images
    digits_images = list((training_path / 'digits' / 'input').glob('*.jpg'))
    arrows_images = list((training_path / 'arrows' / 'input').glob('*.jpg'))

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

    # Count remaining images
    remaining_digits = len(digits_images)
    remaining_arrows = len(arrows_images)

    return JSONResponse({
        "has_images": True,
        "filename": image_path.name,
        "model_type": model_type,
        "image_base64": image_base64,
        "remaining": {
            "digits": remaining_digits,
            "arrows": remaining_arrows,
            "total": remaining_digits + remaining_arrows
        }
    })


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
