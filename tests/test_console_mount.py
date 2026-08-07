"""Coverage for `server/console_mount.py` and the packaging promise behind it.

`mount_console()` had zero tests, and the repo checkout happens to contain a
built `console/dist/` — so the mount works on any dev machine and the real
gap stayed invisible: the published image never carried the console at all.
`docker compose up` is documented as one-command self-hosting, and `/console`
404'd in every container ever built from this Dockerfile.

That gap cannot be caught by exercising `mount_console()` — the function is
correct. It is a BUILD defect, so the Dockerfile itself is the thing under
test here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="server tests require the [server] extra")
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.console_mount import MOUNT_PATH, mount_console  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO_ROOT / "Dockerfile"


# --- the function itself ----------------------------------------------------

def _app_with_console(dist: Path) -> tuple[FastAPI, bool]:
    app = FastAPI()
    import server.console_mount as cm
    original = cm._CONSOLE_DIST
    cm._CONSOLE_DIST = dist
    try:
        return app, mount_console(app)
    finally:
        cm._CONSOLE_DIST = original


def test_missing_dist_does_not_mount_and_does_not_raise(tmp_path):
    """A backend-only deployment is a supported configuration: no console
    build must never stop the API from starting."""
    app, mounted = _app_with_console(tmp_path / "nope")
    assert mounted is False
    assert TestClient(app).get("/health-does-not-exist").status_code == 404


def test_built_dist_is_served_at_the_mount_path(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>SodaMem Console</title>")
    app, mounted = _app_with_console(dist)
    assert mounted is True
    resp = TestClient(app).get(f"{MOUNT_PATH}/")
    assert resp.status_code == 200
    assert "SodaMem Console" in resp.text


def test_client_side_routes_fall_back_to_the_shell(tmp_path):
    """A hard refresh on /console/memories must serve index.html — the SPA
    router only exists after the shell loads."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>SodaMem Console</title>")
    app, _ = _app_with_console(dist)
    resp = TestClient(app).get(f"{MOUNT_PATH}/memories")
    assert resp.status_code == 200
    assert "SodaMem Console" in resp.text


# --- the packaging promise --------------------------------------------------

def test_dockerfile_builds_the_console():
    """The image must BUILD console/dist, not hope one was committed."""
    text = _DOCKERFILE.read_text()
    assert re.search(r"^FROM\s+node:", text, re.M), (
        "Dockerfile has no node stage — the console cannot be built in-image"
    )
    assert re.search(r"npm\s+(ci|install)", text), "console deps are never installed"
    assert re.search(r"npm\s+run\s+build", text), "console is never built"


def test_dockerignore_keeps_console_sources_in_the_build_context():
    """The Dockerfile can only build what the context contains. `console/`
    used to be excluded wholesale, so the new node stage would have failed on
    `COPY console/package.json` — a Dockerfile-text test cannot see that, and
    a green suite next to a red `docker build` is worse than no test."""
    lines = [
        line.strip() for line in (_REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "console/" not in lines, (
        "console/ is excluded from the build context — the console stage cannot "
        "read package.json or the sources"
    )
    assert "dist/" not in lines, (
        "a bare dist/ pattern also matches console/dist; scope it to /dist/ so "
        "the rule stays about the Python build"
    )
    # node_modules must still be cut: `npm ci` recreates it in-stage.
    assert any(item.endswith("node_modules/") for item in lines)


def test_dockerfile_copies_console_dist_into_the_runtime_image():
    """Building it in a throwaway stage and never copying it out is the same
    404 with extra steps."""
    text = _DOCKERFILE.read_text()
    runtime = text[text.rindex("FROM "):]
    assert re.search(r"COPY\s+--from=\S*console\S*.*dist", runtime), (
        "runtime stage never COPYs the built console/dist"
    )
    assert re.search(r"\./console/dist|/app/console/dist|\.\/console\b", runtime), (
        "console/dist must land where console_mount.py looks: <repo>/console/dist"
    )


# ---------------------------------------------------------------------------
# Build-context resolution.
#
# The earlier `.dockerignore` assertions check specific known-bad patterns.
# This one is general: it applies the ignore rules to the actual repository
# and verifies every `COPY <src>` in the Dockerfile still resolves to
# something. That is the class of defect that shipped here once already —
# `console/` was excluded wholesale, so `COPY console/package.json` would have
# failed at build time while every text-matching test stayed green.
#
# Not a substitute for `docker build`. It cannot catch a failing `npm ci` or a
# bad base image. It catches exactly one thing — "the Dockerfile asks for a
# path the build context does not contain" — and that one thing is invisible
# to every other test here.
#
# The real build was run 0806 and confirms what these assertions stand in for:
# 30 stages green, the console-builder stage produced /console/dist and the
# runtime stage received it, and a container off that image answered
# GET /console/ with 200 and the SPA shell (that route 404'd in every image
# built before the node stage existed). Also verified live: /health ok,
# /v1/memories 401 without a key and 200 with one, /metrics serving the
# Prometheus exposition. Image 968 MB, healthcheck reporting healthy.
# ---------------------------------------------------------------------------

def _dockerignore_patterns() -> list[str]:
    return [
        line.strip()
        for line in (_REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Approximate Docker's matcher: directory patterns prune subtrees,
    leading `/` anchors to the context root."""
    from fnmatch import fnmatchcase
    for raw in patterns:
        if raw.startswith("!"):
            continue  # negations only ever re-include; ignoring them is safe here
        pat = raw.rstrip("/")
        anchored = pat.startswith("/")
        pat = pat.lstrip("/")
        if anchored:
            if rel_path == pat or rel_path.startswith(pat + "/"):
                return True
            continue
        parts = rel_path.split("/")
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if fnmatchcase(candidate, pat) or candidate.startswith(pat + "/"):
                return True
            if fnmatchcase(parts[i], pat):
                return True
    return False


def test_every_dockerfile_copy_source_survives_the_dockerignore():
    patterns = _dockerignore_patterns()
    missing = []
    for line in _DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        if "--from=" in stripped:
            continue  # comes from an earlier stage, not the build context
        args = [a for a in stripped.split()[1:] if not a.startswith("--")]
        if len(args) < 2:
            continue
        for src in args[:-1]:  # last arg is the destination
            if any(ch in src for ch in "*?["):
                continue  # globs: presence is not decidable this cheaply
            if not (_REPO_ROOT / src).exists():
                missing.append(f"{src} (not in repo)")
            elif _is_ignored(src, patterns):
                missing.append(f"{src} (excluded by .dockerignore)")
    assert not missing, (
        "Dockerfile COPY sources unavailable in the build context: "
        + ", ".join(missing)
    )
