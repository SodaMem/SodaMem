"""Guardian tests for the SodaMem MCP server (`mcp_server/`).

No real LLM, no network: `add_memories`' success path is exercised with
`sodamem.llm.testing.ScriptedProvider` (same double `tests/test_facade.py`
uses), and the "LLM not configured" failure path is exercised by simply not
configuring one — both are in-process, offline. A full stdio subprocess
handshake (the strongest end-to-end evidence that the server actually speaks
MCP) is covered separately and is not repeated here as a unit test: spinning
a subprocess per test would be slow and is redundant with what `build_server`
+ `FastMCP.call_tool` already exercises against the exact same tool
functions.
"""
from __future__ import annotations

import json

import pytest

# Needs BOTH extras: `mcp` for the SDK, and `[server]`'s pydantic-settings
# because mcp_server reuses server.stores' hardened user_id validation.
pytest.importorskip("mcp", reason="MCP tests require the [mcp] extra")
pytest.importorskip("pydantic_settings", reason="MCP tests require the [server] extra")

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from server.settings import Settings  # noqa: E402
from server.stores import StoreManager  # noqa: E402
from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.extractor import FactEventExtractorV2
from mcp_server.config import McpConfigError, load_settings, resolve_user_id  # noqa: E402
from mcp_server.backend import LocalBackend  # noqa: E402
from mcp_server.main import build_server  # noqa: E402

# FactEventExtractorV2 expects a bare JSON array (sodamem/prompts/extraction.py's
# EXTRACT_SYSTEM_PROMPT: "Return only a JSON array") with real schema fields —
# source_span_ids/support_text, not the source_spans/quote/statement shape a
# naive fixture might guess at. support_text is set to the exact raw turn
# text so `_match_span_by_quote`'s substring-grounding recovers provenance
# even with source_span_ids left empty (this test's fixture doesn't know the
# deterministic span_id the ingest client will assign ahead of time).
_EXTRACT = json.dumps([{
    "kind": "state",
    "predicate_raw": "has a golden retriever named Biscuit",
    "predicate_canonical": "owns_pet",
    "modality": "current_state",
    "entity_roles": {"subject": "user", "pet": "Biscuit"},
    "source_span_ids": [],
    "support_text": "I have a golden retriever named Biscuit",
}])

_EXPECTED_TOOLS = {
    "add_memories",
    "search_memory",
    "get_context",
    "list_memories",
    "delete_memory",
    # PRD R1.11 — the three MemoryTool capabilities that existed but were
    # never exposed over MCP.
    "entity_timeline",
    "explore_memory",
    "refine_search",
}


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(data_root=tmp_path, **overrides)


def _backend(settings: Settings, stores: StoreManager) -> LocalBackend:
    """A LocalBackend that does NOT take the data-root lock.

    The lock is process-wide and per-root; the shipped entry point takes it
    (that is the whole point of LocalBackend), but a test module builds a
    dozen backends over a dozen tmp_paths in one process, and each acquisition
    would drop the previous root's handle. Lock behavior has its own tests —
    see test_local_backend_lock_* below."""
    return LocalBackend(settings, stores, acquire_lock=False)


def _server(tmp_path, *, allow_write: bool = True, **settings_overrides):
    """A server with the write tools ON.

    That is NOT the shipped default (`SODAMEM_MCP_ALLOW_WRITE`, off — see
    `test_write_tools_are_absent_by_default` below); it is what most tests in
    this file are about. Passing it explicitly here rather than setting the
    env var keeps the gate itself testable in the same process."""
    settings = _settings(tmp_path, **settings_overrides)
    return build_server(_backend(settings, StoreManager(settings)),
                        allow_write=allow_write)


def _scripted_store_manager(tmp_path, monkeypatch, n_calls: int = 4) -> StoreManager:
    """A StoreManager whose extractor is a ScriptedProvider double — no LLM
    credentials, no network — so add_memories' success path is testable
    offline."""
    monkeypatch.setattr(
        StoreManager,
        "_build_extractor",
        lambda self: FactEventExtractorV2(provider=ScriptedProvider([_EXTRACT] * n_calls)),
    )
    settings = _settings(tmp_path)
    return StoreManager(settings)


# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_registry_is_complete(tmp_path):
    server = _server(tmp_path)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == _EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and tool.description.strip()
        # Internal dispatch vocabulary must never leak into the MCP surface.
        assert "memory.tool." not in tool.description
        assert "memory.browser." not in tool.description


#: The two tools that mutate. Everything else is a read.
_WRITE_TOOLS = {"add_memories", "delete_memory"}


@pytest.mark.asyncio
async def test_write_tools_are_absent_by_default(tmp_path, monkeypatch):
    """The README promises the write tools are opt-in. Until 0806 nothing in
    the code implemented that: all eight were registered unconditionally, so
    an MCP client wired up by a user who read the README and expected a
    read-only surface could still call delete_memory."""
    monkeypatch.delenv("SODAMEM_MCP_ALLOW_WRITE", raising=False)
    settings = _settings(tmp_path)
    server = build_server(_backend(settings, StoreManager(settings)))
    names = {t.name for t in await server.list_tools()}
    assert names == _EXPECTED_TOOLS - _WRITE_TOOLS
    # Not registered, rather than registered-and-refusing: a tool the model
    # cannot see is one it cannot decide to call.
    assert not (names & _WRITE_TOOLS)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
async def test_env_opt_in_registers_the_write_tools(tmp_path, monkeypatch, value):
    monkeypatch.setenv("SODAMEM_MCP_ALLOW_WRITE", value)
    settings = _settings(tmp_path)
    server = build_server(_backend(settings, StoreManager(settings)))
    assert {t.name for t in await server.list_tools()} == _EXPECTED_TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "ture", "false", "no", "0", "off"])
async def test_a_misspelled_opt_in_fails_closed(tmp_path, monkeypatch, value):
    """A typo in the flag must leave writes OFF. Failing open here would mean
    a user who meant to enable writes and mistyped it gets them anyway —
    which is fine — but equally a user who meant `false` and wrote `flase`
    would silently hand a model delete_memory."""
    monkeypatch.setenv("SODAMEM_MCP_ALLOW_WRITE", value)
    settings = _settings(tmp_path)
    server = build_server(_backend(settings, StoreManager(settings)))
    assert not ({t.name for t in await server.list_tools()} & _WRITE_TOOLS)


@pytest.mark.asyncio
async def test_instructions_describe_only_the_registered_tools(tmp_path, monkeypatch):
    """Telling a model about a tool it has not been given is how a run stalls
    retrying a call that can never resolve."""
    monkeypatch.delenv("SODAMEM_MCP_ALLOW_WRITE", raising=False)
    settings = _settings(tmp_path)
    read_only = build_server(_backend(settings, StoreManager(settings)))
    assert "add_memories" not in read_only.instructions
    assert "delete_memory" not in read_only.instructions
    assert "search_memory" in read_only.instructions

    writable = _server(tmp_path)
    assert "add_memories" in writable.instructions
    assert "delete_memory" in writable.instructions


@pytest.mark.asyncio
async def test_tool_schemas_have_expected_required_params(tmp_path):
    server = _server(tmp_path)
    tools = {t.name: t for t in await server.list_tools()}

    assert tools["add_memories"].inputSchema["required"] == ["messages"]
    assert "user_id" in tools["add_memories"].inputSchema["properties"]
    assert "session_id" in tools["add_memories"].inputSchema["properties"]
    assert "session_time" in tools["add_memories"].inputSchema["properties"]

    assert tools["search_memory"].inputSchema["required"] == ["query"]
    assert "top_k" in tools["search_memory"].inputSchema["properties"]

    assert tools["get_context"].inputSchema["required"] == ["query"]
    assert "token_budget" in tools["get_context"].inputSchema["properties"]

    assert tools["delete_memory"].inputSchema["required"] == ["memory_id"]

    # list_memories has no required params — every field is optional/paginated.
    assert not tools["list_memories"].inputSchema.get("required")


# --------------------------------------------------------------------------
# user_id scoping — no tool call may run unscoped
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,args", [
    ("list_memories", {}),
    ("search_memory", {"query": "anything"}),
    ("get_context", {"query": "anything"}),
    ("delete_memory", {"memory_id": "fact_123"}),
])
async def test_missing_user_id_raises(tmp_path, monkeypatch, tool_name, args):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    server = _server(tmp_path)
    with pytest.raises(ToolError, match="user_id is required"):
        await server.call_tool(tool_name, args)


@pytest.mark.asyncio
async def test_env_user_id_is_used_when_arg_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("SODAMEM_USER_ID", "env_user")
    server = _server(tmp_path)
    result = await server.call_tool("list_memories", {})
    payload = json.loads(result[0][0].text)
    assert payload["user_id"] == "env_user"


@pytest.mark.asyncio
async def test_explicit_user_id_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SODAMEM_USER_ID", "env_user")
    server = _server(tmp_path)
    result = await server.call_tool("list_memories", {"user_id": "explicit_user"})
    payload = json.loads(result[0][0].text)
    assert payload["user_id"] == "explicit_user"


@pytest.mark.asyncio
async def test_path_traversal_user_id_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    server = _server(tmp_path)
    with pytest.raises(ToolError, match="invalid user_id"):
        await server.call_tool("list_memories", {"user_id": ".."})


# --------------------------------------------------------------------------
# add_memories: LLM-config gate (no silent no-op ingest)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_memories_without_llm_config_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    # Default StoreManager: no SODAMEM_LLM_* set -> extractor is None.
    server = _server(tmp_path)
    with pytest.raises(ToolError, match="requires LLM extraction credentials"):
        await server.call_tool(
            "add_memories",
            {"user_id": "u1", "messages": [{"role": "user", "content": "hello"}]},
        )


@pytest.mark.asyncio
async def test_add_memories_rejects_empty_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    server = _server(tmp_path)
    with pytest.raises(ToolError, match="non-empty"):
        await server.call_tool("add_memories", {"user_id": "u1", "messages": []})


# --------------------------------------------------------------------------
# End-to-end round trip with a scripted (offline) extractor
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_search_context_list_delete_round_trip(tmp_path, monkeypatch):
    stores = _scripted_store_manager(tmp_path, monkeypatch)
    settings = _settings(tmp_path)
    server = build_server(_backend(settings, stores), allow_write=True)

    add_result = await server.call_tool(
        "add_memories",
        {
            "user_id": "u1",
            "messages": [{"role": "user", "content": "I have a golden retriever named Biscuit"}],
            "session_time": "2024/03/01 (Fri) 10:00",
        },
    )
    add_payload = json.loads(add_result[0][0].text)
    assert add_payload["user_id"] == "u1"
    assert add_payload["facts_extracted"] >= 1
    assert add_payload["session_id"]  # auto-generated

    search_result = await server.call_tool(
        "search_memory", {"user_id": "u1", "query": "what pet do I have"}
    )
    search_payload = json.loads(search_result[0][0].text)
    assert search_payload["count"] >= 1
    memory_id = search_payload["memories"][0]["id"]
    assert "Biscuit" in json.dumps(search_payload)

    context_result = await server.call_tool(
        "get_context", {"user_id": "u1", "query": "what pet do I have"}
    )
    context_payload = json.loads(context_result[0][0].text)
    assert context_payload["text"]
    assert context_payload["citations"]
    for cid in context_payload["citations"]:
        assert cid in context_payload["text"]

    list_result = await server.call_tool("list_memories", {"user_id": "u1"})
    list_payload = json.loads(list_result[0][0].text)
    assert list_payload["total"] >= 1
    assert any(m["id"] == memory_id for m in list_payload["memories"])

    delete_result = await server.call_tool(
        "delete_memory", {"user_id": "u1", "memory_id": memory_id}
    )
    delete_payload = json.loads(delete_result[0][0].text)
    assert delete_payload["deleted"] is True
    assert delete_payload["already_deleted"] is False

    # Archived facts drop out of list_memories (active_only).
    list_after = await server.call_tool("list_memories", {"user_id": "u1"})
    list_after_payload = json.loads(list_after[0][0].text)
    assert not any(m["id"] == memory_id for m in list_after_payload["memories"])

    # Deleting again is idempotent, not an error.
    delete_again = await server.call_tool(
        "delete_memory", {"user_id": "u1", "memory_id": memory_id}
    )
    delete_again_payload = json.loads(delete_again[0][0].text)
    assert delete_again_payload["deleted"] is True
    assert delete_again_payload["already_deleted"] is True


@pytest.mark.asyncio
async def test_delete_unknown_memory_id_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    server = _server(tmp_path)
    with pytest.raises(ToolError, match="no memory found"):
        await server.call_tool("delete_memory", {"user_id": "u1", "memory_id": "fact_does_not_exist"})


@pytest.mark.asyncio
async def test_delete_cross_user_id_is_refused(tmp_path, monkeypatch):
    """StoreManager already opens one PHYSICALLY separate store per user_id
    (server/stores.py), so two real users can never see each other's
    fact_ids through the normal add/search path — that isolation is covered
    by test_path_traversal_user_id_is_rejected and the store-per-user design
    itself. The ownership check inside Store.archive_fact_event — which
    delete_memory now delegates to, sharing it with the REST route — is
    defense in depth for a narrower case: the SAME physical store somehow
    holding a fact stamped with a different user_id (e.g. a future bug
    elsewhere). Exercise that guard directly by planting such a fact rather
    than relying on cross-store leakage that the architecture already
    prevents."""
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    from sodamem.models import FactEvent, FactKind, FactStatus

    settings = _settings(tmp_path)
    stores = StoreManager(settings)
    server = build_server(_backend(settings, stores), allow_write=True)

    mem = stores.get("victim")
    planted = FactEvent(
        user_id="attacker",  # stamped for a DIFFERENT user than the store...
        kind=FactKind.STATE,
        source_span_ids=[],
        predicate_raw="a fact that doesn't belong to this store's user",
    )
    mem.store.upsert_fact_event(planted)  # ...but upserted into "victim"'s store

    with pytest.raises(ToolError, match="belongs to a different user_id"):
        await server.call_tool(
            "delete_memory", {"user_id": "victim", "memory_id": planted.fact_id}
        )

    # Refused, not silently downgraded: the planted fact is still ACTIVE.
    assert mem.store.get_fact_event(planted.fact_id).status == FactStatus.ACTIVE


# --------------------------------------------------------------------------
# mcp_server.config
# --------------------------------------------------------------------------

def test_load_settings_requires_data_root_env(monkeypatch):
    monkeypatch.delenv("SODAMEM_DATA_ROOT", raising=False)
    with pytest.raises(McpConfigError, match="SODAMEM_DATA_ROOT is required"):
        load_settings()


def test_load_settings_succeeds_when_data_root_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path))
    settings = load_settings()
    assert settings.data_root == tmp_path.resolve()


def test_resolve_user_id_explicit_wins(monkeypatch):
    monkeypatch.setenv("SODAMEM_USER_ID", "env_user")
    assert resolve_user_id("explicit") == "explicit"


def test_resolve_user_id_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SODAMEM_USER_ID", "env_user")
    assert resolve_user_id(None) == "env_user"


def test_resolve_user_id_raises_when_neither_present(monkeypatch):
    monkeypatch.delenv("SODAMEM_USER_ID", raising=False)
    with pytest.raises(McpConfigError, match="user_id is required"):
        resolve_user_id(None)


# ---------------------------------------------------------------------------
# PRD R1.11 — entity_timeline / explore / refine.
#
# None of these needed new engine work: MemoryTool.entity_timeline(),
# .explore() and .refine() were all already implemented and exercised by the
# benchmark's planner loop. They were simply never exposed over MCP, so an
# MCP client had exactly one retrieval shape (top-k similarity) while the
# rig had five. entity_timeline is the one that shows off what this store has
# and a KV memory does not: one entity's history in time order, each item
# still pointing at the span it came from.
# ---------------------------------------------------------------------------

def _payload(result):
    """FastMCP hands back (content_blocks, ...); every tool here answers with
    exactly one JSON text block."""
    return json.loads(result[0][0].text)


def _offline_server(tmp_path, monkeypatch):
    """Server whose extractor is the scripted double — add_memories works
    with no credentials and no network, so these tools can be tested against
    a store that actually has facts in it."""
    settings = _settings(tmp_path)
    return build_server(
        _backend(settings, _scripted_store_manager(tmp_path, monkeypatch)),
        allow_write=True,
    )


# The scripted provider always returns the same extraction, and the extractor
# grounds every fact against the turn text — so the seed content must be the
# sentence `_EXTRACT`'s support_text quotes, or the fact is (correctly)
# dropped as unsupported and the store ends up empty.
_SEED_TEXT = "I have a golden retriever named Biscuit"


async def _seed(server, user_id: str = "u_mcp_new"):
    """Seed one fact and PROVE it landed — a silent zero-fact ingest would
    otherwise surface later as 'the tool found nothing', blaming the tool."""
    result = await server.call_tool("add_memories", {
        "user_id": user_id,
        "messages": [{"role": "user", "content": _SEED_TEXT}],
        "session_time": "2024/03/01 (Fri) 10:00",
    })
    assert _payload(result)["facts_extracted"] >= 1


@pytest.mark.asyncio
async def test_entity_timeline_returns_time_ordered_items(tmp_path, monkeypatch):
    server = _offline_server(tmp_path, monkeypatch)
    await _seed(server)
    payload = _payload(await server.call_tool("entity_timeline", {
        "user_id": "u_mcp_new", "entity_id": "Biscuit",
    }))
    assert payload["entity_id"] == "Biscuit"
    assert payload["count"] == len(payload["items"]) >= 1
    # The differentiator: each item still points at the source it came from
    # (`source_trace_ids` = the spans) and carries that text — this is what a
    # key-value memory cannot hand back.
    assert all(item.get("content") for item in payload["items"])
    assert any(item.get("source_trace_ids") for item in payload["items"])


@pytest.mark.asyncio
async def test_entity_timeline_rejects_empty_entity_id(tmp_path):
    server = _server(tmp_path)
    with pytest.raises(Exception):
        await server.call_tool("entity_timeline", {"user_id": "u_mcp_new", "entity_id": ""})


@pytest.mark.asyncio
async def test_refine_search_filters_without_a_second_round_trip(tmp_path, monkeypatch):
    server = _offline_server(tmp_path, monkeypatch)
    await _seed(server)
    payload = _payload(await server.call_tool("refine_search", {
        "user_id": "u_mcp_new", "query": "pet", "top_k": 5,
    }))
    assert "items" in payload


@pytest.mark.asyncio
async def test_explore_memory_walks_from_a_starting_id(tmp_path, monkeypatch):
    server = _offline_server(tmp_path, monkeypatch)
    await _seed(server)
    listed = _payload(await server.call_tool("list_memories", {"user_id": "u_mcp_new"}))
    start = listed["memories"][0]["id"] if listed.get("memories") else None
    assert start, "seed produced no memory to explore from"
    payload = _payload(await server.call_tool("explore_memory", {
        "user_id": "u_mcp_new", "start_id": start, "depth": 1,
    }))
    assert "nodes" in payload or "items" in payload


# --- the tool boundary: a dead backend must not look like an empty store ----

@pytest.mark.asyncio
async def test_a_dead_remote_backend_reaches_the_client_with_a_remedy():
    """The worst reading of issue #21 is "memory quietly stopped working".

    Two things have to hold at the protocol boundary for that not to happen,
    and neither had a test. First, a dead backend must be DISTINGUISHABLE from
    an empty store: `list_memories` must raise, not return `{"memories": []}`.
    It already does — every tool lets `_call()`'s error out — so this is a gate
    over existing behaviour, not a change; `mcp_server/main.py` is untouched.

    Second, and this is what #21 actually fixes: the sentence that survives the
    trip through `FastMCP.Tool.run` has to carry a remedy. Before the fix it
    read `Error executing tool list_memories: Remote end closed connection
    without response` — accurate, and useless. The framework was writing the
    only sentence the product says while it is broken, because the code that
    knew what went wrong let the exception past.
    """
    # Imported here, not at module scope: `tests/test_mcp_backend` calls
    # `importorskip("uvicorn")`, and this file must not inherit that skip.
    from .test_mcp_backend import _drain_then_close, _socket_server
    from mcp_server.backend import RemoteBackend

    with _socket_server(_drain_then_close) as port:
        base = f"http://127.0.0.1:{port}"
        server = build_server(RemoteBackend(base, "k", timeout=5.0))

        with pytest.raises(ToolError) as caught:
            await server.call_tool("list_memories", {"user_id": "alice"})

        message = str(caught.value)
        assert "cannot reach the SodaMem service" in message
        assert base in message
        assert "`sodamem daemon ensure`" in message
