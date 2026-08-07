import { describe, expect, it } from "vitest";
import { SodaMemApiError, SodaMemClient, SodaMemTimeoutError } from "../src/index.js";
import { createHangingFetch, createMockFetch } from "./mock-fetch.js";

const BASE_URL = "http://localhost:8000";
const API_KEY = "test-key-123";

function makeClient(fetchImpl: ReturnType<typeof createMockFetch>["fetchImpl"], timeoutMs?: number) {
  return new SodaMemClient({ baseUrl: BASE_URL, apiKey: API_KEY, fetch: fetchImpl, timeoutMs });
}

describe("constructor", () => {
  it("throws when baseUrl is missing", () => {
    expect(() => new SodaMemClient({ baseUrl: "", apiKey: API_KEY })).toThrow(/baseUrl/);
  });

  it("throws when apiKey is missing", () => {
    expect(() => new SodaMemClient({ baseUrl: BASE_URL, apiKey: "" })).toThrow(/apiKey/);
  });
});

describe("auth headers", () => {
  it("sends both Authorization: Bearer and X-API-Key on every request", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { status: "ok", version: "0.0.1", schema_version: 1, auth: "enabled" } },
    ]);
    const client = makeClient(fetchImpl);
    await client.health();

    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers).toMatchObject({
      Authorization: `Bearer ${API_KEY}`,
      "X-API-Key": API_KEY,
    });
  });
});

describe("GET /health", () => {
  it("parses the Health response", async () => {
    const { fetchImpl } = createMockFetch([
      { status: 200, body: { status: "ok", version: "0.0.1", schema_version: 3, auth: "disabled" } },
    ]);
    const client = makeClient(fetchImpl);
    const health = await client.health();
    expect(health).toEqual({ status: "ok", version: "0.0.1", schema_version: 3, auth: "disabled" });
  });
});

describe("POST /v1/memories (add)", () => {
  it("returns AddMemoriesAccepted on the 202 async path (default)", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 202, body: { job_id: "job-1", status: "pending", session_id: "sess-1" } },
    ]);
    const client = makeClient(fetchImpl);
    const result = await client.add({
      user_id: "u1",
      messages: [{ role: "user", content: "hi" }],
    });

    expect(result).toEqual({ job_id: "job-1", status: "pending", session_id: "sess-1" });
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url).toBe(`${BASE_URL}/v1/memories`);
    const sentBody = JSON.parse(calls[0]!.body!);
    expect(sentBody.user_id).toBe("u1");
  });

  it("returns AddMemoriesResult on the 200 sync path (async_mode: false)", async () => {
    const { fetchImpl } = createMockFetch([
      {
        status: 200,
        body: { session_id: "sess-1", facts_extracted: 3, spans_written: 5, turns_written: 1 },
      },
    ]);
    const client = makeClient(fetchImpl);
    const result = await client.add({
      user_id: "u1",
      messages: [{ role: "user", content: "hi" }],
      async_mode: false,
    });

    expect(result).toEqual({
      session_id: "sess-1",
      facts_extracted: 3,
      spans_written: 5,
      turns_written: 1,
    });
  });
});

describe("GET /v1/memories, /v1/memories/{id}, DELETE /v1/memories/{id}", () => {
  it("list() serializes scope + pagination as query params, omitting undefined fields", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { memories: [], total: 0, offset: 0, limit: 50 } },
    ]);
    const client = makeClient(fetchImpl);
    await client.list({ user_id: "u1", offset: 10, limit: 20 });

    const url = new URL(calls[0]!.url);
    expect(url.pathname).toBe("/v1/memories");
    expect(url.searchParams.get("user_id")).toBe("u1");
    expect(url.searchParams.get("offset")).toBe("10");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(url.searchParams.has("agent_id")).toBe(false);
    expect(url.searchParams.has("run_id")).toBe(false);
  });

  it("get() fetches a single memory by id with scope in the query string", async () => {
    const { fetchImpl, calls } = createMockFetch([
      {
        status: 200,
        body: {
          id: "m1",
          user_id: "u1",
          content: "likes coffee",
          metadata: {},
        },
      },
    ]);
    const client = makeClient(fetchImpl);
    const memory = await client.get("m1", { user_id: "u1", agent_id: "a1" });

    expect(memory.id).toBe("m1");
    const url = new URL(calls[0]!.url);
    expect(url.pathname).toBe("/v1/memories/m1");
    expect(url.searchParams.get("user_id")).toBe("u1");
    expect(url.searchParams.get("agent_id")).toBe("a1");
  });

  it("delete() archives by default and never sends purge", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { id: "m1", deleted: true, already_deleted: false,
                             purged: false, cascaded: {} } },
    ]);
    const client = makeClient(fetchImpl);
    const result = await client.delete("m1", { user_id: "u1" });

    expect(calls[0]!.method).toBe("DELETE");
    // Absent, not `purge=false`: the destructive path must be opt-in at the
    // wire level too, so a server-side default can never be flipped by us.
    expect(calls[0]!.url).not.toContain("purge");
    expect(result.deleted).toBe(true);
    expect(result.purged).toBe(false);
  });

  it("delete() with purge:true sends the flag and reports cascaded counts", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { id: "m1", deleted: true, already_deleted: false,
                             purged: true, cascaded: { spans: 2, edges: 1 } } },
    ]);
    const client = makeClient(fetchImpl);
    const result = await client.delete("m1", { user_id: "u1" }, { purge: true });

    expect(calls[0]!.url).toContain("purge=true");
    expect(result.purged).toBe(true);
    expect(result.cascaded).toEqual({ spans: 2, edges: 1 });
  });

  it("URL-encodes ids containing special characters", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { id: "m/1", deleted: true, already_deleted: false,
                             purged: false, cascaded: {} } },
    ]);
    const client = makeClient(fetchImpl);
    await client.delete("m/1", { user_id: "u1" });
    expect(calls[0]!.url).toContain("/v1/memories/m%2F1");
  });
});

describe("POST /v1/search", () => {
  it("posts the search body and returns hits", async () => {
    const { fetchImpl, calls } = createMockFetch([
      {
        status: 200,
        body: {
          query: "coffee",
          hits: [{ id: "m1", content: "likes coffee", metadata: {} }],
          degraded: [],
        },
      },
    ]);
    const client = makeClient(fetchImpl);
    const result = await client.search({ user_id: "u1", query: "coffee", top_k: 5 });

    expect(calls[0]!.method).toBe("POST");
    expect(JSON.parse(calls[0]!.body!)).toEqual({ user_id: "u1", query: "coffee", top_k: 5 });
    expect(result.hits).toHaveLength(1);
  });
});

describe("GET /v1/context", () => {
  it("serializes query + token_budget as query params", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { text: "ctx", citations: [], evidence: [], degraded: [] } },
    ]);
    const client = makeClient(fetchImpl);
    await client.context({ user_id: "u1", query: "coffee", token_budget: 1000 });

    expect(calls[0]!.method).toBe("GET");
    const url = new URL(calls[0]!.url);
    expect(url.searchParams.get("query")).toBe("coffee");
    expect(url.searchParams.get("token_budget")).toBe("1000");
  });
});

describe("error handling", () => {
  it("throws SodaMemApiError with code/message/details from the ErrorBody envelope", async () => {
    const { fetchImpl } = createMockFetch([
      {
        status: 400,
        body: { code: "invalid_scope", message: "user_id is required", details: { field: "user_id" } },
      },
    ]);
    const client = makeClient(fetchImpl);

    await expect(client.search({ user_id: "u1", query: "x" })).rejects.toMatchObject({
      name: "SodaMemApiError",
      code: "invalid_scope",
      status: 400,
      message: "user_id is required",
      details: { field: "user_id" },
    });
  });

  it("is a real SodaMemApiError instance (instanceof works)", async () => {
    const { fetchImpl } = createMockFetch([
      { status: 404, body: { code: "not_found", message: "no such memory", details: {} } },
    ]);
    const client = makeClient(fetchImpl);

    try {
      await client.get("missing", { user_id: "u1" });
      expect.unreachable("expected a throw");
    } catch (err) {
      expect(err).toBeInstanceOf(SodaMemApiError);
      expect((err as SodaMemApiError).status).toBe(404);
    }
  });

  it("normalizes FastAPI's raw {detail} error shape (e.g. 401 from the auth layer)", async () => {
    const { fetchImpl } = createMockFetch([
      { status: 401, body: { detail: "missing API key (Authorization: Bearer <key> or X-API-Key)" } },
    ]);
    const client = makeClient(fetchImpl);

    await expect(client.health()).rejects.toMatchObject({
      name: "SodaMemApiError",
      code: "http_error",
      status: 401,
      message: "missing API key (Authorization: Bearer <key> or X-API-Key)",
    });
  });

  it("throws SodaMemApiError when a 2xx body is not valid JSON", async () => {
    const { fetchImpl } = createMockFetch([{ status: 200, rawText: "<html>not json</html>" }]);
    const client = makeClient(fetchImpl);

    await expect(client.health()).rejects.toMatchObject({
      name: "SodaMemApiError",
      code: "invalid_response",
    });
  });

  it("throws SodaMemApiError for a non-2xx response with an unparseable body", async () => {
    const { fetchImpl } = createMockFetch([{ status: 500, rawText: "internal server error" }]);
    const client = makeClient(fetchImpl);

    await expect(client.health()).rejects.toMatchObject({
      name: "SodaMemApiError",
      code: "http_error",
      status: 500,
      message: "internal server error",
    });
  });
});

describe("request timeout", () => {
  it("throws SodaMemTimeoutError when the server never responds", async () => {
    const client = makeClient(createHangingFetch(), 20);
    await expect(client.health()).rejects.toBeInstanceOf(SodaMemTimeoutError);
  });
});

describe("waitForJob", () => {
  it("polls until status: succeeded and returns the final job", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { job_id: "j1", status: "pending", kind: "ingest", user_id: "u1", created_at: "t0" } },
      { status: 200, body: { job_id: "j1", status: "running", kind: "ingest", user_id: "u1", created_at: "t0" } },
      {
        status: 200,
        body: {
          job_id: "j1",
          status: "succeeded",
          kind: "ingest",
          user_id: "u1",
          created_at: "t0",
          finished_at: "t1",
          result: { facts_extracted: 4 },
        },
      },
    ]);
    const client = makeClient(fetchImpl);
    const job = await client.waitForJob("j1", { pollMs: 1 });

    expect(job.status).toBe("succeeded");
    expect(job.result).toEqual({ facts_extracted: 4 });
    expect(calls).toHaveLength(3);
    expect(calls.every((c) => c.url === `${BASE_URL}/v1/jobs/j1`)).toBe(true);
  });

  it("polls until status: failed and returns the final job (does not throw)", async () => {
    const { fetchImpl } = createMockFetch([
      { status: 200, body: { job_id: "j2", status: "running", kind: "ingest", user_id: "u1", created_at: "t0" } },
      {
        status: 200,
        body: {
          job_id: "j2",
          status: "failed",
          kind: "ingest",
          user_id: "u1",
          created_at: "t0",
          finished_at: "t1",
          error: "extraction crashed",
        },
      },
    ]);
    const client = makeClient(fetchImpl);
    const job = await client.waitForJob("j2", { pollMs: 1 });

    expect(job.status).toBe("failed");
    expect(job.error).toBe("extraction crashed");
  });

  it("throws SodaMemTimeoutError if the job never reaches a terminal state in time", async () => {
    const specs = Array.from({ length: 50 }, () => ({
      status: 200,
      body: { job_id: "j3", status: "running", kind: "ingest", user_id: "u1", created_at: "t0" },
    }));
    const { fetchImpl } = createMockFetch(specs);
    const client = makeClient(fetchImpl);

    await expect(client.waitForJob("j3", { pollMs: 1, timeoutMs: 15 })).rejects.toBeInstanceOf(
      SodaMemTimeoutError
    );
  });
});
