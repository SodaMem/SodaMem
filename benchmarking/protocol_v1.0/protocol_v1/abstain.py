"""Abstain consistency guidance (v1.0 IE soften)."""
from __future__ import annotations

from protocol_v1.schema import QuestionSchema


def abstain_advisory(schema: QuestionSchema, *, soften_ie: bool = False) -> str:
    if soften_ie:
        return (
            "protocol_v1.0 ie_evidence_policy:\n"
            "- This question likely asks for a fact from an assistant message, paper, "
            "recommendation, or tool output in the same dialogue (e.g. framerate %, "
            "HAMT result, recommended product).\n"
            "- Assistant-stated numbers/names in the retrieved session ARE valid evidence; "
            "do NOT abstain solely because the USER did not restate them.\n"
            "- Prefer the explicit numeric/named answer from that evidence over "
            "'insufficient information'."
        )
    if schema.task != "ABSTAIN" and "poster" not in schema.predicate:
        return (
            "protocol_v1.0 abstain_consistency: do not bridge adjacent facts into "
            "a proposition the user never stated (e.g. conference venue + poster "
            "≠ undergrad course poster university)."
        )
    return (
        "protocol_v1.0 abstain_consistency:\n"
        "- If the question asserts an event the user never described (e.g. undergrad "
        "course research poster), answer that the information is not enough.\n"
        "- Do not combine 'presented a poster at a conference' with 'visited Harvard' "
        "into 'presented undergrad poster at Harvard' unless explicitly stated."
    )
