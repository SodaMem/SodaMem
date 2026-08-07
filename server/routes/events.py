"""GET /v1/events (PRD M1 R1.12) — the change history of a user's memory.

"Why did the agent forget X?" is this category's top complaint, and it is
unanswerable from the memory table alone: a fact that was superseded, merged
or deleted leaves no trace in the row that replaced it. The store has recorded
this all along in `extraction_traces` (633 supersede decisions across 20
benchmark stores, each with the target fact and the valid_until it would have
closed) — the data existed, nothing exposed it. This route is the exposure.

Deliberately NOT a new table: inventing an `events` table beside a trace table
that already carries stage/action/status/reason/output_fact_ids would leave two
half-truths to reconcile. What was missing was a delete trace (added alongside
this route) and pagination, not a second log.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from server.models import Event, EventList
from server.stores import get_store_manager

from ._scope import reject_unsupported_scope

router = APIRouter(tags=["events"])

# extraction_traces.action -> the product's event vocabulary. The internal
# names leak the ingest pipeline's history; callers get a stable, small set.
#
# The two `*_observed` actions are LEGACY-READ-ONLY as of 0806: ingest no
# longer produces them (supersession became unconditional when its flag was
# removed). They stay mapped because stores written before that date hold
# them, and dropping the mapping would silently shorten a user's event
# history on upgrade — the rows are still true, they just describe a decision
# the old default declined to apply.
_ACTION_TO_TYPE = {
    "extract": "memory_add",
    "delete": "memory_delete",
    "supersede": "memory_supersede",
    "supersede_observed": "memory_supersede",
    "contradict": "memory_supersede",
    "contradict_observed": "memory_supersede",
    "duplicate_merge": "memory_supersede",
    "same_event_extend": "memory_supersede",
}


def _to_event(t) -> Event | None:
    event_type = _ACTION_TO_TYPE.get(t.action)
    if event_type is None:
        # Pipeline bookkeeping (extract_window/flush and friends) is not a
        # memory change — skipped rather than surfaced as a mystery row.
        return None
    ids = list(t.output_fact_ids or [])
    meta = dict(t.metadata or {})
    return Event(
        event_id=t.trace_id,
        ts=datetime.fromtimestamp(t.created_at, tz=timezone.utc).isoformat(),
        user_id=t.user_id,
        type=event_type,
        memory_id=(meta.get("target_fact_id") or (ids[0] if ids else None)),
        session_id=t.session_id or None,
        # Legacy `observed` rows carry the decision WITHOUT having applied
        # it — say so, rather than letting a caller assume the memory changed.
        reason=(
            f"{t.reason} (observe-only: decision recorded, not applied)"
            if t.action.endswith("_observed") and t.reason
            else (t.reason or None)
        ),
        details={**meta, "action": t.action, "status": t.status},
    )


@router.get("/v1/events", response_model=EventList)
def list_events(
    user_id: str = Query(..., min_length=1, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    type: str | None = Query(
        default=None,
        description="Filter: memory_add | memory_supersede | memory_delete.",
    ),
) -> EventList:
    reject_unsupported_scope(agent_id, run_id, project_id)
    mem = get_store_manager().get(user_id)

    actions: tuple[str, ...] = ()
    if type:
        actions = tuple(a for a, t in _ACTION_TO_TYPE.items() if t == type)
        if not actions:
            # An unknown type filter must not silently return "everything".
            return EventList(events=[], total=0, offset=offset, limit=limit)

    traces, total = mem.store.get_events(
        user_id, offset=offset, limit=limit,
        actions=actions or tuple(_ACTION_TO_TYPE),
    )
    return EventList(
        events=[e for e in (_to_event(t) for t in traces) if e is not None],
        total=total, offset=offset, limit=limit,
    )
