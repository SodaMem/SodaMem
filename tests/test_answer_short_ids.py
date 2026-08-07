"""The model reads `e1`; everything else keeps reading the real id.

`ev_fact:fact_<uuid>` is 43 characters, 13.4% of all card bytes on
b5_traced_0731, and every one is repeated in claims, rosters and
selected_evidence_ids — roughly 10% of planner input spent naming things.
The alias map lives at the serialization boundary only: assigned in
first-seen order per question, translated back by exact string match the
moment the packet parses. Observations, claims, the duplicate-call
signature, the final selection and the trace all stay on store ids; the
aliases exist in the serialized user message and the raw planner_output
text, nowhere else.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider

_UUID_ID = "ev_fact:fact_3d75eaaa-ba0c-409f-b4b5-d044aafb6055"


class _Tools:
    def dispatch(self, name, **kwargs):
        return {"items": [{"fact_id": _UUID_ID.split(":", 1)[1],
                           "evidence_id": _UUID_ID,
                           "content": "a row", "event_date": "2023-04-01"}]}


_SEARCH = json.dumps({
    "state_update": {"question_classification": {
        "type": "ordinary", "comparison_requires_count_or_sum": False}},
    "decision": {"action": "tool_calls", "calls": [
        {"tool": "browser_search", "args": {"query": "q"}}]},
})

# The model answers in aliases — claims and selection both.
_FINAL_WITH_ALIAS = json.dumps({
    "state_update": {"upsert_claims": [{
        "claim_id": "c1", "statement": "it is the row",
        "status": "supported", "evidence_ids": ["e1"]}]},
    "decision": {"action": "final", "sufficiency": "sufficient",
                 "selected_evidence_ids": ["e1"],
                 "missing_information": ""},
})


def _run(flag: bool, final: str):
    return run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider([_SEARCH, final]),
        config=PlannerConfig(max_steps=2, capture_planner_input=True,
                             short_evidence_ids=flag),
    )


def test_the_model_sees_aliases_and_never_the_uuid():
    result = _run(True, _FINAL_WITH_ALIAS)

    message = result.planner_trace[1]["planner_input"]
    assert _UUID_ID not in message
    assert '"e1"' in message


def test_the_selection_and_claims_come_back_as_real_ids():
    result = _run(True, _FINAL_WITH_ALIAS)

    assert result.selected_evidence_ids == [_UUID_ID]
    assert result.planner_claims == [
        {"statement": "it is the row", "evidence_ids": [_UUID_ID]}]
    # The trace's packet is the translated one — analysis scripts join it
    # against observation ids and must keep working.
    packet = result.planner_trace[1]["packet"]
    assert packet["decision"]["selected_evidence_ids"] == [_UUID_ID]


def test_off_by_default_the_uuid_flows_through_unchanged():
    final = _FINAL_WITH_ALIAS.replace('"e1"', json.dumps(_UUID_ID))
    result = _run(False, final)

    assert _UUID_ID in result.planner_trace[1]["planner_input"]
    assert result.selected_evidence_ids == [_UUID_ID]
