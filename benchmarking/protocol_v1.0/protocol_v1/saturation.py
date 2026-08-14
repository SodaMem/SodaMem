"""Enumeration saturation — schema-routed follow-up queries (TAS)."""
from __future__ import annotations

from typing import Optional

from protocol_v1.cardinality import extract_cardinality
from protocol_v1.schema import QuestionSchema
from protocol_v1.slots import slot_search_queries


def saturation_queries(
    question: str,
    schema: QuestionSchema,
    *,
    absolute_date: str | None = None,
    cardinality: Optional[int] = None,
) -> list[str]:
    q = question or ""
    out: list[str] = []

    out.extend(slot_search_queries(schema))

    if schema.needs_money_pass or schema.task == "SUM":
        out.append("raised dollars spent cost paid total amount")
        out.append("money total donate raise spent")

    if schema.task in {"COUNT_DISTINCT", "SUM", "ORDERED_LIST"}:
        if schema.predicate:
            out.append(schema.predicate)
            out.append(f"{schema.predicate} date list")

    n = cardinality if cardinality is not None else extract_cardinality(q)
    if n and schema.task in {"COUNT_DISTINCT", "ORDERED_LIST"}:
        out.append(f"all {n} {schema.predicate}".strip())
        out.append(f"visited {schema.predicate} date order earliest latest")

    if schema.axis == "got":
        out.append("arrived received got purchased picked up delivery date")
    if schema.axis == "ordered":
        out.append("booked reserved ordered months ago")

    if absolute_date and schema.task == "TEMPORAL_EVENT":
        out.append(f"{absolute_date} {schema.predicate}".strip())
        out.append(f"on {absolute_date}")

    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        k = s.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(s.strip())
    return uniq[:10]


def saturation_labels(question: str, schema: QuestionSchema) -> list[str]:
    labels: list[str] = []
    for tok in schema.predicate.split():
        if len(tok) > 2:
            labels.append(tok)
    bad = {"how", "many", "much", "did", "have", "the"}
    labels = [x for x in labels if x.lower() not in bad]
    if schema.needs_money_pass:
        labels.extend(["raise", "spent", "dollars", "total"])
    if schema.slot_name == "redeem_threshold":
        labels.extend(["redeem", "points", "threshold", "loyalty"])
    if schema.task == "ORDERED_LIST":
        labels.extend(["visited", "order", "earliest", "latest"])
    seen: set[str] = set()
    out: list[str] = []
    for x in labels:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out[:8] or ["items"]
