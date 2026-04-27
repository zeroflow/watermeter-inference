"""GET /api/metrics — JSON snapshot of pipeline metrics (Issue #4)."""

from fastapi import APIRouter

from .. import watermeter_service

router = APIRouter()


@router.get(
    "/api/metrics",
    tags=["Status & Reading"],
    summary="Pipeline metrics snapshot",
    description=(
        "Returns the current pipeline observability snapshot: counters, "
        "rolling marker-confidence statistics, and 1h/24h failure rates."
    ),
)
async def get_metrics() -> dict:
    """Return the current PipelineMetrics snapshot."""
    service = watermeter_service.get_service()
    return service.get_metrics_snapshot()
