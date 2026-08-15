"""FastAPI application factory.

Layering (CI invariant I3): `server` imports `sodamem`, never the reverse. The
core library stays web-framework-free so `pip install sodamem` pulls no ASGI
stack (I1) — FastAPI lives behind the `[server]` extra.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sodamem import __version__
from sodamem.errors import SodaMemError
from sodamem.versioning import STORE_SCHEMA_VERSION
from server.auth import current_caller
from server.control import acquire_data_root_lock, get_control_plane
from server.metrics import get_latency_registry, get_request_counter
from server.models import ErrorBody, Health
from server.settings import Settings, get_settings
from server.stores import InvalidScopeError

logger = logging.getLogger(__name__)

#: Routes whose SUCCESSFUL calls are kept out of the persisted request log.
#: `/health` is polled by Docker's HEALTHCHECK every 30s — 2,880 rows a day of
#: "still alive", which would evict a 10,000-row rolling window in under four
#: days and leave an ops view that can only see its own heartbeat. A FAILING
#: probe is real signal and is still recorded; only the boring 2xx is dropped.
#: The in-memory latency ring still samples every call, health included.
_LOG_EXEMPT_ON_SUCCESS = frozenset({"/health", "/favicon.ico"})

#: Prefixes exempt for the same reason, plus a sharper one. Observed in the
#: browser, 0729: opening the console logged
#: `GET /console/assets/geist-latin-wght-normal-BgDaEnEv.woff2` — a
#: CONTENT-HASHED filename, in the column this table promises holds route
#: templates and never raw paths. Every frontend rebuild mints new hashes, so
#: the one rule that keeps this table from becoming an index of arbitrary
#: strings was being broken by our own static mount. Serving a page is also
#: not API traffic: five asset rows per page load push out the calls an
#: operator actually came to look at.
_LOG_EXEMPT_PREFIXES = ("/console",)


def _worth_logging(route: str, status_code: int) -> bool:
    if status_code >= 400:
        return True
    if route in _LOG_EXEMPT_ON_SUCCESS:
        return False
    return not route.startswith(_LOG_EXEMPT_PREFIXES)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_auth_configured()

    # ADR 0001 §2: single writer is a CORRECTNESS constraint (per-user SQLite,
    # no WAL). Enforced here rather than trusted to uvicorn's default worker
    # count — a second process fails loudly at startup instead of interleaving
    # writes into a store that cannot survive it.
    acquire_data_root_lock(settings.data_root)

    control = get_control_plane()
    orphaned = control.reconcile_orphaned_jobs()
    if orphaned:
        # Not silent: an operator whose deploy killed 12 in-flight ingests
        # should see that in the log, and the clients polling them now get a
        # readable `failed` instead of an eternal `running`.
        logger.warning(
            "closed %d job(s) orphaned by a previous shutdown (ADR 0001 §3)",
            orphaned,
        )

    app = FastAPI(
        title="SodaMem",
        version=__version__,
        description="Evidence-grounded temporal memory for AI agents.",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # --- request id + timing (PRD R1.13: feeds the latency budget) ---------
    @app.middleware("http")
    async def _timing(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
        # Keyed by the ROUTE TEMPLATE, not the raw path: bucketing by
        # `/v1/memories/{id}` keeps one entry per endpoint, where raw paths
        # would mint a fresh bucket per memory id and turn the registry into
        # an unbounded map of one-sample rings.
        route = request.scope.get("route")
        template = getattr(route, "path", None) or request.url.path
        get_latency_registry().record(f"{request.method} {template}", elapsed_ms)
        # Monotonic tally for the Prometheus counter — the latency ring
        # evicts samples, so it cannot serve as one.
        get_request_counter().record(request.method, template, response.status_code)
        # Persisted alongside the in-memory ring, not instead of it: the ring
        # answers "how fast, right now" cheaply and the table answers "what
        # happened while I was asleep" across restarts. Same route TEMPLATE in
        # both, so neither becomes a high-cardinality index of user ids.
        if _worth_logging(template, response.status_code):
            control.record_request(
                request_id=request_id,
                method=request.method,
                route=template,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
                key_name=current_caller(request),
            )
        logger.info(
            "%s %s -> %d in %.1fms (rid=%s)",
            request.method, request.url.path, response.status_code,
            elapsed_ms, request_id,
        )
        return response

    # --- error envelope ----------------------------------------------------
    @app.exception_handler(InvalidScopeError)
    async def _invalid_scope(_: Request, exc: InvalidScopeError):
        return JSONResponse(
            status_code=400,
            content=ErrorBody(code="invalid_scope", message=str(exc)).model_dump(),
        )

    @app.exception_handler(SodaMemError)
    async def _sodamem_error(_: Request, exc: SodaMemError):
        # The core's typed errors already carry a stable code; map them through
        # rather than flattening everything to a 500.
        code = getattr(getattr(exc, "code", None), "value", None) or "internal_error"
        status = 409 if "VERSION" in str(code).upper() else 400
        return JSONResponse(
            status_code=status,
            content=ErrorBody(
                code=str(code), message=str(exc),
                details=dict(getattr(exc, "details", {}) or {}),
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException):
        # FastAPI's native shape is {"detail": ...}, which would make auth
        # failures the ONE response a client must special-case. Normalize every
        # error onto ErrorBody so `code` is always machine-readable (caught by
        # the TypeScript SDK's contract review, 0725).
        code = {
            400: "bad_request", 401: "unauthorized", 403: "forbidden",
            404: "not_found", 409: "conflict", 422: "unprocessable",
            501: "not_implemented", 503: "service_unavailable",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorBody(code=code, message=str(exc.detail)).model_dump(),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=ErrorBody(
                code="validation_error",
                message="request failed schema validation",
                details={"errors": exc.errors()},
            ).model_dump(mode="json"),
        )

    # --- routes ------------------------------------------------------------
    @app.get("/health", response_model=Health, tags=["ops"])
    async def health() -> Health:
        """Unauthenticated liveness probe. Touches no store — a hung ingest
        must never make the container look dead."""
        return Health(
            version=__version__,
            schema_version=STORE_SCHEMA_VERSION,
            auth="disabled" if settings.auth_disabled else "enabled",
        )

    from server.routes import register_routes
    register_routes(app)

    # Optional static console at /console. Absent build artifact = INFO log,
    # never a startup failure: the API must serve headlessly (containers,
    # CI, embedded use) without anyone having run `npm run build`.
    from server.console_mount import mount_console
    mount_console(app)

    return app


app = None  # populated by `uvicorn server.app:build` / the entrypoint below


def build() -> FastAPI:
    """ASGI entrypoint: `uvicorn server.app:build --factory`."""
    return create_app()
