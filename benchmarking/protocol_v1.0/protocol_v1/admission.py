"""Evidence Admission Gate — filter roster rows by QuestionSchema."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from protocol_v1.schema import QuestionSchema

_PLAN_RE = re.compile(
    r"\b(plan(?:ning)?|thinking of|consider(?:ing)?|want to|going to|"
    r"would like|i[' ]?ll|i will)\b",
    re.I,
)


def _content(item: dict[str, Any]) -> str:
    return str(item.get("content") or "")


def _item_date(item: dict[str, Any]) -> Optional[date]:
    ed = item.get("event_date")
    if isinstance(ed, str) and re.match(r"\d{4}-\d{2}-\d{2}", ed):
        try:
            return date.fromisoformat(ed[:10])
        except ValueError:
            pass
    ep = item.get("occurred_epoch")
    if isinstance(ep, (int, float)) and ep > 0:
        try:
            return datetime.fromtimestamp(ep).date()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def admit_roster(
    roster: list[dict[str, Any]],
    schema: QuestionSchema,
    *,
    current_date: str = "",
    window: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Return schema-admitted candidates (may be empty)."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in roster:
        text = _content(item)
        tl = text.lower()
        if not text.strip():
            continue

        # Exclusion predicates (boundary gate)
        if any(ex.lower() in tl for ex in schema.exclude_predicates):
            # Allow if an include hint also strongly matches and exclude is weak
            if not any(inc.lower() in tl for inc in schema.include_hints):
                continue

        # Planning ≠ leading / attended
        if schema.task in {"COUNT_DISTINCT", "SUM", "ORDERED_LIST"}:
            if _PLAN_RE.search(tl) and not re.search(
                r"\b(attended|bought|spent|led|leading|fixed|serviced|"
                r"completed|raised|purchased|downloaded|replaced)\b",
                tl,
            ):
                continue

        # Include hints: if present, prefer rows that match at least one
        if schema.include_hints:
            if not any(h.lower() in tl for h in schema.include_hints):
                labels = " ".join(str(x).lower() for x in (item.get("labels") or []))
                if not any(h.lower() in labels for h in schema.include_hints):
                    # Soft: still keep if predicate tokens appear
                    pred_toks = [t for t in schema.predicate.split() if len(t) > 3]
                    if pred_toks and not any(t in tl for t in pred_toks):
                        continue

        # Time window hard filter when dated
        if window is not None:
            d = _item_date(item)
            if d is not None:
                try:
                    lo = date.fromisoformat(window["from_date"])
                    hi = date.fromisoformat(window["to_date"])
                    if d < lo or d > hi:
                        continue
                except (KeyError, ValueError):
                    pass

        key = str(item.get("fact_id") or item.get("evidence_id") or "")
        sig = re.sub(r"\s+", " ", tl)[:100]
        dedupe = key or sig
        if dedupe in seen:
            continue
        seen.add(dedupe)
        kept.append(item)
    return kept
