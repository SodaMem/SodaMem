import { SodaMemApiError, SodaMemTimeoutError } from "./errors.js";
import type {
  AddMemoriesAccepted,
  AddMemoriesRequest,
  AddMemoriesResult,
  ContextRequest,
  ContextResponse,
  DeleteOptions,
  DeleteResult,
  ErrorBody,
  Health,
  Job,
  JobStatus,
  ListMemoriesParams,
  Memory,
  MemoryList,
  Scope,
  SearchRequest,
  SearchResponse,
} from "./types.js";

export * from "./types.js";
export { SodaMemApiError, SodaMemTimeoutError } from "./errors.js";

/** Minimal fetch-compatible shape, so callers can inject any implementation
 * (undici, node-fetch, a test double) without pulling in DOM lib types. */
export type FetchLike = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    signal?: AbortSignal;
  }
) => Promise<{
  ok: boolean;
  status: number;
  statusText: string;
  text(): Promise<string>;
}>;

export interface SodaMemClientOptions {
  /** Origin of the SodaMem server, e.g. "http://localhost:8000". No trailing
   * path segments — routes are joined from "/". */
  baseUrl: string;
  /** Sent as both `Authorization: Bearer <apiKey>` and `X-API-Key: <apiKey>`. */
  apiKey: string;
  /** Inject a fetch implementation (tests, non-standard runtimes). Defaults
   * to the global `fetch`. */
  fetch?: FetchLike;
  /** Per-request timeout. Defaults to 30_000ms. */
  timeoutMs?: number;
}

export interface WaitForJobOptions {
  /** Interval between GET /v1/jobs/{id} polls. Defaults to 500ms. */
  pollMs?: number;
  /** Give up and throw SodaMemTimeoutError after this long. Defaults to 60_000ms. */
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_POLL_MS = 500;
const DEFAULT_WAIT_TIMEOUT_MS = 60_000;

const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["succeeded", "failed"]);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

/** True when `body` looks like the server's ErrorBody envelope
 * ({ code, message, details }).
 *
 * Since 0806 a current SodaMem server answers EVERY error this way, router
 * 404/405 included. The `{ detail }` fallback below stays anyway: this SDK
 * talks to a server it does not control, so an older SodaMem, a reverse proxy,
 * or a gateway can still put its own shape on the wire. Normalizing both is
 * what lets a caller switch on `code` unconditionally. */
function isErrorBody(body: unknown): body is ErrorBody {
  return (
    typeof body === "object" &&
    body !== null &&
    typeof (body as Record<string, unknown>).code === "string" &&
    typeof (body as Record<string, unknown>).message === "string"
  );
}

export class SodaMemClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly fetchImpl: FetchLike;
  private readonly timeoutMs: number;

  constructor(options: SodaMemClientOptions) {
    if (!options.baseUrl) {
      throw new Error("SodaMemClient: baseUrl is required");
    }
    if (!options.apiKey) {
      throw new Error("SodaMemClient: apiKey is required");
    }
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    const injected = options.fetch;
    if (injected) {
      this.fetchImpl = injected;
    } else if (typeof fetch === "function") {
      this.fetchImpl = fetch as unknown as FetchLike;
    } else {
      throw new Error(
        "SodaMemClient: no global fetch found; pass { fetch } explicitly (e.g. from undici or node-fetch)"
      );
    }
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  // --- low-level request -----------------------------------------------------

  private async request<T>(
    method: string,
    path: string,
    opts: { query?: Record<string, string | number | boolean | null | undefined>; body?: unknown } = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${path}${opts.query ? buildQuery(opts.query) : ""}`;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "X-API-Key": this.apiKey,
      Accept: "application/json",
    };
    let body: string | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Awaited<ReturnType<FetchLike>>;
    try {
      response = await this.fetchImpl(url, { method, headers, body, signal: controller.signal });
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        throw new SodaMemTimeoutError(
          `${method} ${path} did not complete within ${this.timeoutMs}ms`,
          this.timeoutMs
        );
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }

    const raw = await response.text();
    let parsed: unknown = undefined;
    let parseError = false;
    if (raw.length > 0) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        parseError = true;
      }
    }

    if (!response.ok) {
      if (isErrorBody(parsed)) {
        throw new SodaMemApiError(parsed.message, {
          code: parsed.code,
          status: response.status,
          details: parsed.details ?? {},
        });
      }
      // Not our envelope. A current server always sends one, so reaching
      // here means an older SodaMem or something between us and it — a proxy
      // 502, a gateway timeout page. Salvage a message rather than throwing
      // away the only diagnostic the caller will get.
      const detail =
        typeof parsed === "object" && parsed !== null && "detail" in (parsed as Record<string, unknown>)
          ? String((parsed as Record<string, unknown>).detail)
          : raw || response.statusText || `HTTP ${response.status}`;
      throw new SodaMemApiError(detail, {
        code: "http_error",
        status: response.status,
        details: {},
      });
    }

    if (parseError) {
      throw new SodaMemApiError(`response body was not valid JSON: ${raw.slice(0, 200)}`, {
        code: "invalid_response",
        status: response.status,
        details: {},
      });
    }

    return parsed as T;
  }

  // --- health ------------------------------------------------------------------

  /** GET /health. Unauthenticated liveness probe. */
  health(): Promise<Health> {
    return this.request<Health>("GET", "/health");
  }

  // --- memories --------------------------------------------------------------

  /** POST /v1/memories. Returns AddMemoriesResult when `async_mode: false` is
   * passed explicitly; otherwise (the default) returns AddMemoriesAccepted —
   * pair with `waitForJob` to block until extraction finishes. */
  add(request: AddMemoriesRequest & { async_mode: false }): Promise<AddMemoriesResult>;
  add(request: AddMemoriesRequest & { async_mode?: true }): Promise<AddMemoriesAccepted>;
  add(request: AddMemoriesRequest): Promise<AddMemoriesAccepted | AddMemoriesResult>;
  add(request: AddMemoriesRequest): Promise<AddMemoriesAccepted | AddMemoriesResult> {
    return this.request("POST", "/v1/memories", { body: request });
  }

  /** GET /v1/memories */
  list(params: ListMemoriesParams): Promise<MemoryList> {
    const { user_id, agent_id, run_id, project_id, offset, limit } = params;
    return this.request<MemoryList>("GET", "/v1/memories", {
      query: { user_id, agent_id, run_id, project_id, offset, limit },
    });
  }

  /** GET /v1/memories/{id} */
  get(id: string, scope: Scope): Promise<Memory> {
    return this.request<Memory>("GET", `/v1/memories/${encodeURIComponent(id)}`, {
      query: {
        user_id: scope.user_id, agent_id: scope.agent_id,
        run_id: scope.run_id, project_id: scope.project_id,
      },
    });
  }

  /**
   * DELETE /v1/memories/{id}
   *
   * Archives the fact by default: it stops appearing in `get`, `list`,
   * `search` and `context`, but the row and its provenance are retained.
   * Pass `{ purge: true }` for an irreversible physical erase — which the
   * server refuses with 403 unless it was deployed with
   * `SODAMEM_ALLOW_PURGE=true`.
   */
  delete(id: string, scope: Scope, options: DeleteOptions = {}): Promise<DeleteResult> {
    return this.request<DeleteResult>("DELETE", `/v1/memories/${encodeURIComponent(id)}`, {
      query: {
        user_id: scope.user_id,
        agent_id: scope.agent_id,
        run_id: scope.run_id,
        project_id: scope.project_id,
        purge: options.purge ? "true" : undefined,
      },
    });
  }

  // --- search / context --------------------------------------------------------

  /** POST /v1/search */
  search(request: SearchRequest): Promise<SearchResponse> {
    return this.request<SearchResponse>("POST", "/v1/search", { body: request });
  }

  /** GET /v1/context */
  context(request: ContextRequest): Promise<ContextResponse> {
    const { user_id, agent_id, run_id, project_id, query, token_budget } = request;
    return this.request<ContextResponse>("GET", "/v1/context", {
      query: { user_id, agent_id, run_id, project_id, query, token_budget },
    });
  }

  // --- jobs --------------------------------------------------------------------

  /** GET /v1/jobs/{id} */
  job(jobId: string): Promise<Job> {
    return this.request<Job>("GET", `/v1/jobs/${encodeURIComponent(jobId)}`);
  }

  /**
   * Poll GET /v1/jobs/{id} until it reaches a terminal state
   * ("succeeded" | "failed"), then return it. Throws SodaMemTimeoutError if
   * `options.timeoutMs` elapses first — it never returns a non-terminal job
   * silently.
   *
   * This is the ergonomic counterpart to `async_mode: true` on `add()`: mem0
   * makes callers write their own poll loop, we ship one.
   */
  async waitForJob(jobId: string, options: WaitForJobOptions = {}): Promise<Job> {
    const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
    const timeoutMs = options.timeoutMs ?? DEFAULT_WAIT_TIMEOUT_MS;
    const deadline = Date.now() + timeoutMs;

    for (;;) {
      const job = await this.job(jobId);
      if (TERMINAL_JOB_STATUSES.has(job.status)) {
        return job;
      }
      const remaining = deadline - Date.now();
      if (remaining <= 0) {
        throw new SodaMemTimeoutError(
          `waitForJob(${jobId}) timed out after ${timeoutMs}ms (last status: ${job.status})`,
          timeoutMs
        );
      }
      await sleep(Math.min(pollMs, remaining));
    }
  }
}
