# Spec: dsh native plugin — auto-recall per turn, auto-retain per turn close

Record: GitHub issue #9 (https://github.com/SodaMem/SodaMem/issues/9)
Branch: `feat/9-dsh-native-plugin`
Worktree: `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9`
Ground truth for every dsh API fact below: `specs/issue-9-spike.md` (authoritative).

## Problem

SodaMem's differentiator is `GET /v1/context` — a zero-LLM, prompt-ready
evidence block. Over the existing MCP bridge (`DEEPSEEK_HARNESS_INTEGRATION.md`)
it is exposed as a **tool**, so the model has to decide to call it. The
strongest thing the product has is left to the model's discretion, and on most
turns it simply never fires. MCP cannot fix this: a tool is pull-only, and
nothing in the MCP protocol lets a server contribute to the prompt or observe a
turn closing.

A native DeepSeek Harness (Cordis) plugin can do both, because the harness
exposes exactly two seams we need — `agent/pre-step` (async waterfall, carries
the user's messages and the turn's `AbortSignal`) and `agent/turn-stopping`
(async serial, fires when the turn is about to close).

## Value

- Recall becomes unconditional: every turn is assembled with whatever SodaMem
  knows, without the model choosing to ask.
- Retain becomes unconditional: every closed turn is ingested, without the model
  choosing to write.
- It is the first artifact that proves SodaMem is a memory *layer*, not a tool
  bag — and it is the prerequisite for retiring the MCP DeepSeek guide (a
  follow-up, explicitly out of scope here).

## Scope

New package, in-tree, sibling of `sdk-ts/`. npm name `dsh-plugin-sodamem`.

```
dsh-plugin/
  package.json            # name dsh-plugin-sodamem, type module, dual ESM/CJS
  .npmrc                  # legacy-peer-deps=true  (see "dsh dependency reality")
  tsconfig.json           # shared compiler options
  tsconfig.esm.json       # -> dist/esm
  tsconfig.cjs.json       # -> dist/cjs
  vitest.config.ts
  LICENSE                 # copy of the repo LICENSE (Apache-2.0)
  NOTICE                  # byte-identical copy of the repo NOTICE
  src/
    index.ts              # name / inject / Config / apply — the whole plugin surface
    config.ts             # schemastery Config
    recall.ts             # agent/pre-step handler + the systemPrompt.context provider
    retain.ts             # agent/turn-stopping handler
    cache.ts              # per-agent recall cache
    messages.ts           # dsh Message/ContentBlock -> SodaMem Message projection
    client.ts             # SodaMemClient construction + signal/timeout plumbing
  test/
    recall.test.ts
    retain.test.ts
    cache.test.ts
    dispose.test.ts
  scripts/
    measure-context-latency.mjs   # AC7 harness
  NOTES-latency.md        # AC7 measured numbers (or the explicit "not measured, because…")
  README.md               # final step, see AC10
```

Touched outside `dsh-plugin/` (all mechanical, no behaviour change):

- `.github/workflows/ci.yml` — new `dsh-plugin` job mirroring the `sdk-ts` job.
- `MANIFEST.in` — `prune dsh-plugin` (sdist hygiene, same line as `prune sdk-ts`).
- `README.md` §"Agent integrations" table + `docs/integrations/README.md` table
  — **final step only**, see AC10.

Not touched: any Python file, `pyproject.toml`, `sodamem/`, `server/`,
`mcp_server/`, `sodamem_cli/`, `sodamem_opt/`.

`tests/test_packaging.py` needs no change: `_top_level_packages()` only counts
directories containing `__init__.py`, and `dsh-plugin/` has none. Verified by
reading the test, not assumed.

## Implementation Path

### Verified seams (from `specs/issue-9-spike.md` + the installed `.d.ts`)

```ts
'agent/pre-step'(payload: { agent: Agent; messages: UserMessage[]; turn: number;
                            step: number; signal: AbortSignal },
                 next: () => Promise<PreStepDecision>): Promise<PreStepDecision>   // waterfall

'agent/turn-stopping'(payload: { agent: Agent; turn: number;
                                 signal: AbortSignal }): Promise<void> | void       // serial

'agent/disposed'(payload: { agent: Agent; ... }): void                              // emit

ctx.systemPrompt.context({ name, order, text }): () => void
  //  text: string | ((context: AssembleContext) => string)   <- SYNCHRONOUS
  //  AssembleContext is merge-extended by dsh-agent with `agent?: Agent`
  //  Agent.id: SessionId, and Agent.id === Session.id
```

### Recall (two-stage, because the text provider is synchronous)

1. `agent/pre-step`:
   - Build the query from `payload.messages`: concatenate the `text` of every
     `TextBlock` in each message's `content`. `ContentBlock` is a merge-extensible
     union `{ text | reasoning | image | tool-call | tool-result }` — take
     `type === 'text'` only. Trim; if the result is empty, contribute nothing and
     `return next()` immediately (no HTTP).
   - **Once per turn, not once per step.** `pre-step` fires for every step of a
     tool loop, and steps 2..n carry no new user message. If the cache already
     holds an entry for `(agent.id, payload.turn)`, skip the request entirely.
     This is the single biggest cost/latency lever and it is not configurable.
   - Call `client.context({ user_id, query, token_budget })` with the turn's
     `payload.signal` (see "signal plumbing") and the recall timeout.
   - On success, store `{ turn, text: sanitize(response.text) }` in the cache
     keyed by `payload.agent.id`. On **any** failure — network error,
     `SodaMemApiError`, `SodaMemTimeoutError`, abort, malformed body — store
     `{ turn, text: '' }`, log at `ctx.logger` debug/warn level, and continue.
   - Always `return next()`. Never mutate `payload.messages`, never throw.

2. `ctx.systemPrompt.context({ name: 'sodamem', order: 200, text })` where
   `text` is a sync provider reading `assembleCtx.agent?.id` and returning the
   cached string, or `''` when the agent is absent (diagnostics assemblies) or
   the cache misses. Empty text contributes nothing, per `PromptContext` docs.

3. **`sanitize()` is mandatory, not cosmetic.** Contexts go through the same
   strict `{{variable}}` interpolation as sections, and an unresolvable
   reference makes assembly *fail* — i.e. a memory whose stored text happens to
   contain `{{...}}` would break the user's turn. `sanitize()` neutralises every
   `{{` in the SodaMem text (e.g. replace `{{` with `{ {`) before it is cached.

4. Cache: `Map<SessionId, { turn: number; text: string }>` in module-private
   plugin state, one map per `apply()` call. Evicted on `agent/disposed` and
   cleared on plugin unload. Keyed by agent id, so two agents never see each
   other's memory block.

### Retain

`agent/turn-stopping`:

- The `turn/end` event is not in the log yet at this point, so slice the turn
  from `payload.agent.session.events`: find the last `turn/start` event whose
  `data.turn === payload.turn`, take every event at or after it, project each
  with `session.deriveEventMessage(event)` and drop the `null`s. Keep
  `role === 'user' | 'assistant'`; render each message's content with the same
  text-block projection used by recall; drop messages that render empty.
- If nothing survives, do nothing (no empty ingest).
- `client.add({ user_id, session_id: agent.id, messages })` — default
  `async_mode` (202 + `job_id`), so the harness never waits on fact extraction.
  Do not poll the job.
- Wrap in the same swallow-everything guard as recall. `turn-stopping` is
  awaited by the machine; a throw here would surface in the user's turn.

### Scope mapping (deliberate)

| SodaMem field | value | why |
|---|---|---|
| `user_id` | `config.userId` | required; there is no unscoped route |
| `session_id` (retain) | `agent.id` | `Agent.id === Session.id`, so the mapping is free and stable |
| `agent_id` | **not sent** | it would be the *session* id, which narrows retrieval and would fragment recall across sessions |
| `project_id`, `run_id` | not sent | no honest source in the harness |
| `token_budget` (recall) | `config.tokenBudget` | scope fact, default 1200 |

### Signal plumbing (the one non-obvious constraint)

`SodaMemClient` (published `sodamem@0.1.0`) builds its **own** `AbortController`
from `timeoutMs` and does not accept a caller `signal`. To honour
`payload.signal` without forking the client or adding a second HTTP path,
construct a short-lived `SodaMemClient` per outbound call with an injected
`fetch` that merges the two signals:

```ts
new SodaMemClient({
  baseUrl: config.apiUrl,
  apiKey: config.apiKey,
  timeoutMs,                       // recall: 1500, retain: 5000
  fetch: (url, init) => baseFetch(url, {
    ...init,
    signal: AbortSignal.any([init!.signal!, turnSignal]),   // Node >= 20
  }),
});
```

Client construction allocates nothing but a few fields — no pool, no socket — so
per-call construction is honest and keeps the plumbing free of shared mutable
state. `AbortSignal.any` is fine: the harness requires Node >= 22.

### Timeouts (explicit, not configurable)

| path | timeout | on expiry |
|---|---|---|
| recall (`GET /v1/context`) | **1500 ms** | cache `''`, turn proceeds with no memory |
| retain (`POST /v1/memories`) | **5000 ms** | log and drop; the turn has already produced its answer |

1500 ms is the ceiling a human notices as added latency before the first token.
The daemon runs `--workers 1` by design (ADR 0001 §2), which is exactly why AC7
demands a measured number rather than a hoped-for one.

### Config (schemastery `z.object`, connection + scope facts only)

| field | type | required | default |
|---|---|---|---|
| `apiUrl` | string | yes | — |
| `apiKey` | string | yes | — (any non-empty string when the daemon runs with auth disabled; no magic fallback is invented) |
| `userId` | string | yes | — |
| `tokenBudget` | number | no | `1200` |

No `enable*`, no `mode`, no `injectOn*`. **Remote only:** there is no data-root
option and the plugin imports nothing that can open a store. Two local writers
on one `SODAMEM_DATA_ROOT` corrupt it (`mcp_server/README.md`, ADR 0001 §2), and
a plugin loaded inside an arbitrary harness process is the worst possible second
writer.

### Plugin shape and disposal

```ts
export const name = 'sodamem';
export const inject = ['systemPrompt'];
export const Config = /* schemastery object */;
export function apply(ctx: Context, config: Config) { /* ... */ }
```

Four registrations, four disposers, all handed to `ctx.effect()` (or returned
from `apply`): `systemPrompt.context(...)`, `ctx.on('agent/pre-step')`,
`ctx.on('agent/turn-stopping')`, `ctx.on('agent/disposed')`. Unload also clears
the cache map.

### dsh dependency reality

- `dependencies`: `sodamem@^0.1.0` (the published npm SDK) — the only runtime
  dependency. Depend on the registry version, not a `file:../sdk-ts` link: the
  plugin ships as its own npm tarball and a file link would not resolve for any
  installer.
- `peerDependencies` + `devDependencies`: `@deepseek-ai/cordis`,
  `@deepseek-ai/dsh-agent`, `@deepseek-ai/dsh-system-prompt`,
  `@deepseek-ai/dsh-session`, `@deepseek-ai/dsh-llm`, `@deepseek-ai/schemastery`
  — the harness provides them at runtime; we need them to typecheck and test.
- Peer ranges genuinely conflict in the published rc's (`dsh-system-prompt`
  wants `dsh-llm@^0.0.1-rc.1`, `dsh-agent` wants `^0.1.0-rc.6`). Pin `dsh-llm`
  to `^0.1.0-rc.6` (the payload types we consume come from `dsh-agent`) and add
  `dsh-plugin/.npmrc` with `legacy-peer-deps=true` so `npm ci` works in CI
  without a flag someone has to remember.

### Build/test conventions (mirror `sdk-ts/`)

`tsc` dual ESM/CJS with the same `build:esm` / `build:cjs` / `build` /
`test` (vitest) / `typecheck` scripts, `files: ["dist","README.md","LICENSE","NOTICE"]`.
Tests mock the HTTP boundary by injecting a fake `fetch` (the same seam
`sdk-ts/test/mock-fetch.ts` uses) — no live daemon, ever.

### Decisions taken here, deliberately not raised as questions

- **Versioning:** `dsh-plugin` starts at `0.1.0` and versions independently of
  `pyproject.toml`/`sdk-ts`, because it tracks dsh's rc cadence, not SodaMem's.
  `tests/test_distribution_contracts.py::test_version_is_single_sourced_between_python_and_npm`
  covers `sdk-ts` only and is intentionally left alone.
- **Translated READMEs** (`docs/i18n/README.*.md`) are not updated here. No test
  enforces table parity, and touching seven translations belongs with the MCP
  guide retirement.

## Acceptance Criteria

- [ ] **AC1 — Recall is wired to the verified seams.** `dsh-plugin/src/` registers
      `ctx.on('agent/pre-step', ...)` and `ctx.systemPrompt.context({ name, order, text })`
      (NOT `.section()`), the pre-step handler always `return next()`s, and it
      never assigns to `payload.messages` or its elements.
- [ ] **AC2 — Retain is wired to `agent/turn-stopping`** and derives the closed
      turn's messages from `payload.agent.session` (`events` +
      `deriveEventMessage`), sending them with `session_id = agent.id`.
- [ ] **AC3 — Remote-only, enforced by construction.** The package's config
      schema has no data-root/store field, and `grep` over `dsh-plugin/src`
      finds no import of any store/filesystem SodaMem entry point — every
      outbound call goes through `SodaMemClient` against `config.apiUrl`.
- [ ] **AC4 — One client.** `dsh-plugin/package.json` lists `sodamem` as a
      dependency, and `dsh-plugin/src` contains no hand-rolled `fetch` to a
      `/v1/...` URL. The only `fetch` reference is the injected wrapper that
      merges `payload.signal` into the SDK's request.
- [ ] **AC5 — No behaviour knobs.** The schemastery `Config` exposes exactly
      `apiUrl`, `apiKey`, `userId`, `tokenBudget` — no boolean or enum that
      turns recall or retain on/off or switches strategy.
- [ ] **AC6 — Failure isolation, with stated timeouts.** Recall uses a 1500 ms
      timeout and retain 5000 ms; every SodaMem call site is wrapped so that no
      error, rejection, or abort propagates out of a handler; and both handlers
      degrade to "no memory contributed this turn". Proven by AC8's unreachable
      and timeout tests.
- [ ] **AC7 — Measured `GET /v1/context` latency.** `dsh-plugin/NOTES-latency.md`
      records p50 and p99 over >= 50 sequential requests against a real daemon,
      naming the store size, the query set, and the daemon flags. If a live
      measurement is impossible in this environment, the file says so in one
      explicit sentence, names the blocker, and states what was done instead
      (e.g. the script exists and its invocation is documented). **No number may
      be written that was not produced by a run.**
- [ ] **AC8 — `npm test` passes in `dsh-plugin/`**, with tests that cover, each
      as a distinct named test, and none requiring a live daemon:
      1. recall happy path — the SodaMem text reaches the `systemPrompt.context`
         provider for the right agent id;
      2. recall when SodaMem is unreachable (fetch rejects) — the pre-step
         handler still resolves via `next()` and the provider returns `''`;
      3. recall when the request times out — same outcome, and the timeout, not
         the harness, is what ends the wait;
      4. cache isolation — two different `agent.id`s get their own text and
         neither can read the other's;
      5. retain on turn close — `POST /v1/memories` receives the closed turn's
         user+assistant messages with `session_id === agent.id`;
      6. disposal — every registration's disposer is called and the cache is
         emptied on unload.
- [ ] **AC9 — `npm run typecheck` passes in `dsh-plugin/` with zero errors**, and
      `npm run build` emits both `dist/esm` and `dist/cjs`.
- [ ] **AC10 — The existing repo suite still passes.** `pytest` is green and
      `git diff --stat` shows no change to `sodamem/`, `server/`, `mcp_server/`,
      `sodamem_cli/`, `sodamem_opt/`, `tests/`, or `pyproject.toml`.
- [ ] **AC11 — `{{` sanitisation.** A unit test feeds a SodaMem response whose
      `text` contains `{{something}}` and asserts the contributed context text
      no longer contains a `{{` token — because strict variable interpolation
      over an unresolvable reference fails the whole assembly, i.e. breaks the
      turn.
- [ ] **AC12 — Recall fires once per turn, not once per step.** A test drives
      two `pre-step` calls with the same `agent.id` and the same `turn`, and
      asserts exactly one `GET /v1/context` was issued; a third call with
      `turn + 1` issues a second.
- [ ] **AC13 — Docs, as the final commit and only then.** A separate commit adds
      the plugin row to the `README.md` "Agent integrations" table and to
      `docs/integrations/README.md`, and adds `dsh-plugin/README.md` (install,
      the four config fields, the remote-only requirement, the measured latency
      number or its absence). `DEEPSEEK_HARNESS_INTEGRATION.md` and
      `examples/sodamem-dsh.patch.yml` are byte-identical to `main`.

## Non-goals (out of scope — do not do these here)

- Removing or rewriting `DEEPSEEK_HARNESS_INTEGRATION.md` or
  `examples/sodamem-dsh.patch.yml`. Retiring the MCP guide is a follow-up and is
  blocked on this shipping first.
- Any change to the Python packages (`sodamem/`, `server/`, `mcp_server/`,
  `sodamem_cli/`, `sodamem_opt/`) or to `pyproject.toml`.
- Publishing `dsh-plugin-sodamem` to npm, opening the awesome-dsh-plugin PR, or
  any UI/panel surface.
- Adding a `dsh` target to `sodamem install`.
- Updating the seven translated READMEs under `docs/i18n/`.
