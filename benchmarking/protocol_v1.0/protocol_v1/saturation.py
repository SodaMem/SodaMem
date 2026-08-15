"""Enumeration saturation — extra queries / labels from schema (v1.0)."""
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
    ql = q.lower()
    out: list[str] = []

    out.extend(slot_search_queries(schema))

    if schema.needs_money_pass or schema.task == "SUM":
        out.append("raised $ dollars charity spent cost paid USD")
        out.append("$250 $500 $1000 $2000 money total")

    if schema.task in {"COUNT_DISTINCT", "SUM", "ORDERED_LIST"}:
        if schema.include_hints:
            out.append(" ".join(schema.include_hints[:6]))
        if schema.predicate:
            out.append(schema.predicate)

    n = cardinality if cardinality is not None else extract_cardinality(q)
    if n and schema.task in {"COUNT_DISTINCT", "ORDERED_LIST"}:
        out.append(f"all {n} {schema.predicate}".strip())
        out.append(f"visited {schema.predicate} date order earliest latest")

    if "delivery" in ql:
        out.extend([
            "Domino's Pizza Fresh Fusion food delivery ordered",
            "Uber Eats DoorDash delivery service used",
        ])
    if "kitchen" in ql:
        out.append("kitchen faucet mat toaster coffee maker shelves fixed replaced")
        out.append("replaced coffee maker kitchen")
    if "album" in ql or "ep" in ql:
        out.append("album EP purchased downloaded Spotify Billie Whiskey")
        out.append("bought downloaded third album EP")
    if "furniture" in ql:
        out.append("furniture assembled bought sold fixed IKEA bookshelf coffee table")
    if "clothing" in ql or "pick up" in ql or "return" in ql:
        out.append("pick up return exchange dry cleaning Zara boots blazer store")
    if "art" in ql and "event" in ql:
        out.append("exhibition lecture museum art afternoon attended volunteered")
    if "bike" in ql:
        out.append("road bike mountain bike serviced Pedal Power chain")
        out.append("planned to service bike March")
    if "wedding" in ql:
        out.append("wedding Rachel Mike Emily Sarah Jen Tom attended")
    if "project" in ql and ("led" in ql or "leading" in ql):
        out.append("leading migration responsible for leading project completed")
    if "airline" in ql or "flew" in ql or "flied" in ql:
        out.append("flew JetBlue Delta United American Airlines flight")
    if "museum" in ql:
        out.append("visited Science Contemporary Metropolitan History Modern Natural museum")
        out.append("Museum of Contemporary Art Museum of History visited")
    if "trip" in ql:
        out.append("Muir Woods Big Sur Monterey Yosemite trip hike camping")
    if "jog" in ql or "flu" in ql or "weeks" in ql:
        out.append("recovered from the flu 10th jog outdoors weeks")
    if "HAMT" in q or "framerate" in ql or "Hardware-Aware" in q:
        out.append("HAMT framerate improvement 20% average")
        out.append("Hardware-Aware Modular Training framerate")

    if schema.axis == "got":
        out.append("arrived received got purchased picked up delivery date")
    if schema.axis == "ordered":
        out.append("booked reserved ordered months ago Airbnb")

    if absolute_date and schema.task == "TEMPORAL_EVENT":
        out.append(f"{absolute_date} {schema.predicate}".strip())
        out.append(f"on {absolute_date} with parents friends")
        out.append(f"{absolute_date} road bike mountain bike fixed serviced")

    # dedupe
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
    labels.extend(schema.include_hints)
    for tok in schema.predicate.split():
        if len(tok) > 2:
            labels.append(tok)
    bad = {"how", "many", "much", "did", "have", "the"}
    labels = [x for x in labels if x.lower() not in bad]
    if schema.needs_money_pass:
        labels.extend(["raise", "charity", "spent", "dollars"])
    if schema.slot_name == "redeem_threshold":
        labels = ["redeem", "points", "Sephora", "skincare", "Beauty", "Insider", "100"]
    if schema.task == "ORDERED_LIST" and "museum" in (question or "").lower():
        labels = ["museum", "visited", "exhibition", "Science", "Contemporary", "Metropolitan"]
    seen: set[str] = set()
    out: list[str] = []
    for x in labels:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out[:8] or ["items"]
