"""MQTT configuration routes: get/save MQTT config and test broker connection."""

import asyncio
import logging
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import config_utils, watermeter_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Importable reference to the MQTT client class — allows monkeypatching in tests.
# paho.mqtt.client is mocked in the unit test conftest, so we import it lazily
# via this module-level variable instead of at function call time.
try:
    import paho.mqtt.client as _mqtt_module

    mqtt_client_class = _mqtt_module.Client
    _mqtt_callback_api = _mqtt_module.CallbackAPIVersion.VERSION2
except Exception:  # paho not available (e.g. unit-test environment)
    mqtt_client_class = None  # type: ignore[assignment]
    _mqtt_callback_api = None


def _has_unresolved_tokens(value: str) -> bool:
    """Return True if the string contains ${...} tokens."""
    return bool(re.search(r"\$\{(\w+)\}", value))


@router.get(
    "/api/mqtt/config",
    tags=["MQTT"],
    summary="Get MQTT configuration",
    description="Returns current MQTT, trigger, and Home Assistant config with raw ${...} tokens preserved",
)
async def get_mqtt_config():
    """Return mqtt/trigger/homeassistant sections from config.yaml (raw, not resolved)."""
    try:
        config_path = Path("config.yaml")
        if not config_path.exists():
            return JSONResponse(
                {"success": False, "message": "Config file not found"},
                status_code=404,
            )

        # Read raw file via ruamel to preserve ${...} tokens
        raw_config = config_utils.load_config(config_path)

        # Extract only the relevant sections; default to empty dict if absent
        result = {
            "mqtt": dict(raw_config.get("mqtt", {})),
            "trigger": dict(raw_config.get("trigger", {})),
            "homeassistant": dict(raw_config.get("homeassistant", {})),
        }
        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Error reading MQTT config: {e}")
        return JSONResponse(
            {"success": False, "message": f"Error: {str(e)}"},
            status_code=500,
        )


@router.post(
    "/api/mqtt/config",
    tags=["MQTT"],
    summary="Save MQTT configuration",
    description="Merges mqtt/trigger/homeassistant sections into config.yaml and reloads the service",
)
async def save_mqtt_config(request: Request):
    """Save MQTT/trigger/HA config sections and reload service."""
    try:
        data = await request.json()

        config_path = Path("config.yaml")
        if not config_path.exists():
            return JSONResponse(
                {"success": False, "message": "Config file not found"},
                status_code=404,
            )

        # Load existing config with ruamel (preserves comments)
        config = config_utils.load_config(config_path)

        # Merge submitted sections
        for section in ("mqtt", "trigger", "homeassistant"):
            if section in data:
                if section not in config:
                    config[section] = {}
                for key, value in data[section].items():
                    config[section][key] = value

        # Save with comment preservation
        config_utils.save_config(config, config_path)

        # Reload with yaml.safe_load for a plain dict (required by service)
        with open(config_path, "r") as f:
            plain_config = yaml.safe_load(f)

        service = watermeter_service.get_service()
        reload_result = service.reload_config(plain_config)

        message = "MQTT config saved and applied"
        if reload_result.get("mqtt_reconnected"):
            message += " (MQTT reconnected)"

        return JSONResponse({"success": True, "message": message})

    except Exception as e:
        logger.error(f"Error saving MQTT config: {e}")
        return JSONResponse(
            {"success": False, "message": f"Error: {str(e)}"},
            status_code=500,
        )


@router.post(
    "/api/mqtt/test",
    tags=["MQTT"],
    summary="Test MQTT broker connection",
    description="Creates a temporary MQTT client and tests connectivity to the broker without affecting the running service",
)
async def test_mqtt_connection(request: Request):
    """Test MQTT broker connection with a temporary client."""
    try:
        data = await request.json()

        broker = data.get("broker", "").strip()
        if not broker:
            return JSONResponse(
                {"status": "error", "message": "Broker address is required"},
                status_code=400,
            )

        port = int(data.get("port", 1883))
        if not (1 <= port <= 65535):
            return JSONResponse(
                {"status": "error", "message": "Port must be between 1 and 65535"},
                status_code=400,
            )
        raw_username = data.get("username", "")
        raw_password = data.get("password", "")

        # Resolve ${ENV_VAR} tokens in credentials
        username = config_utils.resolve_env_vars(raw_username) if raw_username else ""
        password = config_utils.resolve_env_vars(raw_password) if raw_password else ""

        # Warn about unresolved env vars
        warnings = []
        if raw_username and _has_unresolved_tokens(username):
            warnings.append(f"Unresolved env var in username: {username}")
        if raw_password and _has_unresolved_tokens(password):
            warnings.append("Unresolved env var in password")

        # Create a temporary MQTT client
        client_class = mqtt_client_class
        if client_class is None:
            # Fallback for environments without paho (should not happen in prod)
            return JSONResponse(
                {"status": "error", "message": "paho-mqtt not available"},
                status_code=500,
            )

        try:
            if _mqtt_callback_api is not None:
                client = client_class(
                    callback_api_version=_mqtt_callback_api,
                    client_id="watermeter-test",
                )
            else:
                client = client_class(client_id="watermeter-test")
        except TypeError:
            # Mocked client_class may not accept these kwargs
            client = client_class()

        if username:
            client.username_pw_set(username, password or None)

        try:
            await asyncio.to_thread(client.connect, broker, port, 5)
            await asyncio.to_thread(client.disconnect)
        except (OSError, ConnectionRefusedError, TimeoutError) as exc:
            message = f"Connection failed: {exc}"
            if warnings:
                message += f" | Warnings: {'; '.join(warnings)}"
            return JSONResponse({"status": "error", "message": message})

        message = "Connection successful"
        if warnings:
            return JSONResponse(
                {
                    "status": "ok",
                    "message": message,
                    "warning": "; ".join(warnings),
                }
            )
        return JSONResponse({"status": "ok", "message": message})

    except Exception as e:
        logger.error(f"Error testing MQTT connection: {e}")
        return JSONResponse(
            {"status": "error", "message": f"Error: {str(e)}"},
            status_code=500,
        )
