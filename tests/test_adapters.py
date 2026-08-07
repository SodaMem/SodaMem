"""Framework adapters (PRD R2.8).

The shared layer (`adapters._core.MemoryTools`) is tested WITHOUT any
framework installed — that is where the behavior lives, and it must not need
LangChain present to be verified. Each framework shell then gets one guarded
test proving it produces that framework's native tool objects with the right
names and schemas.
"""
from __future__ import annotations

import json

import pytest

from adapters import MemoryTools
from sodamem import SodaMem
from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.extractor import FactEventExtractorV2

_FACT = {
    "kind": "fact", "predicate_raw": "User flew United to Boston",
    "predicate_canonical": "travel_by_airline", "event_type": "flight",
    "modality": "past_event", "occurred_start": "2023-06-10",
    "entity_roles": {"subject": "user", "airline": "United Airlines"},
    "source_span_ids": ["irrelevant"],
    "support_text": "I flew United to Boston last week.",
}


@pytest.fixture
def bound(tmp_path):
    d = tmp_path / "u1"
    d.mkdir(parents=True)
    mem = SodaMem.open(
        d, extractor=FactEventExtractorV2(ScriptedProvider([json.dumps([_FACT])])))
    mem.ingest([{"role": "user", "content": _FACT["support_text"]}],
               user_id="u1", session_id="s0", session_time="2023/06/15 (Thu) 10:00")
    return MemoryTools(memory=mem, user_id="u1")


def test_search_returns_records(bound):
    hits = bound.search("United Boston flight")
    assert hits and hits[0]["predicate_canonical"] == "travel_by_airline"


def test_get_context_returns_prompt_ready_text_with_matching_citations(bound):
    ctx = bound.get_context("United Boston flight", token_budget=500)
    assert ctx["text"], "context block should not be empty"
    assert ctx["evidence_count"] == len(ctx["citations"])
    for cid in ctx["citations"]:
        assert cid in ctx["text"], "a citation must name evidence the text contains"


def test_user_id_is_bound_not_a_tool_argument():
    """The model chooses tool ARGUMENTS. A user_id it can choose is a user_id
    it can hallucinate — cross-tenant reads by typo. Scope stays with the
    application, so it must not appear in any tool's signature."""
    import inspect
    for name in ("search", "get_context", "add"):
        params = inspect.signature(getattr(MemoryTools, name)).parameters
        assert "user_id" not in params, f"{name} must not take user_id"


def test_scope_is_bound_through_to_retrieval(tmp_path):
    """agent_id set on the tools must actually narrow, not just be stored —
    and the narrowing must stop exactly where the feature says it stops."""
    d = tmp_path / "u2"
    d.mkdir(parents=True)
    mem = SodaMem.open(
        d, extractor=FactEventExtractorV2(ScriptedProvider([json.dumps([_FACT])])))
    mem.ingest([{"role": "user", "content": _FACT["support_text"]}],
               user_id="u2", session_id="s0", session_time="2023/06/15 (Thu) 10:00",
               agent_id="agent_a")
    def _facts(rows):
        return [r for r in rows if r.get("fact_id")]

    assert _facts(MemoryTools(memory=mem, user_id="u2", agent_id="agent_a").search("United"))
    # agent_b sees NOTHING from agent_a's scoped ingest — not the facts, and
    # (as of R1.2b) not the raw conversation turns either.
    #
    # This assertion used to read the other way, pinning the old limit: scope
    # lived only in fact metadata, `raw_turns` had no scope of their own, and
    # raw-recall rows therefore passed every scoped query. That was defensible
    # while scope meant "which agent contributed this", and indefensible the
    # moment `project_id` arrived — raw turns are the bulk of a coding-agent
    # session, so narrowing to one repo would still have surfaced another
    # repo's conversation text. Scope is now recorded once per session
    # (session_scope), which fact cards and raw-turn cards resolve through
    # alike. Unstamped still matches everything; what changed is that a
    # scoped ingest no longer leaves half its output unstamped.
    assert not MemoryTools(memory=mem, user_id="u2", agent_id="agent_b").search("United")


# --- framework shells -------------------------------------------------------

def test_langgraph_adapter_builds_native_tools(bound):
    pytest.importorskip("langchain_core", reason="needs the [langgraph] extra")
    from adapters.langgraph import create_memory_tools
    tools = create_memory_tools(bound.memory, user_id="u1")
    assert [t.name for t in tools] == ["search_memory", "get_memory_context", "add_memory"]
    assert set(tools[0].args) == {"query", "top_k"}
    assert set(tools[1].args) == {"query", "token_budget"}
    # Round-trip through the framework's own invocation path, not our function.
    out = tools[1].invoke({"query": "United Boston flight", "token_budget": 500})
    assert out["text"] and out["evidence_count"] == len(out["citations"])


def test_crewai_adapter_builds_native_tools(bound):
    """Same standard as the LangGraph test above: names, the schema an agent
    actually sees, and a round-trip through CrewAI's OWN invocation path.

    Asserting construction alone would pass on a tool CrewAI can build and
    then fail to call — which is the only failure mode that matters here,
    since the behaviour itself lives in `MemoryTools` and is covered above.
    """
    pytest.importorskip("crewai", reason="needs the [crewai] extra")
    from adapters.crewai import create_memory_tools
    tools = create_memory_tools(bound.memory, user_id="u1")
    assert [t.name for t in tools] == ["search_memory", "get_memory_context", "add_memory"]

    # The args schema is what an LLM is shown; a wrong field name here is
    # invisible until an agent calls the tool with arguments it will reject.
    assert set(tools[0].args_schema.model_fields) == {"query", "top_k"}
    assert set(tools[1].args_schema.model_fields) == {"query", "token_budget"}
    assert set(tools[2].args_schema.model_fields) == {"messages", "session_id"}

    # BaseTool.run() — CrewAI's own entry point, not our `_run`.
    out = tools[1].run(query="United Boston flight", token_budget=500)
    assert out["text"] and out["evidence_count"] == len(out["citations"])

    hits = tools[0].run(query="United", top_k=5)
    assert hits and all(h.get("evidence_id") for h in hits)


def test_openai_agents_adapter_builds_native_tools(bound):
    """Same standard as above. The SDK wraps our callables into FunctionTool
    objects with a generated JSON schema and an async invoke hook — none of
    which the previous `len(...) == 3` assertion exercised."""
    pytest.importorskip("agents", reason="needs the [openai-agents] extra")
    import asyncio
    import json

    from adapters.openai_agents import create_memory_tools
    tools = create_memory_tools(bound.memory, user_id="u1")
    assert [t.name for t in tools] == ["search_memory", "get_memory_context", "add_memory"]

    # The SDK derives this schema from the signature; it is what the model sees.
    assert set(tools[0].params_json_schema["properties"]) == {"query", "top_k"}
    assert set(tools[1].params_json_schema["properties"]) == {"query", "token_budget"}
    assert set(tools[2].params_json_schema["properties"]) == {"messages", "session_id"}

    # on_invoke_tool is how the SDK actually calls a tool: a ToolContext and a
    # JSON string in, awaited result out. Calling the undecorated function
    # would prove nothing about whether the SDK can drive it.
    from agents.tool_context import ToolContext
    payload = json.dumps({"query": "United Boston flight", "token_budget": 500})
    ctx = ToolContext(context=None, tool_name="get_memory_context",
                      tool_call_id="call_test", tool_arguments=payload)
    out = asyncio.run(tools[1].on_invoke_tool(ctx, payload))
    assert out["text"] and out["evidence_count"] == len(out["citations"])


@pytest.mark.parametrize("module,extra", [
    ("adapters.langgraph", "langgraph"),
    ("adapters.crewai", "crewai"),
    ("adapters.openai_agents", "openai-agents"),
])
def test_missing_framework_names_the_extra_to_install(module, extra, monkeypatch, bound):
    """A missing framework must say WHICH extra installs it — the failure mode
    is an install problem with a one-line fix, and a bare ImportError buries it."""
    import builtins
    import importlib
    mod = importlib.import_module(module)
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name.split(".")[0] in {"langchain_core", "crewai", "agents"}:
            raise ImportError(f"blocked {name}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(ImportError, match=rf"sodamem\[{extra}\]"):
        mod.create_memory_tools(bound.memory, user_id="u1")
