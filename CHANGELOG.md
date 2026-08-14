# Changelog

All notable changes to this repository are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `benchmarking/protocol_v1.0/` — LongMemEval-S **Protocol v1.0** (headline **468/500**):
  question-schema stack, keep-count cardinality, SetEnumeration skill, TR anchors.
- `benchmarking/run_s500.py` — `--range START-END` for sharded 500-question runs.
- `docs/PUBLISHING.md` — what to push to GitHub and how to sync Protocol v1.0.
- `docs/integrations/` — placeholder for PI Agent, Hermes Agent, DeepSeek Harness.

### Changed

- README: headline **93.6% (468/500)**; clarify product vs Protocol layer; agent
  integration table; install URL `github.com/SodaMem/SodaMem`.

### Benchmark notes

| label | score | notes |
|---|---|---|
| Published artifact | 464/500 (92.8%) | `benchmarking/artifacts/` — full reproducible bundle |
| Protocol v1.0 headline | 468/500 (93.6%) | `benchmarking/protocol_v1.0/` — current best protocol |

## [0.0.1] — 2026-03

Initial public tree: evidence-grounded temporal memory, HTTP/MCP/adapters,
LongMemEval harness, published 464/500 artifact run.
