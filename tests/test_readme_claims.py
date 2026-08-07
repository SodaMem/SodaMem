"""The README makes checkable claims. Check them.

Written after an 0806 audit found six things the README asserted in all eight
languages that the code did not do: a package name that 404s on PyPI, an
`SodaMem.open("./data")` that raised FileNotFoundError, an undefined
`my_extractor`, a `curl` example that 405'd, "9 tools" against 8, and "write
tools are opt-in, off by default" against a server that registered them
unconditionally.

Four of the six were only wrong in prose, and prose has no test suite — which
is exactly why one file said "9 tools" for as long as it did. These tests pin
the claims that CAN be mechanically checked against the code, in every
language at once, so a change on one side fails rather than quietly making a
translation lie.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TRANSLATIONS = sorted((ROOT / "docs" / "i18n").glob("README.*.md"))
ALL_READMES = [README, *TRANSLATIONS]


def _name(path: Path) -> str:
    """pytest id: the filename, so a failure names the language directly."""
    return path.name


@pytest.fixture(scope="module")
def texts() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in ALL_READMES}


def test_translations_are_all_present():
    """A language that silently disappears is a broken switcher link in the
    seven that remain."""
    assert len(TRANSLATIONS) == 7, [p.name for p in TRANSLATIONS]


@pytest.mark.parametrize("path", ALL_READMES, ids=_name)
def test_mcp_tool_count_matches_the_server(path, texts):
    """"N tools" is a number the code owns."""
    pytest.importorskip("mcp", reason="needs the [mcp] extra to count tools")
    from mcp_server.main import build_server

    class _NullBackend:
        def __getattr__(self, name):
            return lambda **kwargs: {}

    n_read = len(build_server(_NullBackend(), allow_write=False)._tool_manager.list_tools())
    n_all = len(build_server(_NullBackend(), allow_write=True)._tool_manager.list_tools())

    counts = {int(m) for m in re.findall(r"(\d+)\s*(?:tools|个工具|つのツール|개 도구|outils|herramientas|Tools|ferramentas)", texts[path])}
    assert counts, f"{path.name} states no MCP tool count"
    assert counts <= {n_read, n_all}, (
        f"{path.name} claims {counts} MCP tools; the server registers "
        f"{n_read} read-only / {n_all} with writes on"
    )


@pytest.mark.parametrize("path", ALL_READMES, ids=_name)
def test_write_tools_are_documented_as_gated_by_the_real_env_var(path, texts):
    """The opt-in claim must name the flag that implements it. Until 0806 the
    claim was there and the flag was not."""
    assert "SODAMEM_MCP_ALLOW_WRITE" in texts[path], (
        f"{path.name} describes the write tools without naming the variable "
        "that actually gates them"
    )


@pytest.mark.parametrize("path", ALL_READMES, ids=_name)
def test_no_undefined_placeholder_in_the_quickstart(path, texts):
    """`extractor=my_extractor` was a NameError in the first code block, and
    `FactEventExtractorV2` appeared in zero markdown files repo-wide — so the
    error message telling you to build one pointed at nothing."""
    assert "my_extractor" not in texts[path]
    assert "FactEventExtractorV2" in texts[path]


@pytest.mark.parametrize("path", ALL_READMES, ids=_name)
def test_curl_examples_send_json_with_a_json_content_type(path, texts):
    """`curl -d` defaults to form-urlencoded, which these routes answer with
    422. Adding POST support to /v1/context fixed the 405 and left this."""
    for block in re.findall(r"curl[^`]*?\n(?:```|\Z)", texts[path]):
        if "-d '{" not in block:
            continue
        assert "application/json" in block, (
            f"{path.name}: curl example posts JSON without a JSON "
            f"Content-Type:\n{block}"
        )


@pytest.mark.parametrize("path", ALL_READMES, ids=_name)
def test_documented_endpoints_exist(path, texts):
    """Every /v1/... path named in prose must be a route the app serves."""
    pytest.importorskip("fastapi", reason="needs the [server] extra")
    from server.routes import register_routes
    from fastapi import FastAPI

    app = FastAPI()
    register_routes(app)
    # The OpenAPI document, not app.routes: recent FastAPI nests included
    # routers behind an internal wrapper object with no `.path`, so walking
    # `app.routes` finds five paths and misses twenty.
    served = set(app.openapi()["paths"])
    assert len(served) > 10, f"route collection is broken, not the README: {served}"

    # Template segments differ between prose ({id}) and code ({memory_id}).
    def shape(p: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", p.rstrip("/"))

    normalized = {shape(p) for p in served}
    for endpoint in set(re.findall(r"/v1/[a-z_/{}*]+", texts[path])):
        endpoint = endpoint.rstrip("/")
        if endpoint.endswith("*"):
            # A wildcard like `/v1/admin/*` claims a family exists.
            prefix = endpoint[:-1]
            assert any(p.startswith(prefix) for p in served), (
                f"{path.name} documents {endpoint}, which matches no route"
            )
            continue
        assert shape(endpoint) in normalized, (
            f"{path.name} documents {endpoint}, which no route serves"
        )
