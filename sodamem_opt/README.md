# sodamem_opt — answer-side Miss reductions (Plan B)

Keeps `sodamem/` untouched. This package **monkey-patches** the live answer
path at process start for experiments described in
`meeting/sodamem_optimization_notes.md`:

1. Broader relative-date windows (`N months ago`, wider ±bands)
2. Force `browser_timeline_events` / count for order·recent·relative TR
3. Reader discipline: sort dated evidence; prefer newest `document_time` for
   same attribute; no undated “most recent” guesses
4. Value/price false-negative: rewrite + second search autocall

## Run

```powershell
cd project\SodaMem-dev-main
.\.venv\Scripts\Activate.ps1
. .\scripts\env_bench.ps1   # or set paths below

$env:SODAMEM_BENCH_STORES = "C:\Users\Lenovo\Desktop\Agent Memory Project\data\longmemeval_s_500_Hobs"
$env:SODAMEM_BENCH_DATA   = "C:\Users\Lenovo\Desktop\Agent Memory Project\project\sodamem_databack\bench-data"
$env:SODAMEM_REPO         = (Get-Location).Path

# Unit smoke (no API)
python -m sodamem_opt.unit_smoke

# 7 Miss eval_ids
python -m sodamem_opt.run_frozen --only q007,q028,q034,q035,q039,q055,q116 --out results\opt_miss7 --concurrency 2

# Full 500 (auto-retries Error ids after each pass until none left)
python -m sodamem_opt.run_frozen --out results\opt_s500 --concurrency 4

# Resume / fill Errors for an existing out dir (same flags):
python -m sodamem_opt.run_frozen --out results\opt_s500_entitysubj --concurrency 4

# Cap automatic Error loops (default 20); or disable:
python -m sodamem_opt.run_frozen --out results\opt_s500_entitysubj --concurrency 4 --max-error-rounds 10
python -m sodamem_opt.run_frozen --out results\opt_s500_entitysubj --concurrency 4 --no-error-retry
```

Original baseline (no opt): `python -m sodamem_dev.run_frozen ...`

## Miss-7 smoke (Parent Hobs, 2026-08-08)

| arm | correct |
|-----|---------|
| notes baseline (prior misses) | 0/7 |
| opt v1 (time_window + finalization force) | 1/7 |
| opt v2 (+ step-0 timeline/count + reader rules) | **4/7** (q007, q034, q039, q055) |

Still hard: q028 (airline order incomplete), q035 (Disney+ vs Apple TV+), q116 (500 Mbps vs 1 Gbps “new plan”).

Full 500: `results/opt_s500/`

**Note:** many entitysubj Errors were DeepSeek `402 Insufficient Balance`. Auto-retry cannot succeed until the key has credit.
