"""Relative dates in the question get resolved in code, not by the model.

The 0729 TR failures (9 of 53) split differently from the anchor run's,
which is why this is not the anchor-quadruple fix that list called for. Four
are false missing and belong to the abstention gate. Of the remaining five,
exactly one (q313, "how many weeks since I recovered from the flu when I went
on my 10th jog") needs an anchor pair. The other three share one shape: the
question carries a relative date expression and the planner went searching
without ever turning it into a window.

  q340  "...that I participated in a week ago"      -> found the wrong relative
  q339  "Which bike did I fix the past weekend"     -> wrong bike
  q347  "What kitchen appliance did I buy 10 days ago" -> claimed nothing exists

The tools already take `from_ts`/`to_ts`, and I1 hardened the parsing of both.
Nothing was wrong with the plumbing — the window simply never got computed.
Asking the model to do the date arithmetic is asking it to do the thing it is
measurably worst at, so compute it and hand it over resolved.

Scope is deliberately narrow: forms with one unambiguous reading. Named
holidays ("Valentine's day") are out — that is a calendar lookup, a different
problem with a different failure mode, and guessing at it would be worse than
leaving it to the model.
"""
from __future__ import annotations

from sodamem.answer.timewords import resolve_time_window


def test_n_days_ago_is_that_single_day():
    window = resolve_time_window("What kitchen appliance did I buy 10 days ago?",
                                 current_date="2023-03-25")
    assert window is not None
    assert window["from_date"] == "2023-03-15"
    assert window["to_date"] == "2023-03-15"
    assert window["expression"] == "10 days ago"


def test_a_week_ago_is_seven_days_back():
    window = resolve_time_window("What did I do a week ago?", current_date="2023-03-25")
    assert (window["from_date"], window["to_date"]) == ("2023-03-18", "2023-03-18")


def test_past_weekend_on_a_thursday_is_the_weekend_that_just_ended():
    # 2023-03-30 is a Thursday; the weekend before it is Sat 25 - Sun 26.
    window = resolve_time_window("Which bike did I fix the past weekend?",
                                 current_date="2023-03-30")
    assert (window["from_date"], window["to_date"]) == ("2023-03-25", "2023-03-26")


def test_past_weekend_on_a_monday_is_the_weekend_that_just_ended():
    # 2023-03-27 is a Monday — the weekend two days back, not nine.
    window = resolve_time_window("last weekend", current_date="2023-03-27")
    assert (window["from_date"], window["to_date"]) == ("2023-03-25", "2023-03-26")


def test_past_weekend_on_a_saturday_is_the_previous_weekend():
    """Asked on a Saturday, "last weekend" is the one before this one.

    weekday()+2 already lands there (Sat is 5, so seven days back is the
    previous Saturday). Stepping back a further week would reach two weekends
    ago, which no one means.
    """
    window = resolve_time_window("last weekend", current_date="2023-04-01")
    assert (window["from_date"], window["to_date"]) == ("2023-03-25", "2023-03-26")


def test_no_expression_returns_none_so_the_caller_is_unchanged():
    assert resolve_time_window("What speed is my internet plan?",
                               current_date="2023-05-30") is None


def test_an_unparseable_current_date_resolves_nothing():
    """No anchor, no window. Guessing one would search the wrong days."""
    assert resolve_time_window("what did I do 3 days ago?", current_date="") is None


# ---------------------------------------------------------------------------
# Wiring: the window has to reach the planner's user message, and like every
# other scoring-path change here it ships default-OFF.
# ---------------------------------------------------------------------------

import json  # noqa: E402

from sodamem.answer.loop import _planner_user_message  # noqa: E402
from sodamem.answer.protocol import PlannerState  # noqa: E402
from sodamem.context.store import EvidenceStore  # noqa: E402


def _message(*, time_window: bool):
    return _planner_user_message(
        question="What kitchen appliance did I buy 10 days ago?",
        current_date="2023-03-25",
        state=PlannerState(objective="o"), evidence=EvidenceStore(),
        step=0, max_steps=12, allowed_tools=("browser_search",),
        time_window=time_window,
    )


def test_the_resolved_window_reaches_the_planner_when_the_arm_is_on():
    payload = json.loads(_message(time_window=True))
    assert payload["resolved_time_window"]["from_date"] == "2023-03-15"
    assert payload["resolved_time_window"]["expression"] == "10 days ago"


def test_the_window_arm_is_off_by_default():
    assert "resolved_time_window" not in json.loads(_message(time_window=False))
