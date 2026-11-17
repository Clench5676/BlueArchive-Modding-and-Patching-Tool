"""The startup rig archive steps aside for everything else that uses the library."""

from __future__ import annotations

import threading
import time

from bmpt.library import service


def _library() -> service.ModLibrary:
    library = object.__new__(service.ModLibrary)
    library._lock = service._LibraryLock()
    return library


def test_the_library_lock_notes_foreground_use_only():
    lock = service._LibraryLock()
    with lock:
        with lock:  # re-entrant, as the RLock it wraps
            pass
    used = lock.used
    assert used > 0
    service._background.on = True
    try:
        time.sleep(0.01)
        with lock:
            pass
        assert lock.used == used
    finally:
        service._background.on = False


def test_the_archive_waits_until_nothing_else_used_the_library_for_a_while(monkeypatch):
    monkeypatch.setattr(service, "RIG_ARCHIVE_QUIET", 0.3)
    library = _library()
    busy = 0.6

    def foreground():
        end = time.monotonic() + busy
        while time.monotonic() < end:
            with library._lock:
                time.sleep(0.01)
            time.sleep(0.05)

    worker = threading.Thread(target=foreground)
    worker.start()
    time.sleep(0.05)
    waited = library._yield_to_foreground(None)
    worker.join()
    assert waited is not None and waited >= 0.6  # the rest of the busy spell, then the quiet time
    assert time.monotonic() - library._lock.used >= 0.3


def test_the_archive_goes_on_at_once_on_a_quiet_library_and_stops_when_cancelled(monkeypatch):
    monkeypatch.setattr(service, "RIG_ARCHIVE_QUIET", 5.0)
    library = _library()
    assert library._yield_to_foreground(None) < 0.05  # never used
    with library._lock:
        pass
    started = time.monotonic()
    assert library._yield_to_foreground(lambda: time.monotonic() - started > 0.2) is None
    assert time.monotonic() - started < 1.0


def test_background_work_does_not_hold_back_its_own_next_step(monkeypatch):
    monkeypatch.setattr(service, "RIG_ARCHIVE_QUIET", 5.0)
    library = _library()
    service._background.on = True
    try:
        with library._lock:  # the archive's own core under the lock
            pass
        assert library._yield_to_foreground(None) < 0.05
    finally:
        service._background.on = False


def test_a_paced_job_does_not_count_as_use_it_steps_aside_for():
    lock = service._LibraryLock()
    with lock:
        pass
    asked = lock.asked
    assert asked > 0 and lock.used == asked
    service._background.paced = True
    try:
        time.sleep(0.01)
        with lock:
            pass
        assert lock.asked == asked  # the toggle's own steps
        assert lock.used > asked  # background work still steps aside for a toggle
    finally:
        service._background.paced = False


def test_a_paced_job_goes_on_at_once_when_nobody_else_uses_the_library(monkeypatch):
    monkeypatch.setattr(service, "TOGGLE_YIELD", (0.3, 2.0))
    library = _library()
    assert library._let_others_in() < 0.05  # never used
    service._background.paced = True
    try:
        with library._lock:
            pass
        assert library._let_others_in() < 0.05
    finally:
        service._background.paced = False


def test_a_paced_job_waits_for_the_others_but_no_longer_than_its_bound(monkeypatch):
    monkeypatch.setattr(service, "TOGGLE_YIELD", (0.2, 0.5))
    library = _library()
    with library._lock:
        pass
    waited = library._let_others_in()
    assert 0.15 <= waited < 0.4  # the quiet time after the last use
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            with library._lock:
                pass
            time.sleep(0.02)

    worker = threading.Thread(target=reader)
    worker.start()
    try:
        time.sleep(0.05)
        waited = library._let_others_in()
    finally:
        stop.set()
        worker.join()
    assert 0.45 <= waited < 0.8  # busy all along: the bound


def test_a_reader_is_not_held_to_one_library_call_per_toggle_step(monkeypatch):
    """A preview file request makes several library calls in a row; while a toggle reaches its entries (each under
    the lock for a while), all of them get in between two entries."""
    monkeypatch.setattr(service, "TOGGLE_YIELD", (0.05, 0.5))
    library = _library()
    step, calls, gap = 0.1, 20, 0.01
    done = threading.Event()

    def toggle():
        service._background.paced = True
        try:
            while not done.is_set():
                library._let_others_in()
                with library._lock:
                    time.sleep(step)
        finally:
            service._background.paced = False

    worker = threading.Thread(target=toggle)
    worker.start()
    try:
        time.sleep(0.15)
        started = time.monotonic()
        for _ in range(calls):
            with library._lock:
                pass
            time.sleep(gap)  # the request's own work between its calls
        took = time.monotonic() - started
    finally:
        done.set()
        worker.join()
    assert took < calls * gap + 3 * step  # one call per step would take calls * step


def test_a_paced_job_steps_aside_for_a_caller_blocked_on_the_lock(monkeypatch):
    monkeypatch.setattr(service, "TOGGLE_YIELD", (0.01, 0.3))
    library = _library()
    held, got = threading.Event(), threading.Event()

    def reader():
        held.wait()
        with library._lock:
            got.set()

    worker = threading.Thread(target=reader)
    worker.start()
    with library._lock:
        held.set()
        while not library._lock.waiting:
            time.sleep(0.005)
        time.sleep(0.05)
        # it noted its wait long before the quiet time, but it is still blocked: the job waits (here up to its bound,
        # as this thread holds the lock)
        assert library._let_others_in() >= 0.29
    worker.join()
    assert got.is_set() and library._lock.waiting == 0


def test_a_job_holding_the_library_lets_a_waiting_reader_in_between_two_steps():
    """A job that holds the lock for its whole run (prune, CRC repair) gives it up whole, nested holds included, to a
    caller blocked on it, and takes it back once that caller is done."""
    lock = service._LibraryLock()
    order = []
    with lock, lock:  # the job's own call and one of its steps
        reader = threading.Thread(
            target=lambda: (
                lock.acquire(),
                order.append("reader"),
                time.sleep(0.05),
                order.append("reader done"),
                lock.release(),
            )
        )
        reader.start()
        deadline = time.monotonic() + 2
        while not lock.waiting and time.monotonic() < deadline:
            time.sleep(0.005)
        assert lock.waiting == 1
        waited = lock.step_aside()
        order.append("job")
        assert waited >= 0.04  # it waited for the reader's work
        assert order == ["reader", "reader done", "job"]
    reader.join()
    with lock:  # the lock is whole again: released twice above, nothing left over
        pass
    assert lock._lock.acquire(False)
    lock._lock.release()


def test_a_job_does_not_step_aside_for_nobody_or_without_the_lock():
    lock = service._LibraryLock()
    with lock:
        assert lock.step_aside() == 0.0  # nobody waits
    lock.waiting = 1  # another thread's count, but this thread holds nothing
    assert lock.step_aside() == 0.0
    lock.waiting = 0


def test_the_lock_tells_when_a_reader_starts_and_stops_waiting_for_it():
    """The page pauses its preview deadlines while a read waits for the library: told once per spell, in order,
    never for background work or a paced job."""
    lock = service._LibraryLock()
    told = []
    lock.on_wait = told.append
    with lock:
        pass
    assert told == []  # a free lock: nobody waited
    with lock:
        readers = [threading.Thread(target=lambda: (lock.acquire(), lock.release())) for _ in range(2)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + 2
        while lock.waiting < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert told == [True]
    for reader in readers:
        reader.join()
    assert told == [True, False]

    def background():
        service._background.on = True
        try:
            with lock:
                pass
        finally:
            service._background.on = False

    with lock:
        waiter = threading.Thread(target=background)
        waiter.start()
        time.sleep(0.05)
    waiter.join()
    assert told == [True, False]  # background work waiting for the lock is not told


def test_a_failing_wait_listener_never_fails_the_library():
    lock = service._LibraryLock()
    lock.on_wait = lambda busy: 1 / 0
    with lock:
        reader = threading.Thread(target=lambda: (lock.acquire(), lock.release()))
        reader.start()
        time.sleep(0.05)
    reader.join()
    assert lock.waiting == 0
