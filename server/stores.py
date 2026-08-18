"""Per-user store manager: user_id -> SodaMem, LRU-bounded.

Two jobs, both of which the predecessor implementation got wrong:

1. **Bounded resources.** The rig cached one client per user forever; a
   500-distinct-user run monotonically exhausted file handles / chroma
   sessions until new stores silently returned empty results. One never-closed
   store costs roughly 12 file descriptors and 11 threads, against a hard
   4096-threads-per-process ceiling on macOS — so a few hundred of them is
   already the whole budget. Capped here with an LRU.

   The ceiling alone is not enough, though: evicting a store that a request is
   still using and closing it on the spot leaves that request holding a dead
   SQLite connection. So borrows are counted (`get`/`release`, or the `lease`
   context manager) and an evicted-but-borrowed store's close is deferred to
   its last release. The earlier version relied on the cap "comfortably
   exceeding request concurrency", which is a comment, not a guard.

2. **No path traversal.** The rig sanitized `user_id` by replacing
   non-alphanumerics with `_` — but it ALLOWED `.`, so `user_id=".."` survived
   sanitization and `os.path.join(DATA_ROOT, "..")` escaped the data root
   (the PRD's flagged security item). Here: strict allowlist, explicit dot-run
   rejection, plus a resolved-path containment check as defense in depth. A
   rejected id raises, never gets silently rewritten into a different user's
   store.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sodamem import SodaMem
from sodamem.errors import ErrorCode, SodaMemError

from server.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Deliberately narrow: alphanumeric start, then alphanumerics / dot / dash /
# underscore. Dots are allowed (real ids contain them) but a segment that is
# ALL dots is rejected below — that is the traversal vector, not the dot itself.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidScopeError(ValueError):
    """Raised for a user_id that is unsafe or malformed."""


class StoreOpenError(SodaMemError):
    """Opening a user's store failed.

    Exists so the store-open path has a type at all. chromadb raises
    `pyo3_runtime.PanicException` out of its rust bindings, and that class
    inherits `BaseException`, not `Exception` — so it walks straight past
    every `except Exception` in this repo AND past FastAPI's own error
    middleware, and the client gets an unhandled 500 with no code in it. This
    is what it becomes instead: a handled, machine-readable
    `vector_store_unavailable` carrying a message that names the likely cause.
    """

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message, code=ErrorCode.VECTOR_STORE_UNAVAILABLE,
                         details=details)


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
        raise InvalidScopeError(
            "user_id must be 1-128 chars, start alphanumeric, and contain only "
            "letters, digits, '.', '-', '_'"
        )
    if set(user_id) == {"."}:  # ".", "..", "..." — the traversal family
        raise InvalidScopeError("user_id must not be all dots")
    return user_id


# The one failure this diagnostic exists for. chroma persists its own schema
# version in the store; opening a store whose schema is NEWER than the
# installed chromadb makes the rust bindings index past the end of their own
# migration list and PANIC (issue #13/#14: "range start index 10 out of range
# for slice of length 9" — a store migrated to sysdb 10 by chromadb 1.5.8,
# opened by chromadb 1.1.1, which knows 9). The way that happens in practice
# is a second interpreter: `sodamem daemon ensure` resolves `uvicorn` from
# PATH, so a homebrew chromadb can migrate a store the venv then cannot open.
# The panic text says none of this, so the error message has to.
_CHROMA_VERSION_HINT = (
    "A store written by a NEWER chromadb than the one now running panics out "
    "of chroma's rust bindings on open. Check which interpreter is serving "
    "(`sodamem daemon ensure` takes `uvicorn` from PATH) and run the service "
    "on the chromadb that wrote this store."
)


def _diagnose_store_open(path: Path) -> str:
    """Turn an opaque open failure into something an operator can act on.

    Best effort by construction: every probe is wrapped, because a diagnostic
    that raises would replace the real failure with its own. A store whose
    chroma db is unreadable simply gets the static hint.
    """
    facts: list[str] = []
    try:
        from importlib.metadata import version
        facts.append(f"chromadb {version('chromadb')} is installed")
    except Exception:  # noqa: BLE001 - a diagnostic must never raise
        pass
    try:
        import sqlite3
        chroma_db = path / "chroma" / "chroma.sqlite3"
        if chroma_db.exists():
            conn = sqlite3.connect(f"file:{chroma_db}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT COUNT(*), MAX(filename) FROM migrations "
                    "WHERE dir = 'sysdb'"
                ).fetchone()
            finally:
                conn.close()
            if row and row[0]:
                facts.append(
                    f"this store's chroma sysdb schema is at migration "
                    f"{row[0]} ({row[1]})"
                )
    except Exception:  # noqa: BLE001 - a diagnostic must never raise
        pass
    if facts:
        return "; ".join(facts) + ". " + _CHROMA_VERSION_HINT
    return _CHROMA_VERSION_HINT


def build_provider(settings: Settings | None = None):
    """Shared LLM provider factory for every server path that needs one
    (ingest extraction, /v1/answer).

    Returns None when no key is configured. Callers that REQUIRE a provider
    must raise loudly on None rather than degrade — an answer endpoint that
    quietly returns "" because nobody set SODAMEM_LLM_API_KEY is exactly the
    silent failure this project refuses to ship.
    """
    s = settings or get_settings()
    if not s.llm_api_key:
        return None
    from sodamem.llm.factory import create_provider
    return create_provider(
        provider=s.llm_provider,
        model=s.llm_model or None,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url or None,
    )


class StoreManager:
    """Opens (and caches) one SodaMem store per user_id."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cache: OrderedDict[str, SodaMem] = OrderedDict()
        # user_id -> number of borrowers currently using this store. A store
        # with a non-zero count is never closed, evicted or not.
        self._inflight: dict[str, int] = {}
        # Evicted while still borrowed: closed by the last release().
        self._pending_close: dict[str, SodaMem] = {}
        self._lock = threading.Lock()

    # -- path safety --------------------------------------------------------
    def user_dir(self, user_id: str) -> Path:
        validate_user_id(user_id)
        root = self._settings.data_root
        candidate = (root / user_id).resolve()
        # Defense in depth: even if the regex is ever loosened, a store that
        # resolves outside the data root is refused rather than opened.
        if not candidate.is_relative_to(root):
            raise InvalidScopeError("user_id resolves outside the data root")
        return candidate

    # -- store lifecycle ----------------------------------------------------
    def get(self, user_id: str) -> SodaMem:
        """Borrow this user's store. Pair with `release()` — or just use
        `lease()`, which does it for you.

        A caller that never releases keeps the store pinned (it will not be
        evicted) but nothing breaks; the LRU simply cannot reclaim it. That is
        the safe direction to fail.
        """
        with self._lock:
            mem = self._cache.get(user_id)
            if mem is None:
                path = self.user_dir(user_id)
                path.mkdir(parents=True, exist_ok=True)
                mem = self._open_store(user_id, path)
                self._cache[user_id] = mem
            self._cache.move_to_end(user_id)
            self._inflight[user_id] = self._inflight.get(user_id, 0) + 1
            self._evict_locked()
            return mem

    def release(self, user_id: str) -> None:
        """Give back a store borrowed with `get()`. If it was evicted while
        in use, this is where it actually gets closed."""
        with self._lock:
            remaining = self._inflight.get(user_id, 0) - 1
            if remaining > 0:
                self._inflight[user_id] = remaining
                return
            self._inflight.pop(user_id, None)
            pending = self._pending_close.pop(user_id, None)
            if pending is not None:
                self._close(user_id, pending)
            self._evict_locked()

    @contextmanager
    def lease(self, user_id: str) -> Iterator[SodaMem]:
        """`with stores.lease(uid) as mem:` — the borrow/return pair as one
        block, so no code path can forget the release."""
        mem = self.get(user_id)
        try:
            yield mem
        finally:
            self.release(user_id)

    def _evict_locked(self) -> None:
        """Trim the cache to its ceiling. Caller must hold `self._lock`.

        Evicting a store that a request is still using and closing it there
        and then is a use-after-close: the borrower is left holding a
        `SodaMem` whose SQLite connection is gone, and its next call dies with
        `sqlite3.ProgrammingError: Cannot operate on a closed database`. The
        original code relied on `store_cache_max` "comfortably exceeding
        request concurrency" — a comment, not a guard, and one that a burst of
        distinct user_ids or a lowered cap silently invalidates.

        So an in-flight store is dropped from the cache but its close is
        deferred to the last `release()`. Eviction still bounds the cache; it
        just no longer yanks the floor out from under a live request.
        """
        cap = self._settings.store_cache_max
        # Only unused entries are closable, so skip past pinned ones rather
        # than stopping at the first — otherwise a single long-lived borrow at
        # the LRU end would block all reclamation behind it.
        for candidate_id in [k for k in self._cache if k not in self._inflight]:
            if len(self._cache) <= cap:
                return
            self._close(candidate_id, self._cache.pop(candidate_id))
        # Anything still over the cap is entirely in-flight. Drop the oldest
        # from the cache anyway (so it stops being handed to NEW callers) and
        # let its last release() do the close.
        while len(self._cache) > cap:
            evicted_id, evicted = self._cache.popitem(last=False)
            self._pending_close[evicted_id] = evicted

    def _open_store(self, user_id: str, path: Path) -> SodaMem:
        """Open this user's store. One attempt — the value here is the CATCH,
        not a retry.

        `except BaseException` is deliberate and is the entire point.
        chromadb's rust bindings raise `pyo3_runtime.PanicException`, whose
        MRO is `[PanicException, BaseException, object]`. It is not an
        `Exception`, so `except Exception` here, FastAPI's error middleware,
        and every other handler in this repo are structurally blind to it: the
        panic walked all the way into the ASGI stack and the client got an
        unhandled 500 with no code in it. Any pyo3-backed dependency can do
        this; the catch is what makes the failure typed and handled.

        No retry, deliberately. A retry looks attractive because the second
        open "succeeds" — but only because `Store._init_chroma` swallows
        chroma's follow-on `ValueError` and continues WITHOUT vector search.
        That trades a loud, diagnosable 503 for a quiet 200 with retrieval
        silently halved, which is the worst of the available behaviours for an
        environment fault. Fail once, loudly, and say what to fix.

        Re-raised untouched: `KeyboardInterrupt` and `SystemExit`. Those are
        process-control signals, not store faults — turning Ctrl-C or
        `sys.exit()` into a `StoreOpenError` would be a lie about what
        happened. The list is exhaustive; two more were considered and
        rejected:

        - `GeneratorExit`: `get()` is an ordinary function. `lease()`'s
          GeneratorExit can only land at its `yield`, by which point the open
          has long returned. It cannot reach here.
        - `asyncio.CancelledError`: the routes that touch stores are `def`,
          not `async def`, so they run in the threadpool and cancellation is
          never injected into the worker thread; `SodaMem.open` awaits
          nothing. It cannot reach here.
        """
        try:
            return SodaMem.open(path, extractor=self._build_extractor())
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 - see docstring
            diagnosis = _diagnose_store_open(path)
            logger.error(
                "store open for %s failed: %s: %s. %s",
                user_id, type(exc).__name__, exc, diagnosis, exc_info=True,
            )
            raise StoreOpenError(
                f"could not open the store for user_id={user_id!r}: "
                f"{type(exc).__name__}: {exc}. {diagnosis}",
                details={"user_id": user_id, "cause": type(exc).__name__},
            ) from exc

    def _build_extractor(self):
        """Extraction needs an LLM provider; read-only endpoints do not. A
        store opened without a configured provider still serves search/context
        — .ingest() is the only method that raises, and it raises loudly."""
        s = self._settings
        if not s.llm_api_key:
            return None
        from sodamem.memory.ingest.extractor import FactEventExtractorV2
        # NOTE the seam: gate on the CONFIG (is a key set?), not on the
        # provider object. `build_provider` can legitimately hand back None —
        # tests patch `create_provider` to do exactly that — and the extractor
        # handles a None provider itself (degraded, loudly). Gating on the
        # object instead silently turned "no provider" into "no extractor",
        # which surfaces much later as a confusing config_invalid from
        # IngestClient. Caught by the existing suite, 0727.
        return FactEventExtractorV2(build_provider(s))

    @staticmethod
    def _close(user_id: str, mem: SodaMem) -> None:
        try:
            mem.close()
        except Exception:
            logger.warning("store close failed for %s", user_id, exc_info=True)

    def close_all(self) -> None:
        """Shutdown / test-reset hook. Closes every store that nobody is
        holding, and DEFERS the rest to their last `release()` — the same
        handshake eviction uses.

        This used to close borrowed stores too, on the reasoning that a
        still-borrowed store at teardown is a caller bug worth surfacing
        loudly on its next use. That reasoning does not survive contact with
        sqlite3: freeing a connection while another thread is inside a
        statement on it is undefined behaviour at the C level, not a Python
        exception. The process does not report a caller bug — it segfaults.

        Measured: a captured crash stack had a job-runner thread inside
        `append_extraction_trace` while the main thread was inside
        `Store.close()` on that same store, from a teardown that reset the
        store manager before the job runner.

        Deferring is not a leak. `release()` drains `_pending_close`, so the
        last borrower to let go completes the teardown. A borrow that is
        never released was already leaking that store before this method ran.
        """
        with self._lock:
            for user_id in [k for k in self._cache if k not in self._inflight]:
                self._close(user_id, self._cache.pop(user_id))
            # Anything still cached is borrowed: hand it to the deferred-close
            # queue that release() drains.
            while self._cache:
                user_id, mem = self._cache.popitem(last=False)
                self._pending_close.setdefault(user_id, mem)
            for user_id in [k for k in self._pending_close if k not in self._inflight]:
                self._close(user_id, self._pending_close.pop(user_id))


_manager: StoreManager | None = None


def get_store_manager() -> StoreManager:
    global _manager
    if _manager is None:
        _manager = StoreManager()
    return _manager


def reset_store_manager() -> None:
    """Test hook — drops cached stores so a new data_root takes effect."""
    global _manager
    if _manager is not None:
        _manager.close_all()
    _manager = None
