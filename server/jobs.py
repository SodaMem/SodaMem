"""Async job runner for write-path calls (PRD R1.4), backed by the control
plane (ADR 0001 §3).

Still deliberately NOT redis/celery: a single-process thread pool covers the
self-hosted case, and the store layer is per-user SQLite with its own lock, so
a broker would buy queueing semantics we cannot yet exploit. What changed is
where the registry lives. It used to be a process-local dict, which made
`GET /v1/jobs/{id}` return 404 after a restart — and a 404 there is
indistinguishable from "no such job ever existed". A client that submitted an
async ingest and then hit a deploy had no way to learn whether its data
landed. That is the same silent-failure shape this project refuses everywhere
else; it does not get an exemption for being our own code.

Now: job rows persist, and `reconcile_orphaned_jobs()` runs at startup so a
job whose worker died with the process becomes a readable `failed` terminal
state rather than a `running` that will never finish. ADR 0001 §3 requires
failure to be retrievable, not merely recorded.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sodamem.errors import SodaMemError

from server.control import ControlPlane, JobRecord, get_control_plane


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _describe(exc: BaseException) -> str:
    """Wire-format for a failed job's `error`.

    Stays a plain string because `server.models.Job.error` is one and the
    field is public API. A typed SodaMemError still contributes its stable
    `code` rather than its Python class name — the code is what a client can
    branch on; `IngestError` is an implementation detail that could be renamed.
    """
    if isinstance(exc, SodaMemError):
        code = getattr(getattr(exc, "code", None), "value", None) or "internal_error"
        return f"{code}: {exc}"
    return f"{type(exc).__name__}: {exc}"


class JobRunner:
    def __init__(self, max_workers: int = 4, control: ControlPlane | None = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="sodamem-job")
        self._control = control

    @property
    def control(self) -> ControlPlane:
        # Resolved lazily, not in __init__: the runner is a process-wide
        # singleton that may be constructed before settings point at the
        # final data_root (tests do exactly this).
        return self._control or get_control_plane()

    def submit(self, kind: str, user_id: str, fn: Callable[[], dict]) -> JobRecord:
        job = JobRecord(job_id=uuid.uuid4().hex, kind=kind, user_id=user_id,
                        status="pending", created_at=_now())
        # Insert BEFORE the pool sees the work: a worker that starts and
        # updates a row that does not exist yet would drop the update, and the
        # caller would poll a job stuck at "pending" forever.
        self.control.insert_job(job)

        def _run() -> None:
            self.control.update_job(job.job_id, status="running")
            try:
                result = fn()
                self.control.update_job(job.job_id, status="succeeded",
                                        result=result, finished_at=_now())
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                self.control.update_job(job.job_id, status="failed",
                                        error=_describe(exc), finished_at=_now())

        self._pool.submit(_run)
        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self.control.get_job(job_id)

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and, by default, WAIT for what is already
        running to finish.

        `cancel_futures` only drops jobs still queued — a job already
        executing keeps going regardless. With `wait=False` that meant
        `shutdown()` returned while an ingest was still writing SQLite and
        chroma, so a caller that then tore down the data directory (every test
        via tmp_path, but equally a container stopping a volume) raced a live
        writer. Waiting is what makes "shut down" true.

        `wait=False` remains available for a hard process exit where blocking
        on a stuck job is worse than abandoning it.
        """
        self._pool.shutdown(wait=wait, cancel_futures=True)


_runner: JobRunner | None = None


def get_job_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


def reset_job_runner() -> None:
    global _runner
    if _runner is not None:
        _runner.shutdown()
    _runner = None
