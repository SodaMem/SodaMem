/**
 * Vercel AI SDK adapter (PRD R2.8 — the fourth of the four framework
 * adapters; the other three are Python).
 *
 * Every agent framework wants the same three things from a memory layer:
 * search it, get a prompt-ready block out of it, write to it. This file is a
 * thin shell that hands those to the `ai` package's `tool()` helper; the
 * wire-level work lives in `SodaMemClient`, so a fix there is a fix here.
 *
 * `getMemoryContext` is deliberately the headline: it is the operation mem0
 * and open-source Zep do not offer, and the one an agent author actually
 * needs — a list of records still has to be assembled into a prompt by hand.
 *
 * `ai` and `zod` are OPTIONAL PEER dependencies, imported lazily. Importing
 * `sodamem` for the plain client must never drag an agent framework into the
 * bundle — the same reasoning the Python adapters use when they import
 * langchain inside the factory rather than at module scope.
 */
import type { SodaMemClient } from "./client.js";
import type { Message } from "./types.js";

/** Scope bound at construction. See `createMemoryTools` for why it is not
 * part of any tool's input schema. */
export interface MemoryToolsOptions {
  userId: string;
  agentId?: string;
  runId?: string;
  projectId?: string;
  /** Upper bound on `topK`, whatever the model asks for. */
  maxTopK?: number;
}

// One wording, four frameworks. These strings are what the MODEL reads to
// decide whether to call a tool, so drift between adapters is drift in agent
// behaviour, not a cosmetic inconsistency. Kept byte-identical to
// `adapters/_core.py`.
const SEARCH_DESCRIPTION =
  "Search the user's long-term memory for facts relevant to a query. " +
  "Returns ranked memory records with their evidence. Use get_memory_context " +
  "instead when you want text to paste into a prompt.";

const CONTEXT_DESCRIPTION =
  "Get a prompt-ready block of the user's relevant long-term memories, " +
  "already deduplicated, ranked, time-annotated and trimmed to a token " +
  "budget, with citations for exactly the evidence the text contains. " +
  "This is the preferred read: it needs no LLM call and no assembly.";

const ADD_DESCRIPTION =
  "Store a slice of conversation in the user's long-term memory. Facts are " +
  "extracted and grounded to their source turns.";

function requirePeers(): { tool: typeof import("ai").tool; z: typeof import("zod").z } {
  let ai: typeof import("ai");
  let zod: typeof import("zod");
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    ai = require("ai");
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    zod = require("zod");
  } catch {
    throw new Error(
      "createMemoryTools requires the `ai` and `zod` packages. " +
        "Install them alongside sodamem: npm install ai zod",
    );
  }
  return { tool: ai.tool, z: zod.z };
}

/**
 * Build Vercel AI SDK tools bound to one user's memory.
 *
 * Pass the result straight to `generateText`/`streamText`:
 *
 * ```ts
 * const tools = createMemoryTools(client, { userId: session.userId });
 * await generateText({ model, tools, prompt });
 * ```
 *
 * **Scope is bound here, not exposed to the model.** An agent framework hands
 * tool arguments to the LLM, so a `userId` the model can choose is a `userId`
 * the model can get wrong — a cross-tenant read by hallucination. None of the
 * scope keys appear in any tool's input schema; they come from this call,
 * which is application code.
 */
export function createMemoryTools(client: SodaMemClient, options: MemoryToolsOptions) {
  const { userId, agentId, runId, projectId, maxTopK = 100 } = options;
  if (!userId) {
    throw new Error("createMemoryTools requires a non-empty userId");
  }
  const { tool, z } = requirePeers();
  const scope = {
    user_id: userId,
    ...(agentId ? { agent_id: agentId } : {}),
    ...(runId ? { run_id: runId } : {}),
    ...(projectId ? { project_id: projectId } : {}),
  };

  return {
    searchMemory: tool({
      description: SEARCH_DESCRIPTION,
      inputSchema: z.object({
        query: z.string().describe("What to look for in the user's memory."),
        topK: z.number().int().optional().describe("How many records to return."),
      }),
      execute: async ({ query, topK }: { query: string; topK?: number }) =>
        client.search({
          ...scope,
          query,
          // Clamped rather than forwarded: `topK` arrives from the model, and
          // an unbounded page size is a denial-of-service lever a hallucinated
          // number can pull.
          top_k: Math.max(1, Math.min(topK ?? 10, maxTopK)),
        }),
    }),

    getMemoryContext: tool({
      description: CONTEXT_DESCRIPTION,
      inputSchema: z.object({
        query: z.string().describe("What the assembled context should be about."),
        tokenBudget: z.number().int().optional()
          .describe("Approximate size cap for the returned text."),
      }),
      execute: async ({ query, tokenBudget }: { query: string; tokenBudget?: number }) => {
        const block = await client.context({
          ...scope,
          query,
          token_budget: Math.max(100, Math.min(tokenBudget ?? 2000, 32000)),
        });
        return {
          text: block.text,
          citations: block.citations ?? [],
          evidence_count: block.evidence?.length ?? 0,
        };
      },
    }),

    addMemory: tool({
      description: ADD_DESCRIPTION,
      inputSchema: z.object({
        messages: z.array(
          z.object({
            role: z.enum(["user", "assistant", "system"]).optional(),
            content: z.string(),
          }),
        ).describe("The conversation slice to remember."),
        sessionId: z.string().optional()
          .describe("Groups these messages into one conversation."),
      }),
      execute: async ({ messages, sessionId }: { messages: Message[]; sessionId?: string }) =>
        client.add({
          ...scope,
          messages,
          session_id: sessionId ?? `vercel_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
          // Synchronous on purpose. The async path answers 202 + job_id, and a
          // tool that returns before the write lands teaches the model that
          // the memory is already searchable when it is not.
          async_mode: false,
        }),
    }),
  };
}
