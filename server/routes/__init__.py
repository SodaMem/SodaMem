"""Mounts every v1 router. `/health` is the sole unauthenticated route — it
is wired directly in `server/app.py` before `register_routes` is even
called, so it never passes through this module at all; everything mounted
here sits behind `require_api_key`.

Every handler in these modules is declared `def`, NOT `async def`. That is
deliberate and load-bearing, not an oversight: the whole store layer is
blocking (SQLite, ChromaDB, ONNX embedding) and none of these handlers await
anything. FastAPI runs a `def` handler in its threadpool, but runs an
`async def` handler directly on the event loop — so a single `async def`
here stalls the entire process for the duration of one request. Measured on
one synchronous ingest: `/health` went from 9ms to 7406ms as `async def`,
and stayed at 120ms as `def`. That also defeats the container HEALTHCHECK
(`--timeout=5s --retries=3`), which would mark a perfectly healthy server
unhealthy mid-ingest.

If a handler ever genuinely needs `async def`, it must first push its
blocking work off the loop (`starlette.concurrency.run_in_threadpool`), not
just change the keyword. `tests/test_server_routes.py::
test_no_route_handler_is_a_coroutine_function` guards this.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI

from server.auth import require_api_key

from . import (
    admin, answer, context, events, graph, jobs, maintenance, memories,
    metrics, search,
    prometheus,
    usage,
)


def register_routes(app: FastAPI) -> None:
    auth_dep = [Depends(require_api_key)]
    app.include_router(admin.router, dependencies=auth_dep)
    app.include_router(memories.router, dependencies=auth_dep)
    app.include_router(search.router, dependencies=auth_dep)
    app.include_router(context.router, dependencies=auth_dep)
    app.include_router(graph.router, dependencies=auth_dep)
    app.include_router(jobs.router, dependencies=auth_dep)
    app.include_router(maintenance.router, dependencies=auth_dep)
    app.include_router(answer.router, dependencies=auth_dep)
    app.include_router(events.router, dependencies=auth_dep)
    app.include_router(metrics.router, dependencies=auth_dep)
    app.include_router(usage.router, dependencies=auth_dep)
    app.include_router(prometheus.router, dependencies=auth_dep)


__all__ = ["register_routes"]
