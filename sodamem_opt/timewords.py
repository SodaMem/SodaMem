"""Extended relative-date windows for Plan B (wraps sodamem.answer.timewords)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sodamem.answer.timewords import resolve_time_window as _base_resolve

_EN_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _parse_current_date(current_date: str) -> Optional[date]:
    text = (current_date or "").strip()
    if not text:
        return None
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
        "from_ts": datetime(start.year, start.month, start.day).timestamp(),
        "to_ts": datetime(end.year, end.month, end.day, 23, 59, 59, 999999).timestamp(),
    }


def _band(center: date, radius_days: int) -> tuple[date, date]:
    return center - timedelta(days=radius_days), center + timedelta(days=radius_days)


def resolve_time_window(question: str, *, current_date: str) -> Optional[dict[str, Any]]:
    """Base forms first; then month/year relatives with a recall-friendly band."""
    base = _base_resolve(question, current_date=current_date)
    if base is not None:
        # Widen day/week singles slightly so sparse session dates still hit.
        today = _parse_current_date(current_date)
        expr = str(base.get("expression") or "")
        if today and re.search(r"\bday|week\b", expr):
            start = date.fromisoformat(base["from_date"])
            end = date.fromisoformat(base["to_date"])
            if start == end:
                lo, hi = _band(start, 3 if "day" in expr else 2)
                return _window(expr, lo, hi)
        return base

    today = _parse_current_date(current_date)
    if today is None:
        return None
    text = (question or "").lower()

    match = re.search(
        r"\b(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"\s+(day|week|month|year)s?\s+ago\b",
        text,
    )
    if match:
        raw_n, unit = match.group(1), match.group(2)
        n = int(raw_n) if raw_n.isdigit() else _EN_NUM.get(raw_n)
        if n is None:
            return None
        center = today - timedelta(days=n * _UNIT_DAYS[unit])
        radius = { "day": 3, "week": 4, "month": 14, "year": 45 }[unit]
        lo, hi = _band(center, radius)
        return _window(match.group(0), lo, hi)

    if re.search(r"\btwo\s+months?\s+ago\b", text):
        center = today - timedelta(days=60)
        lo, hi = _band(center, 14)
        return _window("two months ago", lo, hi)

    return None
