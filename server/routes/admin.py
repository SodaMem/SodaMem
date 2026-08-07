"""`/v1/admin/*` — configuration and operational visibility (ADR 0001).

Single-tenant self-hosting still needs an answer to "what is this box running
with, and what has it been doing?" Today that answer requires shell access to
the container: read the env, tail the logs, `du` the volume. That is not an
answer, it is a workaround, and it is the reason these routes exist.

What this deliberately is NOT: a tenancy control plane. There are no users,
no roles, no per-key scopes. mem0's `server/` grows an alembic-managed
`users` + `api_keys` + `request_logs` schema because it is the backend of a
multi-tenant SaaS; copying that shape into a single-tenant self-hosted server
would import an authorization model nobody here needs and everybody here
would have to maintain. Named keys exist for attribution ("which caller"),
not for isolation.

Every route sits behind `require_api_key` with the rest of v1. Route-level
traffic shape, store sizes and key metadata are operational data about a
deployment — not something to hand out with the port.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from server.control import ApiKeyRecord, control_db_path, get_control_plane
from server.models import (
    ApiKeyList,
    ApiKeySummary,
    ConfigView,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    ErrorBody,
    RequestLogEntry,
    RequestLogList,
    StatsView,
    StoreStat,
)
from server.settings import get_settings

router = APIRouter(tags=["admin"], prefix="/v1/admin")

#: How many per-user stores the stats route reports individually. The total is
#: exact; this list is the "what is eating the disk" shortlist, and returning
#: one row per user would make the response grow with the deployment.
LARGEST_STORES_SHOWN = 10


def _summary(record: ApiKeyRecord) -> ApiKeySummary:
    return ApiKeySummary(
        id=record.id, name=record.name, prefix=record.prefix,
        created_at=record.created_at, last_used_at=record.last_used_at,
        revoked_at=record.revoked_at, revoked=record.revoked,
    )


# --- configuration ---------------------------------------------------------

@router.get("/config", response_model=ConfigView)
def get_config() -> ConfigView:
    """The effective configuration, secrets redacted to booleans.

    `*_set: true/false` rather than a masked value: a mask that preserves
    length or prefix leaks more than it appears to, and nobody debugging a
    config problem needs the secret itself — they need to know whether one is
    there at all.
    """
    settings = get_settings()
    control = get_control_plane()
    live_keys = [k for k in control.list_api_keys() if not k.revoked]
    contention_count, contention_last = control.lock_contention()
    return ConfigView(
        data_root=str(settings.data_root),
        store_cache_max=settings.store_cache_max,
        auth="disabled" if settings.auth_disabled else "enabled",
        api_key_set=bool(settings.api_key),
        named_keys_active=len(live_keys),
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model or None,
        llm_api_key_set=bool(settings.llm_api_key),
        llm_base_url=settings.llm_base_url or None,
        cors_origins=list(settings.cors_origins),
        request_log_max=settings.request_log_max,
        job_retention_max=settings.job_retention_max,
        lock_contention_count=contention_count,
        lock_contention_last=contention_last,
    )


# --- api keys --------------------------------------------------------------

@router.get("/keys", response_model=ApiKeyList)
def list_keys(
    include_revoked: bool = Query(
        default=True,
        description="Revoked keys stay listed by default — an ops view that "
                    "hides them cannot answer 'was this key ever used?'",
    ),
) -> ApiKeyList:
    records = get_control_plane().list_api_keys(include_revoked=include_revoked)
    return ApiKeyList(keys=[_summary(r) for r in records], total=len(records))


@router.post("/keys", response_model=CreateApiKeyResponse, status_code=201)
def create_key(request: CreateApiKeyRequest) -> CreateApiKeyResponse:
    """Mint a named key. The plaintext is returned exactly once."""
    record, plaintext = get_control_plane().create_api_key(request.name)
    return CreateApiKeyResponse(key=_summary(record), api_key=plaintext)


@router.delete(
    "/keys/{key_id}",
    response_model=ApiKeySummary,
    responses={404: {"model": ErrorBody}},
)
def revoke_key(key_id: str) -> ApiKeySummary:
    """Revoke a key. Idempotent, and the row is kept rather than deleted — a
    revoked key that vanishes takes its request history's only explanation
    with it.

    The bootstrap key (`SODAMEM_API_KEY`) is not revocable here by design: it
    is the way back in when every named key has been revoked.
    """
    record = get_control_plane().revoke_api_key(key_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"api key {key_id!r} not found")
    return _summary(record)


# --- request log -----------------------------------------------------------

@router.get("/requests", response_model=RequestLogList)
def list_requests(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> RequestLogList:
    """Most recent first. Bounded by `SODAMEM_REQUEST_LOG_MAX` rows total —
    this is a rolling window, not an archive, and `total` reports what is
    actually retained rather than what was ever served."""
    control = get_control_plane()
    return RequestLogList(
        requests=[
            RequestLogEntry(
                request_id=r.request_id, method=r.method, route=r.route,
                status_code=r.status_code, latency_ms=r.latency_ms,
                key_name=r.key_name, created_at=r.created_at,
            )
            for r in control.recent_requests(limit=limit, offset=offset)
        ],
        total=control.count_requests(),
        offset=offset,
        limit=limit,
    )


# --- stats -----------------------------------------------------------------

def _sqlite_bytes(db: Path) -> int:
    """A SQLite database is up to three files, not one.

    Caught in the browser, 0729: after the control plane moved to WAL this
    route reported 4.0 KB while the real footprint was 4.6 MB — the rows live
    in `-wal` until a checkpoint, and `stat()` on the main file alone misses
    them. An ops disk number that is wrong by three orders of magnitude is
    worse than no number, because someone will trust it.
    """
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db) + suffix)
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                # A file that vanished mid-walk (chroma compaction, a store
                # being closed) is not a reason to fail an ops readout.
                continue
    return total


@router.get("/stats", response_model=StatsView)
def get_stats() -> StatsView:
    """Disk and workload shape.

    Walks the data root, so its cost scales with the number of stored files —
    fine for an ops page someone opens, wrong for a polling loop. It is not
    cached: a stale disk number during an incident is worse than a slow one.
    """
    settings = get_settings()
    control = get_control_plane()
    root = Path(settings.data_root)

    stores: list[StoreStat] = []
    if root.is_dir():
        for entry in os.scandir(root):
            # `.control` holds the control database, not a user store. It is
            # excluded from the user count for the same reason it is named
            # with a leading dot: no user_id can ever resolve there.
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            stores.append(StoreStat(user_id=entry.name, bytes=_dir_bytes(Path(entry.path))))

    stores.sort(key=lambda s: s.bytes, reverse=True)
    control_path = control_db_path(settings.data_root)
    return StatsView(
        users=len(stores),
        stores_bytes=sum(s.bytes for s in stores),
        largest_stores=stores[:LARGEST_STORES_SHOWN],
        jobs_by_status=control.count_jobs_by_status(),
        requests_logged=control.count_requests(),
        control_db_bytes=_sqlite_bytes(control_path),
    )
