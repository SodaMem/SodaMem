"""value_board organizer — resolves a single "current/latest value" slot from
ranked evidence when the read-side query-plan classifier
(`organizers/query_plan.py`) judges the question needs one.

This is one of the two REAL
organizers (the other is `enumeration_sweep.py`) that the deleted
`_classify_evidence_contract` type-routing hack (spec §6.3, 0714
user-vetoed) used to sit alongside as a decoy. Unlike that hack, this
module's output only ever narrows/orders evidence a caller already chose to
consult — see `sodamem.context.build_context`'s `organizer` param docstring
for why that caller-selects-the-organizer shape is what makes the type
router unnecessary rather than a rename of it.

Env-read -> parameter cleanup: the source read `BENCHMARK_READER_QUERY_PLAN_MIN_CONF` /
`BENCHMARK_READER_QUERY_PLAN_STRONG_MIN_CONF` via `os.getenv(...)` at call
time inside `_valid_value_board_plan` / `_strong_value_board_safe`. Both are
now explicit keyword parameters (`min_conf` / `strong_min_conf`) carrying
the same numeric defaults the env vars carried — a caller wiring this up
from config supplies them once at composition time instead of this module
reading process environment on every invocation.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sodamem.context.store import EvidenceStore, _one_line, _query_terms, _reader_source_text

_HISTORY_INTENT_RE = re.compile(
    r"\b(previous|former|formerly|used to|earlier|before |prior|originally|"
    r"initial(?:ly)?|at first|old|last time|history|past)\b", re.I,
)
_DELTA_RE = re.compile(
    r"\b(new\s+followers?|additional|gained|growth|increase|delta|last\s+\w+\s+weeks?)\b",
    re.I,
)
_AGGREGATE_SCOPE_RE = re.compile(
    r"\b(total|sum|combined|altogether|overall|all|in\s+total|plus|together|"
    r"completed\s+.*courses?|course[s]?\s+.*completed|donations?|expenses?)\b",
    re.I,
)
_STATE_SCOPE_RE = re.compile(r"\b(where|location|stored?|keep|kept|put|place)\b", re.I)
_RANGE_RE = re.compile(
    r"\b(?:(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)|"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*[-–]\s*"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))\s+"
    r"(hours?|minutes?|days?|weeks?)\b",
    re.I,
)
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*(am|pm)?\b", re.I)
_WORD_NUM = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12",
}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_one_line(v, 80) for v in value if _one_line(v, 80)]
    item = _one_line(value, 80)
    return [item] if item else []


def _plan_confidence(plan: dict[str, Any]) -> float:
    try:
        return float(plan.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _valid_value_board_plan(plan: dict[str, Any], *, min_conf: float = 0.55) -> bool:
    if _plan_confidence(plan) < min_conf:
        return False
    if plan.get("answer_shape") not in ("single_slot_current_value", "current_value", "historical_value"):
        return False
    if plan.get("temporal_intent") not in ("current", "latest"):
        return False
    slot = plan.get("slot_hint") if isinstance(plan.get("slot_hint"), dict) else {}
    if not (slot.get("metric") and slot.get("value_type")):
        return False
    # The planner sometimes correctly classifies answer_shape=current_value
    # but leaves requires_value_board=false. Treat the structural
    # shape+slot as the trigger; this is still semantic-plan driven, not
    # keyword driven.
    return bool(plan.get("requires_value_board") or slot.get("metric"))


def _record_haystack(raw: dict[str, Any]) -> str:
    return " ".join([
        _reader_source_text(raw),
        str(raw.get("predicate_canonical") or ""),
        str(raw.get("predicate_raw") or ""),
        json.dumps(raw.get("entity_roles") or {}, ensure_ascii=False),
    ]).lower()


# Per-value_type unit families. The ported source pooled count and duration
# into ONE whitelist, which on a count question admitted "owned for 3 years"
# (duration=3) as a count candidate while rejecting "one piano"
# (item_count) — exactly backwards. Card census over a full traced run: 1224
# `*_count` quantities blocked, 1516 durations admitted into count questions;
# q193 resolved a piano's ownership years as the instrument count, wrong in
# 12 of 13 runs. An empty unit stays admissible in every family: the
# extractor not labelling a number must not start dropping it now.
_UNIT_FAMILIES: dict[str, Any] = {
    "money": lambda u: u in ("", "money"),
    "count": lambda u: u in ("", "count", "item_count") or u.endswith("_count"),
    "duration": lambda u: u in ("", "duration", "hours", "minutes", "days", "weeks", "months", "years"),
    "time": lambda u: u in ("", "time"),
}


def _value_from_record(raw: dict[str, Any], value_type: str) -> tuple[Any, str] | None:
    quantity = raw.get("quantity") if isinstance(raw.get("quantity"), dict) else {}
    value = quantity.get("value")
    unit = str(quantity.get("unit") or "")
    if value is not None:
        family = _UNIT_FAMILIES.get(value_type)
        if family and not family(unit):
            return None
        return value, unit or value_type
    text = _reader_source_text(raw)
    if value_type == "time":
        m = _TIME_RE.search(text)
        if m:
            return f"{m.group(1)}{(' ' + m.group(2).lower()) if m.group(2) else ''}", "time"
    if value_type in ("duration", "count"):
        m = _RANGE_RE.search(text)
        if m:
            lo = m.group(1) or _WORD_NUM.get((m.group(3) or "").lower(), m.group(3))
            hi = m.group(2) or _WORD_NUM.get((m.group(4) or "").lower(), m.group(4))
            return f"{lo}-{hi}", (m.group(5) or value_type).lower()
    return None


def build_value_board(
    *,
    plan: dict[str, Any],
    evidence: EvidenceStore,
    reader_ids: list[str],
    limit: int = 8,
    strong: bool = False,
    question: str = "",
    strong_min_conf: float = 0.75,
) -> tuple[str, list[str], list[str]]:
    slot = plan.get("slot_hint") if isinstance(plan.get("slot_hint"), dict) else {}
    metric_terms = _query_terms(str(slot.get("metric") or ""))
    entity_terms = set()
    for item in _as_list(slot.get("entities")):
        entity_terms.update(_query_terms(item))
    value_type = str(slot.get("value_type") or "unknown").lower()
    total_vs_delta = str(slot.get("total_vs_delta") or "unknown").lower()
    if not metric_terms and not entity_terms:
        return "", [], []

    candidates: list[dict[str, Any]] = []
    for rid in reader_ids:
        rec = evidence.records.get(rid)
        if rec is None:
            continue
        raw = rec.raw
        if raw.get("modality") == "external_info":
            continue
        val = _value_from_record(raw, value_type)
        if val is None:
            continue
        hay = _record_haystack(raw)
        metric_hits = sum(term in hay for term in metric_terms)
        entity_hits = sum(term in hay for term in entity_terms)
        if metric_terms and metric_hits == 0:
            continue
        if entity_terms and entity_hits == 0:
            continue
        pred = str(raw.get("predicate_canonical") or "").lower()
        future = pred.startswith("plan_") or "future" in pred
        delta = bool(_DELTA_RE.search(hay)) or any(
            term in pred
            for term in ("growth", "engagement", "likes", "comments")
        )
        if future:
            score = -5
        elif total_vs_delta == "total" and delta:
            score = -3
        else:
            score = metric_hits * 2 + entity_hits
            if any(term in pred for term in metric_terms):
                score += 2
            if total_vs_delta == "total" and any(word in pred for word in ("count", "total", "nearing")):
                score += 1
            if total_vs_delta == "total" and ("nearing" in pred or "close to" in hay):
                score += 2
        if score <= 0:
            continue
        candidates.append({
            "evidence_id": rid,
            "value": val[0],
            "unit": val[1],
            "time": raw.get("document_time") or raw.get("session_time") or "",
            "predicate": raw.get("predicate_canonical") or raw.get("predicate_raw") or "",
            "score": score,
            "support": _one_line(_reader_source_text(raw), 360),
        })
    if not candidates:
        return "", [], []
    candidates.sort(key=lambda row: (str(row["time"]), row["score"]), reverse=True)
    top = candidates[0]

    strong_safe, strong_reason = _strong_value_board_safe(
        plan, candidates, question, strong_min_conf=strong_min_conf,
    )
    if strong and strong_safe:
        # Narrow to top-4, NOT to a single row: the escape rule below
        # ("unless … the question asks for a historical value") is unusable
        # if the runner-up values are no longer in the prompt (0706 audit —
        # pool-collapse tail risk).
        keep = candidates[:4]
        lines = [
            "runtime_resolved_value (semantic query-plan contract):",
            f"- resolved_value={top['value']} {top['unit']}",
            f"- evidence={top['evidence_id']} time={top['time'] or 'unknown'} predicate={_one_line(top['predicate'], 80)}",
            # Softened from "Use resolved_value … Do not recompute" (the
            # reader-side addendum carried the "do not recompute" half): q053's
            # evidence held both "need 300 total" and "have 200 so far", gold
            # 100 = the difference — the reader had both rows in the prompt and
            # the old rule forbade the subtraction. Verified by paired replay:
            # this wording flips q053 while all 14/14 KU wins hold.
            "- rule=resolved_value is the best-ranked current/latest candidate for plan_metric. Verify it against the cited evidence before relying on it. If the question asks for a difference, remaining amount, or a total that the evidence supports computing, do that arithmetic from the evidence instead of echoing resolved_value.",
            f"- plan_metric={_one_line(slot.get('metric'), 120)} value_type={value_type} total_vs_delta={total_vs_delta}",
            f"- lower_ranked_candidates_suppressed={max(0, len(candidates) - len(keep))}",
            f"- top_support={top['support']}",
        ]
        for row in keep[1:]:
            lines.append(
                f"- retained_candidate={row['value']} {row['unit']} time={row['time'] or 'unknown'} "
                f"evidence={row['evidence_id']} predicate={_one_line(row['predicate'], 80)}"
            )
        keep_ids = [row["evidence_id"] for row in keep]
        return "\n".join(lines), keep_ids, keep_ids
    lines = [
        "runtime_value_candidates (semantic query-plan advisory; do not use for historical, aggregate/sum, state/location, or future-plan questions):",
        f"- plan_metric={_one_line(slot.get('metric'), 120)} value_type={value_type} total_vs_delta={total_vs_delta}",
        f"- top_candidate={top['value']} {top['unit']} time={top['time'] or 'unknown'} evidence={top['evidence_id']} predicate={_one_line(top['predicate'], 80)}",
    ]
    if strong:
        lines.append(f"- strong_mode_blocked={strong_reason}")
    for row in candidates[:limit]:
        lines.append(
            f"- value={row['value']} {row['unit']} time={row['time'] or 'unknown'} "
            f"score={row['score']} evidence={row['evidence_id']} "
            f"predicate={_one_line(row['predicate'], 80)} support={row['support']}"
        )
    ids = [row["evidence_id"] for row in candidates[:limit]]
    return "\n".join(lines), ids, []


def _strong_value_board_safe(
    plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    question: str = "",
    *,
    strong_min_conf: float = 0.75,
) -> tuple[bool, str]:
    """Precision-first gate for destructive current-value intervention.

    Planner output is a semantic route, not authority. Strong mode may
    remove lower-ranked evidence from the reader, so only allow it for a
    single runtime slot with competing values. This is intentionally
    reader-side only: it does not create entities, canonical predicates, or
    store mutations.
    """
    if _plan_confidence(plan) < strong_min_conf:
        return False, "low_plan_confidence"
    if _HISTORY_INTENT_RE.search(question or ""):
        # Lexical backstop independent of planner labels: a
        # previous/former/original value question must never be
        # strong-narrowed to the LATEST value (q432/q116-class gold IS the
        # older value).
        return False, "history_intent_question"
    if plan.get("answer_shape") not in ("single_slot_current_value", "current_value"):
        return False, "not_current_value_shape"
    if plan.get("temporal_intent") not in ("current", "latest"):
        return False, "not_current_or_latest"
    slot = plan.get("slot_hint") if isinstance(plan.get("slot_hint"), dict) else {}
    metric = str(slot.get("metric") or "")
    value_type = str(slot.get("value_type") or "unknown").lower()
    total_vs_delta = str(slot.get("total_vs_delta") or "unknown").lower()
    if value_type not in ("money", "count", "time", "duration"):
        return False, "unsupported_value_type"
    if total_vs_delta == "delta":
        return False, "delta_metric"
    if _AGGREGATE_SCOPE_RE.search(metric):
        return False, "aggregate_scope"
    if _STATE_SCOPE_RE.search(metric) or value_type == "location":
        return False, "state_scope"
    hint = plan.get("enumeration_hint") if isinstance(plan.get("enumeration_hint"), dict) else {}
    if hint.get("object_type") or hint.get("actions"):
        return False, "enumeration_hint_present"
    if len(candidates) < 2:
        return False, "no_competing_values"
    top = candidates[0]
    top_predicate = str(top.get("predicate") or "")
    top_text = " ".join([top_predicate, str(top.get("support") or "")])
    if top_predicate.startswith("plan_") or "future" in top_predicate.lower():
        return False, "top_future_plan"
    if _DELTA_RE.search(top_text) and total_vs_delta == "total":
        return False, "top_delta_for_total"
    if not str(top.get("time") or ""):
        return False, "top_time_missing"
    top_value = str(top.get("value"))
    competing = [
        row for row in candidates[1:]
        if str(row.get("value")) != top_value and row.get("unit") == top.get("unit")
    ]
    if not competing:
        return False, "no_different_same_unit_value"
    if not any(str(row.get("time") or "") for row in competing):
        return False, "competing_time_missing"
    return True, "single_slot_current_value"


__all__ = ["build_value_board"]
