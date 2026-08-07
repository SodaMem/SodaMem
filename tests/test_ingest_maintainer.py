"""Guardian tests for sodamem.memory.ingest.maintainer (EntityResolver,
GraphMaintainer, SummarySynthesizer). Zero network — pure storage/dedup/
supersession logic exercised against a real (tmp_path sqlite) Store.

Covers entity resolution, maintainer correction, modality/typed dedup,
stale auto-marking, graph edge traversal and summary dirty tracking, plus:
  - the T3 hand-off (`store.entity_rules.summaries_depending_on`/
    `replace_summary_dependencies`, not a flattened `Store` method);
  - the R3 audit fix to `_fact_role` (storage failure now propagates
    instead of silently defaulting to the permissive "user" role).
"""
from __future__ import annotations

import time

import pytest

from sodamem.memory.ingest.config import EdgeConfidenceWeights, IngestConfig, SummaryConfig
from sodamem.memory.ingest.maintainer import EntityResolver, GraphMaintainer, SummarySynthesizer
from sodamem.memory.storage.store import open_store
from sodamem.models import (
    EdgeType,
    FactEdge,
    FactEvent,
    FactKind,
    FactStatus,
    Modality,
    SourceType,
    SummarySynthesis,
)


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    yield s
    s.close()


# ---------------------------------------------------------------------------
# EntityResolver
# ---------------------------------------------------------------------------

class TestEntityResolver:
    def test_exact_and_normalized_alias(self, store):
        r = EntityResolver(store)
        eid, canon, _c, _m = r.resolve("u1", "United Airlines", "organization")
        assert r.resolve("u1", "united airlines")[0] == eid
        assert r.resolve("u1", "the United Airlines")[0] == eid

    def test_acronym_match(self, store):
        r = EntityResolver(store)
        eid = r.resolve("u1", "United Airlines", "organization")[0]
        rid, _canon, _conf, method = r.resolve("u1", "UA", "organization")
        assert rid == eid
        assert method == "acronym"

    def test_single_token_prefix_merge(self, store):
        r = EntityResolver(store)
        eid = r.resolve("u1", "United Airlines", "organization")[0]
        rid, _canon, _conf, method = r.resolve("u1", "United", "organization")
        assert rid == eid
        assert method == "prefix_contained"

    def test_no_false_merge_distinct_entities(self, store):
        r = EntityResolver(store)
        ny = r.resolve("u1", "New York", "place")[0]
        nyt = r.resolve("u1", "New York Times", "organization")[0]
        assert ny != nyt

    def test_user_subject_is_stable(self, store):
        r = EntityResolver(store)
        assert r.resolve("u1", "user")[0] == "entity_user"

    def test_registry_persists(self, store):
        r = EntityResolver(store)
        r.resolve("u1", "United Airlines", "organization")
        rows = store.get_registry_entities("u1")
        assert any(row["dedup_key"] == "united airlines" for row in rows)


# ---------------------------------------------------------------------------
# GraphMaintainer: correction/supersession/dedup
# ---------------------------------------------------------------------------

def _mutating_maintainer(store):
    """A plain GraphMaintainer.

    Kept as a named helper rather than inlined: until 0806 it had to opt into
    mutation through `supersede_observe_only=False`, because the default was
    observe-only. That flag is gone and supersession is unconditional, so the
    name now describes every maintainer — but the call sites read the same,
    which is what makes the diff that removed the flag reviewable."""
    return GraphMaintainer(store)


class TestGraphMaintainerCorrection:
    def test_correction_contradicts_prior(self, store):
        maintainer = _mutating_maintainer(store)
        base = FactEvent(
            user_id="u1",
            kind=FactKind.FACT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="favorite_airline",
            source_type=SourceType.EXPLICIT_TEXT,
            metadata={"entity_roles": {"subject": "user", "airline": "Delta"}},
        )
        maintainer.maintain(base)
        correction = FactEvent(
            user_id="u1",
            kind=FactKind.FACT,
            source_span_ids=["span_s1_1"],
            predicate_canonical="favorite_airline",
            source_type=SourceType.USER_CORRECTION,
            metadata={"entity_roles": {"subject": "user", "airline": "United"}},
        )
        _f, actions = maintainer.maintain(correction)
        assert any(a["action"] == "contradict" for a in actions)
        all_facts = store.get_all_fact_events("u1", active_only=False)
        contradicted = [f for f in all_facts if f.status == FactStatus.CONTRADICTED]
        assert len(contradicted) == 1
        edges = store.get_fact_edges("u1", edge_type=EdgeType.CONTRADICTS.value)
        assert len(edges) == 1

    def test_state_supersedes_prior(self, store):
        maintainer = _mutating_maintainer(store)
        old = FactEvent(
            user_id="u1",
            kind=FactKind.STATE,
            source_span_ids=["span_s1_0"],
            predicate_canonical="lives_in",
            valid_from=1000.0,
            metadata={"entity_roles": {"subject": "user", "location": "Boston"}},
        )
        maintainer.maintain(old)
        new = FactEvent(
            user_id="u1",
            kind=FactKind.STATE,
            source_span_ids=["span_s1_1"],
            predicate_canonical="lives_in",
            valid_from=2000.0,
            created_at=old.created_at + 10,
            metadata={"entity_roles": {"subject": "user", "location": "Seattle"}},
        )
        _f, actions = maintainer.maintain(new)
        assert any(a["action"] == "supersede" for a in actions)
        superseded = [
            f for f in store.get_all_fact_events("u1", active_only=False)
            if f.status == FactStatus.SUPERSEDED
        ]
        assert len(superseded) == 1

    def test_mentions_edge_written(self, store):
        maintainer = GraphMaintainer(store)
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="flew_with",
            event_type="flight",
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        maintainer.maintain(fact)
        mentions = store.get_fact_edges("u1", edge_type=EdgeType.MENTIONS.value)
        assert len(mentions) >= 1


class TestModalityDedup:
    def test_past_and_future_do_not_merge(self, store):
        """Same airline + same date but different modality must NOT be same_event_extend."""
        maintainer = GraphMaintainer(store)
        flew = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="travel_by_airline",
            event_type="flight",
            modality=Modality.PAST_EVENT,
            occurred_start=1741737600.0,
            object_entity_ids=["entity_united_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        plan = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_1"],
            predicate_canonical="travel_by_airline",
            event_type="flight",
            modality=Modality.FUTURE_PLAN,
            occurred_start=1741737600.0,
            object_entity_ids=["entity_united_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        maintainer.maintain(flew)
        _, actions = maintainer.maintain(plan)
        assert not any(a["action"] == "same_event_extend" for a in actions)
        active = store.get_all_fact_events("u1")
        assert len(active) == 2


class TestTypedDedup:
    def test_coexist_distinct_flight_events(self, store):
        """Two flights with different airlines and times must coexist, not merge."""
        maintainer = GraphMaintainer(store)
        march_flight = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="travel_by_airline",
            event_type="flight",
            occurred_start=1741737600.0,   # 2025-03-12
            object_entity_ids=["entity_united_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        april_flight = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_1"],
            predicate_canonical="travel_by_airline",
            event_type="flight",
            occurred_start=1744416000.0,   # 2025-04-12
            object_entity_ids=["entity_southwest_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "Southwest Airlines"}},
        )
        maintainer.maintain(march_flight)
        _, actions = maintainer.maintain(april_flight)
        assert not any(a["action"] in ("duplicate", "same_event_extend") for a in actions)
        active = store.get_all_fact_events("u1")
        assert len(active) == 2

    def test_same_event_extend_merges(self, store):
        """Two facts about the same flight event (same airline + overlapping time) should merge."""
        maintainer = GraphMaintainer(store)
        f1 = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="travel_by_airline",
            event_type="flight",
            occurred_start=1741737600.0,
            object_entity_ids=["entity_united_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        f2 = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_2"],  # different span — not re-extraction
            predicate_canonical="travel_by_airline",
            event_type="flight",
            occurred_start=1741737600.0,   # same day
            quantity_value=2.0,
            quantity_unit="flight_segment",
            object_entity_ids=["entity_united_airlines"],
            metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
        )
        maintainer.maintain(f1)
        _, actions = maintainer.maintain(f2)
        assert any(a["action"] == "same_event_extend" for a in actions)
        active = store.get_all_fact_events("u1")
        assert len(active) == 1  # merged into one fact


class TestStaleAutoMarking:
    def test_expired_state_excluded_from_active(self, store):
        past = time.time() - 3600
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.STATE,
            source_span_ids=["span_s1_0"],
            predicate_canonical="subscription_active",
            valid_from=past - 7200,
            valid_until=past,
            metadata={"entity_roles": {"subject": "user"}},
        )
        store.upsert_fact_event(fact)
        active = store.get_all_fact_events("u1", active_only=True)
        assert all(f.fact_id != fact.fact_id for f in active)

    def test_expired_event_not_excluded(self, store):
        """An EVENT fact with valid_until in the past must NOT be excluded — it is a historical record."""
        past = time.time() - 3600
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.EVENT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="travel_by_airline",
            valid_until=past,
            metadata={"entity_roles": {"subject": "user", "airline": "United"}},
        )
        store.upsert_fact_event(fact)
        active = store.get_all_fact_events("u1", active_only=True)
        assert any(f.fact_id == fact.fact_id for f in active)

    def test_mark_stale_expired_updates_status(self, store):
        past = time.time() - 3600
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.STATE,
            source_span_ids=["span_s1_0"],
            predicate_canonical="plan_active",
            valid_from=past - 7200,
            valid_until=past,
            metadata={"entity_roles": {"subject": "user"}},
        )
        store.upsert_fact_event(fact)
        changed = store.mark_stale_expired("u1")
        assert changed >= 1
        all_facts = store.get_all_fact_events("u1", active_only=False)
        stale = [f for f in all_facts if f.fact_id == fact.fact_id]
        assert stale and stale[0].status == FactStatus.STALE


class TestGraphEdgeTraversal:
    def test_get_fact_edges_by_dst(self, store):
        edge = FactEdge(
            edge_id="edge_test_1",
            user_id="u1",
            src_fact_id="fact_abc",
            dst_id="entity_united_airlines",
            edge_type=EdgeType.SUBJECT_OF,
        )
        store.upsert_fact_edge(edge)
        results = store.get_fact_edges_by_dst(
            "u1", "entity_united_airlines",
            edge_types=[EdgeType.SUBJECT_OF.value, EdgeType.OBJECT_OF.value],
        )
        assert any(e.edge_id == "edge_test_1" for e in results)


# ---------------------------------------------------------------------------
# SummarySynthesizer + T3 hand-off (store.entity_rules.* dependency index)
# ---------------------------------------------------------------------------

class TestSummaryDirtyTracking:
    def test_summary_marked_dirty_after_fact_write(self, store):
        """After maintain(), the session summary should be marked dirty via
        the T3 EntityRulesStore dependency index (store.entity_rules.*), not
        a flattened Store method."""
        summary = SummarySynthesis(
            summary_id="sum_test",
            user_id="u1",
            scope_type="session",
            scope_id="sess1",
            summary_text="test summary",
            dirty=False,
        )
        store.upsert_summary_synthesis(summary)
        maintainer = GraphMaintainer(store)
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.FACT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="test_predicate",
            provenance={"session_ids": ["sess1"], "turn_ids": ["t1"]},
            metadata={"entity_roles": {"subject": "user"}},
        )
        maintainer.maintain(fact)
        summaries = store.get_summary_syntheses("u1")
        target = next((s for s in summaries if s.summary_id == "sum_test"), None)
        assert target is not None and target.dirty is True

    def test_refresh_session_builds_dependency_index_via_entity_rules(self, store):
        synth = SummarySynthesizer(store)
        maintainer = GraphMaintainer(store)
        fact = FactEvent(
            user_id="u1",
            kind=FactKind.FACT,
            source_span_ids=["span_s1_0"],
            predicate_canonical="test_predicate",
            provenance={"session_ids": ["sess2"], "turn_ids": ["t1"]},
            metadata={"entity_roles": {"subject": "user"}},
        )
        maintainer.maintain(fact)
        summary = synth.refresh_session("u1", "sess2")
        assert summary is not None
        # The dependency index lives at store.entity_rules, not a flattened
        # Store method (T3's DR-004#6 split) — this is the T5 hand-off's
        # actual assertion: a later mark on the SAME fact_id must find this
        # summary via store.entity_rules.summaries_depending_on.
        found = store.entity_rules.summaries_depending_on("fact", fact.fact_id)
        assert summary.summary_id in found

    def test_refresh_session_with_no_facts_archives(self, store):
        synth = SummarySynthesizer(store)
        result = synth.refresh_session("u1", "empty_session")
        assert result is None

    def test_summary_size_caps_from_config(self, store):
        maintainer = GraphMaintainer(store)
        for i in range(5):
            fact = FactEvent(
                user_id="u1",
                kind=FactKind.FACT,
                source_span_ids=[f"span_s1_{i}"],
                predicate_canonical=f"fact_{i}",
                predicate_raw="x" * 500,
                provenance={"session_ids": ["sess3"], "turn_ids": [f"t{i}"]},
                metadata={"entity_roles": {"subject": "user"}},
            )
            maintainer.maintain(fact)
        cfg = IngestConfig(summary=SummaryConfig(max_facts=2, max_chars=50))
        synth = SummarySynthesizer(store, cfg)
        summary = synth.refresh_session("u1", "sess3")
        assert summary is not None
        assert len(summary.source_fact_ids) == 5  # all facts recorded as sources
        assert len(summary.summary_text) <= 50    # but text is capped


# ---------------------------------------------------------------------------
# R3 audit fix: _fact_role no longer swallows a storage failure into "user"
# ---------------------------------------------------------------------------

class _BrokenSpansStore:
    """Wraps a real Store but makes get_source_spans_by_ids blow up, to prove
    the failure now propagates instead of being silently caught."""

    def __init__(self, real_store):
        self._real = real_store

    def get_source_spans_by_ids(self, span_ids):
        raise RuntimeError("simulated storage failure")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_fact_role_storage_failure_propagates_not_silently_defaults(store):
    maintainer = GraphMaintainer(_BrokenSpansStore(store))
    fact = FactEvent(
        user_id="u1",
        kind=FactKind.FACT,
        source_span_ids=["span_nonexistent"],
        predicate_canonical="whatever",
        metadata={"entity_roles": {"subject": "user"}},
    )
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        maintainer._fact_role(fact)


# ---------------------------------------------------------------------------
# EdgeConfidenceWeights flow from IngestConfig into written edges
# ---------------------------------------------------------------------------

def test_edge_confidence_from_config_not_module_dict(store):
    custom = IngestConfig(edge_confidence=EdgeConfidenceWeights(mentions=0.42))
    maintainer = GraphMaintainer(store, custom)
    fact = FactEvent(
        user_id="u1",
        kind=FactKind.FACT,
        source_span_ids=["span_s1_0"],
        predicate_canonical="likes_airline",
        metadata={"entity_roles": {"subject": "user", "airline": "United Airlines"}},
    )
    maintainer.maintain(fact)
    mentions = store.get_fact_edges("u1", edge_type=EdgeType.MENTIONS.value)
    assert mentions and mentions[0].confidence == 0.42


class TestSupersessionSlotIdentity:
    """What ACTUALLY decides "same update slot" — corrected 0727.

    An earlier version of this class asserted that value-bearing predicates
    (`staying_on_kauai` vs `staying_on_oahu`) can never share a slot, and
    explained it as "predicate normalization fuses the attribute with its
    value, so two versions can never collide". **That explanation was wrong**,
    and the test passed for an unrelated reason (its fixtures left
    `event_type` empty).

    The truth is a THREE-stage gate, not a single key:
      1. `_is_update_like(incoming)` — no update marker (now/currently/actually/
         changed/moved/...) and no quantity => never even slot-compared;
      2. `_same_update_slot` — `same_predicate` OR (`same_type` AND matching
         non-value role signature). That second path is a COARSER key that
         already handles renamed predicates — exactly the key the 0726 probe
         went looking for, sitting in the code the whole time;
      3. time/authority ordering (`_supersede_order_key`, assistant-must-not-
         supersede-user).

    Corroborating evidence the original claim missed: `extraction_traces` in
    the 20 frozen stores holds 633 `supersede_observed` + 37
    `contradict_observed` records. The mechanism was never starved — those
    stores were built while `supersede_observe_only` was ON (the "obs" in
    "H+obs"), which computed the decision and declined to write it. That flag
    was removed 0806; supersession is unconditional now.
    """

    def test_generic_event_type_is_not_identity_evidence(self, store):
        """Different predicates + EMPTY event_type => no slot identity. An
        empty/`other` type shared by both sides is a catch-all, not evidence."""
        maintainer = GraphMaintainer(store)
        for pred, island in (("staying_on_kauai", "Kauai"), ("staying_on_oahu", "Oahu")):
            _f, actions = maintainer.maintain(FactEvent(
                user_id="u1", kind=FactKind.FACT, source_span_ids=[f"span_{island}"],
                predicate_canonical=pred, source_type=SourceType.EXPLICIT_TEXT,
                metadata={"entity_roles": {"subject": "user", "island": island}},
            ))
        assert not any(a["action"] in ("supersede", "contradict") for a in actions)
        assert not store.get_fact_edges("u1", edge_type=EdgeType.SUPERSEDES.value)

    def test_role_signature_plus_concrete_event_type_IS_identity(self, store):  # noqa: D401
        """The coarser path: same non-generic event_type + same non-value role
        names => same slot, EVEN THOUGH the predicates differ. This is the
        mechanism the 0726 doc claimed did not exist."""
        maintainer = _mutating_maintainer(store)
        first = FactEvent(
            user_id="u1", kind=FactKind.FACT, source_span_ids=["span_a"],
            predicate_canonical="staying_on_kauai", event_type="trip",
            source_type=SourceType.EXPLICIT_TEXT,
            metadata={"entity_roles": {"subject": "user", "island": "Kauai"}},
        )
        maintainer.maintain(first)
        second = FactEvent(
            user_id="u1", kind=FactKind.FACT, source_span_ids=["span_b"],
            predicate_canonical="staying_on_oahu", event_type="trip",
            # `_is_update_like` is the FIRST gate: an incoming fact that carries
            # no update marker (now/currently/actually/changed/moved/...) and no
            # quantity is never even slot-compared. Real "changed my mind" spans
            # carry such a marker; a bare restatement does not.
            predicate_raw="I'm now staying on Oahu instead",
            source_type=SourceType.EXPLICIT_TEXT,
            metadata={"entity_roles": {"subject": "user", "island": "Oahu"}},
        )
        _f, actions = maintainer.maintain(second)
        assert any(a["action"] in ("supersede", "contradict") for a in actions), actions

    def test_supersession_always_writes(self, store):
        """The inverse of the test this replaces.

        Until 0806 `supersede_observe_only` defaulted to observe-only, so this
        same input computed the decision and wrote nothing — 16,926
        `supersede_observed` traces sit in the frozen S500 stores with zero
        SUPERSEDES edges to show for them. HTTP and MCP never construct an
        IngestConfig, so no deployment could turn it off; the flag's only
        reachable setting was the one that did nothing. There is no config to
        pass here because there is no longer a way to ask for less."""
        maintainer = GraphMaintainer(store)
        common = dict(user_id="u1", kind=FactKind.FACT, event_type="trip",
                      source_type=SourceType.EXPLICIT_TEXT)
        # Both sides carry a time anchor: `valid_until` is copied from the
        # winner's own start, so a winner with no time can only close the
        # loser as "superseded, when unknown". Real ingest always has one.
        maintainer.maintain(FactEvent(
            source_span_ids=["span_a"], predicate_canonical="staying_on_kauai",
            valid_from=1000.0,
            metadata={"entity_roles": {"subject": "user", "island": "Kauai"}}, **common))
        _f, actions = maintainer.maintain(FactEvent(
            source_span_ids=["span_b"], predicate_canonical="staying_on_oahu",
            predicate_raw="I'm now staying on Oahu instead",
            valid_from=2000.0,
            metadata={"entity_roles": {"subject": "user", "island": "Oahu"}}, **common))

        assert any(a.get("action") in ("supersede", "contradict") for a in actions), actions
        # No `*_observed` action may ever be produced again: the vocabulary
        # survives only so /v1/events can still read stores written earlier.
        assert not any("observed" in str(a.get("action", "")) for a in actions), actions
        assert store.get_fact_edges("u1", edge_type=EdgeType.SUPERSEDES.value)
        superseded = [f for f in store.get_all_fact_events("u1", active_only=False)
                      if f.status == FactStatus.SUPERSEDED]
        assert len(superseded) == 1, superseded
        # The whole point of ADD-only: the loser stays readable, with a closing
        # timestamp, rather than being rewritten or removed.
        assert superseded[0].predicate_canonical == "staying_on_kauai"
        assert superseded[0].valid_until == 2000.0  # closed at the winner's start
