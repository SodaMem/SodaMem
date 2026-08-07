"""Versioned schema migration ledger.

Ported from the predecessor implementation's `_run_pre_schema_migrations`
(:452-468) and `_run_migrations` (:470-490) — both were the spec §5 "except
Exception: pass" violation: a rename or ADD COLUMN failure (anything other
than "already applied") silently vanished into a log line, and callers had no
way to know their store might be running on a half-migrated schema.

Fixed here two ways:
  1. Existence is checked deterministically via `PRAGMA table_info` before
     attempting a rename/ADD COLUMN, instead of attempting it unconditionally
     and swallowing the "already exists" error. A real failure (locked db,
     corrupt table, ...) now has nothing to hide behind.
  2. Any failure that does occur raises `StoreVersionError` — a store that
     cannot be migrated cleanly must refuse to open, not limp on.

The ledger is keyed `(from_version, to_version) -> [migration callables]`.
Today it holds exactly one step pair: adopting a legacy database written by
the predecessor implementation before `store_meta` existed at all.
`LEGACY_SENTINEL` stands in for "whatever schema state predates store_meta" —
every database the predecessor implementation ever wrote. Future schema bumps (STORE_SCHEMA_VERSION
2, 3, ...) add more `(from, to)` entries here; they do not touch `open_store()`.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from sodamem.errors import ErrorCode, StoreVersionError

LEGACY_SENTINEL = "pre-sodamem"

MigrationFn = Callable[[sqlite3.Connection], None]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _migrate_legacy_column_renames(conn: sqlite3.Connection) -> None:
    """Port of `_run_pre_schema_migrations`: `graph_entities.canonical_name`
    -> `dedup_key` (the field is a dedup key, not a display name). No-op when
    the table doesn't exist yet (fresh store) or the column was already
    renamed (idempotent re-adoption)."""
    cols = _table_columns(conn, "graph_entities")
    if "canonical_name" in cols and "dedup_key" not in cols:
        conn.execute("ALTER TABLE graph_entities RENAME COLUMN canonical_name TO dedup_key")
        conn.commit()


def _migrate_legacy_add_columns(conn: sqlite3.Connection) -> None:
    """Port of `_run_migrations`: additive columns for pre-existing databases
    that predate them (calendar contract §A/§G, DR-004 Summary dependency
    tracking). No-op per column once it already exists."""
    add_columns = [
        ("summary_syntheses", "scope_revision", "INTEGER DEFAULT 0"),
        ("summary_syntheses", "built_from_revision", "INTEGER DEFAULT 0"),
        ("summary_syntheses", "dirty_reason", "TEXT DEFAULT '[]'"),
        ("source_spans", "session_time", "REAL"),
        ("fact_events", "document_time", "REAL"),
    ]
    for table, col, col_type in add_columns:
        existing = _table_columns(conn, table)
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    conn.commit()


# Ordered ledger: every (from_version, to_version) pair maps to the list of
# steps run_migrations() applies, in order, to walk a store from from_version
# to to_version.
MIGRATIONS: dict[tuple[object, object], list[MigrationFn]] = {
    (LEGACY_SENTINEL, 1): [_migrate_legacy_column_renames, _migrate_legacy_add_columns],
}


def run_migrations(conn: sqlite3.Connection, *, from_version: object, to_version: object) -> None:
    """Apply every ledger step registered for (from_version, to_version), in
    order. Raises StoreVersionError — never a silent pass — on a missing
    migration path or a step that fails (spec §6.7 / I6)."""
    steps = MIGRATIONS.get((from_version, to_version))
    if not steps:
        raise StoreVersionError(
            f"no migration path from {from_version!r} to {to_version!r}",
            code=ErrorCode.STORE_INCOMPATIBLE,
            details={"from_version": from_version, "to_version": to_version},
        )
    for step in steps:
        try:
            step(conn)
        except StoreVersionError:
            raise
        except Exception as e:
            raise StoreVersionError(
                f"migration step {step.__name__} failed: {e}",
                code=ErrorCode.STORE_INCOMPATIBLE,
                details={"from_version": from_version, "to_version": to_version, "step": step.__name__},
            ) from e
