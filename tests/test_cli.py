"""`sodamem_cli` — install, project resolution, and the hook path.

Three things are defended here, in rough order of how expensive they are to
get wrong:

1. **install must never damage a config it did not write.** These files hold
   other MCP servers, other settings, and hand-written comments. A merge that
   drops any of that is not a bug report, it is a support thread.
2. **a hook must never break the host.** `hook recall` gates the user's
   prompt. Whatever is wrong with the memory service — down, slow, 401,
   returning nonsense — the exit code is 0 and the session continues.
3. **retain must not lose turns.** Ingest is submitted async, so a 202 is not
   a durable write; the cursor may only advance once the job is confirmed.

The end-to-end test runs a real service in-process with the extractor's own
deterministic offline fallback (`create_provider -> None`), so retain and
recall are exercised through real HTTP with no LLM and no network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="CLI e2e needs the [server] extra")
pytest.importorskip("pydantic_settings", reason="CLI e2e needs the [server] extra")
pytest.importorskip("uvicorn", reason="CLI e2e needs uvicorn")

from sodamem_cli import hooks  # noqa: E402
from sodamem_cli.http import Client  # noqa: E402
from sodamem_cli.main import main  # noqa: E402
from sodamem_cli.project import project_id, repo_root  # noqa: E402
from sodamem_cli.targets import TARGETS  # noqa: E402
from ._service import free_port, reset_server_singletons, running_service  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


# --- project resolution -----------------------------------------------------

def test_a_subdirectory_resolves_to_the_repository(tmp_path):
    repo = tmp_path / "acme"
    (repo / "packages" / "api").mkdir(parents=True)
    _git(repo, "init", "-q", ".")

    assert repo_root(repo / "packages" / "api") == repo.resolve()
    assert project_id(repo / "packages" / "api") == project_id(repo)


def test_a_git_worktree_resolves_to_its_main_repository(tmp_path):
    """The one that matters for agent tooling.

    `git worktree add` per task is how a lot of agent orchestration isolates
    work — this repository's own pipeline does it. One branch per task must
    not mean one memory bank per task; that is precisely the memory you wanted
    carried from the last task to the next.
    """
    repo = tmp_path / "acme"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hi")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    tree = tmp_path / "acme-worktrees" / "task-1"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "feat/task-1")

    assert (tree / ".git").is_file()          # a worktree's .git is a pointer
    assert repo_root(tree) == repo.resolve()
    assert project_id(tree) == project_id(repo)


def test_two_repositories_with_the_same_name_are_different_projects(tmp_path):
    a, b = tmp_path / "one" / "api", tmp_path / "two" / "api"
    for path in (a, b):
        path.mkdir(parents=True)
        _git(path, "init", "-q", ".")
    assert project_id(a) != project_id(b)
    assert project_id(a).startswith("api-")


def test_a_plain_directory_is_still_its_own_project(tmp_path):
    plain = tmp_path / "notes"
    plain.mkdir()
    assert repo_root(plain) == plain.resolve()
    assert project_id(plain).startswith("notes-")


# --- install ----------------------------------------------------------------

def _install(args: list[str]) -> int:
    return main(args)


def test_install_merges_and_leaves_everything_else_alone(tmp_path, capsys):
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps({
        "mcpServers": {"other": {"command": "foo", "args": ["bar"]}},
        "someOtherKey": 42,
    }))

    _install(["install", "claude-code", "--root", str(tmp_path),
              "--user-id", "alice", "--no-hooks"])

    document = json.loads(config.read_text())
    assert document["someOtherKey"] == 42, "an unrelated top-level key was dropped"
    assert document["mcpServers"]["other"] == {"command": "foo", "args": ["bar"]}
    assert document["mcpServers"]["sodamem"]["env"]["SODAMEM_USER_ID"] == "alice"


def test_install_backs_up_once_and_never_overwrites_the_backup(tmp_path):
    config = tmp_path / ".mcp.json"
    original = json.dumps({"mcpServers": {}, "marker": "pre-install"})
    config.write_text(original)

    _install(["install", "claude-code", "--root", str(tmp_path), "--no-hooks"])
    _install(["install", "claude-code", "--root", str(tmp_path), "--no-hooks"])

    backup = tmp_path / ".mcp.json.sodamem-backup"
    # Still the PRE-install state after two runs — a backup that had been
    # re-taken on the second run would already contain our own edits, which
    # makes it useless for the only thing a backup is for.
    assert json.loads(backup.read_text())["marker"] == "pre-install"
    assert "sodamem" not in json.loads(backup.read_text())["mcpServers"]


def test_install_refuses_to_rewrite_a_config_it_cannot_parse(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text('{"mcpServers": {"other": {},}}')  # trailing comma

    with pytest.raises(SystemExit) as exc:
        _install(["install", "claude-code", "--root", str(tmp_path), "--no-hooks"])

    assert "not valid JSON" in str(exc.value)
    assert config.read_text() == '{"mcpServers": {"other": {},}}'


def test_vscode_gets_its_own_root_key(tmp_path):
    """VS Code reads `servers`. Writing `mcpServers` there is ignored with no
    error at all, which is the worst possible failure mode for an installer."""
    _install(["install", "vscode", "--root", str(tmp_path)])
    document = json.loads((tmp_path / ".vscode" / "mcp.json").read_text())
    assert "servers" in document
    assert "mcpServers" not in document


def test_zed_nests_the_command_object(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _install(["install", "zed", "--root", str(tmp_path)])
    document = json.loads((tmp_path / ".config" / "zed" / "settings.json").read_text())
    server = document["context_servers"]["sodamem"]
    assert server["source"] == "custom"
    assert "path" in server["command"] and "args" in server["command"]


def test_codex_toml_keeps_comments_and_other_servers(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '# hand-written, do not lose me\n'
        'model = "gpt-5"\n\n'
        '[mcp_servers.existing]\n'
        'command = "npx"\n'
        'args = ["existing-mcp"]\n'
    )

    _install(["install", "codex-project", "--root", str(tmp_path)])

    text = config.read_text()
    assert "# hand-written, do not lose me" in text
    import tomllib
    parsed = tomllib.loads(text)
    assert parsed["model"] == "gpt-5"
    assert set(parsed["mcp_servers"]) == {"existing", "sodamem"}
    assert parsed["mcp_servers"]["existing"]["command"] == "npx"


def test_reinstalling_replaces_our_toml_table_instead_of_duplicating_it(tmp_path):
    for _ in range(3):
        _install(["install", "codex-project", "--root", str(tmp_path)])
    text = (tmp_path / ".codex" / "config.toml").read_text()
    assert text.count("[mcp_servers.sodamem]") == 1


def test_reinstalling_replaces_our_claude_hook_instead_of_stacking_it(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "someone-elses-hook"}]}
        ]}
    }))

    for _ in range(3):
        _install(["install", "claude-code", "--root", str(tmp_path)])

    document = json.loads(settings.read_text())
    entries = document["hooks"]["UserPromptSubmit"]
    ours = [e for e in entries if "sodamem" in json.dumps(e)]
    theirs = [e for e in entries if "someone-elses-hook" in json.dumps(e)]
    assert len(ours) == 1, "re-running install stacked duplicate hooks"
    assert len(theirs) == 1, "install dropped a hook it did not own"


def test_the_generated_hook_command_is_runnable(tmp_path):
    """A quoting bug here produces a command no shell can run, and a config
    file will hold it forever without complaint."""
    import shlex

    _install(["install", "claude-code", "--root", str(tmp_path)])
    document = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    command = document["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    parts = shlex.split(command)
    assert "hook" in parts and "recall" in parts
    assert Path(parts[0]).exists(), f"hook binary {parts[0]!r} does not exist"


def test_unknown_client_is_refused_by_name(tmp_path, capsys):
    assert _install(["install", "emacs", "--root", str(tmp_path)]) == 2
    assert "unknown client" in capsys.readouterr().err


def test_every_registered_target_installs(tmp_path, monkeypatch):
    """A row in the table that no code path can write is a client we would
    advertise and not support."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in sorted(TARGETS):
        assert _install(["install", name, "--root", str(tmp_path),
                         "--no-hooks"]) == 0
        assert TARGETS[name].config_path(tmp_path).exists()


# --- per-client hook dialects -----------------------------------------------
#
# Four clients, four hook-config formats and four stdin vocabularies. Every
# one of these was read off that client's own documentation; a wrong key or a
# wrong nesting level does not raise anywhere, it just silently never fires.

@pytest.mark.parametrize("client,path,check", [
    ("claude-code", ".claude/settings.json",
     # matcher object wrapping a hooks list, `command`, `timeout`
     lambda d: d["hooks"]["UserPromptSubmit"][0]["hooks"][0]["type"] == "command"),
    ("cursor", "home/.cursor/hooks.json",
     # version envelope, flat entry, `command`
     lambda d: d["version"] == 1 and "command" in d["hooks"]["sessionStart"][0]),
    ("codex", "home/.codex/hooks.json",
     # Claude-Code-shaped nesting, PascalCase events
     lambda d: d["hooks"]["SessionStart"][0]["hooks"][0]["type"] == "command"),
    ("copilot-cli", "home/.copilot/hooks/sodamem.json",
     # `bash` (not `command`) and `timeoutSec` (not `timeout`)
     lambda d: "bash" in d["hooks"]["userPromptSubmitted"][0]
     and "timeoutSec" in d["hooks"]["userPromptSubmitted"][0]),
])
def test_each_client_gets_its_own_hook_dialect(tmp_path, monkeypatch, client,
                                               path, check):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _install(["install", client, "--root", str(tmp_path), "--user-id", "alice"])
    document = json.loads((tmp_path / path).read_text())
    assert check(document), f"{client}: wrong hook shape\n{json.dumps(document, indent=2)}"


def test_clients_without_a_transcript_get_no_retain_hook(tmp_path, monkeypatch):
    """Capability honesty.

    Cursor exposes no transcript to any hook, and Codex documents none — so
    there is nothing for retain to read, and wiring a retain hook there would
    install something that runs on every turn and can only ever do nothing.
    Writes for those clients go through the MCP add_memories tool instead.
    """
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for client, path in (("cursor", "home/.cursor/hooks.json"),
                         ("codex", "home/.codex/hooks.json")):
        _install(["install", client, "--root", str(tmp_path)])
        text = (tmp_path / path).read_text()
        assert "hook recall" in text
        assert "hook retain" not in text, (
            f"{client} got a retain hook it has no transcript to feed"
        )


@pytest.mark.parametrize("client,expected_key", [
    ("claude-code", "hookSpecificOutput"),
    ("copilot-cli", "additionalContext"),
    ("cursor", "additional_context"),
])
def test_recall_is_emitted_in_each_clients_output_schema(client, expected_key,
                                                         monkeypatch, capsys):
    """One recall implementation, four output spellings. Emitting Claude
    Code's envelope to Cursor injects nothing at all — and nothing about that
    failure is visible from either side."""
    from sodamem_cli.hooks import EMITTERS, HOOK_CLIENTS

    EMITTERS[HOOK_CLIENTS[client].emit]("remembered text")
    assert expected_key in capsys.readouterr().out


def test_codex_recall_is_emitted_as_plain_stdout(capsys):
    """Codex feeds a SessionStart hook's stdout to the model directly — a JSON
    envelope would be injected as literal JSON."""
    from sodamem_cli.hooks import EMITTERS, HOOK_CLIENTS

    EMITTERS[HOOK_CLIENTS["codex"].emit]("remembered text")
    out = capsys.readouterr().out
    assert out.strip() == "remembered text"
    assert not out.lstrip().startswith("{")


def test_copilot_camelcase_payload_is_understood(tmp_path):
    """Copilot CLI says sessionId/transcriptPath where Claude Code says
    session_id/transcript_path. Same parser, different row in the table."""
    from sodamem_cli.hooks import parse_event

    event = parse_event({
        "sessionId": "s-42", "cwd": str(tmp_path),
        "transcriptPath": "/tmp/t.jsonl", "prompt": "why is CI red?",
    }, client="copilot-cli")

    assert event.session_id == "s-42"
    assert event.transcript_path == "/tmp/t.jsonl"
    assert event.prompt == "why is CI red?"


def test_cursor_session_start_payload_falls_back_to_the_process_cwd(tmp_path,
                                                                    monkeypatch):
    """Cursor's sessionStart carries no directory at all — and its
    project-level hooks run FROM the project root, so the process cwd IS the
    project. Without this the whole session would be scoped to whatever
    directory Cursor happened to launch from."""
    from sodamem_cli.hooks import parse_event
    from sodamem_cli.project import project_id as pid

    repo = tmp_path / "acme"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    monkeypatch.chdir(repo)

    event = parse_event({"session_id": "s1", "composer_mode": "agent"},
                        client="cursor")

    assert event.prompt == ""          # nothing to recall ON
    assert event.project_id == pid(repo)


# --- transcript handling ----------------------------------------------------

def test_tool_calls_are_dropped_from_retained_turns(tmp_path):
    """Tool calls are most of a coding transcript by volume and close to noise
    by value. A store full of diffs and directory listings makes every later
    recall worse — which is how an integration ends up switched off."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {
            "role": "user", "content": "we retry with exponential backoff"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "understood"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        ]}}) + "\n"
        + json.dumps({"type": "system", "content": "ignore me"}) + "\n"
    )

    turns, cursor = hooks.read_transcript(str(transcript))

    assert cursor == 3
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[1]["content"] == "understood"
    assert "ls -la" not in json.dumps(turns)


def test_a_corrupt_transcript_line_is_skipped_not_raised(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "{not json at all\n"
        + json.dumps({"type": "user", "message": {"role": "user", "content": "ok"}}) + "\n"
    )
    turns, _ = hooks.read_transcript(str(transcript))
    assert [t["content"] for t in turns] == ["ok"]


# --- the never-break-the-host contract --------------------------------------

def _run_hook(action: str, payload: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sodamem_cli", "hook", action,
         "--client", "claude-code", "--user-id", "alice", *extra],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(REPO), timeout=60,
    )


def test_recall_against_a_dead_service_still_exits_zero(tmp_path):
    """The single most important assertion in this file. A hook's exit code
    gates the user's prompt; memory being unavailable is not a reason to stop
    someone from talking to their editor."""
    dead = f"http://127.0.0.1:{free_port()}"
    result = _run_hook("recall", {
        "session_id": "s1", "cwd": str(tmp_path),
        "prompt": "how do we handle retries?",
    }, "--api-url", dead)

    assert result.returncode == 0
    assert result.stdout.strip() == ""          # nothing injected
    assert "cannot reach" in result.stderr      # but not silent either


def test_recall_with_garbage_on_stdin_still_exits_zero(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "sodamem_cli", "hook", "recall",
         "--client", "claude-code", "--user-id", "alice"],
        input="not json", capture_output=True, text=True,
        cwd=str(REPO), timeout=60,
    )
    assert result.returncode == 0


# --- end to end -------------------------------------------------------------

@pytest.fixture
def live_service(tmp_path, monkeypatch):
    """A real service, in-process, with the extractor's offline fallback."""
    from server.app import create_app
    from server.control import release_data_root_lock
    from server.settings import Settings

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("SODAMEM_DATA_ROOT", str(data_root))
    monkeypatch.setenv("SODAMEM_AUTH_DISABLED", "true")
    monkeypatch.setenv("SODAMEM_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SODAMEM_LLM_API_KEY", "unused-test-key")
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    reset_server_singletons()

    app = create_app(Settings(data_root=data_root, auth_disabled=True,
                              llm_provider="openai", llm_api_key="unused-test-key"))
    try:
        with running_service(app) as base_url:
            yield base_url
    finally:
        reset_server_singletons()
        release_data_root_lock(data_root)


def test_retain_then_recall_round_trip(live_service, tmp_path, monkeypatch):
    """What a user actually experiences: something said in one session comes
    back as context in the next."""
    monkeypatch.setenv("SODAMEM_STATE_DIR", str(tmp_path / "state"))
    client = Client(live_service, "")
    project = "acme-test"

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "role": "user",
        "content": "we always deploy the api behind the blue-green switch",
    }}) + "\n")

    event = hooks.HookEvent(session_id="s-round-trip", cwd=str(tmp_path),
                            transcript_path=str(transcript), project_id=project)
    assert hooks.retain(event, client, "alice") == 1

    _await_jobs(client, "alice")

    ask = hooks.HookEvent(session_id="s2", cwd=str(tmp_path),
                          prompt="how do we deploy the api?", project_id=project)
    context = hooks.recall(ask, client, "alice")
    assert "blue-green" in context


def test_a_promptless_hook_recalls_a_project_brief(live_service, tmp_path,
                                                   monkeypatch):
    """Cursor and Codex can only inject at SESSION start, before the user has
    typed anything. Returning "" there would waste the one injection point
    those clients give us, so a promptless recall asks about the project
    itself."""
    monkeypatch.setenv("SODAMEM_STATE_DIR", str(tmp_path / "state"))
    client = Client(live_service, "")
    project = "acme-test"

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "role": "user",
        "content": "this project always deploys behind the blue-green switch",
    }}) + "\n")
    hooks.retain(hooks.HookEvent(session_id="s-brief", cwd=str(tmp_path),
                                 transcript_path=str(transcript),
                                 project_id=project), client, "alice")
    _await_jobs(client, "alice")

    # A sessionStart-shaped event: a session id, a directory, and no prompt.
    brief = hooks.recall(
        hooks.HookEvent(session_id="s2", cwd=str(tmp_path), project_id=project),
        client, "alice",
    )

    assert brief, "a promptless recall returned nothing at all"
    assert "blue-green" in brief
    assert "about this project" in brief   # the design heading, not the per-prompt one


def test_the_cursor_does_not_advance_past_a_failed_ingest(live_service, tmp_path,
                                                          monkeypatch):
    """The hole the first cut of this shipped with.

    Ingest is submitted async, so a 202 means "queued", not "stored". A cursor
    advanced on 202 silently discards every turn of a job that later fails —
    which is the steady state on a service with no extraction credentials.
    """
    monkeypatch.setenv("SODAMEM_STATE_DIR", str(tmp_path / "state"))
    client = Client(live_service, "")

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "role": "user", "content": "the cache ttl is ninety seconds"}}) + "\n")
    event = hooks.HookEvent(session_id="s-fail", cwd=str(tmp_path),
                            transcript_path=str(transcript), project_id="p")

    assert hooks.retain(event, client, "alice") == 1
    cursor = hooks._read_cursor("s-fail")
    # Not committed yet — a job id is outstanding.
    assert cursor.committed == 0
    assert cursor.pending == 1 and cursor.job_id

    # Pretend that job failed, exactly as a credential-less service would.
    monkeypatch.setattr(
        Client, "call",
        lambda self, method, path, **kw: {"status": "failed", "error": "no provider"}
        if path.startswith("/v1/jobs/") else Client.call(self, method, path, **kw),
    )
    resolved = hooks._resolve_pending(cursor, client)
    assert resolved.committed == 0, "a failed ingest advanced the cursor"
    assert resolved.job_id == ""


def _await_jobs(client: Client, user_id: str, timeout: float = 30.0) -> None:
    """Ingest is async; wait for it to land rather than sleeping and hoping."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        listed = client.call("GET", "/v1/memories", query={"user_id": user_id})
        if listed.get("total"):
            return
        time.sleep(0.1)
    raise AssertionError("ingest never landed")
