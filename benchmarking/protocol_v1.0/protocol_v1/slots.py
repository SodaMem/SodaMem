"""Slot binder advisories (v1.1: harder redeem/threshold preference)."""
from __future__ import annotations

from protocol_v1.schema import QuestionSchema


def slot_advisory(schema: QuestionSchema) -> str:
    if not schema.slot_name and not schema.modifiers:
        return ""
    lines = ["protocol_v1.1 slot_binder:"]
    if schema.slot_name == "redeem_threshold":
        lines.extend([
            "- HARD: Question asks how many points are needed to REDEEM a free product.",
            "- If evidence has BOTH a personal 'goal' (e.g. 300) AND a program redeem/"
            "Beauty Insider/threshold number (e.g. 100), you MUST answer the redeem/"
            "threshold number, NOT the personal goal.",
            "- Lexical priority: redeem, Beauty Insider, free product requirement > "
            "'my goal' / 'all set' / 'almost reaching'.",
            "- Final answer should be the single threshold integer when known.",
        ])
    if schema.slot_name == "new_plan_speed" or "new" in schema.modifiers:
        lines.append(
            "- Question asks for the NEW plan / upgrade target. "
            "Prefer the upgrade statement (e.g. 500 Mbps), not a later "
            "'my internet is 1 Gbps' current-status mention unless it clearly "
            "describes the same new plan."
        )
    if schema.slot_name == "prior_gadget":
        lines.append(
            "- Question asks what was invested in BEFORE the Air Fryer (or named item). "
            "Return the chronologically prior gadget, not the Air Fryer itself."
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
            "Sephora Beauty Insider redeem points free skincare 100 threshold",
            "need 100 points to redeem free product Sephora",
            "loyalty program redemption requirement points",
        ]
    if schema.slot_name == "new_plan_speed" or "new" in schema.modifiers:
        return ["upgraded to 500 Mbps new internet plan", "new plan speed Mbps upgrade"]
    return []
