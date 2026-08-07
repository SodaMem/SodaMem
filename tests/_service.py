"""A real SodaMem HTTP service, in a thread, for tests that need one.

Two test modules need to speak actual HTTP to actual routes — the MCP remote
backend and the CLI hook path both exist to talk to a service, and a stubbed
transport would verify neither the headers nor the error envelopes nor the
query encoding they exist to get right.

Both used to carry their own copy of this. The copies had already drifted
(one reset the settings cache, the other did not, which let one module's
Settings authenticate the other's requests), and they shared a shutdown bug:

    server.should_exit = True
    thread.join(timeout=15)

A join that TIMES OUT leaves a live daemon thread holding uvloop and an event
loop, which the interpreter then tears down underneath it — observed once in
~45 full-suite runs as a fatal error with no test failure and no summary
line, i.e. the least debuggable possible outcome. `should_exit` is uvicorn's
graceful request; `force_exit` is its escalation. Use both, then assert the
thread is actually gone rather than hoping.
"""
from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

#: Graceful shutdown, then hard shutdown, then give up and say so.
_GRACEFUL_S = 10.0
_FORCED_S = 5.0
_STARTUP_S = 15.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_service(app, *, port: int | None = None):
    """Yield the base URL of `app` served on loopback, and shut it down for
    real on the way out."""
    import uvicorn

    port = port or free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + _STARTUP_S
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:  # pragma: no cover - CI smoke failure
        server.should_exit = True
        raise RuntimeError("test service did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=_GRACEFUL_S)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=_FORCED_S)
        assert not thread.is_alive(), (
            "the test service thread outlived its shutdown; leaving it running "
            "lets the interpreter tear down uvloop underneath it, which "
            "surfaces as a fatal error in an unrelated test"
        )


def reset_server_singletons() -> None:
    """Every process-global the service layer keeps, in one call.

    Settings is the one that gets forgotten, and it is the one that matters
    most: auth resolves through the CACHED global (`Depends(get_settings)`),
    not through the object handed to `create_app`, so a Settings left behind
    by another module authenticates this one's requests.
    """
    from server.control import reset_control_plane
    from server.jobs import reset_job_runner
    from server.settings import reset_settings_cache
    from server.stores import reset_store_manager

    # ORDER MATTERS, and it is the reverse of the dependency direction:
    # job-runner threads USE stores, so the runner has to stop before the
    # stores it is writing to are torn down. Resetting stores first is what
    # produced the suite's intermittent segfault — a worker thread inside
    # `append_extraction_trace` while the main thread closed that very
    # connection. `close_all` now defers borrowed stores as a second line of
    # defence, but the ordering is the first one.
    reset_job_runner()      # waits for in-flight jobs (JobRunner.shutdown)
    reset_store_manager()   # now safe: nothing is mid-write
    reset_control_plane()
    reset_settings_cache()
