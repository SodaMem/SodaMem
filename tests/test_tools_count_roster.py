"""`evidence_count` must hand back one enumerable roster, not N overlapping lists.

Counting is the second-largest failure bucket on the 0729 full-500 (8 of 53),
and the tool is not the thing that is missing: q157, q172, q193, q158 and q173
all called `browser_count_evidence` and still answered the wrong number. The
I1 count-family gate did its job — coverage went from 0 calls across 500
questions to 100 of 121 MR questions — and the MR subgroup barely moved
(78.0% gated vs 76.2% ungated). The bottleneck is downstream of the tool.

Look at what the reader receives: one `items` list per label, each a raw
search card, plus a per-label `candidate_count`. Before it can count anything
it has to dedupe across labels (labels overlap by construction — they are
synonyms of the same thing), dig a date out of each card, apply the question's
literal qualifiers, and only then count. That is four jobs in prose, and the
per-label counts are actively misleading: summing them double-counts every
fact that matched two labels.

So do the mechanical half in the tool, where it is deterministic: one entry
per distinct fact, carrying its date and which labels matched, ordered. The
reader is then left with the one job that genuinely needs judgment — applying
the question's qualifiers — and the counting is `len()`.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sodamem.tools import MemoryTool


def _tool(cards_by_query: dict, facts: dict):
    class FakeStore:
        def get_fact_event(self, fact_id):
            return facts.get(fact_id)

        def get_source_span(self, span_id):
            return None

    tool = object.__new__(MemoryTool)
    tool._store = FakeStore()
    tool._user_id = "u1"
    tool.search = lambda q, **kw: {"items": cards_by_query.get(q, [])}
    return tool


def _card(fact_id: str, date: str):
    return {"fact_id": fact_id, "evidence_id": f"ev_fact:{fact_id}",
            "event_date": date, "content": f"content of {fact_id}"}


def test_a_fact_matching_two_labels_is_one_roster_entry():
    """Labels are synonyms, so their hits overlap; the roster must not.

    This is the double-count that makes summing `candidate_count` wrong.
    """
    bookshelf = _card("f_shelf", "2023-03-10")
    facts = {"f_shelf": SimpleNamespace(
        occurred_start=datetime(2023, 3, 10).timestamp())}
    tool = _tool({
        "furniture assembled": [bookshelf],
        "furniture built": [bookshelf],
    }, facts)

    out = tool.evidence_count("furniture", ["assembled", "built"])

    roster = out["roster"]
    assert [r["fact_id"] for r in roster] == ["f_shelf"]
    assert sorted(roster[0]["labels"]) == ["assembled", "built"]


def test_roster_is_ordered_by_date_so_a_window_can_be_walked():
    """Chronological, because every count question here is time-bounded.

    "in the past two weeks", "in the last month", "before making an offer" —
    the reader's remaining job is to cut the list at a boundary, which it can
    only do if the list is in date order to begin with.
    """
    facts = {
        "f_may": SimpleNamespace(occurred_start=datetime(2023, 5, 20).timestamp()),
        "f_mar": SimpleNamespace(occurred_start=datetime(2023, 3, 10).timestamp()),
        "f_apr": SimpleNamespace(occurred_start=datetime(2023, 4, 15).timestamp()),
    }
    tool = _tool({
        "furniture bought": [
            _card("f_may", "2023-05-20"),
            _card("f_mar", "2023-03-10"),
            _card("f_apr", "2023-04-15"),
        ],
    }, facts)

    out = tool.evidence_count("furniture", ["bought"])

    assert [r["fact_id"] for r in out["roster"]] == ["f_mar", "f_apr", "f_may"]


def test_an_undated_hit_sorts_last_instead_of_being_dropped():
    """A missing date is a reason to look, not a reason to vanish.

    Dropping it would silently undercount; crashing the sort would lose the
    whole call. It goes to the end where the reader can see it.
    """
    facts = {"f_dated": SimpleNamespace(occurred_start=datetime(2023, 4, 1).timestamp())}
    tool = _tool({
        "items acquired": [_card("f_undated", None), _card("f_dated", "2023-04-01")],
    }, facts)

    out = tool.evidence_count("items", ["acquired"])

    assert [r["fact_id"] for r in out["roster"]] == ["f_dated", "f_undated"]
    assert out["roster"][-1]["occurred_epoch"] is None


def test_groups_are_still_returned_unchanged():
    """Additive only. `groups`/`candidate_count` are an existing contract and
    the planner prompt still describes them; the roster rides alongside."""
    facts = {"f_a": SimpleNamespace(occurred_start=datetime(2023, 4, 1).timestamp())}
    tool = _tool({"x y": [_card("f_a", "2023-04-01")]}, facts)

    out = tool.evidence_count("x", ["y"])

    assert out["groups"][0]["label"] == "y"
    assert out["groups"][0]["candidate_count"] == 1
    assert out["groups"][0]["items"][0]["fact_id"] == "f_a"


# ---------------------------------------------------------------------------
# The roster only counts if it reaches the model. `_run_tool` hands the
# planner exactly what `EvidenceStore.ingest` returns, and that observation is
# counts and ids — so the roster has to ride along in it, or it is a data
# structure nobody reads.
# ---------------------------------------------------------------------------

import json  # noqa: E402

from sodamem.answer.loop import PlannerConfig, run_planner_loop  # noqa: E402
from sodamem.answer.protocol import PlannerState  # noqa: E402
from sodamem.context.store import EvidenceStore  # noqa: E402
from sodamem.llm.testing import ScriptedProvider  # noqa: E402

_ORDINARY = {
    "question_classification": {
        "type": "ordinary",
        "comparison_requires_count_or_sum": False,
    }
}


def test_ingest_surfaces_the_roster_to_the_planner_observation():
    payload = {
        "query": "furniture",
        "labels": ["bought"],
        "groups": [{"label": "bought", "candidate_count": 1, "items": [
            {"fact_id": "f_a", "evidence_id": "ev_fact:f_a", "event_date": "2023-04-01"},
        ]}],
        "roster": [
            {"fact_id": "f_a", "evidence_id": "ev_fact:f_a",
             "event_date": "2023-04-01", "occurred_epoch": 1.0,
             "content": "bought a table", "labels": ["bought"]},
        ],
    }

    observation = EvidenceStore().ingest("browser_count_evidence", {}, payload, 0)

    assert observation["roster"] == payload["roster"]


def test_ingest_omits_roster_for_tools_that_do_not_produce_one():
    """Every other tool's observation keeps its existing shape."""
    payload = {"items": [{"fact_id": "f_a", "evidence_id": "ev_fact:f_a"}]}

    observation = EvidenceStore().ingest("browser_search", {}, payload, 0)

    assert "roster" not in observation


def test_the_roster_stays_out_of_the_planner_when_the_arm_is_off():
    """Scoring-path change, so it ships behind a flag like `abstention_gate`.
    (Default flipped ON 0731 by the stable-set measurement — see
    test_answer_defaults — but the off position must still mean off.)

    The tool still computes the roster — an unread field costs nothing and
    keeps the tool free of loop config — but nothing reaches the model until
    the arm is on.
    """
    facts = {"f_a": SimpleNamespace(occurred_start=datetime(2023, 4, 1).timestamp())}
    rows = {"x y": [_card("f_a", "2023-04-01")]}

    class _Tools:
        def dispatch(self, name, **kwargs):
            return _tool(rows, facts).evidence_count("x", ["y"])

    step = json.dumps({
        "state_update": _ORDINARY,
        "decision": {"action": "tool_calls", "calls": [
            {"tool": "browser_count_evidence", "args": {"query": "x", "labels": ["y"]}},
        ]},
    })
    result = run_planner_loop(
        "How many x?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider([step, step]),
        config=PlannerConfig(max_steps=1, count_roster=False),
    )

    history = result.state["search_history"]
    assert history, "expected at least one recorded call"
    assert all("roster" not in row for row in history)


def test_the_roster_reaches_the_planner_when_the_arm_is_on():
    """The other half of the flag: on means the model actually sees it."""
    facts = {"f_a": SimpleNamespace(occurred_start=datetime(2023, 4, 1).timestamp())}
    rows = {"x y": [_card("f_a", "2023-04-01")]}

    class _Tools:
        def dispatch(self, name, **kwargs):
            return _tool(rows, facts).evidence_count("x", ["y"])

    step = json.dumps({
        "state_update": _ORDINARY,
        "decision": {"action": "tool_calls", "calls": [
            {"tool": "browser_count_evidence", "args": {"query": "x", "labels": ["y"]}},
        ]},
    })
    result = run_planner_loop(
        "How many x?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider([step, step]),
        config=PlannerConfig(max_steps=1, count_roster=True),
    )

    # search_history is what `PlannerState.compact()` projects into the
    # planner's user message; `planner_trace` observations never reach the
    # model at all, so asserting there would prove nothing about what it sees.
    history = result.state["search_history"]
    rosters = [row["roster"] for row in history if "roster" in row]
    assert rosters, "arm is on; the roster must reach the planner payload"
    assert rosters[0][0]["fact_id"] == "f_a"
    assert rosters[0][0]["labels"] == ["y"]


def test_the_roster_actually_lands_in_the_planner_user_message():
    """End to end: the bytes the model reads contain the enumerated list.

    `search_history` carrying it is necessary but not sufficient — the
    signature key is projected out at this boundary, so this pins that the
    roster is not projected out with it.
    """
    from sodamem.answer.loop import _planner_user_message

    facts = {"f_a": SimpleNamespace(occurred_start=datetime(2023, 4, 1).timestamp())}
    roster = _tool({"x y": [_card("f_a", "2023-04-01")]}, facts).evidence_count(
        "x", ["y"])["roster"]
    state = PlannerState(objective="How many x?")
    state.search_history.append({
        "step": 0, "tool": "browser_count_evidence", "args": {},
        "signature": "sig", "new_evidence": 1, "returned_rows": 1,
        "success": True, "roster": roster,
    })

    message = _planner_user_message(
        question="How many x?", current_date="2023-06-01", state=state,
        evidence=EvidenceStore(), step=1, max_steps=12,
        allowed_tools=("browser_count_evidence",),
    )

    assert "roster" in message
    assert "f_a" in message
    assert "sig" not in message
