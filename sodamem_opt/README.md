# sodamem_opt — answer-side Plan B+ patches

Keeps `sodamem/` sources untouched. This package **monkey-patches** the live
answer path at process start:

1. Broader relative-date windows (`N months ago`, wider ±bands)
2. Force timeline / count tools for order·recent·relative TR
3. Reader discipline: sort dated evidence; prefer newest `document_time`
4. Deterministic count helpers used by Protocol v1.0

## Use

```python
from sodamem_opt import apply
apply()
```

Or set `SODAMEM_OPT_APPLY=1` so `benchmarking/answer_one_question.py` applies
patches in each worker.

Protocol v1.0 stacks on top of this package — see
[`benchmarking/protocol_v1.0/`](../benchmarking/protocol_v1.0/).
