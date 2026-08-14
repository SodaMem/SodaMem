"""Slot binder advisories — named-slot discipline (TAS).

Generic guidance for SLOT_LOOKUP / VERSIONED_ATTR. No brand- or item-specific
prompt packs.
"""
from __future__ import annotations

from protocol_v1.schema import QuestionSchema


def slot_advisory(schema: QuestionSchema) -> str:
    if not schema.slot_name and not schema.modifiers:
        return ""
    lines = ["tas slot_binder:"]
    if schema.slot_name == "redeem_threshold":
        lines.extend([
            "- Question asks for a redemption / program threshold.",
            "- If evidence has both a personal goal and a redeem/threshold number, "
            "answer the redeem/threshold that matches the question slot.",
            "- Prefer lexical cues: redeem, threshold, free-product requirement "
            "over 'my goal' / 'almost reaching'.",
        ])
    if schema.slot_name == "new_plan_speed" or "new" in schema.modifiers:
        lines.append(
            "- Question asks for the NEW / upgraded value. "
            "Prefer the upgrade statement over a later current-status mention "
            "unless it clearly describes the same new plan."
        )
    if schema.slot_name == "prior_item":
        lines.append(
            "- Question asks what came BEFORE a named item. "
            "Return the chronologically prior item, not the named endpoint itself."
        )
    if schema.slot_name == "tenure_slot":
        lines.append(
            "- Distinguish time-in-role-before-promotion vs time-in-current-role; "
            "match the slot the question names."
        )
    if "current" in schema.modifiers and "new" not in schema.modifiers:
        lines.append("- Prefer the newest direct user statement of the current value.")
    for n in schema.notes:
        lines.append(f"- note: {n}")
    return "\n".join(lines) if len(lines) > 1 else ""


def slot_search_queries(schema: QuestionSchema) -> list[str]:
    if schema.slot_name == "redeem_threshold":
        return [
            "redeem points threshold free product loyalty",
            "points needed to redeem",
            "loyalty program redemption requirement points",
        ]
    if schema.slot_name == "new_plan_speed" or "new" in schema.modifiers:
        return ["upgraded new plan speed", "new plan upgrade Mbps"]
    return []
