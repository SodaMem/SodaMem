"""Tests for sodamem.memory.ingest.config: the typed IngestConfig tree that
replaces call-time env>toml>code lookups (spec §6.1). Verbatim from
the config contract, plus coverage for the audit-discovered fields
(EdgeConfidenceWeights/SummaryConfig/extractor_llm_*/confidence.fallback_cap)
that were not yet in the design's original skeleton.
"""
from __future__ import annotations

from sodamem.memory.ingest.config import (
    ConfidenceWeights,
    EdgeConfidenceWeights,
    IngestConfig,
    SummaryConfig,
)


def test_ingest_config_defaults_match_source_baseline():
    cfg = IngestConfig()
    assert cfg.fail_fast is False
    # `supersede_observe_only` lived here until 0806. It defaulted to
    # observe-only to match the frozen S500 stores, which made supersession a
    # no-op for every deployment (HTTP/MCP never build an IngestConfig, so the
    # flag was unreachable). Supersession is unconditional now — there is no
    # field left to pin. See test_ingest_maintainer.py's
    # test_supersession_always_writes.
    assert cfg.extract.empty_extraction_is_empty is True
    assert cfg.backfill_session_time is False
    assert cfg.extract.user_only is False
    assert cfg.window.max_tokens == 1200
    assert cfg.window.max_turns == 12


def test_confidence_weights_sum_close_to_one():
    w = ConfidenceWeights()
    core = (w.has_source_trace + w.support_exact_match + w.field_completeness_weight
            + w.source_type_weight + w.alignment_weight)
    assert 0.8 <= core <= 1.0  # matches source comment "权重之和≈1.0（外加几个小 bonus）"


def test_no_dead_flag_fields_exist():
    cfg = IngestConfig()
    for dead_name in ("loose_grounding", "d2_precision", "salience_gate",
                       "extract_enrich", "consolidate", "correctness", "determinism"):
        assert not hasattr(cfg, dead_name)
        assert not hasattr(cfg.extract, dead_name)


# ---------------------------------------------------------------------------
# R3-audit-discovered fields (not in the design's original skeleton — added
# per the skeleton's own rule: a call-time `_cfg.*` hit not yet in the tree
# gets a new field, not a dropped knob).
# ---------------------------------------------------------------------------

def test_confidence_fallback_cap_matches_source():
    # extractor.py:1218's `_cfg.get("ingest", "confidence.fallback_cap", 0.72)`.
    assert ConfidenceWeights().fallback_cap == 0.72


def test_edge_confidence_weights_match_source():
    # maintainer.py:40-47's `_EDGE_CONF` dict.
    w = EdgeConfidenceWeights()
    assert w.evidences == 0.98
    assert w.subject_object_of == 0.90
    assert w.mentions == 0.90
    assert w.mention_link == 0.91
    assert w.occurred_during == 0.86
    assert w.has_quantity == 0.86
    assert w.contradicts == 0.95
    assert w.supersedes == 0.90


def test_summary_config_matches_source():
    # maintainer.py:49-50.
    s = SummaryConfig()
    assert s.max_facts == 12
    assert s.max_chars == 1800


def test_ingest_config_carries_edge_confidence_and_summary_and_extractor_llm_knobs():
    cfg = IngestConfig()
    assert isinstance(cfg.edge_confidence, EdgeConfidenceWeights)
    assert isinstance(cfg.summary, SummaryConfig)
    # extractor.py:726,744's two extractor_llm.* reads: None falls through to
    # the provider's own max_output_tokens, then 8192; temperature default 0.0.
    assert cfg.extractor_llm_max_tokens is None
    assert cfg.extractor_llm_temperature == 0.0


def test_ingest_config_is_frozen():
    cfg = IngestConfig()
    try:
        cfg.fail_fast = True  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "IngestConfig (and its sub-dataclasses) must be frozen"
