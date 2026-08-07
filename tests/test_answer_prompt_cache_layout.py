"""Same information, ordered so a prefix cache can actually hold it.

The b5_traced_0731 census of 33.8M planner-input chars: 70.8% is evidence
cards re-sent whole every step (each card 4.6 times), 8.4% a byte-identical
allowed_tools block, and the provider's prefix cache gets almost none of it
because volatile state serializes before the cards and the cards are
relevance-reranked between steps — 1636 of 2945 adjacent step pairs break
the card prefix, 769 of them pure reorders of an unchanged set. First-seen
ordering lifts stable adjacent card prefixes from 44% to 70.5% of steps;
membership changes (evictions, 867 pairs) remain unstable, honestly.

The flag must not change WHAT the planner learns — only where it sits. The
tests therefore check placement, ordering and equality of content, not new
content.
"""
from __future__ import annotations

import json

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.llm.testing import ScriptedProvider


class _Tools:
    """One fresh row per call, ids in call order."""

    def __init__(self):
        self.n = 0

    def dispatch(self, name, **kwargs):
        self.n += 1
        return {"items": [{"fact_id": f"f{self.n}",
                           "evidence_id": f"ev_fact:f{self.n}",
                           "content": f"row {self.n}",
                           "event_date": "2023-04-01"}]}


def _step(query):
    return json.dumps({
        "state_update": {"question_classification": {
            "type": "ordinary", "comparison_requires_count_or_sum": False}},
        "decision": {"action": "tool_calls", "calls": [
            {"tool": "browser_search", "args": {"query": query}}]},
    })


def _run(*, layout: bool, steps: int = 3):
    provider = ScriptedProvider([_step(f"q{i}") for i in range(steps)])
    result = run_planner_loop(
        "What is it?", current_date="2023-06-01", tools=_Tools(),
        provider=provider,
        # short_evidence_ids pinned off so card-order assertions can name
        # real ids — this file isolates the LAYOUT flag.
        config=PlannerConfig(max_steps=steps, capture_planner_input=True,
                             prompt_cache_layout=layout,
                             short_evidence_ids=False),
    )
    return provider, result


def test_allowed_tools_moves_to_the_system_prompt():
    provider, result = _run(layout=True)

    # The guide text, not just the tool name — PLANNER_SYSTEM_PROMPT's
    # protocol example already mentions browser_search by name.
    assert "ranked mixed memory search" in provider.calls[0]["system"]
    for row in result.planner_trace:
        assert "allowed_tools" not in json.loads(row["planner_input"])
    # Constant across every call — that is the entire point.
    assert len({c["system"] for c in provider.calls}) == 1


def test_default_layout_is_untouched():
    provider, result = _run(layout=False)

    assert "ranked mixed memory search" not in provider.calls[0]["system"]
    payload = json.loads(result.planner_trace[0]["planner_input"])
    assert list(payload)[:3] == ["question", "current_date", "allowed_tools"]


def test_constant_fields_lead_and_volatile_state_trails():
    _, result = _run(layout=True)

    keys = list(json.loads(result.planner_trace[1]["planner_input"]))
    assert keys == ["protocol", "question", "current_date",
                    "evidence_cards", "search_history", "evidence_state"]


def test_cards_hold_first_seen_order_across_steps():
    _, result = _run(layout=True)

    seq = []
    for row in result.planner_trace:
        ids = [c["evidence_id"] for c in
               json.loads(row["planner_input"])["evidence_cards"]]
        assert ids[:len(seq)] == seq, (ids, seq)
        seq = ids
    # Rows arrived one per step; first-seen order is retrieval order.
    assert seq == ["ev_fact:f1", "ev_fact:f2"]


def test_no_field_is_lost_in_the_reshuffle():
    """A reader of either shape must find the same facts."""
    _, flat = _run(layout=True)
    _, nested = _run(layout=False)

    p_flat = json.loads(flat.planner_trace[2]["planner_input"])
    p_nested = json.loads(nested.planner_trace[2]["planner_input"])
    rebuilt = dict(p_flat["evidence_state"])
    rebuilt["protocol"] = p_flat["protocol"]
    rebuilt["evidence_cards"] = p_flat["evidence_cards"]
    rebuilt["search_history"] = p_flat["search_history"]

    original = p_nested["evidence_state"]
    assert set(rebuilt) == set(original)
    for key in original:
        if key == "evidence_cards":
            # Ordering is the one deliberate difference.
            assert sorted(map(json.dumps, rebuilt[key])) == \
                   sorted(map(json.dumps, original[key]))
        else:
            assert rebuilt[key] == original[key], key
