"""Subprocess worker for a single S500 question — see run_s500.py's driver.

Isolation is the point: on 2026-07-24 a full-run attempt stalled for 6+
hours on a handful of questions with CLOSE_WAIT sockets piling up (a
network-level read hang that dodged the HTTP client's own timeout — most
likely a post-retirement gateway trickling bytes to duck a 60s inter-chunk
read timeout without ever completing the response). All of run_s500.py's
internal retry layers (SDK-level, empty-content, policy) are individually
bounded, but a genuine stuck socket in one Python thread cannot be killed
from another thread in-process. A separate OS process CAN always be killed
— the parent monitors a heartbeat file and a hard wall-clock timeout.

Prints exactly one line of JSON to stdout: the answered+judged row (or
{"eval_id": ..., "error": "..."} on any exception). All diagnostic output
goes to stderr so stdout stays parseable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Optional DNS pin when the host resolver cannot reach api.deepseek.com.
import _force_deepseek_dns  # noqa: F401, E402

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
import run_s500  # noqa: E402
from run_s500 import BASE_URL, _preflight_arms, run_judge  # noqa: E402


def _heartbeat(stage: str = "") -> None:
    path = os.environ.get("SODAMEM_HEARTBEAT_PATH", "").strip()
    if not path:
        return
    try:
        Path(path).write_text(
            json.dumps({"t": time.time(), "stage": stage, "pid": os.getpid()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _install_llm_heartbeat() -> None:
    """Pulse heartbeat before/during/after each LLM create so hung sockets go stale.

    Long chat.completions.create calls can exceed --heartbeat-stale without an
    after-pulse; keep a background ticker while the request is in flight.
    """
    if not os.environ.get("SODAMEM_HEARTBEAT_PATH", "").strip():
        return
    try:
        from sodamem.llm import openai_compat as oc
    except Exception:
        return
    if getattr(oc, "_sodamem_hb_installed", False):
        return

    import threading

    def _wrapped(self, messages, request_kwargs, usage_phase):
        attempts = 1 + oc.empty_content_retries()
        text = ""
        for attempt in range(attempts):
            _heartbeat(f"llm_before_{attempt}")
            stop = threading.Event()

            def _tick() -> None:
                n = 0
                while not stop.wait(15.0):
                    n += 1
                    _heartbeat(f"llm_wait_{attempt}_{n}")

            t = threading.Thread(target=_tick, daemon=True)
            t.start()
            try:
                response = self._client.chat.completions.create(
                    messages=messages, **request_kwargs
                )
            finally:
                stop.set()
                t.join(timeout=1.0)
            _heartbeat(f"llm_after_{attempt}")
            self._record_usage(usage_phase, response)
            choice = response.choices[0]
            self._last_finish_reason = getattr(choice, "finish_reason", None)
            text = oc._extract_message_text(choice.message)
            if text:
                return text
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
        raise self._empty_content_error(attempts)

    oc.OpenAICompatibleProvider._complete_with_empty_retry = _wrapped  # type: ignore[method-assign]
    oc._sodamem_hb_installed = True


def _install_tool_heartbeat() -> None:
    """Pulse around MemoryTool.dispatch — retrieval hangs happen between LLMs.

    Long tool calls (evidence-count / event-timeline) can exceed heartbeat-stale
    without an after-pulse; keep a background ticker while dispatch runs.
    """
    if not os.environ.get("SODAMEM_HEARTBEAT_PATH", "").strip():
        return
    try:
        from sodamem.tools import MemoryTool
    except Exception:
        return
    if getattr(MemoryTool, "_sodamem_hb_installed", False):
        return
    import threading

    _orig = MemoryTool.dispatch

    def _wrapped(self, name: str, **kwargs):
        _heartbeat(f"tool_before_{name}")
        stop = threading.Event()

        def _tick() -> None:
            n = 0
            while not stop.wait(15.0):
                n += 1
                _heartbeat(f"tool_wait_{name}_{n}")

        t = threading.Thread(target=_tick, daemon=True)
        t.start()
        try:
            return _orig(self, name, **kwargs)
        finally:
            stop.set()
            t.join(timeout=1.0)
            _heartbeat(f"tool_after_{name}")

    MemoryTool.dispatch = _wrapped  # type: ignore[method-assign]
    MemoryTool._sodamem_hb_installed = True


def _apply_frozen_echo_if_requested() -> None:
    """Private-repo bridge: run_frozen sets SODAMEM_DEV_ECHO_FP so this
    subprocess opens pre-R2.7 stores. Without it, SodaMem.open() fails closed
    on prompt fingerprint drift (I6)."""
    echo = os.environ.get("SODAMEM_DEV_ECHO_FP", "").strip()
    if not echo:
        return
    import sodamem.memory.storage.store as store_mod

    store_mod.prompt_fingerprint = lambda prompts, _echo=echo: _echo


def _apply_opt_if_requested() -> None:
    """Opt-in Plan B patches from ``sodamem_opt`` (env SODAMEM_OPT_APPLY=1).

    Default off — baseline ``sodamem_dev.run_frozen`` is unchanged. The opt
    runner sets the env flag so each worker re-applies patches in-process.
    """
    if os.environ.get("SODAMEM_OPT_APPLY", "").strip() != "1":
        return
    from sodamem_opt.patches import apply as _opt_apply

    _opt_apply()


def _apply_struct_if_requested() -> None:
    """Opt-in structural answer path (env SODAMEM_STRUCT_APPLY=1).

    Mutually preferred over OPT when both are set: STRUCT replaces answer_one
    and must not stack reader/planner prompt addenda from sodamem_opt.
    """
    if os.environ.get("SODAMEM_STRUCT_APPLY", "").strip() != "1":
        return
    # Ensure Plan B prompt patches are not also applied in this worker.
    os.environ.pop("SODAMEM_OPT_APPLY", None)
    from sodamem_struct.apply import apply as _struct_apply

    _struct_apply()


def _apply_skill_scope_board_if_requested() -> None:
    """Opt-in ScopeBoard skill (env SODAMEM_SKILL_SCOPE_BOARD=1).

    Stacks on Soft/OPT. Does not pop OPT.
    """
    if os.environ.get("SODAMEM_SKILL_SCOPE_BOARD", "").strip() != "1":
        return
    os.environ.pop("SODAMEM_STRUCT_APPLY", None)
    root = os.environ.get("SODAMEM_SKILL_SCOPE_BOARD_ROOT", "").strip()
    if root:
        sys.path.insert(0, root)
    from scope_board_skill.apply import apply as _skill_apply

    _skill_apply()


def _apply_protocol_v1_if_requested() -> None:
    """Opt-in Question Schema Protocol v1.0 (env SODAMEM_PROTOCOL_V1=1).

    Stacks on Soft/OPT (Plan B+). ``apply()`` itself calls ``sodamem_opt.apply``.
    """
    if os.environ.get("SODAMEM_PROTOCOL_V1", "").strip() != "1":
        return
    os.environ.pop("SODAMEM_STRUCT_APPLY", None)
    root = os.environ.get("SODAMEM_PROTOCOL_V1_ROOT", "").strip()
    if root:
        sys.path.insert(0, root)
    # Ensure OPT flag is on so Soft base remains consistent if patches short-circuit.
    os.environ["SODAMEM_OPT_APPLY"] = "1"
    from protocol_v1.apply import apply as _protocol_apply

    _protocol_apply()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-id", required=True)
    args = ap.parse_args()
    _heartbeat("worker_start")
    _install_llm_heartbeat()
    _install_tool_heartbeat()
    _apply_frozen_echo_if_requested()
    # Soft base may stack with scope_board / protocol_v1. STRUCT replaces Soft path.
    if os.environ.get("SODAMEM_STRUCT_APPLY", "").strip() == "1":
        _apply_struct_if_requested()
    else:
        if os.environ.get("SODAMEM_PROTOCOL_V1", "").strip() == "1":
            _apply_protocol_v1_if_requested()
        else:
            if os.environ.get("SODAMEM_OPT_APPLY", "").strip() == "1":
                _apply_opt_if_requested()
            if os.environ.get("SODAMEM_SKILL_SCOPE_BOARD", "").strip() == "1":
                _apply_skill_scope_board_if_requested()
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
        _heartbeat("answer_one_begin")
        # Call through the module attribute so STRUCT/OPT monkey-patches to
        # ``run_s500.answer_one`` are visible (a ``from run_s500 import
        # answer_one`` binding would freeze the pre-patch function).
        row.update(run_s500.answer_one(item, api_key))
        _heartbeat("judge_begin")
        judge_client = OpenAI(
            api_key=api_key, base_url=BASE_URL, timeout=60, max_retries=0
        )
        row["judge"] = run_judge(judge_client, item, row["hypothesis"])
        row["error"] = ""
        _heartbeat("done")
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    payload = json.dumps(row, ensure_ascii=False)
    # Sidecar for the parent if stdout PIPE backs up.
    result_path = os.environ.get("SODAMEM_RESULT_PATH", "").strip()
    if result_path:
        try:
            Path(result_path).write_text(payload, encoding="utf-8")
        except OSError:
            pass
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
