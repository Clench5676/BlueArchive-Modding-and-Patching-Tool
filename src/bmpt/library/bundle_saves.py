"""Writing several built bundles at once.

Almost all of a bundle's save is its compression (LZMA, one block per bundle), which releases the GIL; the
serializing before it copies the untouched objects' bytes as read. So the bundles of one build are written in
threads, each into its own file: the bytes are those of a save in turn, only the time differs. A single bundle
cannot be split without changing its bytes, so the largest one bounds the phase; it goes first.

Concurrency: the saves keep their caller's class and locks (a costume build: class 1 for a preview's own build, class
2 for the costume check, both without the library lock; class 2 under it for an apply the user watches) and take no
lock of their own. A save is one step, as before (the largest about 7 s, all of it compression); ``step`` runs
before each one starts, so a waiting foreground preview stops new saves and gets at least half the cores."""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Sequence

from ..errors import ServiceError

MAX_WORKERS = 4


def save_workers(count: int, cpus: int | None = None) -> int:
    """Threads for ``count`` bundles: at most ``MAX_WORKERS``, and at most half the cores, so a foreground preview
    keeps at least one core (a single-core machine saves in turn)."""
    cpus = os.cpu_count() if cpus is None else cpus
    return max(1, min(count, MAX_WORKERS, (cpus or 1) // 2))


def _payload(bundle: Any) -> int:
    """The bundle's uncompressed size as read, a cheap estimate of its compression time (0 when unknown)."""
    try:
        return sum(obj.byte_size for obj in bundle.env.objects)
    except Exception:  # noqa: BLE001 - an estimate only orders the work
        return 0


def save_bundles(jobs: Sequence[tuple[Path, Any, Path, Any]], *, step: Callable[[], None] = lambda: None,
                 progress: Callable[[int, int], None] = lambda done, count: None,
                 workers: int | None = None) -> None:
    """Save each ``(target, bundle, output, options)`` with ``bundle.save(output, options)``.

    ``step`` is called before each save starts, on the calling thread (a job's yield to the foreground, or its
    cancellation by raising); ``progress(done, count)`` after each save ends, on the calling thread too. At most
    ``workers`` saves (``save_workers``) run at a time, the largest bundles first. A save that fails raises
    ``mod_apply_failed`` naming its ``target``; once one failed, or ``step`` raised, no other save starts and the
    ones under way end before the error is raised (their files are in the build's own directory, which the caller
    removes)."""
    order = sorted(range(len(jobs)), key=lambda i: -_payload(jobs[i][1]))
    count = len(jobs)
    progress(0, count)
    if not count:
        return
    workers = save_workers(count) if workers is None else max(1, workers)

    def save(i: int) -> None:
        target, bundle, output, options = jobs[i]
        ok, _ = bundle.save(output, options)
        if not ok:
            raise ServiceError("mod_apply_failed", file=target)

    if workers == 1:
        for done, i in enumerate(order):
            step()
            save(i)
            progress(done + 1, count)
        return
    running: set[Future[None]] = set()
    done = 0

    def collect(until: int) -> None:
        nonlocal running, done
        while len(running) > until:
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                future.result()
                done += 1
                progress(done, count)

    # the pool's exit waits for the saves under way, also when a save or ``step`` raised
    with ThreadPoolExecutor(workers, thread_name_prefix="bundle-save") as pool:
        for i in order:
            step()
            running.add(pool.submit(save, i))
            collect(workers - 1)
        collect(0)
