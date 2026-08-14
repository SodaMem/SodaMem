"""Abstention must be earned by a retrieval that came back empty.

The 0729 full-500 diagnosis: 17 of 53 misses (32%, the largest addressable
bucket) are false missing — the gold answer exists, the evidence is in the
store, and the reader answered "I don't have that information". A zero-LLM
probe on the 0727 run found the key evidence inside the top-10 recall for at
least 4 of 6 sampled cases.

The obvious lever — tell the reader to stop abstaining (`answer_bias`) — was
measured on MR-121 and is null: +2 net on 12 questions moved, McNemar
p = 0.77, and combined with `membership_bias` it goes to -2. It cannot work,
because it is a global knob that cannot tell "the evidence is there and the
reader won't commit" from "the evidence genuinely is not there". Turning it up
trades the 17 false-missing questions against the 4 ABS questions we currently
get right by abstaining correctly.

So gate the claim instead of nudging the prose, exactly as spec I1 AC4 gates
count/sum finalization on a successful count-family call: a planner may
finalize `insufficient` only if some successful retrieval returned zero rows.
If every search it ran brought back rows, it is holding evidence it did not
use, and "the information is missing" is a claim it has not earned.

This is deliberately asymmetric. It never pushes toward answering; it only
refuses *unproven* abstention. A genuinely unanswerable question searches,
comes back empty, and passes the gate untouched — which is why it does not
have the ABS regression that `answer_bias` does.
"""
from __future__ import annotations

from sodamem.answer.loop import _finalization_errors
from sodamem.answer.protocol import PlannerState
from sodamem.context.store import EvidenceStore


def _tool_output(*rows):
    return {"results": list(rows)}


def _state_with_search(returned_rows: int, *, success: bool = True) -> PlannerState:
    state = PlannerState(objective="test")
    state.question_classification = {
        "type": "ordinary",
        "comparison_requires_count_or_sum": False,
    }
    state.search_history.append({
        "step": 0, "tool": "browser_search", "args": {}, "signature": "sig",
        "new_evidence": returned_rows, "returned_rows": returned_rows,
        "success": success,
    })
    return state


def test_insufficient_is_rejected_when_every_search_returned_rows():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a",
         "content": "budget store boots for $50"},
    ), 0)

    errors, _ = _finalization_errors(
        decision={
            "sufficiency": "insufficient",
            "missing_information": "no price for the budget pair",
            "selected_evidence_ids": [],
        },
        state=_state_with_search(returned_rows=3),
        evidence=evidence, max_selected_evidence=24, abstention_gate=True,
    )

    assert any("returned no rows" in e for e in errors), errors


def test_insufficient_is_allowed_when_a_search_came_back_empty():
    """The ABS half. This is why the gate is not `answer_bias` in disguise.

    A genuinely unanswerable question searches, finds nothing, and abstains.
    The gate must not touch it — otherwise we buy the 17 false-missing
    questions by breaking the 25/30 ABS questions we already get right, which
    is precisely the trade `answer_bias` makes.
    """
    errors, _ = _finalization_errors(
        decision={
            "sufficiency": "insufficient",
            "missing_information": "the user never mentioned baking egg tarts",
            "selected_evidence_ids": [],
        },
        state=_state_with_search(returned_rows=0),
        evidence=EvidenceStore(), max_selected_evidence=24, abstention_gate=True,
    )

    assert errors == []


def test_a_failed_search_does_not_earn_an_abstention():
    """An empty result is evidence of absence; an error is evidence of nothing.

    Same rule the capability gate uses — a call that raised did not execute,
    so it cannot stand in for having looked.
    """
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a", "content": "some row"},
    ), 0)
    state = _state_with_search(returned_rows=3)
    state.search_history.append({
        "step": 1, "tool": "browser_search_raw", "args": {}, "signature": "sig2",
        "new_evidence": 0, "returned_rows": 0, "success": False,
    })

    errors, _ = _finalization_errors(
        decision={
            "sufficiency": "insufficient",
            "missing_information": "nothing found",
            "selected_evidence_ids": [],
        },
        state=state, evidence=evidence, max_selected_evidence=24,
        abstention_gate=True,
    )

    assert any("returned no rows" in e for e in errors), errors


def test_a_sufficient_finalization_is_never_touched_by_the_gate():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a", "content": "the answer"},
    ), 0)

    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient",
                  "selected_evidence_ids": ["ev_fact:fact_a"]},
        state=_state_with_search(returned_rows=3),
        evidence=evidence, max_selected_evidence=24, abstention_gate=True,
    )

    assert errors == []
    assert selected == ["ev_fact:fact_a"]


# ---------------------------------------------------------------------------
# The max-steps fallback builds its own decision and never calls
# `_finalization_errors`, so the gate has to be applied there too. 12 of the
# 53 misses in the 0729 full-500 terminated as `max_steps_reader_fallback`.
# ---------------------------------------------------------------------------

import json  # noqa: E402

from sodamem.answer.loop import PlannerConfig, run_planner_loop  # noqa: E402
from sodamem.llm.testing import ScriptedProvider  # noqa: E402
from sodamem.tools import ToolError  # noqa: E402


class _FakeTools:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    def dispatch(self, name, **kwargs):
        if not self._payloads:
            raise ToolError("empty_input", f"exhausted at {name}")
        return self._payloads.pop(0)


_ORDINARY = {
    "question_classification": {
        "type": "ordinary",
        "comparison_requires_count_or_sum": False,
    }
}


def test_running_out_of_steps_with_evidence_in_hand_is_not_an_abstention():
    """"I ran out of budget" is not "the store does not have it".

    The fallback collapses both into `insufficient`, and the reader turns that
    into "I don't have that information" — a false statement about the store
    whenever the searches did come back with rows. Those are the questions
    where the 0727 zero-LLM probe found the answer sitting in the top-10.
    """
    rows = _tool_output(
        {"id": "a", "evidence_id": "ev_fact:a", "content": "the answer is here"},
    )
    tools = _FakeTools([rows, rows, rows])
    step = json.dumps({
        "state_update": dict(_ORDINARY,
                             open_questions=[{"question": "anything else?",
                                              "material": True}]),
        "decision": {"action": "tool_calls", "calls": [
            {"tool": "browser_search", "args": {"query": "q"}},
        ]},
    })
    provider = ScriptedProvider([step, step, step])

    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=tools,
        provider=provider,
        config=PlannerConfig(max_steps=2, abstention_gate=True),
    )

    assert result.termination == "max_steps_reader_fallback"
    assert result.insufficient is False


# ---------------------------------------------------------------------------
# The gate changes the scoring path, so it ships default-OFF behind a flag and
# does not become the default until it beats the baseline on a full-500 paired
# run. Same rule 1c2cee5 set for the reader arms: 未过门不得转正.
# ---------------------------------------------------------------------------

def test_the_gate_is_off_by_default():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a", "content": "a row"},
    ), 0)

    errors, _ = _finalization_errors(
        decision={"sufficiency": "insufficient",
                  "missing_information": "claimed missing",
                  "selected_evidence_ids": []},
        state=_state_with_search(returned_rows=3),
        evidence=evidence, max_selected_evidence=24,
    )

    assert errors == []
