"""Writing several built bundles at once.

Almost all of a bundle's save is its compression (LZMA, one block per bundle), which releases the GIL; the
serializing before it copies the untouched objects' bytes as read. So the bundles of one build are written in
threads, each into its own file: the bytes are those of a save in turn, only the time differs. A single bundle
cannot be split without changing its bytes, so the largest one bounds the phase; it goes first.

Concurrency: the saves keep their caller's class and locks (a costume build: class 1 for a preview's own build, class
2 for the costume check, both without the library lock; class 2 under it for an apply the user watches) and take no
lock of their own. A save is one step (the largest about 7 s, all of it compression); ``step`` runs
before each one starts, so a waiting foreground preview stops new saves and gets at least half the cores.

``fast``: LZMA with the fast mode and a hash-chain match finder (``fast_lzma``) instead of UnityPy's normal mode;
the stream keeps UnityPy's header (lc 3, lp 0, pb 2, an 8 MiB dictionary), so the game can read it, and is
about three times faster for some 3 % more bytes.
"""

from __future__ import annotations

import lzma
import os
import struct
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Sequence

from ..errors import ServiceError

MAX_WORKERS = 4
LZMA_DICT = 0x800000  # UnityPy's dictionary size, written in the header
LZMA_PROPS = 0x5D  # lc 3, lp 0, pb 2, UnityPy's

_fast = threading.local()
_install = threading.Lock()


def _fast_lzma(original: Callable[..., bytes]) -> Callable[..., bytes]:
    def compress_lzma(data, write_decompressed_size: bool = False) -> bytes:
        if not getattr(_fast, "on", False):
            return original(data, write_decompressed_size)
        compressor = lzma.LZMACompressor(
            format=lzma.FORMAT_RAW,
            filters=[
                {
                    "id": lzma.FILTER_LZMA1,
                    "dict_size": LZMA_DICT,
                    "lc": 3,
                    "lp": 0,
                    "pb": 2,
                    "mode": lzma.MODE_FAST,
                    "mf": lzma.MF_HC4,
                    "nice_len": 32,
                }
            ],
        )
        body = compressor.compress(data) + compressor.flush()
        if write_decompressed_size:
            return struct.pack("<BIQ", LZMA_PROPS, LZMA_DICT, len(data)) + body
        return struct.pack("<BI", LZMA_PROPS, LZMA_DICT) + body

    compress_lzma.fast_of = original  # type: ignore[attr-defined]
    return compress_lzma


def install_fast_lzma() -> None:
    """Install the LZMA wrapper once, enabling fast compression only inside fast_lzma."""
    from UnityPy.helpers import CompressionHelper as helper

    with _install:
        if hasattr(helper.compress_lzma, "fast_of"):
            return
        wrapped = _fast_lzma(helper.compress_lzma)
        for flag, function in list(helper.COMPRESSION_MAP.items()):
            if function is helper.compress_lzma:
                helper.COMPRESSION_MAP[flag] = wrapped
        helper.compress_lzma = wrapped


class fast_lzma:
    """Enable fast LZMA saves on this thread after install_fast_lzma has run."""

    def __enter__(self) -> None:
        """Enable fast mode while remembering the thread's enclosing mode."""
        self.before = getattr(_fast, "on", False)
        _fast.on = True

    def __exit__(self, *_: Any) -> None:
        """Restore the thread's enclosing compression mode."""
        _fast.on = self.before


def save_workers(count: int, cpus: int | None = None) -> int:
    """Limit saves to MAX_WORKERS and half the cores, reserving CPU for foreground previews.

    Args:
        count: Number of bundles awaiting saves.
        cpus: Available logical CPU count; None queries the system.

    Returns:
        At least one worker, even on a single-core machine or when the CPU count is unknown.
    """
    cpus = os.cpu_count() if cpus is None else cpus
    return max(1, min(count, MAX_WORKERS, (cpus or 1) // 2))


def _payload(bundle: Any) -> int:
    """The bundle's uncompressed size as read, a cheap estimate of its compression time (0 when unknown)."""
    try:
        return sum(obj.byte_size for obj in bundle.env.objects)
    except Exception:  # noqa: BLE001 - an estimate only orders the work
        return 0


def save_bundles(
    jobs: Sequence[tuple[Path, Any, Path, Any]],
    *,
    step: Callable[[], None] = lambda: None,
    progress: Callable[[int, int], None] = lambda done, count: None,
    workers: int | None = None,
    fast: bool = False,
) -> None:
    """Save bundles concurrently, largest first, with callbacks on the calling thread.

    ``step`` is called before each save starts, on the calling thread (a job's yield to the foreground, or its
    cancellation by raising); ``progress(done, count)`` after each save ends, on the calling thread too. At most
    ``workers`` saves (``save_workers``) run at a time, the largest bundles first. A save that fails raises
    ``mod_apply_failed`` naming its ``target``; once one failed, or ``step`` raised, no other save starts and the
    ones under way end before the error is raised (their files are in the build's own directory, which the caller
    removes).

    Args:
        jobs: (target, bundle, output, options) tuples; target names failures and output
            receives bundle.save's bytes with the supplied save options.
        step: Runs before each save starts; may yield to foreground work or cancel by raising.
        progress: Receives (completed saves, total saves), initially and after each completion.
        workers: Maximum concurrent saves; None uses save_workers, and values below one use one.
        fast: Whether worker threads use the faster LZMA settings described above.

    Raises:
        ServiceError: A bundle cannot be saved (mod_apply_failed), naming its target.
    """
    order = sorted(range(len(jobs)), key=lambda i: -_payload(jobs[i][1]))
    count = len(jobs)
    progress(0, count)
    if not count:
        return
    workers = save_workers(count) if workers is None else max(1, workers)
    if fast:
        install_fast_lzma()

    def save(i: int) -> None:
        target, bundle, output, options = jobs[i]
        if fast:
            with fast_lzma():
                ok, _ = bundle.save(output, options)
        else:
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
