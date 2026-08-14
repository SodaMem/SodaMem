"""Entity / event dedup helpers for count_unit."""
from __future__ import annotations

import re
from typing import Any

from protocol_v1.schema import QuestionSchema

_BIKE_RE = re.compile(r"\b(road bike|mountain bike|bike)\b", re.I)
_MONEY_RE = re.compile(
    r"(?:\$|usd\s*)(\d+(?:,\d{3})*(?:\.\d+)?)|(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:dollars|usd)\b",
    re.I,
)


def entity_keys_for_item(item: dict[str, Any], schema: QuestionSchema) -> list[str]:
    text = str(item.get("content") or "")
    keys: list[str] = []
    if schema.count_unit == "entity":
        if "bike" in (schema.predicate + " " + (schema.notes and "" or "")).lower() or "bike" in text.lower():
            for m in _BIKE_RE.finditer(text):
                keys.append(m.group(1).lower())
            if not keys and re.search(r"\bbike\b", text, re.I):
                keys.append("bike")
        # generic: first quoted / Capitalized noun phrase — keep light
    if not keys:
        # fall back to evidence id (event-level)
        keys.append(str(item.get("fact_id") or item.get("evidence_id") or text[:60]))
    return keys


def dedupe_for_count(
    items: list[dict[str, Any]],
    schema: QuestionSchema,
) -> list[dict[str, Any]]:
    if schema.count_unit != "entity":
        return items
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        keys = entity_keys_for_item(it, schema)
        # if any key already seen, skip (same entity)
        if any(k in seen for k in keys):
            continue
        for k in keys:
            seen.add(k)
        out.append(it)
    return out


def extract_user_money_amounts(text: str) -> list[float]:
    vals: list[float] = []
    for m in _MONEY_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        try:
            vals.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return vals
