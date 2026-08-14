from __future__ import annotations

import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

# `server.settings` needs pydantic-settings, which ships in the [server]
# extra. CI's gate-i1-base-deps collects this tree under a BASE install,
# where the import would otherwise abort collection for the whole run.
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from server.settings import Settings


ROOT = Path(__file__).resolve().parent.parent


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _project_metadata() -> dict:
    return tomllib.loads(_read_text(ROOT / "pyproject.toml"))


def test_mcp_extra_is_bounded_to_supported_major_and_lock_matches():
    metadata = _project_metadata()
    assert metadata["project"]["optional-dependencies"]["mcp"] == [
        "mcp>=1.9.0,<2",
        "sodamem[server]",
    ]

    lock = tomllib.loads(_read_text(ROOT / "uv.lock"))
    locked_mcp = next(package for package in lock["package"] if package["name"] == "mcp")
    assert locked_mcp["version"].split(".", 1)[0] == "1"


def test_ci_full_suite_installs_every_imported_optional_surface():
    """Every optional surface a test imports must be installed by SOME CI job.

    Checked as a union over jobs rather than as one literal install line: the
    framework adapters live in their own job (heavy SDKs, and their resolver
    conflicts must not take the product suite down), so pinning a single
    string would either forbid that split or silently stop covering it.

    The failure this prevents is specific: these suites guard themselves with
    `pytest.importorskip`, so a missing extra does not fail CI — it turns the
    tests into skips and CI stays green while nothing ran.
    """
    workflow = _read_text(ROOT / ".github/workflows/ci.yml")
    installed = set()
    for line in workflow.splitlines():
        marker = 'uv pip install -e ".['
        if marker in line:
            extras = line.split(marker, 1)[1].split("]", 1)[0]
            installed.update(e.strip() for e in extras.split(","))

    # Surfaces whose tests would otherwise skip themselves into invisibility.
    required = {"dev", "chroma", "llm", "mcp", "server",
                "langgraph", "crewai", "openai-agents"}
    missing = required - installed
    assert not missing, f"CI installs no job covering: {sorted(missing)}"


def test_setuptools_discovers_only_the_product_package_roots():
    """`adapters*` joined the list when the framework shells shipped. The rule
    being pinned is not the count — it is that every SHIPPED top-level package
    is listed and nothing else is: `server*` was missing once, and
    `pip install sodamem[server]` then installed FastAPI with no server code
    behind it."""
    metadata = _project_metadata()
    include = metadata["tool"]["setuptools"]["packages"]["find"]["include"]
    assert include == [
        "sodamem*", "sodamem_cli*", "sodamem_opt*", "mcp_server*", "server*", "adapters*"
    ]

    packages = {
        ".".join(path.relative_to(ROOT).parent.parts)
        for path in ROOT.rglob("__init__.py")
        if any(
            fnmatchcase(".".join(path.relative_to(ROOT).parent.parts), pattern)
            for pattern in include
        )
    }
    assert {"sodamem", "sodamem_cli", "sodamem_opt", "mcp_server", "server", "server.routes",
            "adapters"} <= packages
    assert not any(
        package == excluded or package.startswith(f"{excluded}.")
        for package in packages
        for excluded in ("benchmarking", "tests", "console")
    )


def test_built_archives_exclude_test_and_benchmark_payload(tmp_path):
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    sdist = next(tmp_path.glob("*.tar.gz"))

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = [
            name.split("/", 1)[1]
            for name in archive.getnames()
            if "/" in name
        ]

    forbidden = {"benchmarking", "tests", "data", "results", "__pycache__"}
    for names in (wheel_names, sdist_names):
        assert not any(forbidden.intersection(Path(name).parts) for name in names)
        for package_root in ("sodamem", "mcp_server", "server"):
            assert any(name.startswith(f"{package_root}/") for name in names)

    for required in ("LICENSE", "NOTICE", "README.md", "pyproject.toml"):
        assert required in sdist_names


def test_docker_builder_copies_all_packaged_sources_before_frozen_sync():
    dockerfile = _read_text(ROOT / "Dockerfile")
    sync_position = dockerfile.index("RUN uv sync --frozen --no-dev --no-editable")
    for source in ("sodamem", "mcp_server", "server"):
        assert dockerfile.index(f"COPY {source} ./{source}") < sync_position

    assert "COPY --chown=sodamem:sodamem server ./server" in dockerfile
    assert "COPY --from=console-builder" in dockerfile
    assert "/console/dist ./console/dist" in dockerfile


def test_compose_env_file_is_optional_without_auth_bypass_or_default_key():
    compose = _read_text(ROOT / "docker-compose.yml")
    assert re.search(
        r"(?m)^    env_file:\n      - path: \.env\n        required: false$",
        compose,
    )
    assert "SODAMEM_AUTH_DISABLED:" not in compose
    assert "SODAMEM_API_KEY:" not in compose


def test_server_still_fails_closed_without_credentials():
    settings = Settings(api_key="", auth_disabled=False)
    with pytest.raises(RuntimeError, match="SODAMEM_API_KEY is required"):
        settings.require_auth_configured()

    Settings(api_key="", auth_disabled=True).require_auth_configured()


# ---------------------------------------------------------------------------
# R1.10 — release chain.
#
# The go-to-market decision (PRD §5, 0803) is "open source first, cloud only
# after a paying signal". That makes `pip install sodamem` the very first
# thing anyone does, and the PyPI page the storefront. A package that installs
# but has no project links, or a release workflow that can publish something
# CI never tested, is a launch defect — not a polish item.
# ---------------------------------------------------------------------------

def test_project_metadata_carries_links_and_classifiers():
    """A PyPI page with no repository link and no classifiers is a dead end
    for the exact developer the launch is aimed at."""
    meta = _project_metadata()["project"]
    urls = meta.get("urls") or {}
    assert urls, "no [project.urls] — PyPI shows no repository/docs links"
    assert any("github" in v.lower() for v in urls.values()), "no source link"
    classifiers = meta.get("classifiers") or []
    assert classifiers, "no classifiers — the package is unfindable by filter"
    assert any(c.startswith("License ::") for c in classifiers)
    assert any(c.startswith("Programming Language :: Python :: 3") for c in classifiers)


def test_release_workflow_exists_and_is_tag_triggered():
    wf = ROOT / ".github" / "workflows" / "release.yml"
    assert wf.exists(), "no release workflow — nothing publishes the package"
    text = _read_text(wf)
    assert "tags:" in text, "release must be driven by a version tag, not a branch push"


def test_release_workflow_runs_the_suite_before_publishing():
    """Publishing is irreversible: a version number on PyPI can be yanked but
    never reused. Tests must gate the upload, not run beside it."""
    text = _read_text(ROOT / ".github" / "workflows" / "release.yml")
    assert "pytest" in text, "release publishes without running tests"
    assert "needs:" in text, "publish job does not depend on the test job"


def test_release_workflow_uses_trusted_publishing_not_a_long_lived_token():
    """An API token in repo secrets is a standing credential that can publish
    any version at any time. OIDC trusted publishing is scoped to this
    workflow on this repo and expires in minutes."""
    text = _read_text(ROOT / ".github" / "workflows" / "release.yml")
    assert "id-token: write" in text, "no OIDC permission — falls back to a stored token"
    assert "PYPI_API_TOKEN" not in text, "long-lived PyPI token referenced"


def test_version_is_single_sourced_between_python_and_npm():
    """Two version numbers that can disagree WILL disagree. A user reporting
    'sodamem 0.2.1' must mean one thing."""
    py = _project_metadata()["project"]["version"]
    npm = json.loads(_read_text(ROOT / "sdk-ts" / "package.json"))["version"]
    assert py == npm, f"pyproject {py} != sdk-ts/package.json {npm}"


def test_both_packages_carry_the_notice_and_name_the_same_owner():
    """Apache-2.0 §4(d): a distribution of a work that has a NOTICE file must
    carry a readable copy of it. The npm tarball is such a distribution, and
    its `files` allowlist means anything not named there is silently absent.
    """
    root_notice = _read_text(ROOT / "NOTICE")
    assert "FENGRONG WAN" in root_notice
    sdk_notice = ROOT / "sdk-ts" / "NOTICE"
    assert sdk_notice.exists(), "npm package has no NOTICE"
    assert _read_text(sdk_notice) == root_notice, "the two NOTICE files disagree"

    pkg = json.loads(_read_text(ROOT / "sdk-ts" / "package.json"))
    assert "NOTICE" in pkg.get("files", []), "NOTICE is not in the npm files allowlist"
    assert pkg.get("license") == "Apache-2.0"
    assert pkg.get("author"), "npm package names no author"

    py = _project_metadata()["project"]
    assert py["authors"][0]["name"] == pkg["author"], (
        "PyPI and npm name different owners for the same project"
    )


def test_no_dangling_references_to_predecessor_repositories():
    """Comments earn their place by explaining WHY. A path into a repository
    the reader cannot open explains nothing — it just tells them something is
    missing. The reasoning stays; the pointer goes."""
    import re as _re
    # Both spellings: the repository was referred to as `glass_drift` and as
    # `drift-glass`, and a pattern that knew only the first let two
    # `drift-glass-cli` comments sit in a shipped module until 0806.
    dangling = _re.compile(r"memory_core|glass[_-]drift|drift[_-]glass|"
                           r"task-T\d|migration map")
    hits = []
    for sub in ("sodamem", "server", "mcp_server", "adapters", "tests"):
        for path in (ROOT / sub).rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue  # this file names the patterns in order to forbid them
            for i, line in enumerate(_read_text(path).splitlines(), 1):
                if dangling.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{i}")
    assert not hits, "dangling references to a private predecessor: " + ", ".join(hits[:10])
