"""Give the process a logging configuration when nobody else did.

Issue #19: under `sodamem daemon ensure` (and under the Dockerfile CMD, which
is the same `uvicorn --factory` path) every INFO record written by `server/`
and `sodamem/` was dropped before it reached the daemon log. Two independent
causes, both measured on uvicorn 0.51.0 after `Config(...)` has run its
`configure_logging()`:

    root.handlers: []          root.level: 30 (WARNING)
    server.app effective level: WARNING

1. uvicorn's `LOGGING_CONFIG` has no `root` key, so `dictConfig` never touches
   the root logger and it stays at Python's default WARNING. `server.app` sets
   no level of its own, so `getEffectiveLevel()` walks up to WARNING and
   `logger.info(...)` dies inside `isEnabledFor()` — the record is never even
   created.
2. Root has no handler, so a record that DID survive the level check would
   fall through to `logging.lastResort`, itself a WARNING-level handler.

Adding a handler alone therefore fixes nothing; the level has to come down too.
`logger.warning` was visible only because it cleared both WARNING bars by
accident, via `lastResort` rather than via anything we configured.
"""
from __future__ import annotations

import logging
import sys

#: Daemon logs are read hours later, so they need a timestamp — uvicorn's own
#: `%(levelprefix)s %(message)s` has none. Being visually distinct from
#: uvicorn's lines is a feature: you can tell at a glance who wrote which.
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: `server` only, and the omission of `sodamem` is deliberate. Root stays at
#: WARNING so third parties (chromadb, httpx, openai, urllib3) do not flood the
#: log, and the library inherits that same WARNING: issue #19 is about the
#: service layer's records — the per-request line, `console_mount` — and every
#: piece of evidence in it is a `server.*` logger. Waking the ~13 INFO sites in
#: `sodamem/` would additionally start writing user-derived content to
#: `~/.sodamem/daemon.log` in the clear (e.g. `ingest/extractor.py` renders
#: `raw_value` and `predicate_raw`). No credentials are involved, but a log is
#: the thing people paste into bug reports, and changing what a memory product
#: puts on disk is not a decision a logging fix gets to make as a side effect.
#: If library INFO is wanted later it is its own change, with its own argument.
#: Selection is done entirely by logger level — no Filter, no handler level.
_OUR_LOGGERS = ("server",)


def configure_logging_if_unconfigured() -> bool:
    """Install one stderr handler and lower the `server` logger to INFO, once.

    The guard is "the root logger has no handlers", which is exactly "nobody
    in this process has configured logging" — the only case where it is our
    business to. Measured: under `daemon ensure` and under Docker root has
    none at `create_app()` time, and under pytest root always has four
    (`_LiveLoggingNullHandler`, a `_FileHandler` on /dev/null, and two
    `LogCaptureHandler`s), so this is a provable no-op in the test suite.

    Returns True if it installed anything, False if it stood down.

    `StreamHandler(sys.stderr)`, never `FileHandler`: `dictConfig()` calls
    `_clearExistingHandlers()`, which `logging.shutdown()`s every registered
    handler, and `tests/_service.py` builds the app BEFORE `uvicorn.Config`,
    so our handler does get closed. `StreamHandler.close()` leaves the stream
    alone; `FileHandler.close()` would make every later emit raise on a closed
    file. The daemon already redirects the child's stdout/stderr into
    `daemon.log`, so opening a file here would be wrong anyway.
    """
    root = logging.getLogger()
    if root.handlers:
        return False
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    for name in _OUR_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
    return True
