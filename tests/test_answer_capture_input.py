"""The planner's input is what it decided FROM; without it a trace is half a record.

`trace_row` keeps `planner_input_chars` — a length. So a trace shows what the
planner decided and never what it was looking at. For the question that
matters most right now (92 unstable questions, worth 7.6 points, where the
same question passes in one run and fails in the next) the whole diagnosis is
"what differed at the step where they diverged", and half of that is the input.

Default OFF: the message is 1.4-12.5 KB per step and grows monotonically
within a question as evidence cards accumulate. Holding every one of them in
memory is a cost only a diagnostic run should pay, not every embedder of this
library.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider


class _Tools:
    def dispatch(self, name, **kwargs):
        return {"items": [{"fact_id": "f", "evidence_id": "ev_fact:f",
                           "content": "a row", "event_date": "2023-04-01"}]}


_STEP = json.dumps({
    "state_update": {"question_classification": {
        "type": "ordinary", "comparison_requires_count_or_sum": False}},
    "decision": {"action": "tool_calls", "calls": [
        {"tool": "browser_search", "args": {"query": "q"}}]},
})


def _trace(*, capture: bool):
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider([_STEP, _STEP]),
        config=PlannerConfig(max_steps=1, capture_planner_input=capture),
    )
    return result.planner_trace


def test_the_input_is_captured_when_asked_for():
    row = _trace(capture=True)[0]
    assert row["planner_input"].startswith("{")
    # The same string the length was already reported for — not a summary of it.
    assert len(row["planner_input"]) == row["planner_input_chars"]


def test_capture_is_off_by_default():
    assert "planner_input" not in _trace(capture=False)[0]


def test_the_length_is_reported_either_way():
    """`planner_input_chars` predates this and other analyses read it."""
    for capture in (True, False):
        assert _trace(capture=capture)[0]["planner_input_chars"] > 0
