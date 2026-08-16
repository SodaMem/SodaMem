import { afterEach, describe, expect, it } from "vitest";
import { Config, apply, inject, name } from "../src/index.js";
import {
  CONFIG,
  contributedText,
  createFakeContext,
  installContextFetch,
  textBlocks,
  type FetchDouble,
} from "./harness.js";

let fetchDouble: FetchDouble | undefined;

afterEach(() => {
  fetchDouble?.restore();
  fetchDouble = undefined;
});

describe("plugin surface", () => {
  it("exports the cordis plugin shape", () => {
    expect(name).toBe("sodamem");
    expect(inject).toEqual(["systemPrompt"]);
    expect(typeof Config).toBe("function");
  });

  it("Config exposes exactly the four connection/scope fields and no behaviour knob", () => {
    const dict = (Config as unknown as { dict: Record<string, unknown> }).dict;
    expect(Object.keys(dict).sort()).toEqual(["apiKey", "apiUrl", "tokenBudget", "userId"]);
  });

  it("Config defaults tokenBudget to 1200 and rejects a missing apiUrl", () => {
    const resolved = Config({ apiUrl: "http://x", apiKey: "k", userId: "u" });
    expect(resolved.tokenBudget).toBe(1200);
    expect(() => Config({ apiKey: "k", userId: "u" })).toThrow();
  });

  it("agent/disposed evicts only that agent's cached memory", async () => {
    fetchDouble = installContextFetch("remembered");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const preStep = fake.listeners.get("agent/pre-step") as unknown as (
      p: unknown,
      next: () => Promise<unknown>
    ) => Promise<unknown>;
    const payload = (id: string) => ({
      agent: { id },
      messages: [{ content: textBlocks("hello") }],
      turn: 1,
      step: 1,
      signal: new AbortController().signal,
    });

    await preStep(payload("agent-a"), async () => ({ kind: "enter", messages: [] }));
    await preStep(payload("agent-b"), async () => ({ kind: "enter", messages: [] }));

    const disposed = fake.listeners.get("agent/disposed") as unknown as (p: unknown) => void;
    disposed({ agent: { id: "agent-a" } });

    expect(contributedText(fake, "agent-a")).toBe("");
    expect(contributedText(fake, "agent-b")).toBe("remembered");
  });

  it("unload disposes every registration and empties the cache", async () => {
    fetchDouble = installContextFetch("remembered");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const preStep = fake.listeners.get("agent/pre-step") as unknown as (
      p: unknown,
      next: () => Promise<unknown>
    ) => Promise<unknown>;
    await preStep(
      {
        agent: { id: "agent-a" },
        messages: [{ content: textBlocks("hello") }],
        turn: 1,
        step: 1,
        signal: new AbortController().signal,
      },
      async () => ({ kind: "enter", messages: [] })
    );
    expect(contributedText(fake, "agent-a")).toBe("remembered");

    fake.unload();

    expect(fake.disposedLabels.sort()).toEqual([
      "agent/disposed",
      "agent/pre-step",
      "agent/turn-stopping",
      "systemPrompt.context",
    ]);
    // The cache went with the registrations.
    expect(contributedText(fake, "agent-a")).toBe("");
  });
});
