# Publishing SodaMem

This guide covers releases from [github.com/SodaMem/SodaMem](https://github.com/SodaMem/SodaMem)
and explains how **Protocol v1.0** relates to the package version.

## Repository layers

| Layer | Path | Purpose |
|---|---|---|
| Product | `sodamem/`, `server/`, `mcp_server/`, `adapters/` | Memory engine and public APIs |
| Plan B+ | `sodamem_opt/` | Answer-side deterministic count and time-window patches |
| Benchmark harness | `benchmarking/` | LongMemEval runner, tests, and artifacts |
| Protocol v1.0 | `benchmarking/protocol_v1.0/` | Typed Answer Schema (TAS) answer protocol |

Protocol / TAS is a benchmark answer discipline, not the Python package version. The
Python and TypeScript package version is **0.1.0**, declared in
`pyproject.toml`, `sdk-ts/package.json`, and `uv.lock`.

## Release contents

Ship the complete repository, subject to `.gitignore` and the exclusions below:

```text
sodamem/ sodamem_opt/ sodamem_cli/
server/ mcp_server/ adapters/ console/ sdk-ts/
benchmarking/
tests/ docs/ scripts/ specs/
pyproject.toml uv.lock README.md LICENSE
```

The Protocol v1.0 bundle contains:

- `protocol_v1/*.py` — runtime patches and advisories
- `METHOD.md`, `RESULTS_S500.md`
- `ARCHIVE_S500/summary.json` and `ARCHIVE_S500/answers_all.jsonl`
- `run_protocol_s500.py` — runner
- `README.md` — setup, evaluation, and provenance

## Never commit

| Path / data | Reason |
|---|---|
| Frozen `memory.db` stores or raw third-party datasets | Licensing and size |
| API keys, `.env`, credentials | Security |
| `.venv/`, `__pycache__/`, local result directories | Generated environment |
| Raw provider responses and traces | Privacy and unnecessary size |

The reviewed `ARCHIVE_S500/answers_all.jsonl` is an explicit exception to the
general run-output rule: it contains the 500 final benchmark answer records
needed for re-grading, and no API keys or frozen-store databases.

## Pre-release checks

Do not tag until all of these pass on the exact release commit:

```bash
uv lock --check
uv venv
uv pip install -e ".[dev,chroma,server,mcp,llm,anthropic]"
uv run pytest -q
uv run lint-imports
uv run ruff check sodamem server mcp_server tests benchmarking/tests
uv build
uv pip install twine
uv run twine check dist/*

cd sdk-ts
npm ci
npm test --if-present
npm run build
```

Verify that all version declarations equal `0.1.0`:

```bash
python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
node -p "require('./sdk-ts/package.json').version"
```

## Tag-driven release

The release workflow runs only for tags and verifies that the tag, Python
version, and npm version match:

```bash
git tag -a v0.1.0 -m "SodaMem 0.1.0; Typed Answer Schema (TAS) answer protocol"
git push origin v0.1.0
```

Do not create the tag until CI and the package smoke checks above are green.

## Evaluator setup

1. Install: `pip install -e ".[chroma,llm,answer,server,mcp]"`
2. Supply licensed benchmark data and a frozen store through
   `SODAMEM_BENCH_DATA` and `SODAMEM_BENCH_STORES`.
3. Follow `benchmarking/protocol_v1.0/README.md`.
4. Re-grade the published answers in
   `benchmarking/protocol_v1.0/ARCHIVE_S500/answers_all.jsonl`.
