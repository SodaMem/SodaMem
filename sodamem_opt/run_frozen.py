"""Frozen-store S500 runner with Plan B patches applied.

Same fingerprint-echo bridge as ``sodamem_dev.run_frozen``, plus:

- ``sodamem_opt.apply()`` in parent; workers re-apply via ``SODAMEM_OPT_APPLY=1``
- Automatic Error-retry loop (``--no-error-retry`` to disable)
- Wall timeout + heartbeat (see ``Version/v1.3/TIMEOUTS.md``):
  * first pass: 240s / 90s (or whatever ``--question-timeout`` /
    ``--heartbeat-stale`` request, hard-capped at 600s)
  * after a TIMEOUT / HEARTBEAT_STALE: escalate once to 600s / 180s
  * further rounds stay at the 600s cap (no further bump)
  * ``--no-timeout-escalate`` keeps the first pair on every retry
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_TIMEOUT_FIRST = (600, 180)
_TIMEOUT_ESCALATED = (600, 180)
_TIMEOUT_HARD_CAP = 600


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
        default=int(os.environ.get("SODAMEM_OPT_MAX_ERROR_ROUNDS", "20")),
    )
    p.add_argument(
        "--error-retry-sleep",
        type=float,
        default=float(os.environ.get("SODAMEM_OPT_ERROR_RETRY_SLEEP", "5")),
    )
    p.add_argument("--no-error-retry", action="store_true")
    p.add_argument("--no-timeout-escalate", action="store_true")
    return p.parse_known_args(argv)


def _flag_int(rest: list[str], name: str, default: int) -> int:
    for i, a in enumerate(rest):
        if a == name and i + 1 < len(rest):
            try:
                return int(rest[i + 1])
            except ValueError:
                return default
        if a.startswith(name + "="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return default
    return default


def _set_flag(rest: list[str], name: str, value: int) -> list[str]:
    """Return a copy of ``rest`` with ``name value`` set (replace or append)."""
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(rest):
        a = rest[i]
        if a == name and i + 1 < len(rest):
            out.extend([name, str(value)])
            i += 2
            replaced = True
            continue
        if a.startswith(name + "="):
            out.append(f"{name}={value}")
            i += 1
            replaced = True
            continue
        out.append(a)
        i += 1
    if not replaced:
        out.extend([name, str(value)])
    return out


def _clamp_pair(wall_s: int, hb_s: int) -> tuple[int, int]:
    wall_s = max(30, min(int(wall_s), _TIMEOUT_HARD_CAP))
    hb_s = max(0, min(int(hb_s), wall_s))
    return wall_s, hb_s


def _apply_timeouts(rest: list[str], wall_s: int, hb_s: int) -> list[str]:
    wall_s, hb_s = _clamp_pair(wall_s, hb_s)
    rest = _set_flag(rest, "--question-timeout", wall_s)
    rest = _set_flag(rest, "--heartbeat-stale", hb_s)
    return rest


def _remaining_error_rows(answers_path: Path) -> dict[str, dict]:
    import run_s500

    done, errored = run_s500.load_previous_answers(answers_path)
    return {eid: row for eid, row in errored.items() if eid not in done}


def _is_timeout_error(err: object) -> bool:
    if not err:
        return False
    u = str(err).upper()
    return "TIMEOUT" in u or "HEARTBEAT_STALE" in u


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
        out = str(Path("results") / "opt_s500")
    return Path(out)


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("SODAMEM_ANSWER_TIME_WINDOW", "1")
    os.environ.setdefault("SODAMEM_READER_PERSONALIZATION", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT", "1")
    os.environ.setdefault("SODAMEM_OPT_DET_COUNT_HARD", "0")
    os.environ["SODAMEM_OPT_APPLY"] = "1"
    os.environ.pop("SODAMEM_STRUCT_APPLY", None)

    raw = list(sys.argv[1:] if argv is None else argv)
    opt_args, rest = _parse_argv(raw)

    first_wall = _flag_int(rest, "--question-timeout", _TIMEOUT_FIRST[0])
    first_hb = _flag_int(rest, "--heartbeat-stale", _TIMEOUT_FIRST[1])
    first_wall, first_hb = _clamp_pair(first_wall, first_hb)
    esc_wall, esc_hb = _clamp_pair(*_TIMEOUT_ESCALATED)

    root = _repo_root()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "benchmarking"))
    sep = os.pathsep
    prior = os.environ.get("PYTHONPATH", "").strip()
    os.environ["PYTHONPATH"] = str(root) + ((sep + prior) if prior else "")

    from sodamem_opt.patches import apply as apply_opt

    apply_opt()
    print(
        f"[sodamem-opt] Plan B+ applied "
        f"(det_hard={os.environ.get('SODAMEM_OPT_DET_COUNT_HARD')} "
        f"time_window={os.environ.get('SODAMEM_ANSWER_TIME_WINDOW')})",
        flush=True,
    )
    print(
        f"[sodamem-opt] timeouts first={first_wall}s/{first_hb}s "
        f"escalated={esc_wall}s/{esc_hb}s "
        f"escalate={'off' if opt_args.no_timeout_escalate else 'on'} "
        f"hard_cap={_TIMEOUT_HARD_CAP}s",
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
        f"[sodamem-opt] echoing prompt_fingerprint {str(echo)[:16]}… "
        f"for {len(stores)} stores (READ-ONLY)",
        flush=True,
    )
    store_mod.prompt_fingerprint = lambda prompts: echo
    os.environ["SODAMEM_DEV_ECHO_FP"] = echo

    out_dir = _out_dir_from_argv(rest)
    answers_path = out_dir / "answers.jsonl"
    max_rounds = 0 if opt_args.no_error_retry else max(0, opt_args.max_error_rounds)

    rest = _apply_timeouts(rest, first_wall, first_hb)
    escalated = False
    round_i = 0
    rc = 0

    while True:
        round_i += 1
        if round_i > 1:
            pending_rows = _remaining_error_rows(answers_path)
            print(
                f"[sodamem-opt] Error-retry round {round_i - 1}/{max_rounds}: "
                f"{len(pending_rows)} ids still failing → re-run",
                flush=True,
            )
            if (
                not opt_args.no_timeout_escalate
                and not escalated
                and any(_is_timeout_error(r.get("error")) for r in pending_rows.values())
            ):
                rest = _apply_timeouts(rest, esc_wall, esc_hb)
                escalated = True
                n_to = sum(
                    1
                    for r in pending_rows.values()
                    if _is_timeout_error(r.get("error"))
                )
                print(
                    f"[sodamem-opt] escalate timeouts for {n_to} "
                    f"TIMEOUT/HEARTBEAT ids → {esc_wall}s / {esc_hb}s "
                    f"(cap {_TIMEOUT_HARD_CAP}s)",
                    flush=True,
                )
            if opt_args.error_retry_sleep > 0:
                time.sleep(opt_args.error_retry_sleep)

        wall = _flag_int(rest, "--question-timeout", first_wall)
        hb = _flag_int(rest, "--heartbeat-stale", first_hb)
        print(
            f"[sodamem-opt] pass {round_i}: question-timeout={wall}s "
            f"heartbeat-stale={hb}s",
            flush=True,
        )
        sys.argv = ["run_s500.py", *rest]
        rc = run_s500.main()

        pending_rows = _remaining_error_rows(answers_path)
        print(
            f"[sodamem-opt] after pass {round_i}: "
            f"{len(pending_rows)} Error ids remaining "
            f"(answers={answers_path})",
            flush=True,
        )
        if not pending_rows:
            print("[sodamem-opt] no Errors left — done.", flush=True)
            return rc
        if max_rounds <= 0 or round_i > max_rounds:
            print(
                f"[sodamem-opt] stopping with {len(pending_rows)} Errors still open "
                f"(max-error-rounds={max_rounds}).",
                flush=True,
            )
            return 2 if pending_rows else rc


if __name__ == "__main__":
    raise SystemExit(main())
