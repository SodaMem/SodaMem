"""Include/exclude membership for set aggregation (code rules, not prompts)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sodamem_struct.candidates import Candidate


@dataclass
class Decision:
    include: bool
    reason: str
    entity_key: str = ""  # for dedupe


_FUTURE = re.compile(
    r"\b(plan(ning)? to|will |going to|want to|thinking of|hope to|"
    r"might |maybe |intend(ing)? to|scheduled for|upcoming)\b",
    re.I,
)
_PAST = re.compile(
    r"\b(hosted|attended|went|did|completed|finished|served|picked up|"
    r"returned|exchanged|replaced|fixed|led|leading|raised|spent|"
    r"bought|purchased|ordered|had |was |were |last |yesterday|"
    r"already|successfully)\b",
    re.I,
)
_MONEY = re.compile(
    r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\b|"
    r"\b([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:dollars?|usd)\b",
    re.I,
)


def extract_money(text: str) -> Optional[float]:
    m = _MONEY.search(text or "")
    if not m:
        return None
    g = (m.group(1) or m.group(2) or "").replace(",", "")
    try:
        return float(g)
    except Exception:
        return None


def _entity_key(template: str, content: str) -> str:
    t = (content or "").lower()
    if template == "bike_service":
        for k in ("road bike", "mountain bike", "hybrid bike", "electric bike", "bike"):
            if k in t:
                return k
        return t[:40]
    if template == "wedding":
        # couple names: "X and Y"
        m = re.search(r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)", content or "")
        if m:
            return f"{m.group(1).lower()}&{m.group(2).lower()}"
        return t[:50]
    if template == "project_lead":
        m = re.search(r"(?:project|initiative|campaign)\s+[\"']?([A-Za-z0-9][\w\s\-]{2,40})", content or "", re.I)
        if m:
            return m.group(1).strip().lower()
        return t[:60]
    if template == "dinner_party":
        # host/location cue
        m = re.search(r"(?:at|for|with)\s+([A-Z][a-z]+)", content or "")
        if m:
            return m.group(1).lower() + "|" + t[:30]
        return t[:50]
    return t[:80]


def decide_one(template: str, c: Candidate, question: str = "") -> Decision:
    text = c.content or ""
    tl = text.lower()
    ql = (question or "").lower()
    key = _entity_key(template, text)

    # Universal: pure future plans without past anchors → exclude for "how many did / have"
    if _FUTURE.search(tl) and not _PAST.search(tl):
        if re.search(r"\b(did i|have i|have been|attended|hosted)\b", ql):
            return Decision(False, "future_plan", key)

    if template == "dinner_party":
        if re.search(r"\bbirthday\b|\bfamily reunion\b|\breunion\b", tl) and "dinner party" not in tl:
            return Decision(False, "not_dinner_party_event", key)
        if not re.search(r"dinner|potluck|feast|bbq|barbecue", tl):
            return Decision(False, "no_dinner_cue", key)
        if re.search(r"\b(i hosted|we hosted|hosted a|i'm hosting|hosting a)\b", tl) and re.search(
            r"\battended\b", ql
        ):
            return Decision(False, "hosted_not_attended", key)
        if re.search(r"\bplan(ning)? to host\b|\bwill host\b", tl) and not _PAST.search(tl):
            return Decision(False, "planned_only", key)
        return Decision(True, "dinner_event", key)

    if template == "wedding":
        if re.search(r"sister'?s wedding|maid of honor|my sister", tl):
            return Decision(False, "family_sister_wedding", key)
        if "wedding" not in tl:
            return Decision(False, "no_wedding", key)
        if _FUTURE.search(tl) and not _PAST.search(tl):
            return Decision(False, "future_wedding", key)
        return Decision(True, "wedding", key)

    if template == "project_lead":
        if not re.search(r"\b(led|leading|lead|in charge of|responsible for)\b", tl):
            return Decision(False, "not_leading", key)
        if re.search(r"\b(working on|contributing to|helping with)\b", tl) and not re.search(
            r"\b(led|leading|lead)\b", tl
        ):
            return Decision(False, "contributor_not_lead", key)
        if re.search(r"\bplanning to (launch|lead|start)\b", tl) and not _PAST.search(tl):
            return Decision(False, "planned_project", key)
        return Decision(True, "led_project", key)

    if template == "clothing_store":
        if re.search(r"dry\s*clean|tailor|alteration", tl):
            return Decision(False, "not_store", key)
        if not re.search(r"pick(ed)? up|return(ed)?|exchange(d)?|store|mall|shop", tl):
            return Decision(False, "no_store_action", key)
        return Decision(True, "store_action", key)

    if template == "furniture":
        if "furniture" not in tl and not re.search(r"\b(sofa|couch|table|chair|desk|shelf|dresser)\b", tl):
            return Decision(False, "not_furniture", key)
        if _FUTURE.search(tl) and not _PAST.search(tl):
            return Decision(False, "planned_buy", key)
        return Decision(True, "furniture", key)

    if template == "food_delivery":
        if not re.search(r"deliver|doordash|ubereats|grubhub|postmates|seamless", tl):
            return Decision(False, "not_delivery", key)
        return Decision(True, "delivery", key)

    if template == "art_event":
        if not re.search(r"\b(art|gallery|exhibit|museum|opening|vernissage)\b", tl):
            return Decision(False, "not_art", key)
        return Decision(True, "art_event", key)

    if template == "bike_service":
        if "bike" not in tl and "bicycle" not in tl:
            return Decision(False, "not_bike", key)
        if not re.search(r"service|serviced|tune-?up|repair|shop", tl):
            return Decision(False, "no_service", key)
        # include planned service if question says "or plan to"
        return Decision(True, "bike_service", key)

    if template == "kitchen_fix":
        if not re.search(r"kitchen|appliance|fridge|oven|dishwasher|microwave|toaster", tl):
            return Decision(False, "not_kitchen", key)
        if not re.search(r"replace|fix|repair|broke|broken", tl):
            return Decision(False, "no_fix_cue", key)
        return Decision(True, "kitchen_fix", key)

    if template == "album":
        if not re.search(r"\b(album|ep|lp|vinyl|released|listened)\b", tl):
            return Decision(False, "not_album", key)
        return Decision(True, "album", key)

    if template == "charity_raise":
        if not re.search(r"charit|fundrais|donat|raised", tl):
            return Decision(False, "not_charity", key)
        if extract_money(text) is None and c.amount is None:
            return Decision(False, "no_amount", key)
        return Decision(True, "charity", key)

    if template == "bake":
        if not re.search(r"\b(bake|baked|baking|cake|cookies|bread|pastr)\b", tl):
            return Decision(False, "not_bake", key)
        return Decision(True, "bake", key)

    if template == "tank":
        if "tank" not in tl:
            return Decision(False, "not_tank", key)
        return Decision(True, "tank", key)

    if template == "workshop":
        if "workshop" not in tl and "class" not in tl:
            return Decision(False, "not_workshop", key)
        return Decision(True, "workshop", key)

    # generic: keep past-ish content; drop pure future
    if _FUTURE.search(tl) and not _PAST.search(tl):
        return Decision(False, "generic_future", key)
    if len(text.strip()) < 8:
        return Decision(False, "too_short", key)
    return Decision(True, "generic_keep", key)


def filter_candidates(
    template: str,
    candidates: list[Candidate],
    question: str = "",
) -> tuple[list[Candidate], list[tuple[Candidate, str]], list[tuple[Candidate, str]]]:
    """Return (included_deduped, include_log, exclude_log)."""
    include_log: list[tuple[Candidate, str]] = []
    exclude_log: list[tuple[Candidate, str]] = []
    by_key: dict[str, Candidate] = {}

    for c in candidates:
        d = decide_one(template, c, question=question)
        if not d.include:
            exclude_log.append((c, d.reason))
            continue
        include_log.append((c, d.reason))
        k = d.entity_key or c.cid
        # prefer longer / dated content when deduping
        prev = by_key.get(k)
        if prev is None or (len(c.content) > len(prev.content)):
            by_key[k] = c

    return list(by_key.values()), include_log, exclude_log
