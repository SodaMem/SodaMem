"""GET /v1/entity_timeline, GET /v1/explore, POST /v1/refine (PRD R1.11).

These three retrieval shapes existed only on the MCP surface
(`mcp_server/main.py`) until now. That was a real capability gap, and a
transport-shaped one: whether you could ask "what is this entity's history"
depended on whether you had spawned a stdio subprocess or called the API. It
also blocked the MCP server's remote mode (`mcp_server/backend.py`), which
proxies every tool to this service — a tool with no route behind it could
only have been a mode-dependent hole in the tool list.

Thin wrappers over `sodamem.tools.MemoryTool`, whose methods hold the actual
logic. `MemoryTool` is documented as a SINGLE-user tool surface (it binds
`user_id` on the instance), so one is constructed per request against that
request's already-validated `user_id` — never cached and shared, which is the
mistake that would turn a per-user contract into a cross-tenant one.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from server.models import (
    EntityTimelineResponse,
    ExploreResponse,
    RefineRequest,
    RefineResponse,
)
from server.stores import get_store_manager

from ._scope import reject_unsupported_scope

router = APIRouter(tags=["retrieval"])


def _tool(user_id: str):
    from sodamem.tools import MemoryTool
    return MemoryTool(get_store_manager().get(user_id), user_id=user_id)


@router.get("/v1/entity_timeline", response_model=EntityTimelineResponse)
async def entity_timeline(
    user_id: str = Query(..., min_length=1, max_length=128),
    entity_id: str = Query(..., min_length=1),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
) -> EntityTimelineResponse:
    # Scope narrowing is NOT implemented here: MemoryTool.entity_timeline
    # walks entity mentions straight out of the store with no scope filter.
    # 501 rather than accepting and ignoring the keys — same rule the other
    # unscoped routes follow (see _scope.py).
    reject_unsupported_scope(agent_id, run_id, project_id)
    return EntityTimelineResponse(**_tool(user_id).entity_timeline(entity_id))


@router.get("/v1/explore", response_model=ExploreResponse)
async def explore(
    user_id: str = Query(..., min_length=1, max_length=128),
    start_id: str = Query(..., min_length=1),
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=25, ge=1, le=100),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
) -> ExploreResponse:
    reject_unsupported_scope(agent_id, run_id, project_id)
    return ExploreResponse(
        **_tool(user_id).explore(start_id, depth=depth, limit=limit)
    )


@router.post("/v1/refine", response_model=RefineResponse)
async def refine(request: RefineRequest) -> RefineResponse:
    reject_unsupported_scope(request.agent_id, request.run_id, request.project_id)
    return RefineResponse(**_tool(request.user_id).refine(
        request.query,
        top_k=request.top_k,
        entity=request.entity,
        session_id=request.session_id,
        min_confidence=request.min_confidence,
        occurred_from=request.occurred_from,
        occurred_to=request.occurred_to,
    ))
