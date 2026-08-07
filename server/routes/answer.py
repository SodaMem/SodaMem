"""POST /v1/answer (PRD M2 R2.1) — end-to-end question answering.

The differentiating route. mem0 and Graphiti both stop at search and hand the
final LLM synthesis back to the caller; Zep has an equivalent only inside its
closed cloud. Until now our own answer path was reachable from the library
(`SodaMem.answer()`) but not over HTTP, so a service user got strictly less
than a library user — the exact capability gap this endpoint closes.

Uses the THREE-STAGE api (`run_planner_loop` -> `assemble_reader_context` ->
`reader_answer`) rather than the one-shot `SodaMem.answer()` facade, because
the facade returns only `Answer` and discards the `AutonomousAgentResult`
that carries `termination`, the step count and the insufficiency flag. For a
product endpoint those are not diagnostics-for-us, they are the caller's only
way to answer "why did it give up?" — a black-box answer string is not
operable.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, status

from server.jobs import get_job_runner
from server.models import AnswerAccepted, AnswerRequest, AnswerResponse
from server.stores import build_provider, get_store_manager

from ._scope import reject_unsupported_scope

def _record_usage(summary: dict) -> dict:
    """Fold this answer's spend into the process-wide total (R2.10) and hand
    the same dict back to the caller unchanged."""
    from server.usage import get_usage_registry
    get_usage_registry().record("answer", summary)
    return summary


router = APIRouter(tags=["answer"])


def _run_answer(request: AnswerRequest) -> dict:
    """The whole three-stage pipeline, as a plain function so it can be run
    inline (async_mode=false) or handed to the job runner unchanged."""
    from sodamem.answer import (
        PlannerConfig,
        ReaderConfig,
        assemble_reader_context,
        reader_answer,
        run_planner_loop,
    )
    from sodamem.tools import MemoryTool

    provider = build_provider()
    if provider is None:
        # Loud, not silent: an answer endpoint that returns "" because nobody
        # configured credentials is worse than one that refuses.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "/v1/answer needs an LLM provider — set SODAMEM_LLM_API_KEY "
                "(and SODAMEM_LLM_MODEL / SODAMEM_LLM_BASE_URL as needed). "
                "Retrieval routes (/v1/search, /v1/context) keep working "
                "without one; only this route calls an LLM."
            ),
        )

    mem = get_store_manager().get(request.user_id)
    tool = MemoryTool(mem, user_id=request.user_id)
    current_date = request.current_date or date.today().isoformat()
    planner_config = PlannerConfig(
        max_steps=request.max_steps,
        planner_max_tokens=request.planner_max_tokens,
        temperature=request.temperature,
    )
    reader_config = ReaderConfig(
        max_tokens=request.reader_max_tokens, temperature=request.temperature
    )

    loop_result = run_planner_loop(
        request.question, current_date=current_date, tools=tool,
        provider=provider, config=planner_config,
    )
    context = assemble_reader_context(
        loop_result.evidence, loop_result.selected_evidence_ids, request.question,
        current_date=current_date, provider=provider, config=reader_config,
        insufficient=loop_result.insufficient,
        missing_information=loop_result.missing_information,
        planner_claims=loop_result.planner_claims,
        planner_conflicts=loop_result.planner_conflicts,
    )
    result = reader_answer(
        request.question, context, current_date=current_date,
        provider=provider, config=reader_config,
    )
    return {
        "answer": result.text,
        "citations": list(result.citations),
        "termination": loop_result.termination,
        "planner_steps": len(loop_result.planner_trace),
        "insufficient": bool(loop_result.insufficient),
        "missing_information": loop_result.missing_information or "",
        "usage": _record_usage(provider.usage_summary()),
    }


@router.post(
    "/v1/answer",
    response_model=None,
    responses={
        200: {"model": AnswerResponse},
        202: {"model": AnswerAccepted},
        503: {"description": "no LLM provider configured"},
    },
)
def answer_question(request: AnswerRequest):
    reject_unsupported_scope(request.agent_id, request.run_id, request.project_id)

    if request.async_mode:
        # Validate the provider BEFORE accepting the job: a 202 followed by a
        # job that was always going to fail on missing credentials is a worse
        # error surface than an immediate 503.
        if build_provider() is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="/v1/answer needs an LLM provider — set SODAMEM_LLM_API_KEY",
            )
        job = get_job_runner().submit(
            "answer", request.user_id, lambda: _run_answer(request)
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=AnswerAccepted(job_id=job.job_id).model_dump(),
        )

    return AnswerResponse(**_run_answer(request))
