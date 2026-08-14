#!/usr/bin/env python3
"""Run LongMemEval-S under Typed Answer Schema / TAS (Plan B+ then TAS advisories)."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path


class _Tee:
    def __init__(self, stream, log_fp):
        self._stream = stream
        self._log = log_fp
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            try:
                self._stream.write(data)
                self._stream.flush()
            except Exception:
                pass
            try:
                self._log.write(data)
                self._log.flush()
            except Exception:
                pass
        return len(data) if isinstance(data, str) else 0

    def flush(self) -> None:
        with self._lock:
            try:
                self._stream.flush()
            except Exception:
                pass
            try:
                self._log.flush()
            except Exception:
                pass

    def fileno(self):
        return self._stream.fileno()

    def isatty(self) -> bool:
        return False


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    v_root = Path(__file__).resolve().parent
    default_out = str(v_root / "results_s500")

    ap = argparse.ArgumentParser(description="Typed Answer Schema (TAS) LongMemEval-S runner")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default=default_out)
    ap.add_argument("--only", required=True, help="JSON list or line file of eval_ids")
    ap.add_argument(
        "--question-timeout",
        type=int,
        default=600,
        help="hard per-question wall clock (default 600s)",
    )
    ap.add_argument("--count", type=int, default=0, help="limit (0 = all)")
    ap.add_argument("--category", default="", help="optional LongMemEval category filter")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (v_root / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_fp = open(
        out_dir / "console.log", "a", encoding="utf-8", errors="replace", buffering=1
    )
    sys.stdout = _Tee(sys.__stdout__, log_fp)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.__stderr__, log_fp)  # type: ignore[assignment]

    repo = (
        Path(os.environ.get("SODAMEM_REPO", "")).expanduser()
        if os.environ.get("SODAMEM_REPO")
        else None
    )
    if repo is None or not (repo / "benchmarking" / "run_s500.py").exists():
        # Vendored layout: benchmarking/protocol_v1.0/run_protocol_s500.py
        candidate = v_root.parents[1]
        if (candidate / "benchmarking" / "run_s500.py").exists():
            repo = candidate
        else:
            raise SystemExit(
                "Set SODAMEM_REPO to the SodaMem repo root "
                "(directory containing benchmarking/run_s500.py)"
            )

    _load_dotenv(repo / ".env")
    if not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get(
        "SODAMEM_LLM_API_KEY"
    ):
        raise SystemExit(
            "Missing DEEPSEEK_API_KEY or SODAMEM_LLM_API_KEY "
            "(set it in the environment or the SodaMem repo .env file)"
        )
    if not os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("SODAMEM_LLM_API_KEY"):
        os.environ["DEEPSEEK_API_KEY"] = os.environ["SODAMEM_LLM_API_KEY"]

    if not os.environ.get("SODAMEM_BENCH_STORES"):
        raise SystemExit(
            "Set SODAMEM_BENCH_STORES to the frozen LongMemEval store root "
            "(directory of <user_id>/memory.db)"
        )
    if not os.environ.get("SODAMEM_BENCH_DATA"):
        raise SystemExit(
            "Set SODAMEM_BENCH_DATA to the bench-data directory "
            "(questions_slim.json, etc.)"
        )

    os.environ["SODAMEM_REPO"] = str(repo)
    os.environ.setdefault("SODAMEM_ANSWER_TIME_WINDOW", "1")
    os.environ.setdefault("SODAMEM_READER_PERSONALIZATION", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT_HARD", "0")
    os.environ["SODAMEM_OPT_APPLY"] = "1"
    os.environ["SODAMEM_PROTOCOL_V1"] = "1"
    os.environ["SODAMEM_PROTOCOL_V1_ROOT"] = str(v_root)
    os.environ.setdefault("SODAMEM_BENCH_MODEL", "deepseek-v4-flash")
    os.environ.setdefault("SODAMEM_BENCH_BASE_URL", "https://api.deepseek.com/v1")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    sep = os.pathsep
    prior = os.environ.get("PYTHONPATH", "").strip()
    os.environ["PYTHONPATH"] = (
        str(v_root) + sep + str(repo) + ((sep + prior) if prior else "")
    )
    sys.path.insert(0, str(v_root))
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "benchmarking"))

    from protocol_v1.apply import apply

    apply()

    only_path = Path(args.only)
    if not only_path.is_file():
        raise SystemExit(f"missing --only file: {only_path}")
    print(
        f"[protocol_v1.0] root={v_root}\n"
        f"  only={only_path}\n"
        f"  out={out_dir}\n"
        f"  model={os.environ.get('SODAMEM_BENCH_MODEL')}\n"
        f"  stores={os.environ.get('SODAMEM_BENCH_STORES')}",
        flush=True,
    )

    import run_s500

    argv = [
        "--only",
        str(only_path.resolve()),
        "--out",
        str(out_dir),
        "--concurrency",
        str(args.concurrency),
        "--question-timeout",
        str(args.question_timeout),
    ]
    if args.count:
        argv.extend(["--count", str(args.count)])
    if args.category:
        argv.extend(["--category", args.category])
    # run_s500.main() parses sys.argv
    old_argv = sys.argv
    try:
        sys.argv = ["run_s500.py", *argv]
        return int(run_s500.main() or 0)
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
