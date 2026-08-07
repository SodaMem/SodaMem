# sodamem (TypeScript / JavaScript SDK)

Official TypeScript client for the [SodaMem](../README.md) REST API — evidence-grounded
temporal memory for AI agents. Ships ESM and CJS builds with full type
definitions, zero runtime dependencies, and a `waitForJob` helper so you don't
have to hand-roll a poll loop for async ingest.

## Install

```bash
npm install sodamem
```

Requires Node.js >= 18 (for global `fetch`/`AbortController`), or pass your
own `fetch` implementation (see [Custom fetch](#custom-fetch) below).

## Quick start

```ts
import { SodaMemClient } from "sodamem";

const client = new SodaMemClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.SODAMEM_API_KEY!,
});

// 1. Ingest a conversation. async_mode defaults to true: the server returns
//    202 + a job_id immediately and extracts facts in the background.
const accepted = await client.add({
  user_id: "user-42",
  messages: [
    { role: "user", content: "I switched teams last week, I'm on Platform now." },
    { role: "assistant", content: "Got it — I'll remember you're on Platform." },
  ],
});
console.log(accepted.job_id, accepted.status); // "pending"

// 2. Block until extraction finishes, instead of writing your own poll loop.
const job = await client.waitForJob(accepted.job_id, {
  pollMs: 500,     // how often to poll GET /v1/jobs/{id} (default 500ms)
  timeoutMs: 60_000, // give up and throw after this long (default 60s)
});

if (job.status === "failed") {
  throw new Error(`ingest failed: ${job.error}`);
}
console.log(job.result); // { facts_extracted, spans_written, turns_written }

// 3. Now the fact is searchable.
const results = await client.search({
  user_id: "user-42",
  query: "what team are they on?",
  top_k: 5,
});
for (const hit of results.hits) {
  console.log(hit.score, hit.content);
}
```

If you don't want to wait, pass `async_mode: false` to `add()` and it blocks
server-side instead, returning `AddMemoriesResult` directly (TypeScript
narrows the return type for you based on the literal `false`):

```ts
const result = await client.add({
  user_id: "user-42",
  messages: [{ role: "user", content: "..." }],
  async_mode: false,
});
result.facts_extracted; // typed, no job/poll needed
```

## Auth

Every request (except `health()`) is sent with **both**
`Authorization: Bearer <apiKey>` and `X-API-Key: <apiKey>` headers — the
server accepts either, so you don't need to know which one it's configured
for.

```ts
const client = new SodaMemClient({
  baseUrl: "https://sodamem.example.com",
  apiKey: process.env.SODAMEM_API_KEY!,
});
```

## Error handling

Every non-2xx response throws `SodaMemApiError` — nothing resolves to
`undefined` on failure:

```ts
import { SodaMemApiError, SodaMemTimeoutError } from "sodamem";

try {
  await client.get("does-not-exist", { user_id: "user-42" });
} catch (err) {
  if (err instanceof SodaMemApiError) {
    console.error(err.status, err.code, err.message, err.details);
  } else if (err instanceof SodaMemTimeoutError) {
    console.error("request timed out after", err.timeoutMs, "ms");
  } else {
    throw err; // network failure, etc. — not swallowed
  }
}
```

`SodaMemApiError` fields:

| Field     | Type                     | Notes                                                                                   |
|-----------|--------------------------|-------------------------------------------------------------------------------------------|
| `code`    | `string`                 | Machine-readable. `"http_error"` when the server didn't send the `ErrorBody` envelope (e.g. a raw FastAPI 401). |
| `status`  | `number`                 | HTTP status code.                                                                        |
| `details` | `Record<string, unknown>`| Structured details, if any.                                                             |
| `message` | `string`                 | Human-readable (from `Error`).                                                          |

`SodaMemTimeoutError` is thrown by both per-request timeouts (`timeoutMs` on
the client) and by `waitForJob` when the job doesn't reach a terminal state
in time.

## Custom fetch

Inject any `fetch`-compatible implementation — useful for older Node,
non-standard runtimes, or tests:

```ts
import { fetch } from "undici";

const client = new SodaMemClient({
  baseUrl: "http://localhost:8000",
  apiKey: "...",
  fetch, // structurally compatible: (url, init) => Promise<Response>
});
```

## API reference

| Method                                  | HTTP                        | Returns                              |
|------------------------------------------|------------------------------|----------------------------------------|
| `client.health()`                        | `GET /health`                 | `Health`                                |
| `client.add(request)`                    | `POST /v1/memories`           | `AddMemoriesAccepted` (202, default) or `AddMemoriesResult` (200, `async_mode: false`) |
| `client.waitForJob(jobId, opts?)`        | polls `GET /v1/jobs/{id}`     | `Job` (terminal: `succeeded` or `failed`) |
| `client.list(params)`                    | `GET /v1/memories`            | `MemoryList`                            |
| `client.get(id, scope)`                  | `GET /v1/memories/{id}`       | `Memory`                                |
| `client.delete(id, scope, opts?)`        | `DELETE /v1/memories/{id}`    | `DeleteResult`                          |
| `client.search(request)`                 | `POST /v1/search`             | `SearchResponse`                        |
| `client.context(request)`                | `GET /v1/context`             | `ContextResponse`                       |
| `client.job(jobId)`                      | `GET /v1/jobs/{id}`           | `Job`                                   |

`client.delete` archives by default — the memory disappears from `get`,
`list`, `search` and `context`, but its row and provenance are retained.
`client.delete(id, scope, { purge: true })` asks for an irreversible physical
erase instead, which the server rejects with 403 unless it was deployed with
`SODAMEM_ALLOW_PURGE=true`. Check `result.purged` to tell the two apart, and
`result.already_deleted` to tell a fresh archive from a repeat call.

All request types mirror `server/models.py` field-for-field — see
[`src/types.ts`](./src/types.ts) for the full definitions (`Scope`,
`Message`, `AddMemoriesRequest`, `Memory`, `SearchHit`, etc.).

Every request except `health()` requires `user_id` (via `Scope`); `agent_id`
and `run_id` are optional narrowing filters, matching mem0's scope model.

## Development

```bash
cd sdk-ts
npm install
npm run build   # emits dist/esm and dist/cjs
npm test        # vitest, all HTTP calls mocked — no server required
```
