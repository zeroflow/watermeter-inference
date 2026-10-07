"""Config management routes."""

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import config_utils, watermeter_service

logger = logging.getLogger(__name__)

router = APIRouter()


class ConfigSaveSubmission(BaseModel):
    """Request model for config save."""

    content: str
    save_option: str = "saveonly"  # "saveonly" or "restart"


@router.get(
    "/api/config",
    tags=["Configuration"],
    summary="Get configuration",
    description="Get the current config.yaml file content with comments preserved",
)
async def get_config():
    """Get the current config as YAML string (with comments preserved)."""
    try:
        config_path = Path("config.yaml")
        if not config_path.exists():
            return JSONResponse({"success": False, "message": "Config file not found"}, status_code=404)

        # Read raw file to preserve comments
        content = config_path.read_text(encoding="utf-8")

        return JSONResponse({"success": True, "content": content})

    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/config/save",
    tags=["Configuration"],
    summary="Save configuration",
    description="Save modified config.yaml with validation and optional service restart",
)
async def save_config(submission: ConfigSaveSubmission):
    """Save config with comment preservation."""
    try:
        # Validate the YAML first
        validation = config_utils.validate_config(submission.content)
        if not validation["valid"]:
            return JSONResponse(
                {"success": False, "message": f"Invalid config: {validation['error']}"}, status_code=400
            )

        # Parse to verify it's valid YAML (ruamel.yaml preserves comments)
        config = config_utils.load_config_string(submission.content)

        # Save to file
        config_path = Path("config.yaml")
        config_utils.save_config(config, config_path)

        logger.info(f"Config saved (option: {submission.save_option})")

        # Reload config into running service
        with open(config_path, "r") as f:
            plain_config = yaml.safe_load(f)

        service = watermeter_service.get_service()
        reload_result = service.reload_config(plain_config)

        message = "Config saved and applied"
        if reload_result.get("mqtt_reconnected"):
            message += " (MQTT reconnected)"

        return JSONResponse({"success": True, "message": message})

    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/config/schema.json",
    tags=["Configuration"],
    summary="Get configuration schema",
    description="Get JSON schema for config validation (used by Monaco editor for autocomplete)",
)
async def get_config_schema():
    """Get JSON schema for config validation (used by Monaco editor)."""
    return JSONResponse(config_utils.get_config_schema())


ARROWS_MODES = ("model", "opencv", "calibrated")


class ArrowsModeSubmission(BaseModel):
    """Request model for switching the arrow reading backend."""

    mode: str


@router.post(
    "/api/config/arrows-mode",
    tags=["Configuration"],
    summary="Switch arrow reading mode",
    description="Set inference.arrows_mode ('model', 'opencv' or 'calibrated'), keep comments, apply immediately",
)
async def set_arrows_mode(submission: ArrowsModeSubmission):
    """Switch inference.arrows_mode and hot-reload (the arrow backend is rebuilt by reload_config)."""
    if submission.mode not in ARROWS_MODES:
        return JSONResponse(
            {"success": False, "message": f"mode must be one of {', '.join(ARROWS_MODES)}"}, status_code=400
        )
    try:
        config_path = Path("config.yaml")

        def _update(config):
            config.setdefault("inference", {})["arrows_mode"] = submission.mode

        config_utils.update_config(config_path, _update)
        with open(config_path, "r") as f:
            plain_config = yaml.safe_load(f)
        watermeter_service.get_service().reload_config(plain_config)
        logger.info(f"Arrow reading mode set to {submission.mode}")
        return JSONResponse({"success": True, "mode": submission.mode})
    except Exception as e:
        logger.error(f"Error switching arrows mode: {e}")
        return JSONResponse({"success": False, "message": f"Error: {e}"}, status_code=500)
