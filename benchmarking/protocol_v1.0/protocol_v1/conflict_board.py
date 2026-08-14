"""Conflict Candidate Board — surface competing memory values with slot labels.

Generic multi-value conflict resolution aid (not question-id specific).
Hard override is decided separately with a confidence gate.
"""
from __future__ import annotations

from typing import Any, Optional

from protocol_v1.schema import QuestionSchema
from protocol_v1.slot_force import (
    extract_new_vs_current_speed,
    extract_redeem_local_only,
    extract_redeem_vs_goal,
    _blob_from_evidence,
)


def build_conflict_board(
    schema: QuestionSchema,
    evidence: Any,
    *,
    admitted: list[dict[str, Any]] | None = None,
    question: str = "",
) -> tuple[str, Optional[str], bool]:
    """Return (advisory_text, preferred_value_or_None, allow_hard_override).

    allow_hard_override is True only when:
    - task is SLOT_LOOKUP or VERSIONED_ATTR (or clear modifiers), AND
    - two competing values are extracted, AND
    - the question names the preferred slot explicitly.
    """
    blob = _blob_from_evidence(evidence, admitted)
    if not blob.strip():
        return "", None, False

    ql = (question or "").lower()
    lines: list[str] = []
    preferred: Optional[str] = None
    allow_hard = False

    # --- redeem vs personal goal ---
    if schema.slot_name == "redeem_threshold" or (
        schema.task == "SLOT_LOOKUP" and ("redeem" in ql or "points" in ql)
    ):
        redeem, goal = extract_redeem_vs_goal(blob)
        if redeem is not None or goal is not None:
            lines.append("protocol_v1.0 conflict_board (points slots):")
            if redeem is not None:
                lines.append(
                    f"  - candidate value={redeem} slot=redeem_threshold "
                    f"cues=redeem|loyalty|free-product"
                )
            if goal is not None:
                lines.append(
                    f"  - candidate value={goal} slot=personal_goal "
                    f"cues=goal|all-set|need-a-total"
                )
            asks_redeem = (
                "redeem" in ql
                or "threshold" in ql
                or ("free" in ql and "product" in ql)
            )
            # Hard bind only when redeem is locally collocated (not balance/goal).
            redeem_strict = extract_redeem_local_only(blob)
            if (
                asks_redeem
                and redeem_strict is not None
                and goal is not None
                and redeem_strict != goal
            ):
                preferred = str(redeem_strict)
                allow_hard = True
                lines.append(
                    f"  - question_slot=redeem_threshold → prefer {redeem_strict} "
                    f"(local redeem cue); do not answer personal_goal {goal}."
                )
            elif asks_redeem and (redeem_strict is not None or redeem is not None):
                preferred = str(redeem_strict or redeem)
                allow_hard = False  # soft only unless dual+strict
                lines.append(
                    f"  - question_slot=redeem_threshold → prefer {preferred} "
                    "(soft; hard bind needs local redeem cue + competing goal)."
                )
            else:
                lines.append(
                    "  - Resolve by question slot name; do not default to the "
                    "largest or most recent number."
                )

    # --- new plan vs current speed ---
    if (
        schema.task == "VERSIONED_ATTR"
        or "new" in schema.modifiers
        or schema.slot_name == "new_plan_speed"
        or (("new" in ql) and ("plan" in ql or "speed" in ql or "internet" in ql))
    ):
        new_s, cur_s = extract_new_vs_current_speed(blob)
        if new_s or cur_s:
            lines.append("protocol_v1.0 conflict_board (versioned attr):")
            if new_s:
                lines.append(
                    f"  - candidate value={new_s} slot=new_plan "
                    f"cues=upgraded|new-plan|switched"
                )
            if cur_s:
                lines.append(
                    f"  - candidate value={cur_s} slot=current_status "
                    f"cues=currently|my-internet-is|now"
                )
            asks_new = "new" in ql or "upgrad" in ql or "switch" in ql
            if asks_new and new_s and cur_s and new_s.lower() != cur_s.lower():
                preferred = new_s
                allow_hard = True
                lines.append(
                    f"  - question_slot=new_plan → prefer {new_s}; "
                    f"ignore current_status {cur_s}."
                )
            elif asks_new and new_s:
                preferred = new_s
                allow_hard = False
                lines.append(f"  - question_slot=new_plan → prefer {new_s}.")
            else:
                lines.append(
                    "  - Match the version the question names (new vs current); "
                    "do not default to the chronologically latest mention."
                )

    if not lines:
        return "", None, False

    lines.append(
        "  - Select exactly one candidate that matches the question's named slot; "
        "cite the cue that labels that slot."
    )
    return "\n".join(lines), preferred, allow_hard
