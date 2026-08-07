# Security Policy

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting:
[Report a vulnerability](https://github.com/SodaMem/SodaMem/security/advisories/new).

You should get a first response within **5 working days**. If you have not
heard back in 10, assume the report did not arrive and open a public issue
saying only that you are waiting on a security response — no details.

There is no bug bounty.

## Supported versions

Pre-1.0. Only the latest released version gets fixes; there are no backports
to earlier tags. Once 1.0 ships this section will say something more useful.

## What this project stores

SodaMem is a memory layer, so a deployment holds **raw conversation text** —
every extracted fact is a foreign key back to the turn it came from, which is
the whole design. Assume any store contains whatever your users said.

A store is a SQLite file plus a Chroma index on local disk. There is no
encryption at rest and no field-level redaction: protect the volume the way
you would protect a database holding the same conversations.

## The trust boundary

Things worth knowing before you decide what to report:

- **`user_id` selects the store; it is not an authentication claim.** Any
  caller holding a valid API key can pass any `user_id`. Multi-tenant
  isolation is the store boundary, not the parameter.
- **`agent_id` / `run_id` / `project_id` are provenance, not isolation.** An
  unstamped fact matches every scoped query by design, and any caller can pass
  any value. "Agent B cannot see agent A's memories" is not a property this
  system has.
- **API keys are equal.** Named keys exist for attribution — every request
  records which key made it — not for roles. Any live key can read ops
  endpoints and manage other keys.
- **The MCP write tools are opt-in.** `add_memories` and `delete_memory` are
  registered only under `SODAMEM_MCP_ALLOW_WRITE=true`. A report that a
  model-driven client can call them *with that flag set* is working as
  intended; a way to reach them *without* it is a vulnerability.
- **`/v1/answer` sends retrieved memory to a configured LLM provider.** That
  is the documented behaviour of that route and of `SodaMem.answer()`. The
  zero-LLM routes (`/v1/search`, `/v1/context`) must never make a model call —
  if you find one that does, that is a real finding.

Reports we want: authentication bypass, cross-user data access, path traversal
into another store, injection through ingested content that reaches SQL or the
filesystem, and anything that makes a zero-LLM path talk to the network.
