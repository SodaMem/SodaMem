import { afterEach, describe, expect, it, vi } from "vitest";
import { apply, buildQuery, sanitizeContextText } from "../src/index.js";
import {
  CONFIG,
  contributedText,
  createFakeContext,
  installContextFetch,
  installFetch,
  installHangingFetch,
  installUnreachableFetch,
  textBlocks,
  type FetchDouble,
} from "./harness.js";

let fetchDouble: FetchDouble | undefined;

afterEach(() => {
  fetchDouble?.restore();
  fetchDouble = undefined;
  vi.useRealTimers();
});

interface PreStepPayload {
  agent: { id: string };
  messages: { content: ReturnType<typeof textBlocks> }[];
  turn: number;
  step: number;
  signal: AbortSignal;
}

function payload(agentId: string, turn: number, text: string, step = 1): PreStepPayload {
  return {
    agent: { id: agentId },
    messages: [{ content: textBlocks(text) }],
    turn,
    step,
    signal: new AbortController().signal,
  };
}

function preStep(fake: ReturnType<typeof createFakeContext>) {
  const listener = fake.listeners.get("agent/pre-step");
  if (!listener) throw new Error("agent/pre-step was not registered");
  return listener as unknown as (
    p: PreStepPayload,
    next: () => Promise<unknown>
  ) => Promise<unknown>;
}

const ENTER = { kind: "enter", messages: [] };

describe("recall", () => {
  it("registers agent/pre-step and a systemPrompt context (not a section)", () => {
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    expect(fake.listeners.has("agent/pre-step")).toBe(true);
    expect(fake.promptContext?.name).toBe("sodamem");
    expect(typeof fake.promptContext?.text).toBe("function");
  });

  it("happy path: the SodaMem text reaches the prompt-context provider for that agent", async () => {
    fetchDouble = installContextFetch("Aaron prefers TypeScript over Python.");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const next = vi.fn(async () => ENTER);
    const result = await preStep(fake)(payload("agent-a", 1, "what do I prefer?"), next);

    expect(next).toHaveBeenCalledTimes(1);
    expect(result).toBe(ENTER);

    expect(fetchDouble.calls).toHaveLength(1);
    const url = new URL(fetchDouble.calls[0]!.url);
    expect(url.pathname).toBe("/v1/context");
    expect(url.searchParams.get("user_id")).toBe("aaron");
    expect(url.searchParams.get("query")).toBe("what do I prefer?");
    expect(url.searchParams.get("token_budget")).toBe("1200");

    expect(contributedText(fake, "agent-a")).toBe("Aaron prefers TypeScript over Python.");
  });

  it("never mutates payload.messages", async () => {
    fetchDouble = installContextFetch("remembered");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const p = payload("agent-a", 1, "hello");
    const messages = p.messages;
    const firstMessage = messages[0];
    Object.freeze(messages);

    await preStep(fake)(p, async () => ENTER);

    expect(p.messages).toBe(messages);
    expect(p.messages[0]).toBe(firstMessage);
    expect(p.messages).toHaveLength(1);
  });

  it("contributes nothing and issues no request when the turn has no text", async () => {
    fetchDouble = installContextFetch("should not be used");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const next = vi.fn(async () => ENTER);
    const empty: PreStepPayload = {
      agent: { id: "agent-a" },
      messages: [{ content: textBlocks("   ") }],
      turn: 1,
      step: 1,
      signal: new AbortController().signal,
    };
    await preStep(fake)(empty, next);

    expect(fetchDouble.calls).toHaveLength(0);
    expect(next).toHaveBeenCalledTimes(1);
    expect(contributedText(fake, "agent-a")).toBe("");
  });

  it("unreachable SodaMem: pre-step still resolves via next() and the provider returns ''", async () => {
    fetchDouble = installUnreachableFetch();
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const next = vi.fn(async () => ENTER);
    const result = await preStep(fake)(payload("agent-a", 1, "anything"), next);

    expect(next).toHaveBeenCalledTimes(1);
    expect(result).toBe(ENTER);
    expect(contributedText(fake, "agent-a")).toBe("");
    expect(fake.logs.some((entry) => entry.level === "warn")).toBe(true);
  });

  it("request timeout: the 1500ms timeout ends the wait, not the harness", async () => {
    vi.useFakeTimers();
    fetchDouble = installHangingFetch();
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    const next = vi.fn(async () => ENTER);
    const pending = preStep(fake)(payload("agent-a", 1, "anything"), next);

    // Nothing has settled yet: the daemon is still "thinking".
    await vi.advanceTimersByTimeAsync(1499);
    expect(next).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    await pending;

    expect(next).toHaveBeenCalledTimes(1);
    expect(contributedText(fake, "agent-a")).toBe("");
    // The abort came from the client's own timeout, and it is reported as one.
    expect(fetchDouble.calls[0]!.signal?.aborted).toBe(true);
    const warned = fake.logs.find((entry) => entry.level === "warn");
    expect(warned).toBeDefined();
    const error = warned!.args[1] as Error;
    expect(error.name).toBe("SodaMemTimeoutError");
    expect(error.message).toContain("1500ms");
  });

  it("cache isolation: two agent ids get their own text and neither reads the other's", async () => {
    fetchDouble = installFetch((call) => {
      const query = new URL(call.url).searchParams.get("query");
      return {
        ok: true,
        status: 200,
        statusText: "",
        text: async () =>
          JSON.stringify({
            text: `memory for ${query}`,
            citations: [],
            evidence: [],
            degraded: [],
          }),
      };
    });
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    await preStep(fake)(payload("agent-a", 1, "alpha"), async () => ENTER);
    await preStep(fake)(payload("agent-b", 1, "beta"), async () => ENTER);

    expect(contributedText(fake, "agent-a")).toBe("memory for alpha");
    expect(contributedText(fake, "agent-b")).toBe("memory for beta");
    // An assembly with no agent (diagnostics) contributes nothing.
    expect(contributedText(fake, undefined)).toBe("");
    expect(contributedText(fake, "agent-c")).toBe("");
  });

  it("fires once per turn, not once per step", async () => {
    fetchDouble = installContextFetch("remembered");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);
    const handler = preStep(fake);

    await handler(payload("agent-a", 7, "first step", 1), async () => ENTER);
    await handler(payload("agent-a", 7, "second step of the tool loop", 2), async () => ENTER);
    expect(fetchDouble.calls).toHaveLength(1);

    await handler(payload("agent-a", 8, "a new turn", 1), async () => ENTER);
    expect(fetchDouble.calls).toHaveLength(2);
  });

  it("sanitises {{ so strict interpolation cannot fail the assembly", async () => {
    fetchDouble = installContextFetch("Aaron's template is {{unresolvable}} and {{{deep}}}.");
    const fake = createFakeContext();
    apply(fake.ctx, CONFIG);

    await preStep(fake)(payload("agent-a", 1, "templates"), async () => ENTER);

    const text = contributedText(fake, "agent-a");
    expect(text).not.toContain("{{");
    expect(text).toContain("unresolvable");
  });

  it("sanitizeContextText leaves no {{ token behind", () => {
    expect(sanitizeContextText("{{a}}")).toBe("{ {a}}");
    expect(sanitizeContextText("{{{a}}}")).toBe("{ { {a}}}");
    expect(sanitizeContextText("{{{a}}}")).not.toContain("{{");
    expect(sanitizeContextText("no braces")).toBe("no braces");
  });

  it("buildQuery keeps only text blocks, in order", () => {
    const query = buildQuery([
      { content: [...textBlocks("hello"), { type: "reasoning", text: "hidden" } as never] },
      { content: textBlocks("world") },
      { content: [] },
    ]);
    expect(query).toBe("hello\nworld");
  });
});
