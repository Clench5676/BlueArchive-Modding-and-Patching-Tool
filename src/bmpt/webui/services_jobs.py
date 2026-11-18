"""The scan consent, the scan and the jobs of the web UI's services, a mixin of ``Services``."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.errors import ServiceError
from ..library import toggle_timing
from bmpt.engine import parse_filename
from .jobs import MAINTENANCE, USER


logger = logging.getLogger("bmpt.webui.services")  # the services log under one name


PRUNE_WAIT = 120.0  # seconds the start scan waits for the library.prune job submitted with the library
PROGRESS_PUBLISH = 0.25  # seconds between two mods.state events while a toggle reports its progress
SCAN_WRITER_POLL = 0.2  # seconds between two looks of a waiting scan at the writer jobs (_scan)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _changed_of(result: Any) -> dict[str, list[str]]:
    """``library.changed``'s data for a job's result: the cores and kinds it switched or added."""
    if hasattr(result, "duplicates") or (
        isinstance(result, list) and any(isinstance(m, dict) and "target" in m for m in result)
    ):
        switched = [
            *getattr(result, "applied", []),
            *getattr(result, "configured", []),
            *getattr(result, "installed", []),
        ]
        return {"cores": sorted({m.core for m in switched}), "kinds": sorted({m.kind for m in switched}), "mods": True}
    cores, kinds = getattr(result, "cores", None), getattr(result, "kinds", None)
    if cores is not None and kinds is not None:
        return {
            "cores": sorted(cores),
            "kinds": sorted(kinds),
            **(
                {"mods": True}
                if getattr(result, "mods_changed", False)
                or any(item.get("kept_current") for item in getattr(result, "done", []))
                else {}
            ),
        }
    files = getattr(result, "files", None)
    if isinstance(files, list):
        from ..library.judge import core_of

        names = [core_of(Path(f).name) for f in files if isinstance(f, (str, Path))]
        cores = sorted({c for c in names if c})
        from ..library.judge import kind_of_file

        kinds = sorted(
            {
                kind_of_file(Path(f).name) or ("spr" if parse_filename(Path(f).name).core.endswith("_spr") else "lobby")
                for f in files
                if isinstance(f, (str, Path))
            }
        )
        return {"cores": cores, "kinds": kinds, **({"mods": True} if getattr(result, "kept_current", False) else {})}
    mod = getattr(result, "mod", None)
    if mod is not None and getattr(mod, "core", None):
        return {"cores": [mod.core], "kinds": [mod.kind], **({"mods": True} if hasattr(result, "report") else {})}
    return {"cores": [], "kinds": []}


def _touched(result: Any) -> tuple[set[tuple[str, str]], set[str]]:
    """Collect the entries a job's result names as switched, adopted or dropped.

    Returns:
        The entries as (install, core), and the cores of the mods an adoption added.
    """
    keys: set[tuple[str, str]] = set()
    for name in ("done", "adopted", "dropped"):
        for item in getattr(result, name, None) or []:
            if isinstance(item, dict) and isinstance(item.get("install"), str) and isinstance(item.get("core"), str):
                keys.add((item["install"], item["core"]))
    cores = {m.core for m in getattr(result, "added", None) or [] if getattr(m, "core", "")}
    return keys, cores


class JobsMixin:
    """The scan consent, the scan and the jobs of ``Services``."""

    # --- the scan consent and the scan ---

    def scan_allowed(self) -> bool:
        """Whether the user gave the scan consent; the library asks it at each use."""
        return self.state.scan_consent is True

    def set_scan_consent(self, granted: bool) -> None:
        """Save the scan consent: granted, request a scan; withdrawn, scans stop and the entries empty.

        Args:
            granted: Whether the game folders may be checked.
        """
        self.state.set_scan_consent(bool(granted))
        if granted:
            self.request_scan()
        else:
            self.scanner.clear()
            self.snapshot.clear_entries()
            # the scan the classic queue waits for is dropped with the other requests: the queue runs now
            self._release_queue()
        self.publish_state()

    def request_scan(self, dismissed: bool = False, *, cores: set[str] | None = None) -> int | None:
        """Ask the scanner for a scan, which needs the scan consent.

        Args:
            dismissed: Settings' Check, which lists dismissed entries again and starts a prune job first when no job
                runs.
            cores: Scan only these bundle cores; None scans everything.

        Returns:
            The request's number; None without consent.
        """
        if not self.scan_allowed():
            return None
        if dismissed and self.scan_allowed():
            with self._lock:
                if not self.jobs.busy():
                    self._start_prune(self.library)
        if dismissed:
            return self.scanner.request(True)
        return self.scanner.request(cores=cores) if cores is not None else self.scanner.request()

    def _scan(self, cancel: Callable[[], bool], dismissed: bool, cores: set[str] | None = None) -> Any:
        """Scan every known install, on the scanner's thread.

        The start scan waits for the prune job, and every scan for the writer jobs queued or running: a scan between
        a job's steps holds the library while the job waits (a 209-entry Enable Mods waited a minute for the scans
        its own progress requested), and reads files the job is replacing; the end of each writer job requests the
        scan that follows it.
        """
        if self._prune_job is not None:
            with self.timings.measure("scan.prune-wait"):
                self.jobs.wait(self._prune_job, PRUNE_WAIT)
        if self.jobs.busy():
            with self.timings.measure("scan.writer-wait"):
                while self.jobs.busy() and not cancel():
                    time.sleep(SCAN_WRITER_POLL)
        library = self.library
        start = time.perf_counter()
        with self.timings.measure("scan.files"):
            result = library.scan(library.installs(), cancel, include_dismissed=dismissed, cores=cores)
        if cores is not None and not result.stale:
            from dataclasses import replace

            entries = [e for e in self.snapshot.entries() if e.core not in cores] + list(result.entries)
            counts = {}
            for entry in entries:
                count = counts.setdefault(entry.install, {})
                name = "stuck" if entry.state == "pending" and entry.overwritten else entry.state
                count[name] = count.get(name, 0) + 1
                if entry.configured:
                    count["configured"] = count.get("configured", 0) + 1
            result = replace(result, entries=entries, counts=counts)
        logger.debug("Scanned %s cores in %.3f s", "all" if cores is None else len(cores), time.perf_counter() - start)
        return result

    def _scanned(self, result: Any, served: int) -> None:
        if result is not None:
            self.snapshot.set_entries(list(result.entries), _now())
            self.events.publish("library.scanned", {"counts": result.counts})
            self.publish_state()
        self._release_queue(served)

    def _release_queue(self, served: int | None = None) -> None:
        """Run the classic queue once the scan it waits for is done, or at once when consent was withdrawn.

        Only one caller takes the ticket.

        Args:
            served: The number of the last request a scan attempt served; None runs the queue at once.
        """
        with self._lock:
            ticket = self._queue_ticket
            if ticket is None or (served is not None and served < ticket):
                return
            self._queue_ticket = None
        self.run_queue()

    # --- jobs ---

    def submit(
        self,
        kind: str,
        fn: Callable[..., Any],
        *,
        writes: str | None = None,
        library: Any = None,
        rank: int = USER,
        **params: Any,
    ) -> str:
        """Submit a job to the runner.

        A writer job ends on the worker with the library's judging of the unfinished list, a new ``mods.state``
        snapshot, ``library.changed {cores, kinds}`` and a scan (with scan consent).

        Args:
            kind: The job's kind, which names it in events.
            fn: The job's work, called as ``fn(log=..., progress=..., **params)``.
            writes: ``"game"`` or ``"library"`` for a writer job (what it writes); None for a read-only job.
            library: For a writer job, the library it works through, captured at submit; None takes the current one.
            rank: The job's class in the queue, ``jobs.USER`` unless it is maintenance.
            **params: The keyword arguments of ``fn``; ``cancellable=True`` makes a read-only job cancellable
                (``JobRunner.submit``).

        Returns:
            The job's id.
        """
        if writes is None:
            return self.jobs.submit(kind, fn, rank=rank, **params)

        def run(log: Any, progress: Any, **p: Any) -> Any:
            result, failed = None, True
            try:
                result = fn(log=log, progress=progress, **p)
                failed = False
                return result
            finally:
                self._after_writer(library, kind, result, failed)

        return self.jobs.submit(kind, run, writes=writes, rank=rank, **params)

    def _after_writer(self, library: Any, kind: str, result: Any, failed: bool) -> None:
        started = time.perf_counter()
        library = library if library is not None else self._library
        if library is not None and kind != "library.judge":
            try:
                library.judge_unfinished()
            except ServiceError as error:
                logger.info("unfinished entries not judged: %s", error.code)
            except Exception:
                logger.exception("unfinished entries not judged")
        if not failed:
            keys, cores = _touched(result)
            self.snapshot.drop_entries(keys, cores)
        self.snapshot.set_progress(None)
        self.refresh_status(library)
        # a failed job may have switched some entries (a toggle stopped midway): everything is read again
        self.events.publish("library.changed", None if failed else _changed_of(result))
        replace_scan = self.scanner.running or self.scanner.invalidated
        pruned = library.take_pruned_cores() if library is not None else set()
        if kind not in ("library.prune", "library.judge") and (
            kind != "mods.add" or result or getattr(result, "installed", None) or replace_scan or pruned
        ):
            changed = _changed_of(result)
            cores = set(changed["cores"]) if not failed and kind in ("apply", "restore") else None
            variant = getattr(result, "variant", None)
            if cores is not None and not cores and variant:
                # nothing written (unchanged or deferred): only the configuration of the variant's cores changed
                try:
                    cores = library.variant_cores(variant)
                except Exception:
                    logger.exception("variant cores not read")
                    cores = None
            if cores is not None:
                cores.update(pruned)
            if replace_scan:
                cores = None  # a preceding scan may become stale while this job writes
            # Without a complete snapshot a full scan still establishes the initial state.
            self.request_scan(cores=cores if cores and self.snapshot.scanned() else None)
        elapsed = time.perf_counter() - started
        if kind == "mods.toggle":
            self.timings.record("mods.toggle.follow_up", elapsed, quiet=True)
        logger.info("Writer %s follow-up and scan request: %.3f s", kind, elapsed)

    def _job_finished(self, job: Any) -> None:
        # the end of a writer job: mods.state again, with the job no longer busy (a toggle's end)
        if job.writes:
            self.publish_state()

    def _progress(self, progress: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a toggle's progress callback so that it also feeds ``mods.state.progress`` ("Working 12/40").

        The job reports its item at hand as a dict (``mod_progress``): the items before it are done, and it is once
        its share is whole.
        """

        def report(current: int | dict[str, Any], total: int | None = None, label: str = "") -> None:
            progress(current, total, label)
            if isinstance(current, dict):
                total = int(current.get("total") or 0)
                ended = float(current.get("fraction") or 0) >= 1
                done = max(0, int(current.get("current") or 0) - (0 if ended else 1))
                if self.snapshot.progress() == {"done": done, "total": total}:
                    return
            else:
                done, total = int(current), int(total or 0)
            self.snapshot.set_progress({"done": done, "total": total})
            now = time.monotonic()
            if done >= total or now - self._progress_at >= PROGRESS_PUBLISH:
                self._progress_at = now
                with toggle_timing.stage("state_publish"):
                    self.publish_state()

        return report

    def _check_pin(self, library: Any, pin: Any) -> None:
        """``folder_changed`` when the current install is not the one a single-entry job was submitted with."""
        now = library.current_pin()
        if now is None or now.key != pin.key or tuple(now.dirs) != tuple(pin.dirs):
            raise ServiceError("folder_changed")

    def _current_pin(self, library: Any) -> Any:
        pin = library.current_pin()
        if pin is None:
            raise ServiceError("game_not_found")
        return pin

    def submit_toggle(self, enabled: bool, expect: bool) -> str:
        """Submit ``mods.toggle``: the Enable Mods switch over every known install, pinned at submit.

        Args:
            enabled: The wanted state of the switch.
            expect: The intent the caller saw; the job refuses when the stored one differs.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``bad_params`` for non-booleans; ``busy`` while another toggle is queued or running;
                ``classic_open`` while a classic window is open (checked again when the job starts, before the
                intent is saved).
        """
        if not isinstance(enabled, bool) or not isinstance(expect, bool):
            raise ServiceError("bad_params", key="enabled")
        library = self.library
        pins = library.installs()

        def run(log: Any, progress: Any) -> Any:
            self.timings.record("mods.toggle.queue_wait", time.perf_counter() - queued_at, quiet=True)
            self.check_classic()
            return library.toggle(enabled, expect, pins, log=log, progress=self._progress(progress))

        with self.classic.starting():
            if self.jobs.active("mods.toggle"):
                raise ServiceError("busy")
            self.check_classic()
            queued_at = time.perf_counter()
            job = self.submit("mods.toggle", run, writes="game", library=library)
        self.publish_state()
        return job

    def submit_troubleshoot(self, action: str) -> str:
        """Submit ``mods.applyOfficial`` or ``mods.restoreConfig`` (Troubleshoot Mods) over every known install.

        The installs are pinned at submit.

        Args:
            action: ``apply`` to apply the official files to all; anything else restores the saved configuration.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``busy`` while a toggle or another troubleshoot job is queued or running; ``classic_open``
                while a classic window is open.
        """
        from ..library import troubleshoot

        library = self.library
        pins = library.installs()
        work = troubleshoot.apply_official if action == "apply" else troubleshoot.restore

        def run(log: Any, progress: Any) -> Any:
            self.check_classic()
            return work(library, pins, log=log, progress=self._progress(progress))

        with self.classic.starting():
            if self.jobs.active("mods.toggle") or self.jobs.active("mods.troubleshoot"):
                raise ServiceError("busy")
            self.check_classic()
            job = self.submit("mods.troubleshoot", run, writes="game", library=library)
        self.publish_state()
        return job

    def submit_restore_drift(self, entries: list[dict]) -> str:
        """Submit ``mods.restoreDrift``: Apply Mod Configuration of the given entries, pinned at submit.

        Args:
            entries: The entries to apply, as the scan lists them.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``bad_params`` when ``entries`` is not a list of dicts; ``classic_open`` while a classic
                window is open.
        """
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise ServiceError("bad_params", key="entries")
        library = self.library
        pins = library.installs()

        def run(log: Any, progress: Any) -> Any:
            self.check_classic()
            return library.restore_drift(entries, pins, log=log, progress=self._progress(progress))

        with self.classic.starting():
            self.check_classic()
            return self.submit("mods.restore", run, writes="game", library=library)

    def submit_refresh_costumes(self) -> str:
        """Submit ``mods.refreshCostumes``: build again, at the start, the costume builds of an older costume rule.

        See ``refresh_costumes``; it works on the current install. With a classic window open the game files are
        left for the next start.

        Returns:
            The job's id.
        """
        library = self.library
        pin = self._current_pin(library)

        def run(log: Any, progress: Any) -> Any:
            self._check_pin(library, pin)
            return library.refresh_costumes(pin, log=log, progress=progress, write_game=not self.classic_open())

        with self.classic.starting():
            return self.submit("mods.refreshCostumes", run, writes="game", library=library)

    def submit_retry_failed(self) -> str:
        """Submit ``mods.retryFailed``: at the start, write again the entries whose last write failed.

        The entries are those of the current install (``retry_failed``); each is tried once under this build.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``classic_open`` while a classic window is open.
        """
        library = self.library
        pin = self._current_pin(library)

        def run(log: Any, progress: Any) -> Any:
            self._check_pin(library, pin)
            self.check_classic()
            return library.retry_failed(pin, log=log, progress=self._progress(progress))

        with self.classic.starting():
            self.check_classic()
            return self.submit("mods.retryFailed", run, writes="game", library=library)

    def submit_apply(self, variant: str, source: str, kind: str = "spr") -> str:
        """Submit ``actions.apply``: one entry of the current install, pinned at submit.

        Args:
            variant: The variant key.
            source: The source to switch to: ``official``, ``mod:<id>`` or ``variant:<key>``.
            kind: The asset kind, ``spr``, ``lobby``, ``model`` or ``other``.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``bad_params`` for an unknown kind; ``classic_open`` only when it would write a game file
                (an apply while mods are off only changes the configuration).
        """
        if kind not in ("spr", "lobby", "model", "other"):
            raise ServiceError("bad_params", kind=kind)
        library = self.library
        pin = self._current_pin(library)

        def run(log: Any, progress: Any) -> Any:
            self._check_pin(library, pin)
            return library.apply(variant, source, log=log, progress=progress, kind=kind, pin=pin)

        with self.classic.starting():
            if self.classic_open() and library.would_write(variant, source, kind, pin=pin, setting=True):
                raise ServiceError("classic_open")
            return self.submit("apply", run, writes="game", library=library)

    def submit_restore(self, variant: str, kind: str = "spr") -> str:
        """Submit ``actions.restore``: one entry of the current install back to official, pinned at submit.

        A restore that writes nothing (the files are official already) is allowed while a classic window is open;
        if it turns out to write after all (the full judgement finds a file changed), the write itself is refused
        with ``classic_open``.

        Args:
            variant: The variant key.
            kind: The asset kind, ``spr``, ``lobby``, ``model`` or ``other``.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``bad_params`` for an unknown kind; ``classic_open`` when the restore would write.
        """
        if kind not in ("spr", "lobby", "model", "other"):
            raise ServiceError("bad_params", kind=kind)
        library = self.library
        pin = self._current_pin(library)

        def run(log: Any, progress: Any) -> Any:
            self._check_pin(library, pin)
            return library.restore(variant, log=log, kind=kind, pin=pin, progress=progress)

        with self.classic.starting():
            if self.classic_open() and library.would_write(variant, "official", kind, pin=pin):
                raise ServiceError("classic_open")
            return self.submit("restore", run, writes="game", library=library)

    def submit_call(self, method: str, params: dict[str, Any]) -> str:
        """Submit one of the calls the classic queue holds, as its normal job.

        Args:
            method: The RPC method, one of ``classic.QUEUE_METHODS``.
            params: The call's parameters as the queue kept them.

        Returns:
            The job's id.

        Raises:
            ServiceError: ``bad_params`` for another method, or the submit's own refusal.
        """
        params = dict(params or {})
        if method == "mods.toggle":
            return self.submit_toggle(params.get("enabled"), params.get("expect"))
        if method == "mods.restoreDrift":
            return self.submit_restore_drift(params.get("entries"))
        if method == "actions.apply":
            return self.submit_apply(params.get("variant"), params.get("source"), params.get("kind", "spr"))
        if method == "actions.restore":
            return self.submit_restore(params.get("variant"), params.get("kind", "spr"))
        raise ServiceError("bad_params", method=method)

    def submit_judge(self) -> str | None:
        """Judge the unfinished list again as a library writer job, after another data directory changed the intent.

        Returns:
            The job's id; None while no library is built or such a job is queued already.
        """
        library = self._library
        if library is None or self.jobs.active("library.judge"):
            return None
        return self.submit(
            "library.judge",
            lambda log, progress: {"dropped": library.judge_unfinished()},
            writes="library",
            library=library,
            rank=MAINTENANCE,
        )
