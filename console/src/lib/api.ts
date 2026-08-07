import type {
  ApiKeyList,
  ApiKeySummary,
  ConfigView,
  ContextResponse,
  CreateApiKeyResponse,
  DeleteResult,
  ErrorBody,
  Health,
  Job,
  Memory,
  MemoryList,
  RequestLogList,
  SearchResponse,
  StatsView,
} from './types'

/**
 * Thrown for every failure mode: non-2xx HTTP responses (parsed into the
 * server's ErrorBody envelope) AND network-level failures (fetch throwing,
 * DNS, CORS, offline). Callers always get `.code` + `.message` to render —
 * zero silent failures, per project policy.
 */
export class ApiError extends Error {
  code: string
  status: number
  details: Record<string, unknown>

  constructor(status: number, body: ErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details
  }
}

export interface ApiConfig {
  baseUrl: string
  apiKey: string
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    usp.set(k, String(v))
  }
  const s = usp.toString()
  return s ? `?${s}` : ''
}

async function request<T>(cfg: ApiConfig, path: string, init?: RequestInit): Promise<T> {
  const url = `${cfg.baseUrl}${path}`
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
  }
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`

  let res: Response
  try {
    res = await fetch(url, { ...init, headers: { ...headers, ...init?.headers } })
  } catch (err) {
    throw new ApiError(0, {
      code: 'network_error',
      message:
        err instanceof Error
          ? `Could not reach ${url}: ${err.message}`
          : `Could not reach ${url}`,
      details: {},
    })
  }

  if (!res.ok) {
    let body: ErrorBody
    try {
      const parsed = (await res.json()) as Partial<ErrorBody>
      if (typeof parsed?.code !== 'string' || typeof parsed?.message !== 'string') {
        throw new Error('response body is not an ErrorBody')
      }
      body = { code: parsed.code, message: parsed.message, details: parsed.details ?? {} }
    } catch {
      body = {
        code: `http_${res.status}`,
        message: res.statusText || `Request failed with status ${res.status}`,
        details: {},
      }
    }
    throw new ApiError(res.status, body)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  health: (cfg: ApiConfig) => request<Health>(cfg, '/health'),

  listMemories: (
    cfg: ApiConfig,
    params: {
      user_id: string
      agent_id?: string
      run_id?: string
      offset?: number
      limit?: number
    },
  ) => request<MemoryList>(cfg, `/v1/memories${qs(params)}`),

  getMemory: (cfg: ApiConfig, id: string, user_id: string) =>
    request<Memory>(cfg, `/v1/memories/${encodeURIComponent(id)}${qs({ user_id })}`),

  deleteMemory: (cfg: ApiConfig, id: string, user_id: string) =>
    request<DeleteResult>(cfg, `/v1/memories/${encodeURIComponent(id)}${qs({ user_id })}`, {
      method: 'DELETE',
    }),

  search: (
    cfg: ApiConfig,
    body: { user_id: string; agent_id?: string; run_id?: string; query: string; top_k: number },
  ) =>
    request<SearchResponse>(cfg, '/v1/search', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getContext: (
    cfg: ApiConfig,
    params: {
      user_id: string
      agent_id?: string
      run_id?: string
      query: string
      token_budget: number
    },
  ) => request<ContextResponse>(cfg, `/v1/context${qs(params)}`),

  getJob: (cfg: ApiConfig, jobId: string) =>
    request<Job>(cfg, `/v1/jobs/${encodeURIComponent(jobId)}`),

  // --- ops / control plane -------------------------------------------------

  getAdminConfig: (cfg: ApiConfig) => request<ConfigView>(cfg, '/v1/admin/config'),

  getStats: (cfg: ApiConfig) => request<StatsView>(cfg, '/v1/admin/stats'),

  listApiKeys: (cfg: ApiConfig) => request<ApiKeyList>(cfg, '/v1/admin/keys'),

  createApiKey: (cfg: ApiConfig, name: string) =>
    request<CreateApiKeyResponse>(cfg, '/v1/admin/keys', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  revokeApiKey: (cfg: ApiConfig, keyId: string) =>
    request<ApiKeySummary>(cfg, `/v1/admin/keys/${encodeURIComponent(keyId)}`, {
      method: 'DELETE',
    }),

  listRequests: (cfg: ApiConfig, params: { limit?: number; offset?: number } = {}) =>
    request<RequestLogList>(cfg, `/v1/admin/requests${qs(params)}`),
}
