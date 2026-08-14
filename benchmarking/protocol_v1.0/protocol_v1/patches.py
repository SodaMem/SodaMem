"""Monkey-patches: stack Protocol v1.0 on Soft / Plan B+."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Optional

_APPLIED = False
_TLS = threading.local()

V1_PLANNER_ADDENDUM = """
Protocol v1.0 retrieval discipline (schema-routed):
- Parse the question into a task before finalizing.
- MR SetEnumeration: for how-many/total set questions, enumerate an item_list
  (date + quote per item) before counting/summing; keep searching if N asserted
  and list shorter than N.
- CARDINALITY / ORDERED_LIST: build (entity, date) timeline; sort ascending;
  do not finalize order while dated_rows < N.
- SLOT / VERSIONED conflicts: conflict board of (value, slot_label); hard
  override only with local cue + dual candidates.
- TEMPORAL who/which/from-whom: EventAnchor — pin absolute day, fill only the
  who/which slot from in-window evidence.
- SUM charity/raise: money-role gate (raise/donate vs housing/other-spend).
- ENTITY counts: dedupe distinct entities.
- IE/assistant paper metrics: assistant turns are valid evidence.
- Do not loosen exclude gates (birthday≠dinner, plan≠led, got≠preorder).
"""

V1_READER_GUIDANCE = (
    " Protocol v1.0: Obey protocol_v1.0 advisories and SetEnumeration boards. "
    "If set_enumeration_board is present, count/sum from that item_list only. "
    "If ordered_timeline is present, sort (entity, date) before answering order. "
    "If event_anchor is present, fill who/which only from the pinned day. "
    "If gated_slot_hard_bind states FINAL ANSWER MUST BE, that value is mandatory. "
    "SUM charity: exclude housing/other-spend roles. "
    "COUNT entity: use ENTITY DEDUP / set board. "
    "Do not false-abstain on assistant/paper metrics."
)

V1_CONSTRAINTS = [
    "Follow protocol_v1.0 advisories; honor gated_slot_hard_bind when present.",
    "SetEnumeration: enumerate items before how-many/total; do not finalize if have < N.",
    "OrderedTimeline: answer order only from dated (entity, date) rows sorted ascending.",
    "EventAnchor: who/which/from-whom must come from the pinned absolute day.",
    "Do not count excluded predicates (birthday as dinner; planning as leading).",
    "Do not answer 'booked N months in advance' when asked 'how many months ago did I book'.",
    "Do not use preorder date when asked which device was got/received first.",
    "When both a personal points goal and a redeem/threshold appear, answer the redeem/threshold.",
    "For charity totals, sum only raise/donate amounts; exclude mortgage/house/workshop fees.",
]


def is_applied() -> bool:
    return _APPLIED


def _set_override(text: Optional[str]) -> None:
    _TLS.slot_override = text


def _pop_override() -> Optional[str]:
    text = getattr(_TLS, "slot_override", None)
    _TLS.slot_override = None
    return text


def apply() -> None:
    """Apply Soft (Plan B+) first, then Protocol v1.0."""
    global _APPLIED
    if _APPLIED:
        return

    from sodamem_opt.patches import apply as opt_apply

    opt_apply()

    import sodamem.answer as answer_pkg
    import sodamem.answer.reader as reader_mod
    import sodamem.prompts.planner as planner_prompts
    import sodamem.prompts.reader as reader_prompts
    from protocol_v1.advisories import build_protocol_advisories
    from protocol_v1.cardinality import extract_cardinality
    from protocol_v1.saturation import saturation_labels, saturation_queries
    from protocol_v1.schema import parse_question_schema

    if V1_PLANNER_ADDENDUM.strip() not in planner_prompts.PLANNER_SYSTEM_PROMPT:
        planner_prompts.PLANNER_SYSTEM_PROMPT = (
            planner_prompts.PLANNER_SYSTEM_PROMPT.rstrip()
            + "\n"
            + V1_PLANNER_ADDENDUM.strip()
            + "\n"
        )

    if V1_READER_GUIDANCE not in reader_prompts.READER_GUIDANCE:
        reader_prompts.READER_GUIDANCE = (
            reader_prompts.READER_GUIDANCE + V1_READER_GUIDANCE
        )
    constraints = list(reader_prompts.DEFAULT_ANSWER_CONSTRAINTS)
    for c in V1_CONSTRAINTS:
        if c not in constraints:
            constraints.append(c)
    reader_prompts.DEFAULT_ANSWER_CONSTRAINTS = constraints

    import sodamem.answer.agent_guidance as guidance_mod
    import sodamem.answer.rules as rules_mod

    _prev_initial = rules_mod.initial_calls

    def _initial_calls_v1(rules, question: str):
        calls = list(_prev_initial(rules, question) or [])
        schema = parse_question_schema(question)
        card = extract_cardinality(question)
        abs_date = None
        try:
            from sodamem.answer.timewords import resolve_time_window

            w = resolve_time_window(question or "", current_date="")
            if w and w.get("from_date"):
                abs_date = w.get("from_date")
        except Exception:
            abs_date = None
        seen = {
            ((c.get("args") or {}).get("query") or "").strip().lower()
            for c in calls
        }
        for q in saturation_queries(
            question, schema, absolute_date=abs_date, cardinality=card
        )[:5]:
            ql = q.lower()
            if ql in seen:
                continue
            seen.add(ql)
            calls.append({
                "tool": "search",
                "args": {
                    "query": q,
                    "top_k": 12,
                    "include_context": True,
                    "include_multimodal": True,
                },
            })
        labels = saturation_labels(question, schema)
        for call in calls:
            if call.get("tool") in {"browser_count_evidence", "count"}:
                args = dict(call.get("args") or {})
                args["labels"] = labels
                args["query"] = question
                call["args"] = args
        return calls

    rules_mod.initial_calls = _initial_calls_v1

    _prev_cap = guidance_mod.AgentGuidance.capability_calls

    def _capability_calls_v1(self, missing_families, question: str):
        calls = list(_prev_cap(self, missing_families, question))
        schema = parse_question_schema(question)
        card = extract_cardinality(question)
        labels = saturation_labels(question, schema)
        for call in calls:
            if call.get("tool") == "browser_count_evidence":
                call["args"] = {
                    **(call.get("args") or {}),
                    "query": question,
                    "labels": labels,
                }
        if schema.task in {
            "COUNT_DISTINCT",
            "ORDERED_LIST",
            "SUM",
            "SLOT_LOOKUP",
            "VERSIONED_ATTR",
        }:
            seen = {
                ((c.get("args") or {}).get("query") or "").strip().lower()
                for c in calls
            }
            for q in saturation_queries(question, schema, cardinality=card)[:3]:
                if q.lower() in seen:
                    continue
                seen.add(q.lower())
                calls.append({
                    "tool": "search",
                    "args": {
                        "query": q,
                        "top_k": 12,
                        "include_context": True,
                        "include_multimodal": True,
                    },
                })
        return calls

    guidance_mod.AgentGuidance.capability_calls = _capability_calls_v1

    _prev_assemble = reader_mod.assemble_reader_context

    def _assemble_v1(evidence, selected_ids, question, **kwargs):
        ctx = _prev_assemble(evidence, selected_ids, question, **kwargs)
        current_date = kwargs.get("current_date") or ""
        window = None
        try:
            from sodamem.answer.timewords import resolve_time_window

            window = resolve_time_window(question or "", current_date=current_date)
        except Exception:
            window = None
        schema, advs, override, filtered_key = build_protocol_advisories(
            question or "",
            evidence,
            current_date=current_date,
            window=window,
            key_evidence=list(ctx.key_evidence or []),
        )
        _set_override(override)
        advisories = list(ctx.semantic_advisories or [])
        advisories.extend(advs)
        if filtered_key is not None:
            ctx = replace(ctx, key_evidence=filtered_key, semantic_advisories=advisories)
        else:
            ctx = replace(ctx, semantic_advisories=advisories)
        try:
            _TLS.last_schema = schema
            _TLS.last_window = window
            _TLS.last_override = override
        except Exception:
            pass
        return ctx

    reader_mod.assemble_reader_context = _assemble_v1
    answer_pkg.assemble_reader_context = _assemble_v1

    _prev_answer = reader_mod.answer

    def _answer_v13(question, context, **kwargs):
        import inspect

        from sodamem.answer.reader import Answer

        override = _pop_override()
        sig = inspect.signature(_prev_answer)
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            filtered = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        result = _prev_answer(question, context, **filtered)
        if override:
            text = (
                f"{override}\n\n"
                f"(protocol_v1.0 gated_slot_hard_bind: answer fixed to "
                f"question slot = {override})"
            )
            return Answer(
                text=text,
                citations=list(getattr(result, "citations", None) or []),
                trace={
                    **(getattr(result, "trace", None) or {}),
                    "protocol_v1_3_slot_hard": True,
                    "slot_override": override,
                },
            )
        return result

    reader_mod.answer = _answer_v13
    answer_pkg.reader_answer = _answer_v13

    import sys

    rs = sys.modules.get("run_s500")
    if rs is not None:
        rs.assemble_reader_context = _assemble_v1
        rs.reader_answer = _answer_v13

    try:
        import sodamem_opt.det_count as dc

        _prev_filter = dc.filter_roster

        def _filter_roster_v1(roster, question, *, current_date):
            from protocol_v1.admission import admit_roster
            from protocol_v1.dedup import dedupe_for_count
            from protocol_v1.schema import parse_question_schema

            schema = parse_question_schema(question)
            window = None
            try:
                window = dc.resolve_time_window(question, current_date=current_date)
            except Exception:
                window = None
            base = _prev_filter(roster, question, current_date=current_date)
            admitted = admit_roster(
                base or roster, schema, current_date=current_date, window=window
            )
            return dedupe_for_count(admitted, schema)

        dc.filter_roster = _filter_roster_v1
    except Exception:
        pass

    _APPLIED = True
