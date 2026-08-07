"""SodaMem MCP server — exposes the `sodamem` facade as MCP tools over stdio
(Claude Code / Cursor / Claude Desktop / any MCP client) via the official
`mcp` Python SDK. See `mcp_server/README.md` for client configuration.

Every tool here is a thin wrapper over `mcp_server.backend.Backend`: argument
validation and clamping live in this file, the data access lives there. That
split is what keeps local and remote mode honest — a tool cannot accidentally
behave differently in one mode, because it does not know which mode it is in.
Read `backend.py`'s module docstring for why remote mode exists at all (short
version: every coding-tool integration spawns its own MCP process, and they
were all opening the same WAL-less SQLite store).

Eight tools, six of them registered unconditionally. `add_memories` and
`delete_memory` mutate, so they appear only under SODAMEM_MCP_ALLOW_WRITE
(see `config.write_enabled`); an unconfigured server is read-only, and
`sodamem install` writes the opt-in for the coding-tool integration whose
entire purpose is retaining memory.

Tool names/descriptions here are product-facing (`add_memories`,
`search_memory`, `get_context`, `list_memories`, `delete_memory`,
`entity_timeline`, `explore_memory`, `refine_search`) — the
`memory.tool.*` / `memory.browser.*` internal dispatch vocabulary
(`sodamem/tools/__init__.py`) is an artifact of that module's own migration
history and does not leak through this boundary.

Scoping: every tool takes `user_id` as an explicit optional parameter; when
omitted it falls back to `SODAMEM_USER_ID` (`mcp_server.config`). There is no
code path that opens a store without a validated `user_id` —
`server.stores.validate_user_id` (the path-traversal-hardened allowlist) runs
on every resolved id before it ever touches the filesystem. `project_id` is
the repo dimension (R1.2b): explicit argument, else `SODAMEM_PROJECT_ID`, else
unnarrowed.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from server.stores import InvalidScopeError, validate_user_id

from .backend import Backend, new_session_id, now_iso
from .config import (
    McpConfigError, build_backend, resolve_project_id, resolve_user_id,
    write_enabled,
)

logger = logging.getLogger(__name__)

_READ_INSTRUCTIONS = (
    "SodaMem is an evidence-grounded, temporal memory store — every stored "
    "fact traces back to the raw conversation text it came from. Use "
    "search_memory for ranked, citable evidence cards; get_context for a "
    "single prompt-ready text block you can paste directly into another "
    "prompt (no extra formatting needed — this is the fast path, prefer it "
    "over search_memory when you just need memory to ground a reply); "
    "list_memories to enumerate what is stored. Every call is scoped to "
    "exactly one user_id, and optionally to one project_id (the repo or "
    "workspace you are working in)."
)

#: Appended only when the write tools are actually registered. Describing
#: add_memories/delete_memory to a model that has not been given them is how
#: you get a run that stalls retrying a tool call that will never resolve.
_WRITE_INSTRUCTIONS = (
    " This server is also configured for writes: use add_memories to persist "
    "conversation turns (extracts structured facts) and delete_memory to "
    "remove one fact."
)


def _resolve_and_validate_user_id(explicit: str | None) -> str:
    uid = resolve_user_id(explicit)
    try:
        return validate_user_id(uid)
    except InvalidScopeError as exc:
        raise ValueError(f"invalid user_id {uid!r}: {exc}") from exc


def _require_text(value: str | None, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def build_server(backend: Backend | None = None, *,
                 allow_write: bool | None = None) -> FastMCP:
    """Construct the FastMCP app and register its tools.

    Split out from `main()` so tests (and the `sodamem-mcp` script) can inject
    a backend — e.g. a `LocalBackend` over a tmp_path, or a `RemoteBackend`
    pointed at a test service — without touching process environment.

    `allow_write` defaults to `config.write_enabled()` (SODAMEM_MCP_ALLOW_WRITE,
    off). Six read tools always; the two mutating ones only when it is true —
    see that function for why the answer is "not registered" rather than
    "registered and refuses".
    """
    backend = backend or build_backend()
    if allow_write is None:
        allow_write = write_enabled()

    instructions = _READ_INSTRUCTIONS + (_WRITE_INSTRUCTIONS if allow_write else "")
    mcp = FastMCP(name="sodamem", instructions=instructions)

    def _write_tool(**kwargs):
        """`@mcp.tool` for a mutating tool: registers it when writes are on,
        and otherwise leaves the function as a plain local that nothing can
        reach. Written as a decorator so the two write tools below read
        exactly like the six read ones — the ONLY difference between them is
        which decorator they carry, which is the whole point."""
        def decorate(fn):
            return mcp.tool(**kwargs)(fn) if allow_write else fn
        return decorate

    @_write_tool(
        name="add_memories",
        description=(
            "Persist a conversation into the user's memory store. Takes a list "
            "of {role, content} messages and extracts structured, "
            "evidence-grounded facts from them (entities, events, "
            "preferences) via an LLM. Requires extraction credentials to be "
            "configured — fails loudly, does not silently skip extraction, if "
            "they are missing. Pass project_id to stamp the facts with the "
            "repo they came from."
        ),
    )
    def add_memories(
        messages: list[dict[str, str]],
        user_id: str | None = None,
        session_id: str | None = None,
        session_time: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        uid = _resolve_and_validate_user_id(user_id)
        if not messages:
            raise ValueError("messages must be a non-empty list of {role, content}")
        turns = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]
        return backend.add_memories(
            user_id=uid, turns=turns,
            session_id=session_id or new_session_id(),
            session_time=session_time or now_iso(),
            project_id=resolve_project_id(project_id),
        )

    @mcp.tool(
        name="search_memory",
        description=(
            "Ranked search over the user's stored facts (BM25 + vector "
            "fusion). Returns citable, fact-backed evidence cards with "
            "provenance and a stable id (usable with delete_memory) — not a "
            "synthesized answer. Use get_context instead if you want a "
            "single ready-to-paste text block."
        ),
    )
    def search_memory(
        query: str,
        user_id: str | None = None,
        top_k: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return backend.search(
            user_id=_resolve_and_validate_user_id(user_id),
            query=_require_text(query, "query"),
            top_k=max(1, min(int(top_k), 100)),
            project_id=resolve_project_id(project_id),
        )

    @mcp.tool(
        name="get_context",
        description=(
            "Prompt-ready evidence block for `query`: pre-formatted text plus "
            "the citation ids backing it, ready to paste directly into "
            "another prompt. Zero-LLM by default (unlike search_memory, "
            "which returns raw cards you'd still have to format yourself). "
            "This is SodaMem's headline tool — reach for it first when you "
            "need memory to ground a response."
        ),
    )
    def get_context(
        query: str,
        user_id: str | None = None,
        token_budget: int = 2000,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return backend.context(
            user_id=_resolve_and_validate_user_id(user_id),
            query=_require_text(query, "query"),
            token_budget=max(100, min(int(token_budget), 32000)),
            project_id=resolve_project_id(project_id),
        )

    @mcp.tool(
        name="list_memories",
        description=(
            "Enumerate active facts stored for the user, oldest first. "
            "Paginate with limit/offset for large stores."
        ),
    )
    def list_memories(
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return backend.list_memories(
            user_id=_resolve_and_validate_user_id(user_id),
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )

    @mcp.tool(
        name="entity_timeline",
        description=(
            "Everything the store knows about one entity, oldest first, each "
            "item still carrying the source span it was extracted from. This "
            "is the shape a key-value memory cannot answer: not 'what matches "
            "this query' but 'what is this thing's history'. Use it when the "
            "user asks how something changed, or when you need to see an "
            "entity's full record rather than the top few similar facts."
        ),
    )
    def entity_timeline(entity_id: str, user_id: str | None = None) -> dict[str, Any]:
        return backend.entity_timeline(
            user_id=_resolve_and_validate_user_id(user_id),
            entity_id=_require_text(entity_id, "entity_id"),
        )

    @mcp.tool(
        name="explore_memory",
        description=(
            "Walk the graph outward from one memory id — its supporting "
            "spans, the entities it mentions, and facts linked to it by "
            "supersede/contradict/support edges. Answers 'what is this "
            "connected to', which similarity search cannot: related facts "
            "often share no words. depth is capped at 3."
        ),
    )
    def explore_memory(
        start_id: str,
        user_id: str | None = None,
        depth: int = 1,
        limit: int = 25,
    ) -> dict[str, Any]:
        return backend.explore(
            user_id=_resolve_and_validate_user_id(user_id),
            start_id=_require_text(start_id, "start_id"),
            depth=max(1, min(int(depth), 3)),
            limit=max(1, min(int(limit), 100)),
        )

    @mcp.tool(
        name="refine_search",
        description=(
            "Filtered search: same ranking as search_memory, plus structured "
            "narrowing by entity, session, time window, confidence or fact "
            "kind. Reach for it when a plain search returns the right topic "
            "but the wrong slice — it filters in the store instead of making "
            "you fetch a large page and discard most of it."
        ),
    )
    def refine_search(
        query: str,
        user_id: str | None = None,
        top_k: int = 10,
        entity: str = "",
        session_id: str = "",
        min_confidence: float | None = None,
        occurred_from: str | None = None,
        occurred_to: str | None = None,
    ) -> dict[str, Any]:
        return backend.refine(
            user_id=_resolve_and_validate_user_id(user_id),
            query=_require_text(query, "query"),
            top_k=max(1, min(int(top_k), 100)),
            entity=entity or "",
            session_id=session_id or "",
            min_confidence=min_confidence,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )

    @_write_tool(
        name="delete_memory",
        description=(
            "Archive one fact by id, removing it from search_memory / "
            "get_context / list_memories results. This is a tombstone (the "
            "fact's status is set to 'archived'), not a physical erase — the "
            "underlying source text is retained for provenance/audit. Use the "
            "HTTP API's DELETE ?mode=erase if you need the rows actually "
            "gone. Idempotent: deleting an already-archived fact succeeds "
            "with already_deleted=true."
        ),
    )
    def delete_memory(
        memory_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return backend.delete_memory(
            user_id=_resolve_and_validate_user_id(user_id),
            memory_id=_require_text(memory_id, "memory_id"),
        )

    return mcp


def main() -> None:
    # stdio transport uses stdout for the JSON-RPC stream — logging must never
    # land there. logging.basicConfig()'s default StreamHandler targets
    # stderr, which is what we want; this call just makes that explicit
    # rather than relying on the default staying the default.
    import sys

    logging.basicConfig(
        level=os.environ.get("SODAMEM_MCP_LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
    )
    try:
        backend = build_backend()
    except McpConfigError as exc:
        logger.error(str(exc))
        raise SystemExit(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # The data-root lock lives behind build_backend(); a contended one
        # raises ControlPlaneError with a message that already names the fix.
        # Reaching the client as a traceback in a log nobody opens would waste
        # it, so it exits with that text instead.
        logger.error("%s", exc)
        raise SystemExit(str(exc)) from exc
    logger.info("sodamem mcp server starting in %s mode", backend.mode)
    build_server(backend).run(transport="stdio")


if __name__ == "__main__":
    main()
