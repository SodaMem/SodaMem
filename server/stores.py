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

from server.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Deliberately narrow: alphanumeric start, then alphanumerics / dot / dash /
# underscore. Dots are allowed (real ids contain them) but a segment that is
# ALL dots is rejected below — that is the traversal vector, not the dot itself.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidScopeError(ValueError):
    """Raised for a user_id that is unsafe or malformed."""


def validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
        raise InvalidScopeError(
            "user_id must be 1-128 chars, start alphanumeric, and contain only "
            "letters, digits, '.', '-', '_'"
        )
    if set(user_id) == {"."}:  # ".", "..", "..." — the traversal family
        raise InvalidScopeError("user_id must not be all dots")
    return user_id


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
                mem = SodaMem.open(path, extractor=self._build_extractor())
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
