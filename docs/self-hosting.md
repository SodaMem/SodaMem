# Self-hosting operations reference

The [main README](../README.md#self-hosting) covers the one-command quick
start, the auth default, and the single-worker constraint. This is
everything past that: calling the API, admin endpoints, metrics, maintenance,
backups and upgrades.

Running `docker compose up -d` builds the image, starts the server on
`http://localhost:8000`, serves the web console at
`http://localhost:8000/console`, and persists all data in a named Docker
volume (`sodamem-data`, mounted at `/data` inside the container) — nothing is
written to the host filesystem directly, and nothing survives only in the
container's writable layer.

The console is compiled inside the image (a dedicated `console-builder`
stage), so nothing on the host needs Node installed. Running the server
outside Docker is different: `console/dist` won't exist until you run
`npm install && npm run build` in `console/`, and until then the API starts
normally and just logs that the console isn't mounted.

Every other knob (`SODAMEM_LLM_PROVIDER`, `SODAMEM_STORE_CACHE_MAX`,
`SODAMEM_CORS_ORIGINS`, ...) is documented with defaults in `.env.example`.

## Calling it

```
# liveness — unauthenticated, touches no store
curl http://localhost:8000/health
# {"status":"ok","version":"0.0.1","schema_version":1,"auth":"enabled"}

# a real endpoint needs the API key (Authorization: Bearer, or X-API-Key)
curl http://localhost:8000/v1/search \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SODAMEM_API_KEY" \
  -d '{"user_id":"alice","query":"favorite color"}'
```

Full route list and request/response shapes are in `server/models.py` and
served live at `/docs` (Swagger UI) once the container is up.

## Operating it

`/v1/admin/*` answers the questions that otherwise need a shell inside the
container. The web console's **Ops** page is the same data with a UI.

```
# effective configuration — every secret reported as set/not-set, never masked
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/config

# mint a named key; the plaintext is returned ONCE and is not recoverable
curl -X POST -H "Authorization: Bearer $SODAMEM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-pipeline"}' localhost:8000/v1/admin/keys

# who called what, most recent first (rolling window, not an archive)
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/requests

# disk + workload shape
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/stats
```

Named keys exist for **attribution**, not isolation: every request records
which key made it, so "who is hammering /v1/search" has an answer. There are
no roles or per-key scopes — any live key can read ops data and manage other
keys. `SODAMEM_API_KEY` keeps working exactly as before and cannot be revoked
through the API, which makes it the way back in if every named key is
revoked.

## Latency and cost

Both are instruments, not numbers we ask you to take on faith.

```
# per-route latency percentiles over this process's recent requests
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/metrics

# cumulative LLM token spend, split by ingest vs answer
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/usage
```

Both are in-process and reset on restart — counters for "what is this
deployment doing right now", not a billing record. For anything that has to
outlive a restart, scrape the Prometheus endpoint instead:

```
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/metrics
```

It exposes request counts (a true monotonic counter, not the latency ring),
duration quantiles in seconds, and cumulative LLM tokens per operation. It
sits behind the same API key as everything else, so a scrape config needs a
`bearer_token`. There is no OpenTelemetry exporter: OTel earns its cost on
distributed tracing, and this is one process — scraping covers the actual
need at zero added dependencies. Routes with no traffic are
**absent** rather than reported as zero, because a zero reads as "this is
instantaneous" or "this is free".

We do not publish a headline latency number: it depends on hardware, store
size, embedder and concurrency to a degree that makes a single figure close to
meaningless. Measure it on your own deployment with `/v1/metrics`.

For cost, `/v1/usage` splits ingest from answer deliberately: ingest is
output-token heavy and answer is input-heavy, so a single total hides the only
comparison worth making.

## Maintenance

Entity profiles are rebuilt on demand, never on a timer — SodaMem ships no
scheduler, because when to spend those tokens is a deployment decision:

```
curl -H "Authorization: Bearer $SODAMEM_API_KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/maintenance/dream -d '{"user_id":"u1","async":true}'
```

Idempotent, resumable, and safe to overlap: a second call while one is running
returns `status:"already_running"` and does nothing. The response carries
`remaining_stale`, so a cron entry that just runs every hour converges without
anyone having to pick a batch size.

## Data

All state lives under the `sodamem-data` volume: per-user stores, the Chroma
vector index, and the control-plane database (`/data/.control/`) holding job
records, API keys and the request log. Job status now survives a restart —
`GET /v1/jobs/{id}` no longer answers 404 for a job that was in flight during
a deploy. Back it up like any other named volume:

```
docker run --rm -v sodamem-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/sodamem-data.tar.gz -C /data .
```

## Upgrading

```
git pull
docker compose up -d --build
```

The named volume is untouched by a rebuild — only the image changes. Check
`schema_version` in `/health` before and after if you're jumping multiple
releases; a store-schema bump would need a migration note here (none exist
yet).

## Running without Compose

```
docker build -t sodamem .
docker run -d \
  -e SODAMEM_API_KEY=... \
  -p 8000:8000 \
  -v sodamem-data:/data \
  sodamem
```
