"""GET /v1/usage (PRD R2.10) — what this deployment has spent on LLM calls.

Behind `require_api_key`: spend is commercially sensitive, and PRD §1.3 notes
we intend to PUBLISH aggregate write-path cost as a differentiator — that is a
decision to make deliberately, not a side effect of leaving the endpoint open.
"""
from __future__ import annotations

from fastapi import APIRouter

from server.models import UsageResponse
from server.usage import get_usage_registry

router = APIRouter(tags=["usage"])


@router.get("/v1/usage", response_model=UsageResponse)
def get_usage() -> UsageResponse:
    """Cumulative token accounting since this process started, split by
    operation. In-process and non-persistent — same lifetime as /v1/metrics;
    a restart zeroes it, which is why it reports counters, not a bill.
    """
    snap = get_usage_registry().snapshot()
    return UsageResponse(by_operation=snap["by_operation"], total=snap["total"])
