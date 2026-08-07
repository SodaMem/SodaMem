"""Outbound change webhooks (PRD §1.2 observability: "每次记忆变更发事件 + webhook").

`/v1/events` lets a caller ASK what changed. This lets another system be TOLD,
so it can invalidate a cache, notify a user or mirror an audit log without
holding a polling loop against this service.

The subscriber is a third party whose availability and latency we do not
control, which dictates the whole design:

- **Off by default.** No URL configured means no thread, no queue, no egress —
  a self-hosted deployment that never opted in must not phone anywhere.
- **Out of band.** Delivery happens on a worker thread. The request that
  caused the change has already committed; making it wait on someone else's
  server would be adding their p99 to ours.
- **Bounded.** A fixed-size queue. An unreachable receiver in a month-long
  process is a dropped-event counter, not a memory leak. Drops are counted and
  logged — silently discarding them would be the same failure with worse
  forensics.
- **Never raises into the caller.** Their outage is not our 500.
- **Signed.** HMAC-SHA256 over the exact bytes sent, so a receiver can tell a
  genuine delivery from anyone who learned the URL.

Transport is stdlib `urllib.request`, not httpx/requests: the `[server]` extra
is deliberately three packages wide (invariant I1), and adding an HTTP client
so we can send webhooks is exactly the creep that invariant exists to stop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-SodaMem-Signature"
DEFAULT_MAX_QUEUE = 1000
DEFAULT_TIMEOUT_S = 5.0

Transport = Callable[[str, bytes, dict[str, str]], None]

# Sentinel that tells the worker to exit. A dedicated object (not None) so it
# can never collide with a real event.
_SHUTDOWN = object()


def sign_payload(body: bytes, secret: str) -> str:
    """`sha256=<hex>` over the EXACT bytes sent.

    Signing the serialized body rather than the event dict is what makes the
    signature checkable: the receiver has the bytes, not our dict, and any
    re-serialization on either side (key order, separators) would break a
    signature computed over the object.
    """
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _urllib_transport(url: str, body: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
        if response.status >= 400:
            raise OSError(f"webhook receiver returned {response.status}")


class WebhookDispatcher:
    """Best-effort, bounded, signed delivery of change events."""

    def __init__(self, url: str = "", secret: str = "", *,
                 max_queue: int = DEFAULT_MAX_QUEUE,
                 transport: Transport | None = None,
                 start_worker: bool = True) -> None:
        self._url = url
        self._secret = secret
        self._transport = transport or _urllib_transport
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._counts = {"delivered": 0, "failed": 0, "dropped": 0}
        self._counts_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        if url and start_worker:
            # Daemon: a process shutting down must not be held open by an
            # undelivered webhook to a receiver that may never answer.
            self._worker = threading.Thread(target=self._run, daemon=True,
                                            name="sodamem-webhooks")
            self._worker.start()

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def dispatch(self, event: dict[str, Any]) -> bool:
        """Enqueue an event. Returns whether it was ACCEPTED, which is not a
        claim that it was delivered — that is what `stats()` is for."""
        if not self._url:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            with self._counts_lock:
                self._counts["dropped"] += 1
            logger.warning(
                "webhook queue full (%d events); dropping event %r. The "
                "receiver at %s is not keeping up or is unreachable.",
                self._queue.maxsize, event.get("type"), self._url,
            )
            return False

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            if event is _SHUTDOWN:
                self._queue.task_done()
                return
            try:
                self._deliver(event)
            finally:
                self._queue.task_done()

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker and wait for it to exit.

        A dispatcher without this leaks a thread that blocks on `get()`
        forever. Daemon status keeps that from holding up interpreter exit,
        but it does NOT make the thread harmless: it stays alive alongside
        whatever else the process is doing, which is how a leaked worker took
        down a test run that happened to order chromadb work after it.
        Anything that creates a dispatcher owns closing it.
        """
        if self._worker is None:
            return
        self._queue.put(_SHUTDOWN)
        self._worker.join(timeout=timeout)
        self._worker = None

    def __enter__(self) -> "WebhookDispatcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _deliver(self, event: dict[str, Any]) -> None:
        # sort_keys so a signature computed here and re-derived by a receiver
        # over the same logical event agree byte for byte.
        body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "User-Agent": "sodamem-webhooks"}
        if self._secret:
            headers[SIGNATURE_HEADER] = sign_payload(body, self._secret)
        try:
            self._transport(self._url, body, headers)
        except Exception as exc:  # noqa: BLE001 - a third party's outage is not ours
            with self._counts_lock:
                self._counts["failed"] += 1
            logger.warning("webhook delivery to %s failed: %s", self._url, exc)
            return
        with self._counts_lock:
            self._counts["delivered"] += 1

    def flush(self, timeout: float | None = None) -> None:
        """Block until the queue drains. For tests and clean shutdown — the
        request path never calls this."""
        if self._worker is None:
            # No worker (unconfigured, or start_worker=False): drain inline so
            # flush() means the same thing in both modes.
            while not self._queue.empty():
                self._deliver(self._queue.get_nowait())
                self._queue.task_done()
            return
        self._queue.join()

    def stats(self) -> dict[str, int]:
        with self._counts_lock:
            return dict(self._counts)


_DISPATCHER: WebhookDispatcher | None = None


def get_webhook_dispatcher() -> WebhookDispatcher:
    global _DISPATCHER
    if _DISPATCHER is None:
        from server.settings import get_settings
        settings = get_settings()
        _DISPATCHER = WebhookDispatcher(
            url=getattr(settings, "webhook_url", "") or "",
            secret=getattr(settings, "webhook_secret", "") or "",
        )
    return _DISPATCHER


def reset_webhook_dispatcher() -> None:
    """Drop the process-wide dispatcher, stopping its worker first — a reset
    that abandoned the thread would leak one per call."""
    global _DISPATCHER
    if _DISPATCHER is not None:
        _DISPATCHER.close()
    _DISPATCHER = None
