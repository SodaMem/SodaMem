# sodamem_struct

Structural answer-path experiment (optimization notes options 1 + 3).

- **No** planner/reader prompt addenda
- After planner: set-count/sum → include/exclude + code aggregate; slots → code select
- Falls back to stock reader when confidence is low

```powershell
cd project\SodaMem-dev-main
. .\scripts\env_bench.ps1
$env:SODAMEM_BENCH_STORES = "<workspace>\data\longmemeval_s_500_Hobs_entitysubj"
python -m sodamem_struct.unit_smoke
python -m sodamem_struct.run_frozen --only results\miss38_ids.txt --out results\struct_miss38 --concurrency 2
```
