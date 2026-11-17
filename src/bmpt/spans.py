"""Timing spans for code below the web UI (game assets, the library), recorded into the session's timings.

The web UI installs its Timings at startup; without one, spans cost a clock read. Lock waits are recorded only from
WAIT_FLOOR on, so the report shows contention rather than every uncontended acquisition.
"""

from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

WAIT_FLOOR = 0.01
# A report lists at most this many unfinished spans, the oldest first.
ACTIVE_LIMIT = 32


class Sink(Protocol):
    """Receives finished spans: the web UI's session timings."""

    def record(self, phase: str, seconds: float) -> None:
        """Record that ``phase`` took ``seconds``."""
        ...


_sink: Sink | None = None
# Spans still running (id -> name, start). A record is written only when a span ends, so a request the page gave up
# on would leave no trace in a report taken at that moment; these are listed instead.
_active: dict[int, tuple[str, float]] = {}
_active_lock = threading.Lock()
_ids = itertools.count()


def install(sink: Sink | None) -> None:
    """Send the spans to ``sink`` from now on; None drops them."""
    global _sink
    _sink = sink


def record(name: str, seconds: float) -> None:
    """Hand a finished span of ``seconds`` to the installed sink, if there is one."""
    sink = _sink
    if sink is not None:
        sink.record(name, seconds)


@contextmanager
def running(name: str) -> Iterator[float]:
    """List ``name`` among the unfinished spans while the block runs; yields its start (``perf_counter``)."""
    started = time.perf_counter()
    key = next(_ids)
    with _active_lock:
        _active[key] = (name, started)
    try:
        yield started
    finally:
        with _active_lock:
            _active.pop(key, None)


def active(now: float | None = None) -> list[dict[str, Any]]:
    """Return the unfinished spans, the oldest first, at most ``ACTIVE_LIMIT``.

    Args:
        now: The ``perf_counter`` time to measure to; None reads the clock.

    Returns:
        ``{"name", "seconds"}`` for each span, seconds so far rounded to milliseconds.
    """
    now = time.perf_counter() if now is None else now
    with _active_lock:
        items = sorted(_active.values(), key=lambda item: item[1])
    return [{"name": name, "seconds": round(max(0.0, now - started), 3)} for name, started in items[:ACTIVE_LIMIT]]


@contextmanager
def span(name: str) -> Iterator[None]:
    """Time the block as ``name``, listed among the unfinished spans while it runs."""
    with running(name) as started:
        try:
            yield
        finally:
            record(name, time.perf_counter() - started)


@contextmanager
def held(name: str, lock: Any, *, hold: bool = False, floor: float = WAIT_FLOOR) -> Iterator[None]:
    """Hold ``lock`` for the block, recording how long it took to get.

    Args:
        name: Span prefix: ``<name>.wait`` and ``<name>.held``.
        lock: An object with ``acquire`` and ``release``.
        hold: Also record ``<name>.held``, how long this holder kept everyone else waiting. For rare, long holders
            only: every record is also a log line.
        floor: Seconds of waiting from which ``<name>.wait`` is recorded.
    """
    with running(name + ".wait") as started:
        lock.acquire()
    try:
        acquired = time.perf_counter()
        if acquired - started >= floor:
            record(name + ".wait", acquired - started)
        with running(name + ".held"):
            try:
                yield
            finally:
                if hold:
                    record(name + ".held", time.perf_counter() - acquired)
    finally:
        lock.release()
