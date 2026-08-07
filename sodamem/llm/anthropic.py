"""Anthropic Claude provider.

Two behavioral guarantees carried out
during this port (spec §6.7 — no silent degradation), flagged in the
two defects carried over from the predecessor implementation:
  - `__init__` used to swallow SDK import/init failures into
    `logger.warning(...)` + `self._client = None`, surfaced only through
    `.available` if a caller happened to check it before the first `.call()`.
    It now raises `ProviderError` (via `classify_provider_error`) at
    construction time — a provider that can't be built is a bug the caller
    must see immediately, not a landmine that detonates on the next
    completion request. One consequence: `available` no longer has any
    reachable False state (a live instance only exists if construction
    succeeded), so this class doesn't override the base class's trivial
    `available -> True` — overriding it here would just be a special case
    dressed up as an invariant.
  - `call()` is renamed `complete()` to line up with the new `acomplete()`
    (D8, spec §9.3). `acomplete()` wraps the sync SDK call in
    `asyncio.to_thread` rather than adopting the `anthropic` SDK's separate
    `AsyncAnthropic` client — spec §9.3's evidence is that async is the only
    thing this port needs (no tool-calling, no streaming), and
    `asyncio.to_thread` gets there without doubling the SDK client surface
    this module has to construct, inject for tests, and classify errors for.

Call-time SDK exceptions (rate limits, timeouts, etc. raised by
`self._client.messages.create(...)`) are also routed through
`classify_provider_error` — the Phase 0 classifier's rate-limit/timeout name
matching mostly exists to be useful *here*, at the point an API call
actually fails, not just at construction time.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from sodamem.errors import classify_provider_error

from .base import LLMProvider, _UsageMixin, client_max_retries, client_timeout_seconds


class AnthropicProvider(_UsageMixin, LLMProvider):
    """Anthropic Claude provider."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self._model = model
        self._init_usage()
        self.max_output_tokens: Optional[int] = None
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as e:
            # Same pattern as OnnxMiniLmEmbedder's chroma guard: a missing SDK
            # is an install problem with a pip-installable remediation, NOT a
            # provider outage — classify_provider_error would mislabel it
            # PROVIDER_UNAVAILABLE and bury the fix.
            raise ImportError(
                "AnthropicProvider requires the 'anthropic' extra: "
                "pip install 'sodamem[anthropic]'"
            ) from e
        try:
            kwargs: dict = {
                "timeout": client_timeout_seconds(),
                "max_retries": client_max_retries(),
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            self._client = anthropic.Anthropic(**kwargs)
        except Exception as exc:
            raise classify_provider_error(exc) from exc

    def complete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        request: dict = dict(model=self._model, max_tokens=max_tokens, messages=messages)
        if system:
            request["system"] = system
        if temperature is not None:
            request["temperature"] = temperature
        try:
            response = self._client.messages.create(**request)
        except Exception as exc:
            raise classify_provider_error(exc) from exc
        self._record_usage(usage_phase, response)
        return response.content[0].text.strip()

    async def acomplete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        return await asyncio.to_thread(
            self.complete,
            messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            usage_phase=usage_phase,
            **kwargs,
        )
