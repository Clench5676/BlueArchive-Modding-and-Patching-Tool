"""How far one item of a job has got (0..1), for the progress line's percentage.

A long build marks named phases as it passes them (``Meter.phase``), optionally how far it is inside one (``done`` of
``count``). Each phase weighs its measured share of the build's time (a table per build, never per item), so the
fraction grows about evenly with time. The fraction only grows, stops below 1 (``CAP``) until the item ends, and is
reported at most every ``EVERY`` seconds and only when its whole percent changed, so marking costs a clock read. The
meter takes no lock and never waits: the report is the job's progress callback.
"""

from __future__ import annotations

import time
from typing import Callable, Mapping, Sequence

EVERY = 0.2   # seconds between two reports at most
CAP = 0.99    # the highest fraction before the item ends: never 100 % while it runs


class Meter:
    """The fraction done of one item, from ``weights``: the phases in order, each with its share (any scale; a phase
    left out of ``weights`` counts as nothing). ``report(fraction)`` is called when the shown percent grows, at most
    every ``every`` seconds."""

    def __init__(self, report: Callable[[float], None] | None, weights: Sequence[tuple[str, float]] | Mapping[str, float],
                 *, every: float | None = None, clock: Callable[[], float] = time.monotonic):
        pairs = list(weights.items()) if isinstance(weights, Mapping) else list(weights)
        total = sum(max(0.0, float(w)) for _, w in pairs) or 1.0
        self._start: dict[str, tuple[float, float]] = {}
        at = 0.0
        for name, w in pairs:
            share = max(0.0, float(w)) / total
            self._start[name] = (at, share)
            at += share
        self._report = report
        self._every = EVERY if every is None else every
        self._clock = clock
        self._last = float("-inf")  # when it last reported
        self._shown = 0              # the whole percent last reported: the first report is 1 % or more
        self.fraction = 0.0

    def phase(self, name: str, done: float = 0, count: float = 1) -> None:
        """The build is in ``name``, ``done`` of ``count`` through it."""
        start = self._start.get(name)
        if start is None:
            return
        at, share = start
        part = min(1.0, max(0.0, done / count)) if count > 0 else 0.0
        self._move(at + share * part)

    def _move(self, fraction: float) -> None:
        fraction = min(CAP, fraction)
        if fraction <= self.fraction:
            return
        self.fraction = fraction
        if self._report is None:
            return
        percent = int(fraction * 100)
        if percent <= self._shown:
            return
        now = self._clock()
        if now - self._last < self._every:
            return
        self._last, self._shown = now, percent
        self._report(fraction)


class _Quiet:
    """A meter that marks nothing: the build's default."""

    fraction = 0.0

    def phase(self, name: str, done: float = 0, count: float = 1) -> None:
        pass


QUIET = _Quiet()
