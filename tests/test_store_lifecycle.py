"""Store resource lifecycle: SodaMem.close(), and StoreManager's borrow /
release accounting.

Background — why this file exists. `SodaMem.open()` had no counterpart, so a
caller that opens one store per item in a loop accumulated every one of them
for the process's lifetime. Measured cost of one never-closed store: ~12 file
descriptors and ~11 threads, against a hard 4096-threads-per-process ceiling
on macOS. That is the shape of the repo's own 500-question benchmark runs
(`SodaMem.open()` per question, no close anywhere).

The server had the opposite bug: `StoreManager` DID close, but closed stores
that requests were still using — LRU eviction called `close()` on whatever
fell off the end, and the borrower's next call died with
`sqlite3.ProgrammingError: Cannot operate on a closed database`. The cap was
guarded only by a comment saying it should "comfortably exceed request
concurrency".
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from sodamem import SodaMem
from server.settings import Settings
from server.stores import StoreManager


def _open(data_dir):
    """SodaMem.open() does not create its data_dir — StoreManager mkdirs
    before calling it — so tests have to do the same."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return SodaMem.open(data_dir)


def _settings(tmp_path, **over) -> Settings:
    return Settings(data_root=tmp_path, api_key="k", auth_disabled=True, **over)


# ---------------------------------------------------------------------------
# SodaMem.close() — the missing half of open()
# ---------------------------------------------------------------------------

def test_sodamem_close_releases_the_store(tmp_path):
    mem = _open(tmp_path / "u1")
    mem.store.get_all_fact_events("u1")  # works while open

    mem.close()

    with pytest.raises(sqlite3.ProgrammingError):
        mem.store.get_all_fact_events("u1")


def test_sodamem_close_is_idempotent(tmp_path):
    mem = _open(tmp_path / "u1")
    mem.close()
    mem.close()  # must not raise — `with` + explicit close() have to compose


def test_sodamem_is_a_context_manager(tmp_path):
    with _open(tmp_path / "u1") as mem:
        assert mem.store.get_all_fact_events("u1") == []
    with pytest.raises(sqlite3.ProgrammingError):
        mem.store.get_all_fact_events("u1")


def test_closing_in_a_loop_does_not_accumulate_resources(tmp_path):
    """The benchmark pattern, done right. Without close() this leaks ~12 fds
    and ~11 threads per iteration; with it, both stay flat."""
    psutil = pytest.importorskip("psutil")
    proc = psutil.Process()

    # Warm up once so one-off imports/model loads aren't counted as growth.
    with _open(tmp_path / "warm") as mem:
        mem.store.get_all_fact_events("warm")

    before_fds, before_threads = proc.num_fds(), proc.num_threads()
    for i in range(12):
        with _open(tmp_path / f"u{i}") as mem:
            mem.store.get_all_fact_events(f"u{i}")
    after_fds, after_threads = proc.num_fds(), proc.num_threads()

    # Generous slack for allocator/pool jitter; the leak this guards against
    # is ~144 fds and ~135 threads over these 12 iterations.
    assert after_fds - before_fds < 24, (
        f"file descriptors grew by {after_fds - before_fds} over 12 "
        "open/close cycles — close() is not releasing them"
    )
    assert after_threads - before_threads < 24, (
        f"threads grew by {after_threads - before_threads} over 12 "
        "open/close cycles — close() is not releasing them"
    )


# ---------------------------------------------------------------------------
# StoreManager: eviction must not close a store someone is still using
# ---------------------------------------------------------------------------

def test_eviction_does_not_close_an_in_flight_store(tmp_path):
    """The regression this whole mechanism exists for. cache_max=1, so asking
    for a second user evicts the first — while a request still holds it."""
    mgr = StoreManager(_settings(tmp_path, store_cache_max=1))
    alice = mgr.get("alice")           # borrowed, not yet released
    mgr.get("bob")                     # evicts alice from the cache

    # Previously: ProgrammingError, Cannot operate on a closed database.
    assert alice.store.get_all_fact_events("alice") == []

    mgr.release("bob")
    mgr.release("alice")
    mgr.close_all()


def test_evicted_store_is_closed_once_its_last_borrower_releases(tmp_path):
    """Deferred, not skipped — the close still has to happen, or the fix
    would just trade a use-after-close for a leak."""
    mgr = StoreManager(_settings(tmp_path, store_cache_max=1))
    alice = mgr.get("alice")
    mgr.get("bob")                     # alice evicted, close deferred

    mgr.release("alice")               # last borrower -> now it closes
    with pytest.raises(sqlite3.ProgrammingError):
        alice.store.get_all_fact_events("alice")

    mgr.release("bob")
    mgr.close_all()


def test_two_borrowers_of_one_store_close_only_after_both_release(tmp_path):
    mgr = StoreManager(_settings(tmp_path, store_cache_max=1))
    first = mgr.get("alice")
    second = mgr.get("alice")
    assert first is second
    mgr.get("bob")                     # evict alice, two borrowers outstanding

    mgr.release("alice")
    assert first.store.get_all_fact_events("alice") == []  # one still holds it

    mgr.release("alice")
    with pytest.raises(sqlite3.ProgrammingError):
        first.store.get_all_fact_events("alice")

    mgr.release("bob")
    mgr.close_all()


def test_released_stores_are_still_reclaimed_so_the_cache_stays_bounded(tmp_path):
    """Refcounting must not turn the LRU into an unbounded cache."""
    mgr = StoreManager(_settings(tmp_path, store_cache_max=2))
    for i in range(10):
        with mgr.lease(f"u{i}"):
            pass
    assert len(mgr._cache) <= 2
    assert mgr._inflight == {}
    assert mgr._pending_close == {}
    mgr.close_all()


def test_a_pinned_store_does_not_block_reclamation_of_others(tmp_path):
    """A single long-lived borrow at the LRU end must not stop everything
    behind it from being reclaimed."""
    mgr = StoreManager(_settings(tmp_path, store_cache_max=2))
    mgr.get("pinned")                  # borrowed and never released
    for i in range(8):
        with mgr.lease(f"u{i}"):
            pass

    assert "pinned" in mgr._cache or "pinned" in mgr._pending_close
    assert len(mgr._cache) <= 2
    mgr.release("pinned")
    mgr.close_all()


def test_lease_releases_even_when_the_body_raises(tmp_path):
    mgr = StoreManager(_settings(tmp_path, store_cache_max=2))
    with pytest.raises(RuntimeError):
        with mgr.lease("alice"):
            raise RuntimeError("boom")
    assert mgr._inflight == {}
    mgr.close_all()


def test_concurrent_borrows_never_hand_out_a_closed_store(tmp_path):
    """cache_max=1 with several threads churning distinct users: every
    iteration evicts somebody. No borrower may ever see a closed store."""
    mgr = StoreManager(_settings(tmp_path, store_cache_max=1))
    errors: list[BaseException] = []

    def worker(worker_id: int) -> None:
        try:
            for i in range(15):
                uid = f"w{worker_id}u{i}"
                with mgr.lease(uid) as mem:
                    assert mem.store.get_all_fact_events(uid) == []
        except BaseException as exc:  # noqa: BLE001 - surfaced on main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"borrowers hit closed stores: {errors[:3]}"
    assert mgr._inflight == {}
    mgr.close_all()


# ---------------------------------------------------------------------------
# close_all() vs. a live borrow — the same invariant, on the teardown path.
#
# Eviction has respected borrows since this file was written. `close_all` did
# not: it closed everything unconditionally, reasoning that a still-borrowed
# store at teardown is a caller bug worth surfacing "loudly, on its next
# use".
#
# That reasoning does not survive contact with sqlite3. Freeing a connection
# while another thread is inside a statement on it is undefined behaviour at
# the C level, not a Python exception — the process does not report a caller
# bug, it dies. That is the suite's intermittent segfault: a captured stack
# showed a job-runner thread in `append_extraction_trace` while the main
# thread was in `Store.close()` on the same store.
# ---------------------------------------------------------------------------

def test_close_all_defers_a_borrowed_store(tmp_path):
    """Whoever holds this store may be mid-write on another thread."""
    mgr = StoreManager(_settings(tmp_path))
    alice = mgr.get("alice")            # borrowed, not released
    try:
        mgr.close_all()
        assert alice.store.get_all_fact_events("alice") == []
    finally:
        mgr.release("alice")


def test_release_after_close_all_closes_it(tmp_path):
    """Deferring must not leak. The last release completes the teardown —
    the same handshake eviction already uses."""
    mgr = StoreManager(_settings(tmp_path))
    alice = mgr.get("alice")
    mgr.close_all()
    mgr.release("alice")

    with pytest.raises(sqlite3.ProgrammingError):
        alice.store.get_all_fact_events("alice")


def test_close_all_still_closes_idle_stores(tmp_path):
    """The deferral applies only to live borrows. An idle cached store must
    still go, or close_all stops meaning anything."""
    mgr = StoreManager(_settings(tmp_path))
    idle = mgr.get("idle")
    mgr.release("idle")
    mgr.close_all()

    with pytest.raises(sqlite3.ProgrammingError):
        idle.store.get_all_fact_events("idle")


def test_close_all_survives_a_concurrent_writer(tmp_path):
    """The crash, reproduced in miniature: one thread writing while the main
    thread tears the manager down. Without the deferral this is a segfault,
    so a plain assertion cannot catch it — the test passing at all IS the
    assertion."""
    mgr = StoreManager(_settings(tmp_path))
    mem = mgr.get("writer")
    stop = threading.Event()
    errors: list[Exception] = []

    def churn():
        while not stop.is_set():
            try:
                mem.store.get_all_fact_events("writer")
            except Exception as exc:  # noqa: BLE001 - recorded, asserted below
                errors.append(exc)
                return

    t = threading.Thread(target=churn, daemon=True)
    t.start()
    try:
        for _ in range(20):
            mgr.close_all()
    finally:
        stop.set()
        t.join(timeout=5)
        mgr.release("writer")

    assert not errors, f"borrowed store was closed under a live reader: {errors[0]}"
