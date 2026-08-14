"""Question routing for structural answer branches (no LLM, no prompts)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Route:
    kind: str  # set_count | set_sum | slot_new_speed | slot_redeem_points | slot_role_duration | default
    template: str = ""  # membership template name for set_* 


_SET_COUNT = re.compile(r"\bhow many\b", re.I)
_SET_SUM = re.compile(
    r"\b(how much money|how much total|total money|total (?:amount|cost)|"
    r"raised .+ total|spent on|how much .+ (?:spent|raise|save))\b",
    re.I,
)
_NOT_SET = re.compile(
    r"\b(need to earn|points? do i need|do i need|followers|"
    r"internet plan|personal best|wake up|how much is the|"
    r"worth in terms|which university|poster)\b",
    re.I,
)
_NEW_SPEED = re.compile(
    r"\b(new internet|new .+ plan|internet plan|what speed is my new)\b",
    re.I,
)
_REDEEM_POINTS = re.compile(
    r"\b(points?.{0,40}redeem|redeem.{0,40}points|how many points do i need)\b",
    re.I,
)
_ROLE_DURATION = re.compile(
    r"\b(how long have i been (working )?in my current role|"
    r"current role|senior marketing)\b",
    re.I,
)


def detect_template(question: str) -> str:
    ql = (question or "").lower()
    pairs = [
        ("dinner_party", r"dinner part"),
        ("wedding", r"wedding"),
        ("project_lead", r"projects? .*(led|leading)|led or am currently leading"),
        ("clothing_store", r"clothing|pick up or return|return from a store"),
        ("furniture", r"furniture"),
        ("food_delivery", r"food delivery|delivery services"),
        ("art_event", r"art-related event|art event"),
        ("bike_service", r"bikes? .*(service|serviced)|service or plan to service"),
        ("kitchen_fix", r"kitchen items? .*(replace|fix)|replace or fix"),
        ("album", r"albums? or eps?|music albums?"),
        ("charity_raise", r"raise(d)? for charity|charity in total"),
        ("bake", r"\bbake"),
        ("tank", r"\btanks?\b"),
        ("workshop", r"workshop"),
        ("generic_count", r"how many"),
        ("generic_sum", r"how much|total money|spent"),
    ]
    for name, pat in pairs:
        if re.search(pat, ql):
            return name
    return "generic_count"


def route_question(question: str) -> Route:
    q = question or ""
    if _NEW_SPEED.search(q) and re.search(r"\b(speed|mbps|gbps|plan)\b", q, re.I):
        return Route("slot_new_speed")
    if _REDEEM_POINTS.search(q):
        return Route("slot_redeem_points")
    if _ROLE_DURATION.search(q) and re.search(r"\bhow long\b", q, re.I):
        return Route("slot_role_duration")
    if _NOT_SET.search(q):
        return Route("default")
    if _SET_SUM.search(q):
        return Route("set_sum", detect_template(q))
    if _SET_COUNT.search(q):
        return Route("set_count", detect_template(q))
    return Route("default")


def needs_count_tool(question: str) -> bool:
    r = route_question(question)
    return r.kind in {"set_count", "set_sum"}


def aggregation_labels(question: str) -> list[str]:
    stop = {
        "how", "many", "much", "the", "a", "an", "i", "my", "me", "do", "did",
        "have", "has", "had", "been", "was", "were", "is", "are", "to", "of",
        "in", "on", "at", "for", "from", "with", "and", "or", "total", "money",
        "past", "last", "few", "this", "year", "month", "months", "weeks",
        "days", "currently", "including", "different", "types", "type",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}", question or "")
    labels = [w for w in words if w.lower() not in stop][:8]
    return labels or ["items"]
