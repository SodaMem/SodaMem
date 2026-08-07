"""OpenAI-compatible provider: covers OpenAI, DeepSeek, Gemini (via compat
endpoint), or any custom base_url API.

Three behavioral guarantees this provider makes (spec §6.7 — no silent
degradation):
  - `__init__` used to swallow SDK import/init failures into
    `logger.warning(...)` + `self._client = None`; now raises `ProviderError`
    at construction time (see `sodamem.llm.anthropic` for the identical fix
    and the fuller rationale — `available` is dropped here for the same
    reason: a live instance can no longer exist in a half-built state).
  - The empty-content retry loop (a 200 response whose `content` — and
    `reasoning_content`, for thinking models — are both blank) used to
    `return text` after exhausting retries, where `text` could still be
    `""`: a silent empty answer with zero error signal reaching the caller.
    This was flagged separately from the constructor fix above (migration
    map §1.8, "新发现，非某行" row) because it's the same species of bug
    (§6.7 silent degradation) in a completely different code path — an
    exhausted-retries empty string doesn't go through `__init__` at all.
    It now raises `ProviderError(code=ErrorCode.PROVIDER_EMPTY_CONTENT)`.
  - `call()` is renamed `complete()`, and `acomplete()` (D8, spec §9.3) is
    added using the `openai` SDK's native `AsyncOpenAI` client — unlike
    Anthropic, OpenAI's SDK ships a real async client, so there's no
    `asyncio.to_thread` wrapper here.

`complete()`/`acomplete()` also fold in the HTTP-retry shape that lives in
`factory.py`: some OpenAI-compatible gateways false-positive a
400 "invalid_prompt" on an otherwise-valid long agent-trace prompt.
`complete()`/`acomplete()` retry that one specific case with
`factory.compact_messages_for_policy_retry()` before giving up. Every other
exception — including the ones the `openai` SDK's own client already
retried via `max_retries` (see `sodamem.llm.base.client_max_retries`) and
gave up on — is classified via `classify_provider_error` and raised
immediately; there is no value in a second blind retry loop on top of the
SDK's own.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from sodamem.errors import ErrorCode, ProviderError, classify_provider_error

from . import factory
from .base import LLMProvider, _UsageMixin, client_max_retries, client_timeout_seconds, empty_content_retries

logger = logging.getLogger(__name__)


def _extract_message_text(message: Any) -> str:
    """Pull text from an OpenAI-compatible message, tolerating thinking models.

    Non-thinking endpoints put the answer in `content`; thinking endpoints may
    leave `content` empty and put the answer in `reasoning_content`. Returns ""
    when neither carries text (caller decides whether to retry)."""
    content = getattr(message, "content", None)
    if content and content.strip():
        return content.strip()
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning and reasoning.strip():
        return reasoning.strip()
    return ""


class OpenAICompatibleProvider(_UsageMixin, LLMProvider):
    """OpenAI-compatible provider: covers OpenAI, DeepSeek, Gemini (via compat
    endpoint), or any custom base_url API."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Any = None,
        async_client: Any = None,
    ) -> None:
        self._model = model
        self._last_finish_reason: Optional[str] = None
        # Tri-state thinking control for DeepSeek V4 (None = don't send the param;
        # True = enable; False = explicitly disable). v4-flash DEFAULTS to thinking,
        # which emits reasoning tokens and breaks terse/JSON outputs — non-thinking
        # entries must send {"thinking": {"type": "disabled"}}.
        self._thinking: Optional[bool] = None
        self.max_output_tokens: Optional[int] = None
        self._init_usage()
        self._client = client
        self._async_client = async_client
        if self._client is not None and self._async_client is not None:
            return  # fully injected (tests) — no SDK import needed
        try:
            from openai import AsyncOpenAI, OpenAI
        except ImportError as e:
            # Missing SDK = install problem with a pip-installable fix, not a
            # provider outage (same contract as AnthropicProvider / the
            # OnnxMiniLmEmbedder chroma guard).
            raise ImportError(
                "OpenAICompatibleProvider requires the 'llm' extra: "
                "pip install 'sodamem[llm]'"
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
            if self._client is None:
                self._client = OpenAI(**kwargs)
            if self._async_client is None:
                self._async_client = AsyncOpenAI(**kwargs)
        except Exception as exc:
            raise classify_provider_error(exc) from exc

    def _build_messages(self, messages: list, system: str) -> list:
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        return full_messages

    def _request_kwargs(self, max_tokens: int, temperature: Optional[float]) -> dict:
        request_kwargs: dict = dict(model=self._model, max_tokens=max_tokens)
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        # DeepSeek V4 thinking control (extra_body). Omitted when _thinking is None.
        if self._thinking is not None:
            request_kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if self._thinking else "disabled"}
            }
        return request_kwargs

    def _empty_content_error(self, attempts: int) -> ProviderError:
        return ProviderError(
            f"empty content from {self._model} after {attempts} attempt(s)",
            code=ErrorCode.PROVIDER_EMPTY_CONTENT,
            details={"reason": "empty_content_exhausted_retries", "model": self._model},
        )

    def complete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        working_messages = self._build_messages(messages, system)
        request_kwargs = self._request_kwargs(max_tokens, temperature)
        for policy_attempt in range(factory.POLICY_RETRY_ATTEMPTS):
            try:
                return self._complete_with_empty_retry(working_messages, request_kwargs, usage_phase)
            except ProviderError:
                raise  # already classified (e.g. empty-content exhaustion) — don't re-wrap
            except Exception as exc:
                if factory.is_policy_retryable(exc) and policy_attempt + 1 < factory.POLICY_RETRY_ATTEMPTS:
                    working_messages = factory.compact_messages_for_policy_retry(working_messages)
                    continue
                raise classify_provider_error(exc) from exc
        raise AssertionError("unreachable: loop above always returns or raises")

    def _complete_with_empty_retry(self, messages: list, request_kwargs: dict, usage_phase: Optional[str]) -> str:
        attempts = 1 + empty_content_retries()
        text = ""
        for attempt in range(attempts):
            response = self._client.chat.completions.create(messages=messages, **request_kwargs)
            self._record_usage(usage_phase, response)
            choice = response.choices[0]
            # Expose truncation so callers can re-chunk (finish_reason == "length").
            self._last_finish_reason = getattr(choice, "finish_reason", None)
            text = _extract_message_text(choice.message)
            if text:
                return text
            if attempt + 1 < attempts:
                logger.warning(
                    "empty content from %s (attempt %d/%d), retrying",
                    self._model, attempt + 1, attempts,
                )
                time.sleep(0.5 * (attempt + 1))
        raise self._empty_content_error(attempts)

    async def acomplete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        working_messages = self._build_messages(messages, system)
        request_kwargs = self._request_kwargs(max_tokens, temperature)
        for policy_attempt in range(factory.POLICY_RETRY_ATTEMPTS):
            try:
                return await self._acomplete_with_empty_retry(working_messages, request_kwargs, usage_phase)
            except ProviderError:
                raise
            except Exception as exc:
                if factory.is_policy_retryable(exc) and policy_attempt + 1 < factory.POLICY_RETRY_ATTEMPTS:
                    working_messages = factory.compact_messages_for_policy_retry(working_messages)
                    continue
                raise classify_provider_error(exc) from exc
        raise AssertionError("unreachable: loop above always returns or raises")

    async def _acomplete_with_empty_retry(self, messages: list, request_kwargs: dict, usage_phase: Optional[str]) -> str:
        attempts = 1 + empty_content_retries()
        text = ""
        for attempt in range(attempts):
            response = await self._async_client.chat.completions.create(messages=messages, **request_kwargs)
            self._record_usage(usage_phase, response)
            choice = response.choices[0]
            self._last_finish_reason = getattr(choice, "finish_reason", None)
            text = _extract_message_text(choice.message)
            if text:
                return text
            if attempt + 1 < attempts:
                logger.warning(
                    "empty content from %s (attempt %d/%d), retrying",
                    self._model, attempt + 1, attempts,
                )
                await asyncio.sleep(0.5 * (attempt + 1))
        raise self._empty_content_error(attempts)
