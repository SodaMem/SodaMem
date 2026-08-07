"""Automatic recall and retain, as one implementation.

WHY HOOKS AT ALL, GIVEN WE ALSO SHIP AN MCP SERVER
--------------------------------------------------
MCP puts memory in the model's hands: the tools are there, and the model
calls them when it decides to. In a coding session it mostly does not — it is
busy reading files. Hooks put memory in the HOST's hands: recall runs on
every prompt whether the model thought of it or not, and retain runs on every
turn whether the model thought of it or not. That is the difference between a
memory product and a memory API, and it is why every comparable integration
(Hindsight, mem0) is hooks-first with MCP alongside rather than instead.

ONE IMPLEMENTATION, ADAPTERS ONLY FOR I/O
------------------------------------------
`recall()` and `retain()` below are the whole feature. What differs per
client is only how the event arrives on stdin and how the result must be
printed — so that, and only that, is what an adapter contains. The comparable
projects ship one directory per client, each with its own copy of the recall
logic; four copies of a rule become four different rules the first time one
is fixed.

An adapter for a client whose hook format is not verified is not written.
`generic` reads the common keys and prints plain text (which every hook
system in this category injects as context), which is enough to wire a client
by hand without this file guessing at its schema.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .http import Client, ServiceError
from .project import project_id as derive_project_id

#: UserPromptSubmit blocks the model until the hook returns, on a 30s budget.
#: Recall that is slower than the model is worse than no recall, so the whole
#: round trip is bounded well inside it and a timeout degrades to "no extra
#: context" rather than to a stalled editor.
RECALL_TIMEOUT_S = 6.0

#: Claude Code truncates hook output past 10k characters. Asking for a block
#: that would be cut in half wastes the tokens it spent building it.
RECALL_TOKEN_BUDGET = 1200

#: How many transcript turns one retain call carries. Enough to cover a burst
#: of tool use between two user messages; small enough that the payload stays
#: an ingest and not an upload.
RETAIN_MAX_TURNS = 40


@dataclass
class HookEvent:
    """The subset of any client's hook payload this feature needs.

    `project_id` is a resolved FIELD, not a property that re-derives from
    `cwd` on each read: an explicit `--project-id` has to be able to win, and
    a value that is sometimes computed and sometimes overridden is two rules
    where one will do.
    """
    session_id: str = ""
    cwd: str = ""
    prompt: str = ""
    transcript_path: str = ""
    event: str = ""
    project_id: str = ""


@dataclass(frozen=True)
class HookClient:
    """What one coding tool's hook system actually gives us.

    Four clients, four different spellings of the same three facts, and — the
    part that matters — four different answers to "can a hook inject context
    at all". These are capability statements, not preferences: `cursor` recalls
    once per SESSION rather than once per prompt because Cursor's
    `beforeSubmitPrompt` can read the prompt but cannot inject anything (its
    docs list exactly three events that can, and that is not one of them), and
    neither Cursor nor Codex hands a hook a transcript path, so there is
    nothing for retain to read.

    Where a capability is absent it is left absent. The MCP tool surface still
    covers it — the model can call `add_memories` itself — and `sodamem
    clients` prints the matrix, so nobody installs an integration believing it
    does something it does not.
    """
    name: str
    label: str
    prompt_key: str = "prompt"
    session_key: str = "session_id"
    cwd_key: str = "cwd"
    transcript_key: str = "transcript_path"
    #: Which EMITTER writes the recalled block back.
    emit: str = "plain"
    #: False when this client's recall hook fires before any prompt exists,
    #: so recall must fall back to a whole-project brief.
    per_prompt_recall: bool = True
    can_retain: bool = True


HOOK_CLIENTS: dict[str, HookClient] = {
    c.name: c for c in (
        HookClient(
            name="claude-code", label="Claude Code",
            emit="claude-code",
        ),
        HookClient(
            # Copilot CLI: camelCase payload, and `additionalContext` on
            # stdout is injected into the model-facing prompt.
            name="copilot-cli", label="GitHub Copilot CLI",
            session_key="sessionId", transcript_key="transcriptPath",
            emit="additionalContext",
        ),
        HookClient(
            # Cursor: sessionStart carries no cwd (the hook runs from the
            # project root, so an empty cwd resolves against the process's own
            # working directory — which is the project root) and no prompt.
            name="cursor", label="Cursor",
            prompt_key="", cwd_key="workspace_root",
            transcript_key="", emit="additional_context",
            per_prompt_recall=False, can_retain=False,
        ),
        HookClient(
            # Codex: Claude-Code-shaped event names; SessionStart's stdout is
            # fed to the model as context. No documented transcript path, so
            # no retain.
            name="codex", label="Codex CLI",
            transcript_key="", emit="plain",
            per_prompt_recall=False, can_retain=False,
        ),
        HookClient(
            # Escape hatch for a client whose format is not in this table:
            # standard key names, plain stdout.
            name="generic", label="generic",
        ),
    )
}


# --- state ------------------------------------------------------------------
# Retain is incremental: each call ingests only what is new since the last
# one. The cursor is per session and lives outside the store on purpose — it
# is bookkeeping about a transcript file, not memory.
#
# It has two positions, not one, and that is the whole subtlety. Ingest is
# submitted ASYNC (a Stop hook must not block the editor for the length of an
# LLM extraction), so a 202 means "queued", not "stored". A single cursor
# advanced on 202 loses every turn of a job that later fails — which is
# exactly what happens on a service with no extraction credentials, silently,
# forever. So: `committed` is what is known stored, `pending`/`job_id` is what
# is in flight, and the NEXT call resolves the previous job before deciding
# where to read from.

def _state_dir() -> Path:
    root = Path(os.environ.get(
        "SODAMEM_STATE_DIR", Path.home() / ".sodamem" / "state"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cursor_path(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:120]
    return _state_dir() / f"{safe or 'unknown'}.cursor.json"


@dataclass
class Cursor:
    committed: int = 0
    pending: int = 0
    job_id: str = ""


def _read_cursor(session_id: str) -> Cursor:
    try:
        raw = json.loads(_cursor_path(session_id).read_text())
    except (OSError, ValueError):
        return Cursor()
    if not isinstance(raw, dict):
        return Cursor()
    return Cursor(
        committed=int(raw.get("committed") or 0),
        pending=int(raw.get("pending") or 0),
        job_id=str(raw.get("job_id") or ""),
    )


def _write_cursor(session_id: str, cursor: Cursor) -> None:
    try:
        _cursor_path(session_id).write_text(json.dumps({
            "committed": cursor.committed,
            "pending": cursor.pending,
            "job_id": cursor.job_id,
        }))
    except OSError:
        # A cursor we cannot persist means the next retain re-sends turns.
        # Worth a warning, not a failed hook — the user's session must not
        # break because a state directory is read-only.
        print("sodamem: could not persist retain cursor", file=sys.stderr)


def _resolve_pending(cursor: Cursor, client: Client) -> Cursor:
    """Settle the previous call's in-flight ingest before starting another.

    succeeded -> commit it. failed -> forget it, so those turns are read again
    and resent. still running -> leave it alone and let the next call ask
    again; resending a job that is mid-extraction would duplicate it.
    """
    if not cursor.job_id:
        return cursor
    try:
        job = client.call("GET", f"/v1/jobs/{cursor.job_id}", timeout=5.0)
    except ServiceError:
        # Unknown outcome. Treated as still-running (do not commit, do not
        # rewind) — the one thing that is never right here is guessing.
        return cursor
    status = str(job.get("status") or "")
    if status == "succeeded":
        return Cursor(committed=cursor.pending, pending=0, job_id="")
    if status == "failed":
        print(f"sodamem: previous retain failed ({job.get('error')}); "
              f"those turns will be resent", file=sys.stderr)
        return Cursor(committed=cursor.committed, pending=0, job_id="")
    return cursor


# --- transcript -------------------------------------------------------------

def read_transcript(path: str, *, start: int = 0,
                    limit: int = RETAIN_MAX_TURNS) -> tuple[list[dict], int]:
    """Turns from a JSONL transcript, from line `start`, plus the new cursor.

    Deliberately tolerant: an unparseable line is skipped, not raised on. A
    hook that crashes because one transcript line had a shape it did not
    expect would take the user's session down with it, and the cost of the
    skip is one missing turn.
    """
    turns: list[dict] = []
    line_no = start
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index < start:
                    continue
                line_no = index + 1
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError:
                    continue
                turn = _to_turn(record)
                if turn is not None:
                    turns.append(turn)
                if len(turns) >= limit:
                    break
    except OSError:
        return [], start
    return turns, line_no


def _to_turn(record: dict) -> dict | None:
    """One transcript record projected to {role, content}, or None to skip."""
    if not isinstance(record, dict):
        return None
    message = record.get("message")
    if isinstance(message, dict):
        role = message.get("role") or record.get("type") or "user"
        content = _flatten_content(message.get("content"))
    else:
        role = record.get("role") or record.get("type") or ""
        content = _flatten_content(record.get("content"))
    if role not in ("user", "assistant"):
        return None
    content = content.strip()
    if not content:
        return None
    return {"role": role, "content": content}


def _flatten_content(content) -> str:
    """Anthropic-style content: a string, or a list of typed blocks.

    Only `text` blocks are kept. Tool calls and their results are the bulk of
    a coding transcript by volume and close to noise by value — a memory
    store full of file diffs and directory listings makes every later recall
    worse, which is the failure mode that gets an integration switched off.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


# --- the feature ------------------------------------------------------------

#: What a session-start recall asks for when there is no prompt to ask with.
#: Cursor and Codex only let a hook inject context at SESSION start, before
#: the user has typed anything — so "what do you already know about this
#: repo" is the only question available, and it happens to be a good one.
BRIEF_TERMS = "project conventions decisions setup architecture preferences"
BRIEF_TOKEN_BUDGET = 900


def recall(event: HookEvent, client: Client, user_id: str) -> str:
    """The context block to inject before the model sees the prompt.

    Returns "" for every failure. A memory service that is down, slow, or
    misconfigured must cost the user nothing but the memory — never their
    prompt. This is the single most important line in the file.
    """
    prompt = (event.prompt or "").strip()
    if prompt:
        query, budget, heading = (
            prompt[:2000], RECALL_TOKEN_BUDGET,
            "Relevant memory from previous sessions in this project",
        )
    else:
        # No prompt: this is a session-start hook. Ask about the project
        # itself rather than returning nothing — an empty query would just
        # waste the one injection point these clients give us.
        query, budget, heading = (
            f"{Path(event.cwd).name} {BRIEF_TERMS}", BRIEF_TOKEN_BUDGET,
            "What SodaMem remembers about this project from earlier sessions",
        )
    try:
        result = client.context(
            user_id=user_id, query=query,
            project_id=event.project_id, token_budget=budget,
        )
    except ServiceError as exc:
        print(f"sodamem: recall skipped ({exc})", file=sys.stderr)
        return ""
    text = (result.get("text") or "").strip()
    if not text:
        return ""
    return (
        f"{heading} (SodaMem; each item traces to a real earlier "
        f"conversation):\n{text}"
    )


def retain(event: HookEvent, client: Client, user_id: str) -> int:
    """Ingest whatever is new in the transcript. Returns the turn count."""
    if not event.transcript_path:
        return 0
    cursor = _resolve_pending(_read_cursor(event.session_id), client)
    if cursor.job_id:
        # A previous ingest is still extracting. Submitting the next slice now
        # would race it and duplicate turns; the next hook fires soon enough.
        _write_cursor(event.session_id, cursor)
        return 0

    turns, end = read_transcript(event.transcript_path, start=cursor.committed)
    if not turns:
        return 0
    try:
        accepted = client.add(
            user_id=user_id, messages=turns,
            session_id=event.session_id or "sodamem-hook",
            project_id=event.project_id,
        )
    except ServiceError as exc:
        # Nothing is recorded: the same turns are read again next time. A
        # retry costs a duplicate at worst; not retrying loses the session.
        print(f"sodamem: retain failed, will retry ({exc})", file=sys.stderr)
        return 0
    job_id = str(accepted.get("job_id") or "")
    if job_id:
        _write_cursor(event.session_id, Cursor(committed=cursor.committed,
                                               pending=end, job_id=job_id))
    else:
        # A synchronous service (no job id) has already stored them.
        _write_cursor(event.session_id, Cursor(committed=end))
    return len(turns)


# --- adapters ---------------------------------------------------------------

def parse_event(payload: dict, client: str = "generic",
                project_override: str = "") -> HookEvent:
    """One parser, keyed by the client's own field names.

    The alternative — a parser per client — is four copies of the same six
    lines, which is how the first one to be fixed becomes the only one that
    is right.
    """
    spec = HOOK_CLIENTS.get(client) or HOOK_CLIENTS["generic"]
    cwd = _first_str(payload, spec.cwd_key, "cwd", "workspace_root")
    if not cwd:
        # Cursor's sessionStart carries no directory at all, and its
        # project-level hooks run FROM the project root. Falling back to the
        # process cwd is not a guess there, it is the documented behavior.
        cwd = os.getcwd()
    return HookEvent(
        session_id=_first_str(payload, spec.session_key, "session_id", "sessionId",
                              "conversation_id"),
        cwd=cwd,
        prompt=_first_str(payload, spec.prompt_key, "prompt"),
        transcript_path=_first_str(payload, spec.transcript_key,
                                   "transcript_path", "transcriptPath"),
        event=_first_str(payload, "hook_event_name", "hookEventName"),
        project_id=project_override or derive_project_id(cwd or None),
    )


def _first_str(payload: dict, *keys: str) -> str:
    """First non-empty value among `keys`. An empty key name is skipped, so a
    client that HAS no such field (Cursor has no prompt on sessionStart) is
    expressed by leaving it blank in the table rather than by a branch."""
    for key in keys:
        if not key:
            continue
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def emit_claude_code(context: str) -> None:
    """Claude Code's documented JSON output for UserPromptSubmit."""
    if not context:
        return
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }, sys.stdout)
    sys.stdout.write("\n")


def _emit_key(key: str):
    """Copilot CLI (`additionalContext`) and Cursor (`additional_context`)
    both take a single top-level field. Same emitter, different spelling —
    two rows, not two functions."""
    def emit(context: str) -> None:
        if context:
            json.dump({key: context}, sys.stdout)
            sys.stdout.write("\n")
    return emit


def emit_plain(context: str) -> None:
    """Codex's SessionStart feeds a hook's stdout to the model directly, and
    it is the safe default for anything not in the table."""
    if context:
        sys.stdout.write(context + "\n")


EMITTERS = {
    "claude-code": emit_claude_code,
    "additionalContext": _emit_key("additionalContext"),
    "additional_context": _emit_key("additional_context"),
    "plain": emit_plain,
}
