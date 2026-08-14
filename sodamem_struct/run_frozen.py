"""Frozen-store S500 runner with structural answer path (no prompt addenda).

Workers re-apply via ``SODAMEM_STRUCT_APPLY=1``. Does **not** set
``SODAMEM_OPT_APPLY`` — this arm is intentionally prompt-free.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("SODAMEM_REPO", "").strip()
    if env:
        root = Path(env).expanduser()
    else:
        root = Path(__file__).resolve().parents[1]
    if not (root / "benchmarking" / "run_s500.py").exists():
        raise SystemExit(
            f"no benchmarking/run_s500.py under {root}\n"
            "  point SODAMEM_REPO at the SodaMem-dev-main checkout"
        )
    return root


def _parse_argv(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--max-error-rounds",
        type=int,
        default=int(os.environ.get("SODAMEM_STRUCT_MAX_ERROR_ROUNDS", "20")),
    )
    p.add_argument(
        "--error-retry-sleep",
        type=float,
        default=float(os.environ.get("SODAMEM_STRUCT_ERROR_RETRY_SLEEP", "5")),
    )
    p.add_argument("--no-error-retry", action="store_true")
    opt_args, rest = p.parse_known_args(argv)
    return opt_args, rest


def _remaining_error_ids(answers_path: Path) -> set[str]:
    import run_s500

    done, errored = run_s500.load_previous_answers(answers_path)
    return {eid for eid in errored if eid not in done}


def _out_dir_from_argv(rest: list[str]) -> Path:
    out = None
    for i, a in enumerate(rest):
        if a == "--out" and i + 1 < len(rest):
            out = rest[i + 1]
            break
        if a.startswith("--out="):
            out = a.split("=", 1)[1]
            break
    if not out:
        out = str(Path("results") / "struct_s500")
    return Path(out)


def main(argv: list[str] | None = None) -> int:
    # Structural arm only — do not enable Plan B prompt patches.
    os.environ.pop("SODAMEM_OPT_APPLY", None)
    os.environ["SODAMEM_STRUCT_APPLY"] = "1"
    # Keep code time-window resolution available to aggregate filters.
    os.environ.setdefault("SODAMEM_ANSWER_TIME_WINDOW", "1")

    raw = list(sys.argv[1:] if argv is None else argv)
    opt_args, rest = _parse_argv(raw)

    root = _repo_root()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "benchmarking"))
    sep = os.pathsep
    prior = os.environ.get("PYTHONPATH", "").strip()
    os.environ["PYTHONPATH"] = str(root) + ((sep + prior) if prior else "")

    from sodamem_struct.apply import apply

    apply()
    print(
        "[sodamem-struct] structural answer path applied "
        f"(min_conf={os.environ.get('SODAMEM_STRUCT_MIN_CONF', '0.55')} "
        f"time_window={os.environ.get('SODAMEM_ANSWER_TIME_WINDOW')}; "
        "no OPT prompt patches)",
        flush=True,
    )

    import sodamem.memory.storage.store as store_mod
    import run_s500  # noqa: E402
    from sodamem_dev.frozen import recorded_fingerprint

    store_root = run_s500.store_root()
    stores = sorted(p for p in store_root.iterdir() if (p / "memory.db").exists())
    if not stores:
        raise SystemExit(f"no per-user stores under {store_root}")

    seen = {recorded_fingerprint(p) for p in stores}
    if len(seen) != 1:
        raise SystemExit(
            f"{len(seen)} distinct prompt fingerprints under {store_root} — "
            f"not one frozen corpus: {sorted(str(s)[:16] for s in seen)}"
        )
    echo = seen.pop()
    print(
        f"[sodamem-struct] echoing prompt_fingerprint {str(echo)[:16]}… "
        f"for {len(stores)} stores (READ-ONLY)",
        flush=True,
    )
    store_mod.prompt_fingerprint = lambda prompts: echo
    os.environ["SODAMEM_DEV_ECHO_FP"] = echo

    out_dir = _out_dir_from_argv(rest)
    answers_path = out_dir / "answers.jsonl"
    max_rounds = 0 if opt_args.no_error_retry else max(0, opt_args.max_error_rounds)

    round_i = 0
    while True:
        round_i += 1
        if round_i > 1:
            pending = _remaining_error_ids(answers_path)
            print(
                f"[sodamem-struct] Error-retry round {round_i - 1}/{max_rounds}: "
                f"{len(pending)} ids still failing → re-run",
                flush=True,
            )
            if opt_args.error_retry_sleep > 0:
                time.sleep(opt_args.error_retry_sleep)

        sys.argv = ["run_s500.py", *rest]
        rc = run_s500.main()

        pending = _remaining_error_ids(answers_path)
        print(
            f"[sodamem-struct] after pass {round_i}: "
            f"{len(pending)} Error ids remaining "
            f"(answers={answers_path})",
            flush=True,
        )
        if not pending:
            print("[sodamem-struct] no Errors left — done.", flush=True)
            return rc
        if max_rounds <= 0 or round_i > max_rounds:
            print(
                f"[sodamem-struct] stopping with {len(pending)} Errors still open "
                f"(max-error-rounds={max_rounds}).",
                flush=True,
            )
            return 2 if pending else rc


if __name__ == "__main__":
    raise SystemExit(main())
