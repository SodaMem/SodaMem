"""Card selection + text rendering, and the answer-evidence-bundle projector.

Also holds the glue for the `build_context()` facade's `compact_cards(...)`
call shape (§6.2 skeleton).

Two jobs live in this one module deliberately. Evidence selection used to
exist as two independently written implementations of the same job — picking
the most useful evidence for a downstream reader. Rather than keep a second
selection algorithm here, `project_answer_bundle` below reuses
`EvidenceStore.ranked_records` (the exact ranking `compact_cards` uses) via a
scratch `EvidenceStore` built from `eligible_evidence`. The second algorithm's
term-coverage/dedup variant is gone on purpose — this is a consolidation, not
an oversight.
"""
from __future__ import annotations

from typing import Any

from sodamem.context.store import EvidenceStore

# ---------------------------------------------------------------------------
# compact_cards(): the build_context() facade's selection + rendering step.
# ---------------------------------------------------------------------------


def compact_cards(
    store: EvidenceStore,
    *,
    preferred_ids: list[str] | None = None,
    newest_step: int | None = None,
    query: str = "",
    token_budget: int | None = None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """`build_context()`'s card-selection + text-rendering step.

    Two layers, one selector — not a parallel second one:

    1. Selection: `store.compact_cards(...)`. This is the assembly parity
       gate's target (`tests/test_context_assembly_parity.py`): replaying the
       same tool payloads through `EvidenceStore.ingest()` and calling this
       method must reproduce a known card list exactly.
    2. Rendering: turns the selected card list into a flat prompt string
       outside the planner loop. Renders to newline-delimited text, stopping
       once `token_budget` (approximated as `token_budget * 4` characters —
       the same order-of-magnitude token/char ratio used elsewhere for token
       accounting) is exhausted. `token_budget=None` renders every selected
       card.
    """
    cards = store.compact_cards(preferred_ids=preferred_ids, newest_step=newest_step, query=query)
    text, rendered = _render_cards(cards, token_budget=token_budget)
    # T8-review Critical fix: citations/evidence derive from the RENDERED subset,
    # never the pre-truncation list — a citation for evidence absent from the
    # text is a lie the reader will repeat downstream.
    citations = [str(card["evidence_id"]) for card in rendered if card.get("evidence_id")]
    return text, rendered, citations


def _render_cards(
    cards: list[dict[str, Any]], *, token_budget: int | None
) -> tuple[str, list[dict[str, Any]]]:
    """Returns (text, included_cards): the exact subset that made it into the
    text, so callers can keep citations honest under truncation."""
    char_budget = None if token_budget is None else max(0, int(token_budget) * 4)
    lines: list[str] = []
    included: list[dict[str, Any]] = []
    used = 0
    for card in cards:
        line = "- " + " ".join(f"{key}={value}" for key, value in card.items())
        if char_budget is not None and used + len(line) > char_budget:
            break
        lines.append(line)
        included.append(card)
        used += len(line) + 1
    return "\n".join(lines), included


# ---------------------------------------------------------------------------
__all__ = ["compact_cards"]
