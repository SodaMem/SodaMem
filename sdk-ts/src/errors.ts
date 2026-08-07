/**
 * Errors thrown by SodaMemClient. Every non-2xx response and every transport
 * failure (network error, timeout, unparseable body) throws — nothing
 * resolves to `undefined` on failure.
 */

/** Thrown for any non-2xx HTTP response from the SodaMem API. */
export class SodaMemApiError extends Error {
  /** Stable, machine-readable error code. `"http_error"` when the server
   * didn't return the ErrorBody envelope (e.g. a raw FastAPI 401 detail). */
  readonly code: string;
  /** HTTP status code. */
  readonly status: number;
  /** Structured error details, if the server provided any. */
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: { code: string; status: number; details?: Record<string, unknown> }
  ) {
    super(message);
    this.name = "SodaMemApiError";
    this.code = options.code;
    this.status = options.status;
    this.details = options.details ?? {};
    Object.setPrototypeOf(this, SodaMemApiError.prototype);
  }
}

/** Thrown when a request does not complete within `timeoutMs`. */
export class SodaMemTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(message: string, timeoutMs: number) {
    super(message);
    this.name = "SodaMemTimeoutError";
    this.timeoutMs = timeoutMs;
    Object.setPrototypeOf(this, SodaMemTimeoutError.prototype);
  }
}
