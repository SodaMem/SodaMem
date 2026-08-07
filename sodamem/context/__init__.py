"""build_context(): the context-assembly facade. Default path is zero-LLM
(I5's spirit extended one layer up — organizer=None just dedupes/ranks/
renders within token_budget). Passing `organizer` opts into the LLM-backed
query-plan classification + value_board/enumeration_sweep organizers.

This is the point where the deleted `_classify_evidence_contract` type-
routing hack (spec §6.3, 0714 user-vetoed) dissolves:
the old code had `build_context`-equivalent logic silently branch INSIDE
itself on a regex-classified question type. Here the branch is gone — there
is exactly one code path, and organizing (if any) is a value the CALLER
supplies (`organizer: Organizer | None`), not a decision this function makes
on the question's text. "特殊情况变成普通情况" (spec §6.3): the type router
does not get renamed or hidden, it stops existing as an internal branch.

`EvidenceStore`/`cards`/`organizers/*` carry the mechanics (see
`sodamem.context.store`, `sodamem.context.cards`,
`sodamem.context.organizers`); `build_context`/`ContextBlock`/`Organizer`
below are the composition layer over them (spec §6.2 skeleton).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # runtime import would make this lower layer depend on
    from sodamem import SodaMem  # the root facade — a cycle import-linter flags


class Organizer(Protocol):
    def organize(self, evidence: list[dict], *, query: str) -> list[dict]: ...


@dataclass(frozen=True)
class ContextBlock:
    text: str
    evidence: list[dict]
    citations: list[str] = field(default_factory=list)
    degraded: list[Any] = field(default_factory=list)  # sodamem.memory.retrieval.search.Degradation, propagated through


def build_context(mem: SodaMem, query: str, *, user_id: str, token_budget: int,
                   organizer: Organizer | None = None,
                   agent_id: str = "", run_id: str = "",
                   project_id: str = "") -> ContextBlock:
    """Default zero-LLM: mem.search() -> EvidenceStore dedup/rank -> compact_cards
    within token_budget. `organizer` param opts into the LLM-backed
    value_board/enumeration_sweep path (see organizers/query_plan.py's
    reader_query_plan for the classifier that picks which organizer to use —
    THIS caller-selects-the-organizer shape is what makes the old
    "_classify_evidence_contract" type-routing hack disappear per spec §6.3:
    "特殊情况变成普通情况"— not an internal branch, a caller choice)."""
    from sodamem.context.store import EvidenceStore
    from sodamem.context.cards import compact_cards

    result = mem.search(query, user_id=user_id, agent_id=agent_id, run_id=run_id,
                        project_id=project_id)
    store = EvidenceStore()
    for row in result.evidence:
        store.ingest(tool="search", args={"query": query}, payload=row, step=0)
    ranked_ids = store.reader_rows()
    if organizer is not None:
        ranked_ids = organizer.organize(ranked_ids, query=query)
    text, evidence, citations = compact_cards(
        store, preferred_ids=[r["id"] for r in ranked_ids], newest_step=0,
        query=query, token_budget=token_budget,
    )
    return ContextBlock(text=text, evidence=evidence, citations=citations, degraded=result.degraded)


__all__ = ["build_context", "ContextBlock", "Organizer"]
