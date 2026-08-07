"""Vector (embedding) search over a Store's chroma collections.

Ported from the predecessor implementation's `vector_search_source_spans`/
`vector_search_raw_turns`/`vector_search_fact_events` (:1917-1966) — the CQRS
read half of the vector index `Store` (Task 3) owns and writes
(`upsert_source_span`/`upsert_fact_event`/`_embed_raw_turn`). `Store`'s
public read surface for this is `chroma_available` (a bool, by design — see
that property's docstring: "the retrieval layer (Task 6) reads it to produce
a typed Degradation instead of an empty result nobody can distinguish from
'genuinely no matches.'"). The three live chroma collection handles
(`_chroma_spans`/`_chroma_facts`/`_chroma_raw_turns`) stay single-underscore,
same-subsystem access from here — exactly how the source's own `_storage.
_chroma_spans` etc. were only ever reached from inside `StorageBackend`
itself before this port split the read methods out; they do not become new
public `Store` API (Task 6's file list does not include modifying store.py).

§6.7 fix: the source's three methods were each bare `except Exception:
return []` — a vector-index outage was indistinguishable from "genuinely no
matches." Every failure path here (chroma unavailable at all, or a
query-time exception) appends a typed `Degradation(VECTOR_ROUTE_FAILED)` to
the caller-supplied `degraded` list and returns `[]` — the empty list is
still the right return value (there IS no evidence to hand back), but it now
arrives with a signal attached instead of silently reading as a true miss.
"""
from __future__ import annotations

from sodamem.memory.storage.store import Store
from sodamem.models import FactEvent, RawTurn, SourceSpan

from .config import Degradation, DegradationCode


def search_source_spans(
    query: str, *, store: Store, user_id: str, n: int, degraded: list[Degradation]
) -> list[SourceSpan]:
    if not store.chroma_available:
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            "chroma unavailable — vector route for source_spans skipped",
            {"channel": "source_spans"},
        ))
        return []
    try:
        results = store._chroma_spans.query(
            query_texts=[query],
            n_results=n,
            where={"user_id": {"$eq": user_id}},
        )
        ids = results.get("ids", [[]])[0]
        return store.get_source_spans_by_ids(ids)
    except Exception as e:  # noqa: BLE001 - converted to a typed Degradation, not swallowed
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            f"source_spans vector query failed: {e}",
            {"channel": "source_spans"},
        ))
        return []


def search_raw_turns(
    query: str, *, store: Store, user_id: str, n: int, degraded: list[Degradation]
) -> list[RawTurn]:
    """Vector recall over raw turns (issue #75 §2.3). Returns [] (with a
    Degradation only on a genuine failure, not merely "not populated yet")
    until the raw_turns collection is populated — ingest with raw_recall on,
    or a reindex_raw_turns backfill."""
    if not store.chroma_available:
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            "chroma unavailable — vector route for raw_turns skipped",
            {"channel": "raw_turns"},
        ))
        return []
    try:
        results = store._chroma_raw_turns.query(
            query_texts=[query],
            n_results=n,
            where={"user_id": {"$eq": user_id}},
        )
        ids = results.get("ids", [[]])[0]
        return store.get_raw_turns_by_ids(ids)
    except Exception as e:  # noqa: BLE001 - converted to a typed Degradation, not swallowed
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            f"raw_turns vector query failed: {e}",
            {"channel": "raw_turns"},
        ))
        return []


def search_fact_events(
    query: str, *, store: Store, user_id: str, n: int, degraded: list[Degradation]
) -> list[FactEvent]:
    if not store.chroma_available:
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            "chroma unavailable — vector route for fact_events skipped",
            {"channel": "fact_events"},
        ))
        return []
    try:
        results = store._chroma_facts.query(
            query_texts=[query],
            n_results=n,
            where={"user_id": {"$eq": user_id}},
        )
        ids = results.get("ids", [[]])[0]
        facts = [store.get_fact_event(fid) for fid in ids]
        return [f for f in facts if f]
    except Exception as e:  # noqa: BLE001 - converted to a typed Degradation, not swallowed
        degraded.append(Degradation(
            DegradationCode.VECTOR_ROUTE_FAILED,
            f"fact_events vector query failed: {e}",
            {"channel": "fact_events"},
        ))
        return []
