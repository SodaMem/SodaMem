"""TR OrderedTimeline — force (entity, date) table before order answers."""
from __future__ import annotations

import re
from typing import Any, Optional

from protocol_v1.cardinality import extract_cardinality
from protocol_v1.schema import QuestionSchema
from protocol_v1.temporal_pin import _row_date


def needs_ordered_timeline(schema: QuestionSchema, question: str = "") -> bool:
    if schema.task == "ORDERED_LIST":
        return True
    ql = (question or "").lower()
    return bool(
        re.search(r"\b(order of|in what order|from earliest|earliest to latest)\b", ql)
    )


def build_ordered_timeline_advisory(
    schema: QuestionSchema,
    admitted: list[dict[str, Any]],
    *,
    question: str = "",
    key_evidence: Optional[list[dict[str, Any]]] = None,
) -> str:
    if not needs_ordered_timeline(schema, question):
        return ""

    card = extract_cardinality(question)
    lines = [
        "protocol_v1.3 ordered_timeline:",
        "- Build a table of (entity, event_date) for every matching visit/trip/"
        "flight BEFORE stating order.",
        "- Sort by event_date ascending; the answer is that ordered entity list.",
    ]
    if card:
        lines.append(
            f"- Question asserts N={card}. If you have fewer than {card} dated "
            "rows, keep searching; do not invent or drop entities to force N."
        )

    # Collect dated candidates from admitted + key_evidence
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for src in (admitted, key_evidence or []):
        for r in src or []:
            d = _row_date(r)
            text = " ".join(str(r.get("content") or r.get("text") or "").split())
            if not text:
                continue
            key = text[:80].lower()
            if key in seen:
                continue
            seen.add(key)
            date_s = d.isoformat() if d else "undated"
            pairs.append((date_s, text[:100]))

    # Prefer dated, sort ISO dates first
    dated = [(d, t) for d, t in pairs if d != "undated"]
    undated = [(d, t) for d, t in pairs if d == "undated"]
    dated.sort(key=lambda x: x[0])

    if dated:
        lines.append("- Dated candidates (sort key = date):")
        for i, (d, t) in enumerate(dated[:12], 1):
            lines.append(f"  {i}. ({d}) {t}")
    if undated:
        lines.append("- Undated candidates (resolve date before placing in order):")
        for i, (_d, t) in enumerate(undated[:6], 1):
            lines.append(f"  {i}. (undated) {t}")
    if not dated and not undated:
        lines.append(
            "- No timeline rows yet — search each entity with 'visited/flew/trip' "
            "+ date before answering order."
        )

    if card and len(dated) < card:
        lines.append(
            f"- INCOMPLETE: dated_rows={len(dated)} < N={card}. Do not finalize order."
        )
    return "\n".join(lines)
