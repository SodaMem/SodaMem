"""Completeness / conflict check text for reader (v1.0 + cardinality)."""
from __future__ import annotations

from typing import Any, Optional

from protocol_v1.schema import QuestionSchema


def completeness_advisory(
    schema: QuestionSchema,
    *,
    admitted: list[dict[str, Any]],
    raw_roster_n: int,
    det_value: float | int | None = None,
    cardinality: Optional[int] = None,
    entity_names: Optional[list[str]] = None,
) -> str:
    lines = ["protocol_v1.0 completeness_check:"]
    if schema.needs_saturation:
        lines.append(
            f"- Set task: admitted={len(admitted)} raw_roster={raw_roster_n}. "
            "If any dollar/page/year component mentioned in evidence is missing "
            "from your include list, search again before finalizing."
        )
    if cardinality is not None and schema.task in {"COUNT_DISTINCT", "ORDERED_LIST"}:
        have = len(entity_names) if entity_names is not None else len(admitted)
        lines.append(
            f"- CARDINALITY: question asserts N={cardinality} items; "
            f"current distinct set size≈{have}. "
            "If have < N, you are incomplete — keep retrieving before answering. "
            "If have == N, prefer that enumeration/order."
        )
    if entity_names is not None and schema.count_unit == "entity":
        lines.append(
            "- ENTITY DEDUP list (count these, not repeated service events): "
            + (", ".join(entity_names) if entity_names else "(none extracted)")
        )
        lines.append(f"- Prefer final integer = {len(entity_names)} when list is non-empty.")
    if schema.task == "SUM":
        lines.append(
            "- Prefer amounts stated by the USER. Ignore assistant-only fare guesses "
            "when computing savings."
        )
        lines.append(
            "- After listing components, re-add them; if a component like $250 is "
            "present in memory but absent from your sum, include it."
        )
    if schema.exclude_predicates:
        lines.append(
            "- Do NOT include: " + ", ".join(schema.exclude_predicates)
        )
    if det_value is not None:
        lines.append(f"- Mechanized admitted-set size/value hint: {det_value}")
    return "\n".join(lines)
