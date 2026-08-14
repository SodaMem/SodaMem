"""Warm-start launcher for the SodaMem MCP server (stdio).

Hermes / DeepSeek Harness / Cursor all spawn this as a subprocess. On Windows,
the first tool call otherwise pays a cold chromadb/numpy/onnxruntime import
inside the MCP event loop and often times out. Pre-import heavy deps first,
then hand off to ``mcp_server.main``.

Usage (absolute paths recommended on Windows)::

    python scripts/sodamem_mcp_warm.py

Or from an installed env::

    python -c "from scripts... "  # prefer pointing the client at this file
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

# Heavy deps: pre-import BEFORE the MCP event loop starts.
import chromadb  # noqa: F401
import numpy  # noqa: F401

try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass

from mcp_server.main import main

if __name__ == "__main__":
    main()
