"""`python -m sodamem_cli` — the fallback when the console script is not on
PATH (a source checkout, or an install without the entry point on $PATH).
`sodamem install` writes whichever of the two it can find."""
from __future__ import annotations

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
