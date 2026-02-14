"""HTML page routes and template-based fragments."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import watermeter_service

router = APIRouter()

_pkg_dir = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_pkg_dir / "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/label", response_class=HTMLResponse)
async def label_page(request: Request):
    """Render the labeling interface page."""
    return templates.TemplateResponse("label.html", {"request": request})


@router.get("/roi-config", response_class=HTMLResponse)
async def roi_config_page(request: Request):
    """Render the ROI configuration page."""
    return templates.TemplateResponse("roi_config.html", {"request": request})


@router.get("/config-editor", response_class=HTMLResponse)
async def config_editor_page(request: Request):
    """Render the config editor page with Monaco editor."""
    return templates.TemplateResponse("config_editor.html", {"request": request})


@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    """Render the training management page."""
    return templates.TemplateResponse("training.html", {"request": request})


@router.get("/api/status/html", response_class=HTMLResponse)
async def get_status_html(request: Request):
    """Get current status as HTML fragment for HTMX."""
    service = watermeter_service.get_service()
    state = service.current_state

    return templates.TemplateResponse(
        "status_fragment.html",
        {
            "request": request,
            **state  # Unpack all state variables
        }
    )
