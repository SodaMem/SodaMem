"""Tests for sodamem.llm: the four §6.7 no-silent-degradation guarantees,
plus D8 async and the registry-based factory. Every test here is zero-network: real
`openai`/`anthropic` client construction is exercised only via injected fake
clients or a fake `sys.modules` entry, never a live SDK call.
"""
from __future__ import annotations

import sys
import types

import pytest

from sodamem.errors import ConfigError, ErrorCode, ProviderError
from sodamem.llm.anthropic import AnthropicProvider
from sodamem.llm.factory import create_provider, create_provider_for_model
from sodamem.llm.openai_compat import OpenAICompatibleProvider
from sodamem.llm.testing import EchoProvider, RaisingProvider

# ---------------------------------------------------------------------------
# Brief Step 3's required 4 tests (verbatim)
# ---------------------------------------------------------------------------


def test_create_provider_for_unknown_model_raises_config_error():
    with pytest.raises(ConfigError):
        create_provider_for_model("totally-made-up-model-id-xyz")


def test_raising_provider_raises_on_complete():
    p = RaisingProvider()
    with pytest.raises(AssertionError):
        p.complete(messages=[])


def test_echo_provider_is_deterministic():
    p = EchoProvider("hello")
    assert p.complete(messages=[]) == "hello"


@pytest.mark.asyncio
async def test_echo_provider_acomplete():
    p = EchoProvider("hi")
    assert await p.acomplete(messages=[]) == "hi"


# ---------------------------------------------------------------------------
# Fix 1: create_provider()'s if/elif chain -> registry + load_class.
# Not a behavior change (the old chain routed the same providers to the same
# classes) — this is a regression/equivalence test proving the refactor
# didn't silently drop or rewire a provider.
# ---------------------------------------------------------------------------


def _fake_anthropic_module():
    mod = types.ModuleType("anthropic")

    class _FakeAnthropicSDKClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    mod.Anthropic = _FakeAnthropicSDKClient
    return mod


def _fake_openai_module():
    mod = types.ModuleType("openai")

    class _FakeOpenAISDKClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    mod.OpenAI = _FakeOpenAISDKClient
    mod.AsyncOpenAI = _FakeOpenAISDKClient
    return mod


def test_create_provider_registry_dispatches_by_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module())
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module())
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    anthropic_provider = create_provider(provider="anthropic", model="claude-x")
    assert isinstance(anthropic_provider, AnthropicProvider)

    deepseek_provider = create_provider(provider="deepseek", model="deepseek-v4-flash")
    assert isinstance(deepseek_provider, OpenAICompatibleProvider)
    assert deepseek_provider._client.kwargs.get("base_url") == "https://api.deepseek.com"

    gemini_provider = create_provider(provider="gemini", model="gemini-2.0-flash")
    assert gemini_provider._client.kwargs.get("base_url") == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )

    # Unmapped provider name falls back to the openai-compatible class with a
    # caller-supplied base_url, same as the original if/elif chain's catch-all.
    custom_provider = create_provider(provider="some-custom-gateway", model="x", base_url="https://x.example")
    assert isinstance(custom_provider, OpenAICompatibleProvider)
    assert custom_provider._client.kwargs.get("base_url") == "https://x.example"


# ---------------------------------------------------------------------------
# Fix 2: create_provider_for_model() unknown model -> ConfigError (also
# covered by the design's required test above; this adds the registered-model
# success path plus the max_output_tokens/_thinking stamping behavior).
# ---------------------------------------------------------------------------


def test_create_provider_for_registered_model_stamps_output_ceiling(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module())
    prov = create_provider_for_model("deepseek-v4-flash")
    assert isinstance(prov, OpenAICompatibleProvider)
    assert prov.max_output_tokens == 393216
    assert prov._thinking is False


# ---------------------------------------------------------------------------
# Fix 3: constructor swallowing SDK import/init failures -> raise
# ProviderError. Forcing `sys.modules["anthropic"] = None` / `["openai"] =
# None` makes `import anthropic` / `import openai` raise ImportError
# regardless of whether the real package happens to be installed — this is
# the "ctor吞SDK异常" violation's exact trigger, and also the realistic
# zero-extras install case.
# ---------------------------------------------------------------------------


# Contract updated 0723: a missing SDK is an install problem with a
# pip-installable remediation, NOT a provider outage. Wrapping it as
# ProviderError(PROVIDER_UNAVAILABLE) — the original T4 behavior these tests
# pinned — buried the fix behind an outage label. (The source repo declared
# `anthropic` as a BASE dep, so "SDK missing" was unreachable there; the
# optional-SDK axis is SodaMem's own, and the error contract follows its own
# established pattern: OnnxMiniLmEmbedder's chroma guard.)

def test_anthropic_ctor_raises_importerror_with_extra_hint_when_sdk_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(ImportError, match=r"sodamem\[anthropic\]"):
        AnthropicProvider(model="claude-x")


def test_openai_compat_ctor_raises_importerror_with_extra_hint_when_sdk_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match=r"sodamem\[llm\]"):
        OpenAICompatibleProvider(model="x")


def test_injecting_a_client_skips_sdk_import_entirely():
    # No sys.modules manipulation needed: fully-injected clients never touch
    # `import anthropic`/`import openai`, so construction succeeds even with
    # neither SDK installed.
    provider = AnthropicProvider(model="claude-x", client=object())
    assert provider._client is not None


# ---------------------------------------------------------------------------
# Fix 4: empty-content retry exhaustion -> raise ProviderError(code=
# PROVIDER_EMPTY_CONTENT), not `return ""`.
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content="", reasoning_content=None):
        self.content = content
        self.reasoning_content = reasoning_content


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text="", usage=None):
        self.choices = [_FakeChoice(_FakeMessage(content=text))]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, responder):
        self._responder = responder

    def create(self, **kwargs):
        return self._responder(**kwargs)


class _FakeChat:
    def __init__(self, responder):
        self.completions = _FakeCompletions(responder)


class _FakeSyncClient:
    def __init__(self, responder):
        self.chat = _FakeChat(responder)


class _FakeAsyncCompletions:
    def __init__(self, responder):
        self._responder = responder

    async def create(self, **kwargs):
        return await self._responder(**kwargs)


class _FakeAsyncChat:
    def __init__(self, responder):
        self.completions = _FakeAsyncCompletions(responder)


class _FakeAsyncClient:
    def __init__(self, responder):
        self.chat = _FakeAsyncChat(responder)


def test_openai_compat_empty_content_exhausted_raises_provider_error(monkeypatch):
    monkeypatch.setattr("sodamem.llm.openai_compat.time.sleep", lambda *_: None)
    monkeypatch.setenv("MEMORY_LLM_EMPTY_RETRIES", "1")  # 2 attempts total, keep the test fast

    def always_empty(**kwargs):
        return _FakeResponse("")

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(always_empty),
        async_client=_FakeAsyncClient(always_empty),
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.complete(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.code == ErrorCode.PROVIDER_EMPTY_CONTENT
    assert exc_info.value.details["reason"] == "empty_content_exhausted_retries"


def test_openai_compat_retries_then_succeeds_on_empty_content(monkeypatch):
    monkeypatch.setattr("sodamem.llm.openai_compat.time.sleep", lambda *_: None)
    monkeypatch.setenv("MEMORY_LLM_EMPTY_RETRIES", "2")
    calls = {"n": 0}

    def empty_then_ok(**kwargs):
        calls["n"] += 1
        return _FakeResponse("" if calls["n"] < 2 else "finally")

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(empty_then_ok),
        async_client=_FakeAsyncClient(empty_then_ok),
    )
    assert provider.complete(messages=[{"role": "user", "content": "hi"}]) == "finally"
    assert calls["n"] == 2


def test_openai_compat_reads_reasoning_content_for_thinking_models():
    def responder(**kwargs):
        resp = _FakeResponse("")
        resp.choices[0].message = _FakeMessage(content="", reasoning_content="the real answer")
        return resp

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(responder),
        async_client=_FakeAsyncClient(responder),
    )
    assert provider.complete(messages=[{"role": "user", "content": "hi"}]) == "the real answer"


# ---------------------------------------------------------------------------
# Retry shape folded in from cli_harness (factory.py): terminal status ->
# immediate raise; 400 "invalid_prompt" -> compact messages and retry; other
# exceptions (e.g. rate limits) -> classified and raised (no blind retry loop
# on top of what the SDK's own max_retries already gave up on).
# ---------------------------------------------------------------------------


class _FakeAPIStatusError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def test_openai_compat_terminal_status_raises_immediately_no_retry(monkeypatch):
    monkeypatch.setattr("sodamem.llm.openai_compat.time.sleep", lambda *_: None)
    calls = {"n": 0}

    def unauthorized(**kwargs):
        calls["n"] += 1
        raise _FakeAPIStatusError("bad api key", status_code=401)

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(unauthorized),
        async_client=_FakeAsyncClient(unauthorized),
    )
    with pytest.raises(ProviderError):
        provider.complete(messages=[{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # no retry wasted on a config/auth error


def test_openai_compat_retries_compacted_messages_on_gateway_400(monkeypatch):
    monkeypatch.setattr("sodamem.llm.openai_compat.time.sleep", lambda *_: None)
    seen_messages = []

    def responder(**kwargs):
        seen_messages.append(kwargs["messages"])
        if len(seen_messages) == 1:
            raise _FakeAPIStatusError("invalid_prompt: request too long", status_code=400)
        return _FakeResponse("ok")

    long_observation = "OBSERVATION:" + ("x" * 5000)
    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(responder),
        async_client=_FakeAsyncClient(responder),
    )
    result = provider.complete(messages=[{"role": "user", "content": long_observation}])
    assert result == "ok"
    assert len(seen_messages) == 2
    # second attempt used the compacted (shorter) message, not the original
    assert len(seen_messages[1][0]["content"]) < len(seen_messages[0][0]["content"])


def test_openai_compat_call_time_rate_limit_is_classified(monkeypatch):
    monkeypatch.setattr("sodamem.llm.openai_compat.time.sleep", lambda *_: None)

    class RateLimitError(Exception):
        status_code = 429

    def rate_limited(**kwargs):
        raise RateLimitError("slow down")

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(rate_limited),
        async_client=_FakeAsyncClient(rate_limited),
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.complete(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.code == ErrorCode.PROVIDER_RATE_LIMITED


# ---------------------------------------------------------------------------
# D8: acomplete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_compat_acomplete_uses_async_client():
    async def async_responder(**kwargs):
        return _FakeResponse("async-ok")

    def sync_responder(**kwargs):
        raise AssertionError("acomplete() must not touch the sync client")

    provider = OpenAICompatibleProvider(
        model="x",
        client=_FakeSyncClient(sync_responder),
        async_client=_FakeAsyncClient(async_responder),
    )
    result = await provider.acomplete(messages=[{"role": "user", "content": "hi"}])
    assert result == "async-ok"


@pytest.mark.asyncio
async def test_anthropic_acomplete_wraps_sync_complete_via_to_thread():
    class _FakeAnthropicContent:
        def __init__(self, text):
            self.text = text

    class _FakeAnthropicResponse:
        def __init__(self, text):
            self.content = [_FakeAnthropicContent(text)]
            self.usage = None

    class _FakeMessages:
        def create(self, **kwargs):
            return _FakeAnthropicResponse("anthropic-ok")

    class _FakeAnthropicClient:
        def __init__(self):
            self.messages = _FakeMessages()

    provider = AnthropicProvider(model="claude-x", client=_FakeAnthropicClient())
    result = await provider.acomplete(messages=[{"role": "user", "content": "hi"}])
    assert result == "anthropic-ok"


def test_anthropic_call_time_timeout_is_classified():
    class APITimeoutError(Exception):
        pass

    class _FakeMessages:
        def create(self, **kwargs):
            raise APITimeoutError("slow")

    class _FakeAnthropicClient:
        def __init__(self):
            self.messages = _FakeMessages()

    provider = AnthropicProvider(model="claude-x", client=_FakeAnthropicClient())
    with pytest.raises(ProviderError) as exc_info:
        provider.complete(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.code == ErrorCode.PROVIDER_TIMEOUT


# ---------------------------------------------------------------------------
# create_provider_from_env: one spelling for the four LLM settings
# ---------------------------------------------------------------------------
# Until 0806 this function read MEMORY_PROVIDER / MEMORY_MODEL / MEMORY_API_KEY
# / MEMORY_BASE_URL — names inherited from the predecessor project, documented
# in no README, no .env.example and no Docker path, all of which said
# SODAMEM_LLM_*. It also defaulted to `anthropic` while server/settings.py
# defaulted to `openai`, so the same unset environment produced a different
# provider depending on which entry point you came in through.

_ENV_KEYS = (
    "SODAMEM_LLM_PROVIDER", "SODAMEM_LLM_MODEL",
    "SODAMEM_LLM_API_KEY", "SODAMEM_LLM_BASE_URL",
    "MEMORY_PROVIDER", "MEMORY_MODEL", "MEMORY_API_KEY", "MEMORY_BASE_URL",
)


def _clear_llm_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _captured(monkeypatch) -> dict:
    """Capture create_provider's kwargs instead of building a real client —
    the point under test is which env vars get read, not what they construct."""
    from sodamem.llm import factory

    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "create_provider", _fake)
    return seen


def test_from_env_reads_the_sodamem_llm_names(monkeypatch):
    from sodamem.llm.factory import create_provider_from_env

    _clear_llm_env(monkeypatch)
    seen = _captured(monkeypatch)
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("SODAMEM_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "k")
    monkeypatch.setenv("SODAMEM_LLM_BASE_URL", "https://x.example")

    create_provider_from_env()
    assert seen == {"provider": "deepseek", "model": "deepseek-v4-flash",
                    "api_key": "k", "base_url": "https://x.example"}


def test_from_env_still_honors_the_legacy_memory_names(monkeypatch):
    """Back-compat, not a second supported spelling: anyone whose scripts set
    MEMORY_* keeps working."""
    from sodamem.llm.factory import create_provider_from_env

    _clear_llm_env(monkeypatch)
    seen = _captured(monkeypatch)
    monkeypatch.setenv("MEMORY_PROVIDER", "anthropic")
    monkeypatch.setenv("MEMORY_MODEL", "claude-x")

    create_provider_from_env()
    assert seen["provider"] == "anthropic"
    assert seen["model"] == "claude-x"


def test_sodamem_name_wins_over_the_legacy_one(monkeypatch):
    from sodamem.llm.factory import create_provider_from_env

    _clear_llm_env(monkeypatch)
    seen = _captured(monkeypatch)
    monkeypatch.setenv("MEMORY_PROVIDER", "anthropic")
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "deepseek")

    create_provider_from_env()
    assert seen["provider"] == "deepseek"


def test_from_env_defaults_match_the_server(monkeypatch):
    """server/settings.py defaults llm_provider to "openai". Two doors into
    the same capability must not disagree about where an unset environment
    points."""
    from server.settings import Settings
    from sodamem.llm.factory import create_provider_from_env

    _clear_llm_env(monkeypatch)
    seen = _captured(monkeypatch)
    create_provider_from_env()
    assert seen["provider"] == Settings.model_fields["llm_provider"].default


def test_blank_env_value_counts_as_unset(monkeypatch):
    """`SODAMEM_LLM_MODEL=` in a .env file means "I didn't set this". Passing
    "" through as a model id would fail far away from its cause."""
    from sodamem.llm.factory import create_provider_from_env

    _clear_llm_env(monkeypatch)
    seen = _captured(monkeypatch)
    monkeypatch.setenv("SODAMEM_LLM_MODEL", "   ")

    create_provider_from_env()
    assert seen["model"] is None
