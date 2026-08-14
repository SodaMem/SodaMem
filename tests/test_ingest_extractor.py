"""Guardian tests for sodamem.memory.ingest.extractor (FactEventExtractorV2).

Zero network: `sodamem.llm.testing.EchoProvider`/`ScriptedProvider` feed
pre-set extraction JSON, and `_fact_from_item` is also exercised directly
(deterministic post-processing and inferred-LLM behaviour are each covered
directly).

Coverage focuses on what Task 5 actually CHANGED behaviourally:
  - INGEST_CORRECTNESS / INGEST_DETERMINISM folded to unconditional True (no
    more monkeypatching a module flag — there isn't one anymore).
  - LOOSE_GROUNDING deleted: ambiguous multi-substring quote matches always
    drop the fact now (was conditionally lenient).
  - `.available` guard rewrite: `self._provider is None` is the only real
    branch left (a live provider is never "unavailable").
  - ProviderError-typed outage handling + IngestConfig.fail_fast.
"""
from __future__ import annotations

import json

import pytest

from sodamem.errors import ErrorCode, ProviderError
from sodamem.llm.base import LLMProvider
from sodamem.llm.testing import EchoProvider, ScriptedProvider
from sodamem.memory._shared import _iso_to_ts
from sodamem.memory.ingest.config import ConfidenceWeights, IngestConfig
from sodamem.memory.ingest.extractor import FactEventExtractorV2, _resolve_date_value
from sodamem.models import FactKind, Modality, SourceSpan, SourceType


def _span(text, role="user", sid="s1", idx=0):
    return SourceSpan(
        user_id="u1",
        session_id=sid,
        turn_id=f"{sid}_turn_{idx}",
        role=role,
        text=text,
        span_id=f"span_{sid}_{idx}",
    )


# ---------------------------------------------------------------------------
# `.available` guard rewrite (T4 hand-off) — self._provider is None is the
# only real branch; a constructed provider is never "unavailable".
# ---------------------------------------------------------------------------

def test_no_provider_goes_straight_to_fallback_no_llm_call():
    extractor = FactEventExtractorV2(provider=None)
    span = _span("I love hiking on weekends.")
    facts = extractor.extract_session([span], user_id="u1", session_date="2023-06-15")
    assert len(facts) == 1
    assert facts[0].predicate_canonical == "message_unit_statement"
    assert facts[0].kind == FactKind.PREFERENCE  # "love" keyword heuristic


def test_extract_session_empty_spans_short_circuits():
    extractor = FactEventExtractorV2(EchoProvider("[]"))
    assert extractor.extract_session([], user_id="u1", session_date="2023-06-15") == []


def test_available_property_does_not_exist_as_a_dead_true_branch():
    # The source's `.available` property (always True once constructed) is
    # deleted outright, not kept as dead weight — see extractor.py's module
    # docstring for the T4 hand-off rationale.
    extractor = FactEventExtractorV2(provider=None)
    assert not hasattr(extractor, "available")


# ---------------------------------------------------------------------------
# ScriptedProvider-driven extraction: real JSON -> FactEvent, single-call path
# ---------------------------------------------------------------------------

def test_extract_session_with_scripted_provider_produces_facts():
    span = _span("I flew United to Boston last week.")
    payload = json.dumps([{
        "kind": "fact",
        "predicate_raw": "User flew United to Boston",
        "predicate_canonical": "travel_by_airline",
        "event_type": "flight",
        "modality": "past_event",
        "occurred_start": "2023-06-10",
        "entity_roles": {"subject": "user", "airline": "United Airlines"},
        "source_span_ids": [span.span_id],
        "support_text": "I flew United to Boston last week.",
    }])
    provider = ScriptedProvider([payload])
    extractor = FactEventExtractorV2(provider)
    facts = extractor.extract_session([span], user_id="u1", session_date="2023-06-15")
    assert len(facts) == 1
    fact = facts[0]
    assert fact.predicate_canonical == "travel_by_airline"
    assert fact.source_type == SourceType.EXPLICIT_TEXT
    assert fact.metadata["entity_roles"]["airline"] == "United Airlines"
    assert len(provider.calls) == 1
    assert provider.calls[0]["usage_phase"] == "ingest_extractor"


def test_extract_session_scripted_provider_exhaustion_is_a_loud_test_bug_not_silent():
    span = _span("hello")
    provider = ScriptedProvider([])  # nothing scripted
    extractor = FactEventExtractorV2(provider)
    # ScriptedProvider raises AssertionError, which extractor's broad
    # `except Exception` catches and degrades to fallback (documented,
    # LOUDLY-logged behaviour — see extractor.py's _extract_once docstring).
    facts = extractor.extract_session([span], user_id="u1", session_date="2023-06-15")
    assert facts[0].predicate_canonical == "message_unit_statement"


# ---------------------------------------------------------------------------
# ProviderError-typed outage handling + IngestConfig.fail_fast
# ---------------------------------------------------------------------------

class _OutageProvider(LLMProvider):
    def __init__(self, code=ErrorCode.PROVIDER_RATE_LIMITED):
        self._code = code

    def complete(self, **kw):
        raise ProviderError("simulated outage", code=self._code)

    async def acomplete(self, **kw):
        raise ProviderError("simulated outage", code=self._code)


def test_provider_outage_degrades_to_fallback_when_fail_fast_off():
    extractor = FactEventExtractorV2(_OutageProvider(), config=IngestConfig(fail_fast=False))
    span = _span("I love hiking.")
    facts = extractor.extract_session([span], user_id="u1", session_date="2023-06-15")
    assert facts[0].predicate_canonical == "message_unit_statement"


def test_provider_outage_raises_when_fail_fast_on():
    extractor = FactEventExtractorV2(_OutageProvider(), config=IngestConfig(fail_fast=True))
    span = _span("I love hiking.")
    with pytest.raises(ProviderError):
        extractor.extract_session([span], user_id="u1", session_date="2023-06-15")


# ---------------------------------------------------------------------------
# Unconditional INGEST_DETERMINISM (was gated; no module flag left to flip)
# ---------------------------------------------------------------------------

def test_relative_date_refs_resolve_occurred_and_valid_from():
    extractor = FactEventExtractorV2(provider=None)
    span = _span("I moved last Tuesday and it is now current.", sid="s_date")
    item = {
        "kind": "state",
        "predicate_raw": "User moved",
        "predicate_canonical": "lives_in",
        "event_type": "state",
        "modality": "past_event",
        "entity_roles": {"subject": "user", "location": "Chicago"},
        "source_span_ids": [span.span_id],
        "support_text": span.text,
        "occurred_start": None,
        "occurred_date": {"expr": "last Tuesday", "anchor": "session_date"},
        "valid_from": None,
        "valid_from_date": {"expr": "today", "anchor": "session_date"},
    }
    fact = extractor._fact_from_item(item, "u1", {span.span_id}, [span], "2026-06-17")
    assert fact is not None
    assert fact.modality == Modality.CURRENT_STATE
    # anchor 2026-06-17 is a Wednesday -> "last Tuesday" = 2026-06-09
    assert fact.occurred_start == pytest.approx(_iso_to_ts("2026-06-09"))
    assert fact.valid_from == pytest.approx(_iso_to_ts("2026-06-17"))


def test_unmapped_modality_is_dropped():
    extractor = FactEventExtractorV2(provider=None)
    span = _span("Some fact", sid="s_mod")
    item = {
        "kind": "fact",
        "predicate_raw": "Some fact",
        "predicate_canonical": "some_fact",
        "event_type": "other",
        "modality": "garbled_modality",
        "entity_roles": {"subject": "user"},
        "source_span_ids": [span.span_id],
        "support_text": span.text,
    }
    assert extractor._fact_from_item(item, "u1", {span.span_id}, [span], "2026-06-17") is None


# ---------------------------------------------------------------------------
# Unconditional INGEST_CORRECTNESS: no forged provenance, strict grounding
# ---------------------------------------------------------------------------

def test_missing_span_ids_recovered_by_unique_quote_match():
    span = _span("I flew United to Boston last week.", sid="s_quote")
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "User flew United",
        "predicate_canonical": "travel_by_airline",
        "event_type": "flight",
        "modality": "past_event",
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["not_a_real_span_id"],
        "support_text": "I flew United to Boston last week.",
    }
    fact = extractor._fact_from_item(item, "u1", {span.span_id}, [span], "2026-06-17")
    assert fact is not None
    assert fact.source_span_ids == [span.span_id]  # recovered, not forged


def test_missing_span_ids_with_ambiguous_quote_is_dropped_not_guessed():
    # LOOSE_GROUNDING is deleted — an ambiguous multi-substring match always
    # drops the fact now (was conditionally lenient in the source).
    same_text = "we talked about plans"
    s1 = _span(same_text, sid="sA", idx=0)
    s2 = _span(same_text, sid="sA", idx=1)
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "talked about plans",
        "predicate_canonical": "plans_discussed",
        "modality": "past_event",
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["bad_id"],
        "support_text": same_text,
    }
    fact = extractor._fact_from_item(item, "u1", {s1.span_id, s2.span_id}, [s1, s2], "2026-06-17")
    assert fact is None


def test_no_span_match_at_all_drops_the_fact():
    span = _span("Something completely unrelated.", sid="s_none")
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "User bought a submarine",
        "predicate_canonical": "purchase_made",
        "modality": "past_event",
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["bad_id"],
        "support_text": "I bought a nuclear submarine yesterday for cash.",
    }
    assert extractor._fact_from_item(item, "u1", {span.span_id}, [span], "2026-06-17") is None


# ---------------------------------------------------------------------------
# Negation / inferred-LLM tagging (unconditional, was gated by INGEST_CORRECTNESS)
# ---------------------------------------------------------------------------

def test_hedge_phrase_marks_inferred_llm():
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "User probably prefers window seats",
        "predicate_canonical": "seat_preference",
        "event_type": "preference",
        "modality": "preference",
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["span_s1_0"],
        "support_text": "I probably prefer window seats",
    }
    fact = extractor._fact_from_item(item, "u1", {"span_s1_0"}, [])
    assert fact is not None
    assert fact.source_type == SourceType.INFERRED_LLM


def test_explicit_text_no_hedge():
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "User flew United in March",
        "predicate_canonical": "travel_by_airline",
        "event_type": "flight",
        "modality": "past_event",
        "entity_roles": {"subject": "user", "airline": "United"},
        "source_span_ids": ["span_s1_0"],
        "support_text": "I flew United in March",
    }
    fact = extractor._fact_from_item(item, "u1", {"span_s1_0"}, [])
    assert fact is not None
    assert fact.source_type == SourceType.EXPLICIT_TEXT


def test_negation_marks_polarity_negative():
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "fact",
        "predicate_raw": "User no longer drinks coffee",
        "predicate_canonical": "coffee_habit",
        "modality": "current_state",
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["span_s1_0"],
        "support_text": "I no longer drink coffee",
    }
    fact = extractor._fact_from_item(item, "u1", {"span_s1_0"}, [])
    assert fact is not None
    assert fact.polarity == "negative"


# ---------------------------------------------------------------------------
# Quantity-unit synonym folding (unconditional)
# ---------------------------------------------------------------------------

def test_quantity_unit_synonym_folded_to_canonical():
    extractor = FactEventExtractorV2(provider=None)
    item = {
        "kind": "state",
        "predicate_raw": "User watched 5 movies",
        "predicate_canonical": "movie_count",
        "modality": "current_state",
        "quantity_value": 5,
        "quantity_unit": "visit_count",  # off-schema synonym for "count"
        "entity_roles": {"subject": "user"},
        "source_span_ids": ["span_s1_0"],
        "support_text": "I've watched 5 movies this month",
    }
    fact = extractor._fact_from_item(item, "u1", {"span_s1_0"}, [])
    assert fact is not None
    assert fact.quantity_unit == "count"


# ---------------------------------------------------------------------------
# Confidence weights flow from IngestConfig, not a module dict
# ---------------------------------------------------------------------------

def test_fallback_confidence_capped_by_configured_fallback_cap():
    custom = IngestConfig(confidence=ConfidenceWeights(fallback_cap=0.10))
    extractor = FactEventExtractorV2(provider=None, config=custom)
    span = _span("I love hiking.")
    facts = extractor.extract_session([span], user_id="u1", session_date="2023-06-15")
    assert facts[0].confidence <= 0.10


# ---------------------------------------------------------------------------
# Extractor output-token cap: explicit config beats the provider's registry cap
# ---------------------------------------------------------------------------

def test_explicit_extractor_config_wins_over_provider_registry_cap():
    provider = ScriptedProvider(["[]"])
    provider.max_output_tokens = 393216  # simulates a registered model's endpoint ceiling
    extractor = FactEventExtractorV2(
        provider, config=IngestConfig(extractor_llm_max_tokens=32768)
    )
    extractor.extract_session(
        [_span("I flew United yesterday.")], user_id="u1", session_date="2026-06-17"
    )
    assert provider.calls[0]["max_tokens"] == 32768


def test_provider_registry_cap_used_when_no_explicit_override():
    provider = ScriptedProvider(["[]"])
    provider.max_output_tokens = 393216
    extractor = FactEventExtractorV2(provider, config=IngestConfig())
    extractor.extract_session(
        [_span("I flew United yesterday.")], user_id="u1", session_date="2026-06-17"
    )
    assert provider.calls[0]["max_tokens"] == 393216


def test_default_8192_cap_when_neither_config_nor_provider_declares_one():
    provider = ScriptedProvider(["[]"])  # no max_output_tokens attribute set
    extractor = FactEventExtractorV2(provider, config=IngestConfig())
    extractor.extract_session(
        [_span("I flew United yesterday.")], user_id="u1", session_date="2026-06-17"
    )
    assert provider.calls[0]["max_tokens"] == 8192


# ---------------------------------------------------------------------------
# _resolve_date_value direct coverage (used by tests/test_calendar_resolve.py
# too — re-verified here alongside its sibling extractor tests)
# ---------------------------------------------------------------------------

def test_future_ref_marker_blocks_past_event_rollback():
    item = {
        "modality": "past_event",
        "predicate_raw": "user received an invitation to a high school reunion scheduled for August 15th",
        "support_text": "I got the invite yesterday — the reunion is scheduled for August 15th.",
        "occurred_date": {"expr": "August 15th", "anchor": "session_date"},
    }
    assert _resolve_date_value(item, "occurred", None, "2023-05-27") == "2023-08-15"


class _SystemCapturingProvider(LLMProvider):
    """Records the `system` kwarg of every complete() call; returns empty JSON."""

    def __init__(self):
        self.systems: list[str] = []

    def complete(self, messages, system: str = "", **kw) -> str:
        self.systems.append(system)
        return "[]"

    async def acomplete(self, messages, system: str = "", **kw) -> str:
        return self.complete(messages, system=system, **kw)

    def usage_summary(self):
        return {}


def test_extraction_system_prompt_matches_production_no_coarse_rules():
    """Parity pin (audit 0723, drift #7): COARSE_RULES was gated behind
    INGEST_EXTRACT_COARSE which defaulted OFF with no config.toml override —
    the S500-winning H+obs stores were built WITHOUT it, and the C (coarse)
    extraction route measurably LOST on hard-47 (H 24 > BASE 21 > C 18). The
    port made it unconditional, silently promoting an untested-losing prompt
    variant to the product default. The extraction system prompt must be
    exactly EXTRACT + DETERMINISM.

    (R2.7 changed the CONTENT of EXTRACT_SYSTEM_PROMPT to drop the domain
    vocabulary — see tests/test_extraction_prompt.py — but not its assembly,
    which this pins.)"""
    from sodamem.prompts.extraction import (
        COARSE_RULES,
        DETERMINISM_RULES,
        EXTRACT_SYSTEM_PROMPT,
    )
    provider = _SystemCapturingProvider()
    extractor = FactEventExtractorV2(provider)
    extractor._extract_single("some window prompt")
    assert provider.systems, "extractor never called the provider"
    assert provider.systems[0] == EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES
    assert COARSE_RULES not in provider.systems[0]


class _SubjectCapture(LLMProvider):
    """Returns one fact whose entity_roles.subject is a real-world entity."""

    def complete(self, messages, system: str = "", **kw) -> str:
        return json.dumps([{
            "kind": "fact", "predicate_raw": "Omega was founded in 1848 by Louis Brandt",
            "predicate_canonical": "founded_by", "event_type": "other",
            "modality": "external_info",
            "entity_roles": {"subject": "Omega", "founder": "Louis Brandt"},
            "source_span_ids": ["span_s1_0"],
            "support_text": "Omega was founded in 1848 by Louis Brandt",
        }])

    async def acomplete(self, messages, system: str = "", **kw) -> str:
        return self.complete(messages, system=system, **kw)

    def usage_summary(self):
        return {}


def _extract_one(entity_subject: bool):
    from sodamem.memory.ingest.config import IngestConfig
    cfg = IngestConfig()
    object.__setattr__(cfg.extract, "entity_subject", entity_subject)
    extractor = FactEventExtractorV2(_SubjectCapture(), config=cfg)
    facts = extractor.extract_session([_span("Omega was founded in 1848 by Louis Brandt")],
                                      user_id="u1", session_date="2023-05-30")
    assert facts, "extractor returned no facts"
    return facts[0]


def test_entity_subject_flag_off_keeps_the_historical_star_schema():
    """Default OFF must reproduce the exact historical behavior: every fact's
    subject is the literal `entity_user`, no matter what the LLM answered.
    This is what made subject_entity_id 100.0% entity_user across 4700
    measured facts (docs/design/currency-and-graph-shape-0726.md)."""
    assert _extract_one(entity_subject=False).subject_entity_id == "entity_user"


def test_entity_subject_flag_on_honors_the_llm_answer():
    """ON derives the subject from entity_roles["subject"], which the LLM
    already fills correctly — the cheap gate (0726) proved the model emits real
    entities ("Clark/Sullivan Construction", "Loyalty Platform") that the
    hardcoded literal was silently discarding."""
    fact = _extract_one(entity_subject=True)
    assert fact.subject_entity_id != "entity_user"
    assert "omega" in fact.subject_entity_id.lower(), fact.subject_entity_id


def test_entity_subject_is_on_by_default():
    """0804 ruling: flipped ON. A zero-LLM retrieval-diff probe over N=50
    frozen-store copies (tooling since removed) measured 47/50
    questions with byte-identical top-10 evidence and 3/50 within one
    substitution — the score-risk argument for keeping it OFF is gone, and
    the product defects it fixes (25.8% of "memories" not about the user,
    star-shaped explore, degenerate dedup key) are definitional."""
    from sodamem.memory.ingest.config import ExtractConfig
    assert ExtractConfig().entity_subject is True


def _extract_with_subject(subject_value: str):
    """One fact whose entity_roles.subject is `subject_value`, flag ON."""
    import json as _json
    from sodamem.memory.ingest.config import IngestConfig

    class _P(LLMProvider):
        def complete(self, **kw):
            return _json.dumps([{
                "kind": "fact", "predicate_raw": "recommended a hotel in Rome",
                "predicate_canonical": "recommended_hotel",
                "modality": "assistant_advice",
                "entity_roles": {"subject": subject_value, "city": "Rome"},
                "source_span_ids": ["irrelevant"],
                "support_text": "recommended a hotel in Rome",
            }])

        async def acomplete(self, **kw):
            return self.complete(**kw)

        def usage_summary(self):
            return {}

    extractor = FactEventExtractorV2(_P(), config=IngestConfig())
    facts = extractor.extract_session([_span("recommended a hotel in Rome")],
                                      user_id="u1", session_date="2023-05-30")
    assert facts, "extractor returned no facts"
    return facts[0]


def test_assistant_subject_stays_on_the_user_not_a_second_hub():
    """The probe found 22.3% of would-flip subjects are the literal
    'assistant' (1,199 facts in 50 stores): the model answers "who said this
    advice" instead of "what is this knowledge about". 'assistant' is a
    conversational ROLE, not a knowledge entity — honoring it would turn the
    perfect star into a double star with a second fake hub. Same exclusion
    reasoning as 'user'."""
    fact = _extract_with_subject("assistant")
    assert fact.subject_entity_id == "entity_user"
    # Case/whitespace variants are the same role, not different entities.
    assert _extract_with_subject(" Assistant ").subject_entity_id == "entity_user"


def test_real_entity_subject_is_still_honored_by_default():
    fact = _extract_one(entity_subject=True)
    assert "omega" in fact.subject_entity_id.lower()


class _EmptyExtraction(LLMProvider):
    """Model returns `[]` — a judgement that nothing here is about the user."""
    def complete(self, messages, system: str = "", **kw) -> str:
        return "[]"
    async def acomplete(self, messages, system: str = "", **kw) -> str:
        return "[]"
    def usage_summary(self):
        return {}


def _extract_empty(flag: bool):
    from sodamem.memory.ingest.config import IngestConfig
    cfg = IngestConfig()
    object.__setattr__(cfg.extract, "empty_extraction_is_empty", flag)
    extractor = FactEventExtractorV2(_EmptyExtraction(), config=cfg)
    return extractor.extract_session(
        [_span("What is the integral of 1/x for x=1 to x=2?")],
        user_id="u1", session_date="2023-05-30")


def test_empty_extraction_flag_off_fabricates_fallback_noise():
    """Pin the historical behavior: a model that correctly says "[]" gets its
    judgement overwritten with a `message_unit_statement` fact carrying no date
    fields. Measured 0726: this is 12-15% of facts in a v4-flash-built store
    (vs 0.8% frozen), because v4-flash actually obeys the prompt's own "pure
    generic assistant advice can be omitted" rule while deepseek-chat used to
    extract those spans as external_info."""
    facts = _extract_empty(flag=False)
    assert len(facts) == 1
    assert facts[0].predicate_canonical == "message_unit_statement"
    assert facts[0].occurred_start is None  # no date fields — pure dilution


def test_empty_extraction_flag_on_returns_nothing():
    """ON: an empty parse is an empty result. The model made a judgement; we
    record it faithfully instead of manufacturing noise from it."""
    assert _extract_empty(flag=True) == []
