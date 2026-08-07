"""GET /v1/metrics — per-route latency percentiles (PRD R1.13 / goal G5).

Behind `require_api_key` like every other v1 route: route-level traffic shape
is operational data about a deployment, not something to hand out with the
port. `/health` remains the only open endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter

from server.metrics import get_latency_registry
from server.models import MetricsResponse

router = APIRouter(tags=["metrics"])


@router.get("/v1/metrics", response_model=MetricsResponse)
def get_metrics() -> MetricsResponse:
    """Latency in milliseconds, per `METHOD /path`, over the most recent
    samples this process has served (bounded ring — see server/metrics.py).

    Routes with no traffic are absent rather than zero-filled: zeros would
    read as "instantaneous".
    """
    return MetricsResponse(routes=get_latency_registry().snapshot())
