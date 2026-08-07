import importlib.metadata as md


FORBIDDEN_IN_BASE = {"fastapi", "uvicorn", "chromadb"}


def test_base_declared_deps_have_no_web_framework():
    reqs = md.requires("sodamem") or []
    base = [r for r in reqs if "extra ==" not in r]
    names = {r.split()[0].split(">")[0].split("=")[0].split("[")[0].lower() for r in base}
    leaked = names & FORBIDDEN_IN_BASE
    assert not leaked, f"web/heavy deps leaked into base install: {leaked}"


def test_chromadb_is_optional_extra():
    reqs = md.requires("sodamem") or []
    chroma_line = [r for r in reqs if r.lower().startswith("chromadb")]
    assert chroma_line, "chromadb must be declared"
    assert all('extra == "chroma"' in r for r in chroma_line), "chromadb must be gated behind [chroma] extra"


# --- import-coverage gate -----------------------------------------------------
# Audit 0723 found `dateutil` imported at module top level (calendar_resolve)
# while base deps declared only pydantic/numpy/rank-bm25: a clean base install
# crashed on `import sodamem.memory.ingest`. The 288-test suite never caught it
# because the dev env gets python-dateutil transitively (chromadb -> posthog).
# This gate makes the whole class impossible: every third-party import in the
# package must be satisfiable — top-level imports by BASE deps, lazy (inside a
# function) imports by base or a declared extra.

_IMPORT_TO_DIST = {"dateutil": "python-dateutil", "rank_bm25": "rank-bm25"}


def _declared_dists():
    base, extras = set(), set()
    for r in md.requires("sodamem") or []:
        name = r.split(";")[0].split()[0]
        for sep in (">", "<", "=", "[", "!", "~"):
            name = name.split(sep)[0]
        (extras if "extra ==" in r else base).add(name.lower())
    return base, extras


def _third_party_imports():
    import ast
    import pathlib
    import sys
    stdlib = sys.stdlib_module_names
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sodamem"
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text())
        top_level_ids = {id(n) for n in ast.walk(tree) if n in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if m in stdlib or m == "sodamem":
                    continue
                yield m, f"{path.name}:{node.lineno}", id(node) in top_level_ids


def test_every_third_party_import_is_declared():
    base, extras = _declared_dists()
    problems = []
    for mod, loc, is_top in _third_party_imports():
        dist = _IMPORT_TO_DIST.get(mod, mod).lower()
        if is_top and dist not in base:
            problems.append(f"{loc}: top-level `import {mod}` but {dist} not in base deps")
        elif not is_top and dist not in base | extras:
            problems.append(f"{loc}: lazy `import {mod}` but {dist} declared in no extra")
    assert not problems, "undeclared imports:\n" + "\n".join(problems)
