"""The CLI's HTTP client. stdlib only, on purpose.

`sodamem hook recall` runs on EVERY prompt the user submits, as a fresh
process, inside a 30-second budget the editor blocks on. Import time is
latency the user feels, so this module imports nothing that is not already in
the interpreter — no requests, no httpx, no pydantic. The whole surface is
five JSON calls.

It also means `pip install sodamem` is enough to run the integration: the
`[server]` and `[mcp]` extras are what the SERVICE needs, not what a hook
needs to talk to one.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000"


class ServiceError(RuntimeError):
    """The service was reached and refused, or could not be reached at all."""


class Client:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 10.0) -> None:
        self.base_url = (base_url or os.environ.get("SODAMEM_API_URL")
                         or DEFAULT_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(
            "SODAMEM_API_KEY", "")
        self.timeout = timeout

    def call(self, method: str, path: str, *, query: dict | None = None,
             body: dict | None = None, timeout: float | None = None):
        url = f"{self.base_url}{path}"
        if query:
            clean = {k: v for k, v in query.items() if v not in (None, "")}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            raise ServiceError(_explain(exc)) from exc
        except urllib.error.URLError as exc:
            raise ServiceError(
                f"cannot reach the SodaMem service at {self.base_url}: "
                f"{exc.reason}. Run `sodamem daemon ensure` to start one."
            ) from exc
        except http.client.InvalidURL:
            # Carved out of the branch below, which would otherwise catch it as
            # an `HTTPException` and answer it with "run `sodamem daemon
            # ensure`" — a remedy that cannot possibly work, because the URL
            # itself is malformed. It is not even a response-phase failure:
            # it comes out of `HTTPConnection.__init__` -> `_get_hostport`,
            # before a socket exists.
            #
            # A friendlier message here would be a lie of a different kind.
            # Measured: `status()` would return a tidy sentence and then
            # `ensure()` would die three lines later in `_split()` with
            # `ValueError: Port could not be cast to integer value` — a WORSE
            # traceback than the `InvalidURL` the user gets today, since it no
            # longer names the URL as the problem. Nothing `call()` can do
            # fixes that; it needs `_split()`, which this change does not
            # touch. So leave the behaviour exactly as it was and let the
            # config error stay a config error.
            raise
        except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
            # The two branches above only cover the CONNECT phase: urllib's
            # `AbstractHTTPHandler.do_open` wraps the `OSError` from
            # `h.request(...)` into `URLError` and nothing else. Whatever
            # `h.getresponse()` or `r.read()` raises walks straight out — so a
            # service that dies after accepting us used to reach the user as a
            # raw traceback (issue #18), on the one path whose whole job is to
            # answer "is anything alive here?".
            #
            # `ConnectionError` is the issue proper: the SAME shutdown yields
            # `RemoteDisconnected` when the dying server drained our request
            # and `ConnectionResetError` when it did not, so catching either
            # one alone leaves the other escaping. `TimeoutError` is the
            # service that is up but wedged — the same answer to the caller,
            # and since 3.10 it IS `socket.timeout`. `http.client.HTTPException`
            # is "what came back is not a whole HTTP response" — `IncompleteRead`,
            # `BadStatusLine`, `LineTooLong`, `UnknownProtocol`: all of them the
            # service failing to answer, not a bug here. Its one member that is
            # NOT that is `InvalidURL`, re-raised just above.
            #
            # Deliberately NOT bare `OSError` — it would also swallow OS
            # failures that have nothing to do with the network. `json.loads`
            # stays outside the `try`: a complete response that is not JSON is
            # the service's bug and must not be dressed up as "not running".
            #
            # `import http.client` costs nothing: `urllib.request` already
            # imports it, so this module still imports nothing that is not
            # already in the interpreter (see the module docstring — hook
            # import time is latency the user feels).
            raise ServiceError(
                f"cannot reach the SodaMem service at {self.base_url}: "
                f"{type(exc).__name__}: {exc}. "
                f"Run `sodamem daemon ensure` to start one."
            ) from exc
        return json.loads(raw) if raw else {}

    # -- the five calls the integration actually makes ----------------------

    def health(self) -> dict:
        return self.call("GET", "/health", timeout=3.0)

    def context(self, *, user_id: str, query: str, project_id: str = "",
                token_budget: int = 1200) -> dict:
        return self.call("GET", "/v1/context", query={
            "user_id": user_id, "query": query, "project_id": project_id,
            "token_budget": token_budget,
        })

    def add(self, *, user_id: str, messages: list[dict], session_id: str,
            project_id: str = "", timeout: float = 120.0) -> dict:
        return self.call("POST", "/v1/memories", body={
            "user_id": user_id, "project_id": project_id or None,
            "messages": messages, "session_id": session_id,
            "async_mode": True,
        }, timeout=timeout)

    def search(self, *, user_id: str, query: str, project_id: str = "",
               top_k: int = 10) -> dict:
        return self.call("POST", "/v1/search", body={
            "user_id": user_id, "project_id": project_id or None,
            "query": query, "top_k": top_k,
        })


def _explain(exc: urllib.error.HTTPError) -> str:
    """The service's own error envelope, plus the fix where there is one."""
    try:
        payload = json.loads(exc.read() or b"{}")
    except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
        payload = {}
    code = payload.get("code") or f"http_{exc.code}"
    message = payload.get("message") or exc.reason or "request failed"
    if exc.code == 401:
        message = f"{message} — set SODAMEM_API_KEY to a key this service accepts"
    return f"{code}: {message}"
