"""Store: the SQLite + chroma persistence layer.

Ported from the predecessor implementation's `StorageBackend` (2012L),
renamed to `Store` to match spec §6.1's `store: Store` type contract.

`open_store()` is the single entrypoint — it either provisions a fresh store
(writes store_meta) or validates an existing one against the running code's
schema_version + prompt fingerprint, raising `StoreVersionError` on any
mismatch (I6, no silent ALTER). A database written by the pre-SodaMem
the predecessor implementation (no `store_meta` table at all) is
*adopted*: its schema is migrated up to `STORE_SCHEMA_VERSION` and it is
stamped with store_meta for the first time, using the caller's current
prompts as its baseline fingerprint — there is no historical fingerprint to
compare a legacy store against, so adoption bootstraps one. From that point
on it behaves like any other store: a later open with drifted prompts raises.

Two behavioral fixes carried out during this port (spec §6.7 — no silent
degradation):
  - Schema/legacy migrations used to be `except Exception: pass`; they now
    raise `StoreVersionError` via `migrations.run_migrations` (see that
    module).
  - Chroma init failure used to leave `_chroma_client = None` with callers
    silently getting `[]` back from every vector-search method. `Store` now
    exposes `chroma_available` as a real, always-computed bool; the retrieval
    layer (Task 6) reads it to produce a typed `Degradation` instead of an
    empty result nobody can distinguish from "genuinely no matches."
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

from sodamem.embedding import Embedder
from sodamem.errors import ErrorCode, TenancyError
from sodamem.models import (
    EdgeType,
    EntityMention,
    ExtractionTrace,
    FactEdge,
    FactEvent,
    FactKind,
    FactStatus,
    GraphEntity,
    RawTurn,
    SourceSpan,
    SummarySynthesis,
    fact_search_document,
)
from sodamem.versioning import (
    STORE_SCHEMA_VERSION,
    assert_store_compatible,
    embedder_fingerprint,
    prompt_fingerprint,
)

from .entity_rules import EntityRulesStore
from .maintenance_lock import MaintenanceLock, acquire_shared_maintenance_lock
from .migrations import LEGACY_SENTINEL, run_migrations
from .schema import SCHEMA_DDL, store_meta_schema

logger = logging.getLogger(__name__)

__all__ = ["Embedder", "Store", "open_store", "store_meta_schema"]


def _load_json_fields(d: dict, fields: tuple[tuple[str, object], ...]) -> dict:
    """Decode JSON-encoded row columns in place; empty or invalid -> the default."""
    for key, default in fields:
        raw = d.get(key)
        if isinstance(raw, str):
            try:
                d[key] = json.loads(raw) if raw else default
            except json.JSONDecodeError:
                d[key] = default
    return d


# ---------------------------------------------------------------------------
# open_store() — the I6 entrypoint
# ---------------------------------------------------------------------------

def _has_store_meta_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='store_meta'"
    ).fetchone()
    return row is not None


def _read_store_meta(conn: sqlite3.Connection) -> dict:
    row_schema = conn.execute(
        "SELECT value FROM store_meta WHERE key = 'schema_version'"
    ).fetchone()
    row_fp = conn.execute(
        "SELECT value FROM store_meta WHERE key = 'prompt_fingerprint'"
    ).fetchone()
    row_emb = conn.execute(
        "SELECT value FROM store_meta WHERE key = 'embedder_fingerprint'"
    ).fetchone()
    return {
        "schema_version": int(row_schema[0]) if row_schema else None,
        "prompt_fingerprint": row_fp[0] if row_fp else None,
        # None for any store built before R1.7 — assert_store_compatible
        # treats absence as "cannot know", not as a mismatch.
        "embedder_fingerprint": row_emb[0] if row_emb else None,
    }


def _write_store_meta(conn: sqlite3.Connection, schema_version: int, prompt_fp: str,
                      embedder_fp: str | None = None) -> None:
    conn.execute("DELETE FROM store_meta")
    conn.executemany(
        "INSERT INTO store_meta(key, value) VALUES (?, ?)",
        [
            ("schema_version", str(schema_version)),
            ("prompt_fingerprint", prompt_fp),
            ("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        ] + ([("embedder_fingerprint", embedder_fp)] if embedder_fp else []),
    )
    conn.commit()


def _provision_schema(conn: sqlite3.Connection) -> None:
    """Bring `conn`'s schema up to STORE_SCHEMA_VERSION from either nothing (a
    brand-new file) or a pre-SodaMem the predecessor implementation schema (legacy adoption).
    Safe to run identically for both: every step is a checked no-op when its
    target already exists (CREATE TABLE IF NOT EXISTS; column presence via
    PRAGMA table_info in migrations.py rather than swallowing ALTER's
    "duplicate column" error)."""
    conn.executescript(SCHEMA_DDL)
    run_migrations(conn, from_version=LEGACY_SENTINEL, to_version=STORE_SCHEMA_VERSION)


def open_store(
    path: str | Path,
    *,
    prompts: dict[str, str],
    embedder: Embedder,
    raw_recall_enabled: bool = True,
    derived_currency: bool = False,
) -> "Store":
    """The single entrypoint for opening a persisted store.

    - New DB file, or an existing file with no `store_meta` table (a legacy
      pre-SodaMem database): provisions/migrates the schema and writes
      store_meta with the current schema_version + the fingerprint of
      `prompts`.
    - Existing, already-versioned DB file: reads store_meta and calls
      assert_store_compatible — any mismatch (schema drift OR prompt drift)
      raises StoreVersionError. A frozen benchmark store must never silently
      reuse a schema it wasn't built with, and a changed extraction prompt
      invalidates every store built under the old one (spec I6).
    """
    path = Path(path)
    # Create the containing directory, not just the file. Both sqlite3.connect
    # and the maintenance lock's os.open() pass O_CREAT, which creates a FILE
    # in an existing directory and does nothing about a missing one — so
    # `SodaMem.open("./data")` on a fresh checkout died with a FileNotFoundError
    # naming the lock file, the first line of the README's own quickstart.
    # "Open (or create) a store under data_dir" is the documented contract;
    # creating the directory that holds it is part of creating it.
    path.parent.mkdir(parents=True, exist_ok=True)
    maintenance_lock = acquire_shared_maintenance_lock(path)
    conn: sqlite3.Connection | None = None
    try:
        is_new = not path.exists()
        # check_same_thread=False: this Store may be handed across threads (a
        # long-lived server process, a benchmark worker pool); RLock-guarded
        # access below serializes writes, matching the source StorageBackend.
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        expected_fp = prompt_fingerprint(prompts)
        # Probing costs one embed of a short fixed string. Paid once per store
        # open, and only when an embedder was actually supplied — a
        # metadata-only caller must not be forced to load an ONNX session.
        expected_embedder_fp = embedder_fingerprint(embedder) if embedder is not None else None

        if is_new or not _has_store_meta_table(conn):
            _provision_schema(conn)
            _write_store_meta(conn, STORE_SCHEMA_VERSION, expected_fp,
                              embedder_fp=expected_embedder_fp)
        else:
            meta = _read_store_meta(conn)
            assert_store_compatible(
                meta,
                expected_schema=STORE_SCHEMA_VERSION,
                expected_prompt_fp=expected_fp,
                expected_embedder_fp=expected_embedder_fp,
            )
            if meta.get("embedder_fingerprint") is None and expected_embedder_fp:
                # Store predates R1.7. Adopt the current embedder's identity so
                # the NEXT swap is caught. This is not a claim that the store
                # was built with this embedder — it cannot be — which is why it
                # is logged rather than done silently.
                logger.info(
                    "store %s carries no embedder fingerprint (built before this "
                    "check existed); adopting the currently configured embedder "
                    "%s. If this store was built with a DIFFERENT embedder, its "
                    "vectors are already mixed and it should be re-embedded.",
                    path.name, expected_embedder_fp,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO store_meta(key, value) VALUES (?, ?)",
                    ("embedder_fingerprint", expected_embedder_fp),
                )
                conn.commit()
            # Additive tables reach existing stores too. Every statement in
            # SCHEMA_DDL is CREATE ... IF NOT EXISTS, so this adds what is
            # missing and touches nothing that is present — and it runs only
            # AFTER assert_store_compatible has approved the version, so a
            # future-schema store is still refused rather than altered.
            #
            # Without it, "new stores get the current schema, existing ones
            # get whatever they were born with" would mean a purely additive
            # table (session_scope, R1.2b) needed a STORE_SCHEMA_VERSION bump
            # — which makes every store on disk incompatible to add a table
            # that breaks nothing. That trade is backwards.
            conn.executescript(SCHEMA_DDL)

        chroma_dir = path.parent / "chroma"
        return Store(
            conn=conn,
            embedder=embedder,
            chroma_dir=chroma_dir,
            raw_recall_enabled=raw_recall_enabled,
            derived_currency=derived_currency,
            maintenance_lock=maintenance_lock,
        )
    except Exception:
        if conn is not None:
            conn.close()
        maintenance_lock.release()
        raise


class _ChromaEmbeddingFunctionAdapter:
    """Adapts `sodamem.embedding.Embedder` (`.embed(texts) -> vectors`) to
    chromadb's own `EmbeddingFunction` protocol (`__call__(input) ->
    vectors`), so ONE embedder instance backs all three collections — the
    fix for the "ONNX rebuilt every open" bug (see
    `sodamem.embedding.onnx_minilm` for the full story). `Store` never
    assumes its injected `embedder` happens to also satisfy chromadb's
    protocol; this is the only place that bridge is needed."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def __call__(self, input):  # noqa: A002 - name matches chromadb's protocol
        return self._embedder.embed(list(input))

    def embed_query(self, input):  # noqa: A002 - chromadb >=1.5 queries via this,
        # not __call__ (CollectionCommon.py:751/908). Same embedder, same vectors:
        # documents and queries MUST share one embedding space or recall silently
        # collapses. Missing this method = every vector query fails (T6 probe).
        return self._embedder.embed(list(input))

    @staticmethod
    def name() -> str:
        return "sodamem-embedder-adapter"

    @staticmethod
    def is_legacy() -> bool:
        # No get_config()/build_from_config() round-trip support — this
        # duck-typed adapter (not a subclass of chromadb's own
        # EmbeddingFunction ABC, so chromadb's own default is_legacy() isn't
        # inherited) is honestly "legacy" by chromadb's own definition of
        # the term, not a workaround.
        return True


class Store:
    """Dual storage: ChromaDB vectors plus SQLite graph v2 metadata.

    Constructed via `open_store()`, not directly, in normal use — the
    constructor is still a public part of the module's surface (tests build
    fixtures without a real file-backed store meta dance) but does none of
    the version-compatibility checking `open_store()` does.
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        embedder: Embedder,
        chroma_dir: str | Path | None = None,
        raw_recall_enabled: bool = True,
        derived_currency: bool = False,
        maintenance_lock: MaintenanceLock | None = None,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._embedder = embedder
        self._chroma_dir = Path(chroma_dir) if chroma_dir is not None else None
        # Constructor-time config (R2/R3 of the riding-along checklist): both
        # used to be resolved at CALL TIME via env>toml>code lookups
        # (`_derived_currency()` / `_raw_recall_enabled()` in the source).
        # Resolving once here, at construction, is what makes Store's read
        # methods pure functions of (store state, arguments) instead of also
        # depending on ambient global config — and drops the function-level
        # lazy import that existed only to dodge a storage<->graph_v2._helpers
        # import cycle, which no longer exists in this package layout.
        self._raw_recall_enabled = raw_recall_enabled
        self._derived_currency = derived_currency
        self._maintenance_lock = maintenance_lock
        self._write_version: dict[str, int] = {}

        # Best-effort chroma-write failure counters (§6.7 risk note): these
        # writes are best-effort BY DESIGN (a vector-index miss must not fail
        # the SQLite write that already committed), but "best effort" must
        # stay observable, not vanish into a log line nobody reads.
        self._raw_turn_embed_failures = 0
        self._source_span_embed_failures = 0
        self._fact_embed_failures = 0

        self._chroma_client = None
        self._chroma_spans = None
        self._chroma_facts = None
        self._chroma_raw_turns = None
        self._chroma_available = False
        self._init_chroma()

        # DR-011 EntityRules + DR-004 #6 Summary dependency index (R10):
        # composition, not inheritance — see entity_rules.py's docstring.
        self.entity_rules = EntityRulesStore(conn=self._conn, lock=self._lock)

    # ------------------------------------------------------------------
    # Chroma lifecycle
    # ------------------------------------------------------------------

    @property
    def chroma_available(self) -> bool:
        """True iff the three chroma collections initialized successfully.
        Task 6's search() reads this to decide whether the vector route
        degrades (typed Degradation) instead of silently returning []."""
        return self._chroma_available

    @property
    def chroma_write_failure_counts(self) -> dict[str, int]:
        """Best-effort chroma upsert failures since this Store was opened, by
        channel. Non-zero means vector search over that channel is silently
        missing rows despite the SQLite write having succeeded."""
        return {
            "raw_turns": self._raw_turn_embed_failures,
            "source_spans": self._source_span_embed_failures,
            "fact_events": self._fact_embed_failures,
        }

    def _init_chroma(self) -> None:
        if self._chroma_dir is None:
            self._chroma_available = False
            return
        try:
            import chromadb
            from chromadb.config import Settings

            chroma_path = str(self._chroma_dir)
            client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(anonymized_telemetry=False),
            )
            # ONE embedding function instance, shared across all three
            # collections — this line IS the I5-naming-violation fix (source
            # never passed embedding_function= at all).
            ef = _ChromaEmbeddingFunctionAdapter(self._embedder)
            self._chroma_client = client

            def _open(name: str):
                # New sodamem-built stores persist our named adapter → no
                # conflict, explicit EF (shared ONNX session) is used.
                # Legacy stores built by the predecessor implementation persisted chromadb's
                # DEFAULT EF ("default"); chromadb 1.x refuses get_or_create
                # when the provided EF name differs from the persisted one.
                # Our adapter wraps chromadb's OWN ONNXMiniLM_L6_V2 — the same
                # model the default EF uses — so the vectors are byte-identical
                # and the conflict is purely nominal. Fall back to opening
                # WITHOUT an explicit EF: chromadb reuses the persisted default
                # (same ONNX, same vectors, same scores). Trade-off: legacy
                # stores lose the shared-session speedup (latency only, never
                # correctness); new stores keep it.
                try:
                    return client.get_or_create_collection(
                        name=name, metadata={"hnsw:space": "cosine"},
                        embedding_function=ef,
                    )
                except ValueError as ef_conflict:
                    if "embedding function" not in str(ef_conflict).lower():
                        raise
                    logger.info(
                        "collection %r was built with chromadb's default EF; "
                        "opening it without the shared adapter (same ONNX model, "
                        "byte-identical vectors, no shared-session speedup)", name
                    )
                    return client.get_or_create_collection(
                        name=name, metadata={"hnsw:space": "cosine"},
                    )

            self._chroma_spans = _open("source_spans")
            self._chroma_facts = _open("fact_events")
            # Issue #75 §2.3: vector index over immutable raw turns.
            self._chroma_raw_turns = _open("raw_turns")
            self._chroma_available = True
            logger.info("ChromaDB initialized at %s", chroma_path)
        except ImportError as e:
            # chromadb is an optional dependency: its absence is an expected,
            # configured degraded mode (lexical-only). Keep it at warning.
            logger.warning("ChromaDB not installed (%s); vector search disabled.", e)
            self._chroma_client = None
            self._chroma_available = False
        except Exception as e:
            # Any *other* failure (resource exhaustion, corrupt index, etc.)
            # is a real fault. §6.7 fix: Store does not itself manufacture a
            # Degradation envelope (that's the retrieval layer's job) — it
            # just makes the failure state a real, always-current bool
            # instead of a None that reads identically to "never tried."
            logger.error(
                "ChromaDB init FAILED at %s (%s); vector search disabled. "
                "chroma_available is now False.",
                self._chroma_dir, e, exc_info=True,
            )
            self._chroma_client = None
            self._chroma_available = False

    def close(self) -> None:
        try:
            self._close_chroma()
        finally:
            try:
                self._conn.close()
            finally:
                maintenance_lock = self._maintenance_lock
                self._maintenance_lock = None
                if maintenance_lock is not None:
                    maintenance_lock.release()

    def _close_chroma(self) -> None:
        """Release the per-store ChromaDB system (FDs + mmap'd hnsw indices).

        SQLite's ``close()`` only releases its single connection; the chroma
        PersistentClient holds several extra file handles per store that leak
        unless its System is stopped. We also drop the System from chroma's
        global identifier cache so a later re-open of the same path rebuilds
        cleanly (re-opening a stopped, still-cached System would hand back
        dead handles).
        """
        client = self._chroma_client
        self._chroma_client = None
        self._chroma_spans = None
        self._chroma_facts = None
        self._chroma_raw_turns = None
        self._chroma_available = False
        if client is None:
            return
        try:
            system = client._system
            system.stop()
            from chromadb.api.shared_system_client import SharedSystemClient

            identifier = SharedSystemClient._get_identifier_from_settings(system.settings)
            SharedSystemClient._identifier_to_system.pop(identifier, None)
        except Exception:
            logger.warning("ChromaDB close failed for %s", self._chroma_dir, exc_info=True)

    # ------------------------------------------------------------------
    # Write-version counter (BM25 cache invalidation lives in Task 6's
    # BM25Index now; Store only exposes the monotonic counter it keys off).
    # ------------------------------------------------------------------

    def write_version(self, user_id: str) -> int:
        return self._write_version.get(user_id, 0)

    def _bump_write_version(self, user_id: str) -> None:
        self._write_version[user_id] = self._write_version.get(user_id, 0) + 1

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Session scope (R1.2b)
    # ------------------------------------------------------------------

    def set_session_scope(self, user_id: str, session_id: str,
                          scope: dict[str, str]) -> None:
        """Record the scope one ingest ran under.

        An empty/absent scope writes nothing: an unscoped ingest must leave a
        store byte-identical to what it produced before this table existed.
        """
        keys = {k: (scope or {}).get(k, "") or "" for k in
                ("agent_id", "run_id", "project_id")}
        if not any(keys.values()):
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO session_scope
                    (user_id, session_id, agent_id, run_id, project_id, created_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id, session_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    run_id=excluded.run_id,
                    project_id=excluded.project_id
                """,
                (user_id, session_id, keys["agent_id"], keys["run_id"],
                 keys["project_id"], time.time()),
            )
            self._conn.commit()

    def get_session_scopes(self, user_id: str) -> dict[str, dict[str, str]]:
        """`session_id -> {key: value}` for every scoped session of this user.

        Returned whole rather than queried per row: retrieval resolves scope
        for every candidate in a result set, and one indexed read beats N.
        Only non-empty values are included, so a lookup miss and an empty
        stamp are the same thing to the caller.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT session_id, agent_id, run_id, project_id "
                "FROM session_scope WHERE user_id=?",
                (user_id,),
            )
            rows = cur.fetchall()
        out: dict[str, dict[str, str]] = {}
        for session_id, agent_id, run_id, project_id in rows:
            stamp = {k: v for k, v in (("agent_id", agent_id), ("run_id", run_id),
                                       ("project_id", project_id)) if v}
            if stamp:
                out[session_id] = stamp
        return out

    # ------------------------------------------------------------------
    # Graph v2 CRUD: RawTurn, SourceSpan, FactEvent, AuditBundle
    # ------------------------------------------------------------------

    def upsert_raw_turn(self, turn: RawTurn) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO raw_turns (turn_id, user_id, session_id, role, content, timestamp)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    session_id=excluded.session_id,
                    role=excluded.role,
                    content=excluded.content,
                    timestamp=excluded.timestamp
                """,
                (
                    turn.turn_id,
                    turn.user_id,
                    turn.session_id,
                    turn.role,
                    turn.content,
                    turn.timestamp,
                ),
            )
            self._conn.commit()
        # Issue #75 §2.3: index the raw turn for fact-independent recall. Gated
        # so that when raw_recall is disabled this method stays byte-identical
        # to the ungated baseline (no chroma write, no version bump).
        if self._raw_recall_enabled:
            self._bump_write_version(turn.user_id)
            self._embed_raw_turn(turn)

    def _embed_raw_turn(self, turn: RawTurn) -> bool:
        """Upsert a single raw turn into the Chroma raw_turns collection.

        Idempotent (keyed by turn_id). Used by the gated ingest path and by
        the unconditional reindex_raw_turns backfill. Best-effort BY DESIGN:
        the SQLite row is already committed by the time this runs, so a
        vector-index write failure here must never fail the caller — but
        "best effort" stays observable via `chroma_write_failure_counts`
        instead of only a log line (spec §6.7 risk note). Returns True iff
        the row was actually written, so callers can report a faithful
        indexed count.
        """
        if self._chroma_raw_turns is None or not (turn.content or "").strip():
            return False
        try:
            self._chroma_raw_turns.upsert(
                ids=[turn.turn_id],
                documents=[turn.content],
                metadatas=[{
                    "user_id": turn.user_id,
                    "session_id": turn.session_id,
                    "role": turn.role,
                }],
            )
            return True
        except Exception as e:  # noqa: BLE001 - vector index is best-effort BY DESIGN
            logger.warning("ChromaDB RawTurn upsert failed: %s", e)
            self._raw_turn_embed_failures += 1
            return False

    def reindex_raw_turns(self, user_id: str | None = None) -> int:
        """Backfill the raw_turns vector index for stores ingested before
        raw_recall was enabled. Unconditional (ignores raw_recall_enabled):
        embeds every raw turn for the given user (or all users) into the
        Chroma raw_turns collection. Idempotent. Returns the number of turns
        ACTUALLY (re)indexed — turns skipped for empty content or dropped by
        a Chroma failure are not counted, so count < total signals a partial
        backfill.
        """
        if self._chroma_raw_turns is None:
            return 0
        if user_id is None:
            with self._lock:
                cur = self._conn.cursor()
                cur.execute("SELECT DISTINCT user_id FROM raw_turns")
                user_ids = [r[0] for r in cur.fetchall()]
        else:
            user_ids = [user_id]
        count = 0
        for uid in user_ids:
            for turn in self.get_all_raw_turns(uid):
                if self._embed_raw_turn(turn):
                    count += 1
        return count

    def upsert_source_span(self, span: SourceSpan) -> None:
        if not span.text_hash:
            span.text_hash = self._text_hash(span.text)
        if not span.char_end:
            span.char_end = len(span.text)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO source_spans
                    (span_id, user_id, session_id, turn_id, role, char_start, char_end,
                     text, text_hash, span_type, extractor_version, alignment_method,
                     alignment_confidence, created_at, session_time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(span_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    session_id=excluded.session_id,
                    turn_id=excluded.turn_id,
                    role=excluded.role,
                    char_start=excluded.char_start,
                    char_end=excluded.char_end,
                    text=excluded.text,
                    text_hash=excluded.text_hash,
                    span_type=excluded.span_type,
                    extractor_version=excluded.extractor_version,
                    alignment_method=excluded.alignment_method,
                    alignment_confidence=excluded.alignment_confidence,
                    session_time=excluded.session_time
                """,
                (
                    span.span_id,
                    span.user_id,
                    span.session_id,
                    span.turn_id,
                    span.role,
                    span.char_start,
                    span.char_end,
                    span.text,
                    span.text_hash,
                    span.span_type,
                    span.extractor_version,
                    span.alignment_method,
                    span.alignment_confidence,
                    span.created_at,
                    span.session_time,
                ),
            )
            self._conn.commit()
        self._bump_write_version(span.user_id)
        if self._chroma_spans is not None:
            try:
                self._chroma_spans.upsert(
                    ids=[span.span_id],
                    documents=[span.text],
                    metadatas=[{
                        "user_id": span.user_id,
                        "session_id": span.session_id,
                        "turn_id": span.turn_id,
                        "role": span.role,
                    }],
                )
            except Exception as e:  # noqa: BLE001 - vector index is best-effort BY DESIGN
                logger.warning("ChromaDB SourceSpan upsert failed: %s", e)
                self._source_span_embed_failures += 1

    def upsert_fact_event(self, fact: FactEvent) -> None:
        d = fact.to_dict()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO fact_events
                    (fact_id, user_id, kind, status, subject_entity_id,
                     predicate_raw, predicate_canonical, event_type, object_entity_ids,
                     modality, polarity, occurred_start, occurred_end, valid_from,
                     valid_until, document_time, quantity_value, quantity_unit, source_type,
                     source_span_ids, provenance, confidence, confidence_reason,
                     metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    kind=excluded.kind,
                    status=excluded.status,
                    subject_entity_id=excluded.subject_entity_id,
                    predicate_raw=excluded.predicate_raw,
                    predicate_canonical=excluded.predicate_canonical,
                    event_type=excluded.event_type,
                    object_entity_ids=excluded.object_entity_ids,
                    modality=excluded.modality,
                    polarity=excluded.polarity,
                    occurred_start=excluded.occurred_start,
                    occurred_end=excluded.occurred_end,
                    valid_from=excluded.valid_from,
                    valid_until=excluded.valid_until,
                    document_time=excluded.document_time,
                    quantity_value=excluded.quantity_value,
                    quantity_unit=excluded.quantity_unit,
                    source_type=excluded.source_type,
                    source_span_ids=excluded.source_span_ids,
                    provenance=excluded.provenance,
                    confidence=excluded.confidence,
                    confidence_reason=excluded.confidence_reason,
                    metadata=excluded.metadata
                """,
                (
                    d["fact_id"],
                    d["user_id"],
                    d["kind"],
                    d["status"],
                    d["subject_entity_id"],
                    d["predicate_raw"],
                    d["predicate_canonical"],
                    d["event_type"],
                    json.dumps(d["object_entity_ids"]),
                    d["modality"],
                    d["polarity"],
                    d["occurred_start"],
                    d["occurred_end"],
                    d["valid_from"],
                    d["valid_until"],
                    d["document_time"],
                    d["quantity_value"],
                    d["quantity_unit"],
                    d["source_type"],
                    json.dumps(d["source_span_ids"]),
                    json.dumps(d["provenance"]),
                    d["confidence"],
                    d["confidence_reason"],
                    json.dumps(d["metadata"]),
                    d["created_at"],
                ),
            )
            cur.execute("DELETE FROM fact_entity_roles WHERE fact_id=?", (fact.fact_id,))
            roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
            for role, value in roles.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    entity_name = str(item)
                    entity_id = self._canonical_entity_id(entity_name)
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO fact_entity_roles
                            (fact_id, user_id, role, entity_id, entity_name)
                        VALUES (?,?,?,?,?)
                        """,
                        (fact.fact_id, fact.user_id, role, entity_id, entity_name),
                    )
            self._conn.commit()
        self._bump_write_version(fact.user_id)
        if self._chroma_facts is not None:
            try:
                doc = fact_search_document(fact)
                self._chroma_facts.upsert(
                    ids=[fact.fact_id],
                    documents=[doc],
                    metadatas=[{
                        "user_id": fact.user_id,
                        "kind": d["kind"],
                        "event_type": d["event_type"],
                        "modality": d["modality"],
                        "status": d["status"],
                    }],
                )
            except Exception as e:  # noqa: BLE001 - vector index is best-effort BY DESIGN
                logger.warning("ChromaDB fact upsert failed: %s", e)
                self._fact_embed_failures += 1

    def archive_fact_event(self, fact_id: str, *, user_id: str) -> dict:
        """Tombstone one fact — the default meaning of "delete" everywhere in
        this system (REST `DELETE /v1/memories/{id}`, the MCP `delete_memory`
        tool, the SDK and the Console all land here).

        SodaMem is an add-only, evidence-grounded store: the answer loop cites
        source spans, and audit bundles reference fact_ids after the fact. So
        the ordinary delete sets `status='archived'`, which drops the fact out
        of retrieval (fusion/search gate on FactStatus.ACTIVE) while leaving
        the row and its provenance intact. Callers who genuinely need the bytes
        gone want `delete_fact_event` — a different operation, hence a
        different name, rather than the same verb meaning two things depending
        on which door you came in through.

        Ownership rules are deliberately identical to `delete_fact_event`'s: a
        `fact_id` owned by another `user_id` raises `TenancyError`; a
        `fact_id` that does not exist at all is a plain no-op returning
        `deleted=False`. Idempotent — archiving an already-archived fact
        succeeds with `already_archived=True` and writes nothing.

        The check and the write share ONE `self._lock` region, and the write is
        a single-column UPDATE — deliberately not read-modify-write through
        `upsert_fact_event`. Going through upsert would mean loading the whole
        FactEvent into Python, mutating one enum, and writing every column
        back: any concurrent `upsert_fact_event` landing on this fact between
        the read and the write would be silently clobbered by our stale copy.
        That is reachable — the server's JobRunner ingests on four threads —
        and it would lose exactly the kind of write (a supersession) that
        matters most. It also re-embedded the document through ChromaDB just to
        flip one status byte.

        NOTE: with `derived_currency` enabled, retrieval does not gate on
        stored status at all (see fusion.py's `derived_currency` branch), so an
        archived fact can still surface. That predates this method — it is the
        same exposure the MCP tool has always had — but it means archive is not
        a suppression guarantee under that config.
        """
        archived = FactStatus.ARCHIVED.value
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT user_id, status FROM fact_events WHERE fact_id=?", (fact_id,)
            )
            row = cur.fetchone()
            if row is None:
                return {"deleted": False, "already_archived": False}
            owner_id = row["user_id"]
            if owner_id != user_id:
                raise TenancyError(
                    f"fact_id {fact_id!r} belongs to a different user_id — "
                    "refusing cross-user archive",
                    code=ErrorCode.TENANCY_INVALID,
                    details={"fact_id": fact_id, "requested_user_id": user_id,
                             "owner_user_id": owner_id},
                )
            if row["status"] == archived:
                return {"deleted": True, "already_archived": True}
            cur.execute(
                "UPDATE fact_events SET status=? WHERE fact_id=? AND user_id=?",
                (archived, fact_id, user_id),
            )
            self._conn.commit()
        self._bump_write_version(user_id)
        self._sync_chroma_status(fact_id, archived)
        # Same accountability rule as `delete_fact_event`, and it belongs here
        # even more: archive is what "delete" MEANS on every public door
        # (REST, MCP, SDK, Console), so tracing only the purge path would
        # leave the ordinary case — the one users actually hit — with no
        # account of who removed a fact or when. Written after the commit so
        # a failed archive never leaves a lying event behind.
        try:
            self.append_extraction_trace(ExtractionTrace(
                user_id=user_id, stage="api", action="delete", status="ok",
                reason="archive (soft delete)",
                output_fact_ids=[fact_id],
                metadata={"archived": True},
            ))
        except Exception as e:  # noqa: BLE001 — the archive already committed;
            # losing its audit row must not un-archive the fact or raise to
            # the caller. Loud, not silent.
            logger.warning("archive trace not recorded for %s: %s", fact_id, e)
        return {"deleted": True, "already_archived": False}

    def _sync_chroma_status(self, fact_id: str, status: str) -> None:
        """Mirror a status change into the vector index's metadata.

        Best-effort and metadata-only, matching `delete_fact_event`'s chroma
        handling: the SQLite row is authoritative and already committed, so a
        chroma failure is logged rather than raised.

        Nothing queries on this field today — `retrieval/vector.py` filters
        only on `user_id`, and status eligibility is decided on the SQL-loaded
        `fact.status`. It is kept in sync anyway because an index that
        disagrees with its table is a trap for whoever first writes
        `where={"status": ...}` and gets quietly wrong results.

        `update()` (not `upsert()`) with no `documents` argument: chroma
        rewrites the metadata without re-running the embedding function, which
        is the entire point of not going through `upsert_fact_event` here.
        Chroma replaces the metadata dict wholesale rather than merging, so the
        existing one is read back first and patched.
        """
        if self._chroma_facts is None:
            return
        try:
            existing = self._chroma_facts.get(ids=[fact_id], include=["metadatas"])
            metadatas = existing.get("metadatas") or []
            if not metadatas:
                return  # never indexed (chroma write failed earlier) — nothing to sync
            metadata = dict(metadatas[0] or {})
            metadata["status"] = status
            self._chroma_facts.update(ids=[fact_id], metadatas=[metadata])
        except Exception as e:  # noqa: BLE001 - vector index is best-effort BY DESIGN
            logger.warning("ChromaDB status sync failed for %s: %s", fact_id, e)

    def supersede_fact_event(self, loser_id: str, winner_id: str, *, user_id: str) -> dict:
        """Explicitly record that `winner_id` supersedes `loser_id` (PRD R1.5,
        the PATCH /v1/memories/{id} primitive).

        ADD-only, so this is the opposite of `delete_fact_event`: the old
        version stays readable, keeps its text and provenance, and is merely
        CLOSED — `status=superseded`, `valid_until` set to when the new
        version starts being true, plus a SUPERSEDES edge.

        Not routed through `GraphMaintainer`: that path exists to GUESS
        whether two facts are versions of one thing (`_is_update_like` wants
        an update-ish cue word or a quantity, `_same_update_slot` wants a
        matching slot). A PATCH caller has already declared the relationship,
        so re-deriving it would make the endpoint silently do nothing whenever
        the replacement text happens to read like a plain statement. The
        distinction outlived the flag that used to make it visible: the ingest
        heuristic acts on its own initiative, this method honours a direct
        instruction, and the two must not share a decision path.

        Ownership is checked on BOTH facts before anything is written — same
        reasoning as `delete_fact_event`: a `fact_id` is an opaque uuid, not a
        secret. A missing `loser_id` is a plain no-op (nothing to close); a
        missing `winner_id` is an error, because closing a fact and pointing
        it at nothing would strand it with no current version.
        """
        if loser_id == winner_id:
            raise ValueError(
                f"fact_id {loser_id!r} cannot supersede itself — a self-edge "
                "makes the fact its own predecessor and gives currency "
                "derivation a cycle to walk"
            )
        loser = self.get_fact_event(loser_id)
        if loser is None:
            return {"superseded": False, "loser_id": loser_id, "winner_id": winner_id}
        winner = self.get_fact_event(winner_id)
        if winner is None:
            raise ValueError(
                f"winner fact_id {winner_id!r} does not exist — refusing to "
                "close a fact with no successor to point at"
            )
        for fact in (loser, winner):
            if fact.user_id != user_id:
                raise TenancyError(
                    f"fact_id {fact.fact_id!r} belongs to a different user_id — "
                    "refusing cross-user supersede",
                    code=ErrorCode.TENANCY_INVALID,
                )

        loser.status = FactStatus.SUPERSEDED
        # Same rule the ingest path uses (`GraphMaintainer._mark_superseded`):
        # the old value stopped being true when the new one started.
        loser.valid_until = winner.valid_from or winner.occurred_start
        if isinstance(loser.provenance, dict):
            loser.provenance.setdefault("superseded_by_fact_id", winner.fact_id)
        self.upsert_fact_event(loser)
        self.upsert_fact_edge(FactEdge(
            edge_id=self._text_hash(f"fact_edge:{winner.fact_id}:{loser.fact_id}:SUPERSEDES"),
            user_id=user_id,
            src_fact_id=winner.fact_id,
            dst_id=loser.fact_id,
            edge_type=EdgeType.SUPERSEDES,
            predicate_raw="explicit update supersedes prior version",
            predicate_canonical="supersedes",
            confidence=1.0,
            metadata={
                "reason": "explicit client update (PATCH)",
                "current_head_fact_id": winner.fact_id,
            },
        ))
        return {"superseded": True, "loser_id": loser_id, "winner_id": winner_id,
                "valid_until": loser.valid_until}

    def delete_fact_event(self, fact_id: str, *, user_id: str) -> dict:
        """Physical, cascading purge of one fact — NOT the default delete.

        `archive_fact_event` is what `DELETE /v1/memories/{id}` and the MCP
        `delete_memory` tool call. This method is the irreversible erase behind
        an explicit operator opt-in (`?purge=true`, gated by the
        `SODAMEM_ALLOW_PURGE` setting), for right-to-erasure requests where a
        tombstone is not enough.

        Ownership is checked BEFORE any row is touched: a `fact_id` that
        belongs to a different `user_id` raises `TenancyError` rather than
        silently no-op'ing OR (the dangerous naive shape) deleting anyway —
        `fact_id` is an opaque uuid, not a secret, so an unguarded
        `DELETE ... WHERE fact_id=?` would let any caller who learns/guesses
        another user's fact_id delete it. A `fact_id` that does not exist at
        all (already deleted, or never existed) is a plain no-op — that is
        not a tenancy violation, just nothing to do.

        Cascades to every table that references this fact_id: `fact_events`
        itself, `fact_entity_roles`, and `fact_edges` on BOTH sides
        (`src_fact_id` — this fact's own outgoing edges — and `dst_id`,
        since edges can target a fact as their destination too, e.g.
        SUPERSEDES/CONTRADICTS; see `idx_fact_edges_target`'s reverse-
        traversal comment in schema.py). The chroma vector delete is
        best-effort, matching `upsert_fact_event`'s own best-effort chroma
        write: the SQLite delete already committed, so a chroma failure is
        logged, not raised — a stale orphaned vector is a index-freshness
        bug, not a data-integrity one.

        Returns a dict: `deleted` (bool) and `cascaded` (rows removed per
        table, `chroma_vectors` counted as 0 or 1 since a fact has at most
        one embedded document in the `fact_events` collection).
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT user_id FROM fact_events WHERE fact_id=?", (fact_id,))
            row = cur.fetchone()
            if row is None:
                return {"deleted": False, "cascaded": {
                    "fact_events": 0, "fact_entity_roles": 0, "fact_edges": 0,
                    "chroma_vectors": 0,
                }}
            owner_id = row["user_id"]
            if owner_id != user_id:
                raise TenancyError(
                    f"fact_id {fact_id!r} belongs to a different user_id — "
                    "refusing cross-user delete",
                    code=ErrorCode.TENANCY_INVALID,
                    details={"fact_id": fact_id, "requested_user_id": user_id,
                             "owner_user_id": owner_id},
                )
            cascaded = {"chroma_vectors": 0}
            cur.execute("DELETE FROM fact_events WHERE fact_id=? AND user_id=?",
                       (fact_id, user_id))
            cascaded["fact_events"] = cur.rowcount
            cur.execute("DELETE FROM fact_entity_roles WHERE fact_id=? AND user_id=?",
                       (fact_id, user_id))
            cascaded["fact_entity_roles"] = cur.rowcount
            cur.execute(
                "DELETE FROM fact_edges WHERE user_id=? AND (src_fact_id=? OR dst_id=?)",
                (user_id, fact_id, fact_id),
            )
            cascaded["fact_edges"] = cur.rowcount
            self._conn.commit()
        self._bump_write_version(user_id)
        if self._chroma_facts is not None:
            try:
                self._chroma_facts.delete(ids=[fact_id])
                cascaded["chroma_vectors"] = 1
            except Exception as e:  # noqa: BLE001 - best-effort, matches upsert_fact_event
                logger.warning("ChromaDB fact delete failed for %s: %s", fact_id, e)
        # Record the deletion as a trace. "Why did the agent forget X?" is the
        # category's top complaint and a delete is its most literal cause —
        # without this row the fact simply vanishes with no account of who
        # removed it or when. Written AFTER the cascade so a failed delete
        # never leaves a lying event behind.
        try:
            self.append_extraction_trace(ExtractionTrace(
                user_id=user_id, stage="api", action="delete", status="ok",
                reason="DELETE /v1/memories/{id}",
                output_fact_ids=[fact_id],
                metadata={"cascaded": dict(cascaded)},
            ))
        except Exception as e:  # noqa: BLE001 — the delete itself already
            # committed; losing its audit row is bad but must not resurrect
            # the fact or raise to the caller. Loud, not silent.
            logger.warning("delete trace not recorded for %s: %s", fact_id, e)
        return {"deleted": True, "cascaded": cascaded}

    @staticmethod
    def _canonical_entity_id(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        return "entity_" + (slug or "unknown")

    def _row_to_span(self, row) -> SourceSpan:
        return SourceSpan.from_dict(dict(row))

    def _row_to_turn(self, row) -> RawTurn:
        return RawTurn.from_dict(dict(row))

    def get_raw_turn(self, turn_id: str) -> RawTurn | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM raw_turns WHERE turn_id=?", (turn_id,))
            row = cur.fetchone()
        return self._row_to_turn(row) if row else None

    def get_raw_turns_by_ids(self, turn_ids: list) -> list[RawTurn]:
        if not turn_ids:
            return []
        placeholders = ",".join("?" * len(turn_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(f"SELECT * FROM raw_turns WHERE turn_id IN ({placeholders})", turn_ids)
            rows = cur.fetchall()
        turns = [self._row_to_turn(r) for r in rows]
        by_id = {t.turn_id: t for t in turns}
        return [by_id[tid] for tid in turn_ids if tid in by_id]

    def get_all_raw_turns(self, user_id: str) -> list[RawTurn]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM raw_turns WHERE user_id=? ORDER BY timestamp ASC",
                (user_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_turn(r) for r in rows]

    def _row_to_fact(self, row) -> FactEvent:
        d = _load_json_fields(dict(row), (
            ("object_entity_ids", []),
            ("source_span_ids", []),
            ("provenance", {}),
            ("metadata", {}),
        ))
        return FactEvent.from_dict(d)

    def get_source_span(self, span_id: str) -> SourceSpan | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM source_spans WHERE span_id=?", (span_id,))
            row = cur.fetchone()
        return self._row_to_span(row) if row else None

    def get_source_spans_by_ids(self, span_ids: list) -> list[SourceSpan]:
        if not span_ids:
            return []
        placeholders = ",".join("?" * len(span_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(f"SELECT * FROM source_spans WHERE span_id IN ({placeholders})", span_ids)
            rows = cur.fetchall()
        spans = [self._row_to_span(r) for r in rows]
        by_id = {s.span_id: s for s in spans}
        return [by_id[sid] for sid in span_ids if sid in by_id]

    def get_all_source_spans(self, user_id: str) -> list[SourceSpan]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM source_spans WHERE user_id=? ORDER BY session_time ASC",
                (user_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_span(r) for r in rows]

    def get_fact_event(self, fact_id: str) -> FactEvent | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM fact_events WHERE fact_id=?", (fact_id,))
            row = cur.fetchone()
        return self._row_to_fact(row) if row else None

    def get_all_fact_events(self, user_id: str, active_only: bool = True) -> list[FactEvent]:
        if self._derived_currency:
            active_only = False  # §E.3: return all; currency derived at read-side board
        status_filter = " AND status='active'" if active_only else ""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT * FROM fact_events WHERE user_id=?{status_filter} ORDER BY rowid ASC",
                (user_id,),
            )
            rows = cur.fetchall()
        facts = [self._row_to_fact(r) for r in rows]
        if active_only:
            now = time.time()
            facts = [
                fact for fact in facts
                if not (
                    (fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind)
                    in {FactKind.STATE.value, FactKind.PREFERENCE.value, FactKind.PROFILE.value}
                    and fact.valid_until is not None
                    and fact.valid_until < now
                )
            ]
        return facts

    def get_facts_for_span(self, span_id: str) -> list[FactEvent]:
        with self._lock:
            cur = self._conn.cursor()
            status_clause = "" if self._derived_currency else " AND status='active'"
            cur.execute(
                f"SELECT * FROM fact_events WHERE source_span_ids LIKE ?{status_clause}",
                (f"%{span_id}%",),
            )
            rows = cur.fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_graph_v2_counts(self, user_id: str) -> dict:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM raw_turns WHERE user_id=?", (user_id,))
            raw_turns = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM source_spans WHERE user_id=?", (user_id,))
            source_spans = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM fact_events WHERE user_id=?", (user_id,))
            fact_events = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM extraction_traces WHERE user_id=?", (user_id,))
            extraction_traces = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM source_entity_mentions WHERE user_id=?", (user_id,))
            entity_mentions = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM fact_edges WHERE user_id=?", (user_id,))
            fact_edges = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM summary_syntheses WHERE user_id=?", (user_id,))
            summaries = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM graph_entities WHERE user_id=?", (user_id,))
            graph_entities = cur.fetchone()[0]
        return {
            "raw_turns": raw_turns,
            "source_spans": source_spans,
            "fact_events": fact_events,
            "extraction_traces": extraction_traces,
            "entity_mentions": entity_mentions,
            "fact_edges": fact_edges,
            "summaries": summaries,
            "graph_entities": graph_entities,
        }

    def append_extraction_trace(self, trace: ExtractionTrace) -> None:
        d = trace.to_dict()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO extraction_traces
                    (trace_id, user_id, session_id, turn_id, span_id, stage, action,
                     status, reason, input_hash, output_fact_ids, error, metadata,
                     created_at, retain_until)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    status=excluded.status,
                    reason=excluded.reason,
                    output_fact_ids=excluded.output_fact_ids,
                    error=excluded.error,
                    metadata=excluded.metadata,
                    retain_until=excluded.retain_until
                """,
                (
                    d["trace_id"],
                    d["user_id"],
                    d["session_id"],
                    d["turn_id"],
                    d["span_id"],
                    d["stage"],
                    d["action"],
                    d["status"],
                    d["reason"],
                    d["input_hash"],
                    json.dumps(d["output_fact_ids"]),
                    d["error"],
                    json.dumps(d["metadata"]),
                    d["created_at"],
                    d["retain_until"],
                ),
            )
            self._conn.commit()

    def get_events(self, user_id: str, *, offset: int = 0, limit: int = 50,
                   actions: tuple[str, ...] = ()) -> tuple[list[ExtractionTrace], int]:
        """Paginated change history for one user (the /v1/events source).

        Returns (page, total). Unlike `get_recent_extraction_traces` this
        exists to be paged through by an operator answering "what happened to
        this memory?", so it reports the total rather than silently truncating.
        `actions` filters on the trace's action column (e.g. supersede,
        delete) — empty means every action.
        """
        where = "WHERE user_id=?"
        params: list = [user_id]
        if actions:
            where += " AND action IN (%s)" % ",".join("?" * len(actions))
            params.extend(actions)
        with self._lock:
            cur = self._conn.cursor()
            total = cur.execute(
                f"SELECT COUNT(*) FROM extraction_traces {where}", params
            ).fetchone()[0]
            rows = cur.execute(
                f"SELECT * FROM extraction_traces {where} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        traces = [
            ExtractionTrace.from_dict(
                _load_json_fields(dict(r), (("output_fact_ids", []), ("metadata", {})))
            )
            for r in rows
        ]
        return traces, total

    def get_recent_extraction_traces(self, user_id: str, n: int = 50) -> list[ExtractionTrace]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM extraction_traces WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, n),
            )
            rows = cur.fetchall()
        traces = []
        for row in rows:
            d = _load_json_fields(dict(row), (("output_fact_ids", []), ("metadata", {})))
            traces.append(ExtractionTrace.from_dict(d))
        return traces

    def upsert_entity_mention(self, mention: EntityMention) -> None:
        d = mention.to_dict()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO source_entity_mentions
                    (mention_id, user_id, entity_id, entity_name, source_span_id,
                     fact_id, role, link_confidence, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mention_id) DO UPDATE SET
                    entity_id=excluded.entity_id,
                    entity_name=excluded.entity_name,
                    source_span_id=excluded.source_span_id,
                    fact_id=excluded.fact_id,
                    role=excluded.role,
                    link_confidence=excluded.link_confidence
                """,
                (
                    d["mention_id"],
                    d["user_id"],
                    d["entity_id"],
                    d["entity_name"],
                    d["source_span_id"],
                    d["fact_id"],
                    d["role"],
                    d["link_confidence"],
                    d["created_at"],
                ),
            )
            self._conn.commit()

    def get_entity_mentions_by_terms(self, user_id: str, terms: list[str], n: int = 50) -> list[EntityMention]:
        clean_terms = [str(t).strip().lower() for t in terms if str(t).strip()]
        if not clean_terms:
            return []
        with self._lock:
            cur = self._conn.cursor()
            rows = []
            for term in clean_terms[:20]:
                cur.execute(
                    """
                    SELECT * FROM source_entity_mentions
                    WHERE user_id=? AND lower(entity_name) LIKE ?
                    ORDER BY link_confidence DESC, rowid DESC LIMIT ?
                    """,
                    (user_id, f"%{term}%", n),
                )
                rows.extend(cur.fetchall())
        seen = set()
        mentions = []
        for row in rows:
            d = dict(row)
            if d["mention_id"] in seen:
                continue
            seen.add(d["mention_id"])
            mentions.append(EntityMention.from_dict(d))
            if len(mentions) >= n:
                break
        return mentions

    def upsert_fact_edge(self, edge: FactEdge) -> None:
        d = edge.to_dict()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO fact_edges
                    (edge_id, user_id, src_fact_id, dst_id, edge_type, predicate_raw,
                     predicate_canonical, confidence, metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, src_fact_id, dst_id, edge_type) DO UPDATE SET
                    predicate_raw=excluded.predicate_raw,
                    predicate_canonical=excluded.predicate_canonical,
                    confidence=excluded.confidence,
                    metadata=excluded.metadata
                """,
                (
                    d["edge_id"],
                    d["user_id"],
                    d["src_fact_id"],
                    d["dst_id"],
                    d["edge_type"],
                    d["predicate_raw"],
                    d["predicate_canonical"],
                    d["confidence"],
                    json.dumps(d["metadata"]),
                    d["created_at"],
                ),
            )
            self._conn.commit()

    def get_fact_edges(self, user_id: str, fact_id: str = "", edge_type: str = "") -> list[FactEdge]:
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT * FROM fact_edges WHERE user_id=?"
            args = [user_id]
            if fact_id:
                sql += " AND src_fact_id=?"
                args.append(fact_id)
            if edge_type:
                sql += " AND edge_type=?"
                args.append(edge_type)
            cur.execute(sql, args)
            rows = cur.fetchall()
        edges = []
        for row in rows:
            d = _load_json_fields(dict(row), (("metadata", {}),))
            edges.append(FactEdge.from_dict(d))
        return edges

    def get_fact_edges_by_dst(
        self,
        user_id: str,
        dst_id: str,
        edge_types: list[str] | None = None,
    ) -> list[FactEdge]:
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT * FROM fact_edges WHERE user_id=? AND dst_id=?"
            args: list = [user_id, dst_id]
            if edge_types:
                placeholders = ",".join("?" for _ in edge_types)
                sql += f" AND edge_type IN ({placeholders})"
                args.extend(edge_types)
            cur.execute(sql, args)
            rows = cur.fetchall()
        edges = []
        for row in rows:
            d = _load_json_fields(dict(row), (("metadata", {}),))
            edges.append(FactEdge.from_dict(d))
        return edges

    def mark_stale_expired(self, user_id: str) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE fact_events
                SET status=?
                WHERE user_id=?
                  AND status=?
                  AND valid_until IS NOT NULL
                  AND valid_until < ?
                  AND kind IN (?, ?, ?)
                """,
                (
                    FactStatus.STALE.value,
                    user_id,
                    FactStatus.ACTIVE.value,
                    now,
                    FactKind.STATE.value,
                    FactKind.PREFERENCE.value,
                    FactKind.PROFILE.value,
                ),
            )
            changed = cur.rowcount
            self._conn.commit()
        if changed:
            self._bump_write_version(user_id)
        return changed

    # ------------------------------------------------------------------
    # L3 Entity registry (v2 graph)
    # ------------------------------------------------------------------

    def register_entity(
        self,
        user_id: str,
        entity_id: str,
        dedup_key: str,
        surface_form: str,
        entity_type: str = "concept",
    ) -> None:
        """Idempotently register/merge an L3 entity node from one mention.

        Accumulates surface forms, bumps mention_count, and upgrades the type
        away from the generic 'concept' default when a more specific role is
        seen. Cheap upsert (single read-modify-write), called once per mention.
        """
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM graph_entities WHERE user_id=? AND entity_id=?",
                (user_id, entity_id),
            )
            row = cur.fetchone()
            if row is None:
                forms = [surface_form] if surface_form else []
                cur.execute(
                    """
                    INSERT INTO graph_entities
                        (entity_id, user_id, dedup_key, entity_type,
                         surface_forms, mention_count, summary, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (entity_id, user_id, dedup_key, entity_type,
                     json.dumps(forms), 1, "", now, now),
                )
            else:
                d = dict(row)
                try:
                    forms = json.loads(d.get("surface_forms") or "[]")
                except json.JSONDecodeError:
                    forms = []
                if surface_form and surface_form not in forms:
                    forms.append(surface_form)
                etype = d.get("entity_type") or "concept"
                if etype == "concept" and entity_type != "concept":
                    etype = entity_type
                cur.execute(
                    """
                    UPDATE graph_entities SET
                        dedup_key=?, entity_type=?, surface_forms=?,
                        mention_count=?, updated_at=?
                    WHERE user_id=? AND entity_id=?
                    """,
                    (dedup_key or d.get("dedup_key", ""), etype,
                     json.dumps(forms), int(d.get("mention_count", 0) or 0) + 1,
                     now, user_id, entity_id),
                )
            self._conn.commit()

    def get_graph_entity(self, user_id: str, entity_id: str) -> GraphEntity | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM graph_entities WHERE user_id=? AND entity_id=?",
                (user_id, entity_id),
            )
            row = cur.fetchone()
        return GraphEntity.from_dict(dict(row)) if row else None

    def get_all_graph_entities(self, user_id: str) -> list[GraphEntity]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM graph_entities WHERE user_id=? ORDER BY mention_count DESC",
                (user_id,),
            )
            rows = cur.fetchall()
        return [GraphEntity.from_dict(dict(r)) for r in rows]

    def get_registry_entities(self, user_id: str) -> list[dict]:
        """Compatibility view of graph entity registry rows."""
        return [entity.to_dict() for entity in self.get_all_graph_entities(user_id)]

    def get_facts_for_entity(self, entity_id: str, *, user_id: str) -> list[FactEvent]:
        """Facts where the entity participates in any role (the L3->L2 timeline).

        Signature pinned here (not a straight port): the source's
        `active_only` parameter is dropped — every existing call site
        (the predecessor implementation, the predecessor implementation)
        always used the default `active_only=True`, so folding it to
        unconditional is byte-identical for every real caller. This method is
        consumed by both Task 6 (retrieval fusion) and Task 7 (dreaming's
        EntityProfileSynthesizer.rebuild), which must agree on one final
        signature rather than each porting their own variant.
        """
        with self._lock:
            cur = self._conn.cursor()
            sql = (
                "SELECT DISTINCT fe.* FROM fact_events fe "
                "JOIN fact_entity_roles fer ON fe.fact_id = fer.fact_id "
                "WHERE fer.user_id=? AND fer.entity_id=?"
            )
            args = [user_id, entity_id]
            if not self._derived_currency:
                sql += " AND fe.status='active'"
            sql += " ORDER BY COALESCE(fe.occurred_start, fe.valid_from), fe.rowid"
            cur.execute(sql, args)
            rows = cur.fetchall()
        return [self._row_to_fact(row) for row in rows]

    def get_facts_by_edge_target(
        self, user_id: str, edge_type: str, dst_id: str, active_only: bool = True
    ) -> list[FactEvent]:
        """D37 reverse-edge lookup: facts whose typed edge points at dst_id."""
        with self._lock:
            cur = self._conn.cursor()
            sql = (
                "SELECT fe.* FROM fact_events fe "
                "JOIN fact_edges fed ON fe.fact_id = fed.src_fact_id "
                "WHERE fed.user_id=? AND fed.edge_type=? AND fed.dst_id=?"
            )
            args = [user_id, edge_type, dst_id]
            if active_only and not self._derived_currency:
                sql += " AND fe.status='active'"
            cur.execute(sql, args)
            rows = cur.fetchall()
        return [self._row_to_fact(row) for row in rows]

    # ------------------------------------------------------------------
    # D35 entity profile staleness (incremental dirty bit)
    # ------------------------------------------------------------------

    def mark_entity_stale(self, user_id: str, entity_id: str, session_id: str = "") -> None:
        """O(1) dirty-bit upsert: this (user, entity) profile needs a rebuild."""
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO entity_profile_stale
                    (user_id, entity_id, first_marked_at, last_marked_at,
                     new_fact_count, last_session_id)
                VALUES (?,?,?,?,1,?)
                ON CONFLICT(user_id, entity_id) DO UPDATE SET
                    last_marked_at=excluded.last_marked_at,
                    new_fact_count=entity_profile_stale.new_fact_count + 1,
                    last_session_id=excluded.last_session_id
                """,
                (user_id, entity_id, now, now, session_id),
            )
            self._conn.commit()

    def get_stale_entities(self, user_id: str, order: str = "by_staleness") -> list[str]:
        if order == "by_fact_count_delta":
            order_sql = "new_fact_count DESC, last_marked_at ASC"
        elif order == "fifo":
            order_sql = "first_marked_at ASC"
        else:  # by_staleness
            order_sql = "last_marked_at ASC"
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT entity_id FROM entity_profile_stale WHERE user_id=? ORDER BY {order_sql}",
                (user_id,),
            )
            return [r[0] for r in cur.fetchall()]

    def clear_entity_stale(self, user_id: str, entity_id: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM entity_profile_stale WHERE user_id=? AND entity_id=?",
                (user_id, entity_id),
            )
            self._conn.commit()

    def count_stale_entities(self, user_id: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM entity_profile_stale WHERE user_id=?", (user_id,))
            return cur.fetchone()[0]

    def get_source_spans_by_turn(self, user_id: str, turn_id: str) -> list[SourceSpan]:
        """Sibling claim spans of a turn (D37 EVIDENCES-neighbor expansion)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM source_spans WHERE user_id=? AND turn_id=? ORDER BY char_start",
                (user_id, turn_id),
            )
            rows = cur.fetchall()
        return [self._row_to_span(row) for row in rows]

    def get_facts_by_occurred_prefix(
        self, user_id: str, bucket_prefix: str, active_only: bool = True
    ) -> list[FactEvent]:
        """D37 date-window anchor: facts whose OCCURRED_DURING bucket starts with prefix."""
        if not bucket_prefix:
            return []
        with self._lock:
            cur = self._conn.cursor()
            sql = (
                "SELECT fe.* FROM fact_events fe "
                "JOIN fact_edges fed ON fe.fact_id = fed.src_fact_id "
                "WHERE fed.user_id=? AND fed.edge_type='OCCURRED_DURING' AND fed.dst_id LIKE ?"
            )
            args = [user_id, f"{bucket_prefix}%"]
            if active_only and not self._derived_currency:
                sql += " AND fe.status='active'"
            cur.execute(sql, args)
            rows = cur.fetchall()
        return [self._row_to_fact(row) for row in rows]

    def upsert_summary_synthesis(self, summary: SummarySynthesis) -> None:
        d = summary.to_dict()
        now = time.time()
        summary.updated_at = now
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO summary_syntheses
                    (summary_id, user_id, scope_type, scope_id, summary_text,
                     source_fact_ids, source_span_ids, status, dirty,
                     scope_revision, built_from_revision, dirty_reason,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, scope_type, scope_id) DO UPDATE SET
                    summary_text=excluded.summary_text,
                    source_fact_ids=excluded.source_fact_ids,
                    source_span_ids=excluded.source_span_ids,
                    status=excluded.status,
                    -- DR-004 #4 revision-gated commit: if scope advanced past
                    -- built_from during this build, discard the "clean"
                    -- verdict and keep it dirty.
                    built_from_revision=excluded.built_from_revision,
                    scope_revision=MAX(summary_syntheses.scope_revision, excluded.built_from_revision),
                    dirty=CASE WHEN summary_syntheses.scope_revision > excluded.built_from_revision
                               THEN 1 ELSE excluded.dirty END,
                    dirty_reason=CASE WHEN summary_syntheses.scope_revision > excluded.built_from_revision
                                      THEN summary_syntheses.dirty_reason ELSE excluded.dirty_reason END,
                    updated_at=excluded.updated_at
                """,
                (
                    d["summary_id"],
                    d["user_id"],
                    d["scope_type"],
                    d["scope_id"],
                    d["summary_text"],
                    json.dumps(d["source_fact_ids"]),
                    json.dumps(d["source_span_ids"]),
                    d["status"],
                    int(d["dirty"]),
                    int(d.get("scope_revision", 0) or 0),
                    int(d.get("built_from_revision", 0) or 0),
                    json.dumps(d.get("dirty_reason", []) or []),
                    d["created_at"],
                    now,
                ),
            )
            self._conn.commit()
        self._bump_write_version(summary.user_id)

    def get_summary_syntheses(
        self,
        user_id: str,
        active_only: bool = True,
        routing_only: bool = False,
    ) -> list[SummarySynthesis]:
        """routing_only=True returns only summaries eligible for recall
        (DR-004 #3): status=active AND dirty=0 AND built_from_revision==scope_revision."""
        with self._lock:
            cur = self._conn.cursor()
            if routing_only:
                cur.execute(
                    "SELECT * FROM summary_syntheses WHERE user_id=? AND status='active' "
                    "AND dirty=0 AND built_from_revision=scope_revision ORDER BY updated_at DESC",
                    (user_id,),
                )
            elif active_only:
                cur.execute(
                    "SELECT * FROM summary_syntheses WHERE user_id=? AND status='active' ORDER BY updated_at DESC",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM summary_syntheses WHERE user_id=? ORDER BY updated_at DESC",
                    (user_id,),
                )
            rows = cur.fetchall()
        summaries = []
        for row in rows:
            d = _load_json_fields(dict(row), (
                ("source_fact_ids", []),
                ("source_span_ids", []),
                ("dirty_reason", []),
            ))
            summaries.append(SummarySynthesis.from_dict(d))
        return summaries

    def mark_summary_dirty(
        self, user_id: str, scope_type: str, scope_id: str, reason: str = "dependency_change"
    ) -> int:
        """DR-004 #1: on a dependency change, bump scope_revision, set dirty,
        append dirty_reason."""
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE summary_syntheses
                SET dirty=1,
                    scope_revision=scope_revision+1,
                    dirty_reason=json_insert(
                        CASE WHEN dirty_reason IS NULL OR dirty_reason='' THEN '[]' ELSE dirty_reason END,
                        '$[#]', ?),
                    updated_at=?
                WHERE user_id=? AND scope_type=? AND scope_id=? AND status='active'
                """,
                (reason, now, user_id, scope_type, scope_id),
            )
            changed = cur.rowcount
            self._conn.commit()
        if changed:
            self._bump_write_version(user_id)
        return changed

    def mark_summary_dirty_by_id(self, summary_id: str, reason: str = "dependency_change") -> int:
        """DR-004 #6: a Summary looked up via the explicit dependency index is
        invalidated directly by id."""
        now = time.time()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE summary_syntheses
                SET dirty=1,
                    scope_revision=scope_revision+1,
                    dirty_reason=json_insert(
                        CASE WHEN dirty_reason IS NULL OR dirty_reason='' THEN '[]' ELSE dirty_reason END,
                        '$[#]', ?),
                    updated_at=?
                WHERE summary_id=? AND status='active'
                """,
                (reason, now, summary_id),
            )
            changed = cur.rowcount
            cur.execute("SELECT user_id FROM summary_syntheses WHERE summary_id=?", (summary_id,))
            row = cur.fetchone()
            self._conn.commit()
        if changed and row:
            self._bump_write_version(row[0])
        return changed

    def get_scope_revision(self, user_id: str, scope_type: str, scope_id: str) -> int:
        """The scope's current dependency version; implicitly 0 when no Summary row exists."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT scope_revision FROM summary_syntheses "
                "WHERE user_id=? AND scope_type=? AND scope_id=?",
                (user_id, scope_type, scope_id),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def archive_summary(self, user_id: str, scope_type: str, scope_id: str) -> int:
        """DR-004 #5: archive a Summary when its scope has no active facts left
        (status=archived, dirty=0) — keeps history but removes it from the
        recall index."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE summary_syntheses
                SET status='archived', dirty=0, updated_at=?
                WHERE user_id=? AND scope_type=? AND scope_id=? AND status='active'
                """,
                (time.time(), user_id, scope_type, scope_id),
            )
            changed = cur.rowcount
            self._conn.commit()
        if changed:
            self._bump_write_version(user_id)
        return changed

    # DR-011 EntityRules CRUD + DR-004 #6 Summary dependency index (R10:
    # split out of this file into entity_rules.py once porting the ~265L
    # span would have pushed this file past ~1500 lines — see that module's
    # docstring). Reached via composition: `self.entity_rules`, constructed
    # in __init__, not inherited.

    # ------------------------------------------------------------------
    # Audit bundle persistence (R6: default OFF, explicit opt-in)
    # ------------------------------------------------------------------

    def persist_audit_bundle(
        self,
        user_id: str,
        query: str,
        bundle: dict,
        *,
        audit: bool = False,
        retention_cap: int = 100,
    ) -> None:
        """Persist an explicitly opted-in audit and retain a bounded user tail."""
        if not audit:
            return
        if isinstance(retention_cap, bool) or not isinstance(retention_cap, int) or retention_cap < 1:
            raise ValueError("retention_cap must be a positive integer")
        bundle_id = bundle.get("bundle_id") or "audit_bundle_" + self._text_hash(json.dumps(bundle, sort_keys=True))[:16]
        bundle["bundle_id"] = bundle_id
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    INSERT INTO audit_bundles (bundle_id, user_id, query, payload, created_at)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(bundle_id) DO UPDATE SET
                        user_id=excluded.user_id,
                        query=excluded.query,
                        payload=excluded.payload,
                        created_at=excluded.created_at
                    """,
                    (bundle_id, user_id, query, json.dumps(bundle), time.time()),
                )
                self._prune_audit_bundles(cur, user_id, retention_cap)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _prune_audit_bundles(
        cur: sqlite3.Cursor, user_id: str, retention_cap: int
    ) -> None:
        cur.execute(
            """
            DELETE FROM audit_bundles
            WHERE user_id=? AND bundle_id IN (
                SELECT bundle_id
                FROM audit_bundles
                WHERE user_id=?
                ORDER BY created_at DESC, bundle_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (user_id, user_id, retention_cap),
        )
