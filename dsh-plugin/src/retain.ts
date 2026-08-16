/**
 * Retain: ingest every closed turn, without the model having to decide to
 * write.
 *
 * `agent/turn-stopping` fires before `turn/end` reaches the log, so the turn
 * is sliced out of `session.events` by hand: find this turn's `turn/start`,
 * project every event at or after it with `deriveEventMessage`, keep the
 * user- and assistant-role prose.
 */
import type { ContentBlock } from "@deepseek-ai/dsh-llm";
import type { Message } from "sodamem";
import { withSodaMem } from "./client.js";
import { RETAIN_TIMEOUT_MS, type SodaMemConfig } from "./config.js";
import { renderTextBlocks } from "./messages.js";
import type { PluginLogger } from "./recall.js";

/** The shape of a session event this module reads. */
export interface RetainEvent {
  readonly type: string;
  readonly data: unknown;
}

/** The projection `deriveEventMessage` returns, as far as retain cares. */
export interface DerivedMessage {
  readonly role?: string;
  readonly content?: readonly ContentBlock[];
}

/** The subset of `Session` retain reads. */
export interface RetainSession {
  readonly events: readonly RetainEvent[];
  deriveEventMessage(event: RetainEvent): DerivedMessage | null;
}

/** The subset of the `agent/turn-stopping` payload retain reads. */
export interface RetainPayload {
  readonly agent: { readonly id: string; readonly session: RetainSession };
  readonly turn: number;
  readonly signal?: AbortSignal;
}

export interface RetainDeps {
  readonly config: SodaMemConfig;
  readonly logger: PluginLogger;
}

function isTurnStart(event: RetainEvent, turn: number): boolean {
  if (event.type !== "turn/start") return false;
  const data = event.data as { turn?: unknown } | null;
  return !!data && data.turn === turn;
}

/**
 * The closed turn's user and assistant messages, in log order, rendered to
 * flat text. Empty when the turn produced nothing worth remembering — in which
 * case nothing is ingested.
 */
export function collectTurnMessages(session: RetainSession, turn: number): Message[] {
  const events = session?.events;
  if (!events || events.length === 0) return [];

  let start = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event && isTurnStart(event, turn)) {
      start = i;
      break;
    }
  }
  if (start < 0) return [];

  const messages: Message[] = [];
  for (let i = start; i < events.length; i++) {
    const event = events[i];
    if (!event) continue;
    const derived = session.deriveEventMessage(event);
    if (!derived) continue;
    if (derived.role !== "user" && derived.role !== "assistant") continue;
    const content = renderTextBlocks(derived.content);
    if (!content) continue;
    messages.push({ role: derived.role, content });
  }
  return messages;
}

/**
 * The `agent/turn-stopping` listener. It is awaited by the machine, so it
 * swallows everything: a throw here would surface in the user's turn.
 *
 * The write uses the default `async_mode` (202 + `job_id`), so the harness
 * never waits on fact extraction. The job is deliberately not polled.
 */
export function createRetainListener(deps: RetainDeps) {
  const { config, logger } = deps;

  return async function onTurnStopping(payload: RetainPayload): Promise<void> {
    try {
      const agent = payload?.agent;
      if (!agent?.session) return;
      const messages = collectTurnMessages(agent.session, payload.turn);
      if (messages.length === 0) return;

      await withSodaMem(config, RETAIN_TIMEOUT_MS, payload.signal, (client) =>
        client.add({
          user_id: config.userId,
          session_id: agent.id,
          messages,
        })
      );
    } catch (error) {
      logger.warn("sodamem retain failed; the closed turn was not ingested: %o", error);
    }
  };
}
