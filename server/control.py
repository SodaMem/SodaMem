"""The control plane: one SQLite database for operational state (ADR 0001).

Three kinds of data live here — async job status, API keys, request logs —
and the ADR's reason for keeping them OUT of the per-user memory stores is
deletion semantics, not tidiness: `forget(user_id)` must be able to drop a
user's entire store without also erasing the operator's job history and usage
records. A job is not owned by the user it happens to be about.

Physically it is `<data_root>/.control/sodamem_control.db`. The directory
name starts with a dot deliberately: `server/stores.py`'s `_USER_ID_RE`
requires a user_id to START with an alphanumeric, so there is no user_id that
can ever resolve onto this path. The isolation is structural, not a naming
convention someone has to remember.

Three properties this module refuses to compromise on, each of them a lesson
already paid for elsewhere in this codebase:

1. **Bounded by construction.** `request_logs` and `jobs` both carry a row
   cap enforced in the SAME transaction as the insert. The audit_bundles
   incident (unbounded writes, 1,871 rows across 325 stores, ~500MB
   reclaimed) is the precedent: an operational table with no ceiling is a
   disk-exhaustion bug with a delay fuse.

2. **Migrations fail loudly.** Same ledger shape as
   `sodamem/memory/storage/migrations.py` — `(from, to) -> [steps]`, and a
   database whose schema cannot be walked forward refuses to open instead of
   limping on a half-migrated schema.

3. **Single writer, enforced.** `acquire_data_root_lock()` takes an exclusive
   flock on the data root. ADR 0001 §2 calls single-worker a correctness
   constraint (per-user SQLite, no WAL, cross-process writes corrupt), and
   until now that held only because uvicorn's default worker count happens to
   be 1. A constraint defended by a default is not defended.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast

logger = logging.getLogger(__name__)

CONTROL_SCHEMA_VERSION = 1

#: How stale an api_key's `last_used_at` may get before a request pays for an
#: UPDATE. Without this, every authenticated request writes a row — a write
#: amplification nobody asked for on a read-heavy deployment. One minute of
#: staleness is invisible in an ops view and turns per-request writes into
#: per-minute ones.
LAST_USED_REFRESH_SECONDS = 60

KEY_PREFIX = "sm_"


class ControlPlaneError(RuntimeError):
    """Startup-time failure: the control database cannot be opened safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS control_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Plaintext is NEVER stored. The server can verify a presented key (hash and
-- look up) but cannot reproduce one, so a leaked control database does not
-- hand over working credentials. `prefix` exists purely so an ops view can
-- say WHICH key without being able to replay it.
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- Route TEMPLATE, not raw path (`/v1/memories/{memory_id}`, never the id) —
-- same reasoning as the latency registry: raw paths turn an ops table into a
-- high-cardinality index of user data. No request body, no headers, no query
-- string: this table answers "what shape of traffic did this box serve",
-- not "what did user X ask".
CREATE TABLE IF NOT EXISTS request_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    method      TEXT NOT NULL,
    route       TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    latency_ms  REAL NOT NULL,
    key_name    TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_logs_created ON request_logs(created_at DESC);

-- ADR 0001 §3: an async failure must be a RETRIEVABLE terminal state. The
-- error column holds the serialized SodaMemError shape (code/message/details)
-- so a client polling GET /v1/jobs/{id} after a restart still learns why its
-- ingest failed, instead of a 404 that is indistinguishable from "never
-- existed".
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


# ---------------------------------------------------------------------------
# Migrations — same ledger contract as sodamem/memory/storage/migrations.py
# ---------------------------------------------------------------------------

MigrationFn = Callable[[sqlite3.Connection], None]

#: `(from_version, to_version) -> [steps]`. Empty today: version 1 is the
#: first schema this module ever shipped, so there is nothing to walk forward
#: from. Version 2 adds its entry here; `_migrate()` needs no edit.
MIGRATIONS: dict[tuple[int, int], list[MigrationFn]] = {}


def _migrate(conn: sqlite3.Connection, *, from_version: int, to_version: int) -> None:
    """Walk the ledger one step at a time. A missing path or a failing step
    raises — a control database on an unknown schema must refuse to open, not
    serve half-migrated rows (the `except Exception: pass` this project
    already deleted once from the store's migrator)."""
    version = from_version
    while version < to_version:
        steps = MIGRATIONS.get((version, version + 1))
        if steps is None:
            raise ControlPlaneError(
                f"no control-plane migration path from schema {version} to "
                f"{version + 1}; refusing to open a half-known database"
            )
        for step in steps:
            try:
                step(conn)
            except Exception as e:  # noqa: BLE001 - re-raised typed, never swallowed
                raise ControlPlaneError(
                    f"control-plane migration step {step.__name__} "
                    f"({version} -> {version + 1}) failed: {e}"
                ) from e
        version += 1
        conn.execute(
            "INSERT INTO control_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(frozen=True)
class RequestLogRecord:
    request_id: str
    method: str
    route: str
    status_code: int
    latency_ms: float
    key_name: str | None
    created_at: str


#: Closed set, matching `server.models.Job.status`. Typed as a Literal rather
#: than `str` so the wire model and the storage record cannot drift: they used
#: to, and mypy flagged the resulting `str` -> Literal assignment in
#: `server/routes/jobs.py` on every run nobody was reading.
JobStatus = Literal["pending", "running", "succeeded", "failed"]


@dataclass
class JobRecord:
    job_id: str
    kind: str
    user_id: str
    status: JobStatus
    created_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


class ControlPlane:
    """Thread-safe handle on the control database.

    One connection guarded by one lock, same shape as `Store`: the job runner
    writes from pool threads while request handlers read from the event loop
    thread, and `check_same_thread=False` without a lock would be a data race
    dressed up as working code.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        request_log_max: int = 10_000,
        job_max: int = 5_000,
    ) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._request_log_max = max(0, int(request_log_max))
        self._job_max = max(1, int(job_max))
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL + synchronous=NORMAL. Measured, 0729: with the rollback journal's
        # default synchronous=FULL this database costs an fsync per request,
        # which doubled p50 (14.5 -> 30.0 ms) and halved throughput (2114 ->
        # 996 req/s) under 32-way concurrency. Paying that on every /v1/search
        # to keep an ops log is a bad trade, and an ops feature that taxes the
        # product path is one an operator will just turn off.
        #
        # This is NOT the WAL that ADR 0001 §2 rules out. That rule is about
        # the PER-USER stores, whose durability protects user memories. This
        # database holds a rolling request log, job status and key metadata,
        # and `acquire_data_root_lock()` guarantees exactly one process writes
        # it. Under WAL, synchronous=NORMAL still survives a process crash;
        # only a host power loss can lose the last commits — for a rolling
        # window of "what did this box serve recently", that is the correct
        # thing to trade. API keys and job rows sit in the same file and could
        # in principle lose their last write to a power cut; a key you can
        # re-mint and a job that reads `failed` are both recoverable, where a
        # 2x latency tax on every request is not.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._open()

    def _open(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            row = self._conn.execute(
                "SELECT value FROM control_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO control_meta (key, value) VALUES ('schema_version', ?)",
                    (str(CONTROL_SCHEMA_VERSION),),
                )
                self._conn.commit()
                return
            found = int(row["value"])
            if found == CONTROL_SCHEMA_VERSION:
                return
            if found > CONTROL_SCHEMA_VERSION:
                # Downgrade: the binary is older than its data. Opening would
                # mean writing rows a newer schema may reinterpret.
                raise ControlPlaneError(
                    f"control database schema {found} is newer than this build "
                    f"supports ({CONTROL_SCHEMA_VERSION}); upgrade SodaMem or "
                    f"point SODAMEM_DATA_ROOT at a different directory"
                )
            _migrate(self._conn, from_version=found, to_version=CONTROL_SCHEMA_VERSION)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - close must not mask a real error
                logger.warning("control plane close failed", exc_info=True)

    # -- api keys -----------------------------------------------------------

    def create_api_key(self, name: str) -> tuple[ApiKeyRecord, str]:
        """Mint a key. Returns `(record, plaintext)` — the plaintext exists in
        this process for exactly as long as it takes to hand it back to the
        caller, and is never persisted. A caller who loses it mints another;
        there is no recovery path by design."""
        plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
        record = ApiKeyRecord(
            id=uuid.uuid4().hex,
            name=name,
            prefix=plaintext[: len(KEY_PREFIX) + 6],
            created_at=_now(),
            last_used_at=None,
            revoked_at=None,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (id, name, key_hash, prefix, created_at, "
                "last_used_at, revoked_at) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (record.id, record.name, _hash_key(plaintext), record.prefix,
                 record.created_at),
            )
            self._conn.commit()
        return record, plaintext

    def list_api_keys(self, *, include_revoked: bool = True) -> list[ApiKeyRecord]:
        sql = "SELECT * FROM api_keys"
        if not include_revoked:
            sql += " WHERE revoked_at IS NULL"
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_row_to_key(r) for r in rows]

    def revoke_api_key(self, key_id: str) -> ApiKeyRecord | None:
        """Idempotent: revoking an already-revoked key keeps the original
        timestamp rather than rewriting history."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)
            ).fetchone()
            if row is None:
                return None
            if row["revoked_at"] is None:
                self._conn.execute(
                    "UPDATE api_keys SET revoked_at = ? WHERE id = ?", (_now(), key_id)
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM api_keys WHERE id = ?", (key_id,)
                ).fetchone()
        return _row_to_key(row)

    def verify_api_key(self, plaintext: str) -> ApiKeyRecord | None:
        """Return the record for a live key, or None.

        Lookup is by hash, so the comparison SQLite performs is on a digest,
        not on the secret: an attacker learns nothing from timing beyond
        "some digest matched", which is the same information a 401 already
        gives. A revoked key is treated exactly like an unknown one.
        """
        if not plaintext:
            return None
        digest = _hash_key(plaintext)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            if _should_refresh_last_used(row["last_used_at"]):
                self._conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
                self._conn.commit()
        return _row_to_key(row)

    # -- request logs -------------------------------------------------------

    def record_request(
        self,
        *,
        request_id: str,
        method: str,
        route: str,
        status_code: int,
        latency_ms: float,
        key_name: str | None,
    ) -> None:
        """Insert + prune in ONE transaction.

        Pruning in a separate transaction (or on a timer) is how a bounded
        table becomes an unbounded one the first time the pruner is skipped.
        `request_log_max = 0` disables the table entirely — an operator who
        does not want request bodies-adjacent data on disk gets a real off
        switch, not a very small cap.
        """
        if self._request_log_max == 0:
            return
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT INTO request_logs (request_id, method, route, "
                    "status_code, latency_ms, key_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (request_id, method, route, int(status_code),
                     float(latency_ms), key_name, _now()),
                )
                self._conn.execute(
                    "DELETE FROM request_logs WHERE id <= ("
                    "  SELECT MAX(id) - ? FROM request_logs"
                    ")",
                    (self._request_log_max,),
                )
                self._conn.execute("COMMIT")
            except Exception:  # noqa: BLE001 - see below
                # Observability must never take the request down with it. The
                # audit path already settled this argument: a failed write to
                # an ops table is a degradation, not a 500 on somebody's
                # search. Logged at warning so it is not invisible either.
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    # The connection itself is gone (closed handle from a
                    # previous data_root). Rolling back is meaningless then,
                    # and raising here would resurrect the exact failure mode
                    # this handler exists to prevent.
                    pass
                logger.warning("request log write failed", exc_info=True)

    def recent_requests(self, *, limit: int = 100, offset: int = 0) -> list[RequestLogRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM request_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
        return [
            RequestLogRecord(
                request_id=r["request_id"], method=r["method"], route=r["route"],
                status_code=r["status_code"], latency_ms=r["latency_ms"],
                key_name=r["key_name"], created_at=r["created_at"],
            )
            for r in rows
        ]

    def count_requests(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) AS n FROM request_logs"
            ).fetchone()["n"])

    # -- jobs ---------------------------------------------------------------

    def insert_job(self, job: JobRecord) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "INSERT INTO jobs (job_id, kind, user_id, status, created_at, "
                "finished_at, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job.job_id, job.kind, job.user_id, job.status, job.created_at,
                 job.finished_at,
                 json.dumps(job.result) if job.result is not None else None,
                 job.error),
            )
            # Same bounded-by-construction rule as request_logs. Oldest first:
            # a caller polling a job it just submitted must never lose to the
            # retention cap.
            self._conn.execute(
                "DELETE FROM jobs WHERE job_id IN ("
                "  SELECT job_id FROM jobs ORDER BY created_at DESC LIMIT -1 OFFSET ?"
                ")",
                (self._job_max,),
            )
            self._conn.execute("COMMIT")

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"])
        allowed = {"status", "finished_at", "result", "error"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*fields.values(), job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"], kind=row["kind"], user_id=row["user_id"],
            # SQLite has no enum; the column is TEXT and only this module ever
            # writes it, so the cast documents the invariant rather than
            # hiding a conversion.
            status=cast("JobStatus", row["status"]), created_at=row["created_at"],
            finished_at=row["finished_at"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
        )

    def reconcile_orphaned_jobs(self) -> int:
        """Close out jobs whose worker died with the previous process.

        Called once at startup. A `pending`/`running` row that survives a
        restart describes a thread that no longer exists — leaving it alone
        means a client polls forever on a job nobody is working. ADR 0001 §3
        makes failure a RETRIEVABLE terminal state, and "the process that
        owned this died" is a failure the caller must be told about, not an
        ambiguity to preserve. Returns how many rows were closed.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
                "WHERE status IN ('pending', 'running')",
                (_now(),
                 "server_restarted: the worker running this job did not survive "
                 "a server restart; resubmit to retry"),
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def lock_contention(self) -> tuple[int, str | None]:
        """How many times a process was refused this data root, and when last.

        Non-zero means someone is running more workers (or more containers)
        than the single-writer constraint allows. The refused process wrote
        this before dying; the holder — the only process alive to be asked —
        reports it. Without this, that misconfiguration is invisible to every
        signal an operator normally trusts (see `_record_lock_contention`).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM control_meta "
                "WHERE key IN ('lock_contention_count', 'lock_contention_last')"
            ).fetchall()
        found = {r["key"]: r["value"] for r in rows}
        try:
            count = int(found.get("lock_contention_count", 0))
        except (TypeError, ValueError):
            count = 0
        return count, found.get("lock_contention_last")

    def count_jobs_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}


def _row_to_key(row: sqlite3.Row) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=row["id"], name=row["name"], prefix=row["prefix"],
        created_at=row["created_at"], last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


def _should_refresh_last_used(last_used_at: str | None) -> bool:
    if not last_used_at:
        return True
    try:
        seen = datetime.fromisoformat(last_used_at)
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen > timedelta(seconds=LAST_USED_REFRESH_SECONDS)


# ---------------------------------------------------------------------------
# Single-writer enforcement (ADR 0001 §2)
# ---------------------------------------------------------------------------

#: Locks held by THIS process, keyed by resolved data root. A second
#: `create_app()` in the same process (every test that builds an app) must
#: re-use the lock it already holds; flock is per open-file-description, so
#: without this map the second open in the same process would deny itself.
_held_locks: dict[str, Any] = {}
_held_lock_guard = threading.Lock()


def acquire_data_root_lock(data_root: Path) -> None:
    """Refuse to start if another process is already serving this data root.

    This is what makes ADR 0001 §2 real. `--workers 2` makes uvicorn fork a
    second process that imports the app factory; that process fails here with
    a message naming the actual constraint, instead of silently interleaving
    writes into per-user SQLite databases that have no WAL to survive it. It
    also catches the other shape of the same mistake: two containers with the
    same volume mounted.

    Best-effort by design on platforms without flock — an unavailable lock
    primitive must not make the server unstartable, so it logs and continues.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        logger.warning(
            "fcntl unavailable: cannot enforce the single-writer constraint "
            "(ADR 0001). Run exactly one server process against %s.", data_root
        )
        return

    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    key = str(root.resolve())
    with _held_lock_guard:
        if key in _held_locks:
            return
        # This process serves exactly ONE data root. Being asked for a
        # different one means the previous claim is stale, so drop it rather
        # than accumulate an open file descriptor per root. In production this
        # branch never runs (one create_app, one root); in tests it is every
        # tmp_path, and without it a long suite leaks fds until ulimit bites.
        for stale_key, stale_handle in list(_held_locks.items()):
            _held_locks.pop(stale_key, None)
            try:
                stale_handle.close()
            except Exception:  # noqa: BLE001
                logger.warning("stale lock release failed for %s", stale_key, exc_info=True)
        lock_path = root / ".control" / "server.lock"
        _mkdir_or_explain(lock_path.parent, root)
        handle = lock_path.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            handle.close()
            _record_lock_contention(root)
            raise ControlPlaneError(
                f"data_root_locked: another SodaMem process is already serving "
                f"{key}. Per-user stores are SQLite without WAL — concurrent "
                f"writers corrupt them (ADR 0001 §2). Run a single worker "
                f"(--workers 1), or give this instance its own SODAMEM_DATA_ROOT."
            ) from e
        # The handle MUST outlive this function: flock is released when the
        # last descriptor for it closes, so a handle that falls out of scope
        # takes the lock with it — silently, leaving a server that believes it
        # is the sole writer and a second one free to join it.
        handle.write(f"{_now()}\n")
        handle.flush()
        _held_locks[key] = handle


def _mkdir_or_explain(path: Path, data_root: Path) -> None:
    """Create the control directory, or fail with an error someone can act on.

    Verified in a container, 0729: mounting a pre-existing root-owned volume
    at /data exits the container with a bare
    `PermissionError: [Errno 13] ... '/data/.control'`. Loud, which is right —
    but it names a path, not a cause, and the reader has to already know the
    image runs as uid 999 to guess what to do. Every other failure this module
    raises says what to change; this one should too.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        import os
        raise ControlPlaneError(
            f"data_root_not_writable: cannot create {path} as uid {os.getuid()}. "
            f"The server runs unprivileged, so the volume mounted at {data_root} "
            f"must be writable by it — a volume pre-populated by a root container "
            f"(or a bind mount owned by another user) is the usual cause. Fix the "
            f"ownership, e.g. `docker run --rm -u 0 -v <volume>:/data alpine "
            f"chown -R {os.getuid()}:{os.getgid()} /data`."
        ) from e


def _record_lock_contention(data_root: Path) -> None:
    """Leave evidence in the shared control database before dying.

    Verified in a container, 0729: `uvicorn --workers 4` does NOT fail the way
    the exception above implies. The lock holds — exactly one worker serves,
    the data is safe — but uvicorn's supervisor restarts each rejected worker
    forever, and the container stays `running` with `/health` returning 200.
    An operator who set `--workers 4` believes they are running four; they are
    running one, and the health signal agrees with them.

    Data safety was never the gap. Honesty was. The losing worker cannot fix
    the deployment, but it CAN write down that it lost — into the one database
    both processes share — so `/v1/admin/config` can report the truth the
    healthcheck cannot see.

    Best-effort on purpose: this runs on a path that is already failing, and
    nothing here may mask the ControlPlaneError the caller is about to raise.
    A first-ever start where several workers race before any database exists
    may lose the first few records; every subsequent restart in the loop lands.
    """
    try:
        db = control_db_path(data_root)
        if not db.exists():
            return
        conn = sqlite3.connect(str(db), timeout=2.0)
        try:
            conn.execute(
                "INSERT INTO control_meta (key, value) VALUES ('lock_contention_count', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
            )
            conn.execute(
                "INSERT INTO control_meta (key, value) VALUES ('lock_contention_last', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_now(),),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - see docstring
        logger.debug("could not record lock contention", exc_info=True)


def release_data_root_lock(data_root: Path) -> None:
    """Test hook — drops this process's claim so a fresh app can take it."""
    key = str(Path(data_root).resolve())
    with _held_lock_guard:
        handle = _held_locks.pop(key, None)
    if handle is not None:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            logger.warning("lock release failed for %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# Process-wide handle
# ---------------------------------------------------------------------------

_control: ControlPlane | None = None
_control_guard = threading.Lock()


def control_db_path(data_root: Path) -> Path:
    return Path(data_root) / ".control" / "sodamem_control.db"


def get_control_plane() -> ControlPlane:
    """The process-wide handle, keyed by the path settings currently point at.

    Path-keyed rather than plain-singleton so that a changed `data_root`
    re-opens instead of silently serving the previous directory's rows. In
    production the path never changes; in tests it changes every tmp_path, and
    a stale handle there is a test that passes against the wrong database.
    """
    global _control
    with _control_guard:
        from server.settings import get_settings
        settings = get_settings()
        wanted = control_db_path(settings.data_root)
        if _control is not None and _control.path == wanted:
            return _control
        if _control is not None:
            _control.close()
        _control = ControlPlane(
            wanted,
            request_log_max=settings.request_log_max,
            job_max=settings.job_retention_max,
        )
        return _control


def reset_control_plane() -> None:
    """Test hook — closes the handle so a new data_root takes effect."""
    global _control
    with _control_guard:
        if _control is not None:
            _control.close()
        _control = None
