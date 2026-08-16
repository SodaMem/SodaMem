/**
 * Does `--workers 1` queue under the concurrency the plugin actually creates?
 *
 * The sequential harness measures one client. Reality is a dsh turn racing a
 * Cursor hook and a Claude Code hook against the same single-worker daemon.
 * This issues N requests at once and reports the wall time each one saw.
 */
const apiUrl = process.env.SODAMEM_API_URL;
const apiKey = process.env.SODAMEM_API_KEY;
const userId = process.env.SODAMEM_USER_ID;

const QUERIES = [
  "where do I live?", "which airline did I fly to Boston?", "where do I work now?",
  "do I have a pet?", "what city did I move to?", "tell me about my flights",
];

async function once(query) {
  const url = new URL(`${apiUrl}/v1/context`);
  url.searchParams.set("query", query);
  url.searchParams.set("user_id", userId);
  url.searchParams.set("token_budget", "1200");
  const t0 = performance.now();
  const r = await fetch(url, { headers: { Authorization: `Bearer ${apiKey}` } });
  await r.text();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return performance.now() - t0;
}

function pct(sorted, p) {
  return sorted[Math.min(Math.ceil((p / 100) * sorted.length) - 1, sorted.length - 1)];
}

for (const concurrency of [1, 2, 4, 8]) {
  const samples = [];
  // 5 rounds so the number is not one unlucky burst.
  for (let round = 0; round < 5; round++) {
    const batch = Array.from({ length: concurrency }, (_, i) =>
      once(QUERIES[(round * concurrency + i) % QUERIES.length]));
    samples.push(...(await Promise.all(batch)));
  }
  const s = samples.sort((a, b) => a - b);
  console.log(
    `concurrency=${concurrency}  n=${s.length}  ` +
    `p50=${pct(s, 50).toFixed(1)}ms  p99=${pct(s, 99).toFixed(1)}ms  max=${s[s.length - 1].toFixed(1)}ms`
  );
}
