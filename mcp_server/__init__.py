"""SodaMem MCP server — exposes the `sodamem` facade as MCP tools for
Claude Desktop / Cursor / any MCP client, over stdio.

Run it with `python -m mcp_server` (see `mcp_server/__main__.py`) or the
`sodamem-mcp` console script (`pyproject.toml`'s `[project.scripts]`).
Configuration is environment-variable driven; see README.md in this
directory for the full list and a copy-pasteable client config block.
"""
from __future__ import annotations

from .main import build_server, main

__all__ = ["build_server", "main"]
