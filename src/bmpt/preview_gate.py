"""Let library scans and background work yield briefly while the shown page's previews are answered."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from . import spans

logger = logging.getLogger(__name__)
_condition = threading.Condition()
_count = 0
_local = threading.local()  # depth: this thread's foreground() nesting
# told True when a foreground preview starts waiting for a job (``waiting``) with no other one waiting, False when the
# last stops: the page's preview deadlines leave that time out (``preview.waiting``)
on_wait: Callable[[bool], None] | None = None
_waiters = 0
_telling = threading.Lock()


@contextmanager
def foreground() -> Iterator[None]:
    """Count a foreground preview until its export finishes, even when it fails."""
    global _count
    with _condition:
        _count += 1
    _local.depth = getattr(_local, "depth", 0) + 1
    try:
        yield
    finally:
        _local.depth -= 1
        with _condition:
            _count -= 1
            _condition.notify_all()


def in_foreground() -> bool:
    """Whether this thread answers a foreground preview (inside ``foreground``)."""
    return getattr(_local, "depth", 0) > 0


def yield_to_previews(cancel: Callable[[], bool] | None, limit: float = 20.0) -> None:
    """Wait for previews, checking cancellation every 0.1 s and bounding the pause."""
    started = None
    try:
        with _condition:
            if not _count:
                return
            deadline = time.monotonic() + limit
            while _count:
                if cancel is not None and cancel():
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                if started is None:
                    started = time.monotonic()
                _condition.wait(min(0.1, remaining))
    finally:
        if started is not None:
            spans.record("scan.yield", time.monotonic() - started)


def step(cancel: Callable[[], bool] | None = None) -> None:
    """Between two steps of a job that holds no lock a preview needs (a costume build): yield to the foreground
    previews (at most 20 s), unless this thread answers one itself."""
    if not in_foreground():
        yield_to_previews(cancel)


def _tell(busy: bool) -> None:
    hook = on_wait
    if hook is None:
        return
    try:
        hook(busy)
    except Exception:  # a listener never fails a preview
        logger.debug("preview wait listener failed", exc_info=True)


@contextmanager
def waiting() -> Iterator[None]:
    """A foreground preview waits for a job (the model lock a job holds, a costume build of the same pair): it does
    not count as foreground meanwhile, so the job it waits for does not yield to it, and the wait is told
    (``on_wait``) so the page's deadline leaves it out. Nothing for a thread that answers no preview."""
    global _count, _waiters
    depth = getattr(_local, "depth", 0)
    if not depth:
        yield
        return
    with _condition:
        _count -= depth
        _condition.notify_all()
    with _telling:
        _waiters += 1
        if _waiters == 1:
            _tell(True)
    try:
        yield
    finally:
        with _telling:
            _waiters -= 1
            if _waiters == 0:
                _tell(False)
        with _condition:
            _count += depth
