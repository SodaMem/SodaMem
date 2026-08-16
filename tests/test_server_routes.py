"""End-to-end tests for server/routes/* + server/app.py's factory wiring
(register_routes) via fastapi.testclient.TestClient.

Every ingest in this file goes through the REAL server code path
(StoreManager -> SodaMem.ingest -> IngestClient) with
`sodamem.llm.factory.create_provider` monkeypatched to return `None` —
`FactEventExtractorV2(provider=None)` is a real, already-guarded zero-network
code path (see tests/test_ingest_extractor.py::
test_no_provider_goes_straight_to_fallback_no_llm_call): every non-empty
message turns into exactly one `kind=fact,
predicate_canonical="message_unit_statement"` FactEvent, deterministically,
with zero network calls and zero flakiness. This is NOT a test-only stub —
it is the extractor's own documented degraded-but-deterministic fallback
path, reused here instead of hand-rolling scripted LLM JSON.
"""
from __future__ import annotations

import time as _time

import pytest

# The service layer lives behind the [server] extra (invariant I1: a base
# `pip install sodamem` pulls no ASGI stack). Without this guard the whole
# file explodes at COLLECTION time on a base install — which is exactly what
# CI did, since its tests job installed only [dev,chroma] (found 0727).
pytest.importorskip("fastapi", reason="server tests require the [server] extra")
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.jobs import reset_job_runner  # noqa: E402
from server.settings import get_settings, reset_settings_cache  # noqa: E402
from server.stores import get_store_manager, reset_store_manager  # noqa: E402
from sodamem.models import FactStatus  # noqa: E402

API_KEY = "test-secret-key"


def _configure_env(monkeypatch, tmp_path, *, auth_disabled: bool = True) -> None:
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SODAMEM_AUTH_DISABLED", "true" if auth_disabled else "false")
    monkeypatch.setenv("SODAMEM_API_KEY", API_KEY)
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "unused-test-key")
    reset_settings_cache()
    # job-runner threads USE stores — the runner must stop before the
    # stores it writes to are torn down (see tests/_service.py).
    reset_job_runner()
    reset_store_manager()


@pytest.fixture(autouse=True)
def _cleanup_singletons():
    yield
    reset_job_runner()
    reset_store_manager()
    reset_settings_cache()


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """Auth disabled, zero-network deterministic-fallback extractor."""
    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    app = create_app()
    return TestClient(app)


def _seed_one_memory(client: TestClient, user_id: str, content: str = "I own a red bicycle.") -> str:
    r = client.post("/v1/memories", json={
        "user_id": user_id, "async_mode": False,
        "messages": [{"role": "user", "content": content}],
    })
    assert r.status_code == 200, r.text
    listed = client.get("/v1/memories", params={"user_id": user_id})
    return listed.json()["memories"][0]["id"]


# ---------------------------------------------------------------------------
# health + auth
# ---------------------------------------------------------------------------

def test_health_unauthenticated_and_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["auth"] == "disabled"


def test_missing_api_key_401_when_auth_enabled(tmp_path, monkeypatch):
    _configure_env(monkeypatch, tmp_path, auth_disabled=False)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    app = create_app()
    c = TestClient(app)

    # /health stays open even with auth enabled.
    assert c.get("/health").status_code == 200
    assert c.get("/health").json()["auth"] == "enabled"

    r = c.get("/v1/memories", params={"user_id": "u1"})
    assert r.status_code == 401

    r = c.get("/v1/memories", params={"user_id": "u1"},
              headers={"X-API-Key": API_KEY})
    assert r.status_code == 200

    r = c.get("/v1/memories", params={"user_id": "u1"},
              headers={"Authorization": f"Bearer {API_KEY}"})
    assert r.status_code == 200

    r = c.get("/v1/memories", params={"user_id": "u1"},
              headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# full add -> list -> get -> delete -> 404 lifecycle
# ---------------------------------------------------------------------------

def test_full_memory_lifecycle(client):
    add = client.post("/v1/memories", json={
        "user_id": "alice", "async_mode": False,
        "session_time": "2024-01-01T10:00:00",
        "messages": [
            {"role": "user", "content": "I prefer tea over coffee."},
            {"role": "user", "content": "My flight departs Tuesday morning."},
        ],
    })
    assert add.status_code == 200, add.text
    body = add.json()
    assert body["facts_extracted"] == 2
    assert body["spans_written"] == 2
    assert body["turns_written"] == 2
    session_id = body["session_id"]
    assert session_id

    listed = client.get("/v1/memories", params={"user_id": "alice"})
    assert listed.status_code == 200
    lbody = listed.json()
    assert lbody["total"] == 2
    assert len(lbody["memories"]) == 2
    assert lbody["memories"][0]["session_id"] == session_id
    assert lbody["memories"][0]["user_id"] == "alice"
    mem_id = lbody["memories"][0]["id"]

    got = client.get(f"/v1/memories/{mem_id}", params={"user_id": "alice"})
    assert got.status_code == 200
    assert got.json()["id"] == mem_id
    assert got.json()["content"]

    deleted = client.delete(f"/v1/memories/{mem_id}", params={"user_id": "alice"})
    assert deleted.status_code == 200
    dbody = deleted.json()
    assert dbody["id"] == mem_id
    assert dbody["deleted"] is True
    # Default delete is a tombstone, so nothing cascaded and nothing was erased.
    assert dbody["purged"] is False
    assert dbody["already_deleted"] is False
    assert dbody["cascaded"] == {}

    missing = client.get(f"/v1/memories/{mem_id}", params={"user_id": "alice"})
    assert missing.status_code == 404

    listed2 = client.get("/v1/memories", params={"user_id": "alice"})
    assert listed2.json()["total"] == 1


def test_delete_is_idempotent_and_reports_already_deleted(client):
    mem_id = _seed_one_memory(client, "idempotent")

    first = client.delete(f"/v1/memories/{mem_id}", params={"user_id": "idempotent"})
    assert first.json()["already_deleted"] is False

    second = client.delete(f"/v1/memories/{mem_id}", params={"user_id": "idempotent"})
    assert second.status_code == 200
    assert second.json()["deleted"] is True
    assert second.json()["already_deleted"] is True


def test_archived_fact_row_survives_the_default_delete(client):
    """The whole point of tombstoning: the API stops serving it, but the
    evidence row is still on disk for provenance/audit."""
    mem_id = _seed_one_memory(client, "provenance")
    client.delete(f"/v1/memories/{mem_id}", params={"user_id": "provenance"})

    mem = get_store_manager().get("provenance")
    fact = mem.store.get_fact_event(mem_id)
    assert fact is not None
    assert fact.status == FactStatus.ARCHIVED


def test_purge_is_refused_unless_the_operator_enabled_it(client):
    mem_id = _seed_one_memory(client, "nopurge")

    r = client.delete(f"/v1/memories/{mem_id}",
                      params={"user_id": "nopurge", "purge": "true"})
    assert r.status_code == 403
    # Refused, not silently downgraded to an archive — the fact is untouched.
    mem = get_store_manager().get("nopurge")
    assert mem.store.get_fact_event(mem_id).status == FactStatus.ACTIVE


def test_purge_physically_erases_and_cascades_when_enabled(client, monkeypatch):
    mem_id = _seed_one_memory(client, "purger")
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_purge", True)

    r = client.delete(f"/v1/memories/{mem_id}",
                      params={"user_id": "purger", "purge": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["purged"] is True
    assert body["cascaded"]["fact_events"] == 1

    mem = get_store_manager().get("purger")
    assert mem.store.get_fact_event(mem_id) is None


def test_get_unknown_memory_404(client):
    r = client.get("/v1/memories/nonexistent", params={"user_id": "nobody"})
    assert r.status_code == 404


def test_delete_nonexistent_memory_is_a_clean_noop(client):
    r = client.delete("/v1/memories/nonexistent", params={"user_id": "nobody"})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is False
    assert body["already_deleted"] is False
    assert body["cascaded"] == {}


def test_list_memories_pagination(client):
    client.post("/v1/memories", json={
        "user_id": "paginated", "async_mode": False,
        "messages": [
            {"role": "user", "content": "First distinct statement about kayaking."},
            {"role": "user", "content": "Second distinct statement about baking bread."},
            {"role": "user", "content": "Third distinct statement about mountain biking."},
        ],
    })
    page1 = client.get("/v1/memories", params={"user_id": "paginated", "offset": 0, "limit": 2})
    assert page1.status_code == 200
    p1 = page1.json()
    assert p1["total"] == 3
    assert len(p1["memories"]) == 2

    page2 = client.get("/v1/memories", params={"user_id": "paginated", "offset": 2, "limit": 2})
    p2 = page2.json()
    assert p2["total"] == 3
    assert len(p2["memories"]) == 1


# ---------------------------------------------------------------------------
# cross-user access (串户) at the HTTP layer.
#
# server/stores.py's StoreManager opens one PHYSICAL SQLite file per
# user_id (data_root/<user_id>/memory.db) — a request scoped to
# `user_id=intruder` never even queries `owner`'s database file; it opens
# its own, separate (empty, for this fact) store. That is the strongest
# possible guard (no shared query surface to leak through at all), so the
# HTTP-level outcome is a plain not-found, not a distinguishable 4xx.
#
# The actual `Store.delete_fact_event` ownership check (TenancyError when a
# fact_id is found but belongs to a DIFFERENT user_id WITHIN THE SAME
# physical store) is exercised directly and thoroughly in
# tests/test_storage.py — that is the scenario that check exists for: the
# `fact_events` table is itself multi-tenant-shaped (a `user_id` column, not
# a separate table per user), which is exactly how this repo's benchmark
# stores are laid out (many user_ids in one store file) even though
# StoreManager additionally isolates by file for the HTTP server. Both
# layers matter; this test asserts the HTTP layer's (no-leak) behavior.
# ---------------------------------------------------------------------------

def test_delete_cross_user_never_leaks_and_owner_data_survives(client):
    mem_id = _seed_one_memory(client, "owner")

    r = client.delete(f"/v1/memories/{mem_id}", params={"user_id": "intruder"})
    assert r.status_code == 200
    assert r.json()["deleted"] is False  # intruder's own store never had this fact

    still_there = client.get("/v1/memories", params={"user_id": "owner"})
    assert still_there.json()["total"] == 1
    got = client.get(f"/v1/memories/{mem_id}", params={"user_id": "owner"})
    assert got.status_code == 200


def test_get_cross_user_is_a_plain_404_not_a_leak(client):
    mem_id = _seed_one_memory(client, "owner2")

    r = client.get(f"/v1/memories/{mem_id}", params={"user_id": "intruder2"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# agent_id/run_id -> 501, everywhere they're accepted as scope narrowing.
# ---------------------------------------------------------------------------

def test_scope_is_accepted_where_implemented(client):
    """R1.2 landed on the three routes the core can actually honor: ingest
    stamps the scope onto extracted facts, and retrieval narrows on it."""
    r = client.post("/v1/memories", json={
        "user_id": "u1", "agent_id": "a1",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert r.status_code in (200, 202), r.text

    r = client.post("/v1/search", json={"user_id": "u1", "query": "x", "run_id": "r1"})
    assert r.status_code == 200, r.text

    r = client.get("/v1/context", params={"user_id": "u1", "query": "x", "agent_id": "a1"})
    assert r.status_code == 200, r.text


def test_scope_still_501s_where_unimplemented_rather_than_being_ignored(client):
    """Partial support is only honest if the gap is explicit per route. These
    four have no scope plumbing yet, so they must refuse — a blanket
    "supported" that silently drops the keys on four of seven routes is the
    silent degradation this project refuses to ship."""
    for call in (
        lambda: client.get("/v1/memories", params={"user_id": "u1", "agent_id": "a1"}),
        lambda: client.get("/v1/memories/whatever", params={"user_id": "u1", "run_id": "r1"}),
        lambda: client.delete("/v1/memories/whatever", params={"user_id": "u1", "agent_id": "a1"}),
        lambda: client.get("/v1/events", params={"user_id": "u1", "agent_id": "a1"}),
    ):
        r = call()
        assert r.status_code == 501, r.text
        # The message must name where scope DOES work, or a caller has no
        # way to tell "not built yet" from "you used it wrong".
        assert "/v1/search" in r.json()["message"]


# ---------------------------------------------------------------------------
# infer=false -> raw storage (R2.6). This used to assert 501, and that was
# the right answer while the core genuinely had no raw-turn-only path: a
# silent infer=true would have stored facts the caller explicitly asked NOT
# to infer. The core has one now, so the honest 501 became a wrong one — but
# the invariant it protected is unchanged and still asserted below: infer=false
# must never quietly behave like infer=true.
# ---------------------------------------------------------------------------

def test_infer_false_never_silently_behaves_like_infer_true(client):
    r = client.post("/v1/memories", json={
        "user_id": "u_no_infer", "async_mode": False, "infer": False,
        "messages": [{"role": "user", "content": "I sail dinghies on weekends."}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["facts_extracted"] == 0
    assert client.get("/v1/memories", params={"user_id": "u_no_infer"}).json()["total"] == 0


# ---------------------------------------------------------------------------
# async_mode=true -> 202 + job that completes
# ---------------------------------------------------------------------------

def test_async_ingest_returns_202_and_job_completes(client):
    r = client.post("/v1/memories", json={
        "user_id": "asyncu", "async_mode": True,
        "messages": [{"role": "user", "content": "I adopted a cat named Whiskers."}],
    })
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    job_id = body["job_id"]
    assert body["session_id"]

    deadline = _time.time() + 10
    job_body = None
    while _time.time() < deadline:
        jr = client.get(f"/v1/jobs/{job_id}")
        assert jr.status_code == 200
        job_body = jr.json()
        if job_body["status"] in ("succeeded", "failed"):
            break
        _time.sleep(0.05)

    assert job_body is not None
    assert job_body["status"] == "succeeded", job_body
    assert job_body["result"]["facts_extracted"] == 1
    assert job_body["kind"] == "ingest"
    assert job_body["user_id"] == "asyncu"


def test_job_not_found_404_not_fabricated_pending(client):
    r = client.get("/v1/jobs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# search: degraded surfaces through, never swallowed
# ---------------------------------------------------------------------------

def test_search_smoke(client):
    _seed_one_memory(client, "searcher", "I love scuba diving in coral reefs.")
    r = client.post("/v1/search", json={"user_id": "searcher", "query": "scuba diving", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "scuba diving"
    assert isinstance(body["hits"], list)
    assert isinstance(body["degraded"], list)


def test_search_degraded_is_passed_through_verbatim(client, monkeypatch):
    from sodamem import SodaMem
    from sodamem.memory.retrieval.config import Degradation, DegradationCode
    from sodamem.memory.retrieval.search import SearchResult

    fake_result = SearchResult(
        evidence=[],
        degraded=[Degradation(DegradationCode.VECTOR_ROUTE_FAILED, "vector route boom", {"n": 1})],
        routes={},
    )
    monkeypatch.setattr(SodaMem, "search", lambda self, *a, **kw: fake_result)

    r = client.post("/v1/search", json={"user_id": "u1", "query": "anything"})
    assert r.status_code == 200
    body = r.json()
    assert body["hits"] == []
    assert body["degraded"] == [
        {"code": "vector_route_failed", "message": "vector route boom", "details": {"n": 1}},
    ]


# ---------------------------------------------------------------------------
# context: zero-LLM, even when a real (raising) provider is configured
# ---------------------------------------------------------------------------

def test_context_route_never_invokes_llm_provider(tmp_path, monkeypatch):
    _configure_env(monkeypatch, tmp_path)

    # Phase 1: seed data through the zero-network fallback extractor.
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    app1 = create_app()
    c1 = TestClient(app1)
    seeded = c1.post("/v1/memories", json={
        "user_id": "ctxuser", "async_mode": False,
        "messages": [{"role": "user", "content": "I love hiking in Yosemite."}],
    })
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["facts_extracted"] == 1

    # Phase 2: fresh app/store pointed at the SAME on-disk data_root, but now
    # the provider factory returns RaisingProvider — .complete()/.acomplete()
    # raise AssertionError if ever invoked (sodamem.llm.testing's canonical
    # "an LLM call here is a bug" probe). If GET /v1/context touched the LLM
    # anywhere in its call path, this request would blow up with a 500
    # wrapping that AssertionError instead of returning 200.
    reset_settings_cache()
    reset_job_runner()
    reset_store_manager()
    from sodamem.llm.testing import RaisingProvider
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: RaisingProvider())
    app2 = create_app()
    c2 = TestClient(app2)

    r = c2.get("/v1/context", params={"user_id": "ctxuser", "query": "hiking Yosemite"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["text"], str)
    assert isinstance(body["citations"], list)
    assert isinstance(body["evidence"], list)
    assert isinstance(body["degraded"], list)


# ---------------------------------------------------------------------------
# Event-loop discipline.
#
# Every store call in this service is blocking (SQLite, ChromaDB, ONNX
# embedding) and no handler awaits anything. FastAPI runs a `def` handler in
# its threadpool and an `async def` handler ON the event loop, so one
# `async def` here stalls the whole process for the length of a request —
# measured at 7.4s of /health unavailability during a single synchronous
# ingest, which is also long enough for the container HEALTHCHECK
# (--timeout=5s --retries=3) to declare a healthy server dead.
#
# This is a property of the whole router surface, not of any one endpoint, so
# it is asserted over the mounted route table rather than endpoint by
# endpoint — a newly added handler is covered the day it is added.
#
# Scoped to /v1/* on purpose. `/health` is deliberately `async def` and must
# stay that way: it touches no store and does no I/O, so running it on the
# loop is exactly what lets it answer while every threadpool worker is busy —
# which is the whole reason it exists. FastAPI's own /docs and /openapi.json
# are async for the same reason. The rule being enforced here is not "async is
# bad", it is "do not run blocking store I/O on the event loop".
# ---------------------------------------------------------------------------

def test_no_v1_route_handler_is_a_coroutine_function():
    # Walks OUR APIRouter objects, not app.routes. `app.include_router()`
    # does not put APIRoute leaves on `app.routes` in FastAPI 0.140 — it
    # inserts opaque `_IncludedRouter` wrappers with no public `.routes` —
    # so a scan of the app's route table silently finds zero v1 handlers and
    # passes no matter what. `router.routes` is public, stable, and is the
    # thing these modules actually define.
    # Every module under server/routes that exposes a `router`, discovered
    # rather than listed. The hand-written list this replaces named four
    # modules and missed `graph.py`, whose three handlers were `async def`
    # with no `await` in them — blocking store I/O on the loop, which is the
    # exact thing this test exists to forbid, sitting one import away from
    # the test that forbids it. A guard whose scope is maintained by hand
    # protects whatever someone last remembered to add.
    import importlib
    import inspect
    import pkgutil

    import server.routes as routes_pkg

    modules = []
    for info in pkgutil.iter_modules(routes_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"server.routes.{info.name}")
        if hasattr(module, "router"):
            modules.append(module)
    assert len(modules) >= 8, f"route discovery found only {len(modules)}"

    offenders = [
        f"{route.path} -> {route.endpoint.__name__}"
        for module in modules
        for route in module.router.routes
        if inspect.iscoroutinefunction(route.endpoint)
    ]
    assert offenders == [], (
        "These handlers are `async def` but their bodies are blocking, so they "
        "run store I/O directly on the event loop and stall every other "
        "request: " + ", ".join(offenders) + ". Declare them `def` (FastAPI "
        "will use its threadpool), or keep `async def` only after moving the "
        "blocking work behind starlette.concurrency.run_in_threadpool."
    )


def test_health_is_still_reachable_while_v1_work_is_in_flight(client):
    """Companion to the rule above, stated as behavior rather than shape:
    /health must not depend on the store layer being idle."""
    _seed_one_memory(client, "healthprobe")
    assert client.get("/health").status_code == 200


# POST /v1/answer — the differentiating route (PRD R2.1). Covered here with a
# scripted provider so the suite stays zero-network and zero-cost.
# ---------------------------------------------------------------------------

# `question_classification` is a hard precondition for finalization
# (loop.py:325-327). Omitting it makes every `final` bounce with
# "question_classification is required before finalization", so the loop runs
# to max_steps and the scripted provider is exhausted — the symptom that
# actually showed up when this suite was first written.
_PLANNER_FINAL = (
    '{"state_update": {"question_classification": '
    '{"type": "ordinary", "comparison_requires_count_or_sum": false}}, '
    '"decision": {"action": "final", '
    '"selected_evidence_ids": [], "sufficiency": "insufficient", '
    '"missing_information": "nothing stored yet"}}'
)


_PLANNER_TOOLCALL = (
    '{"state_update": {}, "decision": {"action": "tool_calls", '
    '"calls": [{"tool": "browser_search", "args": {"query": "hi?"}}]}}'
)


def _scripted_answer_provider():
    """Step 0 MUST propose tool_calls, not final: the TerminalRule refuses a
    final before any search has run, so a step-0 `final` is rejected and the
    loop just asks again (which exhausts the script). Step 0's calls are then
    replaced by the forced first search; step 1 may finalize."""
    from sodamem.llm.testing import ScriptedProvider
    return ScriptedProvider([
        _PLANNER_TOOLCALL,       # step 0 — overridden by the forced search
        _PLANNER_FINAL,          # step 1 — search seen, final accepted
        '{"organizer": null}',   # reader context plan
        "I don't have that in memory yet.",
    ])


def test_answer_requires_a_configured_llm_provider(client):
    """No credentials must produce a loud 503, never an empty answer string —
    /v1/search and /v1/context keep working without an LLM, this route cannot."""
    r = client.post("/v1/answer", json={"user_id": "alice", "question": "hi?"})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "service_unavailable"
    assert "SODAMEM_LLM_API_KEY" in body["message"]


def test_answer_rejects_unsupported_scope_keys(client):
    r = client.post("/v1/answer",
                    json={"user_id": "alice", "question": "hi?", "agent_id": "a1"})
    assert r.status_code == 501
    assert r.json()["code"] == "not_implemented"


def test_answer_returns_answer_citations_and_termination(client, monkeypatch):
    """The response must carry `termination` / `planner_steps` / `insufficient`
    — the one-shot facade discards those, and without them a caller cannot
    tell a confident answer from a give-up."""
    from server.routes import answer as answer_route
    monkeypatch.setattr(answer_route, "build_provider",
                        lambda *a, **k: _scripted_answer_provider())
    r = client.post("/v1/answer", json={"user_id": "alice", "question": "hi?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["termination"] == "planner_final"
    assert body["planner_steps"] >= 1
    assert body["insufficient"] is True
    assert body["missing_information"] == "nothing stored yet"
    assert isinstance(body["citations"], list)
    # An insufficient finalization SHORT-CIRCUITS the reader: the answer is
    # composed deterministically from missing_information instead of spending
    # an LLM call to say "I don't know". The scripted answer text is therefore
    # never reached on this path — assert the real behavior, not the one the
    # script implies.
    assert "Not enough information" in body["answer"]
    assert "nothing stored yet" in body["answer"]


def test_answer_async_mode_returns_a_job(client, monkeypatch):
    from server.routes import answer as answer_route
    monkeypatch.setattr(answer_route, "build_provider",
                        lambda *a, **k: _scripted_answer_provider())
    r = client.post("/v1/answer",
                    json={"user_id": "alice", "question": "hi?", "async_mode": True})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            break
        _time.sleep(0.05)
    assert job["status"] == "succeeded", job
    assert "Not enough information" in job["result"]["answer"]
    assert job["result"]["termination"] == "planner_final"


# ---------------------------------------------------------------------------
# GET /v1/events — change history (PRD R1.12)
# ---------------------------------------------------------------------------

def test_events_records_a_delete_so_a_vanished_memory_is_accountable(client):
    """A deleted fact leaves no trace in the memory table by construction —
    which is exactly why "why did the agent forget X?" is unanswerable without
    this. The delete must show up as an event carrying the memory id."""
    from sodamem.models import FactEvent, FactKind, SourceType
    from server.stores import get_store_manager

    store = get_store_manager().get("eventu").store
    fact = FactEvent(
        user_id="eventu", kind=FactKind.FACT, source_span_ids=["span_x"],
        predicate_canonical="likes_pizza", predicate_raw="likes pizza",
        source_type=SourceType.EXPLICIT_TEXT,
    )
    store.upsert_fact_event(fact)

    r = client.delete(f"/v1/memories/{fact.fact_id}", params={"user_id": "eventu"})
    assert r.status_code == 200 and r.json()["deleted"] is True

    body = client.get("/v1/events", params={"user_id": "eventu"}).json()
    deletes = [e for e in body["events"] if e["type"] == "memory_delete"]
    assert len(deletes) == 1, body
    assert deletes[0]["memory_id"] == fact.fact_id
    # The DEFAULT delete is an archive (purge needs ?purge=true plus the
    # deploy-time opt-in), so the event carries the archive marker. Asserting
    # the purge shape here is how this gap hid once already: the trace lived
    # only on the purge path, and every ordinary delete went unrecorded.
    assert deletes[0]["details"]["archived"] is True


def test_events_records_a_purge_with_its_cascade(client, monkeypatch):
    """The purge path carries what the archive path cannot: how many dependent
    rows went with the fact. Both must be traced — a physical delete that
    leaves no event is the same accountability hole, one door over."""
    from sodamem.models import FactEvent, FactKind, SourceType
    from server.settings import get_settings, reset_settings_cache
    from server.stores import get_store_manager

    monkeypatch.setenv("SODAMEM_ALLOW_PURGE", "true")
    reset_settings_cache()
    assert get_settings().allow_purge

    store = get_store_manager().get("purgeu").store
    fact = FactEvent(
        user_id="purgeu", kind=FactKind.FACT, source_span_ids=["span_p"],
        predicate_canonical="likes_tea", predicate_raw="likes tea",
        source_type=SourceType.EXPLICIT_TEXT,
    )
    store.upsert_fact_event(fact)

    r = client.delete(f"/v1/memories/{fact.fact_id}",
                      params={"user_id": "purgeu", "purge": "true"})
    assert r.status_code == 200 and r.json()["purged"] is True

    body = client.get("/v1/events", params={"user_id": "purgeu"}).json()
    deletes = [e for e in body["events"] if e["type"] == "memory_delete"]
    assert len(deletes) == 1, body
    assert "cascaded" in deletes[0]["details"]


def test_events_type_filter_and_unknown_type_returns_empty_not_everything(client):
    body = client.get("/v1/events",
                      params={"user_id": "eventu", "type": "memory_delete"}).json()
    assert all(e["type"] == "memory_delete" for e in body["events"])
    # An unrecognized filter must not fall through to "return all" — a caller
    # asking for a type we do not have should see nothing, loudly empty.
    body = client.get("/v1/events",
                      params={"user_id": "eventu", "type": "not_a_type"}).json()
    assert body["events"] == [] and body["total"] == 0


def test_events_rejects_unsupported_scope(client):
    r = client.get("/v1/events", params={"user_id": "alice", "agent_id": "a1"})
    assert r.status_code == 501


# ---------------------------------------------------------------------------
# PATCH /v1/memories/{id} — PRD R1.5. ADD-only, so an "update" is a new
# version plus a SUPERSEDES edge, never an in-place rewrite. That is the whole
# difference from DELETE: after PATCH the old text is still retrievable and
# still cites its original span; it is merely closed.
# ---------------------------------------------------------------------------

def _first_memory_id(client, user_id: str) -> str:
    listing = client.get("/v1/memories", params={"user_id": user_id, "limit": 50})
    assert listing.status_code == 200
    items = listing.json()["memories"]
    assert items, "fixture ingest produced no memories"
    return items[0]["id"]


def _seed(client, user_id: str, content: str) -> str:
    r = client.post("/v1/memories", json={
        "user_id": user_id, "async_mode": False,
        "messages": [{"role": "user", "content": content}],
    })
    assert r.status_code == 200, r.text
    return _first_memory_id(client, user_id)


def test_patch_memory_creates_a_new_version_and_closes_the_old_one(client):
    old_id = _seed(client, "patcher", "I work at Initech as a programmer.")
    r = client.patch(f"/v1/memories/{old_id}", json={
        "user_id": "patcher",
        "content": "Actually I now work at Initrode as a manager.",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["superseded_id"] == old_id
    assert body["memory"]["id"] != old_id


def test_patch_keeps_the_old_version_readable(client):
    """The point of ADD-only: 'forget that' must not destroy provenance."""
    old_id = _seed(client, "patch_keep", "My favourite tea is oolong.")
    client.patch(f"/v1/memories/{old_id}", json={
        "user_id": "patch_keep", "content": "Actually my favourite tea is genmaicha now.",
    })
    still_there = client.get(f"/v1/memories/{old_id}", params={"user_id": "patch_keep"})
    assert still_there.status_code == 200
    assert still_there.json()["status"] == "superseded"


def test_patch_rejects_a_cross_user_memory_id(client):
    """fact_id is an opaque uuid, not a secret — guessing one must not let a
    caller rewrite another tenant's memory."""
    old_id = _seed(client, "patch_owner", "I drive a blue hatchback.")
    r = client.patch(f"/v1/memories/{old_id}", json={
        "user_id": "patch_attacker", "content": "Actually I drive a red truck.",
    })
    assert r.status_code in (403, 404), r.text
    intact = client.get(f"/v1/memories/{old_id}", params={"user_id": "patch_owner"})
    assert intact.json()["status"] == "active"


def test_patch_unknown_memory_id_is_404_not_a_silent_insert(client):
    r = client.patch("/v1/memories/does-not-exist", json={
        "user_id": "patch_missing", "content": "Anything.",
    })
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/memories/batch — bulk import (PRD table stakes: 写路径批量).
#
# The unit is a SESSION, not a message: POST /v1/memories already takes many
# messages, but they all collapse into one conversation with one timestamp.
# Importing a year of history means many conversations at many times, and
# doing that one HTTP call at a time is the thing every competitor's migration
# guide tells you not to do.
#
# Partial failure is the whole design question. All-or-nothing is wrong here —
# the store is ADD-only per user and a single malformed session in a 5,000
# session import must not discard the 4,999 good ones. So: every session is
# attempted, each gets its own status, and the response says plainly which
# ones failed. Silence is the one unacceptable outcome.
# ---------------------------------------------------------------------------

def test_batch_ingests_every_session(client):
    r = client.post("/v1/memories/batch", json={
        "user_id": "bulk", "async_mode": False,
        "sessions": [
            {"session_time": "2023-01-05T10:00:00Z",
             "messages": [{"role": "user", "content": "I adopted a beagle named Cosmo."}]},
            {"session_time": "2023-06-11T10:00:00Z",
             "messages": [{"role": "user", "content": "I moved to Lisbon for work."}]},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["succeeded"] == 2 and body["failed"] == 0
    assert len(body["results"]) == 2
    assert all(item["ok"] for item in body["results"])
    listing = client.get("/v1/memories", params={"user_id": "bulk", "limit": 50})
    assert listing.json()["total"] >= 2


def test_batch_of_one_matches_a_plain_add(client):
    """A batch of one must not be a special case — same extraction, same
    store, just a different envelope."""
    client.post("/v1/memories", json={
        "user_id": "single", "async_mode": False,
        "messages": [{"role": "user", "content": "I play the cello on Tuesdays."}],
    })
    client.post("/v1/memories/batch", json={
        "user_id": "batched", "async_mode": False,
        "sessions": [{"messages": [{"role": "user", "content": "I play the cello on Tuesdays."}]}],
    })
    a = client.get("/v1/memories", params={"user_id": "single", "limit": 50}).json()
    b = client.get("/v1/memories", params={"user_id": "batched", "limit": 50}).json()
    assert a["total"] == b["total"]


def test_batch_reports_a_failing_session_without_dropping_the_others(client):
    """One bad session in a big import must be named, not silently skipped —
    and must not take the good ones down with it."""
    r = client.post("/v1/memories/batch", json={
        "user_id": "bulk_partial", "async_mode": False,
        "sessions": [
            {"messages": [{"role": "user", "content": "First good statement about sailing."}]},
            {"session_time": "not-a-timestamp",
             "messages": [{"role": "user", "content": "This one has a broken time."}]},
            {"messages": [{"role": "user", "content": "Third good statement about pottery."}]},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["succeeded"] == 2 and body["failed"] == 1
    bad = [item for item in body["results"] if not item["ok"]]
    assert len(bad) == 1
    assert bad[0]["index"] == 1
    assert bad[0]["error"]
    assert client.get("/v1/memories", params={"user_id": "bulk_partial"}).json()["total"] >= 2


def test_batch_async_returns_a_job(client):
    r = client.post("/v1/memories/batch", json={
        "user_id": "bulk_async", "async_mode": True,
        "sessions": [{"messages": [{"role": "user", "content": "Async batch statement."}]}],
    })
    assert r.status_code == 202, r.text
    assert r.json()["job_id"]


def test_batch_rejects_an_oversized_import(client):
    """An unbounded batch is a memory-exhaustion lever on a shared service."""
    r = client.post("/v1/memories/batch", json={
        "user_id": "bulk_huge", "async_mode": False,
        "sessions": [
            {"messages": [{"role": "user", "content": f"Statement {i}."}]}
            for i in range(501)
        ],
    })
    assert r.status_code == 422


def test_batch_rejects_an_empty_import(client):
    r = client.post("/v1/memories/batch", json={
        "user_id": "bulk_empty", "async_mode": False, "sessions": [],
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/metrics — PRD G5. The instrument, not a README claim: the PRD's
# published percentiles were measured against an earlier codebase, so
# reprinting them here would be an unverifiable self-report. This lets any
# deployment produce its own.
# ---------------------------------------------------------------------------

def test_metrics_endpoint_reflects_served_requests(client):
    client.get("/v1/memories", params={"user_id": "metrics_probe"})
    body = client.get("/v1/metrics").json()
    assert body["routes"], "no route latencies recorded"
    key = next(k for k in body["routes"] if k.endswith("/v1/memories"))
    entry = body["routes"][key]
    assert entry["count"] >= 1
    assert entry["p50"] > 0


def test_metrics_endpoint_is_behind_auth(tmp_path, monkeypatch):
    """Route-level traffic shape is operational data, not public — it sits
    behind the same key as everything except /health."""
    _configure_env(monkeypatch, tmp_path, auth_disabled=False)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    c = TestClient(create_app())
    assert c.get("/v1/metrics").status_code == 401


def test_infer_false_stores_raw_turns_over_http(client):
    """R2.6 — this route used to answer 501 because the core had no
    raw-turn-only mode. It does now (IngestClient writes turns and spans
    before extraction), so the honest 501 became a wrong one."""
    r = client.post("/v1/memories", json={
        "user_id": "raw_http", "async_mode": False, "infer": False,
        "messages": [{"role": "user", "content": "Store this verbatim, do not extract."}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts_extracted"] == 0
    assert body["turns_written"] == 1
    # No FactEvent means nothing in the memories list — that is the contract,
    # not a bug: the caller asked for storage without inference.
    listing = client.get("/v1/memories", params={"user_id": "raw_http"})
    assert listing.json()["total"] == 0


def test_infer_true_is_still_the_default_over_http(client):
    r = client.post("/v1/memories", json={
        "user_id": "inferred_http", "async_mode": False,
        "messages": [{"role": "user", "content": "I collect vintage postcards."}],
    })
    assert r.status_code == 200
    assert client.get("/v1/memories", params={"user_id": "inferred_http"}).json()["total"] >= 1


# ---------------------------------------------------------------------------
# GET/POST /v1/context — the README's own example used to 405
# ---------------------------------------------------------------------------

def test_context_accepts_both_get_and_post(client):
    """`curl -d '{...}' .../v1/context` — the only HTTP example in the README,
    in all eight languages — sends a POST and got back 405 until 0806, because
    this route was GET-only while /v1/search next door took a JSON body."""
    # `async` is the default on this route, so an un-awaited ingest lets the
    # background job land BETWEEN the two reads below and the comparison fails
    # on a store that changed under it — a flake this test shipped with, and a
    # false accusation against the handler it is supposed to be checking.
    job = client.post("/v1/memories", json={
        "user_id": "ctx", "messages": [{"role": "user", "content": "I like Oahu"}],
        "async_mode": False,
    })
    assert job.status_code == 200, job.text
    body = {"user_id": "ctx", "query": "where do I like", "token_budget": 1000}

    posted = client.post("/v1/context", json=body)
    assert posted.status_code == 200, posted.text

    got = client.get("/v1/context", params=body)
    assert got.status_code == 200, got.text

    # One handler, so the two cannot answer the same question differently.
    assert posted.json() == got.json()


def test_context_post_rejects_the_same_bad_input_get_does(client):
    """Same model on both verbs — a POST must not become a way around the
    validation the GET applies."""
    assert client.post("/v1/context", json={"user_id": "u1", "query": ""}).status_code == 422
    assert client.get("/v1/context", params={"user_id": "u1", "query": ""}).status_code == 422


# ---------------------------------------------------------------------------
# POST /v1/maintenance/dream — the D36 primitive had no caller in a container
# ---------------------------------------------------------------------------

def test_dream_runs_synchronously_and_reports_what_it_did(client):
    client.post("/v1/memories", json={
        "user_id": "dreamer", "messages": [{"role": "user", "content": "I have a dog"}],
    })
    r = client.post("/v1/maintenance/dream", json={"user_id": "dreamer"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "dreamer"
    assert body["status"] == "ok"
    # Counters are present and numeric whether or not anything was stale —
    # "nothing to do" must be reportable, not indistinguishable from an error.
    assert isinstance(body["entities_processed"], int)
    assert isinstance(body["remaining_stale"], int)


def test_dream_async_returns_a_pollable_job(client):
    r = client.post("/v1/maintenance/dream", json={"user_id": "dreamer", "async": True})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "pending"

    for _ in range(200):
        job = client.get(f"/v1/jobs/{job_id}")
        assert job.status_code == 200, job.text
        if job.json()["status"] in ("succeeded", "failed"):
            break
        _time.sleep(0.02)
    assert job.json()["status"] == "succeeded", job.text


def test_dream_does_not_leak_its_store_borrow(client):
    """Both branches hand the borrow back — the sync one in a finally, the
    async one when the job ends. A leaked borrow is invisible until something
    tries to close the store and finds it pinned forever."""
    stores = get_store_manager()
    for _ in range(3):
        assert client.post("/v1/maintenance/dream", json={"user_id": "leaky"}).status_code == 200
    assert stores._inflight.get("leaky", 0) == 0


def test_dream_refuses_scope_keys_rather_than_ignoring_them(client):
    """Dreaming rebuilds profiles for the whole user; an agent_id here would
    be silently dropped, which is the failure mode this project refuses."""
    r = client.post("/v1/maintenance/dream", json={"user_id": "u1", "agent_id": "a1"})
    assert r.status_code == 501, r.text
# ---------------------------------------------------------------------------
# Every error, one envelope — including the two nobody raises by hand
# ---------------------------------------------------------------------------

def test_router_404_and_405_use_the_same_envelope_as_everything_else(client):
    """`server/app.py` says it normalizes every error onto ErrorBody. Until
    0806 that was false for exactly two responses, and they were the two a
    client is most likely to hit first.

    The handler was registered for `fastapi.HTTPException`, which SUBCLASSES
    `starlette.exceptions.HTTPException`. A handler on the subclass catches
    only what route code raises by hand; the router's own "no route matched"
    (404) and "wrong method" (405) are raised as the PARENT and went straight
    to Starlette's default `{"detail": ...}`. So the one guarantee the API
    makes about error shape was broken by the two errors that need no code to
    produce."""
    for response in (client.get("/v1/definitely-not-a-route"),
                     client.delete("/v1/context")):
        assert response.status_code in (404, 405), response.text
        body = response.json()
        assert set(body) == {"code", "message", "details"}, body
        assert body["code"] in ("not_found", "method_not_allowed"), body


def test_every_error_status_maps_to_a_named_code(client):
    """`http_error` is the fallback, not an outcome any reachable status
    should produce — a client switching on `code` gets no information from
    it."""
    seen = {
        client.get("/v1/definitely-not-a-route").json()["code"],
        client.delete("/v1/context").json()["code"],
        client.post("/v1/search", json={}).json()["code"],
        client.get("/v1/events", params={"user_id": "u", "agent_id": "a"}).json()["code"],
    }
    assert "http_error" not in seen, seen
