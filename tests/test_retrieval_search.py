import json
import sqlite3

import pytest

from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.client import IngestClient
from sodamem.memory.ingest.extractor import FactEventExtractorV2
from sodamem.memory.retrieval.search import search, SearchResult, Degradation, DegradationCode
from sodamem.memory.retrieval.config import RetrievalConfig, FusionConfig
from sodamem.memory.storage.store import open_store
from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT

_PROMPTS = {"extract": EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES}


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_retrieval_config_has_no_temporal_hard_filter_field():
    # R7: winner-goes-flagless — the field must not exist.
    assert not hasattr(FusionConfig(), "temporal_hard_filter_enabled")


def test_search_returns_search_result_envelope(tmp_path, monkeypatch):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    # search() on an empty store must not raise — it returns an envelope,
    # possibly with degraded entries, never a bare [] with no signal.
    result = search("anything", user_id="u1", store=store, config=RetrievalConfig())
    assert isinstance(result, SearchResult)
    assert isinstance(result.evidence, list)
    assert isinstance(result.degraded, list)


def test_search_route_defaults_to_fusion():
    assert RetrievalConfig().search_route == "fusion"


# Three `audit_bundles` tests lived here until 0806. They drove
# `RetrievalConfig(audit_enabled=True)`, which no product entry point could
# set — the retrieval-side persist branch and both config fields are gone.
# `Store.persist_audit_bundle` and the `audit_bundles` table stay: removing
# the table would mean bumping STORE_SCHEMA_VERSION, and every existing store
# would then refuse to open. An unread table costs nothing; a forced migration
# to delete it costs every user.


def test_search_wide_route_returns_envelope(tmp_path):
    store = open_store(tmp_path / "wide.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    result = search("anything", user_id="u1", store=store,
                     config=RetrievalConfig(search_route="wide"))
    assert isinstance(result, SearchResult)
    assert result.evidence == []


def test_search_as_of_not_yet_implemented_raises(tmp_path):
    import datetime
    store = open_store(tmp_path / "asof.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    with pytest.raises(NotImplementedError):
        search("anything", user_id="u1", store=store, as_of=datetime.datetime.now())


def test_search_degrades_typed_when_chroma_unavailable(tmp_path):
    # No chroma extra installed in this env is unlikely in dev, so force the
    # degradation path directly: a Store with no chroma_dir has
    # chroma_available False by construction (Task 3).
    from sodamem.memory.storage.store import Store
    from sodamem.memory.storage.schema import SCHEMA_DDL
    from sodamem.memory.storage.migrations import LEGACY_SENTINEL, run_migrations
    from sodamem.versioning import STORE_SCHEMA_VERSION

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_DDL)
    run_migrations(conn, from_version=LEGACY_SENTINEL, to_version=STORE_SCHEMA_VERSION)
    store = Store(conn=conn, embedder=_FakeEmbedder(), chroma_dir=None)
    assert store.chroma_available is False

    result = search("anything", user_id="u1", store=store, config=RetrievalConfig())
    assert isinstance(result, SearchResult)
    assert all(isinstance(d, Degradation) for d in result.degraded)
    assert any(d.code == DegradationCode.VECTOR_ROUTE_FAILED for d in result.degraded)


def test_bm25index_cache_reused_across_calls_on_same_store(tmp_path):
    from sodamem.memory.retrieval.bm25 import get_bm25_index
    store = open_store(tmp_path / "cache.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    idx1 = get_bm25_index(store)
    idx2 = get_bm25_index(store)
    assert idx1 is idx2


# ---------------------------------------------------------------------------
# End-to-end: ingest real facts (zero LLM network — ScriptedProvider), then
# confirm search() actually finds and correctly ranks them. The tests above
# only probe an empty store; this is the port's real correctness check —
# BM25, entity, and summary routes are all exercised (vector route is
# excluded here: this environment's installed chromadb needs a follow-up fix
# to `Store._ChromaEmbeddingFunctionAdapter` — flagged separately, out of
# this task's file scope — so vector calls always degrade typed rather than
# silently return [], which is itself the §6.7 behavior this task adds).
# ---------------------------------------------------------------------------

_FACTS = [
    {"kind": "fact", "predicate_raw": "User flew United to Boston", "predicate_canonical": "travel_by_airline",
     "event_type": "flight", "modality": "past_event", "occurred_start": "2023-06-10",
     "entity_roles": {"subject": "user", "airline": "United Airlines"},
     "source_span_ids": ["irrelevant"], "support_text": "I flew United to Boston last week."},
    {"kind": "fact", "predicate_raw": "User adopted a golden retriever puppy", "predicate_canonical": "owns_pet",
     "event_type": "pet", "modality": "past_event", "occurred_start": "2023-05-01",
     "entity_roles": {"subject": "user", "pet": "golden retriever"},
     "source_span_ids": ["irrelevant"], "support_text": "I adopted a golden retriever puppy."},
    {"kind": "fact", "predicate_raw": "User started a new job at Acme Corp", "predicate_canonical": "employed_by",
     "event_type": "employment", "modality": "past_event", "occurred_start": "2023-04-01",
     "entity_roles": {"subject": "user", "employer": "Acme Corp"},
     "source_span_ids": ["irrelevant"], "support_text": "I started a new job at Acme Corp."},
]


def _ingest_facts(store):
    provider = ScriptedProvider([json.dumps([f]) for f in _FACTS])
    extractor = FactEventExtractorV2(provider)
    client = IngestClient(store=store, extractor=extractor)
    for i, f in enumerate(_FACTS):
        client.ingest_session(
            [{"role": "user", "content": f["support_text"]}],
            user_id="u1", session_id=f"s{i}", session_time=f"2023/06/{15 + i} (Thu) 10:00",
        )


def test_fusion_route_finds_and_top_ranks_the_relevant_fact(tmp_path):
    store = open_store(tmp_path / "e2e_fusion.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_facts(store)
    result = search("United Boston flight", user_id="u1", store=store, config=RetrievalConfig())
    assert result.evidence, "expected at least one evidence item"
    assert result.evidence[0]["predicate_canonical"] == "travel_by_airline"
    assert result.routes.get("bm25_fact_direct", 0) >= 1
    assert result.routes.get("entity", 0) >= 1


def test_wide_route_finds_and_top_ranks_the_relevant_fact(tmp_path):
    store = open_store(tmp_path / "e2e_wide.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_facts(store)
    result = search("United Boston flight", user_id="u1", store=store,
                     config=RetrievalConfig(search_route="wide"))
    assert result.evidence, "expected at least one evidence item"
    assert result.evidence[0]["predicate_canonical"] == "travel_by_airline"


def test_search_limit_override_takes_precedence_over_config_default(tmp_path):
    """The tool layer computes a DYNAMIC per-call limit from top_k/offset
    (max(offset+top_k+20, top_k*4, 80)) and
    passed it straight into retrieve_wide_audit_bundle/
    retrieve_fusion_audit_bundle, both of which took an explicit per-call
    `limit` parameter. This module's search() had no equivalent hook before
    this test — only config.default_limit, fixed at construction — silently
    breaking MemoryTool's offset-based pagination. `limit=` now overrides
    default_limit for one call only; `limit=None` (unset) changes nothing."""
    store = open_store(tmp_path / "limit_override.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_facts(store)  # 3 distinct facts
    tiny_config = RetrievalConfig(default_limit=1, search_route="wide")

    capped = search("User", user_id="u1", store=store, config=tiny_config)
    assert len(capped.evidence) <= 1

    overridden = search("User", user_id="u1", store=store, config=tiny_config, limit=50)
    assert len(overridden.evidence) > len(capped.evidence), (
        "limit= override did not take precedence over config.default_limit"
    )

    # limit=None (the default) must be a true no-op — byte-identical to
    # calling search() with no limit kwarg at all.
    unset = search("User", user_id="u1", store=store, config=tiny_config, limit=None)
    assert len(unset.evidence) == len(capped.evidence)


def test_vector_route_alive_end_to_end(tmp_path):
    """Regression for the T6 probe finding: chromadb 1.5.9 embeds QUERIES via
    EmbeddingFunction.embed_query(input=...); an adapter exposing only
    __call__ makes every vector search fail and surface as
    VECTOR_ROUTE_FAILED. The vector route must contribute with ZERO
    degradations on a healthy store."""
    from sodamem.memory.retrieval import search
    from sodamem.memory.storage.store import open_store

    class _CountingEmbedder:
        dim = 4
        name = "counting"

        def embed(self, texts):
            return [[float(len(t) % 7), 1.0, 0.0, 0.5] for t in texts]

    store = open_store(
        tmp_path / "memory.db", prompts={"x": "y"}, embedder=_CountingEmbedder()
    )
    if not store.chroma_available:
        import pytest

        pytest.skip("chroma extra not installed")
    from sodamem.models import RawTurn

    store.upsert_raw_turn(RawTurn(
        user_id="u1", role="user",
        content="I adopted a golden retriever named Biscuit",
        session_id="s1", turn_id="s1_t0", timestamp=1_700_000_000.0,
    ))
    result = search(query="golden retriever Biscuit", user_id="u1", store=store)
    assert result.degraded == [], f"vector route degraded: {result.degraded}"


# ---------------------------------------------------------------------------
# Scope keys (PRD R1.2). Real ingest -> real search, both routes.
# ---------------------------------------------------------------------------

def _ingest_scoped(store, fact, *, session_id, agent_id="", run_id=""):
    client = IngestClient(store=store,
                          extractor=FactEventExtractorV2(ScriptedProvider([json.dumps([fact])])))
    client.ingest_session(
        [{"role": "user", "content": fact["support_text"]}],
        user_id="u1", session_id=session_id, session_time="2023/06/15 (Thu) 10:00",
        agent_id=agent_id, run_id=run_id,
    )


def _scoped_store(tmp_path, name):
    store = open_store(tmp_path / name, prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_scoped(store, _FACTS[0], session_id="s_a", agent_id="agent_a")
    _ingest_scoped(store, _FACTS[1], session_id="s_b", agent_id="agent_b")
    _ingest_scoped(store, _FACTS[2], session_id="s_none")   # untagged
    return store


def _predicates(result):
    return {e["predicate_canonical"] for e in result.evidence}


def test_unscoped_search_is_unchanged_by_the_feature(tmp_path):
    """The byte-identical guarantee: with no scope argument nothing is
    filtered, so adding scope keys cannot move a single existing result."""
    store = _scoped_store(tmp_path, "scope_none.sqlite3")
    result = search("", user_id="u1", store=store, config=RetrievalConfig(default_limit=50))
    assert len(result.evidence) == len(
        search("", user_id="u1", store=store, config=RetrievalConfig(default_limit=50),
               agent_id="", run_id="").evidence
    )


def test_agent_scope_excludes_another_agents_fact_but_keeps_untagged(tmp_path):
    """`agent_id` NARROWS, it does not partition: a fact contributed by
    another agent drops out, while a fact with no scope stamp stays — it
    belongs to the user, not to whoever happened to record it. Strict-AND
    (mem0's rule) would hide every pre-existing memory the moment a caller
    passes agent_id, which from outside is indistinguishable from data loss."""
    store = _scoped_store(tmp_path, "scope_agent.sqlite3")
    preds = _predicates(search("", user_id="u1", store=store,
                               config=RetrievalConfig(default_limit=50), agent_id="agent_a"))
    assert "travel_by_airline" in preds      # agent_a's own
    assert "employed_by" in preds            # untagged -> user-level, still visible
    assert "owns_pet" not in preds           # agent_b's, filtered out


def test_scope_filter_works_on_the_wide_route_too(tmp_path):
    """Both retrieval engines gate candidates in different modules; the filter
    lives at the single output point precisely so they cannot drift apart."""
    store = _scoped_store(tmp_path, "scope_wide.sqlite3")
    preds = _predicates(search("", user_id="u1", store=store,
                               config=RetrievalConfig(search_route="wide", default_limit=50),
                               agent_id="agent_a"))
    assert "owns_pet" not in preds
    assert "travel_by_airline" in preds


def test_run_scope_narrows_independently_of_agent(tmp_path):
    store = open_store(tmp_path / "scope_run.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_scoped(store, _FACTS[0], session_id="r1", run_id="run_1")
    _ingest_scoped(store, _FACTS[1], session_id="r2", run_id="run_2")
    preds = _predicates(search("", user_id="u1", store=store,
                               config=RetrievalConfig(default_limit=50), run_id="run_1"))
    assert "travel_by_airline" in preds and "owns_pet" not in preds


def test_scope_is_visible_on_the_evidence_card(tmp_path):
    """Exposed, not just filtered on — a caller must be able to SEE which
    agent contributed a fact without a second round-trip."""
    store = _scoped_store(tmp_path, "scope_card.sqlite3")
    result = search("United Boston flight", user_id="u1", store=store, config=RetrievalConfig())
    card = next(e for e in result.evidence if e["predicate_canonical"] == "travel_by_airline")
    assert card["scope"] == {"agent_id": "agent_a"}
