/**
 * Test doubles. Everything is mocked at the HTTP boundary (`globalThis.fetch`)
 * and at the Cordis registration boundary — no live daemon, ever, and no dsh
 * runtime either.
 */
import type { Context } from "@deepseek-ai/cordis";
import type { ContentBlock } from "@deepseek-ai/dsh-llm";
import type { SodaMemConfig } from "../src/config.js";

export const CONFIG: SodaMemConfig = {
  apiUrl: "http://127.0.0.1:8000",
  apiKey: "test-key",
  userId: "aaron",
  tokenBudget: 1200,
};

// --- fetch double ------------------------------------------------------------

export interface RecordedCall {
  url: string;
  method: string | undefined;
  body: string | undefined;
  signal: AbortSignal | undefined;
}

export interface FetchDouble {
  calls: RecordedCall[];
  restore(): void;
}

type FetchInit = { method?: string; body?: string; signal?: AbortSignal } | undefined;
type FetchResponse = { ok: boolean; status: number; statusText: string; text(): Promise<string> };

function jsonResponse(status: number, body: unknown): FetchResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  };
}

/** Install a `globalThis.fetch` driven by `handler`, recording every call. */
export function installFetch(
  handler: (call: RecordedCall) => Promise<FetchResponse> | FetchResponse
): FetchDouble {
  const original = (globalThis as { fetch?: unknown }).fetch;
  const calls: RecordedCall[] = [];

  const impl = (url: string, init: FetchInit) => {
    const call: RecordedCall = {
      url,
      method: init?.method,
      body: init?.body,
      signal: init?.signal,
    };
    calls.push(call);
    return Promise.resolve(handler(call));
  };

  (globalThis as { fetch?: unknown }).fetch = impl;
  return {
    calls,
    restore() {
      (globalThis as { fetch?: unknown }).fetch = original;
    },
  };
}

/** A fetch that always answers `GET /v1/context` with `text`. */
export function installContextFetch(text: string): FetchDouble {
  return installFetch(() => jsonResponse(200, { text, citations: [], evidence: [], degraded: [] }));
}

/** A fetch that answers `POST /v1/memories` with the async-mode 202. */
export function installAcceptedFetch(): FetchDouble {
  return installFetch(() =>
    jsonResponse(202, { job_id: "job-1", status: "pending", session_id: "s" })
  );
}

/** A fetch that rejects the way an unreachable daemon does. */
export function installUnreachableFetch(): FetchDouble {
  return installFetch(() =>
    Promise.reject(
      Object.assign(new Error("connect ECONNREFUSED 127.0.0.1:8000"), { name: "TypeError" })
    )
  );
}

/**
 * A fetch whose HEADERS arrive normally and whose BODY then stalls forever.
 *
 * This is the shape the SDK cannot bound on its own: it clears its deadline
 * the moment headers land, so `response.text()` is awaited with no timer. Like
 * undici, the body read here rejects when the request signal aborts.
 */
export function installStallingBodyFetch(): FetchDouble {
  return installFetch((call) => ({
    ok: true,
    status: 200,
    statusText: "",
    text: () =>
      new Promise<string>((_resolve, reject) => {
        call.signal?.addEventListener("abort", () => {
          reject(Object.assign(new Error("The operation was aborted"), { name: "AbortError" }));
        });
      }),
  }));
}

/**
 * A fetch whose body stalls forever and IGNORES the abort signal entirely —
 * a transport that does not propagate cancellation into the body stream. The
 * plugin's deadline must still end the wait.
 */
export function installUncancellableBodyFetch(): FetchDouble {
  return installFetch(() => ({
    ok: true,
    status: 200,
    statusText: "",
    text: () => new Promise<string>(() => {}),
  }));
}

/** A fetch that never settles until its request is aborted (headers phase). */
export function installHangingFetch(): FetchDouble {
  return installFetch(
    (call) =>
      new Promise<FetchResponse>((_resolve, reject) => {
        call.signal?.addEventListener("abort", () => {
          reject(Object.assign(new Error("The operation was aborted"), { name: "AbortError" }));
        });
      })
  );
}

// --- cordis context double ---------------------------------------------------

export interface PromptContextRegistration {
  name: string;
  order: number;
  text: string | ((assembleCtx: unknown) => string);
}

export interface FakeContext {
  ctx: Context;
  listeners: Map<string, (...args: never[]) => unknown>;
  /** Registration labels whose disposer has been called, in call order. */
  disposedLabels: string[];
  promptContext: PromptContextRegistration | undefined;
  /** Run the disposer `apply()` handed to `ctx.effect()`. */
  unload(): void;
  logs: { level: string; args: unknown[] }[];
}

export function createFakeContext(): FakeContext {
  const listeners = new Map<string, (...args: never[]) => unknown>();
  const disposedLabels: string[] = [];
  const logs: { level: string; args: unknown[] }[] = [];
  let effectDisposer: (() => unknown) | undefined;

  const state: FakeContext = {
    ctx: undefined as unknown as Context,
    listeners,
    disposedLabels,
    promptContext: undefined,
    logs,
    unload() {
      effectDisposer?.();
    },
  };

  const log = (level: string) => (...args: unknown[]) => {
    logs.push({ level, args });
  };

  const logger = Object.assign(() => logger, {
    debug: log("debug"),
    info: log("info"),
    warn: log("warn"),
    error: log("error"),
  });

  const ctx = {
    logger,
    systemPrompt: {
      context(registration: PromptContextRegistration) {
        state.promptContext = registration;
        return () => {
          disposedLabels.push("systemPrompt.context");
        };
      },
    },
    on(event: string, listener: (...args: never[]) => unknown) {
      listeners.set(event, listener);
      return () => {
        disposedLabels.push(event);
        return true;
      };
    },
    effect(body: () => () => unknown) {
      effectDisposer = body();
      return () => {};
    },
  };

  state.ctx = ctx as unknown as Context;
  return state;
}

/** The `text` provider registered as `systemPrompt.context`, called for one agent. */
export function contributedText(fake: FakeContext, agentId: string | undefined): string {
  const registration = fake.promptContext;
  if (!registration) throw new Error("no systemPrompt.context registered");
  const { text } = registration;
  if (typeof text === "string") return text;
  return text(agentId === undefined ? {} : { agent: { id: agentId } });
}

// --- payload builders --------------------------------------------------------

export function textBlocks(...texts: string[]): ContentBlock[] {
  return texts.map((text) => ({ type: "text", text }) as ContentBlock);
}

export function userMessage(text: string): { role: "user"; content: ContentBlock[] } {
  return { role: "user", content: textBlocks(text) };
}

export function assistantMessage(text: string): { role: "assistant"; content: ContentBlock[] } {
  return { role: "assistant", content: textBlocks(text) };
}

export interface FakeEvent {
  type: string;
  data: unknown;
  message?: { role: string; content: ContentBlock[] };
}

/** A session double whose `deriveEventMessage` projects the event's message. */
export function fakeSession(events: FakeEvent[]) {
  return {
    events,
    deriveEventMessage(event: { type: string; data: unknown }) {
      return (event as FakeEvent).message ?? null;
    },
  };
}
