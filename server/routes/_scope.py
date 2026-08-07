"""Shared scope helper for the v1 routes (PRD R1.2).

`agent_id` / `run_id` / `project_id` are now IMPLEMENTED on the paths the core supports —
ingest stamps them into fact metadata, and retrieval narrows on them:

    POST /v1/memories   stamps the scope onto every fact it extracts
    POST /v1/search     narrows
    GET  /v1/context    narrows (it is search + rendering)

They are still NOT implemented on:

    GET  /v1/memories       (enumerates the store directly, no scope filter)
    GET/DELETE /v1/memories/{id}   (id-addressed; scope adds nothing)
    POST /v1/answer         (the planner's MemoryTool takes no scope yet)
    GET  /v1/events         (traces carry no scope stamp)

Those keep raising a loud 501. Partial support is only honest if the gap is
explicit per-route — a blanket "supported" that silently ignores the keys on
four of seven routes is exactly the silent degradation this project refuses.

Semantics (deliberately NOT mem0's strict AND): a fact with no scope stamp
matches every scoped query. The stamp records PROVENANCE — which agent/run
contributed a fact — while ownership is already settled by the per-user
store. Strict AND would make every pre-existing memory vanish the moment a
caller passes agent_id, which from the outside is indistinguishable from
data loss. See sodamem.memory.retrieval.search._stamp_matches.

Scope now covers RAW TURNS as well as facts (R1.2b). It is recorded once per
ingest session in `session_scope`, and both card types resolve through it —
previously only facts carried a stamp, so raw-recall rows passed every scoped
query and narrowing to one project still surfaced another project's raw
conversation text.

LIMIT — this is still narrowing, not isolation. An unstamped fact matches
every scoped query by design, and any caller can pass any scope key, so
`agent_id` must not be read as "agent B cannot see anything from agent A".
Ownership is settled by the per-user store; these keys are provenance.
"""
from __future__ import annotations

from fastapi import HTTPException


def reject_unsupported_scope(*values: str | None) -> None:
    """For routes where scope is still unimplemented.

    Varargs, not one named parameter per key: `project_id` (R1.2b) would
    otherwise have meant editing seven call sites to thread a third argument
    whose only job is to be rejected. The caller passes whatever scope keys
    its signature accepts; this refuses if any of them is set.
    """
    if any(v is not None for v in values):
        raise HTTPException(
            status_code=501,
            detail=(
                "agent_id/run_id/project_id scoping is not implemented on this "
                "route yet (a real TODO, not a silently-ignored parameter). It "
                "IS supported on POST /v1/memories, POST /v1/search and "
                "GET /v1/context."
            ),
        )
