# Changelog

All notable changes to this repository are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed

- Answer protocol public name: **Typed Answer Schema (TAS)**
  (`benchmarking/protocol_v1.0/`). Removed per-question entity
  include/exclude packs and brand saturation query packs; kept task-type
  routing and general engineering constraints.
- README / i18n: primary LongMemEval claim is the published **464/500**
  artifact; TAS docs no longer advertise a scrubbed-code 468 headline.

## [0.1.0] — 2026-08-14

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
| Protocol v1.0 (historical archive) | 468/500 (93.6%) | Prior snapshot under `ARCHIVE_S500/` — not current TAS claim |

## [0.0.1] — 2026-03

Initial public tree: evidence-grounded temporal memory, HTTP/MCP/adapters,
LongMemEval harness, published 464/500 artifact run.
