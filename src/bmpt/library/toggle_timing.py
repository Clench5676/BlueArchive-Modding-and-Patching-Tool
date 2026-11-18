"""Per-toggle timings and built-copy outcomes, scoped to the calling context."""

from __future__ import annotations

import logging
import time
from collections import Counter
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from typing import Iterator, Literal, TypeVar

from ..core import spans

logger = logging.getLogger(__package__)
_PREFIX = "mods.toggle."
_Copy = Literal["rebuilt", "reused", "direct"]
_T = TypeVar("_T")


class _Toggle:
    def __init__(self, enabled: bool) -> None:
        self.direction = "on" if enabled else "off"
        self.prefix = f"{_PREFIX}{self.direction}."
        self.seconds: Counter[str] = Counter()
        self.copies: Counter[_Copy] = Counter()
        self.planned = self.attempted = 0
        self.copy: _Copy | None = None


_current: ContextVar[_Toggle | None] = ContextVar("toggle_timing", default=None)


@contextmanager
def toggle(enabled: bool) -> Iterator[None]:
    """Collect timings for a toggle and log one summary, including on partial exits."""
    run = _Toggle(enabled)
    token = _current.set(run)
    started = time.perf_counter()
    try:
        yield
    finally:
        _current.reset(token)
        elapsed = time.perf_counter() - started
        spans.record(run.prefix + "total", elapsed, quiet=True)
        logger.info(
            "Mods %s timing: %d/%d entries attempted in %.3f s; rebuilt=%d reused=%d direct=%d; %s",
            run.direction,
            run.attempted,
            run.planned,
            elapsed,
            run.copies["rebuilt"],
            run.copies["reused"],
            run.copies["direct"],
            ", ".join(f"{name}={seconds:.3f} s" for name, seconds in sorted(run.seconds.items())),
        )


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Record one stage sample during a toggle; nested stages have inclusive times."""
    run = _current.get()
    if run is None:
        yield
        return
    phase = run.prefix + name
    with spans.running(phase) as started:
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            run.seconds[name] += elapsed
            spans.record(phase, elapsed, quiet=True)


@contextmanager
def enter(name: str, manager: AbstractContextManager[_T]) -> Iterator[_T]:
    """Time entry to a context, leaving its release at the end of the caller's block."""
    if _current.get() is None:
        with manager as value:
            yield value
        return
    with ExitStack() as stack:
        with stage(name):
            value = stack.enter_context(manager)
        yield value


@contextmanager
def entry() -> Iterator[None]:
    """Count one attempted entry and its preparation path, even if the write fails."""
    run = _current.get()
    if run is None:
        yield
        return
    run.attempted += 1
    run.copy = None
    try:
        with stage("entry"):
            yield
    finally:
        if run.copy is not None:
            run.copies[run.copy] += 1


def planned(count: int) -> None:
    """Record how many entries the toggle plans to reach."""
    run = _current.get()
    if run is not None:
        run.planned = count


def prepared(copy: _Copy) -> None:
    """Mark how the current entry obtained the bytes for its write."""
    run = _current.get()
    if run is not None:
        run.copy = copy
