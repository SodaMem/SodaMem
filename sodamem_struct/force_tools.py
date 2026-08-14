"""Structural tool forcing only — no planner/reader prompt text."""
from __future__ import annotations

from typing import Any

from sodamem_struct.classify import aggregation_labels, needs_count_tool, route_question

_APPLIED = False
_ORIG_INITIAL = None
_ORIG_MISSING = None
_ORIG_CAPABILITY = None


def is_applied() -> bool:
    return _APPLIED


def apply_tool_forcing() -> None:
    """Force count/timeline tool families for set/slot routes (code path)."""
    global _APPLIED, _ORIG_INITIAL, _ORIG_MISSING, _ORIG_CAPABILITY
    if _APPLIED:
        return

    import sodamem.answer.agent_guidance as guidance_mod
    import sodamem.answer.loop as loop_mod
    import sodamem.answer.rules as rules_mod

    _ORIG_INITIAL = rules_mod.initial_calls

    def _initial_calls(rules, question: str):
        calls = list(_ORIG_INITIAL(rules, question) or [])
        route = route_question(question)
        if route.kind in {"set_count", "set_sum"} and not any(
            c.get("tool") in {"browser_count_evidence", "count"} for c in calls
        ):
            calls.append({
                "tool": "browser_count_evidence",
                "args": {
                    "query": question,
                    "labels": aggregation_labels(question)[:8],
                },
            })
        return calls

    rules_mod.initial_calls = _initial_calls

    _ORIG_MISSING = loop_mod._missing_capability_families

    def _missing(state: Any):
        required = list(_ORIG_MISSING(state))
        families = {fam for fam, _ in required}
        # objective often embeds the question; fall back to empty
        obj = getattr(state, "objective", "") or ""
        q = obj
        if "Question:" in obj:
            q = obj.split("Question:", 1)[-1].strip()
        elif "question:" in obj.lower():
            idx = obj.lower().find("question:")
            q = obj[idx + len("question:") :].strip()
        if needs_count_tool(q) and "count_family" not in families:
            required.append(("count_family", "browser_count_evidence"))
            if getattr(state, "question_classification", None) is None:
                state.question_classification = {
                    "type": "count",
                    "comparison_requires_count_or_sum": False,
                }
            elif isinstance(state.question_classification, dict):
                if state.question_classification.get("type") in {None, "", "ordinary"}:
                    state.question_classification["type"] = "count"
        return required

    loop_mod._missing_capability_families = _missing

    _ORIG_CAPABILITY = guidance_mod.AgentGuidance.capability_calls

    def _capability_calls(self, missing_families, question: str):
        calls = list(_ORIG_CAPABILITY(self, missing_families, question))
        labels = aggregation_labels(question)
        for call in calls:
            if call.get("tool") == "browser_count_evidence":
                call["args"] = {
                    **(call.get("args") or {}),
                    "query": question,
                    "labels": labels[:8],
                }
        return calls

    guidance_mod.AgentGuidance.capability_calls = _capability_calls
    _APPLIED = True
