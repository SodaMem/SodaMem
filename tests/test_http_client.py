"""`Client.call()` must be total over "the service is not answering".

urllib only wraps the CONNECT phase. `AbstractHTTPHandler.do_open` turns the
`OSError` from `h.request(...)` into a `URLError`; whatever `h.getresponse()`
or `r.read()` raises is handed to the caller untouched. So "nothing is
listening" was handled and "something accepted me and then died" was not —
and the second one is exactly what `daemon stop` leaves behind, one command
before the `daemon ensure` that crashed (issue #18).

The two gates below use REAL listening sockets, because the interesting fact
is which exception the kernel and `http.client` produce between them, and a
mocked `urlopen` can only assert the answer someone already assumed. The
mock-based test is a third, cheaper statement of the catch set — never the
proof.

The pair matters as a pair: the same shutdown surfaces as
`RemoteDisconnected` when the dying server drained the request and as
`ConnectionResetError` when it did not. Either gate alone would let half of
the bug back in.
"""
from __future__ import annotations

import http.client
import socket
import threading
import time
import urllib.error
from contextlib import contextmanager

import pytest

from sodamem_cli import daemon
from sodamem_cli.http import Client, ServiceError
from ._service import free_port


@contextmanager
def _socket_server(handle):
    """A real listening socket on loopback. `handle(conn)` owns the accepted
    connection; the connection is closed for it on the way out.

    No uvicorn, no mock: the point is what the network stack actually does.
    """
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:  # pragma: no cover - listener closed under us
                return
            try:
                handle(conn)
            except OSError:  # pragma: no cover - client hung up first
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=5)
        srv.close()
        assert not thread.is_alive(), "socket server thread leaked"


def _drain_then_close(conn):
    """Read the whole request, answer nothing. The graceful-shutdown shape."""
    conn.settimeout(5)
    seen = b""
    while b"\r\n\r\n" not in seen:
        chunk = conn.recv(4096)
        if not chunk:
            break
        seen += chunk
    assert seen.startswith(b"GET /health"), seen


def _close_without_reading(conn):
    """Never read the request, then close. The kernel answers with RST.

    The sleep is not a race patch — it is the point. Closing before the client
    has finished writing would fail inside `h.request()`, which urllib already
    wraps into `URLError`, i.e. a case that passed before this fix. Letting the
    request land in a buffer nobody reads is what puts the failure in
    `getresponse()`, where the bug lives.
    """
    time.sleep(0.3)


# --- AC1: real socket, request drained, then closed -----------------------

def test_service_that_reads_then_closes_is_a_service_error():
    with _socket_server(_drain_then_close) as port:
        url = f"http://127.0.0.1:{port}"

        with pytest.raises(ServiceError) as caught:
            Client(url, timeout=5.0).health()

        # AC4: the sentence has to carry the three things a user acts on.
        message = str(caught.value)
        assert url in message
        assert type(caught.value.__cause__).__name__ in message
        assert "`sodamem daemon ensure`" in message
        # `str(TimeoutError())` is "" — a message built only from `{exc}` would
        # be a half sentence. The class name is what keeps it whole.
        assert message.rstrip().endswith("Run `sodamem daemon ensure` to start one.")

        # And the caller this issue is about gets its designed answer, not a
        # traceback out of `ensure()`.
        state = daemon.status(url)
        assert state["running"] is False
        assert state["url"] == url
        assert "cannot reach" in state["error"]


# --- AC2: real socket, request NOT read, then closed ----------------------

def test_service_that_closes_without_reading_is_a_service_error():
    with _socket_server(_close_without_reading) as port:
        url = f"http://127.0.0.1:{port}"

        with pytest.raises(ServiceError) as caught:
            Client(url, timeout=5.0).health()
        assert "`sodamem daemon ensure`" in str(caught.value)

        assert daemon.status(url)["running"] is False


# --- AC3: the catch set itself, stated directly ---------------------------

@pytest.mark.parametrize("exc", [
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    ConnectionResetError(54, "Connection reset by peer"),
    TimeoutError(),
    http.client.IncompleteRead(b""),
])
def test_response_phase_failures_become_service_errors(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr("urllib.request.urlopen", boom)
    client = Client(f"http://127.0.0.1:{free_port()}", timeout=1.0)

    with pytest.raises(ServiceError) as caught:
        client.health()
    assert caught.value.__cause__ is exc
    assert type(exc).__name__ in str(caught.value)


# --- AC6: connection-refused is not collateral damage ---------------------

def test_nothing_listening_still_reports_connection_refused():
    url = f"http://127.0.0.1:{free_port()}"

    with pytest.raises(ServiceError) as caught:
        Client(url, timeout=2.0).health()

    assert isinstance(caught.value.__cause__, urllib.error.URLError)
    assert not isinstance(caught.value.__cause__, urllib.error.HTTPError)
    # Word for word the sentence this branch printed before the fix — only the
    # errno number is left loose, because it is the platform's, not ours.
    message = str(caught.value)
    assert message.startswith(f"cannot reach the SodaMem service at {url}: [Errno ")
    assert message.endswith(
        "] Connection refused. Run `sodamem daemon ensure` to start one.")


def test_programming_errors_are_not_swallowed(monkeypatch):
    """The widened catch must not become a bug hider."""
    def boom(*args, **kwargs):
        raise AttributeError("typo in the client, not a dead service")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(AttributeError):
        Client("http://127.0.0.1:1", timeout=1.0).health()
