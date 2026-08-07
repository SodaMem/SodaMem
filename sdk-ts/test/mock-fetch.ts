import type { FetchLike } from "../src/client.js";

export interface MockCall {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

export interface MockResponseSpec {
  status: number;
  /** JSON-serialized as the response body. Mutually exclusive with rawText. */
  body?: unknown;
  /** Sent verbatim as the response body (e.g. to simulate invalid JSON). */
  rawText?: string;
}

/** Queue-based fetch double: each call consumes the next queued response, in
 * order. Records every call for assertions. */
export function createMockFetch(specs: MockResponseSpec[]): {
  fetchImpl: FetchLike;
  calls: MockCall[];
} {
  const calls: MockCall[] = [];
  let cursor = 0;

  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({
      url,
      method: init?.method,
      headers: init?.headers,
      body: init?.body,
    });
    if (cursor >= specs.length) {
      throw new Error(`mock fetch: no more responses queued (call #${calls.length} to ${url})`);
    }
    const spec = specs[cursor++]!;
    const text =
      spec.rawText !== undefined ? spec.rawText : spec.body === undefined ? "" : JSON.stringify(spec.body);
    return {
      ok: spec.status >= 200 && spec.status < 300,
      status: spec.status,
      statusText: "",
      text: async () => text,
    };
  };

  return { fetchImpl, calls };
}

/** A fetch double that never resolves until its request is aborted, at which
 * point it rejects like real fetch/undici do on AbortController.abort(). */
export function createHangingFetch(): FetchLike {
  return (_url, init) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const err = new Error("The operation was aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
}
