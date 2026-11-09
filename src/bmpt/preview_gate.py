"""Let library scans yield briefly while foreground model previews are exported."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from . import spans

_condition = threading.Condition()
_count = 0


@contextmanager
def foreground() -> Iterator[None]:
    """Count a foreground preview until its export finishes, even when it fails."""
    global _count
    with _condition:
        _count += 1
    try:
        yield
    finally:
        with _condition:
            _count -= 1
            _condition.notify_all()


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
