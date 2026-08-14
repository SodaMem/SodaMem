#!/usr/bin/env python3
"""Manifest-driven cleanup for the authorized LongMemEval Hobs audit replay."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sodamem.memory.storage.maintenance_lock import (
    MaintenanceLock,
    MaintenanceLockBusy,
    acquire_exclusive_maintenance_lock,
)

AUTHORIZED_ROOT = Path(
    "/Users/aaron.w/Desktop/LongMemEval-ingest/longmemeval_s_500_Hobs"
)
AUTHORIZED_START = datetime(
    2026, 7, 26, 19, 44, 46, tzinfo=ZoneInfo("Asia/Singapore")
).timestamp()
AUTHORIZED_END = datetime(
    2026, 7, 26, 20, 0, 47, 999999, tzinfo=ZoneInfo("Asia/Singapore")
).timestamp()
# Verified inclusive-window topology. The earlier survey omitted lme_q292,
# whose journal state concealed seven authorized rows.
EXPECTED_ROWS = 1_871
EXPECTED_DATABASES = 325
MANIFEST_VERSION = 1


class CleanupError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _json_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _table_digest(
    conn: sqlite3.Connection,
    table: str,
    *,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> tuple[int, str]:
    columns = [
        row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
    ]
    quoted = ",".join(f'"{column}"' for column in columns)
    query = f'SELECT {quoted} FROM "{table}"'
    if where:
        query += f" WHERE {where}"
    encoded_rows = sorted(
        _canonical_json([_json_cell(row[column]) for column in columns])
        for row in conn.execute(query, params)
    )
    digest = hashlib.sha256()
    digest.update(_canonical_json(columns))
    for encoded in encoded_rows:
        digest.update(b"\n")
        digest.update(encoded)
    return len(encoded_rows), digest.hexdigest()


def _sqlite_metrics(path: Path, conn: sqlite3.Connection) -> dict[str, int]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    wal_path = Path(str(path) + "-wal")
    return {
        "database_size_bytes": path.stat().st_size,
        "wal_size_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "logical_database_bytes": page_size * page_count,
        "logical_free_bytes": page_size * freelist_count,
    }


def _non_audit_tables(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'audit_bundles' ORDER BY name"
        )
    ]
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        count, digest = _table_digest(conn, table)
        result[table] = {"row_count": count, "sha256": digest}
    return result


def _target_rows(
    conn: sqlite3.Connection, start_epoch: float, end_epoch: float
) -> tuple[list[dict[str, Any]], int]:
    rows = conn.execute(
        "SELECT bundle_id,user_id,query,payload,created_at "
        "FROM audit_bundles WHERE created_at BETWEEN ? AND ? "
        "ORDER BY bundle_id",
        (start_epoch, end_epoch),
    ).fetchall()
    target_rows = []
    logical_bytes = 0
    for row in rows:
        query_bytes = len(row["query"].encode("utf-8"))
        payload_bytes = len(row["payload"].encode("utf-8"))
        logical_bytes += query_bytes + payload_bytes
        target_rows.append({
            "bundle_id": row["bundle_id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "query_sha256": hashlib.sha256(
                row["query"].encode("utf-8")
            ).hexdigest(),
            "payload_sha256": hashlib.sha256(
                row["payload"].encode("utf-8")
            ).hexdigest(),
            "query_bytes": query_bytes,
            "payload_bytes": payload_bytes,
        })
    return target_rows, logical_bytes


def _snapshot_database(
    path: Path, root: Path, start_epoch: float, end_epoch: float
) -> dict[str, Any] | None:
    with closing(_connect_read_only(path)) as conn:
        if not _has_table(conn, "audit_bundles"):
            return None
        target_rows, logical_bytes = _target_rows(
            conn, start_epoch, end_epoch
        )
        if not target_rows:
            return None
        non_target_count, non_target_digest = _table_digest(
            conn,
            "audit_bundles",
            where="created_at < ? OR created_at > ?",
            params=(start_epoch, end_epoch),
        )
        return {
            "path": str(path.resolve()),
            "relative_path": str(path.resolve().relative_to(root)),
            "target_count": len(target_rows),
            "target_logical_bytes": logical_bytes,
            "rows": target_rows,
            "non_target_audit": {
                "row_count": non_target_count,
                "sha256": non_target_digest,
            },
            "non_audit_tables": _non_audit_tables(conn),
            "sqlite": _sqlite_metrics(path, conn),
        }


def build_manifest(
    root: Path = AUTHORIZED_ROOT,
    *,
    start_epoch: float = AUTHORIZED_START,
    end_epoch: float = AUTHORIZED_END,
    expected_rows: int | None = EXPECTED_ROWS,
    expected_databases: int | None = EXPECTED_DATABASES,
) -> dict[str, Any]:
    """Read all Hobs memory.db files and return a deterministic, non-mutating manifest."""
    root = root.resolve()
    if start_epoch > end_epoch:
        raise CleanupError("authorized window is inverted")
    databases = []
    for path in sorted(root.glob("**/memory.db")):
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise CleanupError(f"database escapes cleanup root: {resolved}") from exc
        snapshot = _snapshot_database(resolved, root, start_epoch, end_epoch)
        if snapshot:
            databases.append(snapshot)
    total_rows = sum(row["target_count"] for row in databases)
    if expected_rows is not None and total_rows != expected_rows:
        raise CleanupError(
            f"expected {expected_rows} target rows, found {total_rows}"
        )
    if expected_databases is not None and len(databases) != expected_databases:
        raise CleanupError(
            f"expected {expected_databases} affected databases, found {len(databases)}"
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "root": str(root),
        "table": "audit_bundles",
        "window": {
            "timezone": "Asia/Singapore",
            "inclusive": True,
            "start_epoch": start_epoch,
            "end_epoch": end_epoch,
        },
        "affected_database_count": len(databases),
        "target_row_count": total_rows,
        "target_logical_bytes": sum(
            row["target_logical_bytes"] for row in databases
        ),
        "databases": databases,
    }


def _semantic_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(manifest))
    for database in cleaned["databases"]:
        database.pop("sqlite", None)
    return cleaned


def _verify_manifest_current(manifest: dict[str, Any]) -> None:
    window = manifest["window"]
    current = build_manifest(
        Path(manifest["root"]),
        start_epoch=window["start_epoch"],
        end_epoch=window["end_epoch"],
        expected_rows=manifest["target_row_count"],
        expected_databases=manifest["affected_database_count"],
    )
    if _semantic_manifest(current) != _semantic_manifest(manifest):
        raise CleanupError("manifest/database drift detected")


def _check_manifest(
    manifest: dict[str, Any],
    expected_digest: str,
    authorized_root: Path,
) -> None:
    actual_digest = manifest_digest(manifest)
    if actual_digest != expected_digest:
        raise CleanupError(
            f"manifest digest mismatch: expected {expected_digest}, got {actual_digest}"
        )
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CleanupError("unsupported manifest version")
    if manifest.get("table") != "audit_bundles":
        raise CleanupError("manifest targets an unauthorized table")
    root = Path(str(manifest.get("root") or "")).resolve()
    if root != authorized_root.resolve():
        raise CleanupError(f"manifest root is unauthorized: {root}")
    window = manifest.get("window") or {}
    if not window.get("inclusive"):
        raise CleanupError("manifest window must be inclusive")
    if (
        window.get("start_epoch") != AUTHORIZED_START
        or window.get("end_epoch") != AUTHORIZED_END
    ):
        raise CleanupError("manifest time window is unauthorized")
    for database in manifest.get("databases") or []:
        path = Path(str(database.get("path") or "")).resolve()
        if path.name != "memory.db":
            raise CleanupError(f"manifest contains a non-memory.db target: {path}")
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise CleanupError(f"manifest database escapes root: {path}") from exc
        if str(relative) != database.get("relative_path"):
            raise CleanupError(f"manifest relative path drift for {path}")


def _ensure_free_space(path: Path, required: int, purpose: str) -> None:
    free = shutil.disk_usage(path).free
    if free < required:
        raise CleanupError(
            f"insufficient free space for {purpose}: need {required}, have {free}"
        )


def _vacuum_requirement(database: dict[str, Any]) -> int:
    path = Path(database["path"])
    database_bytes = max(
        path.stat().st_size,
        int(database["sqlite"]["logical_database_bytes"]),
    )
    return 2 * database_bytes


def _staging_requirement(database: dict[str, Any]) -> int:
    """Space for one consistent stage plus SQLite's conservative VACUUM peak."""
    path = Path(database["path"])
    database_bytes = max(
        path.stat().st_size,
        int(database["sqlite"]["logical_database_bytes"]),
    )
    return database_bytes + _vacuum_requirement(database)


def _preflight_space(
    databases: list[dict[str, Any]], backup_root: Path
) -> None:
    """Reserve cumulative backups plus peak sequential stage/VACUUM space."""
    representatives: dict[int, Path] = {}
    required: dict[int, int] = {}
    backup_device = backup_root.stat().st_dev
    representatives[backup_device] = backup_root
    required[backup_device] = sum(
        max(
            int(database["sqlite"]["database_size_bytes"]),
            int(database["sqlite"]["logical_database_bytes"]),
        )
        for database in databases
    )
    peak_staging: dict[int, int] = {}
    for database in databases:
        parent = Path(database["path"]).parent
        device = parent.stat().st_dev
        representatives.setdefault(device, parent)
        peak_staging[device] = max(
            peak_staging.get(device, 0), _staging_requirement(database)
        )
    for device, staging_bytes in peak_staging.items():
        required[device] = required.get(device, 0) + staging_bytes
    for device, required_bytes in required.items():
        _ensure_free_space(
            representatives[device],
            required_bytes,
            "cumulative backups and sequential staging/VACUUM workspace",
        )


def _preflight_remaining_staging(databases: list[dict[str, Any]]) -> None:
    peak_by_device: dict[int, tuple[Path, int]] = {}
    for database in databases:
        parent = Path(database["path"]).parent
        device = parent.stat().st_dev
        required = _staging_requirement(database)
        previous = peak_by_device.get(device)
        if previous is None or required > previous[1]:
            peak_by_device[device] = (parent, required)
    for path, required in peak_by_device.values():
        _ensure_free_space(
            path, required, "remaining sequential staging/VACUUM workspace"
        )


def _checkpoint_and_backup(
    database: dict[str, Any], backup_root: Path
) -> dict[str, Any]:
    source_path = Path(database["path"])
    backup_path = backup_root / database["relative_path"]
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise CleanupError(f"backup already exists: {backup_path}")
    source = sqlite3.connect(source_path)
    try:
        checkpoint = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise CleanupError(
                f"WAL checkpoint busy for {source_path}: {tuple(checkpoint)}"
            )
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
            destination.commit()
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise CleanupError(
                    f"backup integrity_check failed for {source_path}: {integrity}"
                )
            if list(destination.execute("PRAGMA foreign_key_check")):
                raise CleanupError(
                    f"backup foreign_key_check failed for {source_path}"
                )
        finally:
            destination.close()
    finally:
        source.close()
    return {
        "path": str(backup_path.resolve()),
        "size_bytes": backup_path.stat().st_size,
        "sha256": _sha256_file(backup_path),
    }


def _verify_protected_state(
    conn: sqlite3.Connection,
    database: dict[str, Any],
    start_epoch: float,
    end_epoch: float,
) -> dict[str, Any]:
    non_target_count, non_target_digest = _table_digest(
        conn,
        "audit_bundles",
        where="created_at < ? OR created_at > ?",
        params=(start_epoch, end_epoch),
    )
    expected_non_target = database["non_target_audit"]
    if {
        "row_count": non_target_count,
        "sha256": non_target_digest,
    } != expected_non_target:
        raise CleanupError(f"{database['path']}: non-target audit data changed")
    current_tables = _non_audit_tables(conn)
    if current_tables != database["non_audit_tables"]:
        raise CleanupError(f"{database['path']}: non-audit table data changed")
    foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    if foreign_keys:
        raise CleanupError(f"{database['path']}: foreign_key_check failed")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise CleanupError(
            f"{database['path']}: integrity_check failed: {integrity}"
        )
    return {
        "non_target_audit": expected_non_target,
        "non_audit_tables": current_tables,
        "foreign_key_check": "ok",
        "integrity_check": "ok",
    }


def _verify_post_delete(
    conn: sqlite3.Connection,
    database: dict[str, Any],
    start_epoch: float,
    end_epoch: float,
) -> dict[str, Any]:
    remaining = conn.execute(
        "SELECT COUNT(*) FROM audit_bundles WHERE created_at BETWEEN ? AND ?",
        (start_epoch, end_epoch),
    ).fetchone()[0]
    if remaining:
        raise CleanupError(
            f"{database['path']}: {remaining} authorized target rows remain"
        )
    return {
        "target_rows_after": 0,
        **_verify_protected_state(
            conn, database, start_epoch, end_epoch
        ),
    }


def _verify_manifest_entry_locked(
    conn: sqlite3.Connection,
    database: dict[str, Any],
    start_epoch: float,
    end_epoch: float,
) -> None:
    current_rows, _ = _target_rows(conn, start_epoch, end_epoch)
    if current_rows != database["rows"]:
        raise CleanupError(f"{database['path']}: manifested target rows drifted")
    _verify_protected_state(conn, database, start_epoch, end_epoch)


def _stage_path(database: dict[str, Any], manifest_sha256: str) -> Path:
    source = Path(database["path"])
    return source.parent / (
        f".{source.name}.audit-cleanup-{manifest_sha256[:16]}.stage"
    )


def _stage_artifacts(stage_path: Path) -> tuple[Path, ...]:
    return (
        stage_path,
        Path(f"{stage_path}-wal"),
        Path(f"{stage_path}-shm"),
        Path(f"{stage_path}-journal"),
    )


def _remove_owned_stage_artifacts(stage_path: Path) -> None:
    for artifact in _stage_artifacts(stage_path):
        if artifact.exists():
            artifact.unlink()


def _copy_database_to_stage(source_path: Path, stage_path: Path) -> None:
    """Create a SQLite-consistent stage from the verified retained backup."""
    if stage_path.exists():
        raise CleanupError(f"staging database already exists: {stage_path}")
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(stage_path)
    try:
        source.backup(destination)
        destination.commit()
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise CleanupError(
                f"staging integrity_check failed for {source_path}: {integrity}"
            )
        if list(destination.execute("PRAGMA foreign_key_check")):
            raise CleanupError(
                f"staging foreign_key_check failed for {source_path}"
            )
    finally:
        destination.close()
        source.close()


def _vacuum_stage(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


def _verify_final_stage(
    conn: sqlite3.Connection,
    database: dict[str, Any],
    start_epoch: float,
    end_epoch: float,
) -> dict[str, Any]:
    return _verify_post_delete(conn, database, start_epoch, end_epoch)


def _prepare_verified_stage(
    stage_path: Path,
    database: dict[str, Any],
    start_epoch: float,
    end_epoch: float,
    *,
    source_journal_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Delete and fully verify only the stage; never mutate the live database."""
    allowed_journal_modes = {
        "delete", "truncate", "persist", "memory", "wal", "off"
    }
    source_journal_mode = source_journal_mode.lower()
    if source_journal_mode not in allowed_journal_modes:
        raise CleanupError(
            f"{database['path']}: unsupported journal mode "
            f"{source_journal_mode!r}"
        )
    ids = [row["bundle_id"] for row in database["rows"]]
    placeholders = ",".join("?" for _ in ids)
    conn = sqlite3.connect(stage_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        journal_mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(journal_mode).lower() != "delete":
            raise CleanupError(
                f"{database['path']}: could not set safe staging journal mode"
            )
        conn.execute("BEGIN IMMEDIATE")
        _verify_manifest_entry_locked(
            conn, database, start_epoch, end_epoch
        )
        cursor = conn.execute(
            f"DELETE FROM audit_bundles WHERE bundle_id IN ({placeholders}) "
            "AND created_at BETWEEN ? AND ?",
            (*ids, start_epoch, end_epoch),
        )
        if cursor.rowcount != database["target_count"]:
            raise CleanupError(
                f"{database['path']}: delete count {cursor.rowcount} != "
                f"{database['target_count']}"
            )
        _verify_post_delete(conn, database, start_epoch, end_epoch)
        conn.commit()
        _vacuum_stage(conn)
        if source_journal_mode != "delete":
            restored = conn.execute(
                f"PRAGMA journal_mode={source_journal_mode}"
            ).fetchone()[0]
            if str(restored).lower() != source_journal_mode:
                raise CleanupError(
                    f"{database['path']}: could not restore journal mode "
                    f"{source_journal_mode}"
                )
        verification = _verify_final_stage(
            conn, database, start_epoch, end_epoch
        )
        after_metrics = _sqlite_metrics(stage_path, conn)
        return verification, after_metrics
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _atomic_replace_verified_stage(stage_path: Path, live_path: Path) -> None:
    """Durably flush the verified stage before same-filesystem replacement."""
    if stage_path.stat().st_dev != live_path.parent.stat().st_dev:
        raise CleanupError(
            f"staging database is not on the live filesystem: {stage_path}"
        )
    shutil.copymode(live_path, stage_path)
    with stage_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(stage_path, live_path)
    directory_fd = os.open(
        live_path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _acquire_cleanup_locks(
    databases: list[dict[str, Any]],
) -> list[MaintenanceLock]:
    locks: list[MaintenanceLock] = []
    try:
        for database in databases:
            try:
                lock = acquire_exclusive_maintenance_lock(database["path"])
            except MaintenanceLockBusy as exc:
                raise CleanupError(
                    f"{database['path']}: store is in use; offline cleanup "
                    "maintenance lock unavailable"
                ) from exc
            locks.append(lock)
    except Exception:
        for lock in reversed(locks):
            lock.release()
        raise
    return locks


def _apply_manifest_under_locks(
    manifest: dict[str, Any],
    *,
    expected_digest: str,
    backup_root: Path,
    authorized_root: Path = AUTHORIZED_ROOT,
) -> dict[str, Any]:
    """Back up every DB, then atomically install fully verified staged cleanup."""
    _check_manifest(manifest, expected_digest, authorized_root)
    _verify_manifest_current(manifest)
    backup_root = backup_root.resolve()
    try:
        backup_root.relative_to(Path(manifest["root"]).resolve())
    except ValueError:
        pass
    else:
        raise CleanupError("backup_root must be outside the cleanup root")
    backup_root.mkdir(parents=True, exist_ok=True)
    _preflight_space(manifest["databases"], backup_root)

    backups: dict[str, dict[str, Any]] = {}
    for database in manifest["databases"]:
        backups[database["path"]] = _checkpoint_and_backup(database, backup_root)

    # No deletion starts until every affected database has a verified backup.
    _verify_manifest_current(manifest)
    _preflight_remaining_staging(manifest["databases"])
    start_epoch = manifest["window"]["start_epoch"]
    end_epoch = manifest["window"]["end_epoch"]
    results = []
    for index, database in enumerate(manifest["databases"]):
        path = Path(database["path"])
        before_size = path.stat().st_size
        stage_path = _stage_path(database, expected_digest)
        collisions = [
            artifact for artifact in _stage_artifacts(stage_path)
            if artifact.exists()
        ]
        if collisions:
            raise CleanupError(
                f"staging artifact already exists: {collisions[0]}"
            )
        live = sqlite3.connect(path)
        live.row_factory = sqlite3.Row
        try:
            live.execute("PRAGMA foreign_keys=ON")
            source_journal_mode = str(
                live.execute("PRAGMA journal_mode").fetchone()[0]
            )
            live.execute("BEGIN EXCLUSIVE")
            _verify_manifest_entry_locked(
                live, database, start_epoch, end_epoch
            )
            _preflight_remaining_staging(manifest["databases"][index:])
            _copy_database_to_stage(
                Path(backups[database["path"]]["path"]),
                stage_path,
            )
            verification, after_metrics = _prepare_verified_stage(
                stage_path,
                database,
                start_epoch,
                end_epoch,
                source_journal_mode=source_journal_mode,
            )
            # The live file remained untouched while staging. Revalidate it
            # under the same write-excluding transaction immediately before
            # the only live mutation: atomic replacement.
            _verify_manifest_entry_locked(
                live, database, start_epoch, end_epoch
            )
            _atomic_replace_verified_stage(stage_path, path)
        except Exception:
            if live.in_transaction:
                live.rollback()
            raise
        finally:
            if live.in_transaction:
                live.rollback()
            live.close()
            _remove_owned_stage_artifacts(stage_path)
        after_size = path.stat().st_size
        results.append({
            "path": str(path),
            "deleted_rows": database["target_count"],
            "backup": backups[database["path"]],
            "before_size_bytes": before_size,
            "after_size_bytes": after_size,
            "reclaimed_bytes": before_size - after_size,
            "after_sqlite": after_metrics,
            **verification,
        })
    return {
        "manifest_sha256": expected_digest,
        "affected_database_count": len(results),
        "deleted_rows": sum(row["deleted_rows"] for row in results),
        "target_rows_after": sum(row["target_rows_after"] for row in results),
        "before_size_bytes": sum(row["before_size_bytes"] for row in results),
        "after_size_bytes": sum(row["after_size_bytes"] for row in results),
        "reclaimed_bytes": sum(row["reclaimed_bytes"] for row in results),
        "databases": results,
    }


def apply_manifest(
    manifest: dict[str, Any],
    *,
    expected_digest: str,
    backup_root: Path,
    authorized_root: Path = AUTHORIZED_ROOT,
) -> dict[str, Any]:
    """Acquire every store offline before any backup or cleanup mutation."""
    _check_manifest(manifest, expected_digest, authorized_root)
    locks = _acquire_cleanup_locks(manifest["databases"])
    try:
        return _apply_manifest_under_locks(
            manifest,
            expected_digest=expected_digest,
            backup_root=backup_root,
            authorized_root=authorized_root,
        )
    finally:
        for lock in reversed(locks):
            lock.release()


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise CleanupError("manifest must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-digest")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if not args.manifest or not args.manifest_digest or not args.backup_root:
                parser.error(
                    "--apply requires --manifest, --manifest-digest, and --backup-root"
                )
            report = apply_manifest(
                _load_manifest(args.manifest),
                expected_digest=args.manifest_digest,
                backup_root=args.backup_root,
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        manifest = build_manifest()
        digest = manifest_digest(manifest)
        if args.output:
            args.output.write_bytes(_canonical_json(manifest) + b"\n")
            print(json.dumps({
                "manifest_path": str(args.output.resolve()),
                "manifest_sha256": digest,
                "affected_database_count": manifest["affected_database_count"],
                "target_row_count": manifest["target_row_count"],
            }, sort_keys=True))
        else:
            print(json.dumps(
                {"manifest": manifest, "manifest_sha256": digest},
                indent=2,
                sort_keys=True,
            ))
        return 0
    except CleanupError as exc:
        print(f"cleanup refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
