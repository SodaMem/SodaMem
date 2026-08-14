"""v1.3 TemporalEvidenceRank — rank in-window evidence; keep undated visible."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional


def _parse_d(s: Any) -> Optional[date]:
    if isinstance(s, str) and re.match(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _row_date(row: dict[str, Any]) -> Optional[date]:
    for k in ("event_date", "date", "session_date", "occurred_date"):
        d = _parse_d(row.get(k))
        if d:
            return d
    ep = row.get("occurred_epoch")
    if isinstance(ep, (int, float)) and ep > 0:
        try:
            from sodamem.memory._shared import _safe_fromtimestamp

            dt = _safe_fromtimestamp(ep)
            if dt is not None:
                return dt.date()
        except Exception:
            return None
    # sniff ISO date in content
    text = str(row.get("content") or row.get("text") or "")
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if m:
        return _parse_d(m.group(1))
    return None


def window_bounds(window: Optional[dict[str, str]]) -> tuple[Optional[date], Optional[date]]:
    if not window:
        return None, None
    return _parse_d(window.get("from_date")), _parse_d(window.get("to_date"))


def in_window(d: Optional[date], lo: Optional[date], hi: Optional[date]) -> bool:
    if d is None or lo is None:
        return False
    if hi is None:
        hi = lo
    return lo <= d <= hi


def needs_day_pin(question: str, schema_task: str) -> bool:
    ql = (question or "").lower()
    if schema_task != "TEMPORAL_EVENT":
        return False
    return bool(
        re.search(r"\b(who did i|who was|which bike|which .+ did i|from whom)\b", ql)
        or "last saturday" in ql
        or "past weekend" in ql
        or "last weekend" in ql
        or "two weeks ago" in ql
        or "the past weekend" in ql
    )


def pin_rows(
    rows: list[dict[str, Any]],
    window: Optional[dict[str, str]],
    *,
    keep_undated: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (in_window, out_of_window). Undated go to out unless keep_undated."""
    lo, hi = window_bounds(window)
    if lo is None:
        return list(rows), []
    inn: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for r in rows:
        d = _row_date(r)
        if d is None:
            (inn if keep_undated else out).append(r)
        elif in_window(d, lo, hi):
            inn.append(r)
        else:
            out.append(r)
    return inn, out


def pin_advisory(
    window: Optional[dict[str, str]],
    *,
    in_n: int,
    out_n: int,
    out_snips: list[str],
) -> str:
    lo = (window or {}).get("from_date")
    hi = (window or {}).get("to_date") or lo
    lines = [
        f"PINNED calendar window: {lo} .. {hi}.",
        f"In-window rows: {in_n}; out-of-window distractors: {out_n}.",
        "Rank in-window first for who/which; do not pick companions/objects "
        "from out-of-window rows even if venue/type is similar.",
    ]
    if out_snips:
        lines.append("Out-of-window examples: " + " | ".join(out_snips[:4]))
    return " ".join(lines)
