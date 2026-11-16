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

from ..errors import ServiceError, log_service_error
from bmpt.engine import t
from .events import EventBus
from .rpc import to_json

logger = logging.getLogger(__name__)

LOG_FLUSH_SECONDS = 0.1
PROGRESS_SECONDS = 0.1
WRITES = ("game", "library")  # a writer job writes the game folder (and the library), or the library only
# The queue serves jobs by class, then in submission order; a running job is never interrupted. Jobs the user started
# and watches go first, then start-up work behind the start cover, then background maintenance (the prune, the CRC
# repair at start): a user's switch never waits for maintenance queued before it.
USER, START, MAINTENANCE = 2, 3, 4
_STOP = 99  # the shutdown marker, after every queued job: queued writers still run


class Cancelled(Exception):
    """Raised by a cancellable job (``submit(..., cancellable=True)``) at a step boundary once it was cancelled
    (``JobRunner.cancel``): the job ends in the "cancelled" state, with no result."""


@dataclass
class Job:
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

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "state": self.state, "result": self.result, "error": self.error,
                "progress": self.progress}

    @property
    def pending(self) -> bool:
        return self.state in ("queued", "running")


class JobRunner:
    """A single worker thread: UnityPy and the game folder are never touched by two jobs at once.

    Writer jobs (``writes``: the game folder or the library) hold back the window's close button, ``app.quit``, a
    library move and the services rebuild after a game folder change; read-only jobs (an extract, a download) do not.
    """

    def __init__(self, events: EventBus, keep: int = 50):
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

    def submit(self, kind: str, fn: Callable[..., Any], *, writes: str | None = None, rank: int = USER,
               cancellable: bool = False, **params: Any) -> str:
        """Queue ``fn(log=..., progress=..., **params)``; returns the job id. ``writes`` marks a writer job; ``rank``
        is its class (``USER`` unless start-up or maintenance work). ``cancellable`` (read-only jobs only): ``cancel``
        may stop it, and ``fn`` also gets ``cancelled``, a callable that says whether it was; the job checks it between
        its steps and raises ``Cancelled``."""
        if writes is not None and writes not in WRITES:
            raise ValueError(f"unknown writes {writes!r}")
        if rank not in (USER, START, MAINTENANCE):
            raise ValueError(f"unknown rank {rank!r}")
        if cancellable and writes is not None:
            raise ValueError("a writer job cannot be cancelled")
        job = Job(id=f"j{next(self._ids)}", kind=kind, params=params, fn=fn, writes=writes, rank=rank,
                  cancellable=cancellable)
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
        return job.id

    def listen(self, fn: Callable[[Job], Any]) -> None:
        """Call ``fn(job)`` on the worker after each job finished (its state is done or failed by then)."""
        self._listeners.append(fn)

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ServiceError("job_not_found", id=job_id)
        return job

    def cancel(self, job_id: str, kinds: tuple[str, ...]) -> str:
        """Stop a cancellable job of one of ``kinds`` that is no longer wanted; its state afterwards. A queued one
        never runs and ends "cancelled" at once; a running one is told to stop at its next step (it ends "cancelled",
        or as it would have when it was past its last check); one that ended is left as it is.
        ``bad_params`` for any other job."""
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
        """Whether a writer job (one writing ``writes``, when given) is queued or running."""
        with self._lock:
            return any(j.writes and (writes is None or j.writes == writes) and j.pending for j in self._jobs.values())

    def reserve_quit(self) -> None:
        """Refuse new writers atomically with checking that no writer is pending."""
        with self._lock:
            if self._quit_reserved or any(j.writes and j.pending for j in self._jobs.values()):
                raise ServiceError("busy")
            self._quit_reserved = True

    def cancel_quit(self) -> None:
        """Allow writers again after a refused update quit."""
        with self._lock:
            self._quit_reserved = False

    def active(self, *kinds: str) -> bool:
        """Whether a job of one of these kinds is queued or running."""
        with self._lock:
            return any(j.kind in kinds and j.pending for j in self._jobs.values())

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.state == "running"), None)

    @property
    def closing(self) -> bool:
        """Whether the runner is shutting down (``shutdown``): long background work stops at its next step."""
        with self._lock:
            return self._closing

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """Wait until a job ran (done, failed or dropped) and the listeners heard its end; whether it did in time. An
        unknown job counts as done."""
        with self._idle:
            return self._idle.wait_for(lambda: job_id not in self._jobs or self._jobs[job_id].finished, timeout)

    def shutdown(self, wait: float | None = None) -> None:
        """Stop: queued read-only jobs are dropped, queued writer jobs still run (quitting is refused while one is
        queued, so a normal quit drops none). With ``wait``, block up to that many seconds while a writer job is
        queued or running (a job writing into the game folder must not be cut off by the app exiting); a running
        read-only job is not waited for (the worker is a daemon thread)."""
        with self._lock:
            self._closing = True
            self._queue.put((_STOP, next(self._seq), None))
        if wait is not None and self._thread.is_alive() and self.busy():
            deadline = time.monotonic() + wait
            with self._idle:
                self._idle.wait_for(lambda: not any(j.writes and j.pending for j in self._jobs.values()),
                                    max(0.0, deadline - time.monotonic()))

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
                    job.progress = {**current, "id": job.id, "sequence": next(progress_sequence)}
                else:
                    label = label or t("webui.progress.process", name=t("webui.progress.mods"))
                    job.progress = {"id": job.id, "current": current, "total": total, "label": label,
                                    "sequence": next(progress_sequence)}
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
