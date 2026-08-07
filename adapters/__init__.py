"""Framework adapters for SodaMem (PRD R2.8).

Four thin shells over one shared implementation (`_core.MemoryTools`):

    adapters.langgraph        LangGraph / LangChain  (@tool)
    adapters.crewai           CrewAI                 (BaseTool)
    adapters.openai_agents    OpenAI Agents SDK      (function_tool)
    (TypeScript: sdk-ts/src/vercel.ts for the Vercel AI SDK)

Each module imports its framework LAZILY, inside the factory function — so
`import adapters` costs nothing and a base install stays free of all four.
"""
from adapters._core import (
    ADD_DESCRIPTION,
    CONTEXT_DESCRIPTION,
    SEARCH_DESCRIPTION,
    MemoryTools,
)

__all__ = ["MemoryTools", "SEARCH_DESCRIPTION", "CONTEXT_DESCRIPTION", "ADD_DESCRIPTION"]
