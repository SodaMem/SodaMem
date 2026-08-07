"""Environment-variable configuration for the SodaMem MCP server.

Two modes, chosen by whether `SODAMEM_API_URL` is set:

**Remote** (`SODAMEM_API_URL=http://localhost:8000`) — every tool proxies to
that running SodaMem service. This is the mode every coding-tool integration
should use and the one `sodamem install` writes, because it is the only one
where several clients (Claude Code, Cursor, a hook firing per prompt) can
share a store at the same time: exactly one process ever opens SQLite.

**Local** (no `SODAMEM_API_URL`) — this process opens the stores itself and
takes the data-root lock while it does. Fine for a single client; a second
one now fails loudly at startup instead of corrupting the store. See
`mcp_server/backend.py` for the full account.

Local mode reuses `server.settings.Settings` (pydantic-settings,
`SODAMEM_`-prefixed) for `data_root` / `store_cache_max` / `llm_*` — the exact
knobs `server/stores.py` already consumes. It layers one stricter rule on top:
`SODAMEM_DATA_ROOT` is optional for the HTTP service (defaults to `./data`,
fine for a long-lived process with a fixed working directory) but REQUIRED
here. An MCP server is launched by an arbitrary client with an arbitrary cwd —
silently writing stores under `<cwd>/data` would scatter them wherever the
client happened to exec from.
"""
from __future__ import annotations

import os

from server.settings import Settings


class McpConfigError(RuntimeError):
    """Raised when the MCP server's required environment is not configured."""


def remote_url() -> str:
    """The configured service URL, or "" for local mode."""
    return os.environ.get("SODAMEM_API_URL", "").strip().rstrip("/")


#: Values accepted as "yes" for SODAMEM_MCP_ALLOW_WRITE. Anything else — an
#: empty string, "no", a typo — is False. A misspelled opt-in must fail
#: CLOSED: the failure is then a tool that isn't there, not a delete that
#: happened because the flag said "ture".
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def write_enabled() -> bool:
    """Whether this process registers the two mutating tools (`add_memories`,
    `delete_memory`). Default OFF.

    An MCP server is launched by a model-driven client. `delete_memory`
    archives a fact and `add_memories` spends the operator's extraction
    credits, and both are one hallucinated tool call away at all times — so
    the tool surface an unconfigured install exposes is read-only, and turning
    writes on is a thing a human did on purpose.

    Off means NOT REGISTERED, not "registered and refuses": a tool the model
    cannot see is a tool it cannot decide to call, and it costs no schema
    tokens in every request. `sodamem install` sets this to true in the env
    block it writes, because retaining memory is the entire point of the
    coding-tool integration — that is the opt-in, recorded in a config file
    the user can read and delete.
    """
    return os.environ.get("SODAMEM_MCP_ALLOW_WRITE", "").strip().lower() in _TRUTHY


def load_settings() -> Settings:
    """Build the shared `Settings` instance for LOCAL mode.

    `Settings()` still validates everything else (e.g. a malformed
    `SODAMEM_PORT` would fail loudly) even though this process never reads
    `host`/`port` — one settings object, one source of truth, rather than a
    second parallel config schema.
    """
    if not os.environ.get("SODAMEM_DATA_ROOT", "").strip():
        raise McpConfigError(
            "SODAMEM_DATA_ROOT is required in local mode: set it to an "
            "absolute path where one store directory per user_id will live "
            "(e.g. /Users/you/.sodamem/data), in the MCP client's env block "
            "for this server. Or set SODAMEM_API_URL instead to run against a "
            "shared SodaMem service, which is what you want if more than one "
            "editor or hook will use this store."
        )
    return Settings()


def build_backend():
    """The backend this process will serve every tool call from.

    One decision, made once at startup, from one environment variable — not a
    per-call branch. A tool that had to ask "am I local or remote?" would be a
    tool with two code paths to keep in agreement.
    """
    from mcp_server.backend import LocalBackend, RemoteBackend

    url = remote_url()
    if url:
        return RemoteBackend(url, os.environ.get("SODAMEM_API_KEY", "").strip())
    return LocalBackend(load_settings())


def resolve_user_id(explicit: str | None) -> str:
    """The effective user_id for one tool call: the explicit argument wins;
    otherwise `SODAMEM_USER_ID`; otherwise raise. Every tool call must be
    scoped to exactly one user — there is no unscoped path."""
    if explicit and explicit.strip():
        return explicit.strip()
    env_user = os.environ.get("SODAMEM_USER_ID", "").strip()
    if env_user:
        return env_user
    raise McpConfigError(
        "user_id is required: pass it explicitly as a tool argument, or set "
        "SODAMEM_USER_ID in the server's environment for a single-user setup. "
        "No tool call may run unscoped."
    )


def resolve_project_id(explicit: str | None) -> str:
    """The effective project_id: explicit argument, else `SODAMEM_PROJECT_ID`,
    else empty.

    Empty is a legitimate answer, unlike `user_id` — an unstamped call is an
    unnarrowed one, which is what you want when the client is not a coding
    tool and has no repo to speak of. `sodamem install` sets the env var per
    client so the common case needs no tool argument at all.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    return os.environ.get("SODAMEM_PROJECT_ID", "").strip()
