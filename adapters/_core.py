"""Framework-neutral tool layer (PRD R2.8).

Every agent framework wants the same three things from a memory layer — search
it, get a prompt-ready block out of it, write to it — and each wraps them in
its own decorator/class. Writing the actual logic four times is how four
adapters drift into four different behaviors.

So: the behavior lives here once, in plain functions with plain types, and
each adapter is a thin shell that hands these to its framework. A bug fixed
here is fixed in all four; a framework-specific quirk stays in that framework's
file where it belongs.

`get_context` is deliberately first-class: it is the operation mem0 and
open-source Zep do not offer, and the one an agent author actually needs
(a list of records still has to be assembled into a prompt by hand).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sodamem import SodaMem


@dataclass(frozen=True)
class MemoryTools:
    """Bound (memory, user_id) pair exposing the three operations.

    `user_id` is bound at construction rather than passed per call: an agent
    framework hands tool arguments to an LLM, and a user_id the model can
    choose is a user_id the model can get wrong — cross-tenant reads by
    hallucination. Scope stays in the application's hands.
    """

    memory: SodaMem
    user_id: str
    agent_id: str = ""
    run_id: str = ""
    project_id: str = ""

    # -- read ---------------------------------------------------------------
    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Ranked memories matching `query`. Returns raw records — prefer
        `get_context` when the result is headed for a prompt."""
        from sodamem.memory.retrieval.config import RetrievalConfig
        result = self.memory.search(
            query, user_id=self.user_id,
            config=RetrievalConfig(default_limit=max(1, min(top_k, 100))),
            agent_id=self.agent_id, run_id=self.run_id,
            project_id=self.project_id,
        )
        if result.degraded:
            # Surfaced, not swallowed: a caller that ignores this gets exactly
            # the risk it chose, never a hidden one.
            for d in result.degraded:
                _log_degraded(d)
        return list(result.evidence)

    def get_context(self, query: str, token_budget: int = 2000) -> dict[str, Any]:
        """Prompt-ready evidence block plus the citations the text actually
        contains. Zero LLM calls."""
        block = self.memory.build_context(
            query, user_id=self.user_id, token_budget=token_budget,
            agent_id=self.agent_id, run_id=self.run_id,
            project_id=self.project_id,
        )
        return {
            "text": block.text,
            "citations": list(block.citations),
            "evidence_count": len(block.evidence),
        }

    # -- write --------------------------------------------------------------
    def add(self, messages: list[dict[str, str]], session_id: str,
            session_time: Any = None) -> dict[str, Any]:
        """Extract and store facts from a conversation slice."""
        import time as _time
        result = self.memory.ingest(
            messages, user_id=self.user_id, session_id=session_id,
            session_time=session_time if session_time is not None else _time.time(),
            agent_id=self.agent_id, run_id=self.run_id,
            project_id=self.project_id,
        )
        counts = getattr(result, "counts", {}) or {}
        return {"session_id": session_id,
                "facts_extracted": counts.get("fact_events_in_session", 0)}


def _log_degraded(d) -> None:
    import logging
    logging.getLogger(__name__).warning(
        "sodamem retrieval degraded: %s — %s",
        getattr(getattr(d, "code", None), "value", "unknown"),
        getattr(d, "message", ""),
    )


# --- shared tool descriptions ----------------------------------------------
# One wording, four frameworks. These strings are what the MODEL reads to
# decide whether to call a tool, so drift between adapters is drift in agent
# behavior — not a cosmetic inconsistency.

SEARCH_DESCRIPTION = (
    "Search the user's long-term memory for facts relevant to a query. "
    "Returns ranked memory records with their evidence. Use get_memory_context "
    "instead when you want text to paste into a prompt."
)
CONTEXT_DESCRIPTION = (
    "Get a prompt-ready block of the user's relevant long-term memories, "
    "already deduplicated, ranked, time-annotated and trimmed to a token "
    "budget, with citations for exactly the evidence the text contains. "
    "This is the preferred read: it needs no LLM call and no assembly."
)
ADD_DESCRIPTION = (
    "Store a slice of conversation in the user's long-term memory. Facts are "
    "extracted and grounded to their source turns."
)
