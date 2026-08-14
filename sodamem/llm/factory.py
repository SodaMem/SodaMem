"""Provider factory + retry shape.

`create_provider`/`create_provider_for_model`/`create_provider_from_env`,
plus an HTTP-retry shape that used to live in a second, divergent copy inside
the benchmark harness. That copy carried two things the provider layer never
had — a terminal/transient HTTP status split and a long-prompt gateway-false-positive
compaction retry — folded in here so both live in one place instead of
drifting apart in two.

Two behavioral fixes carried out during this port (spec §6.7 — no silent
degradation), both of which the predecessor implementation got wrong:
  - `create_provider()`'s if/elif provider chain is replaced by a small
    dotted-path registry + `load_class()` (mem0's factory pattern, the I1
    reference implementation): 3 real lines of dispatch logic (`spec =
    _PROVIDER_REGISTRY.get(...)`, `cls = load_class(spec.class_path)`,
    `return cls(...)`) instead of four `if provider == X: return
    SomeProvider(...)` branches with duplicated api-key/base-url resolution
    baked into each one.
  - `create_provider_for_model()` used to fall back to "treat `name` as a
    bare deepseek id" with only a `logger.warning` when the model wasn't in
    the registry — a caller who mistyped a model name got a live API call
    against a nonexistent endpoint instead of an error. It now raises
    `ConfigError` immediately.
"""
from __future__ import annotations

import importlib
import os
from typing import Any, Optional

from sodamem.errors import ConfigError, ErrorCode

from .base import LLMProvider
from .registry_data import MODEL_REGISTRY

# Provider name constants
ANTHROPIC = "anthropic"
OPENAI = "openai"
GEMINI = "gemini"
DEEPSEEK = "deepseek"

# Gemini OpenAI-compatible endpoint
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# DeepSeek OpenAI-compatible endpoint
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Default models per provider
_DEFAULT_MODELS = {
    ANTHROPIC: "claude-haiku-4-5-20251001",
    OPENAI: "gpt-4o-mini",
    GEMINI: "gemini-2.0-flash",
    DEEPSEEK: "deepseek-v4-flash",
}


class _ProviderSpec:
    """One registry row: which class to build and how to resolve its
    api_key/base_url from env when the caller doesn't pass one explicitly."""

    __slots__ = ("class_path", "api_key_env", "default_base_url", "base_url_env")

    def __init__(
        self,
        class_path: str,
        api_key_env: str,
        default_base_url: Optional[str] = None,
        base_url_env: Optional[str] = None,
    ) -> None:
        self.class_path = class_path
        self.api_key_env = api_key_env
        self.default_base_url = default_base_url
        self.base_url_env = base_url_env


_PROVIDER_REGISTRY: dict[str, _ProviderSpec] = {
    ANTHROPIC: _ProviderSpec("sodamem.llm.anthropic.AnthropicProvider", "ANTHROPIC_API_KEY"),
    GEMINI: _ProviderSpec(
        "sodamem.llm.openai_compat.OpenAICompatibleProvider", "GEMINI_API_KEY",
        default_base_url=_GEMINI_BASE_URL,
    ),
    DEEPSEEK: _ProviderSpec(
        "sodamem.llm.openai_compat.OpenAICompatibleProvider", "DEEPSEEK_API_KEY",
        default_base_url=_DEEPSEEK_BASE_URL,
    ),
    OPENAI: _ProviderSpec(
        "sodamem.llm.openai_compat.OpenAICompatibleProvider", "OPENAI_API_KEY",
        base_url_env="OPENAI_BASE_URL",
    ),
}


def load_class(dotted_path: str) -> type:
    """mem0-style dotted-path class loader: only imports the module that
    actually implements the requested provider, on first use."""
    module_path, _, class_name = dotted_path.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def create_provider(
    provider: str = ANTHROPIC,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    client: Any = None,
) -> LLMProvider:
    """
    Factory that builds the right LLMProvider.

    provider: "anthropic" | "openai" | "gemini" | "deepseek" | anything else
              (falls back to the openai-compatible class with a custom
              base_url, same as the original if/elif chain's catch-all)
    model:    model id; defaults to provider's recommended model if None
    api_key:  key for the provider; falls back to env vars
    base_url: custom endpoint (for openai-compatible servers)
    client:   pre-built SDK client to inject (tests / back-compat)
    """
    provider = provider.lower()
    spec = _PROVIDER_REGISTRY.get(provider, _PROVIDER_REGISTRY[OPENAI])
    resolved_model = model or _DEFAULT_MODELS.get(provider, _DEFAULT_MODELS[OPENAI])
    resolved_key = api_key or (os.getenv(spec.api_key_env) if spec.api_key_env else None)
    resolved_base = base_url or spec.default_base_url or (
        os.getenv(spec.base_url_env) if spec.base_url_env else None
    )
    cls = load_class(spec.class_path)
    return cls(model=resolved_model, api_key=resolved_key, base_url=resolved_base, client=client)


def get_model_spec(name: str) -> Optional[dict]:
    return MODEL_REGISTRY.get(name)


def create_provider_for_model(name: str, api_key: Optional[str] = None) -> LLMProvider:
    """Build a provider from a registered model name (`registry_data.py`).

    Resolves provider/api_model/base_url from the registry and stamps the
    provider with `max_output_tokens` (the endpoint ceiling) so each stage
    can cap its request. Raises `ConfigError` when `name` is not registered
    — an earlier version silently treated an unknown name as a bare
    deepseek model id and let the API call fail (or worse, succeed against
    the wrong endpoint) instead of failing fast at the call site that has
    the actual typo.
    """
    spec = get_model_spec(name)
    if not spec:
        raise ConfigError(
            f"model '{name}' is not in the registry (sodamem.llm.registry_data.MODEL_REGISTRY)",
            code=ErrorCode.CONFIG_INVALID,
            details={"model": name},
        )
    prov = create_provider(
        provider=spec.get("provider", DEEPSEEK),
        model=spec.get("api_model", name),
        api_key=api_key,
        base_url=spec.get("base_url"),
    )
    prov.max_output_tokens = spec.get("max_output_tokens")
    # Send the explicit thinking toggle only for DeepSeek V4 entries that declare
    # one (key present). Other models leave it None so no extra_body is sent.
    if "thinking" in spec:
        prov._thinking = bool(spec.get("thinking"))
    return prov


#: The pre-0806 variable names, kept working and read only when the SODAMEM_
#: one is unset. They date from the predecessor project and were never
#: documented anywhere in this repo — meanwhile `.env.example`,
#: `server/settings.py` and the Docker path all say `SODAMEM_LLM_*`. Two
#: spellings for one setting is a support question that arrives forever; one
#: spelling plus a silent alias is a migration.
_ENV_ALIASES = {
    "SODAMEM_LLM_PROVIDER": "MEMORY_PROVIDER",
    "SODAMEM_LLM_MODEL": "MEMORY_MODEL",
    "SODAMEM_LLM_API_KEY": "MEMORY_API_KEY",
    "SODAMEM_LLM_BASE_URL": "MEMORY_BASE_URL",
}


def _env(name: str) -> str | None:
    """`name`, else its legacy alias, else None. An empty string counts as
    unset: `SODAMEM_LLM_MODEL=` in a .env file means "I didn't set this",
    and passing "" through as a model id would fail far from its cause."""
    for key in (name, _ENV_ALIASES[name]):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def create_provider_from_env() -> LLMProvider:
    """
    Build a provider entirely from environment variables — the same four the
    server reads, so a process embedding the library and a container running
    the service are configured identically.

    SODAMEM_LLM_PROVIDER = openai | anthropic | gemini | deepseek  (default: openai)
    SODAMEM_LLM_MODEL    = model id override
    SODAMEM_LLM_API_KEY  = api key override (falls back to provider-specific key)
    SODAMEM_LLM_BASE_URL = base url override (for openai-compatible endpoints)

    The legacy `MEMORY_*` spelling of each still works as a fallback.

    The default provider is `openai` to match `server/settings.py`. It was
    `anthropic` here while the server said `openai`, so the same unset
    environment produced different providers depending on which door you came
    in through — and this function has no in-tree callers to be broken by
    fixing that.
    """
    return create_provider(
        provider=_env("SODAMEM_LLM_PROVIDER") or OPENAI,
        model=_env("SODAMEM_LLM_MODEL"),
        api_key=_env("SODAMEM_LLM_API_KEY"),
        base_url=_env("SODAMEM_LLM_BASE_URL"),
    )


# ---------------------------------------------------------------------------
# Retry shape folded in from the benchmark harness's own chat wrapper.
# Consumed by openai_compat.py's complete()/acomplete(); not needed by
# anthropic.py (the Anthropic SDK's own client-side retries — via
# `client_max_retries()` — already cover its transient-error surface, and the
# gateway-compaction failure mode has never been observed against the
# Anthropic API).
# ---------------------------------------------------------------------------

# Mirrors the harness wrapper's `for attempt in range(3)`.
POLICY_RETRY_ATTEMPTS = 3

# 400/401/403/404: config/auth errors, ported from cli_harness._openai_chat's
# explicit terminal/transient split (cli_harness.py:487 `if exc.code in
# {400, 401, 403, 404}: raise`). Unlike cli_harness (raw urllib, no SDK-level
# retry of its own), openai_compat.py doesn't need a second explicit check
# for this: `is_policy_retryable()` below is False for all four, so
# openai_compat.py's except-block raises on first occurrence for exactly
# this set — retrying them would waste a call and hide the real problem.
# Everything else (429/5xx/timeouts/connection errors) is already retried by
# the openai SDK's own `max_retries` (see `client_max_retries()`) before an
# exception ever reaches our except-block.


def is_policy_retryable(exc: BaseException) -> bool:
    """True when `exc` looks like the OpenAI-SDK 400 some gateways raise as a
    false positive on long agent traces (a prompt-length/content-policy
    rejection of an otherwise-valid request), ported from cli_harness.
    _openai_chat's `exc.code == 400 and "invalid_prompt" in detail` branch
    (cli_harness.py:481). This is the one case worth an app-level retry —
    the fix is rewriting the prompt, not waiting and hoping."""
    status = getattr(exc, "status_code", None)
    if status != 400:
        return False
    body = str(getattr(exc, "message", None) or exc)
    return "invalid_prompt" in body


def compact_messages_for_policy_retry(messages: list[dict]) -> list[dict]:
    """Shrink prompt packaging for gateways that false-positive on long agent
    traces. Operates on OpenAI wire-format messages
    (`{"role": ..., "content": ...}` dicts), which is what openai_compat.py's
    `complete()`/`acomplete()` build before calling the SDK."""
    import re

    compact: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = str(msg.get("content") or "")
        if role == "system":
            content = re.sub(r"```.*?```", "[code block omitted]", content, flags=re.DOTALL)
            content = content[:3500]
        elif role == "user" and content.startswith("OBSERVATION:"):
            if "ORCHESTRATION STATE" in content:
                content = content[-5000:]
            else:
                content = content[:1800] + ("\n...[observation compacted for policy retry]" if len(content) > 1800 else "")
        elif len(content) > 5000:
            content = content[:5000] + "\n...[message compacted for policy retry]"
        content = content.replace("ip:port", "network endpoint")
        compact.append({"role": role, "content": content})
    return compact
