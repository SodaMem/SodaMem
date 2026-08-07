"""`sodamem` — the command coding-tool integrations are installed and run by.

    sodamem daemon ensure          # the one process that owns the stores
    sodamem install claude-code    # wire a client to it
    sodamem hook recall            # what that client's hooks then call
    sodamem clients                # what can be installed

Everything here is stdlib. `sodamem hook recall` runs as a fresh process on
every prompt the user submits, inside a budget the editor blocks on, so the
import cost of this module is latency a human feels.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import daemon, install as install_mod
from .http import Client, ServiceError
from .hooks import EMITTERS, HOOK_CLIENTS, parse_event, recall, retain
from .project import project_id as derive_project_id
from .project import repo_root
from .targets import TARGETS, resolve


def _add_service_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-url", default="",
                        help="SodaMem service URL (default: $SODAMEM_API_URL "
                             "or http://127.0.0.1:8000)")
    parser.add_argument("--api-key", default="",
                        help="API key (default: $SODAMEM_API_KEY)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sodamem",
        description="Evidence-grounded memory for coding agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- clients ------------------------------------------------------------
    sub.add_parser("clients", help="list the coding tools install supports")

    # -- install ------------------------------------------------------------
    p_install = sub.add_parser(
        "install", help="write a client's MCP (and hook) configuration")
    p_install.add_argument("clients", nargs="+",
                           help=f"one or more of: {', '.join(sorted(TARGETS))}")
    p_install.add_argument("--user-id", default="",
                           help="memory owner (default: your OS username)")
    p_install.add_argument("--project-id", default="",
                           help="repo scope (default: derived from the git root)")
    p_install.add_argument("--no-project", action="store_true",
                           help="do not scope this client to a project")
    p_install.add_argument("--local-store", default="",
                           help="DANGEROUS with more than one client: have "
                                "this client open the stores itself, at this "
                                "data root, instead of using the service")
    p_install.add_argument("--no-hooks", action="store_true",
                           help="MCP tools only — skip automatic recall/retain")
    p_install.add_argument("--dry-run", action="store_true",
                           help="print what would be written, write nothing")
    p_install.add_argument("--root", default="",
                           help="repo root for project-scoped configs "
                                "(default: the git root above the cwd)")
    _add_service_flags(p_install)

    # -- daemon -------------------------------------------------------------
    p_daemon = sub.add_parser("daemon", help="the single store-owning service")
    d_sub = p_daemon.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("ensure", "start one if none is answering (idempotent)"),
        ("status", "what is actually answering"),
        ("stop", "stop the one this machine started"),
    ):
        p = d_sub.add_parser(action, help=help_text)
        _add_service_flags(p)
        if action == "ensure":
            p.add_argument("--data-root", default="",
                           help="where stores live (default: ~/.sodamem/data)")

    # -- hook ---------------------------------------------------------------
    p_hook = sub.add_parser(
        "hook", help="called by a coding tool's hooks; reads its event on stdin")
    p_hook.add_argument("action", choices=["recall", "retain"])
    p_hook.add_argument("--client", default="generic",
                    choices=sorted(HOOK_CLIENTS))
    p_hook.add_argument("--user-id", default="")
    p_hook.add_argument("--project-id", default="")
    _add_service_flags(p_hook)

    return parser


# --- commands ---------------------------------------------------------------

def cmd_clients(_args) -> int:
    width = max(len(name) for name in TARGETS)
    for name in sorted(TARGETS):
        target = TARGETS[name]
        marker = " (+hooks)" if target.hooks else ""
        print(f"  {name:<{width}}  {target.label}{marker}")
        print(f"  {'':<{width}}  -> {target.path} ({target.scope} scope)")
        if target.note:
            print(f"  {'':<{width}}     {target.note}")
    return 0


def cmd_install(args) -> int:
    try:
        targets = resolve(args.clients)
    except KeyError as exc:
        print(str(exc).strip('"'), file=sys.stderr)
        return 2

    root = Path(args.root).resolve() if args.root else repo_root()
    user_id = args.user_id or install_mod.default_user_id()
    project_id = "" if args.no_project else (
        args.project_id or derive_project_id(root))
    env = install_mod.build_env(
        user_id=user_id, api_url=args.api_url, api_key=args.api_key,
        project_id=project_id, data_root=args.local_store,
    )

    plans = []
    for target in targets:
        plans.append(install_mod.install(target, root=root, env=env,
                                         dry_run=args.dry_run))
        spec = install_mod.HOOK_INSTALLS.get(target.hooks)
        if spec and not args.no_hooks:
            plans.append(install_mod.install_hooks(
                spec, root, user_id=user_id, api_url=args.api_url,
                dry_run=args.dry_run,
            ))

    verb = "would write" if args.dry_run else "wrote"
    for plan in plans:
        print(f"{verb} {plan.path} ({plan.action}) — {plan.target.label}")
        if plan.backup and not args.dry_run:
            print(f"  backup: {plan.backup}")
        if plan.detail:
            print(_indent(plan.detail))

    print()
    print(f"  user_id:    {user_id}")
    print(f"  project_id: {project_id or '(none — memories are not repo-scoped)'}")
    if args.local_store:
        print(f"  mode:       LOCAL store at {args.local_store}")
        print("  NOTE: only ONE client may use local mode per data root — a "
              "second one refuses to start rather than corrupt it.")
    else:
        print(f"  mode:       remote, via {args.api_url or 'http://127.0.0.1:8000'}")
        if not args.dry_run:
            print("  next:       sodamem daemon ensure")
    return 0


def cmd_daemon(args) -> int:
    if args.action == "status":
        result = daemon.status(args.api_url, args.api_key)
    elif args.action == "stop":
        result = daemon.stop()
    else:
        result = daemon.ensure(url=args.api_url, api_key=args.api_key,
                               data_root=getattr(args, "data_root", ""))
    print(json.dumps(result, indent=2))
    # An `ensure` that did not end with a healthy service is a failure, and a
    # hook chain that treats it as success would fail later and less clearly.
    if args.action == "ensure" and not result.get("running"):
        return 1
    if args.action == "status" and not result.get("running"):
        return 1
    return 0


def cmd_hook(args) -> int:
    """Never fails the host.

    A hook's exit code gates the user's prompt. Memory being unavailable is
    not a reason to stop someone from talking to their editor, so every error
    path here reports on stderr and returns 0. The one thing this command
    must never do is take a session down with it.
    """
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("sodamem: hook input was not JSON; nothing to do", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0

    event = parse_event(payload, args.client, args.project_id)
    user_id = args.user_id or install_mod.default_user_id()
    client = Client(args.api_url or None, args.api_key or None,
                    timeout=6.0 if args.action == "recall" else 30.0)
    try:
        if args.action == "recall":
            emit = EMITTERS[HOOK_CLIENTS[args.client].emit]
            emit(recall(event, client, user_id))
        else:
            retain(event, client, user_id)
    except ServiceError as exc:
        print(f"sodamem: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"sodamem: hook error ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


COMMANDS = {
    "clients": cmd_clients,
    "install": cmd_install,
    "daemon": cmd_daemon,
    "hook": cmd_hook,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
