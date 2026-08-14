"""Resolve a question's relative date expression into an explicit window.

Read-side only, and deliberately not part of `active_prompt_fingerprint()` for
the same reason `planner.py` is not: this changes which slice of an existing
store gets searched, never what is in the store.

Why in code rather than in a prompt: three of SodaMem's five non-abstention TR
misses on the 0729 full-500 carry a relative expression the planner never
turned into a window before searching ("a week ago", "the past weekend",
"10 days ago"). The tools have taken `from_ts`/`to_ts` since I1 hardened their
parsing — the plumbing was never the problem, the arithmetic was, and date
arithmetic is the thing these models are measurably worst at.

Only unambiguous forms are handled. Named holidays are out: "Valentine's day"
is a calendar lookup with its own failure mode, and a wrong guess is worse
than no guess because it would narrow the search to the wrong days.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

__all__ = ["resolve_time_window"]

_UNIT_DAYS = {"day": 1, "week": 7}


def _parse_current_date(current_date: str) -> Optional[date]:
    text = (current_date or "").strip()
    if not text:
        return None
    # LongMemEval question dates arrive as "2023/05/30 (Tue) 23:29" as well as
    # plain ISO; take the leading date and normalise the separator.
    match = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _window(expression: str, start: date, end: date) -> dict[str, Any]:
    return {
        "expression": expression,
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        # Epoch bounds the tools accept directly. `to_ts` is the last instant
        # of `to_date`, matching I1 AC1's inclusive upper bound — a date-only
        # upper bound includes events through 23:59:59.999999 local time.
        "from_ts": datetime(start.year, start.month, start.day).timestamp(),
        "to_ts": datetime(end.year, end.month, end.day, 23, 59, 59, 999999).timestamp(),
    }


def resolve_time_window(question: str, *, current_date: str) -> Optional[dict[str, Any]]:
    """Return an explicit window for `question`'s relative date, or None.

    None means "no unambiguous expression found" — the caller must carry on
    exactly as before. Returning a guessed window would be worse than
    returning nothing: it narrows the search to the wrong days.
    """
    today = _parse_current_date(current_date)
    if today is None:
        return None
    text = (question or "").lower()

    match = re.search(r"\b(\d+)\s+(day|week)s?\s+ago\b", text)
    if match:
        span = int(match.group(1)) * _UNIT_DAYS[match.group(2)]
        day = today - timedelta(days=span)
        return _window(match.group(0), day, day)

    if re.search(r"\ba\s+week\s+ago\b", text):
        day = today - timedelta(days=7)
        return _window("a week ago", day, day)

    if re.search(r"\byesterday\b", text):
        day = today - timedelta(days=1)
        return _window("yesterday", day, day)

    if re.search(r"\b(last|past)\s+weekend\b", text):
        # Saturday of the most recent weekend that is not the current one.
        # weekday() is 0=Mon, and `weekday() + 2` lands on the right Saturday
        # for every day of the week without a special case: Monday steps back
        # 2 days (the weekend that just ended, not the one nine days back),
        # Thursday 5, and Saturday 7 — which is the *previous* Saturday,
        # exactly what "last weekend" means when you say it on a Saturday.
        saturday = today - timedelta(days=today.weekday() + 2)
        return _window(
            re.search(r"\b(last|past)\s+weekend\b", text).group(0),
            saturday, saturday + timedelta(days=1),
        )

    return None
