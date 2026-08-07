"""Where the MCP tools' data comes from: this process, or a running SodaMem
HTTP service.

THE BUG THIS FIXES
------------------
Every coding-tool integration works the same way: the client spawns its own
`sodamem-mcp` process. Claude Code spawns one. Cursor spawns another. A hook
firing on `UserPromptSubmit` spawns a third. Before this module, each of those
called `StoreManager.get()` and opened the per-user SQLite store **directly**,
with no coordination — while `server/app.py` has taken an exclusive flock on
the data root since ADR 0001 precisely because those stores run without WAL
and concurrent writers corrupt them.

So the one process that was careful about it locked the door, and the process
every integration actually spawns walked in through the window. Two editors
open at once was silent corruption, and the failure would surface later as
missing memories, not as an error anyone could trace back.

TWO MODES, ONE RULE: EXACTLY ONE PROCESS OPENS THE STORES
---------------------------------------------------------
`LocalBackend` takes the same lock `create_app` does. A second local MCP
server now fails at startup with a message naming the fix, instead of
corrupting the store. That is correct but not sufficient — "the second editor
fails to start" is not an integration.

`RemoteBackend` is the fix that makes the integration work: point every
client at one running SodaMem service and they all share the store through
it, because only that one process ever opens SQLite. This is the shape mem0
ships (`mcp.mem0.ai`) and the shape `sodamem install` configures by default.

The two backends are held to the same tool-level dict shapes on purpose. A
tool whose output depends on which mode the server happens to be in is a bug
the caller cannot see; the tests assert both backends against the same
expectations.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sodamem.errors import SodaMemError
from sodamem.memory._shared import _ts_to_iso
from sodamem.models import FactEvent
from sodamem.memory.retrieval.config import Degradation

from server.settings import Settings
from server.stores import StoreManager

logger = logging.getLogger(__name__)

#: Remote calls are short and interactive — a hook blocking a user's prompt is
#: the caller here, so a slow service must fail fast rather than hang the
#: editor. Overridable per-instance for the ingest path, which is genuinely
#: slower (it runs LLM extraction server-side).
DEFAULT_TIMEOUT_S = 15.0
INGEST_TIMEOUT_S = 120.0


class BackendError(RuntimeError):
    """A backend operation failed in a way the caller should see verbatim."""


# --- shared projections -----------------------------------------------------
# Both backends render the same tool-facing dicts. These live at module level,
# not on either class, so there is exactly one definition of "what a memory
# looks like to an MCP client".

def degradation_to_dict(d: Degradation) -> dict[str, Any]:
    return {"code": d.code.value, "message": d.message, "details": dict(d.details)}


def evidence_to_memory(ev: dict) -> dict[str, Any]:
    """One retrieval evidence dict projected down to what a client needs."""
    return {
        "id": ev.get("fact_id") or ev.get("evidence_id"),
        "content": ev.get("support_text") or "",
        "confidence": ev.get("confidence"),
        "occurred_at": ev.get("occurred_start"),
        "valid_from": ev.get("valid_from"),
        "valid_until": ev.get("valid_until") or ev.get("valid_to"),
        "session_id": ev.get("source_session_id"),
        "status": ev.get("status"),
    }


def fact_to_memory(fact: FactEvent) -> dict[str, Any]:
    return {
        "id": fact.fact_id,
        "content": fact.predicate_raw or fact.predicate_canonical,
        "kind": fact.kind.value if hasattr(fact.kind, "value") else fact.kind,
        "status": fact.status.value if hasattr(fact.status, "value") else fact.status,
        "occurred_at": _ts_to_iso(fact.occurred_start),
        "valid_from": _ts_to_iso(fact.valid_from),
        "valid_until": _ts_to_iso(fact.valid_until),
        "confidence": fact.confidence,
    }


class Backend(ABC):
    """The operations every MCP tool is a thin wrapper over.

    Deliberately NOT a Protocol: an abstract base makes "the remote backend
    forgot to implement refine" an error at import time rather than an
    AttributeError inside a user's editor session.
    """

    #: Shown in startup logs and by `sodamem daemon status`.
    mode: str = "unknown"

    @abstractmethod
    def add_memories(self, *, user_id: str, turns: list[dict[str, str]],
                     session_id: str, session_time: str,
                     project_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def search(self, *, user_id: str, query: str, top_k: int,
               project_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def context(self, *, user_id: str, query: str, token_budget: int,
                project_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def list_memories(self, *, user_id: str, limit: int,
                      offset: int) -> dict[str, Any]: ...

    @abstractmethod
    def entity_timeline(self, *, user_id: str, entity_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def explore(self, *, user_id: str, start_id: str, depth: int,
                limit: int) -> dict[str, Any]: ...

    @abstractmethod
    def refine(self, *, user_id: str, query: str, top_k: int, entity: str,
               session_id: str, min_confidence: float | None,
               occurred_from: str | None,
               occurred_to: str | None) -> dict[str, Any]: ...

    @abstractmethod
    def delete_memory(self, *, user_id: str, memory_id: str) -> dict[str, Any]: ...

    def close(self) -> None:
        return None


# --- local ------------------------------------------------------------------

class LocalBackend(Backend):
    """Opens the stores in this process. Holds the data-root lock while it does.

    One process per data root, enforced — not documented and hoped for.
    """

    mode = "local"

    def __init__(self, settings: Settings,
                 store_manager: StoreManager | None = None,
                 *, acquire_lock: bool = True) -> None:
        self._settings = settings
        if acquire_lock:
            # The whole point of this class's existence. `acquire_lock=False`
            # exists for tests that build several backends over tmp_paths in
            # one process; nothing in the shipped entry point passes it.
            from server.control import acquire_data_root_lock
            acquire_data_root_lock(settings.data_root)
        self._stores = store_manager or StoreManager(settings)

    # -- helpers ------------------------------------------------------------
    @contextmanager
    def _mem(self, user_id: str):
        """Borrow this user's store for the length of ONE operation.

        `lease()`, not a bare `get()`: the manager evicts by LRU, and a store
        being used by an in-flight call is otherwise an eligible victim the
        moment another user's call pushes the cache over its cap. Closing a
        store out from under a running ingest is exactly the shape that
        produced silently-empty retrieval before, so the borrow/return pair is
        held as one block that no path can forget to close.
        """
        with self._stores.lease(user_id) as mem:
            yield mem

    @contextmanager
    def _tool(self, user_id: str):
        """One `MemoryTool` bound to this call's user, for the lease's length.

        Constructed per call, not cached: the class is documented as a
        single-user tool surface (it stores `user_id` on the instance), while
        an MCP server is multi-tenant per call. The underlying SodaMem/Store
        is the leased per-user one, so this allocates a thin wrapper.
        """
        from sodamem.tools import MemoryTool
        with self._mem(user_id) as mem:
            yield MemoryTool(mem, user_id=user_id)

    # -- operations ---------------------------------------------------------
    def add_memories(self, *, user_id: str, turns: list[dict[str, str]],
                     session_id: str, session_time: str,
                     project_id: str = "") -> dict[str, Any]:
        with self._mem(user_id) as mem:
            return self._add(mem, user_id=user_id, turns=turns,
                             session_id=session_id, session_time=session_time,
                             project_id=project_id)

    def _add(self, mem, *, user_id: str, turns: list[dict[str, str]],
             session_id: str, session_time: str, project_id: str) -> dict[str, Any]:
        if mem.extractor is None:
            raise BackendError(
                "add_memories requires LLM extraction credentials, which are "
                "not configured on this server. Set SODAMEM_LLM_PROVIDER and "
                "SODAMEM_LLM_API_KEY (optionally SODAMEM_LLM_MODEL / "
                "SODAMEM_LLM_BASE_URL) in the MCP server's environment and "
                "restart it."
            )
        try:
            result = mem.ingest(turns, user_id=user_id, session_id=session_id,
                                session_time=session_time, project_id=project_id)
        except SodaMemError as exc:
            raise BackendError(f"add_memories failed ({exc.code.value}): {exc}") from exc
        counts = result.counts
        spans = counts.get("extract_window_spans_in_session", 0)
        return {
            "user_id": user_id,
            "session_id": session_id,
            "session_time": session_time,
            "turns_written": spans,
            "spans_written": spans,
            "facts_extracted": counts.get("extracted_facts_in_session", 0),
        }

    def search(self, *, user_id: str, query: str, top_k: int,
               project_id: str = "") -> dict[str, Any]:
        with self._mem(user_id) as mem:
            result = mem.search(query, user_id=user_id, project_id=project_id)
        # Retrieval can also surface raw-turn evidence with no FactEvent behind
        # it (exact-wording fallback) — this tool promises "stored facts" with a
        # persistent, deletable id, so only fact-grounded cards are returned.
        evidence = [ev for ev in result.evidence if ev.get("fact_id")][:top_k]
        return {
            "user_id": user_id,
            "query": query,
            "count": len(evidence),
            "memories": [evidence_to_memory(ev) for ev in evidence],
            "degraded": [degradation_to_dict(d) for d in result.degraded],
        }

    def context(self, *, user_id: str, query: str, token_budget: int,
                project_id: str = "") -> dict[str, Any]:
        with self._mem(user_id) as mem:
            block = mem.build_context(
                query, user_id=user_id, token_budget=token_budget,
                project_id=project_id,
            )
        return {
            "user_id": user_id,
            "text": block.text,
            "citations": list(block.citations),
            "evidence_count": len(block.evidence),
            "degraded": [degradation_to_dict(d) for d in block.degraded],
        }

    def list_memories(self, *, user_id: str, limit: int,
                      offset: int) -> dict[str, Any]:
        with self._mem(user_id) as mem:
            facts = mem.store.get_all_fact_events(user_id, active_only=True)
        page = facts[offset: offset + limit]
        return {
            "user_id": user_id,
            "total": len(facts),
            "offset": offset,
            "limit": limit,
            "memories": [fact_to_memory(f) for f in page],
        }

    def entity_timeline(self, *, user_id: str, entity_id: str) -> dict[str, Any]:
        with self._tool(user_id) as tool:
            return tool.entity_timeline(entity_id)

    def explore(self, *, user_id: str, start_id: str, depth: int,
                limit: int) -> dict[str, Any]:
        with self._tool(user_id) as tool:
            return tool.explore(start_id, depth=depth, limit=limit)

    def refine(self, *, user_id: str, query: str, top_k: int, entity: str,
               session_id: str, min_confidence: float | None,
               occurred_from: str | None,
               occurred_to: str | None) -> dict[str, Any]:
        with self._tool(user_id) as tool:
            return tool.refine(
                query, top_k=top_k, entity=entity, session_id=session_id,
                min_confidence=min_confidence, occurred_from=occurred_from,
                occurred_to=occurred_to,
            )

    def delete_memory(self, *, user_id: str, memory_id: str) -> dict[str, Any]:
        """Delegates to `Store.archive_fact_event` rather than flipping the
        status here.

        Three things came free with that, all of which a local copy loses:
        ownership is enforced in the ONE place that rule lives (it raises
        TenancyError, and re-deriving the check here means two messages for
        one rule); the archive is a single-column UPDATE inside the store's
        lock instead of a read-modify-write through `upsert_fact_event`, which
        a concurrent write would silently clobber; and the delete leaves an
        audit trace, so `GET /v1/events` can still say who removed a fact.
        """
        with self._mem(user_id) as mem:
            fact = mem.store.get_fact_event(memory_id)
            if fact is None:
                raise BackendError(
                    f"no memory found with id {memory_id!r} for user {user_id!r}")
            result = mem.store.archive_fact_event(memory_id, user_id=user_id)
        return {"id": memory_id, "user_id": user_id,
                "deleted": result["deleted"],
                "already_deleted": result["already_archived"]}

    def close(self) -> None:
        self._stores.close_all()


# --- remote -----------------------------------------------------------------

class RemoteBackend(Backend):
    """Proxies every operation to a running SodaMem HTTP service.

    stdlib `urllib` on purpose: this process is spawned per editor session and,
    via the hook path, per prompt. Import cost is latency a user feels, and a
    third-party HTTP client would also be a dependency the `[mcp]` extra does
    not need — the whole surface is six JSON calls.
    """

    mode = "remote"

    def __init__(self, base_url: str, api_key: str = "", *,
                 timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    # -- transport ----------------------------------------------------------
    def _call(self, method: str, path: str, *, query: dict | None = None,
              body: dict | None = None, timeout: float | None = None) -> Any:
        url = f"{self._base}{path}"
        if query:
            clean = {k: v for k, v in query.items() if v not in (None, "")}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self._api_key:
            request.add_header("Authorization", f"Bearer {self._api_key}")
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            raise BackendError(self._explain_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise BackendError(
                f"cannot reach the SodaMem service at {self._base}: {exc.reason}. "
                f"Start it (`sodamem daemon ensure`) or unset SODAMEM_API_URL to "
                f"run this MCP server against local stores instead."
            ) from exc
        return json.loads(raw) if raw else {}

    @staticmethod
    def _explain_http_error(exc: urllib.error.HTTPError) -> str:
        """Surface the service's own error envelope.

        `{"code": ..., "message": ...}` (server/app.py normalizes every error
        onto it) carries far more than "HTTP 400" — dropping it would make
        remote mode strictly harder to debug than local mode, which is the
        wrong trade for the mode we tell people to use.
        """
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            payload = {}
        code = payload.get("code") or f"http_{exc.code}"
        message = payload.get("message") or exc.reason or "request failed"
        if exc.code == 401:
            message = (
                f"{message} — set SODAMEM_API_KEY to a key this service accepts"
            )
        return f"{code}: {message}"

    # -- operations ---------------------------------------------------------
    def add_memories(self, *, user_id: str, turns: list[dict[str, str]],
                     session_id: str, session_time: str,
                     project_id: str = "") -> dict[str, Any]:
        # async_mode=False: an MCP tool that returned "accepted, job 7f3a"
        # would be telling the model the memory is stored when it is not.
        result = self._call("POST", "/v1/memories", body={
            "user_id": user_id,
            "project_id": project_id or None,
            "messages": turns,
            "session_id": session_id,
            "session_time": session_time,
            "async_mode": False,
        }, timeout=INGEST_TIMEOUT_S)
        return {
            "user_id": user_id,
            "session_id": result.get("session_id", session_id),
            "session_time": session_time,
            "turns_written": result.get("turns_written", 0),
            "spans_written": result.get("spans_written", 0),
            "facts_extracted": result.get("facts_extracted", 0),
        }

    def search(self, *, user_id: str, query: str, top_k: int,
               project_id: str = "") -> dict[str, Any]:
        result = self._call("POST", "/v1/search", body={
            "user_id": user_id, "project_id": project_id or None,
            "query": query, "top_k": top_k,
        })
        # `metadata` on a SearchHit IS the evidence dict the local path
        # projects from, so both modes run the same projection over the same
        # input rather than two hand-aligned field lists.
        hits = [h.get("metadata") or {} for h in result.get("hits", [])]
        evidence = [ev for ev in hits if ev.get("fact_id")][:top_k]
        return {
            "user_id": user_id,
            "query": query,
            "count": len(evidence),
            "memories": [evidence_to_memory(ev) for ev in evidence],
            "degraded": result.get("degraded", []),
        }

    def context(self, *, user_id: str, query: str, token_budget: int,
                project_id: str = "") -> dict[str, Any]:
        result = self._call("GET", "/v1/context", query={
            "user_id": user_id, "project_id": project_id,
            "query": query, "token_budget": token_budget,
        })
        return {
            "user_id": user_id,
            "text": result.get("text", ""),
            "citations": list(result.get("citations") or []),
            "evidence_count": len(result.get("evidence") or []),
            "degraded": result.get("degraded", []),
        }

    def list_memories(self, *, user_id: str, limit: int,
                      offset: int) -> dict[str, Any]:
        result = self._call("GET", "/v1/memories", query={
            "user_id": user_id, "limit": limit, "offset": offset,
        })
        return {
            "user_id": user_id,
            "total": result.get("total", 0),
            "offset": offset,
            "limit": limit,
            "memories": [
                {
                    "id": m.get("id"),
                    "content": m.get("content"),
                    "kind": m.get("kind"),
                    "status": m.get("status"),
                    "occurred_at": m.get("occurred_at"),
                    "valid_from": m.get("valid_from"),
                    "valid_until": m.get("valid_until"),
                    "confidence": m.get("confidence"),
                }
                for m in result.get("memories", [])
            ],
        }

    def entity_timeline(self, *, user_id: str, entity_id: str) -> dict[str, Any]:
        return self._call("GET", "/v1/entity_timeline", query={
            "user_id": user_id, "entity_id": entity_id,
        })

    def explore(self, *, user_id: str, start_id: str, depth: int,
                limit: int) -> dict[str, Any]:
        return self._call("GET", "/v1/explore", query={
            "user_id": user_id, "start_id": start_id,
            "depth": depth, "limit": limit,
        })

    def refine(self, *, user_id: str, query: str, top_k: int, entity: str,
               session_id: str, min_confidence: float | None,
               occurred_from: str | None,
               occurred_to: str | None) -> dict[str, Any]:
        return self._call("POST", "/v1/refine", body={
            "user_id": user_id, "query": query, "top_k": top_k,
            "entity": entity, "session_id": session_id,
            "min_confidence": min_confidence,
            "occurred_from": occurred_from, "occurred_to": occurred_to,
        })

    def delete_memory(self, *, user_id: str, memory_id: str) -> dict[str, Any]:
        # mode=archive, matching LocalBackend exactly. The default (`erase`)
        # physically removes rows — a tool that tombstoned locally and erased
        # remotely would be the same call with two different outcomes.
        result = self._call(
            "DELETE", f"/v1/memories/{urllib.parse.quote(memory_id, safe='')}",
            query={"user_id": user_id, "mode": "archive"},
        )
        return {
            "id": memory_id,
            "user_id": user_id,
            "deleted": bool(result.get("deleted", True)),
            "already_deleted": bool(result.get("already_deleted", False)),
        }


# --- construction -----------------------------------------------------------

def new_session_id() -> str:
    return f"mcp_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
