"""DR-011 EntityRules: relation / role / projection / alias / trace CRUD,
plus the DR-004 #6 explicit Summary dependency index (both ported from the
same the predecessor implementation span — the source groups
them together, so this port preserves that grouping rather than splitting it
further).

Split out of `store.py` (R10): porting this ~265-line span into `Store`
directly would have pushed `store.py` past ~1500 lines. `Store` holds an
`EntityRulesStore` instance via **composition, not inheritance** (no mixin
base class) — `store.entity_rules.upsert_relation_definition(...)` rather
than `store.upsert_relation_definition(...)`. There is no existing call site
for any of these methods to stay compatible with — DR-011 is unused
infrastructure — so composition was free to pick the shape that avoids ~20
forwarding stubs on `Store`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time


class EntityRulesStore:
    """Shares the parent Store's connection + lock (single SQLite writer,
    same serialization discipline as every other Store method) rather than
    owning its own — this is a facet of one store, not a second store."""

    _SCOPE_PRIORITY = ("user", "tenant", "global", "builtin")

    def __init__(self, *, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def upsert_relation_definition(self, d: dict) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO relation_definitions
                    (relation_key, scope, display_name, description, required_role_keys,
                     optional_role_keys, allowed_modalities, status, version, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(relation_key, scope) DO UPDATE SET
                    display_name=excluded.display_name, description=excluded.description,
                    required_role_keys=excluded.required_role_keys,
                    optional_role_keys=excluded.optional_role_keys,
                    allowed_modalities=excluded.allowed_modalities,
                    status=excluded.status, version=excluded.version, updated_at=excluded.updated_at
                """,
                (d["relation_key"], d.get("scope", "builtin"), d.get("display_name", ""),
                 d.get("description", ""), json.dumps(d.get("required_role_keys", [])),
                 json.dumps(d.get("optional_role_keys", [])), json.dumps(d.get("allowed_modalities", [])),
                 d.get("status", "active"), int(d.get("version", 1)), now, now),
            )
            self._conn.commit()

    def get_relation_definition(self, relation_key: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM relation_definitions WHERE relation_key=? AND status='active'",
                (relation_key,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return None
        rows.sort(key=lambda r: self._SCOPE_PRIORITY.index(r["scope"]) if r["scope"] in self._SCOPE_PRIORITY else 99)
        return rows[0]

    def list_relation_definitions(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM relation_definitions WHERE status='active'")
            return [dict(r) for r in cur.fetchall()]

    def upsert_relation_alias(self, alias_text: str, target_relation_key: str,
                              scope: str = "global", status: str = "active") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO relation_aliases (alias_text, target_relation_key, scope, status, version, created_at)
                   VALUES (?,?,?,?,1,?)
                   ON CONFLICT(alias_text, scope) DO UPDATE SET
                     target_relation_key=excluded.target_relation_key, status=excluded.status""",
                (alias_text.strip().lower(), target_relation_key, scope, status, time.time()),
            )
            self._conn.commit()

    def resolve_relation_alias(self, alias_text: str) -> dict | None:
        """Exact-match an active, unconditional Alias (by scope priority)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM relation_aliases WHERE alias_text=? AND status='active'",
                (alias_text.strip().lower(),),
            )
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return None
        rows.sort(key=lambda r: self._SCOPE_PRIORITY.index(r["scope"]) if r["scope"] in self._SCOPE_PRIORITY else 99)
        return rows[0]

    def list_conditional_relation_aliases(self, alias_text: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM conditional_relation_aliases WHERE alias_text=? AND status='active'",
                (alias_text.strip().lower(),),
            )
            return [dict(r) for r in cur.fetchall()]

    def upsert_projection_rule(self, d: dict) -> None:
        rid = d.get("rule_id") or f"proj_{d['relation_key']}_{d['src_role_key']}_{d['dst_role_key']}"
        with self._lock:
            self._conn.execute(
                """INSERT INTO semantic_projection_rules
                     (rule_id, relation_key, src_role_key, dst_role_key, edge_relation_key,
                      allow_reverse, allow_multi_hop, scope, status, version, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(rule_id) DO UPDATE SET
                     edge_relation_key=excluded.edge_relation_key, allow_reverse=excluded.allow_reverse,
                     allow_multi_hop=excluded.allow_multi_hop, status=excluded.status""",
                (rid, d["relation_key"], d["src_role_key"], d["dst_role_key"], d["edge_relation_key"],
                 int(d.get("allow_reverse", 0)), int(d.get("allow_multi_hop", 1)),
                 d.get("scope", "builtin"), d.get("status", "active"), int(d.get("version", 1)), time.time()),
            )
            self._conn.commit()

    def get_projection_rules(self, relation_key: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM semantic_projection_rules WHERE relation_key=? AND status='active'",
                (relation_key,),
            )
            return [dict(r) for r in cur.fetchall()]

    def upsert_role_definition(self, d: dict) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO participant_role_definitions
                     (role_key, scope, display_name, expected_entity_types, status, version, created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(role_key, scope) DO UPDATE SET
                     display_name=excluded.display_name,
                     expected_entity_types=excluded.expected_entity_types, status=excluded.status""",
                (d["role_key"], d.get("scope", "builtin"), d.get("display_name", ""),
                 json.dumps(d.get("expected_entity_types", [])), d.get("status", "active"),
                 int(d.get("version", 1)), now),
            )
            self._conn.commit()

    def get_role_definition(self, role_key: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM participant_role_definitions WHERE role_key=? AND status='active'",
                (role_key,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def insert_alias_observation(self, d: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO alias_observations
                     (observation_id, user_id, alias_type, alias_text, proposed_target_key, fact_id,
                      session_id, source_span_id, model_decision, candidate_relation_keys,
                      structure_validation, conflict_reason, rules_version, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["observation_id"], d.get("user_id", ""), d["alias_type"], d["alias_text"],
                 d.get("proposed_target_key", ""), d.get("fact_id", ""), d.get("session_id", ""),
                 d.get("source_span_id", ""), d.get("model_decision", ""),
                 json.dumps(d.get("candidate_relation_keys", [])),
                 json.dumps(d.get("structure_validation", {})), d.get("conflict_reason", ""),
                 int(d.get("rules_version", 1)), time.time()),
            )
            self._conn.commit()

    def get_alias_observations(self, alias_type: str, alias_text: str, target_key: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM alias_observations WHERE alias_type=? AND alias_text=? AND proposed_target_key=?",
                (alias_type, alias_text.strip().lower(), target_key),
            )
            return [dict(r) for r in cur.fetchall()]

    def upsert_conditional_relation_alias(self, d: dict) -> None:
        """DR-011 conditional Alias (only boosts candidate ranking, never resolves directly)."""
        cid = d.get("cond_id") or f"cond_{d['alias_text']}_{d['candidate_relation_key']}"
        with self._lock:
            self._conn.execute(
                """INSERT INTO conditional_relation_aliases
                     (cond_id, alias_text, candidate_relation_key, condition_json,
                      scope, status, version, created_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(cond_id) DO UPDATE SET
                     condition_json=excluded.condition_json, status=excluded.status""",
                (cid, d["alias_text"].strip().lower(), d["candidate_relation_key"],
                 json.dumps(d.get("condition", {})), d.get("scope", "global"),
                 d.get("status", "proposed"), int(d.get("version", 1)), time.time()),
            )
            self._conn.commit()

    def upsert_role_alias(self, alias_text: str, target_role_key: str,
                          relation_scope_key: str = "", scope: str = "global",
                          status: str = "active") -> None:
        """DR-007: participant role Alias (non-empty relation_scope_key = relation-scoped)."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO participant_role_aliases
                     (alias_text, target_role_key, relation_scope_key, scope, status, version, created_at)
                   VALUES (?,?,?,?,?,1,?)
                   ON CONFLICT(alias_text, relation_scope_key, scope) DO UPDATE SET
                     target_role_key=excluded.target_role_key, status=excluded.status""",
                (alias_text.strip().lower(), target_role_key, relation_scope_key,
                 scope, status, time.time()),
            )
            self._conn.commit()

    def resolve_role_alias(self, alias_text: str, relation_key: str = "") -> dict | None:
        """Exact-match an active role Alias: relation-scoped wins over global,
        then by scope priority."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM participant_role_aliases
                   WHERE alias_text=? AND status='active' AND relation_scope_key IN (?, '')""",
                (alias_text.strip().lower(), relation_key),
            )
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return None
        rows.sort(key=lambda r: (
            0 if r["relation_scope_key"] else 1,
            self._SCOPE_PRIORITY.index(r["scope"]) if r["scope"] in self._SCOPE_PRIORITY else 99,
        ))
        return rows[0]

    def set_alias_status(self, alias_type: str, alias_text: str, scope: str, status: str) -> None:
        """DR-011 rollback: retire/reactivate an Alias (relation or participant_role)."""
        table = ("participant_role_aliases" if alias_type == "participant_role"
                 else "relation_aliases")
        with self._lock:
            self._conn.execute(
                f"UPDATE {table} SET status=?, version=version+1 WHERE alias_text=? AND scope=?",
                (status, alias_text.strip().lower(), scope),
            )
            self._conn.commit()

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

    def get_summary_dependencies(self, summary_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM summary_dependencies WHERE summary_id=?", (summary_id,))
            return [dict(r) for r in cur.fetchall()]

    def summaries_depending_on(self, dependency_type: str, dependency_id: str) -> list[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT summary_id FROM summary_dependencies "
                "WHERE dependency_type=? AND dependency_id=?",
                (dependency_type, dependency_id),
            )
            return [r[0] for r in cur.fetchall()]

    def insert_resolution_trace(self, d: dict) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO semantic_resolution_traces
                     (trace_id, fact_id, resolution_type, raw_value, resolved_key, decision,
                      registry_candidates, model_name, prompt_version, rules_version,
                      supporting_text, validation_result, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["trace_id"], d["fact_id"], d["resolution_type"], d.get("raw_value", ""),
                 d.get("resolved_key", ""), d.get("decision", ""),
                 json.dumps(d.get("registry_candidates", [])), d.get("model_name", ""),
                 d.get("prompt_version", ""), int(d.get("rules_version", 1)),
                 d.get("supporting_text", ""), json.dumps(d.get("validation_result", {})), time.time()),
            )
            self._conn.commit()

    def get_resolution_traces(self, fact_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM semantic_resolution_traces WHERE fact_id=?", (fact_id,))
            return [dict(r) for r in cur.fetchall()]
