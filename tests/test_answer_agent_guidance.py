"""AgentGuidance owns the arithmetic; the loop owns the orchestration.

These tests pin the module boundary: thresholds and counters live here and
are configured through PlannerConfig fields, so tightening an arm (the c3
run: dup 2→1, zero-rows 4→3) is a config change, not loop surgery. The
verdict strings are trace-stable — run analyses key on `reason=count`.
"""
from __future__ import annotations

from sodamem.answer.agent_guidance import AgentGuidance
from sodamem.answer.loop import PlannerConfig


def _g(**kw):
    return AgentGuidance(PlannerConfig(**kw))


def test_duplicate_threshold_is_configurable():
    g = _g(stall_dup_threshold=1)
    assert g.stall_verdict(0) is None
    g.note_duplicate_proposal()
    assert g.stall_verdict(0) == "dup_proposals=1"


def test_zero_rows_ignores_skips_and_errors():
    g = _g(stall_zero_rows_threshold=2)
    g.note_observation({"returned_rows": 0, "skipped": "exact_duplicate"})
    g.note_observation({"returned_rows": 0, "error": "boom"})
    g.note_observation({"returned_rows": 0})
    assert g.stall_verdict(0) is None
    g.note_observation({"returned_rows": 0})
    assert g.stall_verdict(0) == "zero_row_calls=2"


def test_zero_novelty_comes_from_the_caller():
    g = _g(stall_zero_novelty_threshold=2)
    assert g.stall_verdict(1) is None
    assert g.stall_verdict(2) == "zero_novelty_run=2"


def test_stall_stop_off_silences_every_verdict():
    g = _g(stall_stop=False, stall_dup_threshold=1)
    g.note_duplicate_proposal()
    assert g.stall_verdict(99) is None


def test_truncation_retry_doubles_the_configured_cap():
    assert _g(planner_max_tokens=1200).truncation_retry_max_tokens() == 2400
    assert _g(truncation_retry=False).truncation_retry_max_tokens() is None


def test_capability_calls_synthesize_both_families():
    g = _g(capability_autocall=True)
    calls = g.capability_calls(
        [("count_family", "browser_count_evidence"),
         ("timeline_family", "browser_timeline_events")],
        "How many concerts did I attend?",
    )
    assert [c["tool"] for c in calls] == [
        "browser_count_evidence", "browser_timeline_events"]
    count, timeline = calls
    assert count["args"]["query"] == "How many concerts did I attend?"
    assert count["args"]["labels"]          # non-empty — the tool rejects []
    assert timeline["args"]["events"] == count["args"]["labels"]


def test_capability_calls_are_empty_when_the_flag_is_off():
    g = _g(capability_autocall=False)
    assert g.capability_calls(
        [("count_family", "browser_count_evidence")], "how many?") == []
