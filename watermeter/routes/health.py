"""Public health endpoint (unauthenticated, used by the compose healthcheck, CI smoke test and integration tests)."""

from fastapi import APIRouter

from .. import watermeter_service

router = APIRouter()


@router.get(
    "/health",
    tags=["Status & Reading"],
    summary="Health check",
    description="Health check endpoint for monitoring system availability",
)
def health():
    """Health check endpoint."""
    service = watermeter_service.get_service()
    return {
        "status": "ok",
        "mqtt_connected": service.mqtt_client.is_connected() if service.mqtt_client else False,
        "last_update": service.current_state.get("last_update"),
        "processing": service.current_state["processing"],
    }
