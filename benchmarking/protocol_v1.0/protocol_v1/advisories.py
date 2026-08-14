"""MR/TR-aware protocol advisories for v1.3 (SetEnumeration + EventAnchor + OrderedTimeline)."""
from __future__ import annotations

from typing import Any, Optional

from protocol_v1.abstain import abstain_advisory
from protocol_v1.admission import admit_roster
from protocol_v1.cardinality import extract_cardinality
from protocol_v1.completeness import completeness_advisory
from protocol_v1.conflict_board import build_conflict_board
from protocol_v1.dedup import dedupe_for_count, extract_user_money_amounts
from protocol_v1.entities import entity_summary
from protocol_v1.event_anchor import build_event_anchor_advisory
from protocol_v1.ordered_timeline import build_ordered_timeline_advisory
from protocol_v1.schema import QuestionSchema, parse_question_schema
from protocol_v1.set_enumeration import build_set_enumeration_board
from protocol_v1.slots import slot_advisory
from protocol_v1.sum_gate import sum_gate_advisory
from protocol_v1.temporal import temporal_advisory
from protocol_v1.temporal_pin import needs_day_pin, pin_advisory, pin_rows


def collect_roster(evidence: Any) -> list[dict[str, Any]]:
    observations = getattr(evidence, "observations", None) or []
    for obs in reversed(list(observations)):
        tool = str(obs.get("tool") or "")
        if tool in {"browser_count_evidence", "count"} and obs.get("roster"):
            return list(obs["roster"])
    return []


def build_protocol_advisories(
    question: str,
    evidence: Any,
    *,
    current_date: str = "",
    window: Optional[dict[str, str]] = None,
    key_evidence: Optional[list[dict[str, Any]]] = None,
) -> tuple[QuestionSchema, list[str], Optional[str], Optional[list[dict[str, Any]]]]:
    """Return (schema, advisories, override_answer, maybe_reordered_key_evidence)."""
    schema = parse_question_schema(question)
    card = extract_cardinality(question)
    advisories: list[str] = [
        "protocol_v1.3 question_schema: "
        f"task={schema.task} predicate={schema.predicate!r} "
        f"count_unit={schema.count_unit} axis={schema.axis} "
        f"slot={schema.slot_name!r} modifiers={list(schema.modifiers)} "
        f"exclude={list(schema.exclude_predicates)} "
        f"cardinality={card}"
    ]

    t = temporal_advisory(schema, current_date=current_date, absolute_window=window)
    if t:
        advisories.append(t)
    s = slot_advisory(schema)
    if s:
        advisories.append(s)

    ql = (question or "").lower()
    ie_soften = any(
        k in ql
        for k in (
            "framerate",
            "hamt",
            "hardware-aware",
            "paper",
            "recommended",
            "average improvement",
        )
    )
    if schema.task == "ABSTAIN" or "poster" in ql:
        advisories.append(abstain_advisory(schema, soften_ie=False))
    elif ie_soften:
        advisories.append(abstain_advisory(schema, soften_ie=True))
    elif "university" in ql:
        advisories.append(abstain_advisory(schema, soften_ie=False))

    roster = collect_roster(evidence)
    admitted = admit_roster(roster, schema, current_date=current_date, window=window)
    admitted = dedupe_for_count(admitted, schema)

    # TemporalEvidenceRank
    filtered_key = None
    if key_evidence is not None and (
        needs_day_pin(question, schema.task) or schema.task == "TEMPORAL_EVENT"
    ) and window:
        if needs_day_pin(question, schema.task) or any(
            k in ql
            for k in ("two weeks ago", "last saturday", "past weekend", "from whom")
        ):
            inn, out = pin_rows(list(key_evidence), window, keep_undated=True)
            out_snips = []
            for r in out[:4]:
                snip = " ".join(str(r.get("content") or r.get("text") or "").split())[:80]
                out_snips.append(f"[{r.get('event_date') or '?'}] {snip}")
            advisories.append(
                "protocol_v1.3 temporal_evidence_rank: "
                + pin_advisory(window, in_n=len(inn), out_n=len(out), out_snips=out_snips)
                + " Prefer in-window dated rows; undated remain secondary; "
                "out-of-window are distractors."
            )
            if inn:
                filtered_key = list(inn) + list(out)

    # TR EventAnchor
    ea = build_event_anchor_advisory(
        question,
        schema.task,
        window=window,
        key_evidence=filtered_key if filtered_key is not None else key_evidence,
    )
    if ea:
        advisories.append(ea)

    # Conflict board + stricter gated hard bind
    override: Optional[str] = None
    board, preferred, allow_hard = build_conflict_board(
        schema, evidence, admitted=admitted, question=question
    )
    if board:
        advisories.append(board)
    if (
        allow_hard
        and preferred
        and schema.task in {"SLOT_LOOKUP", "VERSIONED_ATTR"}
    ):
        advisories.append(
            "protocol_v1.3 gated_slot_hard_bind: FINAL ANSWER MUST BE "
            f"{preferred} (high-confidence dual-candidate conflict)."
        )
        override = preferred
    elif preferred:
        advisories.append(
            f"protocol_v1.3 slot_soft_prefer: prefer {preferred} but do not invent "
            "values absent from the conflict_board."
        )

    sg = sum_gate_advisory(schema, admitted, question=question)
    if sg:
        advisories.append(sg.replace("protocol_v1.2", "protocol_v1.3"))

    entity_names: list[str] | None = None
    if schema.count_unit == "entity" or (
        schema.task == "COUNT_DISTINCT"
        and any(
            k in ql
            for k in (
                "bike",
                "kitchen",
                "album",
                "furniture",
                "instrument",
                "tank",
                "jewelry",
                "project",
            )
        )
    ):
        entity_names, _n_ent = entity_summary(admitted, schema)
        if not entity_names:
            texts = []
            for obs in getattr(evidence, "observations", None) or []:
                for hit in obs.get("hits") or obs.get("results") or []:
                    if isinstance(hit, dict):
                        texts.append(str(hit.get("content") or hit.get("text") or ""))
                if obs.get("roster"):
                    for it in obs["roster"]:
                        texts.append(str(it.get("content") or ""))
            from protocol_v1.entities import extract_entity_mentions

            seen: set[str] = set()
            names: list[str] = []
            for tx in texts:
                for n in extract_entity_mentions(tx, schema):
                    if n not in seen:
                        seen.add(n)
                        names.append(n)
            entity_names = names

    # MR SetEnumeration board
    seb = build_set_enumeration_board(
        schema, admitted, question=question, entity_names=entity_names
    )
    if seb:
        advisories.append(seb)

    # TR OrderedTimeline
    ot = build_ordered_timeline_advisory(
        schema,
        admitted,
        question=question,
        key_evidence=filtered_key if filtered_key is not None else key_evidence,
    )
    if ot:
        advisories.append(ot)

    det_hint: float | int | None = None
    if schema.count_unit == "entity" and entity_names:
        det_hint = len(entity_names)
    elif schema.task == "COUNT_DISTINCT" and admitted:
        det_hint = len(admitted)
    elif schema.task == "SUM" and admitted:
        amounts: list[float] = []
        for it in admitted:
            monies = extract_user_money_amounts(str(it.get("content") or ""))
            if len(monies) == 1:
                amounts.append(monies[0])
            elif len(monies) > 1:
                amounts.append(max(monies))
        if amounts:
            det_hint = sum(amounts)

    if card and schema.task in {"COUNT_DISTINCT", "ORDERED_LIST"}:
        from protocol_v1.set_enumeration import count_kept_admitted

        # v1.5: keep-count only (no HARD_STOP). Plan-only must not inflate `have`.
        if schema.count_unit == "entity" and entity_names:
            have = len(entity_names)
        else:
            have = count_kept_admitted(admitted)
        if have < int(card):
            advisories.append(
                f"protocol_v1.5 cardinality_finalize_gate: question asserts N={card} "
                f"but kept/entity set size={have} (plan-only excluded from keep-count). "
                "Do NOT finalize a complete answer; continue search or state the set "
                "is incomplete."
            )

    advisories.append(
        completeness_advisory(
            schema,
            admitted=admitted,
            raw_roster_n=len(roster),
            det_value=det_hint,
            cardinality=card,
            entity_names=entity_names,
        )
    )

    if admitted:
        lines = []
        for i, it in enumerate(admitted[:25], 1):
            d = it.get("event_date") or "?"
            snip = " ".join(str(it.get("content") or "").split())[:140]
            lines.append(f"{i}. [{d}] {snip}")
        advisories.append(
            "protocol_v1.3 admitted_set (prefer these after gate+dedupe):\n"
            + "\n".join(lines)
        )

    return schema, advisories, override, filtered_key
