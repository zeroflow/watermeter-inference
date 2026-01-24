"""
FastAPI Web Application for Water Meter Dashboard
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
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
        save_path = service.config.get('low_confidence', {}).get('save_path', '/var/ml/label-studio-data/import')

        # Create directory structure: save_path/{model}/
        model_dir = Path(save_path) / submission.model
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
