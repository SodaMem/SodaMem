"""query_plan organizer — the answer-shape / temporal-intent LLM classifier
that gates whether `value_board` / `enumeration_sweep` should run for a
question.

Renamed `_reader_query_plan` -> `reader_query_plan` — the leading
underscore is dropped because this is now
package-level public API (`sodamem.context.organizers.query_plan.
reader_query_plan`), not a same-file private helper.

NOT the retrieval `query_plan.py` deleted in Task 6:
that module classified TIME CONSTRAINTS for retrieval-side EvidenceBoard
filtering (`TimeConstraint` / `parse_time_constraint` / `classify_order_
intent`), judged production-dead and removed. This module classifies ANSWER
SHAPE for the two read-side organizers below it in the package. Same name,
unrelated concepts, purely historical coincidence — the port inventory calls
this out explicitly so Phase 1 review never conflates the two.

Transport change: the source's `chat: Callable[..., dict]` parameter (a
CLI-subprocess-shaped signature: model/api_key/base_url/tools/tool_choice/
usage_sink) is replaced by `provider: sodamem.llm.LLMProvider` — the
abstraction this repo already ported for every other LLM call site (Task 4).
`provider.complete(...)` returns `str`, not a `{"content": ...}` dict, so the
source's `message.get("content")` unwrap is gone; usage accounting is
`provider.usage_summary()` (push-model, per-provider), not the source's
pull-style `usage_sink` list threaded through `chat()`.

Failure handling (spec §6.7, "no silent failures"): a failed/unparseable LLM
call degrades to a `confidence=0.0` plan (both `_valid_value_board_plan` and
`_valid_enumeration_plan` already gate on a confidence floor, so this is not
a second, hidden failure path — every caller already has to check
confidence before trusting a plan). It is not, however, a BARE
`except Exception: return default` with only a log line: the caller can
also pass a `degraded: list` to receive a structured note, the same
"caller gets a first-class signal, not just a log line" contract
`sodamem.memory.retrieval.search` established for its own degradations.

T8 handoff item #2 (executed by Task 10, `sodamem.answer`): this originally
appended plain dicts (`{"code": ..., "message": ...}`) rather than importing
retrieval's `Degradation` dataclass — T8's own docstring reasoned that
`Degradation`/`DegradationCode` were scoped to `sodamem.memory.retrieval`
and left unifying this to whichever task actually needed a cross-layer
Degradation envelope. Task 10 is that task: `sodamem.answer.reader.
assemble_reader_context` (the only real caller of `reader_query_plan`)
already has to merge this module's degraded list with retrieval's own
`SearchResult.degraded` into one list the answer layer's caller can inspect
uniformly — two different Degradation-shaped types in that one list would
defeat the point. Layering allows this import (`context -> memory` is a
permitted direction per the `layers` contract; `sodamem.context` already
imports `sodamem.memory.retrieval.config.RetrievalConfig`-adjacent types
elsewhere in this package). Two new `DegradationCode` members
(`ORGANIZER_LLM_ERROR`/`ORGANIZER_UNPARSEABLE`) were added to `config.py`
rather than reusing a retrieval-route code — this failure domain is a
classifier LLM call, not a vector/bm25 route.
"""
from __future__ import annotations

import logging
from typing import Any

from sodamem.context.store import _json_object, _one_line
from sodamem.llm import LLMProvider
from sodamem.memory.retrieval.config import Degradation, DegradationCode
from sodamem.prompts.planner import QUERY_PLAN_PROMPT

logger = logging.getLogger(__name__)


def reader_query_plan(
    *,
    question: str,
    current_date: str,
    provider: LLMProvider,
    max_tokens: int = 500,
    degraded: list[Degradation] | None = None,
) -> dict[str, Any]:
    """LLM semantic plan for read-side scaffolding.

    Intentionally advisory and schema-validated. It does not mutate the
    store and it is not allowed to answer the user directly.
    """
    prompt = (
        QUERY_PLAN_PROMPT
        .replace("__QUESTION__", _one_line(question, 500))
        .replace("__CURRENT_DATE__", _one_line(current_date, 80) or "unknown")
    )
    try:
        content = provider.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
            usage_phase="answer_query_plan",
        )
    except Exception as e:  # noqa: BLE001 - degrades to confidence=0.0 (see module docstring), not swallowed
        logger.warning("reader query plan failed: %s", e)
        if degraded is not None:
            degraded.append(Degradation(code=DegradationCode.ORGANIZER_LLM_ERROR, message=str(e)))
        return {"confidence": 0.0, "error": str(e)}
    parsed = _json_object(content)
    if not isinstance(parsed, dict):
        # An unparseable (e.g. EMPTY — the known deepseek empty-content mode)
        # plan silently disables ALL reader organizers for the question if
        # nothing signals it; run-wide that would be a silent regression to
        # the pre-organizer stack. Logged loudly, and surfaced structurally
        # via `degraded` in addition to the confidence-gate every caller
        # already respects.
        logger.warning(
            "reader query plan unparseable (content=%r) — organizers skipped",
            str(content)[:200],
        )
        if degraded is not None:
            degraded.append(Degradation(
                code=DegradationCode.ORGANIZER_UNPARSEABLE, message=str(content)[:200],
            ))
        return {"confidence": 0.0, "error": "unparseable"}
    return parsed


__all__ = ["reader_query_plan"]
