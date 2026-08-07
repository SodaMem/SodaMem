"""SQLite schema DDL + the I6 store-version metadata contract.

`store_meta` is a key/value table carrying `schema_version`, `prompt_fingerprint`,
and `created_at`. `open_store()` (in store.py) reads it on every open of an
existing store and refuses to start on any mismatch — no silent ALTER, ever
(spec I6).

The remaining 20 `CREATE TABLE` statements (raw_turns, source_spans,
fact_events, fact_entity_roles, extraction_traces, source_entity_mentions,
fact_edges, graph_entities, entity_profile_stale, summary_syntheses,
audit_bundles, and the 9 DR-011 EntityRules tables) are ported verbatim from
the predecessor implementation — same table/column/index names and
order, including the DR-011 relation/role/alias/projection/trace tables.
"""
from __future__ import annotations

from sodamem.versioning import STORE_SCHEMA_VERSION

STORE_META_TABLE = "store_meta"


def store_meta_schema() -> dict[str, object]:
    """Declares the store_meta contract the I6 gate checks against.

    `schema_version` is the actual current version value (an int, not a type
    name) — the Phase 0 gate scaffold (`tests/gates/test_i6_store_versioned.py
    ::test_store_exposes_version_metadata`) asserts
    `store_meta_schema()["schema_version"] == STORE_SCHEMA_VERSION` by value,
    so this function is the single place that assertion can ever drift from
    `sodamem.versioning.STORE_SCHEMA_VERSION` — it re-exports the same
    constant rather than duplicating the number. `prompt_fingerprint` and
    `created_at` are declared by their SQLite storage type (both TEXT
    columns in `store_meta`) since — unlike schema_version — there is no
    single "current value" for them to declare.
    """
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "prompt_fingerprint": "TEXT",
        "created_at": "TEXT",
    }


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Graph-based v2 raw evidence and structured facts
CREATE TABLE IF NOT EXISTS raw_turns (
    turn_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_turns_user_session
    ON raw_turns(user_id, session_id, timestamp);

-- R1.2b: the scope an ingest ran under, recorded ONCE per session.
--
-- Scope was previously stamped only into each extracted fact's metadata,
-- which left raw turns unscoped by construction — and a raw turn is the bulk
-- of what a coding-agent session stores. The consequence was that narrowing
-- to one repo still surfaced another repo's raw conversation text. Keying on
-- the session instead of the row fixes that for facts and raw turns at once,
-- because both carry `source_session_id` on their evidence card.
--
-- A session with no row here is unscoped, exactly as before this table
-- existed: every store written before it keeps behaving the way it did.
CREATE TABLE IF NOT EXISTS session_scope (
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL DEFAULT '',
    run_id     TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, session_id)
);

CREATE TABLE IF NOT EXISTS source_spans (
    span_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL,
    char_start INTEGER DEFAULT 0,
    char_end INTEGER DEFAULT 0,
    text TEXT NOT NULL,
    text_hash TEXT DEFAULT '',
    span_type TEXT DEFAULT 'turn_chunk',
    extractor_version TEXT DEFAULT '',
    alignment_method TEXT DEFAULT '',
    alignment_confidence REAL DEFAULT 1.0,
    created_at REAL NOT NULL,
    session_time REAL
);

CREATE INDEX IF NOT EXISTS idx_source_spans_user
    ON source_spans(user_id, session_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_source_spans_hash
    ON source_spans(user_id, text_hash);

CREATE TABLE IF NOT EXISTS fact_events (
    fact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    subject_entity_id TEXT DEFAULT 'entity_user',
    predicate_raw TEXT DEFAULT '',
    predicate_canonical TEXT DEFAULT '',
    event_type TEXT DEFAULT '',
    object_entity_ids TEXT DEFAULT '[]',
    modality TEXT DEFAULT 'past_event',
    polarity TEXT DEFAULT 'positive',
    occurred_start REAL,
    occurred_end REAL,
    valid_from REAL,
    valid_until REAL,
    document_time REAL,
    quantity_value REAL,
    quantity_unit TEXT DEFAULT '',
    source_type TEXT DEFAULT 'explicit_text',
    source_span_ids TEXT DEFAULT '[]',
    provenance TEXT DEFAULT '{}',
    confidence REAL DEFAULT 0.75,
    confidence_reason TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_events_user_status
    ON fact_events(user_id, status);
CREATE INDEX IF NOT EXISTS idx_fact_events_type_time
    ON fact_events(user_id, kind, event_type, modality, occurred_start, occurred_end);
CREATE INDEX IF NOT EXISTS idx_fact_events_predicate
    ON fact_events(user_id, predicate_canonical);

CREATE TABLE IF NOT EXISTS fact_entity_roles (
    fact_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_name TEXT DEFAULT '',
    PRIMARY KEY (fact_id, role, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_roles_lookup
    ON fact_entity_roles(user_id, role, entity_id);

CREATE TABLE IF NOT EXISTS extraction_traces (
    trace_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    turn_id TEXT DEFAULT '',
    span_id TEXT DEFAULT '',
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT DEFAULT '',
    input_hash TEXT DEFAULT '',
    output_fact_ids TEXT DEFAULT '[]',
    error TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    retain_until REAL
);

CREATE INDEX IF NOT EXISTS idx_extraction_traces_user_span
    ON extraction_traces(user_id, span_id, created_at);
CREATE INDEX IF NOT EXISTS idx_extraction_traces_stage
    ON extraction_traces(user_id, stage, status, created_at);

CREATE TABLE IF NOT EXISTS source_entity_mentions (
    mention_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_name TEXT DEFAULT '',
    source_span_id TEXT NOT NULL,
    fact_id TEXT DEFAULT '',
    role TEXT DEFAULT '',
    link_confidence REAL DEFAULT 0.85,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_mentions_entity
    ON source_entity_mentions(user_id, entity_id, source_span_id);
CREATE INDEX IF NOT EXISTS idx_source_mentions_name
    ON source_entity_mentions(user_id, entity_name);
CREATE INDEX IF NOT EXISTS idx_source_mentions_fact
    ON source_entity_mentions(user_id, fact_id);

CREATE TABLE IF NOT EXISTS fact_edges (
    edge_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    src_fact_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    predicate_raw TEXT DEFAULT '',
    predicate_canonical TEXT DEFAULT '',
    confidence REAL DEFAULT 0.85,
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_edges_unique
    ON fact_edges(user_id, src_fact_id, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_fact_edges_lookup
    ON fact_edges(user_id, edge_type, src_fact_id);
-- D37: "what facts have this attribute/target?" (reverse traversal)
CREATE INDEX IF NOT EXISTS idx_fact_edges_target
    ON fact_edges(user_id, edge_type, dst_id);

CREATE TABLE IF NOT EXISTS graph_entities (
    entity_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    dedup_key TEXT DEFAULT '',
    entity_type TEXT DEFAULT 'concept',
    surface_forms TEXT DEFAULT '[]',
    mention_count INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_entities_type
    ON graph_entities(user_id, entity_type);

CREATE TABLE IF NOT EXISTS entity_profile_stale (
    user_id           TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    first_marked_at   REAL NOT NULL,
    last_marked_at    REAL NOT NULL,
    new_fact_count    INTEGER DEFAULT 0,
    last_session_id   TEXT,
    PRIMARY KEY (user_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_stale_by_user
    ON entity_profile_stale(user_id, last_marked_at);

CREATE TABLE IF NOT EXISTS summary_syntheses (
    summary_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    source_fact_ids TEXT DEFAULT '[]',
    source_span_ids TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    dirty INTEGER DEFAULT 0,
    scope_revision INTEGER DEFAULT 0,
    built_from_revision INTEGER DEFAULT 0,
    dirty_reason TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_scope
    ON summary_syntheses(user_id, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_summary_status
    ON summary_syntheses(user_id, status, dirty);

CREATE TABLE IF NOT EXISTS audit_bundles (
    bundle_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    query TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- DR-011: EntityRules（关系/角色/投影/Alias/观察/解析审计）。
-- 实体身份本身复用既有 graph_entities 表，这里只治理关系语义层。
CREATE TABLE IF NOT EXISTS relation_definitions (
    relation_key TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'builtin',
    display_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    required_role_keys TEXT DEFAULT '[]',
    optional_role_keys TEXT DEFAULT '[]',
    allowed_modalities TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (relation_key, scope)
);

CREATE TABLE IF NOT EXISTS relation_aliases (
    alias_text TEXT NOT NULL,
    target_relation_key TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'builtin',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (alias_text, scope)
);

CREATE TABLE IF NOT EXISTS conditional_relation_aliases (
    cond_id TEXT PRIMARY KEY,
    alias_text TEXT NOT NULL,
    candidate_relation_key TEXT NOT NULL,
    condition_json TEXT DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT 'builtin',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS participant_role_definitions (
    role_key TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'builtin',
    display_name TEXT DEFAULT '',
    expected_entity_types TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (role_key, scope)
);

CREATE TABLE IF NOT EXISTS semantic_projection_rules (
    rule_id TEXT PRIMARY KEY,
    relation_key TEXT NOT NULL,
    src_role_key TEXT NOT NULL,
    dst_role_key TEXT NOT NULL,
    edge_relation_key TEXT NOT NULL,
    allow_reverse INTEGER DEFAULT 0,
    allow_multi_hop INTEGER DEFAULT 1,
    scope TEXT NOT NULL DEFAULT 'builtin',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projection_by_relation
    ON semantic_projection_rules(relation_key, status);

CREATE TABLE IF NOT EXISTS alias_observations (
    observation_id TEXT PRIMARY KEY,
    user_id TEXT DEFAULT '',
    alias_type TEXT NOT NULL,
    alias_text TEXT NOT NULL,
    proposed_target_key TEXT DEFAULT '',
    fact_id TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    source_span_id TEXT DEFAULT '',
    model_decision TEXT DEFAULT '',
    candidate_relation_keys TEXT DEFAULT '[]',
    structure_validation TEXT DEFAULT '{}',
    conflict_reason TEXT DEFAULT '',
    rules_version INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alias_obs_lookup
    ON alias_observations(alias_type, alias_text, proposed_target_key);

CREATE TABLE IF NOT EXISTS semantic_resolution_traces (
    trace_id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL,
    resolution_type TEXT NOT NULL,
    raw_value TEXT DEFAULT '',
    resolved_key TEXT DEFAULT '',
    decision TEXT DEFAULT '',
    registry_candidates TEXT DEFAULT '[]',
    model_name TEXT DEFAULT '',
    prompt_version TEXT DEFAULT '',
    rules_version INTEGER DEFAULT 1,
    supporting_text TEXT DEFAULT '',
    validation_result TEXT DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resolution_by_fact
    ON semantic_resolution_traces(fact_id);

-- DR-007/011: participant role Alias（与 relation Alias 同机制）。
-- relation_scope_key='' 表示全局 role alias；非空表示仅在该 relation 内生效。
CREATE TABLE IF NOT EXISTS participant_role_aliases (
    alias_text TEXT NOT NULL,
    target_role_key TEXT NOT NULL,
    relation_scope_key TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'builtin',
    status TEXT DEFAULT 'active',
    version INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (alias_text, relation_scope_key, scope)
);

-- DR-004 #6: 显式 Summary 依赖索引（fact / source_span / session / entity / topic）。
CREATE TABLE IF NOT EXISTS summary_dependencies (
    summary_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    PRIMARY KEY (summary_id, dependency_type, dependency_id)
);
CREATE INDEX IF NOT EXISTS idx_summary_dep_lookup
    ON summary_dependencies(dependency_type, dependency_id);
"""
