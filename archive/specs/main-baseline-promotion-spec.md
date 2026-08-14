# Spec: Promote the accepted answer baseline and enforce repository boundaries

Record: main baseline promotion / directory-boundary cleanup

## Problem

`origin/main` is still at `9c31ccd4a0bc96fb54370e9034ed6f7c49085a87`,
while `codex/main-baseline-cleanup` starts at
`e7fe1ef19426c10525ec893bd86d6a229786e226`, a 42-commit fast-forward that
contains the accepted c3 answer baseline, the Reader duplicate-label fix, and
the promoted Planner context-offload default. Those accepted changes must
become the public main baseline without accidentally importing the rejected
Issue #15 R20/R20-v2 or Issue #16 deterministic-organizer experiments.

The tracked tree also mixes benchmark-only specifications, harness tests, and
one machine-specific LongMemEval cleanup utility into `.claude/`, `tests/`,
and `scripts/`. This makes product ownership unclear and lets the default
Python test configuration silently omit benchmark tests once they are moved.
Promotion must therefore establish a durable boundary: benchmark experiments,
data tooling, artifacts, and harness tests live under `benchmarking/`; product
directories contain runtime code, general documentation, and tests of product
contracts.

The primary worktree at `/Users/aaron.w/Desktop/SodaMem` contains unrelated
user changes. It is not the integration worktree and must not be checked out,
reset, cleaned, stashed, staged, committed, or otherwise modified during this
work.

Candidate `a6f35a3e0440257fff47b11dc6c99f9dc87f181c` completed the boundary
cleanup but failed the clean-distribution and Docker gates. Its approvals do
not authorize promotion. The replacement candidate must also repair the
distribution and CI dependency blockers specified below and repeat every gate
on its new exact hash.

Candidate `c9f770ba9a4d90d0f6fa6f00ac6be94028300dce` repaired those first
blockers but still received `BUILD_FAIL`: its CI-shaped clean full-test install
omits the declared `llm` extra even though moved benchmark modules import the
`openai` SDK during collection, and its source distribution contains test
payload forbidden by AC6. Its approvals are likewise invalid. The next
candidate is limited to the smallest CI dependency, benchmark-namespace, and
source-manifest repairs defined below, followed by a complete rerun on the new
exact hash.

## Value

Main will expose the reviewed lower-cost answer baseline with its correctness
fix and promoted context offload, while retaining a tree whose paths explain
ownership. Pytest will continue to collect every product and benchmark test,
historical benchmark evidence remains reproducible, rejected experiments stay
out, and the reviewed integration commit can fast-forward `origin/main`
without touching the user's dirty primary worktree.

## Scope

Integration branch and worktree:

- branch: `codex/main-baseline-cleanup`
- worktree: `/Users/aaron.w/Desktop/SodaMem-worktrees/main-reconcile`
- starting HEAD: `e7fe1ef19426c10525ec893bd86d6a229786e226`
- starting remote main: `9c31ccd4a0bc96fb54370e9034ed6f7c49085a87`

Required benchmark-spec moves, preserving content and Git history:

- `.claude/specs/issue-1-lme-correctness-spec.md` ->
  `benchmarking/specs/issue-1-lme-correctness-spec.md`
- `.claude/specs/issue-7-planner-context-offload-spec.md` ->
  `benchmarking/specs/issue-7-planner-context-offload-spec.md`
- `.claude/specs/issue-8-context-offload-baseline-spec.md` ->
  `benchmarking/specs/issue-8-context-offload-baseline-spec.md`

Required benchmark-test moves:

- `tests/test_benchmarking_context_offload.py` ->
  `benchmarking/tests/test_benchmarking_context_offload.py`
- `tests/test_benchmarking_load_arm.py` ->
  `benchmarking/tests/test_benchmarking_load_arm.py`
- `tests/test_benchmarking_rawtrace.py` ->
  `benchmarking/tests/test_benchmarking_rawtrace.py`
- `tests/test_benchmarking_resume.py` ->
  `benchmarking/tests/test_benchmarking_resume.py`
- `tests/test_benchmarking_talkdown.py` ->
  `benchmarking/tests/test_benchmarking_talkdown.py`
- `tests/test_benchmarking_trace.py` ->
  `benchmarking/tests/test_benchmarking_trace.py`
- `tests/test_benchmarking_votes.py` ->
  `benchmarking/tests/test_benchmarking_votes.py`
- `tests/test_cleanup_hobs_audit_bundles.py` ->
  `benchmarking/tests/test_cleanup_hobs_audit_bundles.py`

Required benchmark-utility move:

- `scripts/cleanup_hobs_audit_bundles.py` ->
  `benchmarking/scripts/cleanup_hobs_audit_bundles.py`

Supporting changes are limited to:

- `pyproject.toml`: collect both `tests` and `benchmarking/tests` by default;
- moved test imports, repository-root calculations, and subprocess working
  directories required by the new paths;
- imports/references to the moved cleanup utility, using its new
  `benchmarking.scripts.cleanup_hobs_audit_bundles` location;
- references to the three moved specs or moved tests in tracked documentation,
  tooling, and CI;
- `benchmarking/README.md`: document the ownership boundary, test command,
  and artifact/data policy;
- package marker files under `benchmarking/tests` or `benchmarking/scripts`
  only if they are required for stable imports.

Promotion-blocker fixes additionally permit changes only to:

- `pyproject.toml` and `uv.lock`: constrain the MCP SDK to the supported 1.x
  API and package `server*` in the distribution;
- `MANIFEST.in`: explicitly exclude test, benchmark, data, result, and local
  artifact payload from the source distribution while preserving all required
  product sources and declared build/readme/license metadata;
- new or focused changes to `tests/test_distribution_contracts.py` for
  dependency, CI-install, wheel, and source-manifest invariants, plus focused
  additions to `tests/test_mcp_server.py` or `tests/test_server_routes.py` only
  where needed to pin installed MCP imports and fail-secure server
  configuration;
- `Dockerfile`: copy every packaged Python source directory into the builder
  before the non-editable install and correct the packaging/layout comments;
- `docker-compose.yml`: make `.env` optional for Compose configuration while
  retaining secure server startup defaults;
- `.github/workflows/ci.yml`: install the declared `llm`, MCP, and server
  extras in the full Python test job so default collection can import every
  tested surface;
- benchmark namespace markers or import-path corrections under
  `benchmarking/` only if the required clean-environment diagnosis proves they
  are the smallest fix for the independent namespace failure; they must not
  make `benchmarking` part of the wheel or source distribution;
- README text directly affected by the packaging or optional-Compose-file
  behavior.

Out of scope:

- changing accepted product algorithms, prompts, defaults, public APIs,
  unrelated dependency versions, benchmark numbers, or historical spec
  content. The MCP `<2` compatibility ceiling and lock refresh below are the
  sole dependency exception;
- replacing the explicit CI extra set with `.[all]`, `--all-extras`, or another
  broad install merely to conceal which tested surface requires a dependency;
- adding `benchmarking*` to setuptools product-package discovery or shipping
  benchmark tests, harnesses, specs, results, or data in wheel/sdist artifacts;
- adding any R20, R20-v2, deterministic-organizer, adaptive Reader, or other
  rejected/unfinished experiment;
- running paid benchmarks or making provider, judge, retrieval-service, or
  external data calls;
- rewriting product tests merely because their comments cite LongMemEval or
  benchmark provenance;
- modifying, staging, stashing, cleaning, or switching the primary worktree;
- force-pushing, rewriting `origin/main`, merging an unreviewed commit, or
  pushing before all gates and independent acceptance pass.

## Product/benchmark ownership rule

A file is benchmark-owned when its purpose is to run, analyze, clean, specify,
or verify a benchmark harness or benchmark dataset. It belongs under
`benchmarking/`. A product-owned file implements or verifies a reusable
SodaMem runtime/API contract; benchmark-derived provenance in comments or
self-contained fixtures does not make it benchmark-owned.

The following remain product code/tests:

- `sodamem/answer/agent_guidance.py` and the c3 runtime mechanisms for stall
  stopping, truncation retry, cache layout, short IDs, capability autocall,
  claim-evidence autofill, and count-roster behavior;
- `sodamem/answer/context_offload.py`, its integration in `loop.py` and
  `protocol.py`, and `tests/test_answer_context_offload.py`;
- the Reader duplicate-label fix in `sodamem/context/store.py` and
  `tests/test_reader_label_redundancy.py`;
- `tests/test_answer_defaults.py`, the other `tests/test_answer_*.py` files,
  `tests/test_tools_count_roster.py`, and general provider accounting tests
  such as `tests/test_llm_cache_hit_tokens.py` and
  `tests/test_llm_served_model.py`;
- product loop/context parity tests, ingest/storage/server/SDK tests, runtime
  prompts such as `sodamem/prompts/reader_con.py`, and product design docs,
  even where they record benchmark origins. Their purpose is to protect a
  reusable product contract rather than operate the benchmark harness.

The Hobs cleanup utility is different: its authorized absolute root, fixed
LongMemEval time window, and expected question-store topology make it
benchmark-data operations code, so both it and its tests move.

## Implementation Path

### 1. Pin the accepted baseline and rejected exclusions

Before editing, record the starting refs and require a clean integration
worktree. Prove that the branch is a fast-forward descendant of the observed
`origin/main` and contains these accepted commits:

- c3 promoted baseline: `bafca689c727ee51055b48c2f5e094b07cd6efa8`;
- Reader duplicate fix: `be621bc76d556cfc2708b8d899c6ef9e44ae181b`;
- promoted context offload: `e7fe1ef19426c10525ec893bd86d6a229786e226`.

The cleanup commit must not change product behavior relative to `e7fe1ef`.
The Issue #15 commits `bb4e5abd`, `6401723`, and `20502e2`, and the Issue #16
commit `c4cba4d`, must not be ancestors of the reviewed branch. Audit product
code, benchmark harness code, and tests for the rejected feature surfaces:
selected-first/top-20 Reader configuration, R20-v2 provenance ranking, and
deterministic organizer routing. A historical mention in this specification
or a rejection report is allowed; executable configuration, environment
flags, telemetry, tests, or defaults are not.

Pin the accepted semantics with existing tests: `PlannerConfig()` retains the
c3 defaults and `context_offload=True`; the explicit off override remains;
Reader source-label deduplication remains fixed. Do not cherry-pick from the
Issue #15 or #16 branches to obtain documentation or test helpers.

### 2. Move benchmark-owned files without losing coverage

Perform the exact moves in Scope. Preserve the text of the three historical
specs except for path references that would otherwise be broken. Preserve all
test functions and assertions; a directory cleanup is not permission to
delete, skip, weaken, xfail, or rename coverage.

For each moved `test_benchmarking_*.py`, replace the old assumption that its
parent's parent is the repository root. From `benchmarking/tests`, the
benchmark module directory is `Path(__file__).resolve().parent.parent`; add
that directory to `sys.path` where the harness's current flat imports require
it. Any subprocess that expects repository-relative `benchmarking/...` paths
must instead use `Path(__file__).resolve().parents[2]` as its working directory.
In particular, the context-offload configuration smoke must continue to run
from the repository root so `sys.path.insert(0, "benchmarking")` resolves the
same `run_s500.py` as before.

Update the cleanup test to import
`benchmarking.scripts.cleanup_hobs_audit_bundles`. Keep the utility directly
executable at its new path and update every command/reference from
`scripts/cleanup_hobs_audit_bundles.py` to
`benchmarking/scripts/cleanup_hobs_audit_bundles.py`.

Change pytest configuration to:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "benchmarking/tests"]
asyncio_mode = "strict"
```

The default `pytest` command and CI's existing `pytest -v` must therefore run
both suites. Capture a pre-move collection manifest at `e7fe1ef` and compare it
with the post-move manifest after normalizing the moved path prefixes. Every
old node ID must have exactly one successor, and the total collected count may
not decrease.

### 3. Enforce a tracked-tree and artifact/data policy

`benchmarking/README.md` must state:

- harnesses, experiment specs, benchmark-only tests, dataset maintenance
  utilities, manifests, reports, and comparison visualizations live under
  `benchmarking/`;
- raw datasets, OBS/Chroma/SQLite stores, provider responses, `answers.jsonl`,
  raw traces, secrets, `.env` files, and locally generated run directories are
  never committed;
- `benchmarking/results/` remains ignored for new run output. The already
  tracked compact Issue #7 census JSON may remain as reviewed, non-secret,
  aggregate evidence; any future exception requires explicit review and must
  be compact, sanitized, reproducible, and free of raw conversations,
  credentials, and machine-local mutable data;
- source manifests/ID lists may be tracked only when licensing and privacy are
  explicit and the file contains no conversation/evidence payload;
- benchmark code may import product code, but product code must not import
  `benchmarking`.

Audit the final tracked tree with `git ls-files`, not an untracked-files walk.
The following old paths must be absent: the three moved `.claude/specs` files,
all `tests/test_benchmarking_*.py`,
`tests/test_cleanup_hobs_audit_bundles.py`, and
`scripts/cleanup_hobs_audit_bundles.py`. Their new paths must be tracked.
Search tracked files for stale old-path references. Search product/runtime
imports for `benchmarking` and require none. Cache directories, build output,
paid-run outputs, raw traces, stores, and local environment files must remain
untracked.

Classify by purpose rather than substring: benchmark provenance in a runtime
docstring, a self-contained regression fixture, or a product design document
does not fail the boundary audit. Any newly discovered file whose operational
purpose is tied to LongMemEval/S500 or a benchmark harness must either move
under `benchmarking/` in this change or block acceptance with an explicit Iris
decision; it may not be silently allowlisted.

### 4. Repair the clean-distribution and Docker blockers

#### Clean CI dependency and benchmark namespace contract

The full Python test job is an explicit integration-test environment, not a
base-library install. Because default collection now includes
`benchmarking/tests`, and those tests import benchmark modules that import
`openai` at module scope, its clean editable install must be exactly:

```text
uv pip install -e ".[dev,chroma,llm,mcp,server]"
```

Keep the base-dependency and layering jobs scoped as they are. Do not use
`--all-extras` as the CI repair: a clean all-extras run passing is useful
comparison evidence, but it masks whether CI declares the specific surfaces
its test collection imports. Extend the focused distribution contract test to
pin the explicit `llm` extra in the full-test job and to reject the obsolete
install without `llm`.

The reported `benchmarking` namespace import failure is a separate defect from
the missing `openai` dependency. Reproduce it in a new environment from a
clean archive/export of the exact candidate using the same working directory,
editable install command, and pytest command as CI; capture the failing
node/import and `sys.path`, then compare with a clean `uv sync --all-extras`
run, which is known to pass. Determine whether the
cause is pytest package-root discovery, a missing namespace/package marker, or
an incorrect moved import/root calculation. Fix only that diagnosed cause.
Acceptance requires both direct import of
`benchmarking.scripts.cleanup_hobs_audit_bundles` from the repository root and
default clean-CI collection/full test execution to pass without ad hoc
`PYTHONPATH`, per-machine path injection, test skipping, import fallbacks, or
shipping `benchmarking` as a product package. Record the reproduced failure,
root cause, minimal correction, and clean rerun; the passing all-extras result
is corroborating evidence, not a substitute for the CI-shaped gate.

#### Source-distribution contents contract

AC6 applies independently to both built artifacts. The wheel and source
distribution may contain only the intended product packages and required
distribution metadata; neither artifact may contain `tests/`,
`benchmarking/`, benchmark specs/harnesses/results/data, caches, local stores,
secrets, or generated build output. Add an explicit `MANIFEST.in` source-file
policy rather than relying on setuptools' automatic sdist test inclusion.

The manifest exclusions must preserve every Python source and typed marker
needed by `sodamem*`, `mcp_server*`, and `server*`, plus `pyproject.toml`, the
top-level `README.md`, `LICENSE`, and build-generated metadata required to
install the sdist. Build wheel and sdist from a clean exported candidate tree,
inspect each file list separately, install each artifact in its own clean
environment outside the checkout, and run the applicable base and
`[mcp,server]` imports/entry-point smokes. A test that checks only setuptools
package discovery is insufficient: add a focused assertion over the actual
built archive contents or an equivalent isolated build-contract check that
would fail if `tests/` or `benchmarking/` re-enters the sdist.

#### MCP compatibility and lock discipline

The current implementation and tests use the MCP 1.x `FastMCP` API, and the
reviewed local lock resolves `mcp==1.28.1`. Express that supported contract in
the `mcp` extra as `mcp>=1.9.0,<2`; keep its existing dependency on
`sodamem[server]`. Regenerate `uv.lock` from `pyproject.toml` and require
`uv lock --check`/locked sync to pass. Add a focused test that reads project
metadata and proves the upper bound exists, and a clean installed-extra smoke
that proves the resolved MCP major version is 1 and the existing MCP server
imports/builds against it.

The CI full-test job must install `.[dev,chroma,llm,mcp,server]` before running
default pytest collection. Keep the separate base-only and layering jobs at
their narrower dependency boundaries.

Do not opportunistically port to MCP 2 in this cleanup. A 2.x adaptation is
allowed only if official API material available locally with the installed
distribution demonstrates the replacement `FastMCP` imports, tool-call
contract, errors, and startup behavior, and focused tests prove semantic
parity. Such an adaptation expands the public compatibility surface and must
receive an explicit spec amendment and fresh review before implementation;
absent that evidence and amendment, `<2` is required.

#### Wheel and source-layout contract

Change setuptools discovery from `sodamem*`/`mcp_server*` to include
`server*` as well. Do not add `benchmarking`, `tests`, console source, local
data, or build output to the wheel or sdist. Build both artifacts, inspect
their file lists separately, then install each with `[mcp,server]` into its own
new environment outside the repository with `PYTHONPATH` unset. From a working
directory that cannot import the source checkout, all of these must succeed:

```text
import sodamem
import mcp_server
import mcp_server.main
import server
import server.app
```

The installed distribution must expose the `sodamem-mcp` entry point, resolve
MCP 1.x, and import/build the MCP and HTTP applications without relying on
repository-local `mcp_server/` or `server/` directories.

Because package discovery now includes `mcp_server` and `server`, the Docker
builder must `COPY` all three packaged source roots (`sodamem/`,
`mcp_server/`, and `server/`) before `uv sync --frozen --no-dev
--no-editable`. Keep the runtime-stage `/app/server` source copy: it
intentionally places `server/console_mount.py` at `/app/server`, so its
parent-relative console contract continues to resolve `/app/console/dist`.
Update the Docker comments to explain that this source copy shadows the
installed `server` package for the container layout; it is no longer evidence
that `server` is absent from the wheel. Do not move or duplicate console
assets into site-packages and do not change the console mount algorithm.

#### Optional Compose `.env`, secure runtime

Use the Docker Compose 2.38-supported long syntax exactly in the service:

```yaml
env_file:
  - path: .env
    required: false
```

This makes `docker compose config` and image-oriented validation work in a
clean checkout without a `.env`. It does not supply credentials and must not
set `SODAMEM_AUTH_DISABLED`, an empty/default API key, or any permissive auth
fallback. Preserve the documented operator path (`cp .env.example .env`) and
the server invariant that startup fails when neither a non-empty
`SODAMEM_API_KEY` nor explicit auth-disabled development mode is provided.
Add/retain focused configuration tests for that fail-secure behavior and
validate Compose from a clean exported tree that has no `.env`.

#### Required static Docker contract; optional image evidence

SodaMem's promoted core/library baseline must not depend on a local Docker
daemon. From a clean archive/export of the exact candidate commit, with no
`.env`, Mercury must run the blocking static/configuration gate:

```text
docker compose config
```

It must also inspect the Dockerfile and exported source layout to prove that
the builder copies every packaged Python source root (`sodamem`, `mcp_server`,
and `server`), performs the frozen non-editable install, and preserves the
runtime `/app/server` plus `/app/console/dist` relationship required by the
existing console mount. Compose validity, these source-layout checks, and the
focused fail-secure authentication tests are mandatory promotion gates.

Real image execution is additional, non-blocking evidence for the optional
self-host deployment. Mercury should run `docker info`; if the daemon is not
available and a normal Docker Desktop/daemon restart is possible, perform one
normal restart and a bounded wait/retry. When the daemon is available, attempt:

```text
docker build --no-cache --progress=plain -t sodamem:main-baseline-<hash> .
```

For a successful build, smoke the resulting image by importing `sodamem`,
`mcp_server`, and `server.app`; record daemon/server versions, image ID/digest,
commands, and outcomes. An isolated startup/build-app check without API key or
auth-disabled settings must still fail for the documented authentication
reason; the existing explicit auth-disabled development/health smoke may then
run if that is the repository's documented test path.

Report the optional image evidence separately as `VERIFIED`, `FAILED`, or
`UNVERIFIED_DAEMON_UNAVAILABLE`. In particular, a daemon still unavailable
after the normal restart is
`OPTIONAL_SELF_HOST_IMAGE_UNVERIFIED: daemon unavailable after restart`; it is
not a skipped core dependency and does not produce `BUILD_BLOCKED` for this
baseline-to-main promotion. Likewise, inability to complete the real image
build must not be represented as Docker verification, but it does not replace
or fail the mandatory core/library gates. The local validation image may be
removed after its digest is recorded; no container/image output is staged.

These fixes address packaging, test-environment provisioning, and deployment
only. The diff from `a6f35a3` must not change answer, retrieval, evidence,
Reader, Planner, benchmark, API route semantics, or the directory boundary
already established by that candidate.

### 5. Verify the complete promoted product

No paid benchmark is required. Run these non-provider gates on the final
candidate commit:

1. focused moved tests and baseline pins:
   `uv run pytest -q benchmarking/tests tests/test_answer_defaults.py tests/test_answer_context_offload.py tests/test_reader_label_redundancy.py`;
2. collection comparison and full Python suite:
   `uv run pytest --collect-only -q` and `uv run pytest -q`;
3. dependency and Python quality/contracts: `uv lock --check`, a frozen/locked
   sync, `uv run ruff check .`, `uv run lint-imports`, and
   `uv run python scripts/check_base_deps.py` in a base-only environment;
4. distributable product: build wheel/sdist from a clean export; separately
   prove both omit `tests/`, `benchmarking/`, and data/artifact payload while
   retaining the three product roots plus required pyproject/readme/license
   metadata; install each artifact with `[mcp,server]` in its own clean
   environment outside the checkout, assert MCP major 1, smoke-import
   `sodamem`, `mcp_server`, and `server.app`, and exercise `sodamem-mcp`;
5. TypeScript SDK from `sdk-ts`: `npm ci`, `npm run typecheck`, `npm test`, and
   `npm run build`;
6. web console from `console`: `npm ci`, `npm run lint`, and `npm run build`;
7. Docker contract from a clean exported candidate tree with no `.env`: run
   `docker compose config`, inspect the Dockerfile/source layout, and prove
   missing credentials still fail securely. Separately attempt the local
   daemon/no-cache image build and image imports as non-blocking optional
   self-host evidence, recording its explicit disposition.
8. CI-shaped Python integration environment: from a clean export of the exact
   candidate, install `.[dev,chroma,llm,mcp,server]` in a fresh environment,
   run deterministic default collection and the full suite, and prove the
   direct `benchmarking.scripts.cleanup_hobs_audit_bundles` import works from
   repository root without `PYTHONPATH`. Record the diagnosed cause of the
   prior namespace failure. Also record a clean all-extras pass as comparison
   evidence, never as a replacement for this explicit-extra gate.

If a required core/configuration tool or runtime is unavailable, including the
Compose CLI needed for `docker compose config`, the gate is blocked, not
silently skipped. A local Docker daemon is not such a required runtime; after
the restart attempt above, record its unavailability as an unverified optional
self-host artifact. Existing platform-conditional pytest skips are acceptable
only when they predate this cleanup and the test's own contract explicitly
permits the skip. Record commands, versions, pass counts, skips, artifact
hashes, and the optional image disposition in Mercury's build handoff.
Generated wheels, console/SDK build output, caches, and Docker-local material
must not be staged.

### 6. Independent gates and fast-forward delivery

Commit the blocker fixes on `codex/main-baseline-cleanup`; the previous
`a6f35a3` and `c9f770b` gate results are historical failure evidence, not
reusable approval.
The worktree must be clean after the new commit. Mercury reruns and records all
gates on that exact new commit; Argus independently reviews the full
`origin/main..candidate` product diff plus `a6f35a3..candidate`; Iris
independently checks every acceptance criterion. A code author's self-review
does not replace either gate.

Only after Mercury BUILD_PASS, Argus approval, and Iris ACCEPTANCE_PASS, fetch
the remote again. Let `R` be the exact reviewed commit and `M` the refreshed
`origin/main`. Require `git merge-base --is-ancestor M R`. If this is false,
stop: reconcile onto the new main and repeat build, review, and acceptance on
the new commit. Never force-push.

Fast-forward the remote without switching or modifying the dirty primary
worktree by pushing the reviewed integration ref directly:

```text
git push origin R:refs/heads/main
```

Use the explicit reviewed hash or the verified integration branch ref for
`R`; do not push an ambiguous working-tree state. Verify the remote main hash
equals `R` after the push. Do not run `git checkout`, `git reset`, `git clean`,
`git stash`, `git add`, or `git commit` in
`/Users/aaron.w/Desktop/SodaMem`. Updating that worktree's local checkout may
be handled separately by its owner after the unrelated dirty files are safe.

## Acceptance Criteria

- [ ] AC1: The candidate is a clean fast-forward descendant of refreshed
  `origin/main`, contains accepted commits `bafca68`, `be621bc`, and `e7fe1ef`,
  and changes no answer/retrieval/Planner/Reader/API-route semantics relative
  to `e7fe1ef`; only the specified packaging/dependency/CI/Compose
  compatibility repairs extend `a6f35a3`, and only the authorized explicit
  CI-`llm`, diagnosed benchmark-namespace, and sdist-manifest repairs extend
  `c9f770b`. Rejected Issue #15/#16 commits and executable feature surfaces
  are absent.
- [ ] AC2: The three benchmark experiment specs, eight benchmark/data tests,
  and Hobs cleanup utility exist only at the exact new paths in Scope. All
  stale old-path imports, commands, and documentation references are removed.
- [ ] AC3: Pytest defaults collect `tests` and `benchmarking/tests`. After
  normalizing the moved prefixes, every pre-move node ID has exactly one
  post-move successor, no assertion/test is weakened or skipped by the move,
  and the total collected test count does not decrease.
- [ ] AC4: The final tracked-tree audit finds no benchmark-operational file
  outside `benchmarking/`, no product import of `benchmarking`, no generated
  or raw benchmark/store/secret artifact tracked, and no unreviewed exception.
  Product files that merely document benchmark provenance remain in place
  based on their reusable runtime purpose. `benchmarking/README.md` documents
  the boundary and artifact/data policy, and moved historical evidence retains
  its content/provenance.
- [ ] AC5: Existing tests pin the accepted c3 defaults, Reader duplicate fix,
  promoted `context_offload=True`, and explicit off rollback. No R20/R20-v2 or
  deterministic-organizer code, configuration, benchmark arm, or test is
  introduced.
- [ ] AC6: Project metadata constrains `mcp>=1.9.0,<2`, `uv.lock` is current
  and resolves MCP 1.x, and tests pin both facts. Wheel and sdist each contain
  `sodamem*`, `mcp_server*`, and `server*` but no benchmark/test/data/artifact
  payload; the sdist preserves `pyproject.toml`, top-level `README.md`,
  `LICENSE`, and required product/build metadata. Independent clean
  out-of-tree `[mcp,server]` installs of both artifacts import all three
  packages and expose the MCP entry point without source-checkout leakage. The
  CI full-test job explicitly installs `.[dev,chroma,llm,mcp,server]`, while
  its base and layering jobs retain their scoped installs. The previously
  reported benchmark-namespace failure has a recorded root cause and minimal
  fix; direct repository-root namespace import plus CI-shaped collection/full
  tests pass without `PYTHONPATH`, skips, or packaging `benchmarking`. A clean
  all-extras pass is recorded only as comparison evidence. Docker's builder
  copies every packaged source root, while runtime `/app/server` and
  `/app/console/dist` preserve the existing console-mount layout.
- [ ] AC7: Compose uses optional `.env` long syntax and `docker compose config`
  passes from a clean tree with no `.env`, while missing credentials still
  fail server startup securely. Static Dockerfile/source-layout inspection
  proves every packaged Python source root is copied and the runtime console
  mount relationship is preserved. A real no-cache image build and image
  import smokes are attempted and reported separately as non-blocking optional
  self-host evidence; daemon unavailability after a normal restart is recorded
  explicitly as `OPTIONAL_SELF_HOST_IMAGE_UNVERIFIED`, never as a skipped core
  dependency. Focused and full Python tests, Ruff, import/base gates, clean
  wheel `[mcp,server]` distribution smokes, SDK gates, and console gates remain
  mandatory and pass with commands/results recorded and no generated output
  staged.
- [ ] AC8: The integration worktree is clean and contains only reviewed,
  committed changes. The primary `/Users/aaron.w/Desktop/SodaMem` worktree's
  unrelated dirty files are untouched; no destructive or state-changing Git
  command was run there.
- [ ] AC9: Mercury reports BUILD_PASS, Argus independently approves the exact
  candidate, and Iris reports ACCEPTANCE_PASS after inspecting the diff and
  gate evidence. Any post-review commit invalidates these approvals and
  requires all three gates again.
- [ ] AC10: After a final fetch proves fast-forward ancestry, the exact reviewed
  commit is pushed to `refs/heads/main` without force, remote main is verified
  at that hash, and no local primary-worktree checkout is changed to perform
  the delivery.

## Open Questions

None. The accepted baseline, rejected experiments, ownership rule, exact file
moves, verification matrix, dirty-worktree protection, and fast-forward
delivery procedure are fixed.
