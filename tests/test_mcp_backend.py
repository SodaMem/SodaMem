"""Guardian tests for `mcp_server/backend.py` — the two backends behind every
MCP tool.

Two things are being defended here, and they are the whole reason the module
exists:

1. **The lock.** Before this, an MCP server opened the per-user SQLite stores
   with no coordination while `server/app.py` held an exclusive flock on the
   same data root. Every coding-tool integration spawns its own MCP process,
   so "two editors open" was silent corruption. A second opener must now fail
   loudly.

2. **Mode parity.** Remote mode is the fix that lets N clients share one
   store, which makes it the mode people will actually run. A tool whose
   output shape depends on which mode the server is in is a bug no caller can
   see, so the parity test below runs BOTH backends over the SAME store and
   demands identical dicts.

The remote backend is exercised against a real uvicorn server on an ephemeral
port — not a mocked `urlopen`. Its job is to speak HTTP to the real app
(headers, query encoding, error envelopes); a test that stubbed the transport
would verify none of that.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="MCP tests require the [mcp] extra")
pytest.importorskip("pydantic_settings", reason="MCP tests require the [server] extra")
pytest.importorskip("uvicorn", reason="remote-backend tests need uvicorn")

from server.control import release_data_root_lock  # noqa: E402
from server.settings import Settings  # noqa: E402
from server.stores import StoreManager  # noqa: E402
from sodamem.models import FactEvent, FactKind  # noqa: E402

from mcp_server.backend import BackendError, LocalBackend, RemoteBackend  # noqa: E402
from ._service import free_port, reset_server_singletons, running_service  # noqa: E402


API_KEY = "test-key-abc123"


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(data_root=tmp_path, api_key=API_KEY, **overrides)


def _seed(stores: StoreManager, user_id: str, text: str,
          project_id: str = "") -> FactEvent:
    """One fact straight into the store — no extractor, no LLM, no network."""
    fact = FactEvent(
        user_id=user_id,
        kind=FactKind.STATE,
        source_span_ids=[],
        predicate_raw=text,
        metadata={"scope": {"project_id": project_id}} if project_id else {},
    )
    stores.get(user_id).store.upsert_fact_event(fact)
    return fact


# --- 1. the lock ------------------------------------------------------------

def test_a_second_mcp_process_refuses_to_open_a_locked_data_root(tmp_path):
    """The regression that motivated this module.

    Deliberately a SUBPROCESS, and deliberately the shipped entry point
    (`python -m mcp_server`) rather than the class: the corruption scenario is
    two PROCESSES — Claude Code's MCP server and Cursor's — and
    `acquire_data_root_lock` short-circuits when the calling process already
    holds the root, so an in-process assertion would pass while proving
    nothing about the case that actually loses data.
    """
    import os
    import subprocess
    import sys

    holder = LocalBackend(_settings(tmp_path))
    try:
        env = {
            **os.environ,
            "SODAMEM_DATA_ROOT": str(tmp_path),
            "SODAMEM_USER_ID": "alice",
            "SODAMEM_API_KEY": API_KEY,
        }
        env.pop("SODAMEM_API_URL", None)  # force local mode
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_server"],
            capture_output=True, text=True, timeout=90,
            env=env, cwd=str(_repo_root()), stdin=subprocess.DEVNULL,
        )
        assert proc.returncode != 0, (
            "a second MCP server opened a locked data root — this is the "
            "silent-corruption bug, not a style issue"
        )
        combined = proc.stdout + proc.stderr
        # The message has to be actionable: this is what a user sees in their
        # editor's MCP log when the second client fails to start.
        assert "data_root_locked" in combined
        assert "SODAMEM_DATA_ROOT" in combined or "single worker" in combined
    finally:
        holder.close()
        release_data_root_lock(tmp_path)


def _repo_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent


def test_local_backend_can_skip_the_lock_for_tests_only(tmp_path):
    a = LocalBackend(_settings(tmp_path), acquire_lock=False)
    b = LocalBackend(_settings(tmp_path), acquire_lock=False)
    assert a.mode == b.mode == "local"
    a.close()
    b.close()


# --- 2. remote transport ----------------------------------------------------

@pytest.fixture
def live_service(tmp_path, monkeypatch):
    """A real SodaMem HTTP service on an ephemeral port, plus the StoreManager
    it serves from — so a test can seed the store directly and then read it
    back through HTTP."""
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SODAMEM_API_KEY", API_KEY)
    monkeypatch.delenv("SODAMEM_AUTH_DISABLED", raising=False)
    reset_server_singletons()

    from server.app import create_app
    from server.stores import get_store_manager

    app = create_app(_settings(tmp_path))
    try:
        with running_service(app) as base_url:
            yield base_url, get_store_manager()
    finally:
        reset_server_singletons()
        release_data_root_lock(tmp_path)


def test_remote_backend_reads_through_http(live_service):
    base_url, stores = live_service
    _seed(stores, "alice", "prefers tabs over spaces")

    remote = RemoteBackend(base_url, API_KEY)
    listed = remote.list_memories(user_id="alice", limit=50, offset=0)

    assert listed["total"] == 1
    assert listed["memories"][0]["content"] == "prefers tabs over spaces"


def test_remote_backend_surfaces_the_services_error_envelope(live_service):
    base_url, _ = live_service
    remote = RemoteBackend(base_url, "wrong-key")

    with pytest.raises(BackendError) as exc:
        remote.list_memories(user_id="alice", limit=50, offset=0)

    message = str(exc.value)
    # Not "HTTP Error 401" — the service's own code/message, plus the fix.
    assert "unauthorized" in message
    assert "SODAMEM_API_KEY" in message


def test_remote_backend_explains_an_unreachable_service():
    remote = RemoteBackend(f"http://127.0.0.1:{free_port()}", API_KEY, timeout=2.0)

    with pytest.raises(BackendError) as exc:
        remote.list_memories(user_id="alice", limit=10, offset=0)

    message = str(exc.value)
    assert "cannot reach the SodaMem service" in message
    # A hook that fails at 3am should say what to run, not just what broke.
    assert "sodamem daemon ensure" in message


def test_remote_delete_archives_rather_than_erasing(live_service):
    """delete_memory means the same thing in both modes.

    The HTTP API's DELETE physically erases by default; the MCP tool has
    always tombstoned. Remote mode must pass mode=archive, or the identical
    tool call would destroy data in one mode and not the other.
    """
    base_url, stores = live_service
    fact = _seed(stores, "alice", "uses zsh")

    result = RemoteBackend(base_url, API_KEY).delete_memory(
        user_id="alice", memory_id=fact.fact_id)

    assert result["deleted"] is True
    assert result["already_deleted"] is False
    # The row is still there, archived — not gone.
    stored = stores.get("alice").store.get_fact_event(fact.fact_id)
    assert stored is not None
    assert stored.status.value == "archived"

    again = RemoteBackend(base_url, API_KEY).delete_memory(
        user_id="alice", memory_id=fact.fact_id)
    assert again["already_deleted"] is True


# --- 3. mode parity ---------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda b: b.list_memories(user_id="alice", limit=50, offset=0),
    lambda b: b.search(user_id="alice", query="tabs", top_k=10),
    lambda b: b.context(user_id="alice", query="tabs", token_budget=500),
    # The three retrieval shapes that had no HTTP route at all until this
    # change — they existed only over MCP, which made "can I ask for an
    # entity's history" depend on which transport you happened to hold.
    lambda b: b.entity_timeline(user_id="alice", entity_id="tabs"),
    lambda b: b.refine(user_id="alice", query="tabs", top_k=5, entity="",
                       session_id="", min_confidence=None,
                       occurred_from=None, occurred_to=None),
])
def test_local_and_remote_return_the_same_shape(live_service, call):
    """Same store, two backends, identical dicts.

    This is the test that keeps remote mode honest. It compares KEYS rather
    than only values because a mode-dependent extra field is exactly the kind
    of drift that survives a value-equality check on the happy path.
    """
    base_url, stores = live_service
    _seed(stores, "alice", "prefers tabs over spaces")

    local = LocalBackend(Settings(data_root=stores._settings.data_root),
                         stores, acquire_lock=False)
    remote = RemoteBackend(base_url, API_KEY)

    local_result = call(local)
    remote_result = call(remote)

    assert local_result.keys() == remote_result.keys()
    assert local_result == remote_result
