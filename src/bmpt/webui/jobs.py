"""Long operations run one at a time on a worker thread and report through events."""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.errors import ServiceError, log_service_error
from bmpt.engine import t
from .events import EventBus
from .rpc import to_json

logger = logging.getLogger(__name__)

LOG_FLUSH_SECONDS = 0.1
PROGRESS_SECONDS = 0.1
DONE_ITEMS = 3  # how many of a job's finished items it keeps for the Tasks panel (``Job.summary``)
_ITEM_FIELDS = ("action", "alt", "core", "resourceType", "kind", "name", "source", "label", "current", "total")
WRITES = ("game", "library")  # a writer job writes the game folder (and the library), or the library only
# The queue serves jobs by class, then in submission order; a running job is never interrupted. Jobs the user started
# and watches go first, then start-up work behind the start cover, then background maintenance (the prune, the CRC
# repair at start): a user's switch never waits for maintenance queued before it.
USER, START, MAINTENANCE = 2, 3, 4
_STOP = 99  # the shutdown marker, after every queued job: queued writers still run


class Cancelled(Exception):
    """Raised by a cancellable job at a step boundary once ``JobRunner.cancel`` stopped it.

    Only jobs submitted with ``cancellable=True`` raise it. The job ends in the "cancelled" state, with no result.
    """


@dataclass
class Job:
    """One operation of the runner: what was asked, how far it is and what it ended with."""

    id: str
    kind: str
    params: dict[str, Any]
    fn: Callable[..., Any]
    writes: str | None = None  # "game", "library" or None (read-only)
    rank: int = USER  # its class in the queue: USER, START or MAINTENANCE
    state: str = "queued"  # queued, running, done, failed, dropped, cancelled
    result: Any = None
    error: dict[str, Any] | None = None
    log: list[str] = field(default_factory=list)
    finished: bool = False  # it ran and its end's listeners ran too
    progress: dict[str, Any] | None = None
    cancellable: bool = False  # JobRunner.cancel may stop it: never run when queued, stopped between steps when running
    stop: threading.Event = field(default_factory=threading.Event)
    done_items: list[dict[str, Any]] = field(default_factory=list)  # the last finished items, oldest first

    def note_progress(self, progress: dict[str, Any]) -> None:
        """Keep the item at hand as finished when the job's progress moves on to another item.

        Args:
            progress: The job's new progress snapshot, with its ``current`` item.
        """
        previous = self.progress
        if not previous or previous.get("current") == progress.get("current"):
            return
        if not any(previous.get(key) for key in ("alt", "core", "label", "name")):
            return
        self.done_items = [
            *self.done_items[-(DONE_ITEMS - 1) :],
            {key: previous[key] for key in _ITEM_FIELDS if key in previous},
        ]

    def summary(self) -> dict[str, Any]:
        """Return what the Tasks panel shows of a queued or running job.

        That is its id, kind and state, whether it writes, its last progress and its last finished items.
        """
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "writes": self.writes,
            "progress": self.progress,
            "done": list(self.done_items),
        }

    def to_json(self) -> dict[str, Any]:
        """Return the job's id, kind, state, result, error and last progress, as the page sees it."""
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
        }

    @property
    def pending(self) -> bool:
        """Whether the job is queued or running."""
        return self.state in ("queued", "running")


class JobRunner:
    """A single worker thread: UnityPy and the game folder are never touched by two jobs at once.

    Writer jobs (``writes``: the game folder or the library) hold back the window's close button, ``app.quit``, a
    library move and the services rebuild after a game folder change; read-only jobs (an extract, a download) do not.
    """

    def __init__(self, events: EventBus, keep: int = 50):
        """Start the worker thread.

        Args:
            events: The bus that receives the ``job.*`` events.
            keep: How many jobs the runner remembers for ``get``; the oldest finished ones go first, and a job
                still to run is never forgotten.
        """
        self.on_exception = None
        self.translate: Callable[[BaseException], ServiceError | None] | None = None  # see RpcRegistry.translate
        self._events = events
        self._queue: queue.PriorityQueue[tuple[int, int, Job | None]] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._keep = keep
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._closing = False
        self._quit_reserved = False
        self._idle = threading.Condition(self._lock)
        self._listeners: list[Callable[[Job], Any]] = []
        self._thread = threading.Thread(target=self._run, name="bamt-jobs", daemon=True)
        self._thread.start()

    def submit(
        self,
        kind: str,
        fn: Callable[..., Any],
        *,
        writes: str | None = None,
        rank: int = USER,
        cancellable: bool = False,
        **params: Any,
    ) -> str:
        """Queue a job for the worker.

        Args:
            kind: The job's kind, such as ``mods.apply``; it names the job in events and in ``cancel``'s whitelist.
            fn: The job's work, called on the worker as ``fn(log=..., progress=..., **params)``. ``log(line)`` adds
                a line to the job's log. ``progress`` takes a report dict, or ``(current, total, label)``. Its
                return value becomes the job's result.
            writes: ``"game"`` or ``"library"`` for a writer job (what it writes), None for a read-only job.
            rank: The job's class in the queue: ``USER``, ``START`` or ``MAINTENANCE``.
            cancellable: ``cancel`` may stop the job; read-only jobs only. ``fn`` then also gets ``cancelled``, a
                callable that says whether it was; the job checks it between its steps and raises ``Cancelled``.
            **params: The keyword arguments of ``fn``.

        Returns:
            The job's id.

        Raises:
            ValueError: ``writes`` or ``rank`` is not a known value, or a writer job is cancellable.
            ServiceError: ``busy``, a writer job was submitted while a quit is reserved.
        """
        if writes is not None and writes not in WRITES:
            raise ValueError(f"unknown writes {writes!r}")
        if rank not in (USER, START, MAINTENANCE):
            raise ValueError(f"unknown rank {rank!r}")
        if cancellable and writes is not None:
            raise ValueError("a writer job cannot be cancelled")
        job = Job(
            id=f"j{next(self._ids)}", kind=kind, params=params, fn=fn, writes=writes, rank=rank, cancellable=cancellable
        )
        if cancellable:
            job.params = {**params, "cancelled": job.stop.is_set}
        with self._lock:
            if writes and self._quit_reserved:
                raise ServiceError("busy")
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._keep:
                # never forget a job that is still to run: busy() and wait() count it
                drop = next((i for i, jid in enumerate(self._order) if not self._jobs[jid].pending), None)
                if drop is None:
                    break
                self._jobs.pop(self._order.pop(drop), None)
            self._queue.put((rank, next(self._seq), job))
        self._publish("job.queued", {"id": job.id, "kind": job.kind})
        return job.id

    def listen(self, fn: Callable[[Job], Any]) -> None:
        """Call a function on the worker after each job that ran, once its state is final.

        Args:
            fn: ``fn(job)``; an exception it raises is logged.
        """
        self._listeners.append(fn)

    def get(self, job_id: str) -> Job:
        """Return a job.

        Args:
            job_id: The id ``submit`` returned.

        Raises:
            ServiceError: ``job_not_found``, the job never existed or is forgotten.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ServiceError("job_not_found", id=job_id)
        return job

    def cancel(self, job_id: str, kinds: tuple[str, ...]) -> str:
        """Stop a cancellable job the caller has given up on.

        A queued job never runs and ends "cancelled" at once. A running one is told to stop at its next step: it
        ends "cancelled", or as it would have when it was past its last check. A job that ended is left as it is.

        Args:
            job_id: The id ``submit`` returned.
            kinds: The job kinds the caller may stop.

        Returns:
            The job's state afterwards.

        Raises:
            ServiceError: ``job_not_found`` for an unknown job; ``bad_params`` for a job that is not cancellable
                or not of one of ``kinds``.
        """
        with self._idle:
            job = self._jobs.get(job_id)
            if job is None:
                raise ServiceError("job_not_found", id=job_id)
            if not job.cancellable or job.kind not in kinds:
                raise ServiceError("bad_params", key="id")  # not a job the page may stop
            job.stop.set()
            if job.state != "queued":
                return job.state
            # still in the queue: the worker skips it when it comes up (_run)
            job.state, job.finished = "cancelled", True
            self._idle.notify_all()
        self._publish("job.cancelled", {"id": job.id, "kind": job.kind})
        return "cancelled"

    def _publish(self, event: str, data: Any) -> None:
        try:
            self._events.publish(event, data)
        except Exception:
            logger.exception("job event %s failed", event)

    def busy(self, writes: str | None = None) -> bool:
        """Return whether a writer job is queued or running.

        Args:
            writes: ``"game"`` or ``"library"`` to look only at jobs writing that; None for any writer job.
        """
        with self._lock:
            return any(j.writes and (writes is None or j.writes == writes) and j.pending for j in self._jobs.values())

    def reserve_quit(self) -> None:
        """Refuse new writer jobs, in one step with checking that none is pending.

        Raises:
            ServiceError: ``busy``, a quit is reserved already or a writer job is queued or running.
        """
        with self._lock:
            if self._quit_reserved or any(j.writes and j.pending for j in self._jobs.values()):
                raise ServiceError("busy")
            self._quit_reserved = True

    def cancel_quit(self) -> None:
        """Allow writer jobs again after a quit that did not happen."""
        with self._lock:
            self._quit_reserved = False

    def pending(self) -> list[dict[str, Any]]:
        """List the queued and running jobs as the Tasks panel shows them (``Job.summary``).

        The running job comes first, then the queued ones in the order the worker takes them: by class, then in
        submission order.
        """
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.pending]
            order = {jid: i for i, jid in enumerate(self._order)}
            jobs.sort(key=lambda j: (j.state != "running", j.rank, order.get(j.id, 0)))
            return [j.summary() for j in jobs]

    def active(self, *kinds: str) -> bool:
        """Return whether a job of one of these kinds is queued or running.

        Args:
            *kinds: The job kinds to look for.
        """
        with self._lock:
            return any(j.kind in kinds and j.pending for j in self._jobs.values())

    def outranked(self, rank: int) -> bool:
        """Return whether a job of a higher class than ``rank`` waits in the queue.

        A job of a lower class checks it between its steps: it starts no new step while one waits.

        Args:
            rank: The asking job's class: ``USER``, ``START`` or ``MAINTENANCE``.
        """
        with self._lock:
            return any(j.state == "queued" and j.rank < rank for j in self._jobs.values())

    def running(self) -> Job | None:
        """Return the job that runs now, or None."""
        with self._lock:
            return next((j for j in self._jobs.values() if j.state == "running"), None)

    @property
    def closing(self) -> bool:
        """Whether the runner is shutting down (``shutdown``): long background work stops at its next step."""
        with self._lock:
            return self._closing

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """Wait until a job ran (done, failed, cancelled or dropped) and the listeners heard its end.

        Args:
            job_id: The id ``submit`` returned; an unknown job counts as done.
            timeout: Seconds to wait at most; None waits without limit.

        Returns:
            Whether the job ended in time.
        """
        with self._idle:
            return self._idle.wait_for(lambda: job_id not in self._jobs or self._jobs[job_id].finished, timeout)

    def shutdown(self, wait: float | None = None) -> None:
        """Stop the worker after the jobs queued so far.

        Queued read-only jobs are dropped; queued writer jobs still run (quitting is refused while one is queued,
        so a normal quit drops none).

        Args:
            wait: Seconds to block while a writer job is queued or running, since a job writing into the game
                folder must not be cut off by the App exiting; None does not block. A running read-only job is
                never waited for: the worker is a daemon thread.
        """
        with self._lock:
            self._closing = True
            self._queue.put((_STOP, next(self._seq), None))
        if wait is not None and self._thread.is_alive() and self.busy():
            deadline = time.monotonic() + wait
            with self._idle:
                self._idle.wait_for(
                    lambda: not any(j.writes and j.pending for j in self._jobs.values()),
                    max(0.0, deadline - time.monotonic()),
                )

    def _run(self) -> None:
        while True:
            _rank, _seq, job = self._queue.get()
            if job is None:
                return
            with self._lock:
                if job.state == "cancelled":
                    continue  # cancelled while queued: it ended then (cancel)
                drop = self._closing and not job.writes
                if drop:
                    job.state, job.finished = "dropped", True
                    self._idle.notify_all()
                else:
                    job.state = "running"  # under the lock: a cancel from now on reaches the job itself
            if drop:
                logger.info("job %s (%s) dropped at shutdown", job.id, job.kind)
                continue
            try:
                self._execute(job)
                for fn in list(self._listeners):
                    try:
                        fn(job)
                    except BaseException:
                        logger.exception("job listener failed")
            finally:
                with self._idle:
                    job.finished = True
                    self._idle.notify_all()

    def _execute(self, job: Job) -> None:
        publish = self._publish
        pending: list[str] = []
        last_flush = [time.monotonic()]
        last_progress = [0.0]
        progress_sequence = itertools.count(1)
        progress_lock = threading.Lock()

        def flush() -> None:
            if pending:
                publish("job.log", {"id": job.id, "lines": list(pending)})
                pending.clear()
            last_flush[0] = time.monotonic()

        def log(line: str) -> None:
            text = str(line)
            job.log.append(text)
            pending.append(text)
            if time.monotonic() - last_flush[0] >= LOG_FLUSH_SECONDS:
                flush()

        def progress(current: int | dict[str, Any], total: int | None = None, label: str = "") -> None:
            # Worker callbacks, polling and SSE share immutable, ordered snapshots.
            # A delayed HTTP response must never replace a newer event in the UI.
            with progress_lock:
                if isinstance(current, dict):
                    job.note_progress(current)
                    job.progress = {**current, "id": job.id, "sequence": next(progress_sequence)}
                else:
                    label = label or t("webui.progress.process", name=t("webui.progress.mods"))
                    job.note_progress({"current": current})
                    job.progress = {
                        "id": job.id,
                        "current": current,
                        "total": total,
                        "label": label,
                        "sequence": next(progress_sequence),
                    }
                    now = time.monotonic()
                    if current < total and now - last_progress[0] < PROGRESS_SECONDS:
                        return
                    last_progress[0] = now
                publish("job.progress", job.progress)

        job.state = "running"
        publish("job.started", {"id": job.id, "kind": job.kind})
        try:
            result = job.fn(log=log, progress=progress, **job.params)
            job.result = to_json(result)
            job.state = "done"
            flush()
            publish("job.done", {"id": job.id, "kind": job.kind, "result": job.result})
        except Cancelled:
            logger.info("job %s (%s) cancelled", job.id, job.kind)
            job.state = "cancelled"
            flush()
            publish("job.cancelled", {"id": job.id, "kind": job.kind})
        except ServiceError as e:
            log_service_error(logger, e)
            job.error = e.to_json()
            job.state = "failed"
            flush()
            publish("job.failed", {"id": job.id, "kind": job.kind, **job.error})
        except BaseException as e:  # report and keep the worker alive
            known = self.translate(e) if self.translate and isinstance(e, Exception) else None
            if known is not None:
                log_service_error(logger, known)
                job.error = known.to_json()
            else:
                logger.exception("job %s (%s) failed", job.id, job.kind)
                error = ServiceError("internal", error=f"{type(e).__name__}: {e}")
                log_service_error(logger, error)
                job.error = error.to_json()
                job.log.append(traceback.format_exc())
                if self.on_exception:
                    self.on_exception(type(e), e, e.__traceback__)
            job.state = "failed"
            flush()
            publish("job.failed", {"id": job.id, "kind": job.kind, **job.error})
