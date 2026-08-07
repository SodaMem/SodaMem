"""POST /v1/maintenance/dream — run the D36 profile rebuild for one user.

`sodamem.memory.dreaming` contains no scheduler on purpose: when to dream is
a deployment decision (cron, a k8s CronJob, a front-end firing at end of
session), not a library one. But "the operator calls it" was never true for
the one deployment this project actually ships. `SodaMem.dream()` needs the
store in-process, and the server holds an exclusive lock on its data root, so
a second process cannot have it. The primitive was documented as
operator-driven while being unreachable by any operator.

This route is that hook, and nothing more. The server still decides nothing
about *when*; it only makes *how* possible from outside the container.
"""
from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from sodamem.memory.dreaming import DreamingOptions, run_dream

from server.jobs import get_job_runner
from server.models import DreamAccepted, DreamRequest, DreamResult
from server.routes._scope import reject_unsupported_scope
from server.stores import get_store_manager

router = APIRouter(tags=["maintenance"])


def _run(mem, request: DreamRequest) -> dict:
    result = run_dream(
        store=mem.store,
        user_id=request.user_id,
        options=DreamingOptions(
            batch_size=request.batch_size,
            max_processing_time_sec=request.max_processing_time_sec,
            priority_order=request.priority_order,
        ),
    )
    return result.to_dict()


@router.post(
    "/v1/maintenance/dream",
    response_model=DreamResult,
    responses={202: {"model": DreamAccepted}},
)
def dream(request: DreamRequest):
    # Dreaming rebuilds profiles for the whole user; it reads and rewrites
    # facts regardless of which agent or run contributed them, so a scope key
    # here would be silently ignored rather than honored.
    reject_unsupported_scope(request.agent_id, request.run_id, request.project_id)

    # Borrowed, not `with lease(...)`: in async mode the job outlives the
    # request and keeps using this store, so the borrow has to be handed to
    # the job. Same shape as POST /v1/memories, and for the same reason.
    stores = get_store_manager()
    mem = stores.get(request.user_id)

    if request.async_mode:
        def _dream_then_release() -> dict:
            try:
                return _run(mem, request)
            finally:
                stores.release(request.user_id)

        try:
            job = get_job_runner().submit("dream", request.user_id, _dream_then_release)
        except BaseException:
            # Never submitted, so nothing will ever release it.
            stores.release(request.user_id)
            raise
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=DreamAccepted(job_id=job.job_id).model_dump(),
        )

    try:
        return DreamResult(**_run(mem, request))
    finally:
        stores.release(request.user_id)
