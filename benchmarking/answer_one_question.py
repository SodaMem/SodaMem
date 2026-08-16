"""Subprocess worker for a single S500 question — see run_s500.py's driver.

Isolation is the point: on 2026-07-24 a full-run attempt stalled for 6+
hours on a handful of questions with CLOSE_WAIT sockets piling up (a
network-level read hang that dodged the HTTP client's own timeout — most
likely a post-retirement gateway trickling bytes to duck a 60s inter-chunk
read timeout without ever completing the response). All of run_s500.py's
internal retry layers (SDK-level, empty-content, policy) are individually
bounded, but a genuine stuck socket in one Python thread cannot be killed
from another thread in-process. A separate OS process CAN always be killed
— `subprocess.run(timeout=N)` in the parent guarantees forward progress
regardless of what's actually stuck.

Prints exactly one line of JSON to stdout: the answered+judged row (or
{"eval_id": ..., "error": "..."} on any exception). All diagnostic output
goes to stderr so stdout stays parseable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
from run_s500 import BASE_URL, _preflight_arms, answer_one, run_judge  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-id", required=True)
    args = ap.parse_args()
    import paths as _paths
    questions = json.load(open(_paths.questions_slim()))
    item = next((q for q in questions if q["eval_id"] == args.eval_id), None)
    if item is None:
        print(json.dumps({"eval_id": args.eval_id, "error": "unknown eval_id"}))
        return 1

    api_key = os.environ["DEEPSEEK_API_KEY"]
    # The driver checks this once before spawning anything, so in a normal run
    # this is redundant — deliberately. It is the check that runs in the
    # process that actually builds the configs, and this module is documented
    # as standalone-debuggable, which is a path the driver's preflight never
    # sees. Signature inspection, microseconds.
    _preflight_arms()
    row = dict(item)
    try:
        row.update(answer_one(item, api_key))
        judge_client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=60, max_retries=0)
        row["judge"] = run_judge(judge_client, item, row["hypothesis"])
        row["error"] = ""
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
