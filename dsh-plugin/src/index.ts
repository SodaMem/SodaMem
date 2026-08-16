/**
 * dsh-plugin-sodamem — SodaMem as a memory layer for the DeepSeek Harness.
 *
 * Recall is unconditional: every turn is assembled with whatever SodaMem
 * knows, through `agent/pre-step` + `systemPrompt.context`.
 * Retain is unconditional: every closed turn is ingested, through
 * `agent/turn-stopping`.
 *
 * Neither is a tool, so neither is left to the model's discretion — which is
 * the whole reason this exists rather than another MCP server.
 *
 * Remote only. The plugin speaks HTTP to a running SodaMem daemon and imports
 * nothing that can open a store: two local writers on one `SODAMEM_DATA_ROOT`
 * corrupt it, and a plugin inside an arbitrary harness process is the worst
 * possible second writer.
 */
import type { Context } from "@deepseek-ai/cordis";
import type {} from "@deepseek-ai/dsh-agent";
import { RecallCache } from "./cache.js";
import { CONTEXT_NAME, CONTEXT_ORDER, Config, type SodaMemConfig } from "./config.js";
import { createContextTextProvider, createRecallListener, type PluginLogger } from "./recall.js";
import { createRetainListener } from "./retain.js";

export { Config, type SodaMemConfig } from "./config.js";
export { RECALL_TIMEOUT_MS, RETAIN_TIMEOUT_MS, CONTEXT_NAME, CONTEXT_ORDER } from "./config.js";
export { RecallCache, type RecallEntry } from "./cache.js";
export { buildQuery, createContextTextProvider, createRecallListener } from "./recall.js";
export { collectTurnMessages, createRetainListener } from "./retain.js";
export { renderTextBlocks, sanitizeContextText } from "./messages.js";
export { createClient } from "./client.js";

export const name = "sodamem";

export const inject = ["systemPrompt"];

export function apply(ctx: Context, config: SodaMemConfig): void {
  const cache = new RecallCache();
  const logger = ctx.logger as unknown as PluginLogger;

  const disposers: Array<() => unknown> = [
    ctx.systemPrompt.context({
      name: CONTEXT_NAME,
      order: CONTEXT_ORDER,
      text: createContextTextProvider(cache),
    }),
    ctx.on("agent/pre-step", createRecallListener({ config, cache, logger })),
    ctx.on("agent/turn-stopping", createRetainListener({ config, logger })),
    ctx.on("agent/disposed", (payload) => {
      cache.delete(payload.agent.id);
    }),
  ];

  // Every registration is released on unload, in reverse order, and the cache
  // goes with them — nothing survives the fiber.
  ctx.effect(() => () => {
    for (const dispose of disposers.splice(0).reverse()) dispose();
    cache.clear();
  }, "dsh-plugin-sodamem");
}
