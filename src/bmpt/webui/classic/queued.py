"""The game-folder writes the user chose to apply once the classic window closes."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Protocol

from ...core.errors import ServiceError


QUEUE_METHODS = ("actions.apply", "actions.restore", "mods.toggle", "mods.restoreDrift")
EntryKey = tuple[str, str]  # (install key, core)
FileStat = tuple[int, int] | None  # (size, mtime_ns), None for a missing file


def stat_files(paths: Iterable[str]) -> dict[str, FileStat]:
    """Read the sizes and modification times of files; no contents are read.

    The classic queue compares them to see what the classic window changed.

    Args:
        paths: The file paths.

    Returns:
        Path mapped to ``(size, mtime_ns)``, or None for a file that cannot be read.
    """
    out: dict[str, FileStat] = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(p)] = None
    return out


@dataclass(frozen=True)
class QueuedCall:
    """One write the classic modal queued: its RPC call and what it was judged against.

    - ``entries``: the entries it switches, ``(install key, core)``; none for a toggle. For ``mods.restoreDrift``,
      ``params["entries"][i]`` is the entry ``entries[i]`` names, so a later action on some of them narrows it.
    - ``pins``: install key to the pin it was queued with: every known install for a toggle, its entries' installs for
      an entry action. Compared with ``==``.
    - ``files``: per entry, ``stat_files`` of its game files when it was queued.
    - ``current``: it works on the current install (``actions.apply``, ``actions.restore``).
    """

    method: str
    params: Mapping[str, Any]
    entries: tuple[EntryKey, ...] = ()
    pins: Mapping[str, Any] = field(default_factory=dict)
    files: Mapping[EntryKey, Mapping[str, FileStat]] = field(default_factory=dict)
    current: bool = False

    @property
    def toggle(self) -> bool:
        """Whether the call is the mod switch's toggle."""
        return self.method == "mods.toggle"

    def narrowed(self, keep: Iterable[EntryKey]) -> "QueuedCall | None":
        """Narrow this call to some of its entries.

        Args:
            keep: The entries to keep.

        Returns:
            The call for those entries only, with its params, pins and files narrowed too; this call itself when
            all are kept; None when none is left.
        """
        wanted = set(keep)
        keep = [e for e in self.entries if e in wanted]
        if not keep:
            return None
        if len(keep) == len(self.entries):
            return self
        params = dict(self.params)
        listed = self.params.get("entries")
        if isinstance(listed, list) and len(listed) == len(self.entries):
            params["entries"] = [p for e, p in zip(self.entries, listed) if e in keep]
        installs = {e[0] for e in keep}
        return replace(
            self,
            params=params,
            entries=tuple(keep),
            pins={k: v for k, v in self.pins.items() if k in installs},
            files={e: f for e, f in self.files.items() if e in keep},
        )

    def to_json(self) -> dict[str, Any]:
        """Return the call's method, params and entries for the page."""
        return {"method": self.method, "params": dict(self.params), "entries": [list(e) for e in self.entries]}


@dataclass(frozen=True)
class Dropped:
    """A queued call that was not submitted, with the error that says why."""

    call: QueuedCall
    code: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Return the call and its error for the page."""
        return {**self.call.to_json(), "error": ServiceError(self.code, **self.params).to_json()}


@dataclass
class QueueRun:
    """The outcome of ``ClassicQueue.run``: what was submitted and what was dropped."""

    submitted: list[tuple[QueuedCall, Any]] = field(default_factory=list)  # (call, job id)
    dropped: list[Dropped] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Return the submitted calls with their job ids, and the dropped ones, for the page."""
        return {
            "submitted": [{**c.to_json(), "job": j} for c, j in self.submitted],
            "dropped": [d.to_json() for d in self.dropped],
        }


class QueueHost(Protocol):
    """What ``ClassicQueue.run`` asks of the web UI."""

    def intent(self) -> bool | None:
        """The stored intent: the library's ``enabled``."""

    def pins(self) -> Mapping[str, Any]:
        """The known installs now: install key to pin."""

    def current(self) -> str | None:
        """The current install's key now."""

    def submit(self, method: str, params: dict[str, Any]) -> Any:
        """Submit the call as its normal job; returns the job id, or raises ``ServiceError``."""


class ClassicQueue:
    """The writes the user asked to apply once the classic window closes.

    It lives in memory only: quitting drops it. At most one toggle, and one action per entry: a later toggle
    replaces the queued one, a toggle back to the stored intent removes it, and for an entry the latest action
    wins. Its lock is a leaf lock: ``run`` calls the host outside it.
    """

    def __init__(self, stat: Callable[[Iterable[str]], Mapping[str, FileStat]] = stat_files):
        """Create an empty queue.

        Args:
            stat: Reads the sizes and times of files, as ``stat_files`` does; tests replace it.
        """
        self._lock = threading.Lock()
        self._items: list[QueuedCall] = []
        self._stat = stat

    def __len__(self) -> int:
        """Return how many calls are queued."""
        with self._lock:
            return len(self._items)

    def items(self) -> list[QueuedCall]:
        """Return a copy of the queued calls, in order."""
        with self._lock:
            return list(self._items)

    def add(self, call: QueuedCall, *, intent: bool | None = None) -> int:
        """Queue a call, replacing what it supersedes.

        Args:
            call: The call; its method must be one of ``QUEUE_METHODS``.
            intent: The stored intent, for a toggle: a toggle to the stored intent just removes the queued one.

        Returns:
            How many calls are queued.

        Raises:
            ServiceError: ``bad_params``, the method is not queueable.
        """
        if call.method not in QUEUE_METHODS:
            raise ServiceError("bad_params", method=call.method)
        with self._lock:
            if call.toggle:
                self._items = [i for i in self._items if not i.toggle]
                if call.params.get("enabled") != intent:
                    self._items.append(call)
            else:
                keys = set(call.entries)
                kept: list[QueuedCall] = []
                for item in self._items:
                    if item.toggle or not keys & set(item.entries):
                        kept.append(item)
                        continue
                    rest = item.narrowed(e for e in item.entries if e not in keys)
                    if rest is not None:
                        kept.append(rest)
                kept.append(call)
                self._items = kept
            return len(self._items)

    def clear(self) -> int:
        """Empty the queue, as when the classic modal closed with its box unticked.

        Returns:
            How many calls were dropped.
        """
        with self._lock:
            n, self._items = len(self._items), []
            return n

    def take(self) -> list[QueuedCall]:
        """Empty the queue and return what it held, in order."""
        with self._lock:
            items, self._items = self._items, []
            return items

    def run(self, host: QueueHost) -> QueueRun:
        """Submit the queued calls that still hold, in order; run after ``classic.closed`` and the scan after it.

        Dropped: a toggle whose ``expect`` is not the stored intent (``state_changed``) or whose pins are not the
        known installs (``folder_changed``); an entry whose install changed (``folder_changed``: only its own
        install counts, and the current one for an action on the current install) or whose files changed
        (``entry_changed``). An item the host cannot check (``mod_configuration_unreadable`` from the stored intent) is
        dropped with that error, like a refused submit; the others still run.

        Args:
            host: The web UI's intent, installs and job submission.

        Returns:
            The submitted calls with their job ids, and the dropped ones with their errors.
        """
        out = QueueRun()
        for call in self.take():
            try:
                self._run_one(host, call, out)
            except ServiceError as e:  # raised by a check before anything of this call was listed
                out.dropped.append(Dropped(call, e.code, e.params))
        return out

    def _run_one(self, host: QueueHost, call: QueuedCall, out: QueueRun) -> None:
        if call.toggle:
            if call.params.get("expect") != host.intent():
                out.dropped.append(Dropped(call, "state_changed"))
            elif dict(call.pins) != dict(host.pins()):
                out.dropped.append(Dropped(call, "folder_changed"))
            else:
                self._submit(host, call, out)
            return
        pins, current = host.pins(), host.current()
        keep: list[EntryKey] = []
        for entry in call.entries:
            install = entry[0]
            one = call.narrowed([entry])
            if (
                install not in pins
                or pins.get(install) != call.pins.get(install)
                or (call.current and current != install)
            ):
                out.dropped.append(Dropped(one, "folder_changed"))
                continue
            files = call.files.get(entry, {})
            if dict(self._stat(list(files))) != dict(files):
                out.dropped.append(Dropped(one, "entry_changed"))
                continue
            keep.append(entry)
        rest = call.narrowed(keep)
        if rest is not None:
            self._submit(host, rest, out)

    @staticmethod
    def _submit(host: QueueHost, call: QueuedCall, out: QueueRun) -> None:
        try:
            out.submitted.append((call, host.submit(call.method, dict(call.params))))
        except ServiceError as e:
            out.dropped.append(Dropped(call, e.code, e.params))
