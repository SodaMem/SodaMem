"""LangGraph / LangChain adapter.

`@tool`-decorated callables, ready to hand to `create_react_agent(tools=...)`
or bind to any LangChain model. Works for LangGraph and plain LangChain alike
— both consume the same `BaseTool`.
"""
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
    """Build LangChain tools bound to one user's memory.

    Requires `langchain-core` (the `[langgraph]` extra). Imported here rather
    than at module scope so `import adapters.langgraph` never drags LangChain
    into a process that only wanted to read the docstring.
    """
    try:
        from langchain_core.tools import tool
    except ImportError as e:
        raise ImportError(
            "LangGraph/LangChain adapter needs langchain-core: "
            "pip install 'sodamem[langgraph]'"
        ) from e

    tools = MemoryTools(memory=memory, user_id=user_id,
                        agent_id=agent_id, run_id=run_id,
                        project_id=project_id)

    @tool(description=SEARCH_DESCRIPTION)
    def search_memory(query: str, top_k: int = 10) -> list:
        return tools.search(query, top_k=top_k)

    @tool(description=CONTEXT_DESCRIPTION)
    def get_memory_context(query: str, token_budget: int = 2000) -> dict:
        return tools.get_context(query, token_budget=token_budget)

    @tool(description=ADD_DESCRIPTION)
    def add_memory(messages: list, session_id: str) -> dict:
        return tools.add(messages, session_id=session_id)

    return [search_memory, get_memory_context, add_memory]
