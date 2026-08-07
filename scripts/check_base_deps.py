"""CI gate I1: base install must not carry a web framework or chromadb.

Run in a venv installed WITHOUT extras. Fails (exit 1) if any forbidden
distribution is importable/installed.
"""
import importlib.metadata as md
import sys

FORBIDDEN = {"fastapi", "uvicorn", "chromadb", "starlette"}


def main() -> int:
    installed = {d.metadata["Name"].lower() for d in md.distributions()}
    leaked = FORBIDDEN & installed
    if leaked:
        print(f"I1 FAIL: forbidden distributions present in base install: {sorted(leaked)}", file=sys.stderr)
        return 1
    print("I1 OK: base install is web-framework-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
