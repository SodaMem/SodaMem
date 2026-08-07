"""The wheel must contain every package an extra promises.

`pip install sodamem[server]` used to install FastAPI and no server code:
`[tool.setuptools.packages.find].include` listed only `sodamem*` and
`mcp_server*`, and nothing noticed because the dev tree imports from source.
This pins the contract so the next top-level package cannot repeat it.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories that are NOT shipped python packages.
_NOT_PACKAGES = {
    "tests", "docs", "console", "sdk-ts", "benchmarking", "scripts", "data",
}

#: Opt-out marker for a top-level package that exists in a working tree but
#: must not ship. Drop an empty `.not-packaged` file in the directory.
#:
#: The default is still "declared or the test fails" — that default is what
#: caught `server*` missing from the packaging list, and weakening it to a
#: hand-maintained name list would mean the next real omission gets waved
#: through by whoever edits the list. This is the deliberate, visible way to
#: say "not this one", and it lives in the directory it describes rather than
#: in a list somewhere else that can rot.
_OPT_OUT_MARKER = ".not-packaged"


def _top_level_packages() -> set[str]:
    return {
        p.name for p in ROOT.iterdir()
        if p.is_dir()
        and not p.name.startswith((".", "_"))
        and p.name not in _NOT_PACKAGES
        and not (p / _OPT_OUT_MARKER).exists()
        and (p / "__init__.py").exists()
    }


def test_every_top_level_package_is_declared_for_packaging():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    include = cfg["tool"]["setuptools"]["packages"]["find"]["include"]
    declared = {pattern.rstrip("*") for pattern in include}
    missing = {pkg for pkg in _top_level_packages() if pkg not in declared}
    assert not missing, (
        f"these packages exist but would not ship: {sorted(missing)} — add "
        f"'<name>*' to [tool.setuptools.packages.find].include"
    )
