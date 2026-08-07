"""The client registry: one table, one row per coding tool.

Every MCP client wants the same three facts — run this command, with these
args, with this environment — and then disagrees about where to write them
and what to call the key. VS Code says `servers`, Cursor and Claude Desktop
say `mcpServers`, Zed says `context_servers` and wraps the command in an
object, Codex uses TOML. That is the entire difference between them.

So it lives in a table rather than in nine functions. Adding a client is a
row; it is not a new code path, and it cannot invent a fourth way to write
the same JSON. This is the one thing the integrations directories of
comparable projects get wrong — N copies of the same writer, slowly drifting.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Scope = Literal["user", "project"]

#: The key under which our server is registered, in every client.
SERVER_NAME = "sodamem"


@dataclass(frozen=True)
class Target:
    """One coding tool `sodamem install` knows how to configure."""

    name: str
    label: str
    #: Where the config lives. `project` targets take the repo root.
    scope: Scope
    #: Path relative to $HOME (user scope) or to the repo root (project scope).
    path: str
    #: json | toml — the file's own encoding, not ours to choose.
    fmt: Literal["json", "toml"] = "json"
    #: Top-level key the client reads its server map from.
    root_key: str = "mcpServers"
    #: Some clients nest command/args/env inside a `command` object (Zed).
    nests_command: bool = False
    #: Extra fields merged into the entry (Zed's `source: "custom"`).
    extra_entry: dict = field(default_factory=dict)
    #: Clients whose hook system we can also wire for automatic recall/retain.
    hooks: str = ""
    note: str = ""

    def config_path(self, repo_root: Path) -> Path:
        base = Path(os.path.expanduser("~")) if self.scope == "user" else repo_root
        return base / self.path


TARGETS: dict[str, Target] = {
    t.name: t for t in (
        Target(
            name="claude-code",
            label="Claude Code",
            scope="project",
            path=".mcp.json",
            hooks="claude-code",
            note=(
                "+ UserPromptSubmit/Stop/SessionEnd hooks in "
                ".claude/settings.json: recall per prompt, retain per turn."
            ),
        ),
        Target(
            name="claude-desktop",
            label="Claude Desktop",
            scope="user",
            path=(
                "Library/Application Support/Claude/claude_desktop_config.json"
                if os.uname().sysname == "Darwin"
                else ".config/Claude/claude_desktop_config.json"
            ),
        ),
        Target(
            name="cursor", label="Cursor", scope="user",
            path=".cursor/mcp.json", hooks="cursor",
            note=(
                "+ a sessionStart hook in ~/.cursor/hooks.json: a project "
                "brief at session start. Cursor exposes no transcript to "
                "hooks and its beforeSubmitPrompt cannot inject context, so "
                "there is no per-prompt recall and no retain hook — writes go "
                "through the add_memories tool."
            ),
        ),
        Target(
            name="cursor-project", label="Cursor (this repo only)",
            scope="project", path=".cursor/mcp.json",
        ),
        Target(
            name="vscode", label="VS Code / GitHub Copilot",
            scope="project", path=".vscode/mcp.json",
            # VS Code chose `servers`; using `mcpServers` here means VS Code
            # ignores the file with no error at all.
            root_key="servers",
        ),
        Target(
            name="windsurf", label="Windsurf",
            scope="user", path=".codeium/windsurf/mcp_config.json",
        ),
        Target(
            name="zed", label="Zed",
            scope="user", path=".config/zed/settings.json",
            root_key="context_servers", nests_command=True,
            extra_entry={"source": "custom"},
        ),
        Target(
            name="codex", label="Codex CLI",
            scope="user", path=".codex/config.toml", fmt="toml",
            root_key="mcp_servers", hooks="codex",
            note=(
                "+ a SessionStart hook in ~/.codex/hooks.json (its stdout is "
                "fed to the model): a project brief at session start. Codex "
                "documents no transcript path, so there is no retain hook — "
                "writes go through the add_memories tool."
            ),
        ),
        Target(
            name="codex-project", label="Codex CLI (this repo only)",
            scope="project", path=".codex/config.toml", fmt="toml",
            root_key="mcp_servers",
            note="Codex loads project config only for trusted projects.",
        ),
        Target(
            name="copilot-cli", label="GitHub Copilot CLI",
            scope="user", path=".copilot/mcp-config.json", hooks="copilot-cli",
            note=(
                "+ userPromptSubmitted/agentStop hooks in "
                "~/.copilot/hooks/sodamem.json: recall per prompt, retain per "
                "turn — the same coverage as Claude Code."
            ),
        ),
        Target(
            name="opencode", label="OpenCode",
            scope="user", path=".config/opencode/opencode.json",
            root_key="mcp",
        ),
    )
}


def entry(command: str, args: list[str], env: dict[str, str],
          target: Target) -> dict:
    """The server entry, in whichever shape `target` reads."""
    if target.nests_command:
        body: dict = {"command": {"path": command, "args": list(args)}}
        if env:
            body["command"]["env"] = dict(env)
    else:
        body = {"command": command, "args": list(args)}
        if env:
            body["env"] = dict(env)
    body.update(target.extra_entry)
    return body


def resolve(names: list[str]) -> list[Target]:
    """Names to targets, refusing unknown ones by name.

    A typo must not silently install nothing — that is a user who believes
    they are set up and is not.
    """
    unknown = [n for n in names if n not in TARGETS]
    if unknown:
        raise KeyError(
            f"unknown client(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(TARGETS))}"
        )
    return [TARGETS[n] for n in names]


#: Serializers for the two encodings the table needs. TOML is written by hand
#: rather than pulled from a dependency: the shape is one table per server
#: with three scalar/array fields, and `tomllib` (stdlib) reads but cannot
#: write. The alternative is a runtime dependency on the hook path, which is
#: the one place this project cannot afford one.
def to_toml_table(section: str, body: dict) -> str:
    lines = [f"[{section}]"]
    for key in ("command", "args"):
        if key in body:
            lines.append(f"{key} = {_toml_value(body[key])}")
    if body.get("env"):
        lines.append("")
        lines.append(f"[{section}.env]")
        for k, v in body["env"].items():
            lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _toml_value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

