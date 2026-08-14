"""Code-side count/sum over extracted include-set (bypasses reader for the number)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sodamem_struct.candidates import collect_candidates
from sodamem_struct.classify import Route, route_question
from sodamem_struct.extract import extract_events
from sodamem_struct.membership import extract_money


@dataclass
class AggregateResult:
    ok: bool
    kind: str  # count | sum
    value: Optional[float]
    answer: str
    template: str
    included: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


def _fmt_count(n: int) -> str:
    return str(n)


def _fmt_sum(x: float) -> str:
    if abs(x - round(x)) < 1e-6:
        return f"${int(round(x)):,}"
    return f"${x:,.2f}".rstrip("0").rstrip(".")


def _parse_iso(s: str) -> Optional[date]:
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", (s or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _apply_time_window(events, question: str, current_date: str):
    try:
        from sodamem_opt.timewords import resolve_time_window
    except Exception:
        try:
            from sodamem.answer.timewords import resolve_time_window
        except Exception:
            return events
    window = resolve_time_window(question, current_date=current_date)
    if not window:
        return events
    lo = _parse_iso(str(window.get("from_date") or ""))
    hi = _parse_iso(str(window.get("to_date") or ""))
    if lo is None or hi is None:
        return events
    kept = []
    for e in events:
        d = _parse_iso(e.date)
        if d is None or lo <= d <= hi:
            kept.append(e)
    return kept


# Only these templates have enough precision for hard numeric override.
# Others extract for diagnostics / future soft use but must not replace Reader.
_OVERRIDE_TEMPLATES = frozenset({
    "dinner_party",
    "wedding",
    "project_lead",
    "bike_service",
})


def try_aggregate(
    question: str,
    evidence: Any,
    *,
    current_date: str = "",
    route: Optional[Route] = None,
) -> AggregateResult:
    route = route or route_question(question)
    if route.kind not in {"set_count", "set_sum"}:
        return AggregateResult(False, "", None, "", route.template, reason="not_set_route")
    # Sum path is not ready — money extraction over-counts chat mentions.
    if route.kind == "set_sum":
        return AggregateResult(
            False, "sum", None, "", route.template, reason="sum_override_disabled", confidence=0.0
        )

    cands = collect_candidates(evidence)
    if not cands:
        return AggregateResult(
            False, route.kind.replace("set_", ""), None, "", route.template,
            reason="no_candidates", confidence=0.0,
        )

    events = extract_events(route.template, cands, question=question)
    events = _apply_time_window(events, question, current_date)
    included_dump = [
        {"content": e.text, "date": e.date, "key": e.key, "amount": e.amount}
        for e in events
    ]

    if route.kind == "set_count":
        n = len(events)
        # Only override when extraction found a compact, non-empty set.
        if n == 0:
            return AggregateResult(
                False, "count", None, "", route.template, included_dump, [],
                reason="no_extracted_events", confidence=0.15,
            )
        if n > 12:
            return AggregateResult(
                False, "count", float(n), _fmt_count(n), route.template, included_dump, [],
                reason="too_many_events_uncertain", confidence=0.25,
            )
        # Singleton answers for plural set questions are usually incomplete recall.
        pluralish = bool(
            re.search(r"\bhow many\b.+\b(parties|weddings|projects|bikes|items|types)\b", question, re.I)
        )
        if n == 1 and pluralish:
            return AggregateResult(
                False, "count", float(n), _fmt_count(n), route.template, included_dump, [],
                reason="singleton_incomplete", confidence=0.35,
            )
        if route.template not in _OVERRIDE_TEMPLATES:
            return AggregateResult(
                False, "count", float(n), _fmt_count(n), route.template, included_dump, [],
                reason=f"template_no_override:{route.template}", confidence=0.4,
            )
        conf = 0.9 if 2 <= n <= 8 else 0.75
        return AggregateResult(
            True, "count", float(n), _fmt_count(n), route.template, included_dump, [],
            reason="code_count_extract", confidence=conf,
        )

    # sum
    total = 0.0
    used = 0
    for e in events:
        amt = e.amount if e.amount is not None else extract_money(e.text)
        if amt is None:
            continue
        total += amt
        used += 1
    if used == 0:
        # fallback: money from raw candidates under charity/sum templates
        for c in cands:
            if route.template == "charity_raise" and not re.search(
                r"charit|fundrais|donat|raised|hospital|shelter|cancer", c.content or "", re.I
            ):
                continue
            amt = extract_money(c.content)
            if amt is None:
                continue
            total += amt
            used += 1
            included_dump.append({"content": c.content[:200], "date": c.date, "amount": amt})
    if used == 0:
        return AggregateResult(
            False, "sum", None, "", route.template, included_dump, [],
            reason="no_amounts", confidence=0.2,
        )
    return AggregateResult(
        True, "sum", total, _fmt_sum(total), route.template, included_dump, [],
        reason="code_sum_extract", confidence=0.85 if used >= 2 else 0.65,
    )
