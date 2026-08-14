"""External data locations for the benchmark rig.

Nothing under `benchmarking/` may carry LongMemEval content. The dataset is a
third-party corpus, and this repository's public release is already blocked on
one attribution question (COPYRIGHT_TODO.md) — committing benchmark questions
and gold answers would add a second, avoidable one. The scripts read the data
from wherever the operator put it; the repository holds only the code.

Every path comes from an environment variable and every miss RAISES. The rig
these scripts came from hardcoded four absolute paths under one laptop's home
directory, which meant a wrong or moved dataset surfaced as a confusing
mid-run failure (or, worse, as a silent zero-question run). A missing variable
is an operator error and should say so in one line, before any LLM call is
billed.

    SODAMEM_BENCH_DATA     dir holding questions_slim.json and
                           anchor_slim2_labels.json (build the first with
                           extract_questions.py)
    SODAMEM_BENCH_STORES   frozen per-user store root; each entry is
                           <root>/<user_id>/memory.db
    SODAMEM_BENCH_SRC      full LongMemEval questions.json — only the
                           dataset-prep scripts need this one
    SODAMEM_BENCH_RESULTS  output root (default ./results, gitignored)
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["data_dir", "store_root", "source_questions", "results_root",
           "questions_slim", "anchor_labels"]


def _required(var: str, what: str) -> Path:
    raw = os.environ.get(var, "").strip()
    if not raw:
        raise SystemExit(
            f"{var} is not set — it must point at {what}.\n"
            f"  export {var}=/path/to/it"
        )
    path = Path(raw).expanduser()
    if not path.exists():
        raise SystemExit(f"{var}={path} does not exist.")
    return path


def data_dir() -> Path:
    return _required("SODAMEM_BENCH_DATA",
                     "the dir holding questions_slim.json / anchor_slim2_labels.json")


def store_root() -> Path:
    return _required("SODAMEM_BENCH_STORES",
                     "the frozen store root (<root>/<user_id>/memory.db)")


def source_questions() -> Path:
    return _required("SODAMEM_BENCH_SRC", "the full LongMemEval questions.json")


def results_root() -> Path:
    raw = os.environ.get("SODAMEM_BENCH_RESULTS", "").strip()
    return Path(raw).expanduser() if raw else Path(__file__).parent / "results"


def questions_slim() -> Path:
    path = data_dir() / "questions_slim.json"
    if not path.exists():
        raise SystemExit(
            f"{path} not found — build it with:\n"
            f"  SODAMEM_BENCH_SRC=... python benchmarking/extract_questions.py"
        )
    return path


def anchor_labels() -> Path:
    """Anchor run labels for the paired McNemar comparison.

    Optional by design: without it a run still scores, it just cannot report a
    paired test. Returning None beats raising, because the anchor is a
    comparison convenience and its absence must not block a fresh measurement.

    Prefers the current store of record's 3-run CONSENSUS labels over the
    legacy single-run anchor. A single run's labels are not a stable
    reference: two runs of the identical configuration measured net -11 with
    McNemar p=0.052, so comparing against one of them manufactures
    FIXED/BROKE out of reference jitter.
    """
    for name in ("entitysubj_consensus_anchor.json", "anchor_slim2_labels.json"):
        path = data_dir() / name
        if path.exists():
            return path
    return None
