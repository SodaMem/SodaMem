"""`sodamem install <client>` — write the config a coding tool needs.

Three rules this module does not bend:

1. **Merge, never overwrite.** These are files the user owns and that hold
   other servers, other settings, other people's work. Every write reads the
   existing document, replaces exactly the `sodamem` entry, and leaves the
   rest byte-for-byte where it was.
2. **Back up before the first modification.** A `.sodamem-backup` next to the
   original, written once (never overwritten by a later run, or the second
   install would destroy the pre-install state the first one saved).
3. **Say exactly what changed.** Printed paths, and `--dry-run` to print them
   without touching anything.

Default mode is REMOTE: the generated config points every client at one
running service rather than letting each spawn its own store-opening process.
That is not a preference, it is the correctness constraint from ADR 0001 —
see `mcp_server/backend.py`.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .http import DEFAULT_URL
from .project import project_id as derive_project_id
from .project import repo_root
from .targets import SERVER_NAME, Scope, Target, entry, to_toml_table

#: The command every client is told to run. `sodamem-mcp` is the console
#: script the [mcp] extra installs; sys.executable -m is the fallback for a
#: source checkout where the script is not on PATH.
def mcp_command() -> tuple[str, list[str]]:
    found = shutil.which("sodamem-mcp")
    if found:
        return found, []
    return sys.executable, ["-m", "mcp_server"]


@dataclass
class Plan:
    """What one install would write. Printed as-is by --dry-run."""
    target: Target
    path: Path
    action: str          # "create" | "update"
    backup: Path | None
    detail: str = ""


def build_env(*, user_id: str, api_url: str, api_key: str,
              project_id: str, data_root: str = "") -> dict[str, str]:
    """The env block handed to the MCP server process.

    `SODAMEM_API_URL` present means remote mode; absent means this client's
    server opens the stores itself, which only one client on a machine may do
    (LocalBackend takes the data-root lock). `data_root` is therefore only
    accepted for the deliberate single-client local install.

    SODAMEM_MCP_ALLOW_WRITE is set here and ONLY here. The MCP server ships
    read-only (`mcp_server.config.write_enabled`); retaining what you worked
    on is the entire reason this integration exists, so installing a client
    IS the opt-in — recorded as a line in a config file the user can read,
    edit, or delete, rather than as a default nobody chose.
    """
    env = {"SODAMEM_USER_ID": user_id, "SODAMEM_MCP_ALLOW_WRITE": "true"}
    if data_root:
        env["SODAMEM_DATA_ROOT"] = data_root
    else:
        env["SODAMEM_API_URL"] = api_url or DEFAULT_URL
        if api_key:
            env["SODAMEM_API_KEY"] = api_key
    if project_id:
        env["SODAMEM_PROJECT_ID"] = project_id
    return env


def install(target: Target, *, root: Path, env: dict[str, str],
            dry_run: bool = False) -> Plan:
    path = target.config_path(root)
    command, args = mcp_command()
    body = entry(command, args, env, target)
    action = "update" if path.exists() else "create"
    backup = _backup_path(path) if path.exists() else None

    if dry_run:
        return Plan(target, path, action, backup,
                    detail=json.dumps(body, indent=2, sort_keys=True))

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _back_up_once(path)
    if target.fmt == "toml":
        _write_toml(path, target.root_key, body)
    else:
        _write_json(path, target.root_key, body)
    return Plan(target, path, action, backup)


# --- json -------------------------------------------------------------------

def _write_json(path: Path, root_key: str, body: dict) -> None:
    document = _load_json(path)
    servers = document.get(root_key)
    if not isinstance(servers, dict):
        servers = {}
    servers[SERVER_NAME] = body
    document[root_key] = servers
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        # Refusing beats "helpfully" replacing a file we cannot parse: a
        # hand-edited settings.json with a trailing comma is still the user's
        # settings, and a rewrite would silently discard all of it.
        raise SystemExit(
            f"{path} is not valid JSON ({exc}). Fix or move it, then re-run — "
            f"refusing to overwrite a config that may hold other settings."
        ) from exc
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path} does not contain a JSON object; refusing to edit it.")
    return loaded


# --- toml -------------------------------------------------------------------

def _write_toml(path: Path, root_key: str, body: dict) -> None:
    """Replace (or append) exactly our `[<root_key>.sodamem]` block.

    Text surgery rather than parse-and-reserialize: `tomllib` reads TOML and
    cannot write it, so a round trip would mean either a third-party
    dependency on the hook path or losing every comment and every bit of
    formatting in a file the user maintains by hand.
    """
    section = f"{root_key}.{SERVER_NAME}"
    block = to_toml_table(section, body)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        # our table header, then everything up to the next top-level header
        rf"^\[{re.escape(section)}\][^\[]*(?:^\[{re.escape(section)}\.[^\]]+\][^\[]*)*",
        re.MULTILINE,
    )
    if pattern.search(original):
        updated = pattern.sub(block, original, count=1)
    else:
        separator = "" if not original or original.endswith("\n\n") else (
            "\n" if original.endswith("\n") else "\n\n")
        updated = f"{original}{separator}{block}"
    path.write_text(updated, encoding="utf-8")


# --- backups ----------------------------------------------------------------

def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".sodamem-backup")


def _back_up_once(path: Path) -> Path:
    backup = _backup_path(path)
    if backup.exists():
        # Never re-taken. The point of the backup is the state BEFORE sodamem
        # ever touched this file; a second install overwriting it would leave
        # the user with a "backup" that already contains our changes.
        return backup
    shutil.copy2(path, backup)
    return backup


# --- hooks ------------------------------------------------------------------
#
# Four clients, four hook-config dialects. What differs is only WHERE the file
# lives, WHICH events exist, and HOW deep the command is nested — so that is
# what HOOK_INSTALLS holds, and the merge engine below is written once.
#
# Capabilities are not uniform and are not pretended to be (see
# `hooks.HOOK_CLIENTS`): Claude Code and Copilot CLI recall per PROMPT and can
# retain from a transcript; Cursor and Codex can only inject at SESSION start
# and expose no transcript, so they get a project brief and no retain hook.

def _hook_argv() -> list[str]:
    """How to invoke this CLI, as argv parts.

    A list rather than a string because the fallback is two words
    (`<python> -m sodamem_cli`), and a caller that has to guess where the word
    boundaries are will guess wrong.
    """
    found = shutil.which("sodamem")
    return [found] if found else [sys.executable, "-m", "sodamem_cli"]


def hook_command(action: str, client: str, user_id: str, api_url: str) -> str:
    parts = [*_hook_argv(), "hook", action, "--client", client,
             "--user-id", user_id]
    if api_url:
        parts += ["--api-url", api_url]
    # shlex.quote per ARGUMENT. Quoting the joined string instead — which is
    # what the first cut did — turns the `python -m sodamem_cli` fallback into
    # one quoted word and produces a command no shell can run, which a config
    # file will happily hold forever.
    return " ".join(shlex.quote(p) for p in parts)


@dataclass(frozen=True)
class HookInstall:
    """Where one client's hooks live and how its entries are shaped."""
    client: str
    label: str
    scope: Scope
    path: str
    #: event name -> "recall" | "retain"
    events: dict
    #: How one command becomes one entry under an event.
    style: str            # "nested" | "flat" | "bash"
    root_key: str = "hooks"
    #: Extra top-level keys the file's format requires.
    envelope: dict = field(default_factory=dict)

    def config_path(self, repo_root: Path) -> Path:
        base = Path(os.path.expanduser("~")) if self.scope == "user" else repo_root
        return base / self.path


HOOK_INSTALLS: dict[str, HookInstall] = {
    h.client: h for h in (
        HookInstall(
            client="claude-code", label="Claude Code hooks",
            scope="project", path=".claude/settings.json",
            # Retain on Stop AND SessionEnd: Stop covers the steady state,
            # SessionEnd catches a session closed mid-turn.
            events={"UserPromptSubmit": "recall", "Stop": "retain",
                    "SessionEnd": "retain"},
            style="nested",
        ),
        HookInstall(
            client="codex", label="Codex CLI hooks",
            scope="user", path=".codex/hooks.json",
            # SessionStart only: its stdout is fed to the model as context.
            # Codex documents no transcript path, so there is nothing for a
            # retain hook to read — the MCP `add_memories` tool covers writes.
            events={"SessionStart": "recall"},
            style="nested",
        ),
        HookInstall(
            client="cursor", label="Cursor hooks",
            scope="user", path=".cursor/hooks.json",
            # sessionStart is the only Cursor event that can inject context
            # AND is reachable before work starts. `beforeSubmitPrompt` sees
            # the prompt but cannot inject; `stop` can only auto-submit a
            # follow-up MESSAGE, which is not a thing to do to someone.
            events={"sessionStart": "recall"},
            style="flat", envelope={"version": 1},
        ),
        HookInstall(
            client="copilot-cli", label="GitHub Copilot CLI hooks",
            # Its own file under the hooks directory: every JSON there is
            # loaded, so there is no shared document to merge into and no
            # other tool's entries to preserve.
            scope="user", path=".copilot/hooks/sodamem.json",
            events={"userPromptSubmitted": "recall", "agentStop": "retain"},
            style="bash", envelope={"version": 1},
        ),
    )
}

#: Seconds. Recall blocks the user's prompt, retain does not.
_TIMEOUTS = {"recall": 10, "retain": 30}


def _hook_entry(style: str, command: str, action: str) -> dict:
    timeout = _TIMEOUTS[action]
    if style == "nested":
        # Claude Code / Codex: a matcher object wrapping a list of hooks.
        return {"hooks": [{"type": "command", "command": command,
                           "timeout": timeout}]}
    if style == "bash":
        # Copilot CLI names the command field after the shell it runs in.
        return {"type": "command", "bash": command, "timeoutSec": timeout}
    # Cursor: just a command, and a `timeout` it may or may not read.
    return {"command": command, "timeout": timeout}


def install_hooks(spec: HookInstall, root: Path, *, user_id: str,
                  api_url: str, dry_run: bool = False) -> Plan:
    """Merge our hooks into the client's config without dropping anyone's.

    Our previous entry for an event is removed before ours is appended, so
    re-running install UPDATES our hook instead of stacking a second copy, and
    never touches a hook the user or another tool installed.
    """
    path = spec.config_path(root)
    target = Target(name=f"{spec.client}-hooks", label=spec.label,
                    scope=spec.scope, path=spec.path)
    ours = {
        event: [_hook_entry(spec.style,
                            hook_command(action, spec.client, user_id, api_url),
                            action)]
        for event, action in spec.events.items()
    }
    action_word = "update" if path.exists() else "create"

    if dry_run:
        return Plan(target, path, action_word,
                    _backup_path(path) if path.exists() else None,
                    detail=json.dumps({**spec.envelope, spec.root_key: ours},
                                      indent=2))

    path.parent.mkdir(parents=True, exist_ok=True)
    # A freshly created file has nothing to back up, and reporting a
    # `.sodamem-backup` that does not exist is a line the user might act on.
    backup = _back_up_once(path) if path.exists() else None
    document = _load_json(path)
    document.update({k: v for k, v in spec.envelope.items() if k not in document})
    hooks = document.get(spec.root_key)
    if not isinstance(hooks, dict):
        hooks = {}
    for event, entries in ours.items():
        kept = [e for e in (hooks.get(event) or []) if not _is_ours(e)]
        hooks[event] = kept + entries
    document[spec.root_key] = hooks
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return Plan(target, path, action_word, backup)


def _is_ours(entry) -> bool:
    """True for an entry this installer wrote, in any of the three styles.

    Matching on the serialized entry rather than on one known field: the
    command lives under `command` (Claude Code, Codex, Cursor) or `bash`
    (Copilot), and at the top level or nested one deep. A style-specific check
    that missed one would stack a duplicate hook on every re-install — and
    the duplicate fires, so the user pays for two recalls per prompt.
    """
    if not isinstance(entry, dict):
        return False
    return "sodamem" in json.dumps(entry)


def default_project_id(root: Path | None = None) -> str:
    return derive_project_id(root or repo_root())


def default_user_id() -> str:
    """The OS user, as a starting point. Explicit --user-id always wins."""
    raw = os.environ.get("USER") or os.environ.get("USERNAME") or "default"
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", raw).strip("-") or "default"
    return safe if safe[0].isalnum() else f"u{safe}"
