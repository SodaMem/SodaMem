/**
 * Wire types for the SodaMem REST API (v1).
 *
 * Mirrors `server/models.py` field-for-field. If you change a field there,
 * change it here too — there is no codegen step, this is a hand-kept mirror.
 */

// --- shared -----------------------------------------------------------------

/** Required user_id + optional agent/run/project narrowing. Every read and
 * write carries at least user_id — there is no unscoped route. */
export interface Scope {
  user_id: string;
  agent_id?: string | null;
  run_id?: string | null;
  /**
   * Repo/workspace this call belongs to. Narrows retrieval to facts stamped
   * with the same project plus every unstamped fact; omit it to search across
   * all projects.
   */
  project_id?: string | null;
}

/** Uniform error envelope returned by non-2xx responses. */
export interface ErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  role?: MessageRole;
  content: string;
}

// --- POST /v1/memories -------------------------------------------------------

export interface AddMemoriesRequest extends Scope {
  messages: Message[];
  /** Groups messages into one conversation. Generated when omitted. */
  session_id?: string | null;
  /** Mention time of the conversation (ISO-8601 or epoch). Defaults to now. */
  session_time?: string | number | null;
  /** False stores raw turns without LLM fact extraction (mem0 parity). Defaults to true. */
  infer?: boolean;
  /** True (default) returns 202 + job_id; false blocks until extraction completes. */
  async_mode?: boolean;
}

/** 202 response for async_mode=true (the default). */
export interface AddMemoriesAccepted {
  job_id: string;
  status: "pending";
  session_id: string;
}

/** 200 response for async_mode=false. */
export interface AddMemoriesResult {
  session_id: string;
  facts_extracted: number;
  spans_written: number;
  turns_written: number;
}

// --- GET /v1/memories, GET/DELETE /v1/memories/{id} --------------------------

export interface Memory {
  id: string;
  user_id: string;
  agent_id?: string | null;
  run_id?: string | null;
  project_id?: string | null;
  /** The fact's human-readable statement. */
  content: string;
  kind?: string | null;
  status?: string | null;
  session_id?: string | null;
  occurred_at?: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
  confidence?: number | null;
  metadata: Record<string, unknown>;
}

export interface MemoryList {
  memories: Memory[];
  total: number;
  offset: number;
  limit: number;
}

export interface DeleteResult {
  id: string;
  deleted: boolean;
  /** The fact was already archived; this call wrote nothing. */
  already_deleted: boolean;
  /**
   * `false` (the default) means the fact was tombstoned and its provenance
   * retained. `true` means it was physically erased.
   */
  purged: boolean;
  /**
   * Rows removed per dependent table (spans, edges, vectors).
   * Only populated when `purged` is true — an archive cascades nothing.
   */
  cascaded: Record<string, number>;
}

/** Options for {@link SodaMemClient.delete}. */
export interface DeleteOptions {
  /**
   * Physically erase the fact and cascade to roles/edges/vectors instead of
   * archiving it. Irreversible, and rejected with 403 unless the server was
   * deployed with `SODAMEM_ALLOW_PURGE=true`.
   */
  purge?: boolean;
}

/** Query params for GET /v1/memories. */
export interface ListMemoriesParams extends Scope {
  offset?: number;
  limit?: number;
}

/** Query params for GET/DELETE /v1/memories/{id}. Scope is required to
 * authorize the lookup even though the id is already unique. */
export interface MemoryIdParams extends Scope {
  id: string;
}

// --- POST /v1/search -----------------------------------------------------------

export interface SearchRequest extends Scope {
  query: string;
  top_k?: number;
}

export interface SearchHit {
  id: string;
  content: string;
  score?: number | null;
  session_id?: string | null;
  occurred_at?: string | null;
  metadata: Record<string, unknown>;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  /** Non-empty when a retrieval route silently fell back (e.g. the vector
   * route was unavailable and only BM25 ran). Never swallowed. */
  degraded: Record<string, unknown>[];
}

// --- GET /v1/context -------------------------------------------------------------

export interface ContextRequest extends Scope {
  query: string;
  token_budget?: number;
}

export interface ContextResponse {
  /** Prompt-ready text — drop straight into a prompt. */
  text: string;
  citations: string[];
  evidence: Record<string, unknown>[];
  degraded: Record<string, unknown>[];
}

// --- jobs ------------------------------------------------------------------------

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export interface Job {
  job_id: string;
  status: JobStatus;
  kind: string;
  user_id: string;
  created_at: string;
  finished_at?: string | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
}

// --- health ------------------------------------------------------------------------

export interface Health {
  status: "ok";
  version: string;
  schema_version: number;
  auth: "enabled" | "disabled";
}
