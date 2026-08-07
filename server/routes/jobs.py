"""GET /v1/jobs/{job_id} (PRD M1 R1.4).

Job records live in the control plane (ADR 0001), so a 404 here now means
exactly one thing: no job with that id was ever submitted. It used to also
mean "submitted, but the process restarted" — the same status code for
"never existed" and "your data may or may not have landed", which is the
silent-failure shape this project rejects everywhere else.

A job interrupted by a restart is reported as `failed` with a
`server_restarted` error, not as a permanently-`running` job nobody is
working on (`ControlPlane.reconcile_orphaned_jobs`, called at startup).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.jobs import get_job_runner
from server.models import ErrorBody, Job

router = APIRouter(tags=["jobs"])


@router.get(
    "/v1/jobs/{job_id}",
    response_model=Job,
    responses={404: {"model": ErrorBody}},
)
def get_job(job_id: str) -> Job:
    record = get_job_runner().get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return Job(
        job_id=record.job_id,
        status=record.status,
        kind=record.kind,
        user_id=record.user_id,
        created_at=record.created_at,
        finished_at=record.finished_at,
        result=record.result,
        error=record.error,
    )
