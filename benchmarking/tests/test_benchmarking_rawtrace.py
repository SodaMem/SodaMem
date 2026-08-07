"""Raw trace retention: keep what a diagnosis needs, drop what is duplicated.

`trace_row` carries `planner_output` (the model's raw text) and `packet` (the
same text parsed). Storing both doubles the file for nothing — except when the
parse failed, and then the raw text is the only record of what the model
actually said, which is precisely the case worth keeping.

Kept in a sidecar rather than in answers.jsonl: every analysis so far reads
answers.jsonl end to end, and making that file an order of magnitude bigger
would slow down work that never touches the trace.
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

from run_s500 import prune_raw_trace  # noqa: E402

def test_drops_the_raw_text_when_the_packet_parsed():
    rows = [{"step": 0, "planner_output": '{"decision": {}}',
             "packet": {"decision": {}}, "observations": []}]

    out = prune_raw_trace(rows)

    assert "planner_output" not in out[0]
    assert out[0]["packet"] == {"decision": {}}


def test_keeps_the_raw_text_when_the_packet_did_not_parse():
    """A parse failure is the one case where the raw text is the only record
    of what the model said, and it is the case worth reading."""
    rows = [{"step": 0, "planner_output": "not json at all",
             "packet": None, "observations": []}]

    out = prune_raw_trace(rows)

    assert out[0]["planner_output"] == "not json at all"


def test_leaves_observations_and_rejection_details_intact():
    rows = [{"step": 0, "planner_output": "{}", "packet": {},
             "finalization_rejected": ["missing count_family"],
             "observations": [{"tool": "browser_search", "args": {"query": "q"},
                               "new_ids": ["ev_fact:a"], "returned_rows": 3}]}]

    out = prune_raw_trace(rows)

    assert out[0]["finalization_rejected"] == ["missing count_family"]
    assert out[0]["observations"][0]["args"] == {"query": "q"}
    assert out[0]["observations"][0]["returned_rows"] == 3


def test_an_empty_trace_survives():
    assert prune_raw_trace([]) == []
    assert prune_raw_trace(None) == []
