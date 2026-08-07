"""POST/GET /v1/memories, GET/DELETE /v1/memories/{id} (PRD M1 R1.1/R1.4/R1.6).

Write path (`POST`) is a thin composition over `SodaMem.ingest()`; read/list
(`GET`) reads `Store.get_all_fact_events`/`get_fact_event` directly (no
retrieval ranking involved — this is a raw, paginated dump, not search);
delete (`DELETE`) archives via `Store.archive_fact_event`, with the physical
`Store.delete_fact_event` purge behind `?purge=true` + `SODAMEM_ALLOW_PURGE`
(see sodamem/memory/storage/store.py).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from sodamem import SodaMem
from sodamem.errors import ErrorCode, TenancyError
from sodamem.memory._shared import _ts_to_iso
from sodamem.models import FactEvent, FactStatus

from server.jobs import get_job_runner
from server.models import (
    BatchAddRequest,
    BatchAddResult,
    BatchItemResult,
    UpdateMemoryRequest,
    UpdateResult,
    AddMemoriesAccepted,
    AddMemoriesRequest,
    AddMemoriesResult,
    DeleteResult,
    ErrorBody,
    Memory,
    MemoryList,
)
from server.settings import get_settings
from server.stores import get_store_manager
from server.usage import get_usage_registry
from server.webhooks import get_webhook_dispatcher

from ._scope import reject_unsupported_scope

router = APIRouter(tags=["memories"])


def _run_ingest(mem: SodaMem, turns: list[dict], *, user_id: str,
                 session_id: str, session_time: float,
                 agent_id: str = "", run_id: str = "",
                 project_id: str = "", infer: bool = True) -> dict:
    """Shared by both the sync (200) and async (job-runner) paths so the two
    never drift on which counts field means what."""
    result = mem.ingest(turns, user_id=user_id, session_id=session_id,
                        session_time=session_time,
                        agent_id=agent_id, run_id=run_id,
                        project_id=project_id, infer=infer)
    # `IngestResult.usage` was computed and dropped here — R2.10's whole gap.
    get_usage_registry().record("ingest", result.usage)
    counts = result.counts
    # One SourceSpan + one RawTurn is written per non-empty turn (see
    # IngestClient.ingest_session) — "extract_window_spans_in_session" counts
    # exactly those, so it doubles correctly as both spans_written and
    # turns_written rather than needing two separately-tracked counters.
    spans = counts.get("extract_window_spans_in_session", 0)
    return {
        "session_id": session_id,
        "facts_extracted": counts.get("extracted_facts_in_session", 0),
        "spans_written": spans,
        "turns_written": spans,
    }


def _emit(event_type: str, *, user_id: str, memory_id: str | None = None,
          session_id: str | None = None, **extra) -> None:
    """Announce a memory change to a subscriber, if one is configured.

    Fire-and-forget by construction (see server/webhooks.py): unconfigured is
    a no-op, delivery happens off-thread, and a dead receiver can neither slow
    nor fail the change that just committed.
    """
    get_webhook_dispatcher().dispatch({
        "type": event_type,
        "user_id": user_id,
        "memory_id": memory_id,
        "session_id": session_id,
        **extra,
    })


@router.post(
    "/v1/memories",
    response_model=AddMemoriesResult,
    responses={202: {"model": AddMemoriesAccepted}},
)
def add_memories(request: AddMemoriesRequest):
    # Scope IS supported on this route: agent_id/run_id/project_id are stamped
    # onto every fact this ingest extracts (see _scope.py for which routes
    # still 501).
    session_id = request.session_id or uuid.uuid4().hex
    session_time = (
        request.session_time if request.session_time is not None else time.time()
    )
    turns = [{"role": m.role, "content": m.content} for m in request.messages]
    # Borrowed here, released in whichever branch actually finishes with it.
    # A plain `with lease(...)` would be wrong for async_mode: the job keeps
    # using this store after the response is sent, so the borrow has to
    # outlive the request and be handed to the job.
    stores = get_store_manager()
    mem = stores.get(request.user_id)

    if request.async_mode:
        def _ingest_then_release() -> dict:
            try:
                return _run_ingest(mem, turns, user_id=request.user_id,
                                   session_id=session_id, session_time=session_time,
                                   agent_id=request.agent_id or "",
                                   run_id=request.run_id or "",
                                   project_id=request.project_id or "",
                                   infer=request.infer)
            finally:
                stores.release(request.user_id)

        try:
            job = get_job_runner().submit("ingest", request.user_id,
                                          _ingest_then_release)
        except BaseException:
            # Never submitted, so nothing will ever release it.
            stores.release(request.user_id)
            raise
        accepted = AddMemoriesAccepted(job_id=job.job_id, session_id=session_id)
        # Returned as a raw JSONResponse (not the declared response_model
        # instance) precisely because the shape differs from the 200 case —
        # FastAPI only auto-serializes via response_model for a plain
        # returned object; a Response subclass is passed through untouched,
        # so this is the correct way to honor two different response shapes
        # off one route without lying about either in the OpenAPI doc.
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED,
                            content=accepted.model_dump())
    try:
        result = _run_ingest(mem, turns, user_id=request.user_id,
                             session_id=session_id, session_time=session_time,
                             agent_id=request.agent_id or "",
                             run_id=request.run_id or "",
                             project_id=request.project_id or "",
                             infer=request.infer)
    finally:
        stores.release(request.user_id)
    _emit("memory_add", user_id=request.user_id, session_id=session_id,
          facts_extracted=result.get("facts_extracted", 0))
    return AddMemoriesResult(**result)


def _to_memory(fact: FactEvent, spans_by_id: dict[str, Any]) -> Memory:
    span = spans_by_id.get(fact.source_span_ids[0]) if fact.source_span_ids else None
    meta = fact.metadata if isinstance(fact.metadata, dict) else {}
    # The scope stamp lives in metadata (one dict, see IngestClient); lifting
    # it to named response fields is what lets a caller SEE which project a
    # memory came from without parsing `metadata` — the alternative is a
    # `project_id` you can filter on but never read back.
    stamp = meta.get("scope") or {}
    return Memory(
        id=fact.fact_id,
        user_id=fact.user_id,
        agent_id=stamp.get("agent_id"),
        run_id=stamp.get("run_id"),
        project_id=stamp.get("project_id"),
        # `Memory.content` is documented as "the fact's human-readable
        # statement", and that is what this now returns. It used to return
        # `fact_search_document(fact)` — the INDEX serialization BM25 and
        # chroma share, which pipe-joins predicate, event_type, modality,
        # units and entity roles ("prefers tabs | past_event | subject user").
        # Fine as a retrieval document, wrong as a display string, and it made
        # the same memory read back differently over HTTP than over MCP (the
        # MCP surface always projected predicate_raw). Caught by the
        # local/remote parity test in tests/test_mcp_backend.py.
        content=fact.predicate_raw or fact.predicate_canonical or "",
        kind=fact.kind.value if hasattr(fact.kind, "value") else fact.kind,
        status=fact.status.value if hasattr(fact.status, "value") else fact.status,
        session_id=span.session_id if span else None,
        occurred_at=_ts_to_iso(fact.occurred_start),
        valid_from=_ts_to_iso(fact.valid_from),
        valid_until=_ts_to_iso(fact.valid_until),
        confidence=fact.confidence,
        metadata=meta,
    )


@router.get("/v1/memories", response_model=MemoryList)
def list_memories(
    user_id: str = Query(..., min_length=1, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> MemoryList:
    reject_unsupported_scope(agent_id, run_id, project_id)
    with get_store_manager().lease(user_id) as mem:
        facts = mem.store.get_all_fact_events(user_id, active_only=True)
        total = len(facts)
        page = facts[offset: offset + limit]
        span_ids = sorted({sid for f in page for sid in (f.source_span_ids or [])})
        spans_by_id = (
            {s.span_id: s for s in mem.store.get_source_spans_by_ids(span_ids)}
            if span_ids else {}
        )
        memories = [_to_memory(f, spans_by_id) for f in page]
    return MemoryList(memories=memories, total=total, offset=offset, limit=limit)


@router.get(
    "/v1/memories/{memory_id}",
    response_model=Memory,
    responses={404: {"model": ErrorBody}},
)
def get_memory(
    memory_id: str,
    user_id: str = Query(..., min_length=1, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
) -> Memory:
    reject_unsupported_scope(agent_id, run_id, project_id)
    with get_store_manager().lease(user_id) as mem:
        return _read_memory(mem, memory_id, user_id)


def _read_memory(mem: SodaMem, memory_id: str, user_id: str) -> Memory:
    fact = mem.store.get_fact_event(memory_id)
    # An archived fact is gone as far as this API is concerned: DELETE already
    # answered `deleted: true` for it, and the list endpoint drops it
    # (get_all_fact_events is active_only). Serving it here would make "deleted"
    # mean something different on every endpoint. The row and its provenance
    # still exist underneath — that is the point of a tombstone — but reaching
    # them is an operator/audit concern, not this route's.
    if fact is None or fact.status == FactStatus.ARCHIVED:
        raise HTTPException(status_code=404,
                            detail=f"memory {memory_id!r} not found")
    if fact.user_id != user_id:
        # Defense in depth, not a reachable path through this route today:
        # StoreManager opens one physical store PER user_id (server/stores.py),
        # so `mem.store` here can only ever contain facts written under this
        # same `user_id` — this branch guards the case where that per-user
        # file isolation is ever bypassed (e.g. a shared multi-tenant store,
        # which is how this repo's own benchmark stores are laid out). Same
        # typed error DELETE's core delete_fact_event raises for the
        # identical situation, so one failure mode gets one status code
        # everywhere in this API, not two different shapes depending on
        # which route noticed it.
        raise TenancyError(
            f"memory {memory_id!r} belongs to a different user_id",
            code=ErrorCode.TENANCY_INVALID,
            details={"memory_id": memory_id, "requested_user_id": user_id},
        )
    spans_by_id: dict[str, Any] = {}
    if fact.source_span_ids:
        spans = mem.store.get_source_spans_by_ids(fact.source_span_ids[:1])
        spans_by_id = {s.span_id: s for s in spans}
    return _to_memory(fact, spans_by_id)


def _run_batch(mem, sessions, *, user_id: str, agent_id: str, run_id: str,
               project_id: str = "") -> dict:
    """Ingest every session, collecting per-session outcomes.

    Every session is attempted even after one fails. All-or-nothing would be
    the wrong contract for a per-user ADD-only store: a single bad row in a
    5,000-session migration must not discard the 4,999 good ones. The failure
    is recorded with its index so the caller can fix and resubmit exactly that
    one — the unacceptable outcome is a skipped session nobody hears about.
    """
    results: list[BatchItemResult] = []
    for index, session in enumerate(sessions):
        session_id = session.session_id or uuid.uuid4().hex
        session_time = (
            session.session_time if session.session_time is not None else time.time()
        )
        turns = [{"role": m.role, "content": m.content} for m in session.messages]
        try:
            outcome = _run_ingest(mem, turns, user_id=user_id, session_id=session_id,
                                  session_time=session_time, agent_id=agent_id,
                                  run_id=run_id, project_id=project_id)
        except Exception as exc:  # noqa: BLE001 - surfaced per item, never swallowed
            results.append(BatchItemResult(
                index=index, ok=False, session_id=session_id,
                error=f"{type(exc).__name__}: {exc}",
            ))
            continue
        results.append(BatchItemResult(
            index=index, ok=True, session_id=session_id,
            facts_extracted=outcome.get("facts_extracted", 0),
        ))
    succeeded = sum(1 for item in results if item.ok)
    return BatchAddResult(succeeded=succeeded, failed=len(results) - succeeded,
                          results=results).model_dump()


@router.post(
    "/v1/memories/batch",
    response_model=BatchAddResult,
    responses={202: {"model": AddMemoriesAccepted}},
)
def add_memories_batch(request: BatchAddRequest):
    """Bulk import (PRD table stakes). Sync returns per-session outcomes;
    async returns 202 + one job covering the whole batch.

    No route-shadowing hazard today: the only `/v1/memories/{memory_id}`
    routes are GET/PATCH/DELETE, so a literal `/batch` under POST cannot be
    captured as `memory_id="batch"`. If a `POST /v1/memories/{memory_id}` is
    ever added it must be declared AFTER this one — FastAPI matches in
    declaration order within a router.
    """
    reject_unsupported_scope(request.agent_id, request.run_id, request.project_id)
    mem = get_store_manager().get(request.user_id)
    sessions = request.sessions
    if request.async_mode:
        job = get_job_runner().submit(
            "ingest_batch",
            request.user_id,
            lambda: _run_batch(mem, sessions, user_id=request.user_id,
                               agent_id=request.agent_id or "",
                               run_id=request.run_id or "",
                               project_id=request.project_id or ""),
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=AddMemoriesAccepted(job_id=job.job_id, session_id="").model_dump(),
        )
    return BatchAddResult(**_run_batch(mem, sessions, user_id=request.user_id,
                                       agent_id=request.agent_id or "",
                                       run_id=request.run_id or "",
                                       project_id=request.project_id or ""))


@router.patch(
    "/v1/memories/{memory_id}",
    response_model=UpdateResult,
    responses={404: {"model": ErrorBody}},
)
def update_memory(memory_id: str, request: UpdateMemoryRequest) -> UpdateResult:
    """Correct a memory (PRD R1.5). ADD-only: this ingests `content` as a new
    memory and CLOSES the old one with a SUPERSEDES edge — it never rewrites
    a row. The old version stays readable at GET /v1/memories/{old_id} with
    `status=superseded` and a `valid_until`, so "why did the AI change its
    mind" is still answerable afterwards.

    Ownership is verified BEFORE the ingest, not after: extracting first and
    then discovering the target belongs to someone else would leave the new
    facts stranded in the caller's store with nothing superseded — a partial
    write on the error path.
    """
    reject_unsupported_scope(request.agent_id, request.run_id, request.project_id)
    mem = get_store_manager().get(request.user_id)
    target = mem.store.get_fact_event(memory_id)
    if target is None:
        raise HTTPException(status_code=404,
                            detail=f"memory {memory_id!r} not found")
    if target.user_id != request.user_id:
        raise TenancyError(
            f"memory {memory_id!r} belongs to a different user_id",
            code=ErrorCode.TENANCY_INVALID,
            details={"memory_id": memory_id, "requested_user_id": request.user_id},
        )

    session_id = request.session_id or uuid.uuid4().hex
    session_time = (
        request.session_time if request.session_time is not None else time.time()
    )
    before = {f.fact_id for f in mem.store.get_all_fact_events(request.user_id, active_only=False)}
    _run_ingest(mem, [{"role": "user", "content": request.content}],
                user_id=request.user_id, session_id=session_id,
                session_time=session_time, agent_id="", run_id="")
    fresh = [f for f in mem.store.get_all_fact_events(request.user_id, active_only=False)
             if f.fact_id not in before]
    if not fresh:
        # Extraction found nothing assertable in the correction. Closing the
        # old memory anyway would leave the user with no current value at all,
        # which is strictly worse than refusing.
        raise HTTPException(
            status_code=422,
            detail=(
                "no memory could be extracted from `content` — refusing to "
                "close the existing one, which would leave no current version"
            ),
        )
    # The correction's own newest assertion is the winner; ties fall to the
    # first extracted, matching the order the extractor emits.
    winner = max(fresh, key=lambda f: (f.valid_from or f.occurred_start or 0.0))
    result = mem.store.supersede_fact_event(memory_id, winner.fact_id,
                                            user_id=request.user_id)
    spans_by_id: dict[str, Any] = {}
    if winner.source_span_ids:
        spans = mem.store.get_source_spans_by_ids(winner.source_span_ids[:1])
        spans_by_id = {s.span_id: s for s in spans}
    _emit("memory_supersede", user_id=request.user_id, memory_id=winner.fact_id,
          session_id=session_id, superseded_id=memory_id)
    return UpdateResult(
        memory=_to_memory(winner, spans_by_id),
        superseded_id=memory_id,
        valid_until=_ts_to_iso(result.get("valid_until")),
    )


@router.delete("/v1/memories/{memory_id}", response_model=DeleteResult)
def delete_memory(
    memory_id: str,
    user_id: str = Query(..., min_length=1, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    purge: bool = Query(
        default=False,
        description=(
            "Physically erase the fact and cascade to roles/edges/vectors "
            "instead of archiving it. Requires SODAMEM_ALLOW_PURGE=true."
        ),
    ),
) -> DeleteResult:
    reject_unsupported_scope(agent_id, run_id, project_id)
    # Default delete is a tombstone, matching the MCP `delete_memory` tool —
    # one verb, one meaning, whichever door the caller came in through. The
    # irreversible erase is a separate opt-in on two independent switches: the
    # operator's deploy-time SODAMEM_ALLOW_PURGE, and the caller's ?purge=true.
    if purge and not get_settings().allow_purge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("purge is disabled on this deployment; unset ?purge=true to "
                    "archive instead, or set SODAMEM_ALLOW_PURGE=true"),
        )
    # Cross-user ownership is enforced inside the store methods themselves
    # (both raise TenancyError, mapped to a 4xx by app.py's SodaMemError
    # handler) — not re-checked here, so there is exactly one place that
    # rule lives.
    with get_store_manager().lease(user_id) as mem:
        if purge:
            result = mem.store.delete_fact_event(memory_id, user_id=user_id)
            _emit("memory_delete", user_id=user_id, memory_id=memory_id,
                  purged=True, deleted=result["deleted"])
            return DeleteResult(id=memory_id, deleted=result["deleted"],
                                purged=True, cascaded=result["cascaded"])
        result = mem.store.archive_fact_event(memory_id, user_id=user_id)
        _emit("memory_delete", user_id=user_id, memory_id=memory_id,
              purged=False, deleted=result["deleted"])
        return DeleteResult(id=memory_id, deleted=result["deleted"],
                            already_deleted=result["already_archived"])
