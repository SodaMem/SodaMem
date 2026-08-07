/**
 * TypeScript mirror of the wire contract in `server/models.py`. Keep these in
 * lockstep with that file by hand — there is no codegen step for this
 * console, so a field added/renamed on the server must be updated here too.
 */

export interface ErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface Health {
  status: 'ok'
  version: string
  schema_version: number
  auth: 'enabled' | 'disabled'
}

// --- memories ----------------------------------------------------------

export interface Memory {
  id: string
  user_id: string
  agent_id?: string | null
  run_id?: string | null
  content: string
  kind?: string | null
  status?: string | null
  session_id?: string | null
  occurred_at?: string | null
  valid_from?: string | null
  valid_until?: string | null
  confidence?: number | null
  metadata: Record<string, unknown>
}

export interface MemoryList {
  memories: Memory[]
  total: number
  offset: number
  limit: number
}

export interface DeleteResult {
  id: string
  deleted: boolean
  already_deleted: boolean
  /** Always false from the console — it never sends ?purge=true. */
  purged: boolean
  cascaded: Record<string, number>
}

// --- search --------------------------------------------------------------

export interface SearchHit {
  id: string
  content: string
  score?: number | null
  session_id?: string | null
  occurred_at?: string | null
  metadata: Record<string, unknown>
}

export interface SearchResponse {
  query: string
  hits: SearchHit[]
  /** Non-empty when a retrieval route silently fell back server-side. Must
   * always be surfaced in the UI, never swallowed. */
  degraded: Record<string, unknown>[]
}

// --- context ---------------------------------------------------------------

export interface ContextResponse {
  /** Prompt-ready evidence block. Zero LLM calls on this path. */
  text: string
  citations: string[]
  evidence: Record<string, unknown>[]
  degraded: Record<string, unknown>[]
}

// --- jobs --------------------------------------------------------------

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface Job {
  job_id: string
  status: JobStatus
  kind: string
  user_id: string
  created_at: string
  finished_at?: string | null
  result?: Record<string, unknown> | null
  error?: string | null
}

// --- ops / control plane ---------------------------------------------------

export interface ApiKeySummary {
  id: string
  name: string
  /** First few characters only. Enough to tell two keys apart, never enough
   * to replay one — the server stores a digest and cannot return the rest. */
  prefix: string
  created_at: string
  last_used_at?: string | null
  revoked_at?: string | null
  revoked: boolean
}

export interface ApiKeyList {
  keys: ApiKeySummary[]
  total: number
}

export interface CreateApiKeyResponse {
  key: ApiKeySummary
  /** Shown once, never recoverable. Deliberately not persisted anywhere in
   * this client — see the ops page's comment on localStorage. */
  api_key: string
  warning: string
}

export interface RequestLogEntry {
  request_id: string
  method: string
  /** Route TEMPLATE (`/v1/memories/{memory_id}`), never a raw path. */
  route: string
  status_code: number
  latency_ms: number
  key_name?: string | null
  created_at: string
}

export interface RequestLogList {
  requests: RequestLogEntry[]
  total: number
  offset: number
  limit: number
}

export interface ConfigView {
  data_root: string
  store_cache_max: number
  auth: 'enabled' | 'disabled'
  api_key_set: boolean
  named_keys_active: number
  llm_provider: string
  llm_model?: string | null
  llm_api_key_set: boolean
  llm_base_url?: string | null
  cors_origins: string[]
  request_log_max: number
  job_retention_max: number
  workers: 1
  /** Non-zero means more processes are running than the single-writer
   * constraint allows. The data is safe, but the extra workers are in a
   * crash-restart loop that neither the container status nor /health shows. */
  lock_contention_count: number
  lock_contention_last?: string | null
}

export interface StoreStat {
  user_id: string
  bytes: number
}

export interface StatsView {
  users: number
  stores_bytes: number
  largest_stores: StoreStat[]
  jobs_by_status: Record<string, number>
  requests_logged: number
  control_db_bytes: number
}
