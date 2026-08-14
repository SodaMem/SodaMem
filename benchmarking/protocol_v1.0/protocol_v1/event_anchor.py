"""TR EventAnchor — structure who/which answers onto a pinned calendar day."""
from __future__ import annotations

import re
from typing import Any, Optional

from protocol_v1.temporal_pin import in_window, needs_day_pin, _row_date, window_bounds


def _slot_kind(question: str) -> str:
    ql = (question or "").lower()
    if re.search(r"\b(who did i|who was|from whom|with whom|go with)\b", ql):
        return "who"
    if re.search(r"\b(which bike|which .+ did i|which airline|which .+)\b", ql):
        return "which"
    if "where" in ql:
        return "where"
    return "event"


def build_event_anchor_advisory(
    question: str,
    schema_task: str,
    *,
    window: Optional[dict[str, str]],
    key_evidence: Optional[list[dict[str, Any]]] = None,
) -> str:
    if not needs_day_pin(question, schema_task):
        # Also cover "two weeks ago" art-event style without who/which regex
        ql = (question or "").lower()
        if schema_task != "TEMPORAL_EVENT":
            return ""
        if not re.search(
            r"\b(two weeks ago|last saturday|past weekend|last weekend|"
            r"from whom|who did i|which bike)\b",
            ql,
        ):
            return ""

    lo, hi = window_bounds(window)
    slot = _slot_kind(question)
    lines = [
        "protocol_v1.0 event_anchor:",
        f"- Target slot to fill: {slot} (companion / object / place).",
    ]
    if lo:
        lines.append(
            f"- Absolute day pin: {lo}"
            + (f" .. {hi}" if hi and hi != lo else "")
            + ". Bind the slot ONLY to evidence dated this window."
        )
    else:
        lines.append(
            "- Resolve the relative day to an absolute date before picking who/which."
        )

    inn: list[str] = []
    out: list[str] = []
    for r in key_evidence or []:
        d = _row_date(r)
        snip = " ".join(str(r.get("content") or r.get("text") or "").split())[:90]
        label = d.isoformat() if d else "undated"
        if d and in_window(d, lo, hi):
            inn.append(f"  [{label}] {snip}")
        else:
            out.append(f"  [{label}] {snip}")

    if inn:
        lines.append("- In-window evidence (USE for the slot):")
        lines.extend(inn[:8])
    else:
        lines.append(
            "- No dated in-window rows found yet — search with the absolute date "
            "string before answering; prefer insufficient-information over "
            "borrowing companions from other days."
        )
    if out:
        lines.append("- Out-of-window / undated distractors (do NOT use for the slot):")
        lines.extend(out[:6])

    lines.append(
        f"- Answer format: state the {slot} value and the pinned date; "
        "do not mix people/objects from other sessions."
    )
    return "\n".join(lines)
