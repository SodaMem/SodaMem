# Spike findings — dsh native plugin seams (ground truth from published .d.ts)

Verified by installing the real packages, not from docs:

| package | version on npm |
|---|---|
| `@deepseek-ai/cordis` | 4.0.1 |
| `@deepseek-ai/dsh-agent` | 0.1.0-rc.6 |
| `@deepseek-ai/dsh-system-prompt` | 0.0.1-rc.1 |
| `@deepseek-ai/dsh-session` | 0.0.1-rc.1 |

Note: peer ranges conflict across these (`dsh-system-prompt` wants
`dsh-llm@^0.0.1-rc.1`); installs need `--legacy-peer-deps`. Developer preview.

## VERDICT: PASS — both seams exist and both are async.

### Recall seam (inject memory into the prompt)

Two-stage, because the prompt-section text provider is SYNCHRONOUS and we need HTTP.

1. `ctx.on('agent/pre-step', async (payload, next) => ...)` — **waterfall, async**
   ```ts
   payload: { agent: Agent; messages: UserMessage[]; turn: number;
              step: number; signal: AbortSignal }
   next: () => Promise<PreStepDecision>
   ```
   - `payload.messages` is the query source (the user turn entering this step).
   - `payload.signal` is the turn's abort signal — pass it to fetch.
   - MUST `return next()` to preserve messages. We do NOT mutate messages.

2. `ctx.systemPrompt.context({ name, order, text })` — the registry for
   "Dynamic model context materialized as a durable user-role snapshot".
   This is the correct API, NOT `.section()` (that is persona/identity).
   ```ts
   text: string | ((context: AssembleContext) => string)   // SYNC
   ```
   Serves the value cached in stage 1.

3. Cache key: `@deepseek-ai/dsh-agent` merge-extends `AssembleContext`:
   ```ts
   declare module '@deepseek-ai/dsh-system-prompt' {
     interface AssembleContext { agent?: Agent }
   }
   ```
   `Agent.id: SessionId`. So the cache is keyed by `assembleCtx.agent?.id`.
   Multi-agent safe. Absent on diagnostics assemblies → contribute ''.

### Retain seam (write the turn back to memory)

`ctx.on('agent/turn-stopping', async (payload) => ...)` — **serial, async**
```ts
payload: { agent: Agent; turn: number; signal: AbortSignal }
```
- Fires when the turn is about to close and the model owes no response.
- `payload.agent.session` is the durable source of truth;
  `Session.events: readonly SessionEvent[]` and the derived-history accessor
  give the turn's messages.
- `Agent.id === Session.id` (`readonly id: SessionId` — "the single identity
  shared with session"), so session_id mapping is free.

### Other facts that constrain the design

- `Config` is declared with `@deepseek-ai/schemastery` (`z.object`), not zod.
- Plugin shape: `export const name`, `export const inject: string[]`,
  `export const Config`, `export function apply(ctx, config)`.
- Every registration returns a disposer; `ctx.effect()` / the returned
  disposer must be honoured so unload is clean.
- Node >= 22 (the reference third-party plugin pins 22.19).

## Open risk carried into the build

`/v1/context` moves onto the per-turn synchronous latency path, and the
daemon runs `--workers 1` by design (ADR 0001 §2). p50/p99 must be measured
against a real store before this is called done.
