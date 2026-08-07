"""`sodamem` — the CLI that installs and runs the coding-tool integrations.

Kept OUT of the `sodamem` package on purpose: that package is the embeddable
core library (invariant I1 — a base install pulls no web framework), and an
argparse entry point that shells out to uvicorn does not belong inside it.
"""
from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from .main import main as _main
    return _main(argv)
