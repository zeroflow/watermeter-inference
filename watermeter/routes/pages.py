"""HTML page routes and template-based fragments."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import watermeter_service
from ..inference import get_inference_service

router = APIRouter()

_pkg_dir = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_pkg_dir / "templates"))


@router.get(
    "/",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="Dashboard page",
    description="Render the main dashboard page showing current meter reading and status",
)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    # First-run detection: no reference image -> redirect to setup wizard
    if not Path("/data/reference_raw.jpg").exists():
        return RedirectResponse("/roi-config?setup=1", status_code=303)

    inference_svc = get_inference_service()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={
            "nav_active": "dashboard",
            "models_loaded": inference_svc.models_loaded,
        },
    )


@router.get(
    "/label",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="Labeling interface",
    description="Render the labeling interface page for manually labeling training images",
)
async def label_page(request: Request):
    """Render the labeling interface page."""
    return templates.TemplateResponse(request, "label.html", context={"nav_active": "label"})


@router.get(
    "/roi-config",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="ROI configuration page",
    description="Render the ROI configuration page for setting up detection regions",
)
async def roi_config_page(request: Request):
    """Render the ROI configuration page."""
    service = watermeter_service.get_service()
    setup_mode = request.query_params.get("setup") == "1"
    image_src = service.config.get("images", {}).get("src", "")
    return templates.TemplateResponse(
        request,
        "roi_config.html",
        context={
            "nav_active": "roi",
            "setup_mode": setup_mode,
            "image_src": image_src,
        },
    )


@router.get(
    "/config-editor",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="Configuration editor",
    description="Render the config editor page with Monaco editor for YAML editing",
)
async def config_editor_page(request: Request):
    """Render the config editor page with Monaco editor."""
    return templates.TemplateResponse(request, "config_editor.html", context={"nav_active": "config"})


@router.get(
    "/training",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="Training management page",
    description="Render the training management page for model training and benchmarking",
)
async def training_page(request: Request):
    """Render the training management page."""
    return templates.TemplateResponse(request, "training.html", context={"nav_active": "training"})


@router.get(
    "/training/explore",
    response_class=HTMLResponse,
    tags=["Pages"],
    summary="Explore & Tune page",
    description="Render the Explore & Tune page for browsing training images and running data tools",
)
async def explore_page(request: Request):
    """Render the Explore & Tune page."""
    return templates.TemplateResponse(request, "explore.html", context={"nav_active": "training"})


@router.get(
    "/api/status/html",
    response_class=HTMLResponse,
    tags=["Status & Reading"],
    summary="Get status HTML fragment",
    description="Get current status as HTML fragment for HTMX dynamic updates",
)
async def get_status_html(request: Request):
    """Get current status as HTML fragment for HTMX."""
    service = watermeter_service.get_service()
    # Refresh pipeline_status / is_live / age before rendering so the badge
    # reflects current health even between processing cycles (Issue #2).
    if hasattr(service, "_derive_pipeline_status"):
        service._derive_pipeline_status()
    # CRITICAL: Create a copy of current_state to prevent pollution.
    # Starlette's TemplateResponse mutates the context dict by adding a "request" key,
    # so passing current_state directly would inject a non-serializable Request object
    # into the shared state, breaking /api/status JSON serialization.
    state = dict(service.current_state)
    state["mqtt_connected"] = service.mqtt_client.is_connected() if service.mqtt_client else False

    return templates.TemplateResponse(
        request,
        "status_fragment.html",
        context=state,
    )
