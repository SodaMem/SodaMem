"""Guardian tests for sodamem.memory.dreaming: the D36
run_dream primitive, D35 dirty-bit consumption, and SodaMem.dream() wiring.

Covers the documented contract (empty-store no-op +
idempotent double-call), plus one end-to-end case exercising the actual
EntityProfileSynthesizer -> GraphMaintainer.maintain() -> Store round trip
that the two minimal tests above don't touch: a stale entity with a real fact
must come out with a PROFILE fact and a cleared dirty bit.
"""
from __future__ import annotations

from sodamem import SodaMem
from sodamem.memory.storage.store import open_store
from sodamem.models import FactEvent, FactKind, Modality, SourceType


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_dream_is_a_noop_on_empty_store(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    mem = SodaMem(store=store, embedder=_FakeEmbedder())
    mem.dream(user_id="u1")  # must not raise on an empty store


def test_dream_is_idempotent(tmp_path):
    store = open_store(tmp_path / "s2.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    mem = SodaMem(store=store, embedder=_FakeEmbedder())
    mem.dream(user_id="u1")
    mem.dream(user_id="u1")  # second call must also not raise


def test_dream_rebuilds_stale_entity_into_profile_fact_and_clears_dirty_bit(tmp_path):
    user_id = "u3"
    entity_id = "entity_alice"
    store = open_store(tmp_path / "s3.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())

    # Seed one real fact mentioning "Alice" (role "friend") + register her in
    # the L3 entity registry (surface form used for the profile's display
    # name), then mark her dirty — mirrors what IngestClient.ingest_session
    # does via GraphMaintainer._write_mentions_and_edges + mark_entity_stale,
    # done here directly against Store so this test doesn't need a full
    # extractor/LLM round trip (dreaming is zero-LLM; this fixture should be
    # too).
    store.register_entity(
        user_id=user_id, entity_id=entity_id, dedup_key="alice", surface_form="Alice",
    )
    fact = FactEvent(
        user_id=user_id,
        kind=FactKind.FACT,
        source_span_ids=[],
        subject_entity_id="entity_user",
        predicate_raw="Alice works at Acme",
        predicate_canonical="works_at",
        event_type="employment",
        modality=Modality.PAST_EVENT,
        source_type=SourceType.EXPLICIT_TEXT,
        metadata={"entity_roles": {"friend": "Alice"}},
    )
    store.upsert_fact_event(fact)
    store.mark_entity_stale(user_id, entity_id)
    assert store.count_stale_entities(user_id) == 1

    mem = SodaMem(store=store, embedder=_FakeEmbedder())
    mem.dream(user_id=user_id)

    # D35: the dirty bit is cleared once the entity is processed.
    assert store.count_stale_entities(user_id) == 0

    # A deterministic PROFILE fact was written and is reachable through the
    # same pinned get_facts_for_entity() the retrieval layer (Task 6) uses.
    facts = store.get_facts_for_entity(entity_id, user_id=user_id)
    profiles = [f for f in facts if f.kind == FactKind.PROFILE]
    assert len(profiles) == 1
    assert "Alice" in profiles[0].predicate_raw
    assert "works at Acme" in profiles[0].predicate_raw

    # Idempotent: re-running with nothing new marked stale changes nothing.
    mem.dream(user_id=user_id)
    facts_again = store.get_facts_for_entity(entity_id, user_id=user_id)
    assert len([f for f in facts_again if f.kind == FactKind.PROFILE]) == 1


def test_dream_threads_ingest_config_to_maintainer(tmp_path):
    """T7-review regression: a deployment-customized IngestConfig must govern
    dream-time edge maintenance too — otherwise write-time and dream-time
    edges silently carry different confidence systems."""
    from sodamem.memory.dreaming import run_dream
    from sodamem.memory.ingest.config import (
        ConfidenceWeights, EdgeConfidenceWeights, ExtractConfig,
        ExtractWindowConfig, IngestConfig, SummaryConfig,
    )

    user_id = "u4"
    entity_id = "entity_bob"
    store = open_store(tmp_path / "s4.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.register_entity(
        user_id=user_id, entity_id=entity_id, dedup_key="bob", surface_form="Bob",
    )
    fact = FactEvent(
        user_id=user_id,
        kind=FactKind.FACT,
        source_span_ids=[],
        subject_entity_id="entity_user",
        predicate_raw="Bob plays tennis",
        predicate_canonical="plays",
        event_type="hobby",
        modality=Modality.PAST_EVENT,
        source_type=SourceType.EXPLICIT_TEXT,
        metadata={"entity_roles": {"friend": "Bob"}},
    )
    store.upsert_fact_event(fact)
    store.mark_entity_stale(user_id, entity_id)

    custom = IngestConfig(
        window=ExtractWindowConfig(), extract=ExtractConfig(),
        confidence=ConfidenceWeights(),
        edge_confidence=EdgeConfidenceWeights(mentions=0.42),
        summary=SummaryConfig(),
    )
    run_dream(store, user_id, config=custom)

    edges = store.get_fact_edges(user_id)
    ev = [e for e in edges if str(getattr(e.edge_type, "value", e.edge_type)).lower().endswith("mentions")]
    assert ev, f"no mentions edges produced: {[str(e.edge_type) for e in edges]}"
    assert all(abs(e.confidence - 0.42) < 1e-9 for e in ev), (
        f"dream ignored custom edge weights: {[e.confidence for e in ev]}"
    )
