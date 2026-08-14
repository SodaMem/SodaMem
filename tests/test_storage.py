"""Tests for sodamem.memory.storage: open_store()/store_meta versioning (I6),
CRUD round-trips, the pinned get_facts_for_entity signature, the R6
persist_audit_bundle opt-in, and legacy (pre-SodaMem) database adoption.
"""
from __future__ import annotations

import sqlite3

import pytest

from sodamem.errors import StoreVersionError, TenancyError
from sodamem.memory.storage.maintenance_lock import (
    MaintenanceLockBusy,
    acquire_exclusive_maintenance_lock,
)
from sodamem.memory.storage.store import open_store
from sodamem.models import (
    EdgeType,
    FactEdge,
    FactEvent,
    FactKind,
    FactStatus,
    RawTurn,
    SourceSpan,
)


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


# ---------------------------------------------------------------------------
# I6: open_store() version contract
# ---------------------------------------------------------------------------

def test_open_store_creates_fresh_db(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert store.write_version("u1") == 0
    assert store.chroma_available in (True, False)  # must be a real bool, not None


def test_open_store_creates_a_missing_parent_directory(tmp_path):
    """`SodaMem.open("./data")` — the first line of the README quickstart —
    died with `FileNotFoundError: ./data/memory.db.maintenance.lock` on any
    machine that didn't already have a ./data. Both sqlite3.connect and the
    lock's os.open pass O_CREAT, which creates the FILE and says nothing
    about the directory holding it."""
    path = tmp_path / "does" / "not" / "exist" / "s.sqlite3"
    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert path.exists()
    assert store.write_version("u1") == 0


def test_open_store_does_not_disturb_an_existing_directory(tmp_path):
    """mkdir(exist_ok=True) on the way in must not touch a sibling that is
    already there — the reopen path runs through the same line."""
    sibling = tmp_path / "keep.txt"
    sibling.write_text("untouched")
    open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert sibling.read_text() == "untouched"


def test_reopening_same_prompts_does_not_raise(tmp_path):
    path = tmp_path / "s.sqlite3"
    open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    store2 = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())  # must not raise
    assert store2 is not None


def test_reopening_drifted_prompts_raises_store_version_error(tmp_path):
    path = tmp_path / "s.sqlite3"
    open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    with pytest.raises(StoreVersionError):
        open_store(path, prompts={"x": "DRIFTED"}, embedder=_FakeEmbedder())


def test_store_holds_shared_maintenance_lock_until_close(tmp_path):
    path = tmp_path / "s.sqlite3"
    store = open_store(
        path, prompts={"x": "y"}, embedder=_FakeEmbedder()
    )
    with pytest.raises(MaintenanceLockBusy):
        acquire_exclusive_maintenance_lock(path)

    store.close()
    exclusive = acquire_exclusive_maintenance_lock(path)
    exclusive.release()


def test_open_store_error_releases_shared_maintenance_lock(tmp_path):
    path = tmp_path / "s.sqlite3"
    store = open_store(
        path, prompts={"x": "y"}, embedder=_FakeEmbedder()
    )
    store.close()

    with pytest.raises(StoreVersionError):
        open_store(
            path,
            prompts={"x": "DRIFTED"},
            embedder=_FakeEmbedder(),
        )

    exclusive = acquire_exclusive_maintenance_lock(path)
    exclusive.release()


def test_store_close_failure_still_releases_shared_maintenance_lock(
    tmp_path, monkeypatch,
):
    path = tmp_path / "s.sqlite3"
    store = open_store(
        path, prompts={"x": "y"}, embedder=_FakeEmbedder()
    )
    store._close_chroma()

    def fail_close_chroma():
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(store, "_close_chroma", fail_close_chroma)
    with pytest.raises(RuntimeError, match="injected close failure"):
        store.close()

    exclusive = acquire_exclusive_maintenance_lock(path)
    exclusive.release()


def test_store_meta_row_written_on_fresh_store(tmp_path):
    path = tmp_path / "s.sqlite3"
    open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    conn = sqlite3.connect(str(path))
    rows = dict(conn.execute("SELECT key, value FROM store_meta").fetchall())
    conn.close()
    assert rows["schema_version"] == "1"
    assert "prompt_fingerprint" in rows
    assert "created_at" in rows


# ---------------------------------------------------------------------------
# Legacy (pre-SodaMem) database adoption
# ---------------------------------------------------------------------------

def _write_legacy_db(path) -> None:
    """A minimal stand-in for a database written by the pre-SodaMem
    the predecessor implementation: `graph_entities.canonical_name`
    (not yet renamed to `dedup_key`), and no `store_meta` table at all."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE graph_entities (
            entity_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            canonical_name TEXT DEFAULT '',
            entity_type TEXT DEFAULT 'concept',
            surface_forms TEXT DEFAULT '[]',
            mention_count INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, entity_id)
        );
        CREATE TABLE summary_syntheses (
            summary_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            source_fact_ids TEXT DEFAULT '[]',
            source_span_ids TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            dirty INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO graph_entities VALUES (?,?,?,?,?,?,?,?,?)",
        ("entity_acme", "u1", "acme_corp", "organization", "[]", 1, "", 0.0, 0.0),
    )
    conn.commit()
    conn.close()


def test_open_store_adopts_legacy_db_missing_store_meta(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    _write_legacy_db(path)

    store = open_store(path, prompts={"extract": "v1"}, embedder=_FakeEmbedder())

    # The renamed column is readable under its new name, with the old row's
    # data intact.
    entity = store.get_graph_entity("u1", "entity_acme")
    assert entity is not None
    assert entity.dedup_key == "acme_corp"

    # Columns/tables the legacy DB predates now exist and are queryable
    # (raw_turns didn't exist at all in the legacy fixture; source_spans
    # existed in real legacy DBs but without session_time/document_time).
    assert store.get_all_raw_turns("u1") == []
    assert store.get_all_source_spans("u1") == []
    assert store.get_all_fact_events("u1") == []

    conn = sqlite3.connect(str(path))
    meta = dict(conn.execute("SELECT key, value FROM store_meta").fetchall())
    conn.close()
    assert meta["schema_version"] == "1"
    store.close()

    # From this point on it behaves like any other versioned store: same
    # prompts reopen cleanly, drifted prompts raise.
    store2 = open_store(path, prompts={"extract": "v1"}, embedder=_FakeEmbedder())
    assert store2.write_version("u1") == 0
    store2.close()

    with pytest.raises(StoreVersionError):
        open_store(path, prompts={"extract": "v2-drifted"}, embedder=_FakeEmbedder())


def _stop_chroma_system(client) -> None:
    """Release a PersistentClient's System + drop it from chromadb's global
    identifier cache, so a later PersistentClient at the same path does a
    real cold load from disk instead of transparently sharing this one's
    live handles (mirrors Store._close_chroma, minus the try/except: a probe
    failing to tear down cleanly should fail loudly, not warn-and-continue)."""
    system = client._system
    system.stop()
    from chromadb.api.shared_system_client import SharedSystemClient

    identifier = SharedSystemClient._get_identifier_from_settings(system.settings)
    SharedSystemClient._identifier_to_system.pop(identifier, None)


def test_open_store_reuses_existing_chroma_dir_at_data_dir_chroma(tmp_path):
    """P0 regression: chroma_dir must be `path.parent / "chroma"` — byte-compatible
    with the source the predecessor implementation's `data_dir/chroma`
    layout — not `path.parent / f"{path.stem}_chroma"`. A frozen legacy store
    (memory.db + a sibling chroma/ directory holding real vectors) must be
    opened as-is; the old formula silently pointed at a new, empty
    `memory_chroma/` directory instead, orphaning every vector the store had
    ever indexed (the scenario the reviewer caught against a frozen store)."""
    chromadb = pytest.importorskip("chromadb")
    from chromadb.config import Settings

    from sodamem.memory.storage.store import _ChromaEmbeddingFunctionAdapter

    path = tmp_path / "memory.db"
    _write_legacy_db(path)

    # A pre-existing chroma/ dir, sibling to memory.db (the on-disk shape a
    # real frozen benchmark store has), seeded with one already-embedded
    # vector via the SAME adapter Store's _init_chroma uses — chromadb
    # rejects a get_or_create_collection() whose embedding_function.name()
    # disagrees with what's persisted in the collection's config, so the
    # seed must use the real adapter class, not an ad-hoc stand-in.
    chroma_dir = tmp_path / "chroma"
    ef = _ChromaEmbeddingFunctionAdapter(_FakeEmbedder())
    seed_client = chromadb.PersistentClient(
        path=str(chroma_dir), settings=Settings(anonymized_telemetry=False)
    )
    seed_coll = seed_client.get_or_create_collection(
        name="source_spans", metadata={"hnsw:space": "cosine"}, embedding_function=ef
    )
    seed_coll.add(
        ids=["span_1"], embeddings=[[0.1, 0.2, 0.3]], documents=["hello"],
        metadatas=[{"user_id": "u1"}],
    )
    assert seed_coll.count() == 1
    _stop_chroma_system(seed_client)  # force the Store below to cold-load from disk

    store = open_store(path, prompts={"extract": "v1"}, embedder=_FakeEmbedder())
    try:
        assert store.chroma_available is True
        # Connected to the EXISTING chroma/ dir and its one seeded vector —
        # not a freshly-created, empty collection.
        assert store._chroma_spans.count() == 1
    finally:
        store.close()

    # No `memory_chroma` (or any other `{stem}_chroma`) directory was
    # spun up alongside the real one.
    assert not (tmp_path / "memory_chroma").exists()


# ---------------------------------------------------------------------------
# CRUD round-trips
# ---------------------------------------------------------------------------

def test_upsert_and_get_raw_turn_roundtrip(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    turn = RawTurn(user_id="u1", role="user", content="I fly United a lot.")
    store.upsert_raw_turn(turn)
    fetched = store.get_raw_turn(turn.turn_id)
    assert fetched is not None
    assert fetched.content == "I fly United a lot."


def test_upsert_source_span_bumps_write_version(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert store.write_version("u1") == 0
    span = SourceSpan(user_id="u1", turn_id="t1", session_id="s1", role="user", text="hello world")
    store.upsert_source_span(span)
    assert store.write_version("u1") == 1
    fetched = store.get_source_span(span.span_id)
    assert fetched is not None
    assert fetched.text == "hello world"


def test_upsert_fact_event_and_get_facts_for_entity_pinned_signature(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    fact = FactEvent(
        user_id="u1",
        kind=FactKind.FACT,
        source_span_ids=[],
        metadata={"entity_roles": {"airline": "United Airlines"}},
    )
    store.upsert_fact_event(fact)
    entity_id = store._canonical_entity_id("United Airlines")
    # Pinned signature: (entity_id, *, user_id) -> list[FactEvent].
    facts = store.get_facts_for_entity(entity_id, user_id="u1")
    assert [f.fact_id for f in facts] == [fact.fact_id]


def test_persist_audit_bundle_default_off_is_a_noop(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.persist_audit_bundle("u1", "some query", {"a": 1})  # audit defaults False
    conn = sqlite3.connect(str(tmp_path / "s.sqlite3"))
    count = conn.execute("SELECT COUNT(*) FROM audit_bundles").fetchone()[0]
    conn.close()
    assert count == 0


def test_persist_audit_bundle_explicit_audit_true_writes(tmp_path):
    path = tmp_path / "s.sqlite3"
    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.persist_audit_bundle("u1", "some query", {"a": 1}, audit=True)
    conn = sqlite3.connect(str(path))
    count = conn.execute("SELECT COUNT(*) FROM audit_bundles").fetchone()[0]
    conn.close()
    assert count == 1


def test_persist_audit_bundle_prunes_deterministic_newest_tail(tmp_path, monkeypatch):
    path = tmp_path / "s.sqlite3"
    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    timestamps = iter((10.0, 20.0, 20.0))
    monkeypatch.setattr(
        "sodamem.memory.storage.store.time.time", lambda: next(timestamps)
    )
    for bundle_id in ("old", "new-a", "new-b"):
        store.persist_audit_bundle(
            "u1", bundle_id, {"bundle_id": bundle_id}, audit=True, retention_cap=2
        )
    rows = store._conn.execute(
        "SELECT bundle_id FROM audit_bundles ORDER BY created_at DESC, bundle_id DESC"
    ).fetchall()
    assert [row["bundle_id"] for row in rows] == ["new-b", "new-a"]


def test_persist_audit_bundle_prune_failure_rolls_back_insert(tmp_path, monkeypatch):
    store = open_store(
        tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder()
    )

    def fail_prune(*args, **kwargs):
        raise sqlite3.OperationalError("injected prune failure")

    monkeypatch.setattr(store, "_prune_audit_bundles", fail_prune)
    with pytest.raises(sqlite3.OperationalError):
        store.persist_audit_bundle(
            "u1", "query", {"bundle_id": "new"}, audit=True, retention_cap=2
        )
    assert store._conn.execute(
        "SELECT COUNT(*) FROM audit_bundles"
    ).fetchone()[0] == 0


def test_entity_rules_reachable_via_composition_not_inheritance(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert not hasattr(store, "upsert_relation_definition")  # not flattened onto Store
    store.entity_rules.upsert_relation_definition({"relation_key": "works_at"})
    got = store.entity_rules.get_relation_definition("works_at")
    assert got is not None
    assert got["relation_key"] == "works_at"


def test_chroma_adapter_query_and_document_embeddings_identical():
    """embed_query and __call__ must stay the same embedding space forever —
    if someone edits one and not the other, recall silently collapses."""
    from sodamem.memory.storage.store import _ChromaEmbeddingFunctionAdapter

    class _E:
        dim = 3
        name = "e"

        def embed(self, texts):
            return [[float(len(t)), 0.0, 1.0] for t in texts]

    a = _ChromaEmbeddingFunctionAdapter(_E())
    assert a.embed_query(["hello", "hi"]) == a(["hello", "hi"])


def test_open_store_reads_legacy_default_ef_chroma_collection(tmp_path):
    """T11 integration blocker: the predecessor implementation built chroma collections with
    chromadb's DEFAULT embedding function ('default'); sodamem passes a named
    adapter. chromadb 1.x refuses get_or_create when EF names differ, so every
    frozen store's vector route went dark (chroma_available flipped False).
    Our adapter wraps the SAME ONNXMiniLm chromadb uses by default, so the
    conflict is purely nominal — open_store must fall back and read the
    persisted collection with vectors intact."""
    import chromadb
    from chromadb.config import Settings

    data_dir = tmp_path / "legacy"
    data_dir.mkdir()
    # Build a 'default'-EF collection the way the predecessor implementation did (no explicit EF).
    client = chromadb.PersistentClient(
        path=str(data_dir / "chroma"), settings=Settings(anonymized_telemetry=False)
    )
    coll = client.get_or_create_collection(
        name="fact_events", metadata={"hnsw:space": "cosine"}
    )
    coll.add(ids=["f1"], documents=["Alice adopted a golden retriever"])
    del coll, client  # release the persistent client before reopening

    from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder
    from sodamem.memory.storage.store import open_store

    store = open_store(
        data_dir / "memory.db", prompts={"x": "y"}, embedder=OnnxMiniLmEmbedder()
    )
    assert store.chroma_available is True, "EF-name conflict left vector route dark"
    # the persisted vector must be reachable — not a silently-empty degraded read
    assert store._chroma_facts.count() == 1
    q = store._chroma_facts.query(query_texts=["golden retriever dog"], n_results=5)
    assert q["ids"][0] == ["f1"], "persisted default-EF vector not queryable"


# ---------------------------------------------------------------------------
# delete_fact_event: real delete + cascade (M1 R1.6 core delete gap).
#
# Written TDD-red-first: before this method existed, `Store` had zero delete
# capability at all (AttributeError on call) — the naive shape a first-pass
# implementation might take is "DELETE FROM fact_events WHERE fact_id=?" with
# no ownership check at all, which would let user B delete user A's fact by
# guessing/observing the fact_id (facts are content-addressed only by a uuid,
# never secret). The cross-user test below is the regression guard for
# exactly that: it must keep failing (TenancyError, fact intact) even if a
# future refactor "simplifies" the method back down to an unguarded DELETE.
# ---------------------------------------------------------------------------

def _make_fact(user_id: str, fact_id: str | None = None, **overrides) -> FactEvent:
    kwargs = dict(
        user_id=user_id,
        kind=FactKind.FACT,
        source_span_ids=[],
        predicate_raw="Alice adopted a golden retriever",
        predicate_canonical="pet_ownership",
        metadata={"entity_roles": {"subject": "user", "pet": "golden retriever"}},
    )
    kwargs.update(overrides)
    if fact_id is not None:
        kwargs["fact_id"] = fact_id
    return FactEvent(**kwargs)


# ---------------------------------------------------------------------------
# archive_fact_event: the DEFAULT delete (tombstone). Both the REST route and
# the MCP delete_memory tool land here, so these tests pin the one shared
# meaning of "delete" — physical erase is delete_fact_event, tested below.
# ---------------------------------------------------------------------------

def test_archive_fact_event_missing_id_is_a_noop(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    result = store.archive_fact_event("fact_does_not_exist", user_id="u1")
    assert result == {"deleted": False, "already_archived": False}
    store.close()


def test_archive_fact_event_tombstones_but_keeps_the_row(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_arch_1"))

    result = store.archive_fact_event("fact_arch_1", user_id="u1")
    assert result == {"deleted": True, "already_archived": False}

    fact = store.get_fact_event("fact_arch_1")
    assert fact is not None, "archive must NOT remove the row — provenance is the point"
    assert fact.status == FactStatus.ARCHIVED
    # Dropped from the active read path.
    assert [f.fact_id for f in store.get_all_fact_events("u1", active_only=True)] == []
    store.close()


def test_archive_fact_event_is_idempotent(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_arch_2"))

    assert store.archive_fact_event("fact_arch_2", user_id="u1")["already_archived"] is False
    second = store.archive_fact_event("fact_arch_2", user_id="u1")
    assert second == {"deleted": True, "already_archived": True}
    store.close()


def test_archive_fact_event_touches_only_the_status_column(tmp_path):
    """Regression guard for the read-modify-write shape this method used to
    have: loading the whole FactEvent, mutating status, and writing every
    column back means any concurrent write to this fact between the read and
    the write is clobbered by the stale in-memory copy. A single-column UPDATE
    cannot lose a neighbouring write, and this pins the observable half of
    that — nothing except `status` moves."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_cols"))

    before = dict(store._conn.execute(
        "SELECT * FROM fact_events WHERE fact_id=?", ("fact_cols",)).fetchone())
    store.archive_fact_event("fact_cols", user_id="u1")
    after = dict(store._conn.execute(
        "SELECT * FROM fact_events WHERE fact_id=?", ("fact_cols",)).fetchone())

    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"status"}, f"archive moved columns it had no business touching: {changed}"
    assert after["status"] == FactStatus.ARCHIVED.value
    store.close()


def test_archive_fact_event_does_not_re_embed(tmp_path):
    """Archiving flips one status byte; it must not push the document back
    through the embedding function. The old upsert-based shape did."""
    class _CountingEmbedder:
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            return [[0.0] for _ in texts]

    embedder = _CountingEmbedder()
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=embedder)
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_embed"))

    baseline = embedder.calls
    store.archive_fact_event("fact_embed", user_id="u1")
    assert embedder.calls == baseline, (
        f"archive re-embedded ({embedder.calls - baseline} extra embed call(s)) — "
        "it should be a metadata-only update"
    )
    store.close()


def test_archive_fact_event_bumps_write_version(tmp_path):
    """The UPDATE bypasses upsert_fact_event, which is where the bump used to
    come from for free — so the bump has to be done explicitly, and read-side
    caches keyed on write_version must not keep serving the archived fact."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_ver"))

    before = store.write_version("u1")
    store.archive_fact_event("fact_ver", user_id="u1")
    assert store.write_version("u1") > before

    # A no-op archive writes nothing, so it must not bump either.
    steady = store.write_version("u1")
    store.archive_fact_event("fact_ver", user_id="u1")
    assert store.write_version("u1") == steady
    store.close()


def test_archive_fact_event_does_not_lose_a_concurrent_write(tmp_path, monkeypatch):
    """The lost update the single-column UPDATE exists to close.

    Deterministic on purpose. Just racing two threads does NOT reproduce this
    — the window between the old implementation's read and its write is a few
    microseconds and 60 barrier-synchronised rounds sail straight past it, so
    a plain stress test passes against the buggy code and protects nothing.

    Instead the window is forced open: `get_fact_event` is slowed down. That is
    a fair discriminator rather than a rigged one, because it only affects an
    implementation that reads the fact through `get_fact_event` and writes it
    back — exactly the read-modify-write shape being outlawed. The UPDATE-based
    implementation never calls it, holds one lock across check and write, and
    so is untouched by the patch.

    Legal end state: `predicate_raw == "NEW"` (the concurrent write survives,
    archived or not). `"OLD"` means the archive resurrected its stale copy.
    """
    import threading
    import time

    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_race", predicate_raw="OLD"))

    real_get = store.get_fact_event
    reading = threading.Event()

    def slow_get(fact_id):
        fact = real_get(fact_id)
        reading.set()
        time.sleep(0.3)  # hold the stale copy while the writer commits
        return fact

    monkeypatch.setattr(store, "get_fact_event", slow_get)

    # A thread that dies raises into nothing and pytest never notices — which
    # would turn this into a test that passes because the archive never ran.
    # Capture and re-raise instead.
    thread_error = []

    def archiver():
        try:
            store.archive_fact_event("fact_race", user_id="u1")
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            thread_error.append(exc)

    t = threading.Thread(target=archiver)
    t.start()
    # If the implementation reads via get_fact_event, wait until it is holding
    # the stale copy. If it does not (the UPDATE shape), don't block the test.
    reading.wait(timeout=1.0)
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_race", predicate_raw="NEW"))
    t.join()
    if thread_error:
        raise AssertionError("the archiving thread died") from thread_error[0]

    assert real_get("fact_race").predicate_raw == "NEW", (
        "the archive wrote back a stale in-memory copy and clobbered a "
        "concurrent update — read-modify-write, not a single-column UPDATE"
    )
    store.close()


def test_archive_fact_event_rejects_cross_user_and_leaves_data_intact(tmp_path):
    """Same guard as delete_fact_event's: a fact_id is an opaque uuid, not a
    secret, so archiving must not be the softer door into another tenant."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("user1", fact_id="fact_arch_cross"))

    with pytest.raises(TenancyError):
        store.archive_fact_event("fact_arch_cross", user_id="user2")

    fact = store.get_fact_event("fact_arch_cross")
    assert fact.status == FactStatus.ACTIVE
    store.close()


def test_delete_fact_event_missing_id_is_a_noop_not_deleted(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    result = store.delete_fact_event("fact_does_not_exist", user_id="u1")
    assert result["deleted"] is False
    store.close()


def test_delete_fact_event_removes_row_and_cascades(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    fact = _make_fact("u1", fact_id="fact_del_1")
    store.upsert_fact_event(fact)
    assert store.get_fact_event("fact_del_1") is not None
    roles = store._conn.execute(
        "SELECT COUNT(*) FROM fact_entity_roles WHERE fact_id=?", ("fact_del_1",)
    ).fetchone()[0]
    assert roles > 0  # entity_roles cascade precondition: something to delete

    # Other fact, on both sides of an edge with the target fact.
    other = _make_fact("u1", fact_id="fact_del_other")
    store.upsert_fact_event(other)
    store.upsert_fact_edge(FactEdge(
        user_id="u1", src_fact_id="fact_del_1", dst_id="fact_del_other",
        edge_type=EdgeType.SUPPORTS,
    ))
    store.upsert_fact_edge(FactEdge(
        user_id="u1", src_fact_id="fact_del_other", dst_id="fact_del_1",
        edge_type=EdgeType.CONTRADICTS,
    ))
    assert len(store.get_fact_edges("u1", fact_id="fact_del_1")) == 1
    assert len(store.get_fact_edges_by_dst("u1", "fact_del_1")) == 1

    result = store.delete_fact_event("fact_del_1", user_id="u1")

    assert result["deleted"] is True
    assert result["cascaded"]["fact_events"] == 1
    assert result["cascaded"]["fact_entity_roles"] == roles
    assert result["cascaded"]["fact_edges"] == 2  # one as src, one as dst

    assert store.get_fact_event("fact_del_1") is None
    remaining_roles = store._conn.execute(
        "SELECT COUNT(*) FROM fact_entity_roles WHERE fact_id=?", ("fact_del_1",)
    ).fetchone()[0]
    assert remaining_roles == 0
    assert store.get_fact_edges("u1", fact_id="fact_del_1") == []
    assert store.get_fact_edges_by_dst("u1", "fact_del_1") == []
    # The untouched fact on the other side of the deleted edge survives.
    assert store.get_fact_event("fact_del_other") is not None
    store.close()


def test_delete_fact_event_rejects_cross_user_and_leaves_data_intact(tmp_path):
    """Regression guard: deleting someone else's fact_id must raise, not
    silently no-op AND must not delete. A naive unguarded
    `DELETE FROM fact_events WHERE fact_id=?` would let user2 delete user1's
    fact purely by knowing/guessing the id — this must never work."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    fact = _make_fact("user1", fact_id="fact_cross_user")
    store.upsert_fact_event(fact)

    with pytest.raises(TenancyError):
        store.delete_fact_event("fact_cross_user", user_id="user2")

    # Still there, untouched, owned by user1.
    survivor = store.get_fact_event("fact_cross_user")
    assert survivor is not None
    assert survivor.user_id == "user1"
    store.close()


def test_archive_syncs_chroma_status_metadata_without_re_embedding(tmp_path):
    """The vector index carries a `status` field in its metadata. Nothing
    queries on it today (retrieval/vector.py filters on user_id only), but an
    index that disagrees with its table is a trap for whoever first writes
    `where={"status": ...}`. Sync it — via update(), which rewrites metadata
    without re-running the embedding function."""
    chromadb = pytest.importorskip("chromadb")
    del chromadb
    from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder

    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=OnnxMiniLmEmbedder())
    if not store.chroma_available:
        pytest.skip("chroma unavailable in this environment")
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_arch_vec"))
    before = store._chroma_facts.get(ids=["fact_arch_vec"], include=["metadatas", "embeddings"])
    assert before["metadatas"][0]["status"] == FactStatus.ACTIVE.value

    store.archive_fact_event("fact_arch_vec", user_id="u1")

    after = store._chroma_facts.get(ids=["fact_arch_vec"], include=["metadatas", "embeddings"])
    assert after["metadatas"][0]["status"] == FactStatus.ARCHIVED.value
    # Vector untouched — metadata-only update, and the row is still indexed.
    assert after["ids"] == ["fact_arch_vec"]
    assert list(after["embeddings"][0]) == list(before["embeddings"][0])
    # Other metadata keys survived the wholesale replace.
    assert after["metadatas"][0]["user_id"] == "u1"
    assert after["metadatas"][0]["kind"] == before["metadatas"][0]["kind"]
    store.close()


def test_delete_fact_event_removes_chroma_vector_when_available(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    del chromadb
    from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder

    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=OnnxMiniLmEmbedder())
    if not store.chroma_available:
        pytest.skip("chroma unavailable in this environment")
    fact = _make_fact("u1", fact_id="fact_del_vec")
    store.upsert_fact_event(fact)
    assert store._chroma_facts.get(ids=["fact_del_vec"])["ids"] == ["fact_del_vec"]

    result = store.delete_fact_event("fact_del_vec", user_id="u1")

    assert result["cascaded"]["chroma_vectors"] == 1
    assert store._chroma_facts.get(ids=["fact_del_vec"])["ids"] == []
    store.close()


# ---------------------------------------------------------------------------
# Explicit supersession (PRD R1.5: PATCH = new version + SUPERSEDES edge,
# never an in-place rewrite).
#
# Deliberately NOT a reuse of GraphMaintainer's path. That one runs three
# heuristic gates — _is_update_like (needs an update-ish cue word or a
# quantity), _same_update_slot, _supersede_order_key — because at ingest time
# nobody has said whether two facts are versions of one thing; it has to
# guess. A PATCH caller has already said so. Routing an explicit request
# through a guesser means the API silently does nothing whenever the new text
# happens to lack a cue word, which is the worst of both designs.
#
# The separation outlived the flag that used to make it visible
# (`supersede_observe_only`, removed 0806): the ingest heuristic acts on its
# own initiative, this method honours a direct instruction, and they must not
# share a decision path.
# ---------------------------------------------------------------------------

def test_supersede_fact_event_closes_the_loser_and_writes_the_edge(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    loser = _make_fact("u1", fact_id="fact_old")
    winner = _make_fact("u1", fact_id="fact_new")
    winner.valid_from = 1_700_000_000.0
    store.upsert_fact_event(loser)
    store.upsert_fact_event(winner)

    result = store.supersede_fact_event("fact_old", "fact_new", user_id="u1")

    assert result["superseded"] is True
    stored = store.get_fact_event("fact_old")
    assert stored.status == FactStatus.SUPERSEDED
    assert stored.valid_until == 1_700_000_000.0
    assert stored.provenance["superseded_by_fact_id"] == "fact_new"
    edges = [e for e in store.get_fact_edges("u1", fact_id="fact_new")
             if e.edge_type == EdgeType.SUPERSEDES]
    assert len(edges) == 1 and edges[0].dst_id == "fact_old"
    store.close()


def test_supersede_fact_event_keeps_the_loser_readable(tmp_path):
    """ADD-only: the old version is closed, never removed. Provenance and the
    original text must survive — that is the whole difference from DELETE."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_old"))
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_new"))
    store.supersede_fact_event("fact_old", "fact_new", user_id="u1")
    assert store.get_fact_event("fact_old") is not None
    store.close()


def test_supersede_fact_event_rejects_cross_user(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_old"))
    store.upsert_fact_event(_make_fact("u2", fact_id="fact_new"))
    with pytest.raises(TenancyError):
        store.supersede_fact_event("fact_old", "fact_new", user_id="u1")
    with pytest.raises(TenancyError):
        store.supersede_fact_event("fact_old", "fact_new", user_id="u2")
    assert store.get_fact_event("fact_old").status == FactStatus.ACTIVE
    store.close()


def test_supersede_fact_event_missing_id_is_a_noop(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_new"))
    assert store.supersede_fact_event("nope", "fact_new", user_id="u1")["superseded"] is False
    store.close()


def test_supersede_fact_event_refuses_to_supersede_a_fact_by_itself(tmp_path):
    """A self-edge would make the fact its own predecessor and give currency
    derivation a cycle to walk."""
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.upsert_fact_event(_make_fact("u1", fact_id="fact_a"))
    with pytest.raises(ValueError):
        store.supersede_fact_event("fact_a", "fact_a", user_id="u1")
    store.close()


# ---------------------------------------------------------------------------
# R1.7 — swapping the embedder must be refused, not absorbed.
# ---------------------------------------------------------------------------

class _WideEmbedder:
    """Different model, different width."""
    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_reopening_with_a_different_embedder_is_refused(tmp_path):
    """The one bug in this store that corrupts data instead of failing a
    request: two models' vectors in one index, distances between them
    meaningless, retrieval quality rotting with nothing raised."""
    path = tmp_path / "s.sqlite3"
    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.close()
    with pytest.raises(StoreVersionError) as exc:
        open_store(path, prompts={"x": "y"}, embedder=_WideEmbedder())
    # The operator must be able to act on this without reading source.
    assert "embedder" in str(exc.value).lower()
    assert "re-embed" in str(exc.value).lower()


def test_reopening_with_the_same_embedder_is_fine(tmp_path):
    path = tmp_path / "s.sqlite3"
    open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder()).close()
    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    assert store is not None
    store.close()


def test_pre_r17_store_is_adopted_not_rejected(tmp_path):
    """A store built before this check existed has no fingerprint. Refusing it
    would break every existing deployment for a risk that may not be present —
    we cannot know retroactively which embedder built it. Adopt and log."""
    path = tmp_path / "s.sqlite3"
    open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder()).close()
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM store_meta WHERE key='embedder_fingerprint'")
    conn.commit()
    conn.close()

    store = open_store(path, prompts={"x": "y"}, embedder=_FakeEmbedder())
    store.close()
    conn = sqlite3.connect(str(path))
    row = conn.execute(
        "SELECT value FROM store_meta WHERE key='embedder_fingerprint'"
    ).fetchone()
    conn.close()
    assert row is not None, "adopted fingerprint was not persisted"
