"""The planner trace has to survive the run, not just be counted and dropped.

`answer_one` reduces `loop_result.planner_trace` to `tools_used` (which tool
names appeared) and `planner_steps` (how many rows) and then lets it go. Those
two answer "did the count family ever fire", which is what they were added
for, and nothing else.

They cannot answer the question the 0730 data actually raises. 68.9% of ABS
questions burn all 12 steps, and they are 96.0% correct when they do — the
exhaustion is right, but it costs 14 LLM calls against 8 for everything else,
roughly 145 wasted calls per 500-question run. Whether those 12 steps are
re-searching synonyms of a thing that is not there, or genuinely widening the
net, is a question about the *queries*, and the queries are exactly what gets
discarded.

Compacted, not raw: a full trace with every observation's evidence rows is
megabytes per question. What a diagnosis needs is the sequence of (step, tool,
query) — small enough to keep for every question of every arm.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# A base `pip install sodamem` has no [llm] extra, and CI's
# gate-i1-base-deps job collects this whole tree under exactly that
# install. Skipping is the contract; exploding at import is what the
# gate exists to catch.
pytest.importorskip("openai", reason="the benchmark harness imports the OpenAI SDK at module level; it lives behind the [llm] extra")

from run_s500 import compact_trace  # noqa: E402

def test_keeps_the_query_of_every_call_in_order():
    trace = [
        {"step": 0, "observations": [
            {"tool": "browser_search", "args": {"query": "egg tarts"}},
        ]},
        {"step": 1, "observations": [
            {"tool": "browser_search_raw", "args": {"query": "baking egg tart"}},
            {"tool": "browser_inspect_session", "args": {"session_id": "s7"}},
        ]},
    ]

    assert compact_trace(trace) == [
        {"step": 0, "tool": "browser_search", "query": "egg tarts"},
        {"step": 1, "tool": "browser_search_raw", "query": "baking egg tart"},
        {"step": 1, "tool": "browser_inspect_session", "query": ""},
    ]


def test_drops_the_evidence_payload_that_makes_a_raw_trace_unstorable():
    trace = [{"step": 0, "observations": [{
        "tool": "browser_search",
        "args": {"query": "q"},
        "new_ids": ["ev_fact:a"] * 500,
        "seen_ids": ["ev_fact:a"] * 500,
        "roster": [{"content": "x" * 1000}],
    }]}]

    row = compact_trace(trace)[0]

    assert row == {"step": 0, "tool": "browser_search", "query": "q"}


def test_a_step_with_no_observations_contributes_nothing():
    """Planner steps that only update state make no calls; they are already
    counted by `planner_steps` and would be empty rows here."""
    assert compact_trace([{"step": 0, "observations": []}, {"step": 1}]) == []


def test_an_empty_trace_is_an_empty_list_not_a_crash():
    assert compact_trace([]) == []
    assert compact_trace(None) == []
