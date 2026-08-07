"""enumeration_sweep organizer — enumerates matching evidence rows for
count/sum/aggregate-shaped questions the query-plan classifier flags as
needing a sweep rather than a single resolved value.

`_valid_enumeration_plan` placement correction: an earlier pass of the
the port's line-range windowing put this function in `value_board.py`
(both live inside the source's 526-949 span). Corrected here per the map's
own follow-up note: its only real caller pairing is `build_enumeration_sweep`
in THIS file (both gate/build the same organizer), not anything in
`value_board.py`.
"""
from __future__ import annotations

import re
from typing import Any

from sodamem.context.organizers.value_board import _as_list, _plan_confidence, _record_haystack
from sodamem.context.store import EvidenceStore, _one_line, _query_terms, _reader_source_text

_FUTURE_PLAN_RE = re.compile(
    r"\b(plan(?:ning)?|thinking of|consider(?:ing)?|looking forward|want to|"
    r"going to|this weekend|next weekend|tomorrow|soon|would like to|"
    r"i[' ]?ll|i will|should add|going to add)\b",
    re.I,
)
_ADVICE_RE = re.compile(r"\b(recommend|suggest|tips|ideas|options|should|could)\b", re.I)


def _valid_enumeration_plan(plan: dict[str, Any], *, min_conf: float = 0.55) -> bool:
    if not plan.get("requires_enumeration_sweep"):
        return False
    if _plan_confidence(plan) < min_conf:
        return False
    if plan.get("answer_shape") not in ("enumeration", "aggregate_sum", "sum"):
        return False
    hint = plan.get("enumeration_hint") if isinstance(plan.get("enumeration_hint"), dict) else {}
    return bool(hint.get("object_type") or hint.get("actions"))


def _stem_hit(term: str, hay: str) -> bool:
    """Prefix-stem match so 'replace' hits 'replacing' and 'fix' hits 'fixed'.

    Raw substring matching missed morphology (0706 audit, q196: 'replace' is
    not a substring of 'replacing') — an advisory sweep can afford the small
    false-positive cost of a stem prefix in exchange for recall."""
    if term in hay:
        return True
    stem = term[: max(4, len(term) - 2)] if len(term) > 4 else term
    return re.search(rf"\b{re.escape(stem)}\w*", hay) is not None


def _plan_status_scoped(raw: dict[str, Any], terms: set[str]) -> str:
    """Sentence-scoped future-plan detection.

    The whole-turn regex marks real past events as plans inside multi-topic
    turns (0706 audit, q173: 3 real bake events dropped because the SAME
    turn also said "I'm thinking of … next weekend"). Scope the check to
    the sentences that actually mention the swept object/action terms."""
    text = str(_reader_source_text(raw) or "")
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    hits = [s for s in sentences if any(t in s.lower() for t in terms)] if terms else []
    target = " ".join(hits) if hits else text
    return "plan" if _FUTURE_PLAN_RE.search(target) else "actual"


def build_enumeration_sweep(
    *,
    plan: dict[str, Any],
    evidence: EvidenceStore,
    reader_ids: list[str],
    limit: int = 24,
) -> tuple[str, list[str]]:
    hint = plan.get("enumeration_hint") if isinstance(plan.get("enumeration_hint"), dict) else {}
    object_terms = _query_terms(str(hint.get("object_type") or ""))
    action_terms = set()
    for action in _as_list(hint.get("actions")):
        action_terms.update(_query_terms(action))
    slot = plan.get("slot_hint") if isinstance(plan.get("slot_hint"), dict) else {}
    entity_groups = [_query_terms(item) for item in _as_list(slot.get("entities"))]
    exclude_status = {item.lower() for item in _as_list(hint.get("exclude_status"))}
    if not object_terms and not action_terms:
        return "", []

    rows: list[dict[str, Any]] = []
    entity_row_hits = [0] * len(entity_groups)
    for rid in reader_ids:
        rec = evidence.records.get(rid)
        if rec is None:
            continue
        raw = rec.raw
        if raw.get("modality") == "external_info":
            continue
        hay = _record_haystack(raw)
        object_hits = sum(_stem_hit(term, hay) for term in object_terms)
        action_hits = sum(_stem_hit(term, hay) for term in action_terms)
        entity_hits = sum(1 for terms in entity_groups if terms and any(term in hay for term in terms))
        if plan.get("answer_shape") in ("aggregate_sum", "sum") and entity_groups and entity_hits == 0:
            continue
        broad_object = bool(object_terms.intersection({"item", "items", "thing", "things", "event", "events"}))
        if object_terms and object_hits == 0 and not (broad_object and action_hits > 0):
            continue
        if action_terms and action_hits == 0:
            continue
        status = "actual"
        pred = str(raw.get("predicate_canonical") or raw.get("predicate_raw") or "").lower()
        if pred.startswith("plan_") or "future" in pred:
            status = "plan"
        else:
            if _FUTURE_PLAN_RE.search(hay):
                # whole-turn hit -> re-check at sentence scope before excluding
                status = _plan_status_scoped(raw, object_terms | action_terms)
            if status == "actual" and _ADVICE_RE.search(hay) and (
                raw.get("role") or raw.get("source_role")
            ) == "assistant":
                status = "advice"
        if status in exclude_status:
            continue
        for gi, terms in enumerate(entity_groups):
            if terms and any(term in hay for term in terms):
                entity_row_hits[gi] += 1
        score = object_hits * 2 + action_hits
        if entity_hits:
            score += entity_hits * 2
        if status != "actual":
            score -= 2
        if score <= 0:
            continue
        rows.append({
            "evidence_id": rid,
            "time": raw.get("occurred_start") or raw.get("event_date") or raw.get("session_time") or "",
            "predicate": raw.get("predicate_canonical") or raw.get("predicate_raw") or "",
            "status": status,
            "score": score,
            "support": _one_line(_reader_source_text(raw), 180),
        })
    if not rows:
        return "", []
    # Keep high-scoring actual rows first, then chronological-ish for reader sanity.
    rows.sort(key=lambda row: (row["status"] == "actual", row["score"], str(row["time"])), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        support_key = re.sub(r"\W+", " ", str(row["support"]).lower())[:120]
        key = (
            str(row["predicate"]).lower(),
            str(row["time"])[:10],
            support_key,
        )
        loose_key = support_key[:90]
        if key in seen or loose_key in seen:
            continue
        seen.add(key)
        seen.add(loose_key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    lines = [
        "runtime_enumeration_sweep (semantic query-plan advisory; enumerate components "
        "before counting. This list may be INCOMPLETE — cross-check the full evidence "
        "before finalizing a count. Treat window-boundary dates as inside the window; "
        "a component with an unknown date is not automatically outside the window):",
        f"- object_type={_one_line(hint.get('object_type'), 100)} actions={', '.join(_as_list(hint.get('actions')))} time_window={_one_line(hint.get('time_window'), 100) or 'unspecified'}",
    ]
    entities_raw = _as_list(slot.get("entities"))
    if plan.get("answer_shape") in ("aggregate_sum", "sum") and entity_groups:
        missing = [
            entities_raw[gi]
            for gi, terms in enumerate(entity_groups)
            if terms and entity_row_hits[gi] == 0 and gi < len(entities_raw)
        ]
        if missing:
            # q090-class guard: a required component with zero evidence must
            # not be silently summed as 0 — that converts a correct
            # abstention into a fabricated total.
            lines.append(
                f"- missing_components={'; '.join(missing)} (0 evidence rows) — do NOT "
                "treat a missing component as 0; if it is required for the requested "
                "total, state that the information is missing."
            )
    for row in deduped:
        lines.append(
            f"- date={row['time'] or 'unknown'} status={row['status']} score={row['score']} "
            f"evidence={row['evidence_id']} predicate={_one_line(row['predicate'], 80)} "
            f"support={row['support']}"
        )
    return "\n".join(lines), [row["evidence_id"] for row in deduped]


__all__ = ["build_enumeration_sweep"]
