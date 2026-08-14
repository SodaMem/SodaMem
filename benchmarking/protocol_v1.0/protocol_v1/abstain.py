"""Abstain consistency — generic guidance (TAS)."""
from __future__ import annotations

from protocol_v1.schema import QuestionSchema


def abstain_advisory(schema: QuestionSchema, *, soften_ie: bool = False) -> str:
    if soften_ie:
        return (
            "tas ie_evidence_policy:\n"
            "- Facts stated by the assistant, a cited paper, or a tool output in the "
            "same dialogue are valid evidence when the question asks for them.\n"
            "- Prefer the explicit numeric/named answer from that evidence over "
            "'insufficient information'."
        )
    if schema.task == "ABSTAIN":
        return (
            "tas abstain_consistency:\n"
            "- If the question asserts an event or identity the user never described, "
            "say the information is not enough.\n"
            "- Do not stitch adjacent facts into a proposition that was never stated."
        )
    return (
        "tas abstain_consistency: do not bridge adjacent facts into a proposition "
        "the user never stated."
    )
