"""OpenAI Agents SDK adapter — `function_tool`-wrapped callables."""
from __future__ import annotations

from sodamem import SodaMem

from adapters._core import (
    ADD_DESCRIPTION,
    CONTEXT_DESCRIPTION,
    SEARCH_DESCRIPTION,
    MemoryTools,
)


def create_memory_tools(memory: SodaMem, *, user_id: str,
                        agent_id: str = "", run_id: str = "",
                        project_id: str = "") -> list:
    """Tools for `Agent(tools=...)`. Requires the `[openai-agents]` extra."""
    try:
        from agents import function_tool
    except ImportError as e:
        raise ImportError(
            "OpenAI Agents SDK adapter needs openai-agents: "
            "pip install 'sodamem[openai-agents]'"
        ) from e

    tools = MemoryTools(memory=memory, user_id=user_id,
                        agent_id=agent_id, run_id=run_id,
                        project_id=project_id)

    @function_tool(description_override=SEARCH_DESCRIPTION)
    def search_memory(query: str, top_k: int = 10) -> list:
        return tools.search(query, top_k=top_k)

    @function_tool(description_override=CONTEXT_DESCRIPTION)
    def get_memory_context(query: str, token_budget: int = 2000) -> dict:
        return tools.get_context(query, token_budget=token_budget)

    @function_tool(description_override=ADD_DESCRIPTION)
    def add_memory(messages: list, session_id: str) -> dict:
        return tools.add(messages, session_id=session_id)

    return [search_memory, get_memory_context, add_memory]
