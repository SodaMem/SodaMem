/**
 * SodaMem client construction: one SDK, remote only, signal plumbing included.
 *
 * `SodaMemClient` builds its own `AbortController` from `timeoutMs` and does
 * not accept a caller signal. To honour the turn's `AbortSignal` without
 * forking the SDK or opening a second HTTP path, we inject a `fetch` that
 * merges the two signals. Constructing a client per outbound call is honest:
 * it allocates a few fields, no pool and no socket, and it keeps the plumbing
 * free of shared mutable state.
 */
import { SodaMemClient, type FetchLike } from "sodamem";
import type { SodaMemConfig } from "./config.js";

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
 * `timeoutMs`, or the turn being cancelled.
 */
export function createClient(
  config: SodaMemConfig,
  timeoutMs: number,
  turnSignal?: AbortSignal
): SodaMemClient {
  const fetchImpl: FetchLike = (url, init) => {
    const signals: AbortSignal[] = [];
    if (init?.signal) signals.push(init.signal);
    if (turnSignal) signals.push(turnSignal);
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
