"""Typed Answer Schema (TAS) — heuristic question typing (no LLM).

Layer kept on purpose: map surface phrasing to a *task type* so the answer
path can apply general engineering constraints (enumerate-before-count,
temporal pin, versioned-slot conflict). This module intentionally does **not**
attach per-question entity include/exclude lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Task = Literal[
    "COUNT_DISTINCT",
    "SUM",
    "ORDERED_LIST",
    "SLOT_LOOKUP",
    "TEMPORAL_EVENT",
    "VERSIONED_ATTR",
    "ABSTAIN",
    "ORDINARY",
]

CountUnit = Literal["entity", "event", "unknown"]
Axis = Literal["got", "ordered", "said", "auto"]


@dataclass(frozen=True)
class QuestionSchema:
    task: Task
    predicate: str = ""
    count_unit: CountUnit = "unknown"
    time_window_raw: str = ""
    modifiers: tuple[str, ...] = ()
    slot_name: str = ""
    axis: Axis = "auto"
    needs_money_pass: bool = False
    needs_saturation: bool = False
    # Retained for API compatibility with admission/completeness; TAS does not
    # populate per-question entity filters.
    exclude_predicates: tuple[str, ...] = ()
    include_hints: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_set_task(self) -> bool:
        return self.task in {"COUNT_DISTINCT", "SUM"}

    @property
    def is_temporal(self) -> bool:
        return self.task in {"TEMPORAL_EVENT", "ORDERED_LIST"} or bool(self.time_window_raw)


_ORDER = re.compile(
    r"\b(order of|in what order|from earliest|earliest to latest|chronolog)\b", re.I
)
_SUM = re.compile(
    r"\b(how much|total (money|amount|cost)|in total|altogether|spent|raise[d]?|"
    r"page count|years in total|how much .+ save)\b",
    re.I,
)
_COUNT = re.compile(r"\bhow many\b", re.I)
_REL = re.compile(
    r"\b(\d+\s+(day|week|month|year)s?\s+ago|"
    r"(last|past)\s+(weekend|saturday|sunday|week|month|few\s+\w+)|"
    r"two weeks ago|four weeks ago|this year|past (three )?months?|"
    r"valentine'?s?\s+day)\b",
    re.I,
)
_SLOT = re.compile(
    r"\b(how many points|need to (earn|redeem)|redeem|threshold|"
    r"before getting|before the)\b",
    re.I,
)
_VERSION = re.compile(
    r"\b(new (internet )?plan|my new |upgraded?|switched to|"
    r"previous|former|personal best)\b",
    re.I,
)
_GOT_FIRST = re.compile(r"\b(got first|received first|which .+ first)\b", re.I)
_BOOK_AGO = re.compile(r"\b(how many months ago .+ book|book(?:ed)? .+ ago)\b", re.I)
_ENTITY_NOUN = re.compile(
    r"\b(bikes?|items?|albums?|eps?|projects?|weddings?|types?|services?|"
    r"events?|museums?|airlines?)\b",
    re.I,
)


def parse_question_schema(question: str) -> QuestionSchema:
    q = question or ""
    ql = q.lower()
    notes: list[str] = []
    modifiers: list[str] = []
    axis: Axis = "auto"
    slot = ""
    count_unit: CountUnit = "unknown"
    time_raw = ""
    m = _REL.search(q)
    if m:
        time_raw = m.group(0)

    if _ORDER.search(q):
        task: Task = "ORDERED_LIST"
    elif _BOOK_AGO.search(q) or re.search(r"\bhow many (months|weeks|days|years) ago\b", ql):
        task = "TEMPORAL_EVENT"
    elif (
        re.search(r"\b(need to (earn|redeem)|redeem a free|points? do i need)\b", ql)
        or (_SLOT.search(q) and "how many points" in ql)
    ):
        task = "SLOT_LOOKUP"
    elif _COUNT.search(q):
        task = "COUNT_DISTINCT"
    elif _SUM.search(q):
        task = "SUM"
    elif _VERSION.search(q) and ("plan" in ql or "speed" in ql or "best" in ql):
        task = "VERSIONED_ATTR"
    elif _SLOT.search(q) or "before getting" in ql:
        task = "SLOT_LOOKUP"
    elif _GOT_FIRST.search(q) or _REL.search(q) or "who did i" in ql:
        task = "TEMPORAL_EVENT"
    else:
        task = "ORDINARY"

    if task == "COUNT_DISTINCT":
        if _ENTITY_NOUN.search(ql):
            if re.search(r"\b(how many (?:times|sessions|workouts))\b", ql):
                count_unit = "event"
            else:
                count_unit = "entity"
        else:
            count_unit = "event"

    predicate = _guess_predicate(ql)

    if "new" in ql and ("plan" in ql or "speed" in ql or "internet" in ql):
        modifiers.append("new")
        slot = "new_plan_speed"
        if task == "ORDINARY":
            task = "VERSIONED_ATTR"
    if "current" in ql and ("plan" in ql or "speed" in ql or "internet" in ql):
        modifiers.append("current")
    if "redeem" in ql or (
        "points" in ql
        and re.search(r"\b(need|earn|redeem|threshold|free\s+\w+\s+product)\b", ql)
    ):
        slot = slot or "redeem_threshold"
        if task == "ORDINARY":
            task = "SLOT_LOOKUP"
    if "before getting" in ql or re.search(r"\bbefore the\b", ql):
        slot = slot or "prior_item"
        if task == "ORDINARY":
            task = "SLOT_LOOKUP"
    if ("promotion" in ql or "senior" in ql) and "year" in ql:
        slot = slot or "tenure_slot"

    if _GOT_FIRST.search(q):
        axis = "got"
        notes.append("prefer acquisition/arrival over preorder when the question asks got/received")
    if _BOOK_AGO.search(q):
        axis = "ordered"
        notes.append("months-ago means elapsed since booking, not lead time before stay")
        if task == "ORDINARY":
            task = "TEMPORAL_EVENT"

    needs_money = task == "SUM" or bool(
        re.search(r"\b(money|raise|spent|cost|save|\$|dollars|points)\b", ql)
    )
    needs_sat = task in {"COUNT_DISTINCT", "SUM", "ORDERED_LIST"}

    return QuestionSchema(
        task=task,
        predicate=predicate,
        count_unit=count_unit,
        time_window_raw=time_raw,
        modifiers=tuple(modifiers),
        slot_name=slot,
        axis=axis,
        needs_money_pass=needs_money,
        needs_saturation=needs_sat,
        exclude_predicates=(),
        include_hints=(),
        notes=tuple(notes),
    )


def _guess_predicate(ql: str) -> str:
    """Lightweight content tokens from the question — not a closed entity list."""
    words = re.findall(r"[a-z][a-z0-9+\-]{2,}", ql)
    stop = {
        "how", "many", "much", "the", "did", "have", "had", "was", "were",
        "what", "which", "where", "who", "when", "from", "with", "that",
        "this", "past", "last", "total", "different", "types", "order",
        "earliest", "latest", "remind", "about",
    }
    keep = [w for w in words if w not in stop]
    return " ".join(keep[:6])
