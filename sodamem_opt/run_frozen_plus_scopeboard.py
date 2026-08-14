"""Soft (Plan B+) + ScopeBoard logistics skill (advisory only).

Workers: OPT patches first, then scope_board answer_one.
Hard integer overrides stay off.
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


def _scope_skill_root() -> Path:
    env = os.environ.get("SODAMEM_SKILL_SCOPE_BOARD_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    repo = _repo_root()
    candidates = [
        repo.parents[1] / "新版本方法" / "v3.0" / "skill" / "scope_board",
        repo.parent.parent / "新版本方法" / "v3.0" / "skill" / "scope_board",
    ]
    for p in candidates:
        if (p / "scope_board_skill").is_dir():
            return p
    raise SystemExit(
        "set SODAMEM_SKILL_SCOPE_BOARD_ROOT to "
        ".../新版本方法/v3.0/skill/scope_board"
    )


def _parse_argv(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--max-error-rounds",
        type=int,
        default=int(os.environ.get("SODAMEM_OPT_MAX_ERROR_ROUNDS", "20")),
    )
    p.add_argument(
        "--error-retry-sleep",
        type=float,
        default=float(os.environ.get("SODAMEM_OPT_ERROR_RETRY_SLEEP", "5")),
    )
    p.add_argument("--no-error-retry", action="store_true")
    return p.parse_known_args(argv)


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
        out = str(Path("results") / "soft_plus_scopeboard")
    return Path(out)


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("SODAMEM_ANSWER_TIME_WINDOW", "1")
    os.environ.setdefault("SODAMEM_READER_PERSONALIZATION", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT_HARD", "0")
    os.environ["SODAMEM_OPT_APPLY"] = "1"

    skill_root = _scope_skill_root()
    os.environ["SODAMEM_SKILL_SCOPE_BOARD"] = "1"
    os.environ["SODAMEM_SKILL_SCOPE_BOARD_ROOT"] = str(skill_root)
    os.environ.setdefault("SODAMEM_SCOPE_BOARD_JUDGE", "1")
    os.environ.setdefault("SODAMEM_SCOPE_BOARD_NARROW_SELECTED", "1")
    os.environ.pop("SODAMEM_STRUCT_APPLY", None)
    # Retired skills — ensure not accidentally unlocked.
    os.environ.pop("SODAMEM_SKILL_TEMPORAL_ANCHOR", None)
    os.environ.pop("SODAMEM_SKILL_SET_COUNT", None)

    raw = list(sys.argv[1:] if argv is None else argv)
    opt_args, rest = _parse_argv(raw)

    root = _repo_root()
    for p in (skill_root, root, root / "benchmarking"):
        sys.path.insert(0, str(p))
    sep = os.pathsep
    prior = os.environ.get("PYTHONPATH", "").strip()
    os.environ["PYTHONPATH"] = (
        str(skill_root) + sep + str(root) + ((sep + prior) if prior else "")
    )

    from sodamem_opt.patches import apply as apply_opt
    from scope_board_skill.apply import apply as apply_scope

    apply_opt()
    apply_scope()
    print(
        "[soft+scopeboard] OPT stacked with ScopeBoard "
        f"(det_hard={os.environ.get('SODAMEM_OPT_DET_COUNT_HARD')} "
        f"judge={os.environ.get('SODAMEM_SCOPE_BOARD_JUDGE')} "
        f"narrow={os.environ.get('SODAMEM_SCOPE_BOARD_NARROW_SELECTED')})",
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
            f"{len(seen)} distinct prompt fingerprints under {store_root}"
        )
    echo = seen.pop()
    print(
        f"[soft+scopeboard] echoing prompt_fingerprint {str(echo)[:16]}… "
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
                f"[soft+scopeboard] Error-retry {round_i - 1}/{max_rounds}: "
                f"{len(pending)} still failing",
                flush=True,
            )
            if opt_args.error_retry_sleep > 0:
                time.sleep(opt_args.error_retry_sleep)

        sys.argv = ["run_s500.py", *rest]
        rc = run_s500.main()

        pending = _remaining_error_ids(answers_path)
        print(
            f"[soft+scopeboard] after pass {round_i}: "
            f"{len(pending)} Errors ({answers_path})",
            flush=True,
        )
        if not pending:
            print("[soft+scopeboard] no Errors left — done.", flush=True)
            return rc
        if max_rounds <= 0 or round_i > max_rounds:
            return 2 if pending else rc


if __name__ == "__main__":
    raise SystemExit(main())
