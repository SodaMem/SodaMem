"""Cooperative cross-process maintenance locks for one SQLite store."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import BinaryIO

try:
    import fcntl as _fcntl
except ImportError:  # Windows — no flock; hold an open handle only.
    _fcntl = None

__all__ = [
    "MaintenanceLock",
    "MaintenanceLockBusy",
    "acquire_exclusive_maintenance_lock",
    "acquire_shared_maintenance_lock",
    "maintenance_lock_path",
]


class MaintenanceLockBusy(RuntimeError):
    """Raised when nonblocking exclusive maintenance cannot start offline."""


class MaintenanceLock:
    """An owned advisory file lock. ``release`` is idempotent."""

    def __init__(self, path: Path, handle: BinaryIO) -> None:
        self.path = path
        self._handle: BinaryIO | None = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "MaintenanceLock":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def maintenance_lock_path(database_path: str | Path) -> Path:
    database = Path(database_path).resolve()
    return database.with_name(f"{database.name}.maintenance.lock")


def _acquire(database_path: str | Path, *, exclusive: bool, nonblocking: bool) -> MaintenanceLock:
    path = maintenance_lock_path(database_path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        handle = os.fdopen(fd, "a+b", buffering=0)
    except Exception:
        os.close(fd)
        raise

    if _fcntl is None:
        # Best-effort on Windows: keep the lockfile open so accidental double
        # exclusive writers still collide on some editors, but do not emulate
        # POSIX flock. Fine for read-only frozen-benchmark opens.
        if exclusive and nonblocking and sys.platform == "win32":
            pass
        return MaintenanceLock(path, handle)

    operation = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
    if nonblocking:
        operation |= _fcntl.LOCK_NB
    try:
        _fcntl.flock(handle.fileno(), operation)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise MaintenanceLockBusy(
                f"maintenance lock is already held: {path}"
            ) from exc
        raise
    return MaintenanceLock(path, handle)


def acquire_shared_maintenance_lock(
    database_path: str | Path,
) -> MaintenanceLock:
    """Mark a Store as live until its ``close`` releases this shared lock."""
    return _acquire(database_path, exclusive=False, nonblocking=False)


def acquire_exclusive_maintenance_lock(
    database_path: str | Path,
) -> MaintenanceLock:
    """Acquire offline ownership immediately or fail without waiting."""
    return _acquire(database_path, exclusive=True, nonblocking=True)
