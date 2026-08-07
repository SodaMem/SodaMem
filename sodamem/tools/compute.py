"""Deterministic computation over retrieved evidence cards.

Lives as a `sodamem/tools/` submodule rather than a top-level package
because the tool surface is its only consumer: `date_calc()` and the tool
dispatcher import `ComputeError`/`compute`/`derived_evidence_id`/
`list_operators` from here, and nothing else does.

This module is intentionally post-retrieval only: it receives resolved
evidence cards and never reads a query or performs retrieval. Zero LLM, zero
network egress — pure stdlib (hashlib/json/re/datetime).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


class ComputeError(Exception):
    """Typed operator failure surfaced to the agent loop."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_OPERATORS = [
    {
        "name": "duration.subtract",
        "description": "Subtract a second explicit duration from a first explicit duration.",
        "accepted_input_kinds": ["ev_fact"],
        "arity": 2,
        "input_roles": ["duration_to_reduce", "duration_to_subtract"],
        "usage": "Pass [total or later duration, elapsed or earlier duration to subtract].",
        "required_fields": ["duration expression in source-backed text"],
        "output_unit": "month",
        "ordered_inputs": True,
        "error_modes": [
            "empty_input",
            "incompatible_input_kind",
            "missing_required_field",
            "unit_mismatch",
        ],
    },
]

_DURATION_WITH_YEARS = re.compile(
    r"\b(?P<years>\d+)\s*(?:years?|yrs?|y)"
    r"(?:\s*(?:and|,)?\s*(?P<months>\d+)\s*(?:months?|mos?|m))?\b",
    re.IGNORECASE,
)
_DURATION_MONTHS_ONLY = re.compile(r"\b(?P<months>\d+)\s*(?:months?|mos?)\b", re.IGNORECASE)


def list_operators() -> dict:
    return {"operators": [dict(operator) for operator in _OPERATORS]}


def derived_evidence_id(operator: str, inputs: list[str], params: dict[str, Any] | None = None) -> str:
    """Content-addressed id for a derived_runtime card (D49.4).

    Single source of truth for the `ev_derived:<op>:<hash>` format so every
    producer (compute operators, stateless date arithmetic) emits identical
    ids for identical (operator, inputs, params) — the recompute key.
    """
    identity = json.dumps(
        {"operator": operator, "inputs": list(inputs), "params": dict(params or {})},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"ev_derived:{operator}:{digest}"


def compute(
    operator: str,
    input_ids: list[str],
    input_cards: list[dict],
    params: dict[str, Any] | None = None,
) -> dict:
    """Run one registered operator and return a derived_runtime card."""
    params = dict(params or {})
    if operator != "duration.subtract":
        raise ComputeError("unknown_operator", f"Unknown compute operator: {operator}")
    if not input_ids:
        raise ComputeError("empty_input", "duration.subtract requires two evidence inputs")
    if len(input_ids) != 2 or len(input_cards) != 2:
        raise ComputeError("incompatible_input_kind", "duration.subtract requires exactly two evidence inputs")
    if any(not evidence_id.startswith("ev_fact:") for evidence_id in input_ids):
        raise ComputeError("incompatible_input_kind", "duration.subtract accepts ev_fact evidence only")

    left_months = _duration_months(input_cards[0])
    right_months = _duration_months(input_cards[1])
    result_months = left_months - right_months
    left_display = _format_months(left_months)
    right_display = _format_months(right_months)
    result_display = _format_months(result_months)
    calculation_trace = f"{left_display} - {right_display} = {result_display}"

    content = f"Verified calculation: {calculation_trace}."
    return {
        "evidence_id": derived_evidence_id(operator, input_ids, params),
        "kind": "derived",
        "source_type": "derived_runtime",
        "operator": operator,
        "inputs": list(input_ids),
        "params": params,
        "derived_from_fact_ids": [evidence_id.removeprefix("ev_fact:") for evidence_id in input_ids],
        "input_values": [
            {"value": left_months, "unit": "month", "display_value": left_display},
            {"value": right_months, "unit": "month", "display_value": right_display},
        ],
        "value": result_months,
        "unit": "month",
        "display_value": result_display,
        "calculation_trace": calculation_trace,
        "content": content,
        "content_preview": content,
        "content_preview_source": "DerivedRuntime",
        "content_preview_truncated": False,
        "content_preview_is_source_excerpt": False,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "recompute_policy": "idempotent_from_inputs",
        "provenance_status": "verified",
        "confidence": 1.0,
    }


def _duration_months(card: dict) -> int:
    texts: list[str] = []
    for field in ("extracted_support_text", "content", "predicate_raw", "content_preview"):
        value = card.get(field)
        if isinstance(value, str) and value and value not in texts:
            texts.append(value)
    for text in texts:
        match = _DURATION_WITH_YEARS.search(text)
        if match:
            years = int(match.group("years"))
            months = int(match.group("months") or 0)
            if months >= 12:
                raise ComputeError("unit_mismatch", f"Invalid month component in duration: {match.group(0)}")
            return years * 12 + months
    for text in texts:
        match = _DURATION_MONTHS_ONLY.search(text)
        if match:
            return int(match.group("months"))

    quantity = card.get("quantity") or {}
    if isinstance(quantity, dict) and quantity.get("value") is not None:
        unit = str(quantity.get("unit") or "").lower()
        value = quantity.get("value")
        if unit in {"month", "months"} and float(value).is_integer():
            return int(float(value))
        raise ComputeError(
            "unit_mismatch",
            "Duration quantity is not represented as exact months and cannot be subtracted without rounding",
        )
    raise ComputeError("missing_required_field", "No explicit duration expression found in input evidence")


def _format_months(months: int) -> str:
    sign = "-" if months < 0 else ""
    value = abs(months)
    years, remaining_months = divmod(value, 12)
    parts: list[str] = []
    if years:
        parts.append(f"{years} year" + ("" if years == 1 else "s"))
    if remaining_months or not parts:
        parts.append(f"{remaining_months} month" + ("" if remaining_months == 1 else "s"))
    return sign + " and ".join(parts)
