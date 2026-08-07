"""sodamem.llm — provider abstraction (Anthropic, OpenAI-compatible: OpenAI/
DeepSeek/Gemini), factory + provider-error classification wiring, and test
doubles.

Split into `base`/`factory`/`anthropic`/`openai_compat`/`testing` along
spec §6's package-boundary rules. See each module's
docstring for its specific §6.7 no-silent-degradation guarantees.

`sodamem.memory.retrieval` must never import this package (I5, enforced by
the `retrieval-no-llm` import-linter contract in `.importlinter`).
"""
from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import LLMProvider
from .factory import create_provider, create_provider_from_env
from .openai_compat import OpenAICompatibleProvider
from .testing import EchoProvider, RaisingProvider

__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "create_provider",
    "create_provider_from_env",
    "RaisingProvider",
    "EchoProvider",
]
