# Publishing to GitHub (SodaMem/SodaMem)

This guide covers what to push to [github.com/SodaMem/SodaMem](https://github.com/SodaMem/SodaMem)
and how **Protocol v1.0** fits in.

## What the repo is

| layer | path in repo | what it is |
|---|---|---|
| **Product** | `sodamem/`, `server/`, `mcp_server/`, `adapters/`, … | Memory engine, APIs, MCP |
| **Plan B+ patches** | `sodamem_opt/` | Answer-side optimizations (deterministic count, time window, …) |
| **Benchmark harness** | `benchmarking/` | `run_s500.py`, tests, artifacts |
| **Protocol v1.0** | `benchmarking/protocol_v1.0/` | LongMemEval answer protocol (468/500 headline) |

**Protocol v1.0 is not the Python package version.** Package version lives in
`pyproject.toml` (`0.0.1` today). Protocol v1.0 is a **benchmark protocol
tree** copied from internal `Version/v1.0/`.

## What to upload (include)

Push the **entire product tree** from this directory (`SodaMem-dev-main`), minus
ignored paths:

```
sodamem/ sodamem_opt/ sodamem_cli/
server/ mcp_server/ adapters/ console/ sdk-ts/
benchmarking/          # includes protocol_v1.0/
tests/ docs/ scripts/ specs/
pyproject.toml uv.lock README.md LICENSE …
```

**Protocol v1.0 bundle** (already under `benchmarking/protocol_v1.0/`):

- `protocol_v1/*.py` — runtime patches
- `skill/set_enumeration/` — MR enumeration skill
- `METHOD.md`, `RESULTS_S500.md`, `ARCHIVE_S500/summary.json`
- `run_protocol_s500.py` — runner
- `README.md` — how to reproduce

## What NOT to upload

| never commit | why |
|---|---|
| `data/`, frozen `memory.db` stores | third-party corpus / huge |
| `benchmarking/results/`, `*.jsonl`, run output | generated, gitignored |
| `api/.env`, secrets, API keys | security |
| `.venv/`, `__pycache__/`, local `results/` | environment |
| `Version/` whole tree from Agent Memory Project | internal experiment archive; only **protocol_v1.0** subset is vendored into `benchmarking/` |

Optional: publish full `ARCHIVE_S500/answers_all.jsonl` as a release asset
(not in git) if you want downloadable 468-run answers like `benchmarking/artifacts/`.

## How to push an update

```bash
cd project/SodaMem-dev-main   # or your clone of SodaMem/SodaMem

git remote add origin https://github.com/SodaMem/SodaMem.git   # once
git checkout -b release/protocol-v1.0   # or commit on main
git add README.md CHANGELOG.md benchmarking/protocol_v1.0/ docs/
git status   # verify no .env, no answers.jsonl, no memory.db
git commit -m "docs: Protocol v1.0 headline 468/500; update README"
git push -u origin HEAD
```

Tag when ready:

```bash
git tag -a v0.1.0 -m "Protocol v1.0 benchmark bundle; headline 468/500"
git push origin v0.1.0
```

## After cloning (for evaluators)

1. `pip install -e ".[chroma,llm,answer,server,mcp]"`
2. Point env vars (`SODAMEM_BENCH_DATA`, `SODAMEM_BENCH_STORES`) — see
   `benchmarking/README.md`
3. Run Protocol v1.0: `benchmarking/protocol_v1.0/README.md`

## Syncing from Agent Memory Project

When internal `Version/v1.0/` changes:

```powershell
$src = "...\Agent Memory Project\Version\v1.5"
$dst = "...\SodaMem-dev-main\benchmarking\protocol_v1.0"
Copy-Item -Recurse -Force "$src\protocol_v1" "$dst\protocol_v1"
Copy-Item -Recurse -Force "$src\skill" "$dst\skill"
Copy-Item -Force "$src\METHOD.md","$src\RESULTS_S500.md" "$dst\"
Copy-Item -Force "$src\run_union_x.py" "$dst\run_protocol_s500.py"
```

Do **not** copy `results_*`, `_heartbeats`, or `answers.jsonl`.
