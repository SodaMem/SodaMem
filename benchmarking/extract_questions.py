"""One-time: slim the 257MB LongMemEval questions.json (haystack sessions
included) down to the six fields the runner needs. Provenance:
/Users/aaron.w/Desktop/LongMemEval-ingest/longmemeval_s_500/questions.json
(the reconstructed, 4-fold-verified S500 question set of record).
"""
import json
import sys

import paths as _paths

SRC = _paths.source_questions()
OUT = _paths.data_dir() / "questions_slim.json"

rows = json.load(open(SRC))
slim = []
for i, row in enumerate(rows, start=1):
    slim.append({
        "eval_id": str(row.get("eval_id") or f"q{i:03d}"),
        "user_id": str(row.get("user_id") or f"lme_q{i:03d}"),
        "question": str(row.get("question") or ""),
        "answer": row.get("gold_answer") or row.get("answer") or "",
        "category": row.get("category") or row.get("benchmark_category") or "",
        "question_type": row.get("question_type") or "",
        "question_date": row.get("question_date") or "",
    })
json.dump(slim, open(OUT, "w"), ensure_ascii=False, indent=0)
print(f"{len(slim)} questions -> {OUT}", file=sys.stderr)
