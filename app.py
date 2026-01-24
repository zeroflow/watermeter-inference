"""
FastAPI Web Application for Water Meter Dashboard
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import logging

from watermeter_service import get_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI app."""
    # Startup
    logger.info("Starting Water Meter Dashboard...")
    service = get_service()
    service.start_mqtt()
    logger.info("Water Meter Dashboard started")

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
