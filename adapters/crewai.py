"""CrewAI adapter — `BaseTool` subclasses.

CrewAI wants classes with a pydantic args schema, not decorated functions, so
this shell is a little thicker than the other three. The behavior still lives
in `MemoryTools`; only the packaging differs.
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
    """Tools for `Agent(tools=...)`. Requires the `[crewai]` extra."""
    try:
        from crewai.tools import BaseTool
    except ImportError as e:
        raise ImportError(
            "CrewAI adapter needs crewai: pip install 'sodamem[crewai]'"
        ) from e
    from pydantic import BaseModel, Field

    bound = MemoryTools(memory=memory, user_id=user_id,
                        agent_id=agent_id, run_id=run_id,
                        project_id=project_id)

    class _SearchArgs(BaseModel):
        query: str = Field(description="What to look for in memory.")
        top_k: int = Field(default=10, description="Max records to return.")

    class _ContextArgs(BaseModel):
        query: str = Field(description="What the context should be about.")
        token_budget: int = Field(default=2000, description="Max tokens to render.")

    class _AddArgs(BaseModel):
        messages: list = Field(description="[{'role','content'}, ...] to store.")
        session_id: str = Field(description="Conversation this slice belongs to.")

    class SearchMemory(BaseTool):
        name: str = "search_memory"
        description: str = SEARCH_DESCRIPTION
        args_schema: type[BaseModel] = _SearchArgs

        def _run(self, query: str, top_k: int = 10) -> list:
            return bound.search(query, top_k=top_k)

    class GetMemoryContext(BaseTool):
        name: str = "get_memory_context"
        description: str = CONTEXT_DESCRIPTION
        args_schema: type[BaseModel] = _ContextArgs

        def _run(self, query: str, token_budget: int = 2000) -> dict:
            return bound.get_context(query, token_budget=token_budget)

    class AddMemory(BaseTool):
        name: str = "add_memory"
        description: str = ADD_DESCRIPTION
        args_schema: type[BaseModel] = _AddArgs

        def _run(self, messages: list, session_id: str) -> dict:
            return bound.add(messages, session_id=session_id)

    return [SearchMemory(), GetMemoryContext(), AddMemory()]
