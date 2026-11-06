"""Timing spans for code below the web UI (game assets, the library), recorded into the session's timings.

The web UI installs its Timings at startup; without one, spans cost a clock read. Lock waits are recorded only from
WAIT_FLOOR on, so the report shows contention rather than every uncontended acquisition.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

WAIT_FLOOR = 0.01


class Sink(Protocol):
    def record(self, phase: str, seconds: float) -> None: ...


_sink: Sink | None = None


def install(sink: Sink | None) -> None:
    global _sink
    _sink = sink


def record(name: str, seconds: float) -> None:
    sink = _sink
    if sink is not None:
        sink.record(name, seconds)


@contextmanager
def span(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - started)


@contextmanager
def held(name: str, lock: Any, *, hold: bool = False, floor: float = WAIT_FLOOR) -> Iterator[None]:
    """Hold ``lock``, recording ``<name>.wait`` when acquiring it took ``floor`` or more, and with ``hold`` also
    ``<name>.held``: how long this holder kept everyone else waiting (for rare, long holders only; every record is
    also a log line)."""
    started = time.perf_counter()
    with lock:
        acquired = time.perf_counter()
        if acquired - started >= floor:
            record(name + ".wait", acquired - started)
        try:
            yield
        finally:
            if hold:
                record(name + ".held", time.perf_counter() - acquired)
