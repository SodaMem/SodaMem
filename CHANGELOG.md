# Changelog

All notable changes to this repository are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `benchmarking/protocol_v1.0/` — LongMemEval-S **Protocol v1.0** (headline **468/500**):
  question-schema stack, keep-count cardinality, SetEnumeration / TR / slot advisories.
- `sodamem_opt/` — Plan B+ answer-side patches used by Protocol v1.0.
- `HERMES_INTEGRATION.md` / `DEEPSEEK_HARNESS_INTEGRATION.md` — MCP integration guides.
- `examples/sodamem-dsh.patch.yml`, `scripts/sodamem_mcp_warm.py` — DeepSeek Harness helpers.
- `benchmarking/run_s500.py` — `--range START-END` for sharded 500-question runs.
- `docs/PUBLISHING.md` — what to push to GitHub and how to sync Protocol v1.0.

### Changed

- README: headline **93.6% (468/500)**; Agent integrations section links Hermes /
  DeepSeek Harness guides; clarify product vs Protocol layer.

### Benchmark notes

| label | score | notes |
|---|---|---|
| Published artifact | 464/500 (92.8%) | `benchmarking/artifacts/` — full reproducible bundle |
| Protocol v1.0 headline | 468/500 (93.6%) | `benchmarking/protocol_v1.0/` — current best protocol |

## [0.0.1] — 2026-03

Initial public tree: evidence-grounded temporal memory, HTTP/MCP/adapters,
LongMemEval harness, published 464/500 artifact run.
