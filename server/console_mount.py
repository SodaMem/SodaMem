"""Optional mount point for the pre-built web console (`console/dist/`).

Deliberately decoupled from `server/app.py`: the console is a separate,
independently-built artifact (Vite + React + shadcn/ui under `console/`), and
the API must run identically whether or not anyone has ever run
`npm run build` there. `mount_console()` is the one integration point; the
app factory calls it and decides nothing else about the console.

No silent failures (project policy): a missing `console/dist/` is not an
error — most deployments don't need the console — but it is never silent
either. We log once, at INFO, and return False so the caller can decide
whether that's worth surfacing further.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger(__name__)

# server/console_mount.py -> repo root -> console/dist
_CONSOLE_DIST = (Path(__file__).resolve().parent.parent / "console" / "dist").resolve()

MOUNT_PATH = "/console"


class _SpaStaticFiles(StaticFiles):
    """Serves the built SPA, falling back to `index.html` for any request
    that doesn't resolve to a real file on disk.

    This is what makes client-side routes work on a hard refresh or a
    deep link (e.g. `GET /console/memories`) — the console's own router runs
    in the browser after `index.html` loads and never asks the server to
    understand `/console/memories` as a real path. A genuinely missing asset
    (a stale hashed JS chunk after a redeploy) still round-trips through this
    fallback and serves the shell rather than a bare 404, which is the
    standard, accepted trade-off for SPA hosting.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            # `self.directory`, NOT the module-level `_CONSOLE_DIST`. They are
            # the same value for the mount `mount_console` builds, which is
            # what hid this: the fallback read a global instead of the
            # directory THIS instance was constructed with, so an instance
            # pointed anywhere else served the wrong shell — or, when the
            # global's index.html did not exist, raised FileNotFoundError out
            # of the response body and turned a deep link into a 500.
            shell = Path(self.directory) / "index.html"
            if not shell.is_file():
                # The mount exists but its shell is gone (a dist wiped after
                # start-up). 404 is the honest answer; a 500 from an
                # unhandled FileNotFoundError is not.
                raise
            return FileResponse(shell)


def mount_console(app: FastAPI) -> bool:
    """Mount the built console at `/console` if it exists.

    Returns True if mounted, False if `console/dist/` is absent (logged at
    INFO, not raised — the API itself must never fail to start because a
    frontend build artifact is missing).
    """
    if not (_CONSOLE_DIST / "index.html").is_file():
        logger.info(
            "Console not mounted: %s has no index.html. Run `npm install && "
            "npm run build` in console/ to build it, or ignore this if you "
            "don't need the web console.",
            _CONSOLE_DIST,
        )
        return False

    app.mount(
        MOUNT_PATH,
        _SpaStaticFiles(directory=_CONSOLE_DIST, html=True),
        name="console",
    )
    logger.info("Console mounted at %s from %s", MOUNT_PATH, _CONSOLE_DIST)
    return True
