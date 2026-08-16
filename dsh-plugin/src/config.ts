/**
 * Plugin configuration: connection and scope facts only.
 *
 * There is deliberately no knob that turns recall or retain on/off, switches
 * strategy, or points the plugin at a local store. Recall and retain are the
 * whole point of the plugin, and a second local writer on one
 * `SODAMEM_DATA_ROOT` corrupts it — so this plugin talks HTTP to a running
 * daemon and nothing else.
 */
import z from "@deepseek-ai/schemastery";

/** The validated shape `apply()` receives. */
export interface SodaMemConfig {
  /** Origin of the SodaMem daemon, e.g. "http://127.0.0.1:8000". */
  apiUrl: string;
  /**
   * API key sent on every request. Any non-empty string works when the daemon
   * runs with auth disabled; there is no magic fallback.
   */
  apiKey: string;
  /** The SodaMem `user_id` every read and write is scoped to. */
  userId: string;
  /** Token budget handed to `GET /v1/context`. */
  tokenBudget: number;
}

export const Config: Schemastery<Partial<SodaMemConfig>, SodaMemConfig> = z.object({
  apiUrl: z
    .string()
    .required()
    .description("Origin of the SodaMem daemon, e.g. http://127.0.0.1:8000"),
  apiKey: z
    .string()
    .required()
    .description("API key; any non-empty string when the daemon runs with auth disabled"),
  userId: z.string().required().description("SodaMem user_id every call is scoped to"),
  tokenBudget: z
    .number()
    .default(1200)
    .description("Token budget for the recalled evidence block"),
}) as unknown as Schemastery<Partial<SodaMemConfig>, SodaMemConfig>;

/**
 * Recall sits on the synchronous path between the user pressing enter and the
 * first token. 1500 ms is the ceiling before a human notices added latency;
 * past it the turn proceeds with no memory rather than waiting.
 */
export const RECALL_TIMEOUT_MS = 1500;

/**
 * Retain runs after the turn has already produced its answer, so it can afford
 * more — but not unboundedly, because `agent/turn-stopping` is awaited.
 */
export const RETAIN_TIMEOUT_MS = 5000;

/** Prompt-context registration name and order. */
export const CONTEXT_NAME = "sodamem";
export const CONTEXT_ORDER = 200;
