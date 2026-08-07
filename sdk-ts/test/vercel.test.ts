import { describe, expect, it } from "vitest";
import { SodaMemClient } from "../src/index.js";
import { createMemoryTools } from "../src/vercel.js";
import { createMockFetch } from "./mock-fetch.js";

const BASE_URL = "http://localhost:8000";
const API_KEY = "test-key-123";

function makeClient(fetchImpl: ReturnType<typeof createMockFetch>["fetchImpl"]) {
  return new SodaMemClient({ baseUrl: BASE_URL, apiKey: API_KEY, fetch: fetchImpl });
}

describe("createMemoryTools", () => {
  it("exposes the same three operations the other adapters do", () => {
    const { fetchImpl } = createMockFetch([]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    expect(Object.keys(tools).sort()).toEqual(
      ["addMemory", "getMemoryContext", "searchMemory"],
    );
  });

  it("requires a userId", () => {
    const { fetchImpl } = createMockFetch([]);
    expect(() => createMemoryTools(makeClient(fetchImpl), { userId: "" })).toThrow(/userId/);
  });

  it("never puts userId in a tool's input schema", () => {
    // The single most important property here. An agent framework hands tool
    // arguments to the MODEL. A user_id the model can choose is a user_id the
    // model can hallucinate — i.e. a cross-tenant read. Scope stays bound at
    // construction, in the application's hands, exactly as the Python
    // adapters do it.
    const { fetchImpl } = createMockFetch([]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    for (const [name, tool] of Object.entries(tools)) {
      const keys = Object.keys((tool as { inputSchema: { shape: object } }).inputSchema.shape);
      for (const forbidden of ["userId", "user_id", "agentId", "agent_id", "runId", "run_id"]) {
        expect(keys, `${name} exposes ${forbidden} to the model`).not.toContain(forbidden);
      }
    }
  });
});

describe("searchMemory", () => {
  it("calls /v1/search with the bound scope and returns the hits", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { memories: [{ id: "m1", content: "likes tea" }], count: 1 } },
    ]);
    const tools = createMemoryTools(makeClient(fetchImpl), {
      userId: "u1", agentId: "a1", runId: "r1",
    });
    const out = await tools.searchMemory.execute!({ query: "tea", topK: 5 }, {} as never);

    expect(calls[0]!.url).toContain("/v1/search");
    const body = JSON.parse(calls[0]!.body!);
    expect(body).toMatchObject({ user_id: "u1", agent_id: "a1", run_id: "r1", query: "tea" });
    expect((out as { memories: unknown[] }).memories).toHaveLength(1);
  });

  it("clamps topK instead of forwarding whatever the model asked for", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { memories: [], count: 0 } },
    ]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    await tools.searchMemory.execute!({ query: "x", topK: 9999 }, {} as never);
    expect(JSON.parse(calls[0]!.body!).top_k).toBeLessThanOrEqual(100);
  });
});

describe("getMemoryContext", () => {
  it("returns the prompt-ready block and its citations", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { text: "User likes tea.", citations: ["ev1"], evidence: [{}] } },
    ]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    const out = await tools.getMemoryContext.execute!(
      { query: "drinks", tokenBudget: 500 }, {} as never,
    ) as { text: string; citations: string[] };

    expect(calls[0]!.url).toContain("/v1/context");
    expect(out.text).toBe("User likes tea.");
    expect(out.citations).toEqual(["ev1"]);
  });
});

describe("addMemory", () => {
  it("writes synchronously so the tool result reflects a completed store", async () => {
    // A tool that returns before the write lands teaches the model that the
    // memory is already searchable when it is not.
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { session_id: "s1", facts_extracted: 2, spans_written: 1, turns_written: 1 } },
    ]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    const out = await tools.addMemory.execute!(
      { messages: [{ role: "user", content: "I drink oolong" }] }, {} as never,
    ) as { facts_extracted: number };

    expect(JSON.parse(calls[0]!.body!).async_mode).toBe(false);
    expect(out.facts_extracted).toBe(2);
  });

  it("generates a session id when the model does not supply one", async () => {
    const { fetchImpl, calls } = createMockFetch([
      { status: 200, body: { session_id: "generated", facts_extracted: 0, spans_written: 0, turns_written: 0 } },
    ]);
    const tools = createMemoryTools(makeClient(fetchImpl), { userId: "u1" });
    await tools.addMemory.execute!(
      { messages: [{ role: "user", content: "hi" }] }, {} as never,
    );
    expect(JSON.parse(calls[0]!.body!).session_id).toBeTruthy();
  });
});

describe("dependency isolation", () => {
  it("keeps ai/zod as OPTIONAL peers so the base client stays dependency-free", async () => {
    const fs = await import("node:fs");
    const pkg = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
    expect(pkg.dependencies ?? {}).toEqual({});
    for (const peer of ["ai", "zod"]) {
      expect(pkg.peerDependencies?.[peer], `${peer} is not a peer`).toBeTruthy();
      expect(pkg.peerDependenciesMeta?.[peer]?.optional,
        `${peer} must be optional — installing sodamem must not pull an agent framework`,
      ).toBe(true);
    }
  });

  it("does not re-export the adapter from the package root", async () => {
    // `import { SodaMemClient } from "sodamem"` must not drag `ai` into a
    // bundle. The subpath export (`sodamem/vercel`) is what keeps that true,
    // and a stray re-export in index.ts would silently undo it.
    const fs = await import("node:fs");
    const index = fs.readFileSync(new URL("../src/index.ts", import.meta.url), "utf8");
    expect(index).not.toContain("vercel");

    const pkg = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
    expect(pkg.exports["./vercel"]).toBeTruthy();
  });
});
