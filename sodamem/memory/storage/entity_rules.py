"""The DR-004 #6 explicit Summary dependency index.

`Store` holds an `EntityRulesStore` via **composition, not inheritance** —
`store.entity_rules.summaries_depending_on(...)` rather than
`store.summaries_depending_on(...)`.

This module used to carry 21 methods: the DR-011 EntityRules relation / role
/ projection / alias / trace CRUD, plus this dependency index. Its own
docstring said "there is no existing call site for any of these methods" —
and on 0806 that was still true of 19 of the 21, so they were deleted.

**The nine DR-011 tables stay in `schema.py`.** Dropping them would mean
bumping STORE_SCHEMA_VERSION, and `assert_store_compatible` would then refuse
to open every store that already exists. An unread table costs nothing; a
forced migration to remove it costs every user. If the relation/alias work is
ever picked up, the tables are waiting and the methods are in git.
"""
from __future__ import annotations

import sqlite3
import threading


class EntityRulesStore:
    """Shares the parent Store's connection + lock (single SQLite writer,
    same serialization discipline as every other Store method) rather than
    owning its own — this is a facet of one store, not a second store."""

    _SCOPE_PRIORITY = ("user", "tenant", "global", "builtin")

    def __init__(self, *, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    # ---- DR-004 #6: explicit Summary dependency index -----------------

    def replace_summary_dependencies(self, summary_id: str, deps: list[tuple[str, str]]) -> None:
        """Rebuild a Summary's dependencies (call after a refresh; both the
        before and after dependency sets remain reverse-lookupable)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM summary_dependencies WHERE summary_id=?", (summary_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO summary_dependencies VALUES (?,?,?)",
                [(summary_id, dtype, did) for dtype, did in deps if did],
            )
            self._conn.commit()

    def summaries_depending_on(self, dependency_type: str, dependency_id: str) -> list[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT summary_id FROM summary_dependencies "
                "WHERE dependency_type=? AND dependency_id=?",
                (dependency_type, dependency_id),
            )
            return [r[0] for r in cur.fetchall()]

