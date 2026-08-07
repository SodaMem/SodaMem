"""Inbound API-key auth. On by default; opt out explicitly.

Accepts either `Authorization: Bearer <key>` or `X-API-Key: <key>` — mem0 and
Zep Cloud each picked one, callers arrive expecting both.

Two sources of truth, in this order:

1. **The bootstrap key** (`SODAMEM_API_KEY`). Works exactly as it did before
   named keys existed — an upgrade must not lock an operator out of their own
   deployment. It lives only in the environment, so it cannot be revoked
   through the API: that is the deliberate way back in if every named key is
   revoked (including by whoever leaked one). Compared in constant time,
   since it is the one secret this process compares directly.

2. **Named keys** in the control plane (ADR 0001). These are what make ops
   visibility mean anything — "who called this route" needs a `who`, and one
   shared secret has no answer. Stored as SHA-256 digests, so a leaked
   control database yields no working credentials.

There are no privilege tiers in v1. Any live key can read ops data and mint
or revoke other keys; the bootstrap key is the recovery path. Roles are the
multi-tenant control plane this project deliberately did not build — adding
them here would invent an authorization model nobody has asked for yet, and a
half-designed one is worse than a documented flat one.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, Request, status

from server.settings import Settings, get_settings

#: `request.state` attribute holding the caller's identity for this request.
#: Set by the dependency, read by the request-log middleware in
#: `server/app.py`. Starlette backs `.state` with `scope["state"]`, so what
#: the dependency writes on the endpoint's Request is visible on the
#: middleware's Request — same scope, same dict.
CALLER_STATE_KEY = "sodamem_caller"

BOOTSTRAP_CALLER = "bootstrap"
ANONYMOUS_CALLER = "anonymous"


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Authenticate, and return the caller's name (also stashed on
    `request.state` for the middleware that logs it)."""
    if settings.auth_disabled:
        return _remember(request, ANONYMOUS_CALLER)

    presented = ""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            presented = token.strip()
    if not presented and x_api_key:
        presented = x_api_key.strip()
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key (Authorization: Bearer <key> or X-API-Key)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.api_key and secrets.compare_digest(presented, settings.api_key):
        return _remember(request, BOOTSTRAP_CALLER)

    from server.control import get_control_plane
    record = get_control_plane().verify_api_key(presented)
    if record is not None:
        return _remember(request, record.name)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key"
    )


def _remember(request: Request, caller: str) -> str:
    setattr(request.state, CALLER_STATE_KEY, caller)
    return caller


def current_caller(request: Request) -> str | None:
    """The authenticated caller's name, or None for a request that never got
    past auth. A 401 has no caller, and recording one would be a lie in the
    ops view."""
    return getattr(request.state, CALLER_STATE_KEY, None)
