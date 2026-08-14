#!/usr/bin/env python3
"""Run Soft∪v1.1∪v1.2 miss union X (62) under Protocol v1.3."""
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
    default_out = str(v_root / "results_union_x")
    default_only = str(v_root / "union_x_ids.json")

    ap = argparse.ArgumentParser(description="Protocol v1.3 on union X (62)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default=default_out)
    ap.add_argument("--only", default=default_only)
    ap.add_argument(
        "--question-timeout",
        type=int,
        default=600,
        help="hard per-question wall clock (default 600s)",
    )
    ap.add_argument(
        "--heartbeat-stale",
        type=int,
        default=180,
        help="kill if no heartbeat for N seconds (default 180)",
    )
    ap.add_argument("--no-error-retry", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (v_root / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Hot dir (results_*) contends under Windows; keep a cool copy of console
    # lines next to status_s500 so mid-run status never opens results_*.
    cool_log = v_root / f"_progress_{out_dir.name}.log"
    log_fp = open(
        out_dir / "console.log", "a", encoding="utf-8", errors="replace", buffering=1
    )
    cool_fp = open(cool_log, "a", encoding="utf-8", errors="replace", buffering=1)

    class _MultiTee(_Tee):
        def __init__(self, stream, *logs):
            self._stream = stream
            self._logs = logs
            self._lock = threading.Lock()

        def write(self, data: str) -> int:
            with self._lock:
                try:
                    self._stream.write(data)
                    self._stream.flush()
                except Exception:
                    pass
                for lg in self._logs:
                    try:
                        lg.write(data)
                        lg.flush()
                    except Exception:
                        pass
            return len(data) if isinstance(data, str) else 0

        def flush(self) -> None:
            with self._lock:
                try:
                    self._stream.flush()
                except Exception:
                    pass
                for lg in self._logs:
                    try:
                        lg.flush()
                    except Exception:
                        pass

    sys.stdout = _MultiTee(sys.__stdout__, log_fp, cool_fp)  # type: ignore[assignment]
    sys.stderr = _MultiTee(sys.__stderr__, log_fp, cool_fp)  # type: ignore[assignment]

    repo = (
        Path(os.environ.get("SODAMEM_REPO", "")).expanduser()
        if os.environ.get("SODAMEM_REPO")
        else None
    )
    if repo is None or not (repo / "benchmarking" / "run_s500.py").exists():
        # Vendored in repo: benchmarking/protocol_v1.0/run_protocol_s500.py
        candidate = v_root.parents[1]
        if not (candidate / "benchmarking" / "run_s500.py").exists():
            # Agent Memory Project layout: Version/v1.0 beside project/
            candidate = v_root.parents[1] / "project" / "SodaMem-dev-main"
        if (candidate / "benchmarking" / "run_s500.py").exists():
            repo = candidate
        else:
            raise SystemExit(
                "Set SODAMEM_REPO to the SodaMem repo root "
                "(directory containing benchmarking/run_s500.py)"
            )

    ws = repo.parent.parent if repo.parent.name == "project" else repo.parent
    _load_dotenv(ws / "api" / ".env")
    if not (ws / "api" / ".env").is_file():
        _load_dotenv(repo / ".env")
    if not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get(
        "SODAMEM_LLM_API_KEY"
    ):
        raise SystemExit(f"Missing DEEPSEEK_API_KEY in {ws / 'api' / '.env'}")

    os.environ["SODAMEM_REPO"] = str(repo)
    os.environ.setdefault("SODAMEM_ANSWER_TIME_WINDOW", "1")
    os.environ.setdefault("SODAMEM_READER_PERSONALIZATION", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT_HARD", "0")
    os.environ["SODAMEM_OPT_APPLY"] = "1"
    os.environ["SODAMEM_PROTOCOL_V1"] = "1"
    os.environ["SODAMEM_PROTOCOL_V1_ROOT"] = str(v_root)
    os.environ.pop("SODAMEM_STRUCT_APPLY", None)
    os.environ.setdefault("SODAMEM_BENCH_MODEL", "deepseek-v4-flash")
    os.environ.setdefault("SODAMEM_BENCH_BASE_URL", "https://api.deepseek.com/v1")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Parent + worker DNS pin when system getaddrinfo fails for api.deepseek.com.
    os.environ.setdefault("SODAMEM_FORCE_DEEPSEEK_DNS", "1")
    try:
        import _force_deepseek_dns  # noqa: F401
    except ImportError:
        bench = repo / "benchmarking"
        if str(bench) not in sys.path:
            sys.path.insert(0, str(bench))
        import _force_deepseek_dns  # noqa: F401

    stores = ws / "data" / "longmemeval_s_500_Hobs_entitysubj"
    if stores.is_dir():
        os.environ["SODAMEM_BENCH_STORES"] = str(stores)
    else:
        raise SystemExit(f"Missing stores: {stores}")

    for cand in (
        ws / "project" / "sodamem_databack" / "bench-data",
        ws / "sodamem_databack" / "bench-data",
    ):
        if cand.is_dir() and not os.environ.get("SODAMEM_BENCH_DATA"):
            os.environ["SODAMEM_BENCH_DATA"] = str(cand)
            break

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
        f"[protocol_v1.3 union X] root={v_root}\n"
        f"  only={only_path} (62)\n"
        f"  out={args.out}\n"
        f"  model={os.environ.get('SODAMEM_BENCH_MODEL')}\n"
        f"  stores={os.environ.get('SODAMEM_BENCH_STORES')}",
        flush=True,
    )

    from sodamem_opt import run_frozen

    argv = [
        "--only",
        str(only_path.resolve()),
        "--out",
        args.out,
        "--concurrency",
        str(args.concurrency),
        "--question-timeout",
        str(args.question_timeout),
        "--heartbeat-stale",
        str(args.heartbeat_stale),
    ]
    if args.no_error_retry:
        argv.append("--no-error-retry")
    return run_frozen.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
