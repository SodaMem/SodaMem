## What and why

<!-- What changes, and what made it necessary. If this changes behaviour, say
what the OLD behaviour was — a reviewer cannot judge a diff without it. -->

## How it was verified

<!-- Which tests, run how. "Tests pass" is not verification; naming the test
that would have caught the bug is. -->

## Checklist

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check sodamem server mcp_server sodamem_cli tests benchmarking`
- [ ] `uv run lint-imports`

If any box below applies, the PR body should address it — none of them block a
change, they just need saying out loud:

- [ ] Adds or changes a **public API** (anything in a package's `__all__`)
- [ ] Changes what gets **stored**, or the store schema
- [ ] Adds a dependency to the **base install** (a CI gate fails if the base
      list grows)
- [ ] Adds a `/v1/*` route handler — it must be `def`, not `async def`
- [ ] Adds a config field, flag, or environment variable

## On that last one

A knob that only ever gets set to one value is not a choice — it is a default
plus a failure surface, and this project has removed two of them. If you are
adding a flag, say who sets it to the other value and how they reach it.

<!-- Claiming a benchmark improvement? One run per arm proves nothing here:
measured run-to-run spread is around ±13 questions, and a single-run paired
McNemar can return p≈0.05 for no change at all. N≥3 per arm. -->
