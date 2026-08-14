"""Build set_enumeration_board advisory from admitted evidence (MR)."""
from __future__ import annotations

import re
from typing import Any, Optional

from protocol_v1.cardinality import extract_cardinality
from protocol_v1.schema import QuestionSchema

_DONE = re.compile(
    r"\b(attended|bought|spent|led|leading|fixed|serviced|completed|raised|"
    r"purchased|downloaded|replaced|assembled|sold|viewed|acquired|own|owned|"
    r"have|had|got|received|ordered|visited)\b",
    re.I,
)
_PLAN_ONLY = re.compile(
    r"\b(plan(?:ning)?|thinking of|consider(?:ing)?|want to|going to|"
    r"would like|i[' ]?ll|i will)\b",
    re.I,
)


def needs_set_enumeration(schema: QuestionSchema, question: str = "") -> bool:
    if schema.task in {"COUNT_DISTINCT", "SUM", "ORDERED_LIST"}:
        return True
    ql = (question or "").lower()
    return bool(re.search(r"\b(how many|how much|in total|which .+ did i)\b", ql))


def is_plan_only_row(text: str) -> bool:
    """True when row looks like planning without a completed-act cue."""
    tl = (text or "").lower()
    return bool(_PLAN_ONLY.search(tl) and not _DONE.search(tl))


def count_kept_admitted(admitted: list[dict[str, Any]]) -> int:
    """Count admitted rows that would be tagged keep on the set board."""
    n = 0
    for it in admitted or []:
        if is_plan_only_row(str(it.get("content") or "")):
            continue
        n += 1
    return n


def build_set_enumeration_board(
    schema: QuestionSchema,
    admitted: list[dict[str, Any]],
    *,
    question: str = "",
    entity_names: Optional[list[str]] = None,
) -> str:
    if not needs_set_enumeration(schema, question):
        return ""

    card = extract_cardinality(question)
    lines = [
        "set_enumeration_board:",
        "- Write an item_list first; then derive the count/sum from that list only.",
    ]
    if card:
        lines.append(
            f"- Question asserts N={card}. If item_list length < {card}, "
            "KEEP SEARCHING; do not finalize a complete answer."
        )

    # Materialize candidate rows
    rows: list[str] = []
    for i, it in enumerate(admitted[:30], 1):
        text = str(it.get("content") or "")
        d = it.get("event_date") or "?"
        snip = " ".join(text.split())[:120]
        tag = "EXCLUDE-plan-only" if is_plan_only_row(text) else "keep"
        rows.append(f"  {i}. [{d}] ({tag}) {snip}")

    if rows:
        lines.append("- Candidate memory rows (gate plan-only):")
        lines.extend(rows)
    else:
        lines.append(
            "- No admitted rows yet — run additional searches for the predicate "
            f"before answering ({schema.predicate!r})."
        )

    if entity_names:
        lines.append(
            "- Distinct entity names (merge aliases; count once each): "
            + ", ".join(entity_names)
        )
        lines.append(f"- Entity count hint = {len(entity_names)}")

    if schema.task == "SUM":
        lines.append(
            "- For totals: list each amount with its role (raise/donate vs "
            "buy/mortgage/workshop/salary); sum ONLY amounts matching the "
            "question predicate."
        )

    lines.append(
        "- Final integer/total must equal the length (COUNT) or sum (SUM) of "
        "the kept item_list after excludes."
    )
    return "\n".join(lines)
