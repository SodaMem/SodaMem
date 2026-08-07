"""The runtime settles the tool debt the model always pays anyway.

The c2 census that motivates this: 151 finalizations bounced for a missing
count/timeline-family call — each question bounced exactly once, and
151/151 issued the required call on the very next step. That is a
3-planner-call dance (bounced final → make-up call → final again) for a
rule the model follows 100% of the time once told. With the flag on, the
runtime synthesizes the call at finalization time, runs it, and lets the
finalization stand — the retrieved roster still lands in the evidence
store, where the full-pool reader sees it.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider


class _Tools:
    """Search returns a plain row; evidence-count returns a roster row."""

    def __init__(self):
        self.calls = []

    def dispatch(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name == "memory.tool.evidence-count":
            return {"items": [{"fact_id": "roster1", "evidence_id": "ev_fact:roster1",
                               "content": "counted item", "event_date": "2023-04-02"}]}
        return {"items": [{"fact_id": "f", "evidence_id": "ev_fact:f",
                           "content": "a row", "event_date": "2023-04-01"}]}


_COUNT_CLASSIFICATION = {"question_classification": {
    "type": "count", "comparison_requires_count_or_sum": False}}

_SEARCH = json.dumps({
    "state_update": _COUNT_CLASSIFICATION,
    "decision": {"action": "tool_calls", "calls": [
        {"tool": "browser_search", "args": {"query": "q"}}]},
})

# A final that never called the count family — the c2 bounce shape.
_FINAL = json.dumps({
    "state_update": {},
    "decision": {"action": "final", "sufficiency": "sufficient",
                 "selected_evidence_ids": ["ev_fact:f"],
                 "missing_information": ""},
})


def _run(autocall: bool, extra=()):
    tools = _Tools()
    result = run_planner_loop(
        "How many plants do I own?", current_date="2023-06-01", tools=tools,
        provider=ScriptedProvider([_SEARCH, _FINAL, *extra]),
        config=PlannerConfig(max_steps=4, capability_autocall=autocall,
                             short_evidence_ids=False),
    )
    return tools, result


def test_the_debt_is_settled_in_the_same_step():
    tools, result = _run(autocall=True)

    # Two planner calls total: the search step and ONE final — no bounce.
    assert result.termination == "planner_final"
    assert len(result.planner_trace) == 2
    final_row = result.planner_trace[1]
    assert final_row["capability_autocall"] == ["browser_count_evidence"]
    assert "finalization_rejected" not in final_row
    # The synthesized call really ran, with the question as its query.
    count_calls = [(n, kw) for n, kw in tools.calls
                   if n == "memory.tool.evidence-count"]
    assert len(count_calls) == 1
    assert count_calls[0][1]["query"] == "How many plants do I own?"
    # The model's own selection stands untouched...
    assert result.selected_evidence_ids == ["ev_fact:f"]
    # ...and the roster row is in the store for the full-pool reader.
    assert "ev_fact:roster1" in result.evidence.records


def test_off_the_bounce_happens_exactly_as_on_c2():
    tools, result = _run(autocall=False, extra=[_FINAL, _FINAL])

    bounced = result.planner_trace[1]
    assert any("count_family" in e for e in bounced["finalization_rejected"])
    assert not any(n == "memory.tool.evidence-count" for n, _ in tools.calls)


def test_a_failing_autocall_still_bounces():
    """A failed call proves nothing about what exists — same rule as the
    capability gate itself. The debt stays owed and the bounce message is
    unchanged."""

    from sodamem.tools import ToolError

    class _BrokenCount(_Tools):
        def dispatch(self, name, **kwargs):
            if name == "memory.tool.evidence-count":
                # ToolError is MemoryTool's documented failure contract —
                # what a rejected-args or backend failure actually raises.
                raise ToolError("invalid_request", "boom")
            return super().dispatch(name, **kwargs)

    tools = _BrokenCount()
    result = run_planner_loop(
        "How many plants do I own?", current_date="2023-06-01", tools=tools,
        provider=ScriptedProvider([_SEARCH, _FINAL, _SEARCH, _FINAL]),
        config=PlannerConfig(max_steps=4, capability_autocall=True,
                             short_evidence_ids=False, stall_stop=False),
    )

    bounced = result.planner_trace[1]
    assert any("count_family" in e for e in bounced["finalization_rejected"])
    assert bounced["capability_autocall"] == ["browser_count_evidence"]
