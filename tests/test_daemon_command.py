"""The daemon only ever runs on the interpreter that launched it (issue #14).

`_serve_command` used to prefer `shutil.which("uvicorn")`. PATH answers "is
there a uvicorn on this machine", not "where is SodaMem installed", and
uvicorn's CLI does `sys.path.insert(0, ".")` — so a stranger's uvicorn,
started in a source checkout, imports `server` and serves the user's stores
with its own chromadb. Chroma migrates a store's schema FORWARD on open with
no downgrade path, so one such start permanently locks the correct install
out of its own memory (that chain produced #13 and #15).

The regression gate below therefore runs with a POISONED PATH and asserts the
poisoning worked before asserting anything else: a test that passes merely
because the runner's PATH happened to have no uvicorn is not a gate.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from sodamem_cli import daemon


def _poison_path(tmp_path, monkeypatch):
    """Put an executable decoy named `uvicorn` first on PATH. Returns its dir."""
    decoy_dir = tmp_path / "poison-bin"
    decoy_dir.mkdir()
    decoy = decoy_dir / "uvicorn"
    decoy.write_text("#!/bin/sh\nexit 1\n")
    decoy.chmod(decoy.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(decoy_dir))
    return decoy_dir, decoy


# --- AC1: the regression gate ---------------------------------------------

def test_serve_command_ignores_a_uvicorn_on_path(tmp_path, monkeypatch):
    decoy_dir, decoy = _poison_path(tmp_path, monkeypatch)

    # (1) The environment proves itself first. Without this the whole test
    # could pass because PATH simply had no uvicorn at all.
    assert shutil.which("uvicorn") == str(decoy)

    cmd = daemon._serve_command("127.0.0.1", 8000)

    # (2) The command runs THIS interpreter's uvicorn module.
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "uvicorn"]

    # (3) Nothing from the decoy's directory leaked into the command.
    assert all(str(decoy_dir) not in part for part in cmd), cmd


# --- AC2: the rest of the command was not damaged in passing ---------------

def test_serve_command_keeps_single_worker_and_factory_target():
    cmd = daemon._serve_command("0.0.0.0", 9137)

    assert "server.app:build" in cmd
    assert "--factory" in cmd
    # ADR 0001 §2: one writer is a correctness constraint, spelled out.
    assert cmd[cmd.index("--workers") + 1] == "1"
    # Non-default host/port so equality cannot be a coincidence.
    assert cmd[cmd.index("--host") + 1] == "0.0.0.0"
    assert cmd[cmd.index("--port") + 1] == "9137"


# --- AC3: no uvicorn here means no spawn, no pid file ----------------------

def test_ensure_without_uvicorn_refuses_before_spawning(tmp_path, monkeypatch):
    monkeypatch.setenv("SODAMEM_HOME", str(tmp_path))
    monkeypatch.setattr(daemon, "status", lambda *a, **k: {"running": False, "url": "u"})
    monkeypatch.setattr(daemon, "find_spec", lambda name: None)

    def _boom(*a, **k):  # pragma: no cover - the point is that it never runs
        raise AssertionError("ensure() spawned a process without uvicorn")

    monkeypatch.setattr(daemon.subprocess, "Popen", _boom)

    result = daemon.ensure(url="http://127.0.0.1:8123")

    assert result["running"] is False
    assert result["started"] is False
    assert sys.executable in result["error"]
    assert "sodamem[server]" in result["error"]
    assert not daemon.pid_file().exists()


# --- AC4: the start header names the interpreter and the argv --------------

def test_ensure_logs_interpreter_and_command(tmp_path, monkeypatch):
    monkeypatch.setenv("SODAMEM_HOME", str(tmp_path))
    decoy_dir, _ = _poison_path(tmp_path, monkeypatch)
    monkeypatch.setenv("SODAMEM_API_KEY", "k")

    calls = {"n": 0}

    def _status(*a, **k):
        # Down first (so ensure spawns), healthy after.
        calls["n"] += 1
        if calls["n"] == 1:
            return {"running": False, "url": "u"}
        return {"running": True, "url": "u", "version": "x"}

    class _FakeProcess:
        pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr(daemon, "status", _status)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: _FakeProcess())

    result = daemon.ensure(url="http://127.0.0.1:8123")
    assert result["started"] is True

    log = daemon.log_file().read_text()
    assert f"interpreter={sys.executable}" in log
    assert "-m uvicorn" in log
    assert str(decoy_dir) not in log


# --- AC5 / AC6: the service says who it is, in the LOG only ----------------
#
# The service layer lives behind the [server] extra (invariant I1), so these
# three skip individually rather than at module scope — AC1-AC4 above are
# pure CLI and must still run on a base install.

def _server_env(monkeypatch, tmp_path):
    pytest.importorskip("fastapi", reason="server tests require the [server] extra")
    pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")
    from server.jobs import reset_job_runner
    from server.settings import reset_settings_cache
    from server.stores import reset_store_manager

    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("SODAMEM_AUTH_DISABLED", "true")
    monkeypatch.setenv("SODAMEM_API_KEY", "test-secret-key")
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "unused-test-key")

    def _reset():
        # job-runner threads USE stores: stop the runner before tearing down
        # the stores it writes to (see tests/_service.py).
        reset_job_runner()
        reset_store_manager()
        reset_settings_cache()

    _reset()
    return _reset


def test_create_app_logs_the_running_interpreter(monkeypatch, tmp_path, caplog):
    reset = _server_env(monkeypatch, tmp_path)
    from server.app import create_app
    try:
        # The record is emitted on `uvicorn.error` so it actually lands in
        # daemon.log — uvicorn configures no root handler, so a `server.app`
        # INFO would be dropped (verified against a real `daemon ensure`).
        # `at_level` on that logger, not the root one, because uvicorn's
        # dictConfig is a PROCESS GLOBAL: any earlier test that ran a service
        # with `log_level="error"` (tests/_service.py does) leaves
        # `uvicorn.error` pinned at ERROR for the rest of the session, which
        # is a property of the suite's logging state, not of this code.
        with caplog.at_level(logging.INFO, logger="uvicorn.error"):
            create_app()
        said = [r.getMessage() for r in caplog.records if "interpreter" in r.getMessage()]
        assert said, [r.getMessage() for r in caplog.records]
        assert sys.executable in said[0]
    finally:
        reset()


def test_chroma_probe_failure_never_breaks_startup(monkeypatch, tmp_path):
    reset = _server_env(monkeypatch, tmp_path)
    import server.stores as stores
    from server.app import create_app

    def _boom():
        raise RuntimeError("chroma probe exploded")

    monkeypatch.setattr(stores, "_installed_chroma", _boom)
    try:
        assert create_app() is not None
    finally:
        reset()


def test_health_body_gains_no_paths(monkeypatch, tmp_path):
    reset = _server_env(monkeypatch, tmp_path)
    from fastapi.testclient import TestClient
    from server.app import create_app
    try:
        with TestClient(create_app()) as client:
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        # AC6: the diagnostic goes to the log, NOT to an unauthenticated
        # endpoint bound by default to 0.0.0.0 (#13 AC8(v): no filesystem
        # paths in bodies).
        assert set(body) == {"status", "version", "schema_version", "auth"}
        for value in body.values():
            text = str(value)
            assert sys.executable not in text
            assert not text.startswith(os.sep)
    finally:
        reset()


# --- AC1b: the gate must also catch a PATH lookup that LOOKS safe ----------

def test_serve_command_ignores_a_uvicorn_inside_our_own_prefix(monkeypatch):
    """The decoy lives under `sys.prefix`, not in a tmp dir.

    The obvious "safe" reintroduction of the deleted branch is
    `[u] if u and u.startswith(sys.prefix) else [sys.executable, "-m", ...]`,
    and a decoy in `tmp_path` waves it straight through — the whole suite
    stayed green under exactly that mutation. So this case asserts the real
    invariant ("no PATH lookup"), not the weaker one the tmp_path case can
    see ("no RAW PATH lookup"). Both are kept: they fail on different
    mutations.

    The prefix's `bin` normally already contains a real `uvicorn`, which
    serves as the decoy with nothing written at all. Only if it is missing do
    we create one, and then restore the tree exactly; if we cannot, the case
    skips rather than leave anything behind in a live environment.
    """
    bin_dir = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    decoy = bin_dir / "uvicorn"
    created = False
    if not decoy.exists():
        if not bin_dir.is_dir():
            pytest.skip(f"no {bin_dir} to plant a decoy in")
        try:
            decoy.write_text("#!/bin/sh\nexit 1\n")
            decoy.chmod(0o755)
            created = True
        except OSError as exc:
            pytest.skip(f"cannot plant a decoy in {bin_dir}: {exc}")
    try:
        monkeypatch.setenv("PATH", str(bin_dir))
        # Self-check first, same as the tmp_path case: prove PATH really
        # resolves to the decoy before concluding anything from the command.
        assert shutil.which("uvicorn") == str(decoy)
        # And prove the decoy is inside sys.prefix, or this case is no
        # stronger than the one above.
        assert str(decoy).startswith(sys.prefix)

        cmd = daemon._serve_command("127.0.0.1", 8000)

        assert cmd[0] == sys.executable
        assert cmd[1:3] == ["-m", "uvicorn"]
        # The FILE, not its directory: `bin_dir` legitimately contains
        # `sys.executable` itself.
        assert all(str(decoy) not in part for part in cmd), cmd
    finally:
        if created:
            decoy.unlink(missing_ok=True)
            assert not decoy.exists()


# --- issue #18: `ensure` must survive the service it is replacing ---------

def test_ensure_survives_a_service_that_dies_mid_probe(monkeypatch):
    """`daemon stop` then `daemon ensure` is the ordinary restart, and it used
    to be the one input that produced a traceback.

    `ensure()` opens with `status()`, and `status()` probes a URL that, one
    command earlier, was a live daemon now in the middle of shutting down. A
    real socket reproduces that shape exactly: accept, read the request, close
    without answering. Measured against a real daemon under SIGTERM the same
    probe surfaces as `ConnectionResetError` — the window is sub-millisecond,
    which is why this is asserted here rather than by racing a subprocess.

    `find_spec` is stubbed to None so `ensure` returns its "nothing to start"
    dict instead of spawning uvicorn: the assertion is about surviving the
    probe, and a test that boots a server would be measuring the boot.
    """
    from tests.test_http_client import _drain_then_close, _socket_server

    monkeypatch.setattr(daemon, "find_spec", lambda name: None)

    with _socket_server(_drain_then_close) as port:
        url = f"http://127.0.0.1:{port}"
        result = daemon.ensure(url=url)   # must not raise

    assert result["running"] is False
    assert result["started"] is False
    assert result["url"] == url
