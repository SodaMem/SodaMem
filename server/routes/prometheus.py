"""GET /metrics — Prometheus scrape endpoint (PRD R2.10).

Unversioned `/metrics`, not `/v1/metrics`: this path is a scraper convention,
and its contract is the exposition format's, not ours. `/v1/metrics` remains
the JSON view for humans and the console.

Behind `require_api_key` like everything but `/health`. Route-level traffic
shape and token spend are operational data about a deployment; a Prometheus
scrape config can carry a bearer token, so the cost of keeping this closed is
one line of scraper config.
"""
from __future__ import annotations

from fastapi import APIRouter, Response

from server.metrics import get_latency_registry, get_request_counter
from server.prometheus import CONTENT_TYPE, render_exposition
from server.usage import get_usage_registry

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=Response)
def scrape() -> Response:
    body = render_exposition(
        latency=get_latency_registry(),
        requests=get_request_counter(),
        usage=get_usage_registry(),
    )
    return Response(content=body, media_type=CONTENT_TYPE)
