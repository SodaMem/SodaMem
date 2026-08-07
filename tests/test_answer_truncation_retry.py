"""A truncated planner step must cost a longer completion, not a whole step.

96 of the 99 unparseable planner outputs in b5_traced_0731 are hard
truncation at the 1200-token cap: 2985-4626 characters of well-formed JSON
head with no closing braces, concentrated on count questions whose
state_update re-upserts a growing claim set (24 questions own all 99 failed
steps; q173 alone lost 10 of its 12). The existing handling appends
"Response was not a valid JSON protocol object." and continues — which
regenerates the SAME response at the SAME cap on the next step, because the
loop runs at temperature 0. Those 24 questions score 75% against the run's
90%.

The retry must raise the cap or it is theater: an identical call at
temperature 0 reproduces the identical truncation. One retry at double the
cap; if that also fails to parse, the step falls through to the existing
feedback path unchanged.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider


class _Tools:
    def dispatch(self, name, **kwargs):
        return {"items": [{"fact_id": "f", "evidence_id": "ev_fact:f",
                           "content": "a row", "event_date": "2023-04-01"}]}


_TRUNCATED = json.dumps({
    "state_update": {"question_classification": {
        "type": "ordinary", "comparison_requires_count_or_sum": False}},
    "decision": {"action": "tool_calls", "calls": [
        {"tool": "browser_search", "args": {"query": "q"}}]},
})[:-40]  # a JSON head with no tail — the b5 failure shape

_STEP = json.dumps({
    "state_update": {"question_classification": {
        "type": "ordinary", "comparison_requires_count_or_sum": False}},
    "decision": {"action": "tool_calls", "calls": [
        {"tool": "browser_search", "args": {"query": "q"}}]},
})

_FINAL = json.dumps({
    "state_update": {},
    "decision": {"action": "final", "sufficiency": "sufficient",
                 "selected_evidence_ids": ["ev_fact:f"],
                 "missing_information": ""},
})


def test_retry_doubles_the_cap_and_saves_the_step():
    provider = ScriptedProvider([_TRUNCATED, _STEP, _FINAL])
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=provider,
        config=PlannerConfig(max_steps=3, truncation_retry=True),
    )

    # Three calls: the truncated one, its retry, and step 1's final —
    # the truncation consumed a completion, not a step.
    assert [c["max_tokens"] for c in provider.calls] == [1200, 2400, 1200]
    assert result.planner_trace[0]["truncation_retry"] is True
    assert result.termination == "planner_final"
    assert len(result.planner_trace) == 2


def test_opting_out_wastes_the_step_exactly_as_before():
    provider = ScriptedProvider([_TRUNCATED, _STEP, _FINAL])
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=provider,
        config=PlannerConfig(max_steps=3, truncation_retry=False),
    )

    assert [c["max_tokens"] for c in provider.calls] == [1200, 1200, 1200]
    assert "truncation_retry" not in result.planner_trace[0]
    assert result.planner_trace[0]["packet"] is None
    assert len(result.planner_trace) == 3


def test_a_retry_that_also_fails_falls_through_to_feedback():
    provider = ScriptedProvider([_TRUNCATED, _TRUNCATED, _STEP, _FINAL])
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=provider,
        config=PlannerConfig(max_steps=3, truncation_retry=True),
    )

    # One retry only — no doubling ladder, no loop.
    assert [c["max_tokens"] for c in provider.calls] == [1200, 2400, 1200, 1200]
    assert result.planner_trace[0]["packet"] is None
    assert result.termination == "planner_final"
