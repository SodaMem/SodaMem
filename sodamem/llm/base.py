"""LLM provider ABC + usage accounting + shared client-config knobs.

This layer began as a 475-line monolith holding one abstract class, two
concrete providers, a factory, and a model registry all in one module; it is
split along spec §6's package-boundary rules into
`base.py` (this file: the ABC + usage bookkeeping + env-driven client
knobs shared by every provider), `factory.py` (provider construction +
retry shape), `anthropic.py`/`openai_compat.py` (one concrete provider
each), `testing.py` (test doubles), and `registry_data.py` (the model
capability table, formerly a JSON file).

One interface change from the source: `LLMProvider.call()` is renamed
`complete()` here, to sit next to the new `acomplete()` (D8 — async support,
spec §9.3). `LLMProvider` is now an ABC: a provider that doesn't implement
both is a bug at class-definition time, not a `NotImplementedError` a
caller discovers at call time.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional
import os


def _env_number(names: tuple[str, ...], *, default, cast, minimum):
    """Read the first set variable in `names`, or return `default`.

    An UNSET variable falling back to a default is a default. A SET variable
    being ignored is a lie — and this used to tell it twice: an unparseable
    value was swallowed into the default, and an in-range-but-too-small value
    was clamped. Either way the operator typed a number, got a different one,
    and was told nothing. Both now raise, naming the variable, the value it
    was given, and what would be acceptable.
    """
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw == "":
            continue
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"{name}={raw!r} is not a valid {cast.__name__}; refusing to "
                f"silently fall back to {default}"
            ) from None
        if value < minimum:
            raise ValueError(
                f"{name}={raw!r} is below the minimum of {minimum}; refusing "
                "to silently clamp it"
            )
        return value
    return default


def client_timeout_seconds() -> float:
    return _env_number(
        ("MEMORY_LLM_TIMEOUT_SECONDS", "BENCHMARK_LLM_TIMEOUT_SECONDS"),
        default=60.0, cast=float, minimum=1.0,
    )


def client_max_retries() -> int:
    # SDK-level retries for transient HTTP/connection errors (with backoff).
    # Default 2: a single transient connection error used to fall straight
    # through to the lossy extractor fallback, causing run-to-run score jitter.
    return _env_number(
        ("MEMORY_LLM_MAX_RETRIES", "BENCHMARK_LLM_MAX_RETRIES"),
        default=2, cast=int, minimum=0,
    )


def empty_content_retries() -> int:
    # App-level retries when the API returns a 200 with empty `content` (the
    # SDK does not retry these). Thinking-capable models can return empty
    # content / put the text in `reasoning_content`; openai_compat.py reads
    # that first, then retries a few times if still empty.
    return _env_number(
        ("MEMORY_LLM_EMPTY_RETRIES",), default=2, cast=int, minimum=0,
    )


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    def complete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Synchronous single-turn completion. Returns the response text."""
        raise NotImplementedError

    @abstractmethod
    async def acomplete(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 2048,
        temperature: Optional[float] = None,
        usage_phase: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Async counterpart of `complete()`. Returns the response text."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True

    def usage_summary(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Usage accounting — ported verbatim (provider-agnostic bookkeeping, shared by
# both concrete providers via `_UsageMixin`).
# ---------------------------------------------------------------------------

def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _usage_numbers(usage: Any) -> dict:
    if usage is None:
        return {}
    prompt_tokens = _value(usage, "prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = _value(usage, "input_tokens", 0)
    completion_tokens = _value(usage, "completion_tokens")
    if completion_tokens is None:
        completion_tokens = _value(usage, "output_tokens", 0)
    total_tokens = _value(usage, "total_tokens")
    if total_tokens is None:
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)

    prompt_details = _value(usage, "prompt_tokens_details") or _value(usage, "input_token_details") or {}
    cached_input_tokens = (
        _value(prompt_details, "cached_tokens")
        or _value(prompt_details, "cache_read_input_tokens")
        or _value(usage, "cache_read_input_tokens")
        # DeepSeek reports cache hits at the TOP level of usage, not inside
        # prompt_tokens_details — without this fallback the cache-layout
        # arm's one deliverable (billed-token reduction) is unmeasurable
        # against the provider it targets.
        or _value(usage, "prompt_cache_hit_tokens")
        or 0
    )
    cache_creation_input_tokens = (
        _value(prompt_details, "cache_creation_input_tokens")
        or _value(usage, "cache_creation_input_tokens")
        or 0
    )
    return {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "cached_input_tokens": int(cached_input_tokens or 0),
        "cache_creation_input_tokens": int(cache_creation_input_tokens or 0),
    }


def new_usage_summary() -> dict:
    return {
        "calls": 0,
        "calls_with_usage": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "by_phase": {},
        "calls_detail": [],
        # Distinct models that actually answered. A list, not a set: this
        # summary is JSON-serialised into every run artifact.
        "served_models": [],
    }


def record_response_usage(summary: dict, *, phase: Optional[str], model: str, response: Any) -> dict:
    phase_name = phase or "default"
    usage = _usage_numbers(_value(response, "usage"))
    usage_available = bool(usage)

    summary.setdefault("calls", 0)
    summary.setdefault("calls_with_usage", 0)
    summary.setdefault("prompt_tokens", 0)
    summary.setdefault("completion_tokens", 0)
    summary.setdefault("total_tokens", 0)
    summary.setdefault("cached_input_tokens", 0)
    summary.setdefault("cache_creation_input_tokens", 0)
    summary.setdefault("by_phase", {})
    summary.setdefault("calls_detail", [])
    summary.setdefault("served_models", [])

    phase_stats = summary["by_phase"].setdefault(phase_name, {
        "calls": 0,
        "calls_with_usage": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    })
    summary["calls"] += 1
    phase_stats["calls"] += 1
    if usage_available:
        summary["calls_with_usage"] += 1
        phase_stats["calls_with_usage"] += 1
    # `model` is what we asked for; `served_model` is what answered. They can
    # differ without any error surfacing — a retired model id silently routes
    # to its successor — and only the response body knows.
    served_model = _value(response, "model")
    detail = {
        "phase": phase_name,
        "model": model,
        "served_model": served_model,
        "usage_available": usage_available,
        **usage,
    }
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_input_tokens", "cache_creation_input_tokens"):
        value = int(usage.get(key, 0))
        summary[key] += value
        phase_stats[key] += value
    if served_model and served_model not in summary["served_models"]:
        summary["served_models"].append(served_model)
    summary["calls_detail"].append(detail)
    return detail


class _UsageMixin:
    def _init_usage(self) -> None:
        self._usage = new_usage_summary()

    def _record_usage(self, phase: Optional[str], response: Any) -> None:
        record_response_usage(self._usage, phase=phase, model=self._model, response=response)

    def usage_summary(self) -> dict:
        return self._usage


def merge_usage_summaries(*summaries: Optional[dict]) -> dict:
    """Merge nested numeric LLM usage summaries."""
    merged: dict = {}

    def add(dst: dict, src: dict) -> None:
        for key, value in src.items():
            if isinstance(value, dict):
                child = dst.setdefault(key, {})
                if isinstance(child, dict):
                    add(child, value)
                else:
                    dst[key] = value
            elif isinstance(value, (int, float)):
                dst[key] = dst.get(key, 0) + value
            elif isinstance(value, list):
                existing = dst.setdefault(key, [])
                if isinstance(existing, list):
                    if key == "served_models":
                        # Set-by-meaning, order-preserving. Concatenating would
                        # render one model used in two phases as two entries —
                        # indistinguishable from the mid-run swap this field
                        # exists to catch. `calls_detail` still concatenates:
                        # there every call is genuinely its own row.
                        existing.extend(v for v in value if v not in existing)
                    else:
                        existing.extend(value)
                else:
                    dst[key] = value
            elif key not in dst:
                dst[key] = value

    for summary in summaries:
        if isinstance(summary, dict):
            add(merged, summary)
    return merged
