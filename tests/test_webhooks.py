"""Outbound change webhooks — the half of PRD §1.2's observability row that
had no code ("每次记忆变更发事件 + webhook"; `/v1/events` shipped, this did not).

Polling `/v1/events` answers "what changed" only for someone already asking.
A webhook is what lets another system react — invalidate a cache, notify a
user, mirror into an audit log — without holding this service's read path open
in a loop.

Three properties matter more than the feature itself, because a webhook
subscriber is a THIRD PARTY whose behaviour we do not control:

1. **It must never affect the request that caused it.** A slow or dead
   receiver cannot be allowed to add latency to an ingest, let alone fail it.
2. **It must be bounded.** An unreachable receiver must not accumulate an
   unbounded backlog in a long-lived process.
3. **It must be verifiable.** The receiver has to be able to tell a genuine
   delivery from anyone who learned the URL, so payloads are HMAC-signed.

Delivery uses stdlib urllib rather than httpx/requests: the `[server]` extra
is deliberately three packages wide, and "we added a HTTP client to send
webhooks" is exactly the kind of creep invariant I1 exists to stop.

Every dispatcher below is closed, via `with`. That is not tidiness: a
dispatcher owns a worker thread, and leaving them running turned this file
into an intermittent SEGFAULT of the whole suite — the leaked threads
outlived their tests and were still alive when a later test drove chromadb.
`close()` exists because of that, and `with` is how these tests keep the
guarantee structural rather than remembered.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from server.webhooks import WebhookDispatcher, sign_payload


def test_no_url_configured_is_a_silent_no_op():
    """Webhooks are opt-in. An unconfigured deployment must not start a
    delivery thread or touch the network at all."""
    calls = []
    with WebhookDispatcher(url="", transport=lambda *a, **k: calls.append(a)) as d:
        assert d.dispatch({"type": "memory_add"}) is False
        d.flush()
    assert calls == []


def test_configured_dispatcher_delivers_the_event_body():
    sent = []
    with WebhookDispatcher(
        url="https://example.test/hook",
        transport=lambda url, body, headers: sent.append((url, body, headers)),
    ) as d:
        assert d.dispatch({"type": "memory_add", "memory_id": "m1"}) is True
        d.flush()
    assert len(sent) == 1
    url, body, _ = sent[0]
    assert url == "https://example.test/hook"
    assert json.loads(body)["memory_id"] == "m1"


def test_payload_is_hmac_signed_so_a_receiver_can_verify_it():
    """Without a signature, anyone who learns the URL can forge a change
    event — and the receiver's whole reason to trust it is that we sent it."""
    sent = []
    with WebhookDispatcher(
        url="https://example.test/hook", secret="s3cret",
        transport=lambda url, body, headers: sent.append((url, body, headers)),
    ) as d:
        d.dispatch({"type": "memory_delete"})
        d.flush()
    _, body, headers = sent[0]
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["X-SodaMem-Signature"], expected)
    assert sign_payload(b"abc", "s3cret") == (
        "sha256=" + hmac.new(b"s3cret", b"abc", hashlib.sha256).hexdigest()
    )


def test_a_failing_receiver_never_reaches_the_caller():
    """The ingest that triggered this already committed. Raising here would
    turn someone else's outage into our 500."""
    def boom(url, body, headers):
        raise ConnectionError("receiver is down")

    with WebhookDispatcher(url="https://example.test/hook", transport=boom) as d:
        assert d.dispatch({"type": "memory_add"}) is True  # accepted, not delivered
        d.flush()  # must return, not raise
        assert d.stats()["failed"] == 1


def test_queue_is_bounded_and_drops_rather_than_growing():
    """A dead receiver in a month-long process must not become a memory leak.
    Dropping is the honest failure: it is counted and logged, not hidden."""
    with WebhookDispatcher(url="https://example.test/hook", max_queue=4,
                           transport=lambda *a, **k: None,
                           start_worker=False) as d:
        accepted = [d.dispatch({"n": i}) for i in range(20)]
        assert accepted.count(True) == 4
        assert accepted.count(False) == 16
        assert d.stats()["dropped"] == 16


def test_delivery_reports_success_count():
    with WebhookDispatcher(url="https://example.test/hook",
                           transport=lambda *a, **k: None) as d:
        for i in range(3):
            d.dispatch({"n": i})
        d.flush()
        assert d.stats()["delivered"] == 3
        assert d.stats()["failed"] == 0


def test_unsigned_dispatcher_omits_the_signature_header_entirely():
    """An empty signature would look like a signature. Absence is honest."""
    sent = []
    with WebhookDispatcher(
        url="https://example.test/hook", secret="",
        transport=lambda url, body, headers: sent.append(headers),
    ) as d:
        d.dispatch({"type": "memory_add"})
        d.flush()
    assert "X-SodaMem-Signature" not in sent[0]


def test_close_stops_the_worker_thread():
    """The guarantee the rest of this file leans on. A dispatcher that cannot
    be stopped leaks a thread per construction — which is exactly how this
    suite started segfaulting."""
    d = WebhookDispatcher(url="https://example.test/hook",
                          transport=lambda *a, **k: None)
    worker = d._worker
    assert worker is not None and worker.is_alive()
    d.close()
    assert not worker.is_alive()


def test_close_is_idempotent():
    d = WebhookDispatcher(url="https://example.test/hook",
                          transport=lambda *a, **k: None)
    d.close()
    d.close()  # must not raise or hang


# --- wired into the change routes -------------------------------------------

def test_routes_emit_change_events(tmp_path, monkeypatch):
    """The dispatcher is only useful if the routes actually call it. Wiring is
    the part that silently rots — the unit tests above would stay green
    forever with nothing connected."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import server.webhooks as wh
    from tests.test_server_routes import _configure_env
    from server.app import create_app

    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)

    seen = []
    wh.reset_webhook_dispatcher()
    wh._DISPATCHER = wh.WebhookDispatcher(
        url="https://example.test/hook",
        transport=lambda url, body, headers: seen.append(json.loads(body)),
    )
    try:
        client = TestClient(create_app())
        client.post("/v1/memories", json={
            "user_id": "hooked", "async_mode": False,
            "messages": [{"role": "user", "content": "I keep bees on the roof."}],
        })
        wh._DISPATCHER.flush()
        assert any(e["type"] == "memory_add" for e in seen), seen
    finally:
        # Closes the worker as well — see reset_webhook_dispatcher().
        wh.reset_webhook_dispatcher()
