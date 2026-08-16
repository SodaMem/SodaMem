"""Fact/Event extraction: turn raw text into typed claims (D29/D31/D32).

Ported from the predecessor implementation (1224L) + the extraction-only
half of the predecessor implementation. Prompts are
imported from `sodamem.prompts.extraction` (byte-identical text, ported by
Task 2) rather than defined here a second time.

Five R2.9 "loser" feature flags were deleted entirely along with their code
paths, per the delete list and the user's
"production 没开的就不需要" ruling: `INGEST_D2_PRECISION`, `EXTRACT_SALIENCE_GATE`/`INGEST_SALIENCE_GATE`,
`EXTRACT_ENRICH`/`INGEST_EXTRACT_ENRICH`, `EXTRACT_CONSOLIDATE`/
`INGEST_CONSOLIDATE` (their prompts, methods, and counters are gone — not
just the boolean switch), and `GRAPH_V2_LOOSE_GROUNDING` (strict 0.8-overlap
grounding is now the only path). `EXTRACT_COARSE`/`INGEST_EXTRACT_COARSE` is also gone,
switch and prompt text both — the coarse route lost its A/B and nothing ever
enabled it.

Two flags folded to unconditional True (winner-goes-flagless, established
baseline — see `IngestConfig`'s docstring): `INGEST_CORRECTNESS` and
`INGEST_DETERMINISM`. Every `if INGEST_CORRECTNESS:`/`if INGEST_DETERMINISM:`
branch in the source is now just the code that used to run when the flag was
on; the code that used to run when it was off has been deleted, not kept
as a dead `else`.

`.available` guard rewrite: `FactEventExtractorV2.available` used to read
`self._provider.available` — always-True dead weight now that `sodamem.llm`
providers raise
`ProviderError` at construction instead of returning a half-built instance
(see `sodamem.llm.anthropic`/`sodamem.llm.openai_compat`). The property is
deleted outright; the one real remaining question — "was a provider injected
at all" — is asked directly as `self._provider is None` at its single call
site (`extract_session`).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from sodamem.errors import ProviderError
from sodamem.llm.base import LLMProvider
from sodamem.memory._shared import _canonical_entity_id, _canonical_text, _iso_to_ts, _tokenize
from sodamem.models import (
    FactEvent,
    FactKind,
    FactStatus,
    Modality,
    SourceSpan,
    SourceType,
)
from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT

from .calendar_resolve import iso_precision, resolve_date, resolve_range
from .config import ConfidenceWeights, IngestConfig

import json
import logging

logger = logging.getLogger(__name__)

# Max recursion depth for output-truncation re-splitting (PR-1.4). Depth 3 turns
# one over-long window into at most 8 sub-windows — enough for any realistic turn
# density without unbounded recursion.
_MAX_SPLIT_DEPTH = 3

_ALLOWED_KINDS = {x.value for x in FactKind}
_ALLOWED_MODALITIES = {x.value for x in Modality}

# PR-3.2: map off-list modality strings to the closest valid enum value.
_MODALITY_SYNONYMS = {
    "ongoing": "current_state", "habit": "current_state", "habitual": "current_state",
    "routine": "current_state", "recurring": "current_state", "current": "current_state",
    "state": "current_state", "now": "current_state",
    "plan": "future_plan", "planned": "future_plan", "scheduled": "future_plan",
    "upcoming": "future_plan", "future": "future_plan",
    "recommendation": "assistant_advice", "advice": "assistant_advice", "suggestion": "assistant_advice",
    "want": "intent", "intend": "intent", "intention": "intent", "goal": "intent",
    "event": "past_event", "past": "past_event",
}

# The extraction prompt's schema documents event_type/quantity_unit as closed
# enums, but FactEvent stores both as plain str with no runtime enforcement
# (unlike modality, which has _ALLOWED_MODALITIES). An off-enum value from the
# LLM previously passed through with zero indication — not a crash, just a
# value nothing downstream necessarily expects. Log (don't coerce/drop) so the
# rate is visible; consistent with "no silent failure" without risking new
# behavior from forcing an unfamiliar value into a bucket it may not fit.
_ALLOWED_EVENT_TYPES = {
    "flight", "ride", "purchase", "meeting", "trip", "work", "health", "media",
    "food", "preference", "state", "advice", "other",
}
_ALLOWED_QUANTITY_UNITS = {
    "flight_segment", "trip_count", "ride_count", "money", "duration", "count",
    "percent", "item_count", "none", "",
}
# #5 (0705 audit): the LLM emits off-schema unit synonyms (visit_count/game_count/
# run_count for "count"; minutes/time/duration_minutes for "duration"), which
# SPLIT a single semantic across synonyms and break structured sum/count. Fold the
# clear off-schema synonyms into their nearest ALLOWED unit; the schema's intended
# distinctions (trip_count vs ride_count vs count) are left alone. Was gated by
# INGEST_CORRECTNESS (a strict-normalization improvement) — now unconditional.
_QUANTITY_UNIT_CANON = {
    "visit_count": "count", "game_count": "count", "run_count": "count",
    "occurrence_count": "count", "times": "count", "number": "count",
    "duration_minutes": "duration", "minutes": "duration", "time": "duration",
    "hours": "duration", "days": "duration", "weeks": "duration",
}
# #8: negation the extractor never captured (polarity was 100% "positive"). A
# fact whose predicate/support asserts the NEGATIVE ("no longer", "not", "never",
# "stopped", "quit", "cancelled") is tagged polarity="negative" so structured
# queries can tell "does X" from "does NOT X". Text still carries the words.
_NEGATION_RE = re.compile(
    r"\b(no longer|not |n't\b|never |stopped |quit |cancell?ed |gave up|"
    r"no more |ceased |discontinued |used to )", re.I,
)
# #6: the LLM collapses a stated RANGE to a scalar midpoint ("20-30 pages" -> 25),
# losing the range. Detect the range in the source text and preserve [lo, hi] in
# metadata (quantity_range) so nothing is lost; quantity_value is left untouched.
_RANGE_RE = re.compile(r"(\d[\d,]*\.?\d*)\s*(?:-|–|to)\s*(\d[\d,]*\.?\d*)")
# Integer -> English word, for the quantity grounding check: a small integer
# stated in the source as a WORD ("three times a week") is grounded even though
# no digit run appears (2026-07-04 log: 2067 "no matching digit run" warnings were
# overwhelmingly word-number quantities 1-6, not hallucinations). Mirrors the
# word-number gap fixed on the date side (calendar_resolve._EN_NUMBER).
_INT_TO_WORD = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
}

# Future-referring markers inside an otherwise-past fact ("received an
# invitation to a reunion SCHEDULED FOR August 15th"): the fact is past but its
# date field points at a future sub-event, so the yearless month+day roll-back
# (see _resolve_date_value) must not fire — the date resolves forward instead.
_FUTURE_REF_RE = re.compile(
    r"\b(scheduled\s+for|will\s+(?:be|take|start|happen|open|visit|attend)|upcoming|"
    r"planning\s+to|planned\s+for|coming\s+up|booked\s+for|reservation\s+for|"
    r"appointment\s+(?:is\s+)?(?:on|for)|due\s+(?:on|in|by)|rsvp|save\s+the\s+date|"
    r"looking\s+forward\s+to|next\s+(?:week|month|year))\b",
    re.I,
)

# Verbs whose (past-tense) form expresses a STATE CHANGE — the resulting ongoing
# state is what knowledge-update questions need (current_state), not past_event.
_STATE_CHANGE_RE = re.compile(
    r"\b(moved|relocated|switched|changed|quit|left|joined|became|"
    r"started\s+(using|working|living|taking)|now\s+(lives?|uses?|works?|prefers?|takes?)|"
    r"upgraded\s+to|switched\s+to)\b",
    re.I,
)


def _normalize_modality(kind: str, modality: str, item: dict) -> tuple[str, str]:
    """Resolve modality by semantics, not surface tense (PR-3.2).

    - map off-list synonyms to the closest enum value;
    - state-change verbs (moved/switched/now uses ...) => current_state (+kind=state),
      so KU supersession fires (e.g. 830ce83f "She moved to Chicago");
    - enforce kind=state ⟹ current_state and kind=preference ⟹ preference.
    Returns (kind, modality)."""
    m = (modality or "").strip().lower()
    if m not in _ALLOWED_MODALITIES:
        m = _MODALITY_SYNONYMS.get(m, m)
    text = " ".join([str(item.get("predicate_raw") or ""), str(item.get("support_text") or "")])
    if m in ("past_event", "") and _STATE_CHANGE_RE.search(text):
        m = "current_state"
        if kind == "fact":
            kind = "state"
    if kind == "state" and m not in ("current_state", "preference"):
        m = "current_state"
    if kind == "preference":
        m = "preference"
    return kind, m


def _date_ref(item: dict, field: str) -> tuple[str, str]:
    """Read relative-date references from the extractor JSON.

    Preferred shape: {"occurred_date": {"expr": "last Tuesday", "anchor":
    "session_date"}}. Compatibility aliases let older prompts/tests use
    occurred_date_expr / occurred_anchor or date_expr / anchor."""
    ref = item.get(f"{field}_date")
    if isinstance(ref, dict):
        expr = str(ref.get("expr") or ref.get("date_expr") or "").strip()
        anchor = str(ref.get("anchor") or "session_date").strip()
        return expr, anchor
    expr = str(
        item.get(f"{field}_date_expr")
        or (item.get("date_expr") if field == "occurred" else "")
        or ""
    ).strip()
    anchor = str(
        item.get(f"{field}_anchor")
        or item.get("anchor")
        or "session_date"
    ).strip()
    return expr, anchor


def _resolve_date_value(
    item: dict,
    field: str,
    raw_value: Any,
    session_date: str,
    fallback_text: str = "",
) -> Any:
    """Resolve a date field without asking the LLM to do calendar arithmetic.

    Explicit ISO dates may pass through. Relative dates must be represented as
    expr+anchor and are resolved here. The fallback_text path exists only for
    legacy extractor output that omitted expr metadata.

    NO SILENT FAILURE: the deterministic resolver, not the LLM's own guess, is
    authoritative whenever it can resolve the phrase — this is the actual
    mechanism that keeps the LLM from silently computing dates itself (not a
    prompt instruction the model might ignore). Every path where the resolver
    disagrees with, or cannot check, an LLM-supplied absolute value is logged so
    a bad date is never trusted invisibly."""
    if not session_date:
        return raw_value
    expr, anchor = _date_ref(item, field)
    # Modality directs the resolver's directionless forms (bare weekday/month):
    # a future_plan/intent event's "Saturday" is the upcoming one, a past_event's
    # the most recent past. Only affects phrases with no explicit last/next/ago.
    # past_event gets the distinct "past_event" signal — it also rolls a
    # YEARLESS month+day back a year when it lands in the anchor's future
    # (q198: "I just attended ... on March 15-16" said in February is LAST
    # year's March). Plain "past" (current_state/preference/...) keeps the
    # anchor year so a scheduled date ("my flight is on March 15") stays upcoming.
    # GUARD: a past_event whose text refers FORWARD ("received an invitation to
    # a reunion scheduled for August 15th") dates a future sub-event — the fact
    # is past, the date is not. Future-referring markers flip direction to
    # "future" so the date resolves forward instead of rolling back.
    _mod = str(item.get("modality") or "").strip().lower()
    _txt = " ".join([str(item.get("predicate_raw") or ""),
                     str(item.get("support_text") or "")])
    if _mod in ("future_plan", "intent"):
        prefer = "future"
    elif _mod == "past_event":
        prefer = "future" if _FUTURE_REF_RE.search(_txt) else "past_event"
    else:
        prefer = "past"
    # Template-echo guard (2026-07-04 log: 84 exprs were the literal prompt token
    # "session_date"/"before session_date", not a date). "session_date" dates the
    # fact to the session itself; "before session_date" is an unbounded past with
    # no computable point (leave the LLM's raw value / None as-is).
    if expr:
        _e = expr.strip().lower()
        if _e in ("session_date", "session date"):
            return resolve_date("today", session_date) or raw_value
        if _e in ("before session_date", "before session"):
            return raw_value
    if expr and (not anchor or anchor == "session_date"):
        resolved = resolve_date(expr, session_date, prefer=prefer)
        if resolved:
            if raw_value and str(raw_value) != resolved:
                logger.warning(
                    "extractor._resolve_date_value: resolver overrides LLM-computed "
                    "%s (expr=%r, session_date=%r): LLM said %r, resolver says %r — "
                    "using the resolver (deterministic, verifiable)",
                    field, expr, session_date, raw_value, resolved,
                )
            return resolved
        # A vague phrase ("recently", "a while ago") the LLM correctly flagged as
        # relative but the deterministic resolver can't compute — a documented,
        # pending gap (§G.2 "模糊→LLM 兜底"), not a surprise. INFO, not WARNING:
        # visible/measurable, but not an anomaly to chase per-occurrence.
        logger.info(
            "extractor._resolve_date_value: resolver could not resolve %s expr=%r "
            "(vague/unrecognized phrase, session_date=%r) — using the LLM's raw "
            "value %r, unverified",
            field, expr, session_date, raw_value,
        )
    if not raw_value and fallback_text:
        # Only a speculative scan of the correction/support text for an embedded
        # relative phrase the LLM didn't isolate into expr — most facts have no
        # date at all, so finding nothing here is the NORMAL case and must stay
        # silent (warning on every non-dated fact would drown out real signal).
        return resolve_date(fallback_text, session_date, prefer=prefer) or raw_value
    if raw_value and not expr:
        # The LLM filled an absolute value directly with no expr to cross-check —
        # this is the one path with zero independent verification (the residual
        # risk PR-3.1 exists to close). Visible, not silent, so the rate is
        # measurable instead of assumed.
        logger.info(
            "extractor._resolve_date_value: %s=%r has no expr to verify against "
            "(session_date=%r) — using the LLM's value unverified",
            field, raw_value, session_date,
        )
    return raw_value


def _parse_json(raw: str):
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            "_parse_json: direct json.loads failed (%s) — trying brace-extraction "
            "fallback. raw (first 300 chars): %r", e, text[:300],
        )
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            logger.warning(
                "_parse_json: brace-extraction also failed (%s) — returning None, "
                "caller degrades to fallback extraction. raw (first 300 chars): %r",
                e, text[:300],
            )
            return None
    logger.warning(
        "_parse_json: no JSON object/array found in text — returning None, caller "
        "degrades to fallback extraction. raw (first 300 chars): %r", text[:300],
    )
    return None


_PREDICATE_ALIASES = {
    "flight": "travel_by_airline",
    "trip": "travel_trip",
    "ride": "ride_service_used",
    "purchase": "purchase_made",
    "meeting": "meeting_attended",
    "work": "work_event",
    "health": "health_activity",
    "media": "media_consumed",
    "food": "food_preference_or_event",
    "preference": "preference_stated",
    "state": "state_value",
    "advice": "assistant_advice_given",
    "other": "message_unit_statement",
}


def _resolve_predicate_canonical(value: str, event_type: str, kind: str) -> tuple[str, dict]:
    proposed = re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")
    proposed = re.sub(r"_+", "_", proposed)
    if proposed and len(proposed) <= 64 and not proposed.startswith("_"):
        return proposed, {
            "predicate_proposed": value or proposed,
            "predicate_resolver": "normalized_snake_case",
            "canonical_resolution_confidence": 0.78,
        }
    fallback = _PREDICATE_ALIASES.get(event_type) or _PREDICATE_ALIASES.get(kind) or "message_unit_statement"
    return fallback, {
        "predicate_proposed": value or "",
        "predicate_resolver": "event_type_alias_fallback",
        "canonical_resolution_confidence": 0.62,
    }


# D32: unit conversion is canonicalize-on-extract. The resolver NEVER converts;
# it only compares already-canonical values. Duration collapses to a minute base
# so "8 hours" and "480 minutes" never read as a spurious change.
_DURATION_TO_MIN = {
    "second": 1 / 60, "seconds": 1 / 60, "sec": 1 / 60, "secs": 1 / 60,
    "minute": 1, "minutes": 1, "min": 1, "mins": 1,
    "hour": 60, "hours": 60, "hr": 60, "hrs": 60,
    "day": 1440, "days": 1440, "week": 10080, "weeks": 10080,
}
_COUNT_LIKE_UNITS = {"count", "item_count", "trip_count", "ride_count", "flight_segment"}
# Weight has no dedicated enum unit (lands in count/none), yet the spec names
# "lbs vs kg" explicitly. Collapse to a gram base so the same weight stated in
# different units never reads as a change.
_WEIGHT_TO_GRAM = {
    "kg": 1000.0, "kgs": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "g": 1.0, "gram": 1.0, "grams": 1.0,
    "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237,
    "oz": 28.349523, "ounce": 28.349523, "ounces": 28.349523,
}


def _parse_quantity_range(text: str) -> Optional[tuple[float, float]]:
    """Parse an explicit count range like '3-5 times' or 'between 3 and 5'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|—|to|through)\s*(\d+(?:\.\d+)?)", text, re.I)
    if not m:
        m = re.search(r"between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)", text, re.I)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi:
            return (lo, hi)
    return None


def _canonicalize_quantity(value: Optional[float], unit: str, text: str) -> tuple[Optional[float], dict]:
    """Canonicalize-on-extract (D32).

    Records the canonical (base-unit) value in metadata for the resolver/compute
    layer WITHOUT mutating ``quantity_value`` — the stored value stays the
    natural stated form ("180 kg" stays 180, not 180000 g) so evidence cards and
    the answerer never see an unnatural unit. Returns (value_unchanged, meta).
    """
    meta: dict = {}
    # Range only for count-like measures, so date fragments like '2026-02' or
    # 'the 20th-22nd' (which arrive with a date-ish unit or no value) never
    # masquerade as a quantity range.
    if unit in _COUNT_LIKE_UNITS:
        rng = _parse_quantity_range(text)
        if rng is not None:
            meta["quantity_range"] = [rng[0], rng[1]]
    if value is not None:
        canonical: Optional[tuple[float, str, str]] = None  # (value, base_unit, raw_word)
        if unit == "duration":
            m = re.search(
                r"(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks)\b",
                text, re.I,
            )
            if m and float(m.group(1)) == float(value):
                factor = _DURATION_TO_MIN.get(m.group(2).lower())
                if factor:
                    canonical = (round(float(value) * factor, 6), "minute", m.group(2).lower())
        else:
            # Weight has no enum unit; detect from text. Guard the value matches
            # the number right before the weight word.
            wm = re.search(
                r"(\d+(?:\.\d+)?)\s*(kgs?|kilograms?|grams?|lbs?|pounds?|ounces?|oz|g)\b",
                text, re.I,
            )
            if wm and float(wm.group(1)) == float(value):
                factor = _WEIGHT_TO_GRAM.get(wm.group(2).lower())
                if factor:
                    canonical = (round(float(value) * factor, 6), "gram", wm.group(2).lower())
        if canonical is not None:
            meta["quantity_canonical_value"] = canonical[0]
            meta["quantity_canonical_unit"] = canonical[1]
            meta["quantity_raw_unit_word"] = canonical[2]
    return value, meta


def _source_alignment_features(fact: FactEvent, spans: list[SourceSpan]) -> dict:
    raw_text = " ".join(s.text for s in spans)
    support = fact.metadata.get("support_text", "") if isinstance(fact.metadata, dict) else ""
    support_norm = _canonical_text(support)
    raw_norm = _canonical_text(raw_text)
    exact_support = bool(support_norm and support_norm in raw_norm)
    has_source = bool(spans)
    required_fields = [
        bool(fact.predicate_raw),
        bool(fact.source_span_ids),
        bool(fact.kind),
        bool(fact.source_type),
    ]
    field_completeness = sum(1 for x in required_fields if x) / len(required_fields)
    return {
        "has_source_trace": has_source,
        "support_exact_in_source": exact_support,
        "field_completeness": field_completeness,
        "source_type_weight": 1.0 if fact.source_type == SourceType.EXPLICIT_TEXT else 0.65,
        "alignment_confidence": min((s.alignment_confidence for s in spans), default=0.0),
    }


def _compute_system_confidence(
    fact: FactEvent,
    spans: list[SourceSpan],
    weights: ConfidenceWeights,
    llm_self_confidence: Optional[float] = None,
) -> tuple[float, str, dict]:
    features = _source_alignment_features(fact, spans)
    score = 0.0
    score += weights.has_source_trace if features["has_source_trace"] else 0.0
    score += weights.support_exact_match if features["support_exact_in_source"] else weights.support_inexact_match
    score += weights.field_completeness_weight * float(features["field_completeness"])
    score += weights.source_type_weight * float(features["source_type_weight"])
    score += weights.alignment_weight * float(features["alignment_confidence"])
    if fact.occurred_start is not None or fact.valid_from is not None:
        score += weights.has_time_bonus
    if fact.quantity_value is not None or fact.quantity_unit:
        score += weights.has_quantity_bonus
    if llm_self_confidence is not None:
        score += weights.llm_self_confidence_weight * max(0.0, min(1.0, float(llm_self_confidence)))
        features["llm_self_confidence"] = llm_self_confidence
    confidence = max(weights.min, min(weights.max, score))
    reason_bits = ["source_type", "span_alignment", "field_completeness"]
    if features["support_exact_in_source"]:
        reason_bits.append("exact_support_text")
    return round(confidence, 3), "+".join(reason_bits), features


class FactEventExtractorV2:
    """LLM-backed SourceSpan -> FactEvent extractor."""

    def __init__(self, provider: Optional[LLMProvider] = None, *, config: Optional[IngestConfig] = None):
        self._provider = provider
        self._config = config or IngestConfig()
        # Diagnostic counters for the extract-window A/B (turn/overlap sweep).
        # split_events = how many times a window overflowed the output cap and was
        # halved; deepest_split = deepest recursion reached; split_exhausted = a
        # window still truncating at _MAX_SPLIT_DEPTH (dropped to fallback). The
        # client resets these per session (ingest_session) and reads them into
        # counts. A high split rate means the window is too large for the model's
        # output cap — the signal that separates "whole-session" from small windows.
        self.split_events = 0
        self.deepest_split = 0
        self.split_exhausted = 0
        # split-exhausted spans recovered by the 2x-cap retry instead of degrading.
        self.high_budget_recovered = 0

    def usage_summary(self) -> dict:
        if self._provider is None:
            return {}
        return self._provider.usage_summary()

    def extract_session(
        self,
        spans: list[SourceSpan],
        *,
        user_id: str,
        session_date: str = "",
    ) -> list[FactEvent]:
        if not spans:
            return []
        if self._provider is None:
            return self._fallback_facts(spans, user_id)
        return self._extract_recursive(spans, user_id, session_date, depth=0)

    def _extract_recursive(
        self, spans: list[SourceSpan], user_id: str, session_date: str, depth: int
    ) -> list[FactEvent]:
        """Extract; on output truncation, split the spans in half and retry each.

        A fact-dense window can overflow the output token cap, producing invalid
        (truncated) JSON. Rather than degrade the whole window to fallback, halve
        the spans so each sub-window fits. Bounded by _MAX_SPLIT_DEPTH."""
        facts, need_split = self._extract_once(spans, user_id, session_date)
        if need_split and len(spans) > 1 and depth < _MAX_SPLIT_DEPTH:
            self.split_events += 1
            self.deepest_split = max(self.deepest_split, depth + 1)
            logger.warning(
                "extractor: output-cap split at depth=%d over %d spans "
                "(window too large for the model output cap)", depth, len(spans)
            )
            mid = len(spans) // 2
            left = self._extract_recursive(spans[:mid], user_id, session_date, depth + 1)
            right = self._extract_recursive(spans[mid:], user_id, session_date, depth + 1)
            return left + right
        if facts is None:
            self.split_exhausted += 1
            # Before degrading to structureless fallback: one retry of this
            # (single/undividable) span at a RAISED output budget. The fallback
            # here is almost always a fact-dense span whose STRUCTURED output
            # overflowed the normal cap — not a genuinely unparseable input — so
            # doubling the cap usually recovers real facts instead of losing them.
            # Only if it STILL truncates do we fall back for real.
            retry_facts, _ = self._extract_once(
                spans, user_id, session_date, cap_override=self._extract_cap() * 2
            )
            if retry_facts is not None:
                self.high_budget_recovered += 1
                logger.info(
                    "extractor: high-budget retry (2x cap) recovered %d span(s) that "
                    "hit the output cap — avoided degraded fallback", len(spans),
                )
                return retry_facts
            logger.warning(
                "extractor: split exhausted at depth=%d over %d spans → fallback "
                "(fact-dense window still truncating even at 2x cap)", depth, len(spans)
            )
            return self._fallback_facts(spans, user_id)
        return facts

    def _extract_once(
        self, spans: list[SourceSpan], user_id: str, session_date: str,
        cap_override: Optional[int] = None,
    ) -> tuple[Optional[list[FactEvent]], bool]:
        """One extraction call. Returns (facts, need_split).

        need_split=True (with facts=None) means the output was truncated and the
        caller should re-chunk. On non-truncation parse failure, returns
        (fallback_facts, False). cap_override raises the output-token budget for
        the split-exhausted high-budget retry."""
        lines = [
            f"[{span.span_id}] ({span.role}, session={span.session_id}) {span.text}"
            for span in spans
        ]
        prompt = (
            f"Session date: {session_date or 'unknown'}\n\n"
            "SourceSpans:\n" + "\n".join(lines)
        )
        try:
            data, truncated = self._extract_single(prompt, cap_override)
            if isinstance(data, list):
                span_ids = {s.span_id for s in spans}
                facts = [
                    f for f in (self._fact_from_item(item, user_id, span_ids, spans, session_date) for item in data)
                    if f is not None
                ]
                if not facts:
                    # Distinguish the two cases the historical `facts or
                    # fallback` conflated (see IngestConfig.extract.
                    # empty_extraction_is_empty): a model that returned `[]`
                    # made a JUDGEMENT (nothing here is about the user);
                    # a model that returned items we all rejected FAILED.
                    if data and not self._config.extract.empty_extraction_is_empty:
                        facts = self._fallback_facts(spans, user_id)
                    elif data:
                        logger.info(
                            "extractor: %d item(s) all failed validation for %d span(s) "
                            "— empty_extraction_is_empty ON, returning no facts",
                            len(data), len(spans),
                        )
                    elif not self._config.extract.empty_extraction_is_empty:
                        facts = self._fallback_facts(spans, user_id)
                    else:
                        logger.debug(
                            "extractor: model returned [] for %d span(s) — nothing "
                            "about the user here; no fallback noise emitted", len(spans),
                        )
                return facts, False
            # Parse failed. If the model hit the length cap, the JSON is almost
            # certainly truncated — signal a re-split rather than fallback.
            if truncated:
                return None, True
            return self._fallback_facts(spans, user_id), False
        except ProviderError as e:
            # A provider OUTAGE (rate limit / timeout / unavailable / exhausted
            # empty-content retries — sodamem.llm already classifies all of these
            # into a typed ProviderError at the point the SDK call fails) is not a
            # per-span defect — it degrades EVERY touched span to
            # message_unit_statement noise. Make it LOUD (ERROR) and, under
            # IngestConfig.fail_fast, abort so the operator fixes the provider
            # rather than shipping a corrupted store. Ordinary parse/other errors
            # (below) keep the WARNING + degraded-fallback behaviour.
            #
            # R3 audit note: the source classified outages by regex-matching the
            # exception STRING ("402|401|insufficient balance|quota|billing|..."),
            # a fragile heuristic. sodamem.llm's providers now raise a typed
            # ProviderError with a real .code at the exact point the SDK call
            # fails (Task 4), so this catches the TYPE instead of pattern-matching
            # the message — the regex is gone, not ported.
            logger.error(
                "FactEventExtractorV2: provider OUTAGE (%s: %s) — spans degrade to "
                "message_unit_statement noise; fix the provider / credentials and "
                "re-ingest the affected users", e.code.value, str(e)[:200],
            )
            if self._config.fail_fast:
                raise
            return self._fallback_facts(spans, user_id), False
        except Exception as e:  # noqa: BLE001 - benign-by-design degraded path,
            # deliberate and LOUDLY
            # logged (WARNING) degrade-to-fallback, not a silent swallow — the
            # resulting facts are visibly tagged predicate_canonical=
            # "message_unit_statement" so a caller inspecting the store can tell
            # this branch fired. Kept identical to source behaviour.
            logger.warning("FactEventExtractorV2 failed: %s", e)
            return self._fallback_facts(spans, user_id), False

    def _extract_cap(self) -> int:
        """Output-token cap: explicit config override > model endpoint ceiling > 8192."""
        if self._config.extractor_llm_max_tokens is not None:
            return self._config.extractor_llm_max_tokens
        return getattr(self._provider, "max_output_tokens", None) or 8192

    def _extract_single(self, prompt: str, cap_override: Optional[int] = None) -> tuple[Optional[list], bool]:
        """Single-call extraction (the only extraction path — EXTRACT_ENRICH's
        two-stage variant was a dead R2.9 flag, deleted). Returns
        (parsed_data_or_None, truncated)."""
        # EXTRACT + DETERMINISM, and nothing else. A coarse-granularity
        # variant was gated behind INGEST_EXTRACT_COARSE (default OFF, never
        # enabled by any store) and lost its A/B on hard-47 (H 24 > BASE 21 >
        # C 18); it was deleted 0806. These two are what the fingerprint
        # covers, so this assembly and `SodaMem.open`'s fp_prompts must agree.
        system = EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES
        raw = self._provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=cap_override or self._extract_cap(),
            temperature=self._config.extractor_llm_temperature,
            usage_phase="ingest_extractor",
        )
        truncated = getattr(self._provider, "_last_finish_reason", None) == "length"
        return _parse_json(raw), truncated

    @staticmethod
    def _match_span_by_quote(item: dict, spans: list[SourceSpan]) -> Optional[str]:
        """Recover provenance for a fact whose LLM source_span_ids were invalid.

        Returns a span_id only when the fact's quote maps UNAMBIGUOUSLY to one
        span (unique substring match, else a single high token-overlap span).
        Returns None when no/ambiguous match — caller drops the fact.

        Strict grounding only (R2.9: GRAPH_V2_LOOSE_GROUNDING was a loser flag,
        deleted along with its looser 0.6-overlap / pick-first-on-ambiguous
        path — the 0.8 threshold and "ambiguous means drop" are now the only
        behaviour, not a conditional)."""
        quote = (item.get("support_text") or item.get("predicate_raw") or "").strip().lower()
        if len(quote) < 6:
            return None
        substr = [s.span_id for s in spans if quote in (s.text or "").lower()]
        if len(substr) == 1:
            return substr[0]
        if substr:
            # Ambiguous multi-substring match: drop rather than guess.
            return None
        qt = {t for t in _tokenize(quote) if len(t) > 2}
        if len(qt) < 3:
            return None
        best, best_ov = None, 0.0
        for s in spans:
            st = {t for t in _tokenize(s.text or "") if len(t) > 2}
            if not st:
                continue
            ov = len(qt & st) / len(qt)
            if ov > best_ov:
                best, best_ov = s.span_id, ov
        # #9: the LLM often PARAPHRASES, so the verbatim quote scores <0.8 overlap
        # and the fact is dropped → degraded fallback.
        return best if best_ov >= 0.8 else None

    def _fact_from_item(
        self,
        item: dict,
        user_id: str,
        allowed_span_ids: set[str],
        spans: list[SourceSpan],
        session_date: str = "",
    ) -> Optional[FactEvent]:
        if not isinstance(item, dict):
            return None
        source_span_ids = [
            sid for sid in item.get("source_span_ids", [])
            if sid in allowed_span_ids
        ]
        if not source_span_ids:
            # PR-2.1 (unconditional — INGEST_CORRECTNESS folded to always-on):
            # never forge provenance by binding to spans[0]. Try to recover the
            # true source by matching the fact's quote to a span; if none
            # matches, drop the fact rather than attach a wrong source. The
            # source's `else: source_span_ids = [spans[0].span_id]` fallback
            # (the losing INGEST_CORRECTNESS=False path) is deleted, not kept.
            matched = self._match_span_by_quote(item, spans)
            if matched is None:
                return None
            source_span_ids = [matched]
        # Role-aware extraction (IngestConfig.extract.user_only): drop a fact
        # whose provenance is assistant-ONLY. A fact grounded in any user span
        # is kept (the user's own info, even if paraphrased by the assistant).
        # raw_turns are untouched → assistant recommendations remain in raw recall.
        if self._config.extract.user_only:
            _src = [s for s in spans if s.span_id in source_span_ids]
            if _src and all((getattr(s, "role", "user") or "user") == "assistant" for s in _src):
                return None
        kind = str(item.get("kind") or "fact")
        if kind not in _ALLOWED_KINDS:
            kind = "fact"
        modality = str(item.get("modality") or "past_event")
        # PR-3.2 (unconditional — INGEST_DETERMINISM folded to always-on):
        # normalize modality by SEMANTICS, not surface tense. The LLM types
        # relocations/switches as past_event (because "moved" is past tense),
        # which blocks KU supersession (needs state/current_state).
        kind, modality = _normalize_modality(kind, modality, item)
        if modality not in _ALLOWED_MODALITIES:
            # The source's INGEST_DETERMINISM=False fallback (coerce to
            # "past_event" and keep the fact) is deleted, not kept — dropping
            # an unnormalizable modality is now the only behaviour.
            return None
        roles = item.get("entity_roles") or {}
        if not isinstance(roles, dict):
            roles = {}
        quantity_value = item.get("quantity_value")
        try:
            quantity_value = None if quantity_value in ("", None, "null") else float(quantity_value)
        except (TypeError, ValueError):
            logger.warning(
                "extractor: LLM gave non-numeric quantity_value=%r for predicate=%r "
                "— dropping to None", item.get("quantity_value"), item.get("predicate_raw"),
            )
            quantity_value = None
        quantity_unit = str(item.get("quantity_unit") or "")
        # #5 (unconditional): fold off-schema unit synonyms into their nearest
        # allowed unit so a single semantic doesn't split across synonyms.
        if quantity_unit in _QUANTITY_UNIT_CANON:
            quantity_unit = _QUANTITY_UNIT_CANON[quantity_unit]
        if quantity_unit not in _ALLOWED_QUANTITY_UNITS:
            logger.info(
                "extractor: off-schema quantity_unit=%r for predicate=%r — kept "
                "as-is, not coerced (schema documents: %s)",
                quantity_unit, item.get("predicate_raw"), sorted(_ALLOWED_QUANTITY_UNITS),
            )
        if quantity_unit == "none":
            quantity_unit = ""
        # #6 (unconditional): preserve a stated range the LLM collapsed to a
        # midpoint. Keep [lo,hi] in metadata (quantity_value is left as the LLM
        # gave it) so nothing is lost.
        quantity_range = None
        if quantity_value is not None:
            _rm = _RANGE_RE.search(str(item.get("support_text") or ""))
            if _rm:
                try:
                    _lo = float(_rm.group(1).replace(",", ""))
                    _hi = float(_rm.group(2).replace(",", ""))
                    if _lo != _hi and min(_lo, _hi) <= quantity_value <= max(_lo, _hi):
                        quantity_range = [min(_lo, _hi), max(_lo, _hi)]
                except ValueError:
                    pass
        quantity_text = " ".join([
            str(item.get("predicate_raw") or ""),
            str(item.get("support_text") or ""),
        ])
        if quantity_value is not None:
            # Loose cross-check: does the claimed number appear anywhere in the
            # source text (any common formatting: with/without decimals, commas,
            # currency symbols)? A miss doesn't mean the LLM is wrong (it may have
            # summed/converted units), but it IS a signal worth being able to see.
            _qv_str = f"{quantity_value:g}"
            _clean = quantity_text.replace(",", "")
            _digit_ok = re.search(r"\b" + re.escape(_qv_str.split(".")[0]) + r"\b", _clean)
            # word-number grounding: "three times a week" grounds quantity_value=3
            _word = _INT_TO_WORD.get(int(quantity_value)) if float(quantity_value).is_integer() else None
            _word_ok = _word and re.search(r"\b" + _word + r"\b", _clean.lower())
            if not _digit_ok and not _word_ok:
                logger.info(
                    "extractor: quantity_value=%r for predicate=%r has no matching "
                    "digit run in the source text — LLM may have computed/summed "
                    "it rather than quoted it verbatim: %r",
                    quantity_value, item.get("predicate_raw"), quantity_text[:200],
                )
        quantity_value, quantity_meta = _canonicalize_quantity(quantity_value, quantity_unit, quantity_text)
        if quantity_range is not None:
            quantity_meta = {**quantity_meta, "quantity_range": quantity_range}
        # Subject: the literal below was unconditional until 0726. The LLM's
        # own answer lives in roles["subject"] and was being discarded — the
        # prompt was never the only cause of the star schema, this line was.
        subject_entity_id = "entity_user"
        if self._config.extract.entity_subject:
            raw_subject = roles.get("subject")
            # 'assistant' is excluded alongside 'user': both are conversational
            # ROLES, not knowledge entities. The retrieval-diff probe measured
            # 22.3% of would-flip subjects as the literal 'assistant' (the
            # model answering "who said this advice" rather than "what is this
            # knowledge about") — honoring it would replace the perfect star
            # with a double star around a second fake hub.
            if isinstance(raw_subject, str) and raw_subject.strip().lower() not in ("", "user", "assistant"):
                subject_entity_id = _canonical_entity_id(raw_subject)
        object_entity_ids = []
        for role, value in roles.items():
            if role == "subject":
                continue
            values = value if isinstance(value, list) else [value]
            object_entity_ids.extend(_canonical_entity_id(v) for v in values if v)
        provenance_spans = [s for s in spans if s.span_id in source_span_ids]
        # document_time = when the user STATED this fact (latest session_time over
        # its source spans) — the mention/observed time axis. Deterministic,
        # 100% available. First-class FactEvent field: the supersession tie-break
        # uses said-order over ingest arrival-order (created_at) for facts with
        # no real event/valid time, so out-of-order / repair ingest can't
        # mis-order them.
        _doc_time = max(
            (s.session_time for s in provenance_spans if s.session_time is not None),
            default=None,
        )
        event_type = str(item.get("event_type") or "")
        if event_type and event_type not in _ALLOWED_EVENT_TYPES:
            logger.info(
                "extractor: off-schema event_type=%r for predicate=%r — kept "
                "as-is, not coerced (schema documents: %s)",
                event_type, item.get("predicate_raw"), sorted(_ALLOWED_EVENT_TYPES),
            )
        predicate_canonical, resolver_meta = _resolve_predicate_canonical(
            str(item.get("predicate_canonical") or ""),
            event_type,
            kind,
        )
        # Scan predicate_raw AND support_text together: the correction marker
        # ("actually", "not X but Y") often lands only in the verbatim support
        # quote, not in the normalized predicate. An `or` short-circuit would
        # miss it whenever predicate_raw is non-empty.
        correction_haystack = " ".join([
            str(item.get("predicate_raw") or ""),
            str(item.get("support_text") or ""),
        ])
        # Keep this conservative: a broad "not ...," pattern over-flags ordinary
        # contrasts ("flew to Boston, not Chicago") as corrections, which makes
        # them update-like and wrongly supersedes earlier events — that tanked
        # multi-session reasoning. Require the explicit "not X, but Y" shape.
        source_type = SourceType.USER_CORRECTION if re.search(
            r"\b(correction|correcting|actually|rather|instead|not .*, but)\b",
            correction_haystack,
            re.I,
        ) else SourceType.EXPLICIT_TEXT
        if source_type != SourceType.USER_CORRECTION and re.search(
            r"\b(probably|maybe|likely|might|seems?|appears?|guess|assume|infer)\b",
            correction_haystack,
            re.I,
        ):
            source_type = SourceType.INFERRED_LLM
        # PR-3.1 (unconditional): deterministic date resolution. Relative date
        # phrases are passed as expr+anchor and resolved here; legacy null-fill
        # remains as a fallback.
        occurred_raw = item.get("occurred_start")
        valid_from_raw = item.get("valid_from")
        occurred_end_raw = item.get("occurred_end")
        time_precision = None
        time_source = "none"
        tz_known = False
        weekday = None
        # A range phrase ("the past 3 months") fills BOTH occurred ends.
        occ_expr, occ_anchor = _date_ref(item, "occurred")
        occ_range = (
            resolve_range(occ_expr, session_date)
            if (session_date and occ_expr and (not occ_anchor or occ_anchor == "session_date"))
            else None
        )
        if occ_range:
            occurred_raw, occurred_end_raw = occ_range
        else:
            occurred_raw = _resolve_date_value(
                item, "occurred", occurred_raw, session_date, correction_haystack
            )
        # "for N units" (a state's running duration) -> valid_from = anchor−N.
        vf_expr, vf_anchor = _date_ref(item, "valid_from")
        vf_range = (
            resolve_range(vf_expr, session_date)
            if (session_date and vf_expr and (not vf_anchor or vf_anchor == "session_date"))
            else None
        )
        if vf_range:
            valid_from_raw = vf_range[0]  # state began at the range start
        else:
            valid_from_raw = _resolve_date_value(
                item, "valid_from", valid_from_raw, session_date
            )
        # §0 precision: derive from the (possibly partial) resolved ISO string.
        for _v in (occurred_raw, valid_from_raw):
            if isinstance(_v, str) and re.match(r"\d{4}", _v):
                time_precision = iso_precision(_v)
                break
        # time_source = hard-filter safety valve: explicit absolute date the LLM
        # gave, vs a resolved relative phrase, vs none. (mention-backfill =
        # "inferred" is tagged later.) Only explicit/resolved times are safe to
        # hard-filter on.
        _has_time = bool(occurred_raw or valid_from_raw)
        _had_absolute = bool(item.get("occurred_start") or item.get("valid_from"))
        time_source = "none" if not _has_time else ("explicit" if _had_absolute else "resolved_relative")
        tz_known = bool(re.search(r"[+-]\d{2}:?\d{2}$|Z$", str(occurred_raw or valid_from_raw or "")))
        # weekday: deterministic derived field (datetime, not LLM), kept OUT of
        # the ISO string; handed to the reader. Only meaningful at day+ precision.
        if time_precision in ("day", "hour", "minute", "second"):
            _wts = _iso_to_ts(occurred_raw or valid_from_raw)
            if _wts:
                try:
                    weekday = datetime.fromtimestamp(_wts).strftime("%A")
                except (ValueError, OSError, OverflowError) as e:
                    logger.warning(
                        "extractor: could not derive weekday from epoch %r "
                        "(%s) — leaving weekday unset", _wts, e,
                    )
        fact = FactEvent(
            user_id=user_id,
            kind=FactKind(kind),
            status=FactStatus.ACTIVE,
            subject_entity_id=subject_entity_id,
            predicate_raw=str(item.get("predicate_raw") or item.get("support_text") or ""),
            predicate_canonical=predicate_canonical,
            event_type=event_type,
            object_entity_ids=object_entity_ids,
            modality=Modality(modality),
            occurred_start=_iso_to_ts(occurred_raw),
            occurred_end=_iso_to_ts(occurred_end_raw) or _iso_to_ts(occurred_raw),
            valid_from=_iso_to_ts(valid_from_raw),
            valid_until=_iso_to_ts(item.get("valid_until")),
            document_time=_doc_time,
            quantity_value=quantity_value,
            quantity_unit=quantity_unit,
            source_type=source_type,
            source_span_ids=source_span_ids,
            provenance={
                "session_ids": sorted({s.session_id for s in provenance_spans}),
                "turn_ids": sorted({s.turn_id for s in provenance_spans}),
                "extractor_version": "fact_event_extractor_v2_llm",
            },
            metadata={
                "entity_roles": roles,
                "support_text": str(item.get("support_text") or ""),
                **({"time_precision": time_precision} if time_precision else {}),
                "time_source": time_source,
                **({"tz_known": tz_known} if (occurred_raw or valid_from_raw) else {}),
                **({"weekday": weekday} if weekday else {}),
                **resolver_meta,
                **quantity_meta,
            },
        )
        if not fact.predicate_raw:
            fact.predicate_raw = " ".join(s.text for s in provenance_spans)[:300]
        # Temporal interval sanity (§E.1: STATIC/DYNAMIC intervals must be
        # non-inverted). Not a crash — the LLM's raw dates are trusted for the
        # value, but an inverted interval is a real extraction defect worth
        # seeing, not silently stored as an unusable interval.
        if fact.valid_until is not None and fact.valid_from is not None and fact.valid_until < fact.valid_from:
            logger.warning(
                "extractor: inverted validity interval on predicate=%r — "
                "valid_until(%r) < valid_from(%r)",
                fact.predicate_raw, item.get("valid_until"), valid_from_raw,
            )
            # A backwards validity interval is never usable (the relative-year
            # mis-resolution "finish before February" stated in May → Feb < May).
            # DROP the bad upper bound (open-ended) rather than store an interval
            # that reads as already-expired (unconditional correctness fix).
            fact.valid_until = None
        if fact.occurred_end is not None and fact.occurred_start is not None and fact.occurred_end < fact.occurred_start:
            logger.warning(
                "extractor: inverted occurred interval on predicate=%r — "
                "occurred_end(%r) < occurred_start(%r)",
                fact.predicate_raw, occurred_end_raw, occurred_raw,
            )
            # e.g. "by 4pm today": timed start + date-only end → end=midnight <
            # start. Clamp the interval to a point (end := start) instead of
            # storing a backwards span (unconditional correctness fix).
            fact.occurred_end = fact.occurred_start
        # §E.2 mention-time backfill (IngestConfig.backfill_session_time): before
        # declaring a non-preference fact time-less, anchor it to document_time
        # (said/mention time, already computed above — deterministic, 100%
        # available). A state gets valid_from (validity begins ≥ mention); an
        # event/fact gets occurred_start (mention ≈ occurrence when none given).
        # time_source='inferred_session' → never hard-filterable (it's a guess).
        if (self._config.backfill_session_time and fact.kind != FactKind.PREFERENCE
                and fact.occurred_start is None and fact.valid_from is None
                and _doc_time is not None):
            if (fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind) == FactKind.STATE.value:
                fact.valid_from = _doc_time
            else:
                fact.occurred_start = _doc_time
            fact.metadata["time_source"] = "inferred_session"
            fact.metadata.setdefault("time_precision", "day")
        # §E.2: STATIC/DYNAMIC facts (kind=fact/event/state, not preference) with
        # NO time signal at all are an extraction defect per the contract's own
        # root-cause finding (real no-time facts are ~always non-fact noise, not
        # legitimately timeless STATIC/DYNAMIC content) — unconditional.
        if (fact.kind != FactKind.PREFERENCE
                and fact.occurred_start is None and fact.valid_from is None):
            logger.info(
                "extractor: kind=%s fact has NO time signal (occurred_start and "
                "valid_from both unset) for predicate=%r — per §E.2 this is "
                "usually an extraction-precision issue, not a legitimately "
                "timeless fact",
                fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind,
                fact.predicate_raw,
            )
        llm_self_confidence = item.get("confidence")
        try:
            llm_self_confidence = None if llm_self_confidence in ("", None, "null") else float(llm_self_confidence)
        except (TypeError, ValueError):
            logger.warning(
                "extractor: LLM gave non-numeric confidence=%r for predicate=%r — "
                "dropping to None", item.get("confidence"), item.get("predicate_raw"),
            )
            llm_self_confidence = None
        confidence, reason, features = _compute_system_confidence(
            fact, provenance_spans, self._config.confidence, llm_self_confidence
        )
        fact.confidence = confidence
        fact.confidence_reason = reason
        fact.metadata["confidence_features"] = features
        # #8 (unconditional): capture negation the extractor never set (polarity
        # was 100% positive). Scan predicate + support so "I no longer drink
        # coffee" is polarity=negative, not stored as an affirmative "drinks coffee".
        if _NEGATION_RE.search(
            " ".join([fact.predicate_raw or "", fact.predicate_canonical or "",
                      str(item.get("support_text") or "")])
        ):
            fact.polarity = "negative"
        return fact

    def _fallback_facts(self, spans: list[SourceSpan], user_id: str) -> list[FactEvent]:
        # Degraded path: no LLM structured extraction happened (no provider
        # injected, or JSON parse failed even after max split-retry depth) —
        # every fact this produces has NO date fields (occurred_start/valid_from/
        # valid_until all None) and modality is a coarse role-based guess, not
        # analyzed. This must be visible: a caller silently getting generic
        # "message_unit_statement" facts instead of real extraction is exactly
        # the failure mode this policy exists to catch.
        logger.warning(
            "extractor._fallback_facts: degraded fallback extraction for %d span(s) "
            "(session_ids=%s) — facts will have NO date fields and generic "
            "predicate_canonical='message_unit_statement'",
            len(spans), sorted({s.session_id for s in spans}),
        )
        facts = []
        for span in spans:
            if not span.text.strip():
                continue
            modality = Modality.ASSISTANT_ADVICE if span.role == "assistant" else Modality.PAST_EVENT
            kind = FactKind.FACT
            if span.role == "user" and re.search(r"\bprefer|like|love|hate|enjoy\b", span.text, re.I):
                kind = FactKind.PREFERENCE
                modality = Modality.PREFERENCE
            fact = FactEvent(
                user_id=user_id,
                kind=kind,
                predicate_raw=span.text[:500],
                predicate_canonical="message_unit_statement",
                event_type="other",
                modality=modality,
                source_span_ids=[span.span_id],
                provenance={
                    "session_ids": [span.session_id],
                    "turn_ids": [span.turn_id],
                    "extractor_version": "fact_event_extractor_v2_fallback",
                },
                metadata={
                    "entity_roles": {"subject": "user"},
                    "support_text": span.text[:500],
                    "predicate_resolver": "fallback_message_unit_statement",
                },
            )
            confidence, reason, features = _compute_system_confidence(fact, [span], self._config.confidence, None)
            fact.confidence = min(confidence, self._config.confidence.fallback_cap)
            fact.confidence_reason = "fallback+" + reason
            fact.metadata["confidence_features"] = features
            facts.append(
                fact
            )
        return facts
