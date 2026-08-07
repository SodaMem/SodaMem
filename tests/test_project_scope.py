"""`project_id` — the repo dimension coding-agent integrations need (R1.2b).

Same harness as tests/test_server_routes.py: real ingest through the real
route, with `create_provider` returning None so the extractor takes its own
documented deterministic fallback (one fact per non-empty turn, zero network).

What is being pinned down here is the SEMANTIC, not the plumbing. `project_id`
narrows, it does not partition:

  * a fact stamped for project A must not surface when narrowing to project B
  * a fact stamped for NO project must surface under every narrowing
  * dropping the key must bring everything back

That last two are what make cross-project recall possible, and they are the
reason this is not implemented as one store per project. Get them backwards
and a user who opens a second repo appears to have lost their memory.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="server tests require the [server] extra")
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.jobs import reset_job_runner  # noqa: E402
from server.settings import reset_settings_cache  # noqa: E402
from server.stores import reset_store_manager  # noqa: E402


@pytest.fixture(autouse=True)
def _cleanup_singletons():
    yield
    # job-runner threads USE stores — the runner must stop before the
    # stores it writes to are torn down (see tests/_service.py).
    reset_job_runner()
    reset_store_manager()
    reset_settings_cache()


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SODAMEM_AUTH_DISABLED", "true")
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "unused-test-key")
    reset_settings_cache()
    reset_job_runner()
    reset_store_manager()
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    return TestClient(create_app())


def _add(client: TestClient, content: str, project_id: str | None = None) -> None:
    body = {
        "user_id": "alice", "async_mode": False,
        "messages": [{"role": "user", "content": content}],
    }
    if project_id is not None:
        body["project_id"] = project_id
    r = client.post("/v1/memories", json=body)
    assert r.status_code == 200, r.text


def _search(client: TestClient, query: str, project_id: str | None = None) -> list[str]:
    body = {"user_id": "alice", "query": query, "top_k": 50}
    if project_id is not None:
        body["project_id"] = project_id
    r = client.post("/v1/search", json=body)
    assert r.status_code == 200, r.text
    return [hit["content"] for hit in r.json()["hits"]]


PROJECT_A = "the api server uses postgres for everything"
PROJECT_B = "the mobile client caches responses in sqlite"
GLOBAL = "always run the formatter before committing"


def test_stamped_facts_are_narrowed_to_their_own_project(client):
    _add(client, PROJECT_A, project_id="repo-a")
    _add(client, PROJECT_B, project_id="repo-b")

    a_hits = " ".join(_search(client, "postgres sqlite formatter", project_id="repo-a"))
    assert "postgres" in a_hits
    assert "sqlite" not in a_hits


def test_unstamped_facts_survive_every_narrowing(client):
    """The non-negotiable one.

    Strict AND (mem0's semantics) would hide every memory written before a
    project was ever passed. From the outside that is indistinguishable from
    data loss, and it would fire the first time an existing user installed the
    Claude Code integration.
    """
    _add(client, GLOBAL)  # no project_id at all
    _add(client, PROJECT_A, project_id="repo-a")

    hits = " ".join(_search(client, "formatter postgres", project_id="repo-b"))
    assert "formatter" in hits, "an unstamped memory vanished under narrowing"


def test_dropping_the_key_restores_cross_project_recall(client):
    """"How did I fix this in the other repo?" has to remain answerable."""
    _add(client, PROJECT_A, project_id="repo-a")
    _add(client, PROJECT_B, project_id="repo-b")

    everything = " ".join(_search(client, "postgres sqlite"))
    assert "postgres" in everything
    assert "sqlite" in everything


def test_context_narrows_on_the_same_key(client):
    """get_context is search + rendering; if the two disagreed on scope, the
    prompt-ready block would leak the other repo's facts."""
    _add(client, PROJECT_A, project_id="repo-a")
    _add(client, PROJECT_B, project_id="repo-b")

    r = client.get("/v1/context", params={
        "user_id": "alice", "query": "postgres sqlite",
        "project_id": "repo-a", "token_budget": 2000,
    })
    assert r.status_code == 200, r.text
    assert "sqlite" not in r.json()["text"]


def test_project_id_is_readable_back_off_the_memory(client):
    """A key you can filter on but never read back is only half a feature."""
    _add(client, PROJECT_A, project_id="repo-a")

    r = client.get("/v1/memories", params={"user_id": "alice"})
    assert r.status_code == 200, r.text
    assert r.json()["memories"][0]["project_id"] == "repo-a"


def test_raw_turn_recall_is_scoped_too(client):
    """The half-feature this nearly shipped as.

    Scope used to live only in each extracted fact's metadata, so raw-turn
    evidence — which has no metadata — passed every scoped query. For a coding
    agent that is the whole ballgame: raw turns are most of what a session
    writes, so "narrow to this repo" would still have handed back the other
    repo's conversation text. Fixed by recording scope once per SESSION
    (session_scope), which both card types can be resolved through.
    """
    secret = "the staging database password rotates every friday"
    _add(client, secret, project_id="repo-b")

    r = client.post("/v1/search", json={
        "user_id": "alice", "query": "staging database password",
        "top_k": 50, "project_id": "repo-a",
    })
    assert r.status_code == 200, r.text
    blob = " ".join(
        (hit.get("content") or "") + " " + str(hit.get("metadata", {}))
        for hit in r.json()["hits"]
    )
    assert "rotates every friday" not in blob


def test_stores_written_before_session_scope_still_open(tmp_path):
    """Backward compatibility, stated as a test rather than as a hope.

    `session_scope` is a purely additive table, and `open_store` now applies
    the (fully idempotent) DDL to existing stores as well as new ones. A store
    created without the table must open, gain it, and keep every fact it had.
    """
    from sodamem import SodaMem
    from sodamem.memory.ingest.extractor import FactEventExtractorV2

    data_dir = tmp_path / "alice"
    data_dir.mkdir(parents=True)
    mem = SodaMem.open(data_dir, extractor=FactEventExtractorV2(provider=None))
    mem.ingest([{"role": "user", "content": "I deploy on fridays."}],
               user_id="alice", session_id="s1", session_time=1720000000.0)
    # Simulate a store that predates the table.
    mem.store._conn.execute("DROP TABLE session_scope")
    mem.store._conn.commit()
    mem.close()

    reopened = SodaMem.open(data_dir, extractor=FactEventExtractorV2(provider=None))
    try:
        assert reopened.store.get_session_scopes("alice") == {}
        assert len(reopened.store.get_all_fact_events("alice", active_only=True)) == 1
        # And a scoped query against it still works — everything is unstamped,
        # so everything matches, which is the pre-existing behavior.
        assert reopened.search("fridays", user_id="alice",
                               project_id="repo-a").evidence
    finally:
        reopened.close()


def test_routes_without_scope_support_still_refuse_it(client):
    """501, not a silently-ignored parameter — the rule the other scope keys
    already follow."""
    r = client.get("/v1/events", params={"user_id": "alice", "project_id": "repo-a"})
    assert r.status_code == 501
    assert "project_id" in r.json()["message"]
