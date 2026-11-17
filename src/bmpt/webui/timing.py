"""Session timings without paths, also available in the session log."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

from .. import spans

logger = logging.getLogger(__name__)


class Timings:
    """Count, total and maximum seconds per named phase, safe to use from any thread."""

    def __init__(self) -> None:
        """Start with no phases recorded."""
        self._lock = threading.Lock()
        self._values: dict[str, dict[str, float | int]] = {}

    @contextmanager
    def measure(self, phase: str):
        """Time a block or, as a decorator, each call of a function, and record it under ``phase``.

        Args:
            phase: The phase's name, such as ``detail.data``.
        """
        # listed as unfinished while it runs, for a report taken meanwhile
        with spans.running(phase) as started:
            try:
                yield
            finally:
                self.record(phase, time.perf_counter() - started)

    def record(self, phase: str, seconds: float) -> None:
        """Add one run of a phase to its totals and log it.

        Args:
            phase: The phase's name.
            seconds: How long this run took.
        """
        with self._lock:
            value = self._values.setdefault(phase, {"count": 0, "seconds": 0.0, "max": 0.0})
            value["count"] += 1
            value["seconds"] += seconds
            # The longest single value: one long wait reads differently from many short ones.
            value["max"] = max(value["max"], seconds)
        logger.info("Timing %s: %.3f s", phase, seconds)

    def snapshot(self) -> dict:
        """Copy the totals: per phase ``count``, ``seconds`` (sum) and ``max`` (longest single run)."""
        with self._lock:
            return {key: dict(value) for key, value in self._values.items()}
