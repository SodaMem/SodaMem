"""A planner that is provably spinning should stop paying for steps.

The b5_traced_0731 step census: of 3445 planner steps, 297 added zero new
evidence, 205 retrieved zero rows, and 592 proposals were exact duplicates
the runtime skipped — the skip saves the retrieval but not the step that
proposed it, nor the next step spent reading "Skipped exact duplicate call"
and proposing it again (q094 and q268 each re-proposed one dead query 13
times). The 99 questions that burn all 12 steps eat 37.4% of the run's
tokens and score 83.8% against the run's 90%.

Three triggers, thresholds from the offline counterfactual that cut each
trace where the trigger would have fired and checked what the final
selection would have lost: a second exact-duplicate proposal, a fourth
zero-row retrieval, or two consecutive steps with no new evidence. Union:
137/500 questions, 603/3445 steps cut (17.5% of tokens at the ~4K-token
marginal step). Reader replay of the 11 questions that would lose selected
rows: 8 valid controls, q214 flips wrong, q017 flips right — net zero.

The cut is at the END of the offending step: its retrievals are kept, and
the existing max-steps fallback (selected seeded from supported claims,
else compacted cards) builds the final decision, so a stalled question
still answers from everything it retrieved.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider


class _Tools:
    """Fresh row per distinct query; same query always returns its row."""

    def dispatch(self, name, **kwargs):
        q = str(kwargs.get("query") or kwargs.get("session_id") or "x")
        key = "".join(ch for ch in q if ch.isalnum()) or "x"
        return {"items": [{"fact_id": key, "evidence_id": f"ev_fact:{key}",
                           "content": f"row for {q}",
                           "event_date": "2023-04-01"}]}


class _EmptyAndFreshTools:
    """Each call pair: `dead ...` queries return nothing, others a fresh row."""

    def dispatch(self, name, **kwargs):
        q = str(kwargs.get("query") or "")
        if q.startswith("dead"):
            return {"items": []}
        key = "".join(ch for ch in q if ch.isalnum()) or "x"
        return {"items": [{"fact_id": key, "evidence_id": f"ev_fact:{key}",
                           "content": f"row for {q}",
                           "event_date": "2023-04-01"}]}


def _step(*calls):
    return json.dumps({
        "state_update": {"question_classification": {
            "type": "ordinary", "comparison_requires_count_or_sum": False}},
        "decision": {"action": "tool_calls", "calls": [
            {"tool": "browser_search", "args": {"query": q}} for q in calls]},
    })


def test_second_duplicate_proposal_ends_the_loop():
    # Step 0 is the forced first search; step 1 executes "same q"; steps 2
    # and 3 re-propose it verbatim — skipped by the runtime, and the second
    # skip is the stall signal.
    script = [_step("same q"), _step("same q"), _step("same q"), _step("same q")]
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider(script + [_step("never reached")]),
        # Thresholds pinned to the looser c1 values: this test documents the
        # counting mechanics; test_answer_defaults pins what ships (1/3).
        config=PlannerConfig(max_steps=12, stall_stop=True,
                             stall_dup_threshold=2, stall_zero_rows_threshold=4),
    )

    assert result.planner_trace[-1]["stall_stop"] == "dup_proposals=2"
    assert result.termination == "stall_stop_reader_fallback"
    assert len(result.planner_trace) == 4
    # The stalled question still answers from what it retrieved.
    assert result.selected_evidence_ids


def test_two_steps_without_new_evidence_end_the_loop():
    # Step 0's forced search retrieves the question's row. Step 1 re-queries
    # it with bare args (a different signature, so it executes) and gets the
    # same row back: novelty run 1. Step 2's identical proposal is skipped
    # (one skip — below the duplicate threshold): run 2, stall.
    script = [
        _step("ignored - step 0 calls are replaced by the forced search"),
        _step("What is it?"),
        _step("What is it?"),
    ]
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider(script),
        # dup pinned loose so the zero-novelty line is the one that fires —
        # at the shipped dup=1 the step-2 skip would trip the dup line first.
        config=PlannerConfig(max_steps=12, stall_stop=True,
                             stall_dup_threshold=2, stall_zero_rows_threshold=4),
    )

    assert result.planner_trace[-1]["stall_stop"] == "zero_novelty_run=2"
    assert len(result.planner_trace) == 3


def test_fourth_zero_row_retrieval_ends_the_loop():
    # Every step also lands a fresh row, so the novelty run never starts and
    # no proposal repeats — only the zero-row count climbs, one per step.
    script = [
        _step("ignored - step 0 calls are replaced by the forced search"),
        _step("dead 1", "fresh 1"),
        _step("dead 2", "fresh 2"),
        _step("dead 3", "fresh 3"),
        _step("dead 4", "fresh 4"),
    ]
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_EmptyAndFreshTools(),
        provider=ScriptedProvider(script),
        config=PlannerConfig(max_steps=12, stall_stop=True,
                             stall_dup_threshold=2, stall_zero_rows_threshold=4),
    )

    trace = result.planner_trace
    # Step 0's forced search replaces the proposed calls, so the four
    # dead+fresh pairs run on steps 1-4 and the fourth zero-row lands there.
    assert trace[-1]["stall_stop"] == "zero_row_calls=4"
    assert len(trace) == 5


def test_opting_out_restores_the_run_to_max_steps():
    """Default flipped ON 0731 after the c1 full-500: −46.5% tokens, score
    445 between the two zero-change baselines (450/441). Opting out must
    still reproduce the old exhaustive behavior exactly."""
    script = [_step("same q")] * 6
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider(script),
        config=PlannerConfig(max_steps=6, stall_stop=False),
    )

    assert result.termination == "max_steps_reader_fallback"
    assert len(result.planner_trace) == 6
    assert all("stall_stop" not in row for row in result.planner_trace)
