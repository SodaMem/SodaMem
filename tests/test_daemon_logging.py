"""Issue #19: under the daemon, every INFO from `server/` was dropped.

THE TRAP THESE TESTS EXIST TO AVOID. pytest's logging plugin attaches four
handlers to the ROOT logger for the duration of every single test (measured:
`_LiveLoggingNullHandler`, a `_FileHandler` on /dev/null, and two
`LogCaptureHandler`s), and `caplog.at_level()` additionally forces the level
down to INFO. So a test shaped like

    with caplog.at_level(logging.INFO):
        create_app()
    assert caplog.records

is GREEN ON UNFIXED CODE and gates precisely nothing. Emptying
`root.handlers` and putting root back at WARNING is a PRECONDITION of the
gate below, not a stylistic choice — it is the only way to reproduce, inside
pytest, the state uvicorn actually leaves behind under `daemon ensure`:

    root.handlers: []          root.level: 30 (WARNING)
    server.app effective level: WARNING

Note also that there were TWO independent killers, and a fix that addresses
only the handler one still drops everything in production. Hence the explicit
`getEffectiveLevel()` assertion in AC1.
"""
from __future__ import annotations

import io
import logging
import sys

import pytest

#: Every logger these tests (or the code under test, or uvicorn's dictConfig)
#: can mutate. Snapshotted and restored so the suite is order-independent.
_TOUCHED = ("", "server", "sodamem", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_logging():
    """Put the process-global logging state back exactly as it was.

    Handlers are restored by CONTENT into the original list object, so any
    reference pytest's own plugin holds stays valid.
    """
    saved = [
        (
            logging.getLogger(name),
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in _TOUCHED
    ]
    try:
        yield
    finally:
        for logger, handlers, level, propagate in saved:
            logger.handlers[:] = handlers
            logger.level = level
            logger.propagate = propagate


def _server_env(monkeypatch, tmp_path):
    """The same minimal service environment `tests/test_daemon_command.py`
    uses; the service layer is behind the [server] extra (invariant I1), so
    this module skips rather than fails on a base install."""
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
        reset_job_runner()
        reset_store_manager()
        reset_settings_cache()

    _reset()
    return _reset


def _become_the_daemon() -> None:
    """Reproduce the logging state uvicorn leaves behind at `create_app()`."""
    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(logging.WARNING)
    logging.getLogger("server").setLevel(logging.NOTSET)
    logging.getLogger("sodamem").setLevel(logging.NOTSET)


# --- AC1: the gate that fails without the fix ------------------------------

def test_info_from_server_reaches_the_log_in_daemon_state(
    monkeypatch, tmp_path, restore_logging
):
    reset = _server_env(monkeypatch, tmp_path)
    from server.app import create_app

    _become_the_daemon()
    assert logging.getLogger("server.app").getEffectiveLevel() == logging.WARNING, (
        "precondition: this is the state the daemon really runs in"
    )

    # Bind the handler to a buffer rather than the real stderr so `create_app`'s
    # own start-up records do not become new noise in the suite's output (AC2).
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    try:
        create_app()

        # KILLER 2 — a handler now exists, and it is a StreamHandler. NOT a
        # FileHandler: `dictConfig()` calls `_clearExistingHandlers()`, which
        # `logging.shutdown()`s every registered handler, and tests/_service.py
        # builds the app before `uvicorn.Config`, so this handler does get
        # closed. `StreamHandler.close()` leaves the stream alone;
        # `FileHandler.close()` would make every later emit raise.
        root = logging.getLogger()
        assert len(root.handlers) == 1, root.handlers
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert not isinstance(handler, logging.FileHandler)

        # KILLER 1 — the one an "add a handler" fix misses entirely. Without
        # this, `logger.info(...)` returns inside `isEnabledFor()` and the
        # record is never created, in production, forever.
        assert logging.getLogger("server.app").getEffectiveLevel() <= logging.INFO

        # SCOPE, pinned rather than merely tolerated: `sodamem` stays at
        # WARNING. Issue #19 is about the service layer, and waking the
        # library's INFO sites would start writing user-derived content
        # (`ingest/extractor.py`'s `raw_value` / `predicate_raw`) into
        # `daemon.log` in the clear. That is a separate decision.
        assert logging.getLogger("sodamem").getEffectiveLevel() == logging.WARNING
        assert logging.getLogger("sodamem.memory").getEffectiveLevel() == logging.WARNING

        stream = io.StringIO()
        handler.setStream(stream)
        logging.getLogger("server.app").info(
            "GET /health -> 200 in 1.2ms (rid=%s)", "abc123"
        )
        rendered = stream.getvalue()
        assert "GET /health -> 200 in 1.2ms (rid=abc123)" in rendered, rendered
        assert "INFO" in rendered and "server.app" in rendered, rendered

        # Root itself stays at WARNING, so chromadb/httpx/openai do not flood
        # the daemon log. Selection is done by logger level alone.
        assert root.level == logging.WARNING
        assert handler.level == logging.NOTSET
    finally:
        reset()


# --- AC3: a provable no-op wherever logging is already configured ----------

def test_create_app_is_a_logging_no_op_under_pytest(monkeypatch, tmp_path):
    """Gates the GUARD, so nobody later "simplifies" it into unconditional
    configuration and starts writing duplicate lines into every test's
    output. Without the fix this fails at import (the symbol does not exist).
    """
    reset = _server_env(monkeypatch, tmp_path)
    from server.app import create_app
    from server.logging_setup import configure_logging_if_unconfigured

    root = logging.getLogger()
    assert root.handlers, "pytest is expected to have configured logging already"
    before = list(root.handlers)
    try:
        assert configure_logging_if_unconfigured() is False
        create_app()
        assert list(root.handlers) == before
    finally:
        reset()


def test_configuring_twice_installs_one_handler(restore_logging):
    """Idempotence — the second call sees a non-empty `root.handlers`."""
    from server.logging_setup import configure_logging_if_unconfigured

    _become_the_daemon()
    assert configure_logging_if_unconfigured() is True
    assert configure_logging_if_unconfigured() is False
    assert len(logging.getLogger().handlers) == 1


# --- AC4: no duplicated lines against uvicorn's real logger tree -----------

def test_no_duplicate_lines_against_uvicorns_real_config(
    monkeypatch, tmp_path, restore_logging
):
    """Build the ACTUAL logger tree `uvicorn.Config.__init__` produces, then
    run `create_app()` on top of it, then emit on each logger that matters and
    require exactly one rendered line per record.

    `uvicorn` and `uvicorn.access` are `propagate=False` with their own
    handlers; `uvicorn.error` propagates but its chain terminates at
    `uvicorn`. So none of them can reach the root handler we install, and the
    root handler is the only one `server.*` records ever see.
    """
    reset = _server_env(monkeypatch, tmp_path)
    pytest.importorskip("uvicorn")
    import uvicorn.config
    from server.app import create_app

    err = io.StringIO()
    out = io.StringIO()
    # Patched BEFORE dictConfig runs: uvicorn's LOGGING_CONFIG resolves
    # `ext://sys.stderr` / `ext://sys.stdout` at configuration time.
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdout", out)
    try:
        _become_the_daemon()
        uvicorn.config.Config("server.app:build", factory=True)  # configure_logging()
        assert logging.getLogger().handlers == [], (
            "uvicorn's LOGGING_CONFIG has no `root` key; this is the bug"
        )

        create_app()

        logging.getLogger("server.app").info("MARKER-server-app")
        logging.getLogger("uvicorn.error").info("MARKER-uvicorn-error")
        # Shaped for uvicorn's AccessFormatter, which unpacks exactly five
        # args; a bare message makes the formatter raise and `handleError`
        # then echoes the message to stderr a second time.
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:1234", "GET", "/MARKER-uvicorn-access", "1.1", 200,
        )
        for handler in logging.getLogger().handlers + logging.getLogger("uvicorn").handlers:
            handler.flush()

        combined = err.getvalue() + out.getvalue()
        for marker in ("MARKER-server-app", "MARKER-uvicorn-error", "MARKER-uvicorn-access"):
            assert combined.count(marker) == 1, (marker, combined)
    finally:
        reset()
