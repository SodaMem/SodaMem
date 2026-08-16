/**
 * Recall: contribute what SodaMem knows to every turn, without the model
 * having to ask.
 *
 * Two stages, because `systemPrompt.context`'s text provider is SYNCHRONOUS
 * and recall needs HTTP:
 *
 *   1. `agent/pre-step` (async waterfall) fetches `GET /v1/context` and caches
 *      the result under the agent id.
 *   2. The registered prompt context reads that cache synchronously at
 *      assembly time.
 */
import type { ContentBlock } from "@deepseek-ai/dsh-llm";
import type { AssembleContext } from "@deepseek-ai/dsh-system-prompt";
import type { RecallCache } from "./cache.js";
import { createClient } from "./client.js";
import { CONTEXT_NAME, RECALL_TIMEOUT_MS, type SodaMemConfig } from "./config.js";
import { renderTextBlocks, sanitizeContextText } from "./messages.js";

/** The subset of `ctx.logger` this plugin uses. */
export interface PluginLogger {
  debug(format: unknown, ...args: unknown[]): void;
  warn(format: unknown, ...args: unknown[]): void;
}

/** The subset of the `agent/pre-step` payload recall reads. */
export interface RecallPayload {
  readonly agent: { readonly id: string };
  readonly messages: readonly { readonly content?: readonly ContentBlock[] }[];
  readonly turn: number;
  readonly signal?: AbortSignal;
}

export interface RecallDeps {
  readonly config: SodaMemConfig;
  readonly cache: RecallCache;
  readonly logger: PluginLogger;
}

/**
 * The query handed to `GET /v1/context`: the text of the messages entering
 * this step, in order.
 */
export function buildQuery(messages: RecallPayload["messages"]): string {
  const parts: string[] = [];
  for (const message of messages ?? []) {
    const text = renderTextBlocks(message?.content);
    if (text) parts.push(text);
  }
  return parts.join("\n").trim();
}

/**
 * The `agent/pre-step` listener.
 *
 * Contract, in order of importance:
 * - it ALWAYS `return next()`, so the loop's messages are preserved;
 * - it never assigns to `payload.messages` or its elements;
 * - it never throws and never rejects — every SodaMem failure degrades to
 *   "no memory this turn".
 */
export function createRecallListener(deps: RecallDeps) {
  const { config, cache, logger } = deps;

  return async function onPreStep<T>(
    payload: RecallPayload,
    next: () => Promise<T>
  ): Promise<T> {
    try {
      const agentId = payload.agent?.id;
      // Once per turn, not once per step: `pre-step` fires for every step of a
      // tool loop and steps 2..n carry no new user message. This is the single
      // biggest cost and latency lever, and it is not configurable.
      if (agentId && !cache.has(agentId, payload.turn)) {
        const query = buildQuery(payload.messages);
        if (query) {
          // Claim the turn before awaiting, so a concurrent step cannot issue a
          // second request, and so a failure below already reads as "no memory".
          cache.set(agentId, payload.turn, "");
          const client = createClient(config, RECALL_TIMEOUT_MS, payload.signal);
          const response = await client.context({
            user_id: config.userId,
            query,
            token_budget: config.tokenBudget,
          });
          const text = typeof response?.text === "string" ? response.text : "";
          cache.set(agentId, payload.turn, sanitizeContextText(text));
        }
      }
    } catch (error) {
      logger.warn("sodamem recall failed; continuing with no memory this turn: %o", error);
    }
    return next();
  };
}

/**
 * The synchronous text provider registered as `systemPrompt.context`.
 *
 * `assembleCtx.agent` is absent on diagnostics assemblies; empty text
 * contributes nothing, which is exactly the right answer there.
 */
export function createContextTextProvider(
  cache: RecallCache
): (assembleCtx: AssembleContext) => string {
  return (assembleCtx) => cache.get(assembleCtx?.agent?.id);
}

export { CONTEXT_NAME };
