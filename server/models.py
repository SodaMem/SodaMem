"""API v1 wire contract (PRD R1.1: every route gets a pydantic response_model).

Naming note: these are the PRODUCT's names. The internal `memory.tool.*` /
`memory.browser.*` / CLI triple-vocabulary is an artifact of this project's history
and stops at this boundary — no translation shims are exposed to callers.

Scope keys (R1.2, aligned with mem0 minus app_id): `user_id` is required and
selects the store; `agent_id` / `run_id` / `project_id` are optional narrowing
filters. Every read and write carries at least `user_id` — there is no
unscoped route.

`project_id` (R1.2b) is the repo dimension every coding-agent integration
needs — Claude Code in repo A must not be handed repo B's build quirks. It is
a NARROWING key, not a partition, exactly like the other two: an unstamped
fact still matches, and dropping `project_id` from a query brings every
project's facts back. That is what makes "how did I fix this in the other
repo?" answerable, which a per-project store would have made impossible.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- shared ----------------------------------------------------------------

class Scope(BaseModel):
    """Required user_id + optional agent/run/project narrowing."""
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(
        default=None, max_length=128,
        description=(
            "Repo/workspace this call belongs to. Narrows retrieval to facts "
            "stamped with the same project plus every unstamped fact; omit it "
            "to search across all projects."
        ),
    )


class ErrorBody(BaseModel):
    """Uniform error envelope. `code` is stable and machine-readable; `message`
    is for humans and may change."""
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant", "system"] = "user"
    content: str


# --- POST /v1/memories -----------------------------------------------------

class AddMemoriesRequest(Scope):
    messages: list[Message] = Field(min_length=1)
    session_id: str | None = Field(
        default=None,
        description="Groups messages into one conversation. Generated when omitted.",
    )
    session_time: str | int | float | None = Field(
        default=None,
        description=(
            "Mention time of the conversation (ISO-8601 or epoch). Defaults to "
            "now. Unparseable input RAISES — never silently falls back to "
            "wall-clock, which would date every fact wrong (spec §6.1)."
        ),
    )
    infer: bool = Field(
        default=True,
        description=(
            "False stores the turns verbatim and extracts nothing (mem0 "
            "parity): no facts, no dual timeline, no evidence chain, and no "
            "LLM call or token cost. Such turns are reachable through the "
            "raw-recall retrieval path, not through /v1/memories."
        ),
    )
    async_mode: bool = Field(
        default=True,
        description="True returns 202 + job_id; False blocks until extraction completes.",
    )


class AddMemoriesAccepted(BaseModel):
    """202 response for async_mode=true."""
    job_id: str
    status: Literal["pending"] = "pending"
    session_id: str


class AddMemoriesResult(BaseModel):
    """200 response for async_mode=false."""
    session_id: str
    facts_extracted: int
    spans_written: int
    turns_written: int


# --- GET /v1/memories, GET/DELETE /v1/memories/{id} ------------------------

class Memory(BaseModel):
    id: str
    user_id: str
    agent_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    content: str = Field(description="The fact's human-readable statement.")
    kind: str | None = None
    status: str | None = None
    session_id: str | None = None
    occurred_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryList(BaseModel):
    memories: list[Memory]
    total: int
    offset: int = 0
    limit: int = 50


# --- POST /v1/maintenance/dream --------------------------------------------

class DreamRequest(Scope):
    """Rebuild this user's stale entity profiles (the D36 primitive).

    Nothing auto-dreams: `sodamem.memory.dreaming` deliberately contains no
    scheduler, on the reasoning that the schedule belongs to the deployment
    (cron, a k8s CronJob, a front-end firing when a session ends). That left
    the primitive with no caller at all in a container — reachable only from
    `SodaMem.dream()` in-process, which an operator cannot do to a running
    service that holds the data-root lock. This route is the schedule's hook:
    the operator still owns *when*, the server owns *how*.
    """
    batch_size: int = Field(
        default=50, ge=1, le=1000,
        description="Entities per pass. The call returns remaining_stale, so a "
                    "cron job can simply run again rather than pick a big number.",
    )
    max_processing_time_sec: float = Field(
        default=0.0, ge=0.0, le=3600.0,
        description="Soft deadline; 0 means none. On expiry the run stops "
                    "cleanly with cancelled=true and the dirty bits of "
                    "unprocessed entities intact — the next call resumes.",
    )
    priority_order: Literal["by_staleness", "by_fact_count_delta", "fifo"] = "by_staleness"
    async_mode: bool = Field(
        default=False, alias="async",
        description="Return 202 + a job id instead of blocking. Dreaming a "
                    "large store outlasts most HTTP client timeouts.",
    )


class DreamResult(BaseModel):
    """200 response for async=false; also the job result for async=true.

    `status="already_running"` is a SUCCESS, not an error: the per-user mutex
    is non-blocking by design, so two overlapping cron ticks are a normal
    event, and the second one truthfully reporting that it did nothing is the
    correct outcome — not a 409 that pages someone.
    """
    user_id: str
    status: Literal["ok", "already_running"]
    entities_processed: int = 0
    profiles_written: int = 0
    profiles_superseded: int = 0
    cancelled: bool = False
    remaining_stale: int = 0


class DreamAccepted(BaseModel):
    """202 response for async=true."""
    job_id: str
    status: Literal["pending"] = "pending"


class UsageResponse(BaseModel):
    """Cumulative LLM token usage since process start, split by operation
    (ingest and answer have opposite cost profiles). Empty before any
    LLM-backed traffic — absent, not zero-filled, since zeros read as free."""
    by_operation: dict[str, dict[str, Any]] = Field(default_factory=dict)
    total: dict[str, Any] = Field(default_factory=dict)


class RouteLatency(BaseModel):
    count: int
    min: float
    p50: float
    p95: float
    p99: float
    max: float


class MetricsResponse(BaseModel):
    """Milliseconds, per `METHOD /path`, over this process's most recent
    samples. Untrafficked routes are absent, not zero — see server/metrics.py."""
    routes: dict[str, RouteLatency] = Field(default_factory=dict)


class BatchSession(BaseModel):
    """One conversation in a bulk import. Same shape as an add's body minus
    the scope keys, which are set once for the whole batch."""
    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1)
    session_id: str | None = None
    session_time: str | int | float | None = None


class BatchAddRequest(Scope):
    """Bulk import. The unit is a SESSION: an add already takes many messages,
    but they share one conversation and one timestamp, which is not what
    importing a year of history looks like."""
    sessions: list[BatchSession] = Field(min_length=1, max_length=500)
    async_mode: bool = Field(
        default=True,
        description="True returns 202 + job_id; False blocks until every session is ingested.",
    )


class BatchItemResult(BaseModel):
    index: int = Field(description="Position in the submitted `sessions` array.")
    ok: bool
    session_id: str | None = None
    facts_extracted: int = 0
    error: str | None = Field(
        default=None,
        description="Populated iff ok=false. A failed session is always named, never silently skipped.",
    )


class BatchAddResult(BaseModel):
    """Per-session outcomes. Deliberately not all-or-nothing: one malformed
    session out of thousands must not discard the rest, and must not vanish."""
    succeeded: int
    failed: int
    results: list[BatchItemResult]


class UpdateMemoryRequest(Scope):
    """PATCH body. `content` is the CORRECTED statement in the user's own
    voice, not a diff — extraction runs on it exactly like an add, because the
    replacement is a new memory in every sense except that it also closes an
    old one."""
    content: str = Field(min_length=1)
    session_id: str | None = Field(
        default=None,
        description="Groups the correction with a conversation. Generated when omitted.",
    )
    session_time: str | int | float | None = Field(
        default=None,
        description="Mention time of the correction (ISO-8601 or epoch). Defaults to now.",
    )


class UpdateResult(BaseModel):
    """ADD-only update: `memory` is the NEW version, `superseded_id` is the
    old one — still readable, now closed with `status=superseded` and a
    `valid_until`. Nothing was rewritten in place."""
    memory: "Memory"
    superseded_id: str
    valid_until: str | None = None


class DeleteResult(BaseModel):
    id: str
    deleted: bool
    already_deleted: bool = Field(
        default=False,
        description="The fact was already archived; this call wrote nothing.",
    )
    purged: bool = Field(
        default=False,
        description=(
            "False (the default) means the fact was tombstoned and its "
            "provenance retained. True means it was physically erased."
        ),
    )
    cascaded: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Rows removed per dependent table (spans, edges, vectors). "
            "Only populated when purged=True — an archive cascades nothing."
        ),
    )
    already_deleted: bool = Field(
        default=False,
        description=(
            "True when mode=archive found the memory already archived. "
            "Archiving is idempotent; erasing a missing id is a 404."
        ),
    )


DeleteMode = Literal["erase", "archive"]


# --- POST /v1/search -------------------------------------------------------

class SearchRequest(Scope):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)


class SearchHit(BaseModel):
    id: str
    content: str
    score: float | None = None
    session_id: str | None = None
    occurred_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    degraded: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Non-empty when a retrieval route silently fell back (e.g. the "
            "vector route was unavailable and only BM25 ran). Surfaced rather "
            "than swallowed — zero silent degradation."
        ),
    )


# --- GET /v1/context -------------------------------------------------------

class ContextRequest(Scope):
    query: str = Field(min_length=1)
    token_budget: int = Field(default=2000, ge=100, le=32000)


class ContextResponse(BaseModel):
    """Prompt-ready evidence block. Zero LLM calls on this path."""
    text: str = Field(description="Drop straight into a prompt.")
    citations: list[str]
    evidence: list[dict[str, Any]]
    degraded: list[dict[str, Any]] = Field(default_factory=list)


# --- POST /v1/answer -------------------------------------------------------

class AnswerRequest(Scope):
    """End-to-end question answering over this user's memory (PRD R2.1).

    This is the differentiating endpoint: mem0 and Graphiti both stop at
    search and leave final synthesis to the caller. Costs real LLM calls
    (measured ~7.3 calls / ~34k tokens per question), so it is the one route
    where an operator's own SODAMEM_LLM_* credentials are mandatory.
    """
    question: str = Field(min_length=1)
    current_date: str | None = Field(
        default=None,
        description="Anchor for relative-time reasoning (ISO date). Defaults to today.",
    )
    max_steps: int = Field(default=12, ge=1, le=24)
    planner_max_tokens: int = Field(default=1200, ge=64, le=8192)
    reader_max_tokens: int = Field(default=3000, ge=64, le=16384)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    async_mode: bool = Field(
        default=False,
        description=(
            "False (default) blocks until the answer is ready — a caller asking "
            "a question is waiting for it. True returns 202 + job_id for the "
            "cases where a request timeout is tighter than the ~10-60s this "
            "path can take."
        ),
    )


class AnswerResponse(BaseModel):
    answer: str
    citations: list[str] = Field(
        description="Evidence ids the reader actually saw — derived from the "
                    "rendered context, never the pre-truncation pool."
    )
    termination: str = Field(
        description="How the planner loop ended (planner_final / "
                    "max_steps_reader_fallback / ...). Exposed because 'why did "
                    "it give up?' is unanswerable without it."
    )
    planner_steps: int
    insufficient: bool = Field(
        description="True when the planner finished without enough evidence; "
                    "the answer still comes back, flagged rather than hidden."
    )
    missing_information: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class AnswerAccepted(BaseModel):
    """202 response for async_mode=true."""
    job_id: str
    status: Literal["pending"] = "pending"


# --- jobs ------------------------------------------------------------------

class Job(BaseModel):
    job_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    kind: str
    user_id: str
    created_at: str
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# --- events ----------------------------------------------------------------

class Event(BaseModel):
    """One memory-change record. Answers 'why did the agent forget X?' — the
    category's top complaint (PRD R1.12)."""
    event_id: str
    ts: str
    user_id: str
    agent_id: str | None = None
    run_id: str | None = None
    type: Literal["memory_add", "memory_supersede", "memory_delete"]
    memory_id: str | None = None
    session_id: str | None = None
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EventList(BaseModel):
    events: list[Event]
    total: int
    offset: int = 0
    limit: int = 50


# --- retrieval shapes: GET /v1/entity_timeline, /v1/explore, POST /v1/refine -

class EntityTimelineResponse(BaseModel):
    """Everything the store knows about one entity, oldest first.

    Each `items` entry is an evidence card as `sodamem.tools.MemoryTool`
    renders it (`_fact_to_card`); it is passed through verbatim rather than
    re-projected, because the MCP surface and the HTTP surface must return the
    SAME card or a client that switches transports silently sees different
    fields.
    """
    user_id: str
    entity_id: str
    items: list[dict[str, Any]]
    count: int


class ExploreResponse(BaseModel):
    """Graph walk outward from one memory id."""
    user_id: str
    start_id: str
    start_type: str = "memory"
    count: dict[str, int]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class RefineRequest(Scope):
    """Filtered search. POST, not GET: the filter set is open-ended enough
    that a query string would be the wrong shape for it."""
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    entity: str = ""
    session_id: str = ""
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    occurred_from: str | None = None
    occurred_to: str | None = None


class RefineResponse(BaseModel):
    """`MemoryTool.refine`'s own shape, declared rather than re-projected —
    same reason as EntityTimelineResponse. `filters` echoes back exactly what
    the store applied, which is how a caller tells "no results" apart from "my
    filter was silently dropped"."""
    user_id: str
    query: str
    items: list[dict[str, Any]]
    offset: int = 0
    next_offset: int = 0
    has_more: bool = False
    total_candidates: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)
    available_next_tools: list[Any] = Field(
        default_factory=list,
        description=(
            "What the store suggests reaching for next given these results. "
            "Declared because MemoryTool.refine emits it and the MCP surface "
            "returns it — a response_model that omitted it would have made "
            "the HTTP route quietly return less than the MCP tool, which the "
            "local/remote parity test catches."
        ),
    )
    degraded: list[dict[str, Any]] = Field(default_factory=list)


# --- health ----------------------------------------------------------------

class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    schema_version: int
    auth: Literal["enabled", "disabled"]


# --- ops / control plane (ADR 0001) ----------------------------------------

class ApiKeySummary(BaseModel):
    """A key as the ops view sees it. No field here can reconstruct the
    secret: `prefix` is the first few characters, kept so a human can tell two
    keys apart, and the digest never leaves the database."""
    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None
    revoked: bool = False


class ApiKeyList(BaseModel):
    keys: list[ApiKeySummary]
    total: int


class CreateApiKeyRequest(BaseModel):
    name: str = Field(
        min_length=1, max_length=64,
        description="Human label shown in the ops view and recorded on each "
                    "request this key makes.",
    )


class CreateApiKeyResponse(BaseModel):
    """The ONLY response that ever carries a key's plaintext.

    Keys are stored as digests, so this value cannot be re-derived later. That
    is deliberate — a control database that can hand back working credentials
    is a credential store, and losing one should not be recoverable by reading
    a table.
    """
    key: ApiKeySummary
    api_key: str = Field(description="Shown once. Not recoverable — store it now.")
    warning: str = (
        "This is the only time the plaintext is returned. If lost, revoke this "
        "key and create another."
    )


class RequestLogEntry(BaseModel):
    request_id: str
    method: str
    route: str = Field(description="Route TEMPLATE, never the raw path.")
    status_code: int
    latency_ms: float
    key_name: str | None = Field(
        default=None,
        description="Which key made the call; null for an unauthenticated or "
                    "rejected request.",
    )
    created_at: str


class RequestLogList(BaseModel):
    requests: list[RequestLogEntry]
    total: int
    offset: int = 0
    limit: int = 100


class ConfigView(BaseModel):
    """Effective configuration, with every secret redacted.

    Answers the self-hosting question that used to require shell access:
    "what is this box actually running with?" Secrets are never included —
    not even masked-with-length, which leaks more than it looks like it does.
    """
    data_root: str
    store_cache_max: int
    auth: Literal["enabled", "disabled"]
    api_key_set: bool = Field(description="Whether a bootstrap key is configured.")
    named_keys_active: int
    llm_provider: str
    llm_model: str | None = None
    llm_api_key_set: bool
    llm_base_url: str | None = None
    cors_origins: list[str] = Field(default_factory=list)
    request_log_max: int
    job_retention_max: int
    workers: Literal[1] = Field(
        default=1,
        description="Fixed at 1: a correctness constraint, not a tuning knob "
                    "(ADR 0001 §2 — per-user SQLite without WAL).",
    )
    lock_contention_count: int = Field(
        default=0,
        description=(
            "How many times another process was refused this data root. "
            "Non-zero means more workers or containers are running than the "
            "single-writer constraint allows: the data is safe (only one "
            "writer ever holds the lock) but the extra processes are in a "
            "crash-restart loop, and neither the container status nor /health "
            "can see it. Anything above 0 needs an operator."
        ),
    )
    lock_contention_last: str | None = Field(
        default=None,
        description="When the most recent refusal happened.",
    )


class StoreStat(BaseModel):
    user_id: str
    bytes: int


class StatsView(BaseModel):
    users: int
    stores_bytes: int
    largest_stores: list[StoreStat] = Field(default_factory=list)
    jobs_by_status: dict[str, int] = Field(default_factory=dict)
    requests_logged: int
    control_db_bytes: int
