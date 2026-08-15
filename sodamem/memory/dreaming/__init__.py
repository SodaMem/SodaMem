"""D35/D36 Dreaming / Consolidation.

Ported from the predecessor implementation.

Mechanism only — no policy. The memory core exposes the ``dream(user_id)``
primitive (`sodamem.SodaMem.dream`) and its invariants (per-user mutex,
cancellable, idempotent, non-blocking to reads). It does NOT decide when to
run. Trigger policy lives in the operator's scheduler / front-end / cron,
which reaches the primitive through ``POST /v1/maintenance/dream``.

The profile synthesizer is deterministic (no LLM): the same fact history
always produces the same profile fact id, so killing a cycle mid-way and
restarting converges to an identical end state.

Signature note (not a straight port): the source's `run_dream(storage,
maintainer, user_id, ...)` took an externally-constructed `GraphMaintainer`
because its caller (`GraphMemoryClientV2`, the predecessor implementation)
already owned one as a long-lived instance field (`self._maintainer =
GraphMaintainer(self._storage)`, built once at client construction). This
port's facade (`SodaMem`) keeps no such standing state — `.ingest()` already
builds its own `GraphMaintainer` per call inside `IngestClient.__init__`
rather than the facade owning one, and `.search()` follows the same
"construct on call" shape. `run_dream` here does the same: it builds its own
`GraphMaintainer(store)` (default `IngestConfig`, matching the source's
default-constructed maintainer exactly) so callers only ever hand it a
`Store` + `user_id` — see `sodamem/__init__.py`'s `.dream()` method.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from sodamem.memory._shared import _stable_id
from sodamem.memory.ingest.maintainer import GraphMaintainer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sodamem.memory.ingest.config import IngestConfig
from sodamem.memory.storage.store import Store
from sodamem.models import (
    FactEvent,
    FactKind,
    FactStatus,
    Modality,
    SourceType,
)

__all__ = [
    "DreamingOptions",
    "DreamingResult",
    "EntityProfileSynthesizer",
    "run_dream",
]


class CancelToken:
    """Cooperative cancellation for a dream cycle."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


@dataclass
class DreamingOptions:
    batch_size: int = 50
    max_processing_time_sec: float = 0.0      # 0 = no soft deadline
    priority_order: str = "by_staleness"       # by_staleness | by_fact_count_delta | fifo


@dataclass
class DreamingResult:
    user_id: str
    status: str = "ok"                         # ok | already_running
    entities_processed: int = 0
    profiles_written: int = 0
    profiles_superseded: int = 0
    cancelled: bool = False
    remaining_stale: int = 0

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "status": self.status,
            "entities_processed": self.entities_processed,
            "profiles_written": self.profiles_written,
            "profiles_superseded": self.profiles_superseded,
            "cancelled": self.cancelled,
            "remaining_stale": self.remaining_stale,
        }


# Per-user mutex registry. Different users dream in parallel; the same user
# never runs two cycles at once.
_USER_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _user_lock(user_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _USER_LOCKS.setdefault(user_id, threading.Lock())


class EntityProfileSynthesizer:
    """Rebuilds a deterministic cross-session profile fact for one entity."""

    def __init__(self, store: Store, maintainer: GraphMaintainer) -> None:
        self._store = store
        self._maintainer = maintainer

    def rebuild(self, user_id: str, entity_id: str) -> tuple[Optional[FactEvent], list[dict]]:
        facts = [
            f for f in self._store.get_facts_for_entity(entity_id, user_id=user_id)
            if f.kind != FactKind.PROFILE
        ]
        if not facts:
            return None, []
        ent = self._store.get_graph_entity(user_id, entity_id)
        # Display name comes from a surface form, never the dedup key.
        forms = (ent.surface_forms if ent else None) or []
        name = (forms[0] if forms else "") or entity_id

        ordered = sorted(
            facts,
            # valid/event time only — never created_at (ingestion wall-clock).
            key=lambda f: (f.occurred_start or f.valid_from or 0.0, f.fact_id),
        )
        parts: list[str] = []
        span_ids: list[str] = []
        fact_ids: list[str] = []
        valid_from = 0.0
        for f in ordered:
            roles = f.metadata.get("entity_roles", {}) if isinstance(f.metadata, dict) else {}
            role_text = " ".join(
                str(v) if not isinstance(v, list) else " ".join(str(x) for x in v)
                for k, v in roles.items() if k != "subject"
            )
            line = " ".join(p for p in [f.predicate_raw, role_text] if p).strip()
            if line:
                parts.append(line)
            span_ids.extend(f.source_span_ids)
            fact_ids.append(f.fact_id)
            # profile validity starts at the latest source VALID/event time,
            # never ingestion wall-clock (created_at).
            valid_from = max(valid_from, f.valid_from or f.occurred_start or 0.0)

        profile_text = " | ".join(parts)[:1800]
        # Deterministic id: stable while content is stable (idempotent re-run),
        # new when content changes (old profile is superseded via D32).
        content_hash = _stable_id("phash", user_id, entity_id, profile_text)
        profile = FactEvent(
            user_id=user_id,
            kind=FactKind.PROFILE,
            status=FactStatus.ACTIVE,
            fact_id=_stable_id("profile_fact", user_id, entity_id, content_hash),
            subject_entity_id="entity_user",
            predicate_raw=f"Profile of {name}: {profile_text}",
            predicate_canonical=f"entity_profile_{entity_id}",
            event_type="profile",
            modality=Modality.CURRENT_STATE,
            valid_from=valid_from or None,  # no source time -> None (no wall-clock)
            source_type=SourceType.DERIVED_MATERIALIZED,
            source_span_ids=sorted(set(span_ids)),
            confidence=0.7,
            confidence_reason="dreaming_entity_profile",
            provenance={
                "session_ids": [],
                "generator": "entity_profile_synth_v1",
                "profile_source_fact_ids": fact_ids,
            },
            metadata={
                "entity_roles": {"subject": "user", "profile_of": name},
                "support_text": profile_text,
                "profile_of_entity_id": entity_id,
                "profile_source_fact_count": len(fact_ids),
            },
        )
        maintained, actions = self._maintainer.maintain(profile)
        return maintained, actions


def run_dream(
    store: Store,
    user_id: str,
    *,
    config: Optional["IngestConfig"] = None,
    options: Optional[DreamingOptions] = None,
) -> DreamingResult:
    """Process the stale entity set into refreshed profile facts.

    Enforces the D36 invariants directly: per-user mutex (non-blocking
    acquire -> 'already_running'), cancellation, and idempotent progress via the
    dirty-bit table (only cleared once an entity is processed).
    """
    opts = options or DreamingOptions()
    result = DreamingResult(user_id=user_id)
    lock = _user_lock(user_id)
    if not lock.acquire(blocking=False):
        result.status = "already_running"
        result.remaining_stale = store.count_stale_entities(user_id)
        return result
    try:
        # T7-review fix: dream MUST maintain edges under the SAME IngestConfig the
        # deployment used at ingest, or edge-confidence weights silently fork
        # between write-time and dream-time (impossible in the source: one shared
        # maintainer + one global config). Callers thread their config here.
        maintainer = GraphMaintainer(store, config)
        synth = EntityProfileSynthesizer(store, maintainer)
        deadline = (
            time.time() + opts.max_processing_time_sec
            if opts.max_processing_time_sec > 0 else None
        )
        stale = store.get_stale_entities(user_id, order=opts.priority_order)
        processed = 0
        for entity_id in stale:
            if deadline is not None and time.time() > deadline:
                # `cancelled` means "stopped before the stale set was drained",
                # which is exactly what a deadline cut is. It used to be set
                # only by a CancelToken that no entry point could pass, so the
                # flag was always False and POST /v1/maintenance/dream
                # documented a value it never returned.
                result.cancelled = True
                break
            if processed >= opts.batch_size:
                break
            profile, actions = synth.rebuild(user_id, entity_id)
            result.entities_processed += 1
            if profile is not None:
                result.profiles_written += 1
                result.profiles_superseded += sum(
                    1 for a in actions if a.get("action") == "supersede"
                )
            # Clear the dirty bit ONLY after the entity is processed, so a
            # cancelled / deadline-cut cycle leaves the rest for the next call.
            store.clear_entity_stale(user_id, entity_id)
            processed += 1
        result.remaining_stale = store.count_stale_entities(user_id)
        return result
    finally:
        lock.release()
