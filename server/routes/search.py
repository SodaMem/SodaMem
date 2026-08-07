"""POST /v1/search (PRD M1 R1.1). Thin wrapper over `SodaMem.search()` —
`SearchResult.degraded` is mapped through verbatim (zero silent
degradation): a caller that ignores `degraded` gets exactly the risk it
chose, never a hidden one.
"""
from __future__ import annotations

from fastapi import APIRouter

from sodamem.memory.retrieval.config import Degradation, RetrievalConfig

from server.models import SearchHit, SearchRequest, SearchResponse
from server.stores import get_store_manager


router = APIRouter(tags=["search"])


def _degradation_to_dict(d: Degradation) -> dict:
    return {"code": d.code.value, "message": d.message, "details": dict(d.details)}


def _to_hit(item: dict) -> SearchHit:
    rank = item.get("rank") or {}
    return SearchHit(
        id=item.get("fact_id") or item.get("evidence_id") or "",
        content=item.get("support_text") or item.get("predicate_raw") or "",
        score=rank.get("score"),
        session_id=item.get("source_session_id"),
        occurred_at=item.get("occurred_start") or item.get("session_time"),
        metadata=item,
    )


@router.post("/v1/search", response_model=SearchResponse)
def search_memories(request: SearchRequest) -> SearchResponse:
    # Scope IS supported here; see _scope.py for the routes that still 501.
    # top_k caps eligible_evidence at the store layer (config.default_limit),
    # not by slicing the response after the fact — a smaller top_k means
    # less work done, not just less returned.
    with get_store_manager().lease(request.user_id) as mem:
        result = mem.search(
            request.query, user_id=request.user_id,
            config=RetrievalConfig(default_limit=request.top_k),
            agent_id=request.agent_id or "", run_id=request.run_id or "",
            project_id=request.project_id or "",
        )
    return SearchResponse(
        query=request.query,
        hits=[_to_hit(item) for item in result.evidence],
        degraded=[_degradation_to_dict(d) for d in result.degraded],
    )
