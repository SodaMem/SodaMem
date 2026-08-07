"""Which project a call belongs to, derived from a directory.

The rule: a project is a **repository**, identified by the path of its main
working tree. Two properties fall out of that, and both matter more than they
look:

  * a git WORKTREE resolves to its parent repository. `git worktree add` is
    how a lot of agent tooling isolates a task (this repo's own pipeline does
    it), and one branch per task must not mean one memory bank per task —
    that is precisely the memory you wanted carried across.
  * a subdirectory resolves to the repository root, so running an agent from
    `packages/api` and from the repo root is one project, not two.

Outside a repository the directory itself is the project. Not a fallback to
"unscoped": a user working in a plain folder still expects its memories kept
apart from another folder's.

No git subprocess is spawned — this runs on every prompt through the hook
path, and `.git` is read directly (it is a directory in a normal clone, and a
one-line `gitdir:` pointer file in a worktree).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

#: Same allowlist `server.stores.validate_user_id` enforces, applied here so a
#: derived id is never rejected downstream by a rule this module did not know
#: about. Anything outside it is replaced, not dropped, so two different names
#: cannot collapse into one.
_SAFE = re.compile(r"[^A-Za-z0-9._-]")

#: Long enough that a collision needs deliberate effort, short enough that the
#: id stays readable in a config file or a log line.
_HASH_LEN = 8


def repo_root(start: str | Path | None = None) -> Path:
    """The main working tree containing `start`, or `start` itself."""
    path = Path(start or Path.cwd()).resolve()
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate
        if marker.is_file():
            main = _worktree_parent(marker)
            if main is not None:
                return main
            return candidate
    return path


def _worktree_parent(dotgit_file: Path) -> Path | None:
    """Resolve a linked worktree's `.git` FILE back to the main working tree.

    The file holds `gitdir: /path/to/main/.git/worktrees/<name>`; the main
    working tree is that path's third parent. Returns None rather than
    guessing if the layout is anything else — an unreadable pointer must fall
    back to "this directory is the project", never to a wrong project.
    """
    try:
        text = dotgit_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.split(":", 1)[1].strip())
    if gitdir.parent.name != "worktrees":
        return None
    main_git_dir = gitdir.parent.parent      # .../main/.git
    if main_git_dir.name != ".git":
        return None
    return main_git_dir.parent


def project_id(start: str | Path | None = None) -> str:
    """A stable, filesystem-safe id for the project containing `start`.

    `<basename>-<hash of the absolute path>`. The name is there so a human can
    read it; the hash is there because two checkouts of different repos are
    routinely called `api`, and silently merging their memories would be worse
    than any amount of ugliness in an id.
    """
    root = repo_root(start)
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:_HASH_LEN]
    name = _SAFE.sub("-", root.name) or "project"
    # The allowlist also requires an alphanumeric first character.
    if not name[0].isalnum():
        name = f"p{name}"
    return f"{name}-{digest}"[:128]
