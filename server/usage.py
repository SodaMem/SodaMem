"""Process-wide LLM token accounting (PRD R2.10).

Nothing here computes anything new. `IngestResult.usage` has carried
cumulative token counts since the ingest client was ported, and `/v1/answer`
already hands its provider's summary back to the caller — but `_run_ingest`
threw its copy away, so no operator could ever see what a deployment spent.

Worth more than ops hygiene: PRD §1.3 records that no competitor publishes
write-path cost, and §9.2 measured ours (output is 65% of the bill). Being the
only memory service that can answer "what did that ingest cost me" is a
differentiator we had already paid for and were not collecting.

Shape mirrors `server/metrics.py` on purpose — an untrafficked deployment
reports an EMPTY total rather than zeros, because zeros read as "this is
free".
"""
from __future__ import annotations

import threading
from typing import Any

from sodamem.llm.base import merge_usage_summaries


class UsageRegistry:
    """Additive token counters, split by operation.

    Ingest and answer have completely different cost profiles (ingest is
    output-heavy, answer is input-heavy), so collapsing them into one number
    would hide the only comparison anyone actually wants.
    """

    def __init__(self) -> None:
        self._by_op: dict[str, dict[str, Any]] = {}
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, operation: str, usage: dict[str, Any] | None) -> None:
        """Fold one call's usage in. A falsy summary is IGNORED rather than
        registered as a zero-cost call: a zero-network ingest (no LLM
        configured) reports `{}`, and recording it would invent an operation
        whose average cost is zero — i.e. "ingest is free"."""
        if not usage:
            return
        with self._lock:
            # merge_usage_summaries is the same provider-agnostic merge the
            # LLM layer uses for its own nested per-model breakdowns; rolling
            # our own would flatten only the top level and silently drop
            # everything underneath.
            self._by_op[operation] = merge_usage_summaries(
                self._by_op.get(operation), usage
            )
            self._calls[operation] = self._calls.get(operation, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            by_op = {
                op: {**counts, "calls": self._calls.get(op, 0)}
                for op, counts in self._by_op.items()
            }
            total = merge_usage_summaries(*self._by_op.values())
        return {"by_operation": by_op, "total": total}

    def reset(self) -> None:
        with self._lock:
            self._by_op.clear()
            self._calls.clear()


_REGISTRY: UsageRegistry | None = None


def get_usage_registry() -> UsageRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = UsageRegistry()
    return _REGISTRY


def reset_usage_registry() -> None:
    global _REGISTRY
    _REGISTRY = None
