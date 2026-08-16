#!/usr/bin/env node
/**
 * AC7 harness: measure GET /v1/context latency against a REAL running SodaMem
 * daemon, sequentially, the way the plugin issues it on the per-turn path.
 *
 * It writes no file and invents no number: it prints what it measured, and
 * exits non-zero if the daemon is not reachable.
 *
 * Usage:
 *   SODAMEM_API_URL=http://127.0.0.1:8000 \
 *   SODAMEM_API_KEY=dev \
 *   SODAMEM_USER_ID=aaron \
 *   node scripts/measure-context-latency.mjs [iterations]
 *
 * Queries come from SODAMEM_QUERIES (newline- or `|`-separated) when set, and
 * otherwise from the small built-in set printed in the report — the query set
 * is part of the measurement, so it is always named in the output.
 */

const apiUrl = (process.env.SODAMEM_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const apiKey = process.env.SODAMEM_API_KEY ?? "";
const userId = process.env.SODAMEM_USER_ID ?? "";
const tokenBudget = Number(process.env.SODAMEM_TOKEN_BUDGET ?? 1200);
const iterations = Number(process.argv[2] ?? process.env.SODAMEM_ITERATIONS ?? 50);

if (!apiKey || !userId) {
  console.error("SODAMEM_API_KEY and SODAMEM_USER_ID are required.");
  process.exit(2);
}
if (!Number.isInteger(iterations) || iterations < 50) {
  console.error("AC7 requires at least 50 sequential requests.");
  process.exit(2);
}

const DEFAULT_QUERIES = [
  "what do I prefer for backend work?",
  "where do I live?",
  "what did we decide about the release process?",
  "which database are we using?",
  "what is my timezone?",
];

const queries = process.env.SODAMEM_QUERIES
  ? process.env.SODAMEM_QUERIES.split(/[\n|]/).map((q) => q.trim()).filter(Boolean)
  : DEFAULT_QUERIES;

function percentile(sorted, p) {
  if (sorted.length === 0) return NaN;
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.min(Math.max(rank, 0), sorted.length - 1)];
}

async function health() {
  const response = await fetch(`${apiUrl}/health`);
  if (!response.ok) throw new Error(`GET /health -> HTTP ${response.status}`);
  return response.json();
}

async function contextOnce(query) {
  const url = new URL(`${apiUrl}/v1/context`);
  url.searchParams.set("user_id", userId);
  url.searchParams.set("query", query);
  url.searchParams.set("token_budget", String(tokenBudget));

  const started = process.hrtime.bigint();
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${apiKey}`, "X-API-Key": apiKey, Accept: "application/json" },
  });
  const body = await response.text();
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;
  if (!response.ok) throw new Error(`GET /v1/context -> HTTP ${response.status}: ${body.slice(0, 200)}`);
  return elapsedMs;
}

let info;
try {
  info = await health();
} catch (error) {
  console.error(`No reachable SodaMem daemon at ${apiUrl}: ${error.message}`);
  console.error("Start one and re-run. This script never reports a number it did not measure.");
  process.exit(1);
}

// One warm-up request, excluded from the sample.
await contextOnce(queries[0]);

const samples = [];
for (let i = 0; i < iterations; i++) {
  samples.push(await contextOnce(queries[i % queries.length]));
}
samples.sort((a, b) => a - b);

const round = (n) => Math.round(n * 10) / 10;
console.log(
  JSON.stringify(
    {
      apiUrl,
      daemon: info,
      iterations,
      queries,
      tokenBudget,
      min_ms: round(samples[0]),
      p50_ms: round(percentile(samples, 50)),
      p95_ms: round(percentile(samples, 95)),
      p99_ms: round(percentile(samples, 99)),
      max_ms: round(samples[samples.length - 1]),
    },
    null,
    2
  )
);
