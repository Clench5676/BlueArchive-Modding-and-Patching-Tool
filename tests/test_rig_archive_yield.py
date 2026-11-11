"""The startup rig archive steps aside for everything else that uses the library (BL-183)."""
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
