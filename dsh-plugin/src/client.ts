/**
 * SodaMem client construction: one SDK, remote only, and a deadline that
 * actually bounds the call.
 *
 * Two things have to be true for a SodaMem call on the per-turn path:
 *
 * 1. It aborts when the turn does.
 * 2. It aborts when OUR deadline expires — the whole call, not just its
 *    headers.
 *
 * `SodaMemClient` gives us neither for free. It builds its own
 * `AbortController` from `timeoutMs`, does not accept a caller signal, and —
 * critically — clears that deadline the moment headers arrive:
 *
 * ```js
 * try     { response = await this.fetchImpl(url, { signal: controller.signal }); }
 * finally { clearTimeout(timer); }   // deadline dies here
 * const raw = await response.text(); // unbounded
 * ```
 *
 * So a daemon that answers 200 and then stalls mid-body would leave the
 * `await` pending with no deadline at all (undici's own `bodyTimeout` is 300s)
 * — inside `agent/pre-step`, and inside the awaited `agent/turn-stopping`.
 * That is precisely the failure this plugin exists to prevent.
 *
 * The fix lives at the seam we already own. `withSodaMem()` arms a
 * plugin-owned deadline that stays armed across the body read, feeds its
 * signal into the injected `fetch` (so the transport tears the socket down),
 * AND races the whole call against it (so the handler is bounded even if a
 * transport ignores the signal on the body). No SDK fork, no second HTTP path,
 * no second client.
 */
import { SodaMemClient, type FetchLike } from "sodamem";
import type { SodaMemConfig } from "./config.js";

/** Thrown when a SodaMem call exceeds the plugin's own deadline. */
export class SodaMemDeadlineError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`SodaMem call exceeded the plugin deadline of ${timeoutMs}ms`);
    this.name = "SodaMemDeadlineError";
    this.timeoutMs = timeoutMs;
  }
}

function globalFetch(): FetchLike {
  const impl = (globalThis as { fetch?: unknown }).fetch;
  if (typeof impl !== "function") {
    throw new Error(
      "dsh-plugin-sodamem: no global fetch found; Node >= 22 is required by the harness"
    );
  }
  return impl as FetchLike;
}

/**
 * A client whose requests abort on whichever comes first: the SDK's own
 * `timeoutMs`, the plugin deadline, or the turn being cancelled.
 *
 * Constructing a client per call is honest — it allocates a few fields, no
 * pool and no socket — and keeps the plumbing free of shared mutable state.
 */
export function createClient(
  config: SodaMemConfig,
  timeoutMs: number,
  turnSignal?: AbortSignal,
  deadlineSignal?: AbortSignal
): SodaMemClient {
  const fetchImpl: FetchLike = (url, init) => {
    const signals: AbortSignal[] = [];
    if (init?.signal) signals.push(init.signal);
    if (turnSignal) signals.push(turnSignal);
    if (deadlineSignal) signals.push(deadlineSignal);
    const signal = signals.length > 1 ? AbortSignal.any(signals) : signals[0];
    return globalFetch()(url, { ...init, signal });
  };

  return new SodaMemClient({
    baseUrl: config.apiUrl,
    apiKey: config.apiKey,
    timeoutMs,
    fetch: fetchImpl,
  });
}

/** A promise that rejects with the signal's reason when it aborts, and never resolves. */
function rejectOnAbort(signal: AbortSignal): Promise<never> {
  return new Promise<never>((_resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

/**
 * Run one SodaMem call under a deadline that covers the ENTIRE call, headers
 * and body alike.
 *
 * Any failure that happens after the deadline fired is reported as
 * {@link SodaMemDeadlineError}, so the reason a call ended is unambiguous
 * regardless of which layer noticed first.
 */
export async function withSodaMem<T>(
  config: SodaMemConfig,
  timeoutMs: number,
  turnSignal: AbortSignal | undefined,
  call: (client: SodaMemClient) => Promise<T>
): Promise<T> {
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(new SodaMemDeadlineError(timeoutMs)), timeoutMs);
  try {
    const client = createClient(config, timeoutMs, turnSignal, deadline.signal);
    return await Promise.race([call(client), rejectOnAbort(deadline.signal)]);
  } catch (error) {
    if (deadline.signal.aborted) throw deadline.signal.reason;
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
