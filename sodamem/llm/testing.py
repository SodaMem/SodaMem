# sodamem/llm/testing.py
"""Test doubles for sodamem.llm. RaisingProvider is the canonical "an LLM call
here is a bug" probe, used by I5's zero-LLM gate and by any test asserting a
code path must never generate.

A shared provider test double. Before this existed, every test that needed
one hand-rolled its own
`MagicMock(spec=LLMProvider)` (see `tests/conftest.py:30-32`) or, in the I5
gate scaffold Phase 0 already committed to this repo
(`tests/gates/test_i5_zero_llm_zero_egress.py`), a local `_RaisingProvider`
class duplicating exactly this one. `RaisingProvider` here is the canonical
replacement both are meant to converge on (Task 6 rewires the I5 gate fixture
to import this one instead of its local copy).
"""
from __future__ import annotations

from .base import LLMProvider


class RaisingProvider(LLMProvider):
    def complete(self, **kw):
        raise AssertionError("RaisingProvider.complete() was called — this code path must be LLM-free")

    async def acomplete(self, **kw):
        raise AssertionError("RaisingProvider.acomplete() was called — this code path must be LLM-free")


class EchoProvider(LLMProvider):
    """Deterministic stand-in for tests that need a provider to return
    something specific without a network call."""

    def __init__(self, response: str = "") -> None:
        self._response = response

    def complete(self, **kw) -> str:
        return self._response

    async def acomplete(self, **kw) -> str:
        return self._response


class ScriptedProvider(LLMProvider):
    """Test double that returns a scripted sequence of responses, one per
    `complete()`/`acomplete()` call — for guardian tests that need a
    multi-call pipeline stage (e.g. `sodamem.memory.ingest`'s per-window
    extraction, or Task 10's planner/reader loop) to see specific, pre-set
    output across MULTIPLE calls within one test, which `EchoProvider`'s
    single fixed response can't express. Raises `AssertionError` (not a
    silent empty string) if called more times than scripted — an exhausted
    script is a test-authoring bug, not a real degraded-provider case.

    Added by Task 5 (`sodamem/memory/ingest`'s guardian tests need to script
    per-extraction-window JSON payloads); the docstring on
    `sodamem/llm/testing.py`'s module intro calls this file "the canonical
    replacement" test doubles are meant to converge on, so this lives beside
    `EchoProvider`/`RaisingProvider` rather than as a private copy inside
    `tests/test_ingest_*.py`."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages, system: str = "", max_tokens: int = 2048,
                 temperature=None, usage_phase=None, **kwargs) -> str:
        self.calls.append({
            "messages": messages, "system": system, "max_tokens": max_tokens,
            "temperature": temperature, "usage_phase": usage_phase,
        })
        if not self._responses:
            raise AssertionError(
                f"ScriptedProvider exhausted after {len(self.calls)} call(s) — "
                "no more scripted responses"
            )
        return self._responses.pop(0)

    async def acomplete(self, **kw) -> str:
        return self.complete(**kw)
