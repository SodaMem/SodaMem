"""The single local service every client talks to.

ADR 0001 §2 makes one writer a correctness constraint, not a throughput
setting: per-user stores are SQLite without WAL. Meanwhile every coding-tool
integration spawns its own process — one per editor, one per hook invocation.
Those two facts only reconcile one way: exactly one process owns the stores
and everything else speaks HTTP to it.

So `sodamem daemon ensure` is not a convenience wrapper. It is the thing that
makes "install SodaMem in Claude Code AND Cursor" a supported sentence.

Idempotent by construction: `ensure` health-checks the configured URL first
and starts nothing if a service already answers there. Two editors racing to
start one on first launch is the normal case, not the edge case — and the
loser of that race finds a healthy service and exits happy.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from importlib.util import find_spec
from pathlib import Path

from .http import DEFAULT_URL, Client, ServiceError


def state_dir() -> Path:
    root = Path(os.environ.get("SODAMEM_HOME", Path.home() / ".sodamem"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def pid_file() -> Path:
    return state_dir() / "daemon.pid"


def log_file() -> Path:
    return state_dir() / "daemon.log"


def status(url: str = "", api_key: str = "") -> dict:
    """What is actually answering, not what a pid file claims.

    A pid file says a process exists. It does not say the process is serving,
    or that it is serving THIS data root, or that it is ours at all after a
    reboot recycled the number. The health endpoint answers the only question
    a caller has.
    """
    client = Client(url or None, api_key)
    try:
        health = client.health()
    except ServiceError as exc:
        return {"running": False, "url": client.base_url, "error": str(exc)}
    return {
        "running": True,
        "url": client.base_url,
        "version": health.get("version"),
        "auth": health.get("auth"),
        "schema_version": health.get("schema_version"),
    }


def ensure(*, url: str = "", api_key: str = "", data_root: str = "",
           wait_s: float = 30.0) -> dict:
    """Start a service if none answers. Returns the resulting status."""
    current = status(url, api_key)
    if current["running"]:
        current["started"] = False
        current.setdefault("warnings", extraction_warnings())
        return current

    target = url or os.environ.get("SODAMEM_API_URL") or DEFAULT_URL
    host, port = _split(target)
    env = {**os.environ}
    if data_root:
        env["SODAMEM_DATA_ROOT"] = data_root
    env.setdefault("SODAMEM_DATA_ROOT", str(state_dir() / "data"))
    if api_key:
        env["SODAMEM_API_KEY"] = api_key
    if not env.get("SODAMEM_API_KEY"):
        # The server refuses to start unauthenticated (server/settings.py), and
        # rightly. Rather than fail with that message, bind to loopback with
        # auth off: this is a personal daemon on 127.0.0.1, and demanding a
        # secret to talk to your own machine is the kind of friction that ends
        # with people disabling the thing entirely. Anything non-loopback keeps
        # the requirement.
        if host in ("127.0.0.1", "localhost", "::1"):
            env["SODAMEM_AUTH_DISABLED"] = "true"

    # Preflight, before anything is spawned or written: the daemon can only
    # ever run on THIS interpreter (see `_serve_command`), so if this
    # interpreter has no uvicorn there is nothing to start. `find_spec` looks
    # without importing. Saying so here costs one sentence the user can act
    # on; the alternative is a spawned process that dies with `code 1` in a
    # log nobody is watching.
    if find_spec("uvicorn") is None:
        return {
            "running": False, "url": target, "started": False,
            "error": (
                f"uvicorn is not installed in the interpreter running SodaMem "
                f"({sys.executable}), and the daemon only ever runs there. "
                f"Install the server extra: pip install 'sodamem[server]'"
            ),
        }

    Path(env["SODAMEM_DATA_ROOT"]).mkdir(parents=True, exist_ok=True)
    command = _serve_command(host, port)
    log = open(log_file(), "ab")
    # The interpreter and the exact argv go in the header, not in any HTTP
    # body (issue #13 AC8(v): `/health` is unauthenticated and must not leak
    # filesystem paths). Written BEFORE the spawn so a process that dies in
    # milliseconds still leaves behind who was supposed to run it.
    log.write(f"\n--- sodamem daemon start {time.strftime('%Y-%m-%dT%H:%M:%S')} "
              f"interpreter={sys.executable} command={shlex.join(command)} ---\n"
              .encode())
    log.flush()
    process = subprocess.Popen(
        command, env=env, stdout=log, stderr=log,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    pid_file().write_text(str(process.pid))

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if process.poll() is not None:
            return {
                "running": False, "url": target, "started": False,
                "error": (
                    f"the service exited immediately (code {process.returncode}). "
                    f"See {log_file()}."
                ),
            }
        current = status(target, api_key)
        if current["running"]:
            current["started"] = True
            current["pid"] = process.pid
            current["data_root"] = env["SODAMEM_DATA_ROOT"]
            current["warnings"] = extraction_warnings(env)
            return current
        time.sleep(0.25)
    return {"running": False, "url": target, "started": False,
            "error": f"the service did not become healthy in {wait_s:.0f}s. "
                     f"See {log_file()}."}


def stop() -> dict:
    """Stop the daemon this machine started, if it is still ours."""
    path = pid_file()
    if not path.exists():
        return {"stopped": False, "reason": "no pid file — nothing was started by `sodamem daemon`"}
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        path.unlink(missing_ok=True)
        return {"stopped": False, "reason": "unreadable pid file, removed"}
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return {"stopped": False, "reason": f"process {pid} was already gone"}
    except PermissionError:
        return {"stopped": False, "reason": f"process {pid} is not ours to stop"}
    path.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


def extraction_warnings(env: dict | None = None) -> list[str]:
    """Say up front what will otherwise fail silently later.

    Retain submits ingest ASYNCHRONOUSLY: the hook gets a 202 and the job
    fails afterwards, in a log nobody is watching, once per turn, forever.
    The user's symptom is "memory does nothing" with no error anywhere they
    look. One line at start-up is the difference between a five-minute fix
    and never finding it.
    """
    source = env if env is not None else os.environ
    if source.get("SODAMEM_LLM_API_KEY"):
        return []
    return [
        "no SODAMEM_LLM_API_KEY is set, so fact extraction cannot run: "
        "recall will work, but every retain will be accepted and then fail. "
        "Set SODAMEM_LLM_PROVIDER and SODAMEM_LLM_API_KEY, then "
        "`sodamem daemon stop && sodamem daemon ensure`."
    ]


def _serve_command(host: str, port: int) -> list[str]:
    """`--workers 1` is spelled out here because it is a correctness
    constraint (ADR 0001 §2), and a default that happens to be 1 is not the
    same thing as a guarantee.

    The interpreter is ALWAYS `sys.executable`, never a `uvicorn` found on
    PATH (issue #14). PATH answers "is there a uvicorn on this machine", not
    "where is SodaMem installed" — and uvicorn's CLI does
    `sys.path.insert(0, ".")`, so a stranger's uvicorn launched in a source
    checkout imports `server` happily and then serves the stores with ITS
    OWN chromadb. That is not cosmetic: chroma migrates a store's schema
    forward on open, with no downgrade path, so one wrong start permanently
    locks the correctly-installed SodaMem out of the user's memory. Do not
    reintroduce a PATH lookup here.
    """
    return [
        sys.executable, "-m", "uvicorn",
        "server.app:build", "--factory",
        "--host", host, "--port", str(port), "--workers", "1",
    ]


def _split(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse
    parsed = urlparse(url if "//" in url else f"http://{url}")
    return parsed.hostname or "127.0.0.1", parsed.port or 8000
