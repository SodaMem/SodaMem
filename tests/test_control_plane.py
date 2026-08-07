"""Control plane (ADR 0001): schema, bounded tables, named keys, job
durability, single-writer enforcement, and the /v1/admin/* surface.

The properties under test here are the ones that were argued for in the ADR
and then not implemented for two weeks, so each test names the failure it
prevents rather than the method it calls.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="server tests require the [server] extra")
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.control import (  # noqa: E402
    CONTROL_SCHEMA_VERSION,
    ControlPlane,
    ControlPlaneError,
    JobRecord,
    acquire_data_root_lock,
    control_db_path,
    release_data_root_lock,
    reset_control_plane,
)
from server.jobs import reset_job_runner  # noqa: E402
from server.settings import reset_settings_cache  # noqa: E402
from server.stores import reset_store_manager  # noqa: E402

API_KEY = "test-bootstrap-key"


@pytest.fixture(autouse=True)
def _cleanup_singletons():
    yield
    # job-runner threads USE stores — the runner must stop before the
    # stores it writes to are torn down (see tests/_service.py).
    reset_job_runner()
    reset_store_manager()
    reset_control_plane()
    reset_settings_cache()


def _control(tmp_path, **kwargs) -> ControlPlane:
    return ControlPlane(control_db_path(tmp_path), **kwargs)


# --- schema + migrations ---------------------------------------------------

def test_fresh_database_stamps_current_schema_version(tmp_path):
    cp = _control(tmp_path)
    with sqlite3.connect(cp.path) as raw:
        row = raw.execute(
            "SELECT value FROM control_meta WHERE key = 'schema_version'"
        ).fetchone()
    cp.close()
    assert int(row[0]) == CONTROL_SCHEMA_VERSION


def test_reopening_is_idempotent(tmp_path):
    """A restart must not re-run creation as if the store were new."""
    first = _control(tmp_path)
    record, _plaintext = first.create_api_key("ci")
    first.close()

    second = _control(tmp_path)
    names = [k.name for k in second.list_api_keys()]
    second.close()
    assert names == ["ci"]
    assert record.name == "ci"


def test_database_from_a_newer_build_refuses_to_open(tmp_path):
    """Opening a forward-versioned database would mean writing rows a newer
    schema may reinterpret. Refuse, loudly, instead of limping."""
    cp = _control(tmp_path)
    cp.close()
    with sqlite3.connect(control_db_path(tmp_path)) as raw:
        raw.execute(
            "UPDATE control_meta SET value = ? WHERE key = 'schema_version'",
            (str(CONTROL_SCHEMA_VERSION + 1),),
        )
        raw.commit()

    with pytest.raises(ControlPlaneError) as exc:
        _control(tmp_path)
    assert "newer than this build" in str(exc.value)


def test_missing_migration_path_raises_rather_than_passing_silently(tmp_path):
    """The `except Exception: pass` this project deleted from the store's
    migrator must not reappear here: an unwalkable schema is a hard error."""
    cp = _control(tmp_path)
    cp.close()
    with sqlite3.connect(control_db_path(tmp_path)) as raw:
        raw.execute("UPDATE control_meta SET value = '0' WHERE key = 'schema_version'")
        raw.commit()

    with pytest.raises(ControlPlaneError) as exc:
        _control(tmp_path)
    assert "no control-plane migration path" in str(exc.value)


# --- bounded tables --------------------------------------------------------

def test_request_log_is_capped_in_the_same_transaction_as_the_insert(tmp_path):
    """The audit_bundles lesson: an ops table with no ceiling is a
    disk-exhaustion bug on a delay fuse."""
    cp = _control(tmp_path, request_log_max=10)
    for i in range(35):
        cp.record_request(request_id=f"r{i}", method="GET", route="/v1/search",
                          status_code=200, latency_ms=1.0, key_name="ci")
    total = cp.count_requests()
    newest = cp.recent_requests(limit=1)[0]
    cp.close()
    assert total == 10, "cap must hold without a separate pruning pass"
    assert newest.request_id == "r34", "pruning must drop the OLDEST rows"


def test_control_database_runs_in_wal_mode(tmp_path):
    """Measured, 0729: without WAL this database costs an fsync per request —
    p50 14.5 -> 30.0 ms and throughput 2114 -> 996 req/s under 32-way load.
    With WAL + synchronous=NORMAL the same load costs +17%, not +107%. An ops
    log that taxes every /v1/search is one an operator turns off, so guard the
    pragma: a silent revert would be invisible until someone benchmarks again.

    Distinct from ADR 0001 §2's no-WAL rule, which governs the per-user stores
    holding user memories — not this process-local operational database.
    """
    cp = _control(tmp_path)
    with cp._lock:
        journal = cp._conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = cp._conn.execute("PRAGMA synchronous").fetchone()[0]
    cp.close()
    assert journal.lower() == "wal"
    assert sync == 1, "synchronous=NORMAL (1); FULL (2) reinstates the per-commit fsync"


def test_request_log_max_zero_is_a_real_off_switch(tmp_path):
    cp = _control(tmp_path, request_log_max=0)
    cp.record_request(request_id="r0", method="GET", route="/health",
                      status_code=200, latency_ms=1.0, key_name=None)
    total = cp.count_requests()
    cp.close()
    assert total == 0


def test_job_table_is_capped_oldest_first(tmp_path):
    cp = _control(tmp_path, job_max=5)
    for i in range(12):
        cp.insert_job(JobRecord(job_id=f"j{i}", kind="ingest", user_id="alice",
                                status="pending", created_at=f"2026-07-29T00:00:{i:02d}+00:00"))
    surviving = [f"j{i}" for i in range(12) if cp.get_job(f"j{i}") is not None]
    counts = cp.count_jobs_by_status()
    cp.close()
    assert sum(counts.values()) == 5
    assert surviving == ["j7", "j8", "j9", "j10", "j11"], \
        "the newest jobs are the ones a caller polls; the cap drops the oldest"


# --- api keys --------------------------------------------------------------

def test_plaintext_is_never_persisted(tmp_path):
    """A leaked control database must not hand over working credentials."""
    cp = _control(tmp_path)
    _record, plaintext = cp.create_api_key("agent-1")
    cp.close()
    blob = control_db_path(tmp_path).read_bytes()
    assert plaintext.encode() not in blob


def test_verify_accepts_a_live_key_and_records_use(tmp_path):
    cp = _control(tmp_path)
    record, plaintext = cp.create_api_key("agent-1")
    assert record.last_used_at is None

    verified = cp.verify_api_key(plaintext)
    assert verified is not None and verified.id == record.id

    after = [k for k in cp.list_api_keys() if k.id == record.id][0]
    cp.close()
    assert after.last_used_at is not None, "ops view cannot answer 'was this used?' without it"


def test_revoked_key_is_indistinguishable_from_an_unknown_one(tmp_path):
    cp = _control(tmp_path)
    record, plaintext = cp.create_api_key("leaked")
    revoked = cp.revoke_api_key(record.id)
    assert revoked is not None and revoked.revoked

    assert cp.verify_api_key(plaintext) is None
    assert cp.verify_api_key("sm_never-issued") is None

    # Revocation is idempotent AND keeps the original timestamp: rewriting it
    # would erase when the key actually stopped working.
    again = cp.revoke_api_key(record.id)
    still_listed = [k.id for k in cp.list_api_keys()]
    cp.close()
    assert again is not None and again.revoked_at == revoked.revoked_at
    assert record.id in still_listed, "a revoked key that vanishes takes its history with it"


def test_revoking_an_unknown_key_returns_none(tmp_path):
    cp = _control(tmp_path)
    result = cp.revoke_api_key("no-such-id")
    cp.close()
    assert result is None


# --- job durability --------------------------------------------------------

def test_jobs_survive_a_restart(tmp_path):
    """The bug this whole table exists for: GET /v1/jobs/{id} used to 404
    after a restart, which reads identically to 'never submitted'."""
    first = _control(tmp_path)
    first.insert_job(JobRecord(job_id="j1", kind="ingest", user_id="alice",
                               status="pending", created_at="2026-07-29T00:00:00+00:00"))
    first.update_job("j1", status="succeeded", result={"facts_extracted": 3},
                     finished_at="2026-07-29T00:00:05+00:00")
    first.close()

    second = _control(tmp_path)
    job = second.get_job("j1")
    second.close()
    assert job is not None
    assert job.status == "succeeded"
    assert job.result == {"facts_extracted": 3}


def test_orphaned_jobs_become_a_readable_failure_not_an_eternal_running(tmp_path):
    first = _control(tmp_path)
    first.insert_job(JobRecord(job_id="alive", kind="ingest", user_id="alice",
                               status="running", created_at="2026-07-29T00:00:00+00:00"))
    first.insert_job(JobRecord(job_id="done", kind="ingest", user_id="alice",
                               status="succeeded", created_at="2026-07-29T00:00:01+00:00"))
    first.close()

    second = _control(tmp_path)
    closed = second.reconcile_orphaned_jobs()
    orphan = second.get_job("alive")
    untouched = second.get_job("done")
    second.close()

    assert closed == 1
    assert orphan is not None and orphan.status == "failed"
    assert "server_restarted" in (orphan.error or "")
    assert untouched is not None and untouched.status == "succeeded", \
        "reconciliation must not rewrite terminal states"


def test_update_job_rejects_unknown_columns(tmp_path):
    cp = _control(tmp_path)
    cp.insert_job(JobRecord(job_id="j1", kind="ingest", user_id="alice",
                            status="pending", created_at="2026-07-29T00:00:00+00:00"))
    with pytest.raises(ValueError):
        cp.update_job("j1", user_id="mallory")
    cp.close()


# --- single-writer enforcement (ADR 0001 §2) -------------------------------

def test_a_refused_process_leaves_evidence_the_holder_can_report(tmp_path):
    """Verified in a container, 0729: `--workers 4` does not fail loudly. The
    lock holds and the data is safe, but uvicorn restarts each refused worker
    forever while the container stays `running` and /health stays 200. The
    loser cannot fix that — but it can write down that it lost, so the holder
    can tell an operator what no other signal will."""
    cp = _control(tmp_path)  # create the database the loser will write into
    before, _ = cp.lock_contention()
    cp.close()

    acquire_data_root_lock(tmp_path)
    try:
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(sys.path[0])!r})
            from server.control import acquire_data_root_lock, ControlPlaneError
            try:
                acquire_data_root_lock({str(tmp_path)!r})
            except ControlPlaneError:
                pass
        """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
    finally:
        release_data_root_lock(tmp_path)

    cp = _control(tmp_path)
    count, last = cp.lock_contention()
    cp.close()
    assert count == before + 1
    assert last is not None


@pytest.mark.skipif(__import__("os").getuid() == 0, reason="root can write anywhere")
def test_unwritable_data_root_names_the_cause_not_just_the_path(tmp_path):
    """Verified in a container, 0729: a pre-existing root-owned volume exits
    with a bare PermissionError naming `/data/.control`. Loud is right; mute
    about the cause is not."""
    import os
    root = tmp_path / "readonly"
    root.mkdir()
    os.chmod(root, 0o500)
    try:
        with pytest.raises(ControlPlaneError) as exc:
            acquire_data_root_lock(root)
        message = str(exc.value)
        assert "data_root_not_writable" in message
        assert "chown" in message, "an error an operator cannot act on is half an error"
    finally:
        os.chmod(root, 0o700)


def test_lock_contention_reads_zero_on_a_healthy_deployment(tmp_path):
    cp = _control(tmp_path)
    count, last = cp.lock_contention()
    cp.close()
    assert (count, last) == (0, None)


def test_same_process_reacquire_is_a_noop(tmp_path):
    """Every test in the suite builds an app; the lock must not deny itself."""
    acquire_data_root_lock(tmp_path)
    acquire_data_root_lock(tmp_path)  # must not raise
    release_data_root_lock(tmp_path)


def test_a_second_process_refuses_to_serve_the_same_data_root(tmp_path):
    """This is the test that makes `--workers 1` real. Per-user stores are
    SQLite without WAL; a second writer corrupts them, so the second process
    must die at startup rather than at data-loss time."""
    acquire_data_root_lock(tmp_path)
    try:
        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(sys.path[0])!r})
            from server.control import acquire_data_root_lock, ControlPlaneError
            try:
                acquire_data_root_lock({str(tmp_path)!r})
            except ControlPlaneError as e:
                print(json.dumps({{"locked": True, "message": str(e)}}))
            else:
                print(json.dumps({{"locked": False, "message": ""}}))
        """)
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
            cwd=str(control_db_path(tmp_path).parent.parent.parent),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["locked"] is True
        assert "data_root_locked" in payload["message"]
    finally:
        release_data_root_lock(tmp_path)


# --- /v1/admin/* -----------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch) -> TestClient:
    """Auth ENABLED — these routes are the ones where auth behaviour matters."""
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SODAMEM_AUTH_DISABLED", "false")
    monkeypatch.setenv("SODAMEM_API_KEY", API_KEY)
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "unused-test-key")
    reset_settings_cache()
    reset_job_runner()
    reset_store_manager()
    reset_control_plane()
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    client = TestClient(create_app())
    client.headers.update({"Authorization": f"Bearer {API_KEY}"})
    return client


def test_config_redacts_every_secret(admin_client):
    r = admin_client.get("/v1/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth"] == "enabled"
    assert body["api_key_set"] is True
    assert body["llm_api_key_set"] is True
    assert body["workers"] == 1
    # The whole point: the payload proves a secret is configured without
    # containing it — not even masked, which leaks length.
    serialized = json.dumps(body)
    assert API_KEY not in serialized
    assert "unused-test-key" not in serialized


def test_minted_key_authenticates_and_revocation_takes_effect(admin_client):
    created = admin_client.post("/v1/admin/keys", json={"name": "agent-1"})
    assert created.status_code == 201
    payload = created.json()
    plaintext = payload["api_key"]
    key_id = payload["key"]["id"]

    # The named key works on a normal route, and is attributed by name.
    scoped = TestClient(admin_client.app)
    scoped.headers.update({"X-API-Key": plaintext})
    assert scoped.get("/v1/metrics").status_code == 200

    assert admin_client.delete(f"/v1/admin/keys/{key_id}").status_code == 200
    assert scoped.get("/v1/metrics").status_code == 401, "revocation must be immediate"


def test_listing_keys_never_returns_plaintext(admin_client):
    created = admin_client.post("/v1/admin/keys", json={"name": "agent-1"}).json()
    listed = admin_client.get("/v1/admin/keys").json()
    assert listed["total"] == 1
    assert created["api_key"] not in json.dumps(listed)
    assert listed["keys"][0]["prefix"].startswith("sm_")


def test_revoking_an_unknown_key_is_404(admin_client):
    r = admin_client.delete("/v1/admin/keys/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_admin_routes_require_auth(admin_client):
    anon = TestClient(admin_client.app)
    for path in ("/v1/admin/config", "/v1/admin/keys", "/v1/admin/requests",
                 "/v1/admin/stats"):
        assert anon.get(path).status_code == 401, path


def test_request_log_records_the_route_template_and_the_caller(admin_client):
    admin_client.get("/v1/metrics")
    entries = admin_client.get("/v1/admin/requests").json()["requests"]
    routes = {e["route"] for e in entries}
    assert "/v1/metrics" in routes
    assert all(e["key_name"] == "bootstrap" for e in entries if e["status_code"] == 200)


def test_request_log_never_records_a_raw_path_with_an_id(admin_client):
    """High-cardinality raw paths would turn an ops table into an index of
    user data — same reason the latency registry keys on the template."""
    admin_client.get("/v1/jobs/some-unknown-job-id")
    entries = admin_client.get("/v1/admin/requests").json()["requests"]
    assert any(e["route"] == "/v1/jobs/{job_id}" for e in entries)
    assert not any("some-unknown-job-id" in e["route"] for e in entries)


def test_rejected_request_has_no_caller_attributed(admin_client):
    anon = TestClient(admin_client.app)
    anon.get("/v1/metrics")  # 401
    entries = admin_client.get("/v1/admin/requests").json()["requests"]
    unauthorized = [e for e in entries if e["status_code"] == 401]
    assert unauthorized, "the rejection itself must still be visible to ops"
    assert all(e["key_name"] is None for e in unauthorized), \
        "a 401 has no caller; naming one would be a lie in the ops view"


def test_successful_health_probes_do_not_flood_the_rolling_window(admin_client):
    """Docker polls /health every 30s. Logging that is 2,880 rows a day of
    'still alive', which evicts a 10k window in under four days and leaves an
    ops view that can only see its own heartbeat."""
    for _ in range(5):
        assert admin_client.get("/health").status_code == 200
    routes = [e["route"] for e in admin_client.get("/v1/admin/requests").json()["requests"]]
    assert "/health" not in routes


def test_a_failing_health_probe_is_still_recorded():
    """Only the boring 2xx is dropped. A probe that fails is exactly the row
    an operator needs after an outage."""
    from server.app import _worth_logging
    assert _worth_logging("/health", 200) is False
    assert _worth_logging("/health", 503) is True
    assert _worth_logging("/v1/search", 200) is True


def test_console_assets_never_reach_the_route_column():
    """Observed in the browser, 0729: opening the console logged
    `/console/assets/geist-latin-wght-normal-BgDaEnEv.woff2` — a CONTENT-HASHED
    filename in the column documented to hold route templates and never raw
    paths. Every frontend rebuild would mint new values, and serving a page is
    not API traffic anyway."""
    from server.app import _worth_logging
    assert _worth_logging("/console/assets/index-Capqraho.css", 200) is False
    assert _worth_logging("/console/favicon.svg", 200) is False
    assert _worth_logging("/favicon.ico", 200) is False
    # A broken console mount is still worth seeing.
    assert _worth_logging("/console/assets/missing.js", 404) is True
    # And nothing here may swallow real API traffic.
    assert _worth_logging("/v1/memories/{memory_id}", 200) is True


def test_stats_counts_user_stores_and_excludes_the_control_directory(admin_client):
    admin_client.post("/v1/memories", json={
        "user_id": "alice", "async_mode": False,
        "messages": [{"role": "user", "content": "I own a red bicycle."}],
    })
    stats = admin_client.get("/v1/admin/stats").json()
    assert stats["users"] == 1
    assert [s["user_id"] for s in stats["largest_stores"]] == ["alice"]
    assert stats["stores_bytes"] > 0
    assert stats["control_db_bytes"] > 0
    assert stats["requests_logged"] > 0


def test_control_db_size_counts_the_wal_not_just_the_main_file(admin_client, tmp_path):
    """Caught in the browser, 0729: under WAL the rows sit in `-wal` until a
    checkpoint, so stat()-ing the main file alone reported 4.0 KB for a 4.6 MB
    database. A disk number wrong by three orders of magnitude is worse than
    none, because someone will trust it."""
    from server.control import control_db_path
    from server.routes.admin import _sqlite_bytes
    from server.settings import get_settings

    for _ in range(50):
        admin_client.get("/v1/metrics")
    db = control_db_path(get_settings().data_root)
    reported = admin_client.get("/v1/admin/stats").json()["control_db_bytes"]

    main_only = db.stat().st_size
    wal = Path(str(db) + "-wal")
    assert wal.exists(), "WAL mode should have produced a -wal file by now"

    # Deliberately not an equality check against a freshly-computed total: the
    # /v1/admin/stats call is itself logged, so the WAL grows between the
    # server's measurement and this one. The property that matters is that the
    # reported figure counts the sidecar files at all — before the fix it was
    # exactly `main_only`, which is what made it wrong by 1000x.
    assert reported > main_only, "reported size ignores the WAL where the rows actually are"
    assert reported <= _sqlite_bytes(db), "only this test's own traffic may have been added since"
