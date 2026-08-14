"""Typed ingest configuration — replaces the env>toml>code call-time lookup
API (the predecessor implementation/config/__init__.py) with a one-time-parsed dataclass tree.
No field here is read at call time from os.environ; the composition root
(SodaMem.ingest() / a CLI entrypoint) parses env/toml once and constructs
this tree, per spec §6.1 point 4 ("not read from a global env at call time").

Field provenance: the design's skeleton (window/extract/confidence/top-level
flags) was verified against the predecessor implementation's `[ingest.*]`
table and `_helpers.py:527-538`'s `_CONF` dict. The R3 line-by-line audit of
extractor.py/maintainer.py (Task 5 Step 1) found five MORE call-time
`_cfg.get()`/`_cfg.env_int()` reads that were not yet in the skeleton —
`ConfidenceWeights.fallback_cap`, `IngestConfig.extractor_llm_max_tokens`,
`IngestConfig.extractor_llm_temperature`, `EdgeConfidenceWeights` (8 keys),
and `SummaryConfig` (2 keys) — added below per the same rule the skeleton's
own docstring states: a `_cfg.*` hit not yet in the dataclass tree gets a new
field, not a dropped knob. See this module's field docstrings for the
full source-line provenance of every field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ExtractWindowConfig:
    max_tokens: int = 1200   # GRAPH_V2_EXTRACT_WINDOW_MAX_TOKENS
    max_turns: int = 12      # GRAPH_V2_EXTRACT_WINDOW_MAX_TURNS
    max_chars: int = 0       # GRAPH_V2_EXTRACT_WINDOW_MAX_CHARS
    overlap: int = 0         # GRAPH_V2_EXTRACT_WINDOW_OVERLAP — historically undeclared
    # in config.toml's [ingest.extract_window] table. This typed field closes that gap by making the value
    # explicit regardless of whether config.toml declares it.


@dataclass(frozen=True)
class ConfidenceWeights:
    """Mirrors config.toml's [ingest.confidence] table 1:1 (verified against
    _helpers.py:527-538's _CONF dict — 11 keys — plus `fallback_cap`, found by
    the R3 audit at extractor.py:1218 (`_fallback_facts`'s degraded-path
    confidence ceiling), which was under `confidence.fallback_cap` in the
    same [ingest.confidence] table but not yet in this dataclass)."""
    has_source_trace: float = 0.30
    support_exact_match: float = 0.20
    support_inexact_match: float = 0.08
    field_completeness_weight: float = 0.20
    source_type_weight: float = 0.15
    alignment_weight: float = 0.10
    has_time_bonus: float = 0.03
    has_quantity_bonus: float = 0.02
    llm_self_confidence_weight: float = 0.05
    min: float = 0.05
    max: float = 0.98
    fallback_cap: float = 0.72


@dataclass(frozen=True)
class EdgeConfidenceWeights:
    """R3 audit finding (maintainer.py:40-47, `_EDGE_CONF`): 8 more call-time
    `_cfg.get("ingest", "edge_confidence.*", ...)` reads the design's skeleton
    didn't yet carry — the per-edge-type confidence GraphMaintainer stamps on
    every FactEdge it writes."""
    evidences: float = 0.98
    subject_object_of: float = 0.90
    mentions: float = 0.90
    mention_link: float = 0.91
    occurred_during: float = 0.86
    has_quantity: float = 0.86
    contradicts: float = 0.95
    supersedes: float = 0.90


@dataclass(frozen=True)
class SummaryConfig:
    """R3 audit finding (maintainer.py:49-50): SummarySynthesizer's session
    summary size caps, previously module-load-time `_cfg.get()` reads."""
    max_facts: int = 12
    max_chars: int = 1800


@dataclass(frozen=True)
class ExtractConfig:
    user_only: bool = False               # INGEST_USER_ONLY, pending A/B, non-loser
    # OFF was the historical behavior: subject_entity_id is the literal
    # "entity_user" for every fact, making the graph a perfect star (measured
    # 100.0% on 4700 facts) and flattening world knowledge ("Omega was founded
    # in 1848") onto the user. ON derives the subject from
    # entity_roles["subject"], which the LLM already fills correctly with no
    # prompt change (cheap gate 0726: 86 of 574 facts carried real entities —
    # "Clark/Sullivan Construction", "Loyalty Platform" — that the literal
    # threw away).
    #
    # Flipped ON 0804. The blocker had been "write-side changes need an S500
    # rebuild gate" ($22.74 + 22h, with the tfix store's -12 as precedent).
    # A zero-LLM retrieval-diff probe removed it (tooling since deleted; the
    # measurement stands): the LLM's subject answer is
    # already persisted in fact_entity_roles, so rewriting that one column on
    # frozen-store COPIES reproduces the flag exactly — measured 47/50
    # questions with byte-identical top-10 evidence, 3/50 within one
    # substitution, and BM25/chroma documents untouched by construction. The
    # fix is for the product ('what do you know about me' returning Omega's
    # founding year; explore_memory one hop from collapsing back to the hub;
    # a dedup key with an all-constant column), not for the score.
    # 'assistant' subjects stay on entity_user — a second fake hub is the same
    # defect with a different name (see extractor.py).
    entity_subject: bool = True
    # 0726, pending S500. OFF = historical behavior: an extraction that parsed
    # fine but yielded zero facts is treated as FAILURE and replaced with
    # `message_unit_statement` fallback noise. That conflates two different
    # things — "the model said []" (legitimately nothing about the user in a
    # generic-knowledge Q&A span) versus "the model emitted items we could not
    # use". Measured 0726: deepseek-chat used to extract those spans as
    # `external_info` facts; v4-flash correctly obeys the prompt's own "pure
    # generic assistant advice can be omitted" rule and returns [], which this
    # code then turns into 12-15% degraded noise (vs 0.8% on the frozen store).
    # 0727: default flipped False -> True after the cheap gate passed all three
    # checks (gold evidence intact 3/3, degraded rate 11.7-15.3% -> 0.6%, and
    # raw_turns/source_spans byte-identical so no text coverage was lost). OFF
    # ships fabricated `message_unit_statement` rows — no dates, generic
    # predicate, duplicate text — for every span the model correctly declined
    # to extract from. ON = an empty parse is an empty result, not a failure.
    empty_extraction_is_empty: bool = True
    # (audit 0723) coarse_domain_profile deleted: it was an unimplemented
    # placeholder whose presence excused an unconditional COARSE_RULES append —
    # a silent drift from production (INGEST_EXTRACT_COARSE defaulted OFF).
    # R2.7 resolved the question it was holding open by making the extraction
    # prompt domain-neutral outright, so there is nothing left to select.


@dataclass(frozen=True)
class IngestConfig:
    window: ExtractWindowConfig = field(default_factory=ExtractWindowConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    confidence: ConfidenceWeights = field(default_factory=ConfidenceWeights)
    edge_confidence: EdgeConfidenceWeights = field(default_factory=EdgeConfidenceWeights)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    backfill_session_time: bool = False    # GRAPH_V2_BACKFILL_SESSION_TIME, pending A/B
    fail_fast: bool = False                # GRAPH_V2_INGEST_FAIL_FAST — current source default
    # is False (outage logs ERROR but continues with degraded fallback); this is an
    # ops choice, not an ablation arm, but the FALSE default must not silently
    # change to True during port — verified against _helpers.py:79-89 source.
    extractor_llm_max_tokens: Optional[int] = None
    # GRAPH_V2_EXTRACTOR_LLM_MAX_TOKENS / MEMORY_EXTRACTOR_LLM_MAX_TOKENS override
    # (R3 audit finding, extractor.py:726 `_extract_cap()`). None (the source's
    # "not set" state) falls through to the provider's own max_output_tokens
    # (the registered model's endpoint ceiling), then to 8192 — see
    # FactEventExtractorV2._extract_cap().
    extractor_llm_temperature: float = 0.0
    # ingest.extractor_llm.temperature (R3 audit finding, extractor.py:744).
    #
    # NOTE: no fields for INGEST_CORRECTNESS / INGEST_DETERMINISM — both are
    # folded to unconditional True (winner-goes-flagless, established baseline).
    # NOTE: no fields for LOOSE_GROUNDING / INGEST_D2_PRECISION /
    # INGEST_SALIENCE_GATE / INGEST_EXTRACT_ENRICH / INGEST_CONSOLIDATE — R2.9
    # loser flags, deleted entirely along with their code paths (their
    # `_cfg.*` reads and `except Exception` guards were deleted with them —
    # see the field docstrings above for the accounting).
