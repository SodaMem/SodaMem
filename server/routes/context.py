"""GET and POST /v1/context (PRD M1 R1.5): the zero-LLM prompt-ready
evidence block.

Two verbs, ONE handler body. GET came first and remains the canonical form —
this route is a pure read and caches, logs and retries like one. POST exists
because `/v1/search` next door takes a JSON body, and everyone writing an
example reaches for the same shape twice: the README's own `curl -d` sent a
POST to this path and got back a 405. Asking a caller to remember which of
two adjacent read endpoints takes query params and which takes a body is a
special case with nothing behind it.

`SodaMem.build_context(organizer=None)` — the default, and the ONLY mode
this route may ever call — never imports `sodamem.llm` in its call path
(mem.search() -> EvidenceStore -> compact_cards, all zero-LLM per I5).
Passing an `organizer` opts into the LLM-backed value-board /
enumeration-sweep path; this route must never do that, so `organizer` is
simply never threaded through from here — there is no flag to accidentally
flip.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from sodamem.memory.retrieval.config import Degradation

from server.models import ContextRequest, ContextResponse
from server.stores import get_store_manager


router = APIRouter(tags=["context"])


def _degradation_to_dict(d: Degradation) -> dict:
    return {"code": d.code.value, "message": d.message, "details": dict(d.details)}


def _build(params: ContextRequest) -> ContextResponse:
    """The whole route. Both verbs call this and nothing else, so the two can
    never drift into answering the same question differently."""
    # Scope IS supported here (context is search + rendering); see _scope.py
    # for the routes that still 501.
    with get_store_manager().lease(params.user_id) as mem:
        block = mem.build_context(
            params.query, user_id=params.user_id, token_budget=params.token_budget,
            agent_id=params.agent_id or "", run_id=params.run_id or "",
            project_id=params.project_id or "",
        )
    return ContextResponse(
        text=block.text,
        citations=block.citations,
        evidence=block.evidence,
        degraded=[_degradation_to_dict(d) for d in block.degraded],
    )


@router.get("/v1/context", response_model=ContextResponse)
def get_context(params: Annotated[ContextRequest, Query()]) -> ContextResponse:
    return _build(params)


@router.post("/v1/context", response_model=ContextResponse)
def post_context(params: ContextRequest) -> ContextResponse:
    return _build(params)
