"""Bounded caches of derived costume poses, and lossless clip sampling."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
import zipfile
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Mapping

import numpy as np

POSE_SCHEMA = 1
CLIP_SCHEMA = 1
POSE_BYTES = 32 * 1024 * 1024
CLIP_BYTES = 32 * 1024 * 1024
DISK_BYTES = 32 * 1024 * 1024
ENTRY_BYTES = 512 * 1024
DISK_ENTRIES = 256


class ArrayLRU:
    """Only numerical arrays; copying at both boundaries keeps builds independent."""

    def __init__(self, limit: int = POSE_BYTES):
        self.limit = limit
        self.size = 0
        self._entries: OrderedDict[str, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _cost(key: str, value: tuple[np.ndarray, np.ndarray]) -> int:
        return len(key.encode()) + 256 + sum(a.nbytes for a in value)

    def get(self, key: str) -> tuple[np.ndarray, np.ndarray] | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
        return tuple(a.copy() for a in value) if value is not None else None

    def put(self, key: str, value: tuple[np.ndarray, np.ndarray]) -> None:
        cost = self._cost(key, value)
        if cost > self.limit:
            return
        value = tuple(a.copy() for a in value)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self.size -= self._cost(key, previous)
            self._entries[key] = value
            self.size += cost
            while self.size > self.limit:
                old_key, old = self._entries.popitem(last=False)
                self.size -= self._cost(old_key, old)


_poses = ArrayLRU()
_clips = ArrayLRU(CLIP_BYTES)


class PoseStore:
    """Caller-supplied poses keyed by schema, model, game revision and input digest.

    Disk entries hold only sampled matrices and animated node indices, never
    readers or asset data. Atomic replacement permits concurrent builds; an
    unreadable entry is a miss. Cache locks cover memory bookkeeping alone;
    copies and disk IO run without them. No library or model lock is added.
    Class 1 previews and class 2 checks retain their caller's cancellation and
    yield steps.
    """

    def __init__(self, root: Path | None, revisions: Mapping[str, str], *, memory: ArrayLRU | None = None):
        self.root, self.revisions = root, dict(revisions)
        self.memory = memory if memory is not None else _poses

    def _key(self, dev: str, token: str) -> str | None:
        revision = self.revisions.get(dev)
        if not revision:
            return None
        return hashlib.sha256(f"{POSE_SCHEMA}|{dev}|{revision}|{token}".encode()).hexdigest()

    @staticmethod
    def _valid(poses: np.ndarray, animated: np.ndarray) -> bool:
        return (poses.dtype == np.float64 and poses.ndim == 4 and poses.shape[2:] == (4, 4)
                and 0 < poses.shape[0] <= 3 and poses.shape[1] > 0
                and poses.nbytes + animated.nbytes <= ENTRY_BYTES and np.isfinite(poses).all()
                and animated.dtype == np.int64 and animated.ndim == 1
                and len(animated) <= poses.shape[1]
                and ((animated >= 0) & (animated < poses.shape[1])).all())

    def get(self, dev: str, token: str) -> tuple[np.ndarray, np.ndarray] | None:
        key = self._key(dev, token)
        if key is None:
            return None
        found = self.memory.get(key)
        if found is not None or self.root is None:
            return found
        path = self.root / f"{key}.npz"
        try:
            if path.stat().st_size > ENTRY_BYTES:
                return None
            raw = path.read_bytes()
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(i.file_size for i in archive.infolist()) > ENTRY_BYTES + 4096:
                    return None
            with np.load(io.BytesIO(raw), allow_pickle=False) as data:
                poses, animated = data["poses"], data["animated"]
            if not self._valid(poses, animated):
                return None
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile, zlib.error):
            return None
        self.memory.put(key, (poses, animated))
        return poses, animated

    def put(self, dev: str, token: str, poses: np.ndarray, animated: np.ndarray) -> None:
        key = self._key(dev, token)
        if key is None or not self._valid(poses, animated):
            return
        self.memory.put(key, (poses, animated))
        if self.root is None:
            return
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".pose-", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                np.savez_compressed(f, poses=poses, animated=animated)
            if temporary.stat().st_size > ENTRY_BYTES:
                return
            os.replace(temporary, self.root / f"{key}.npz")
            self._prune()
        except OSError:
            pass  # inability to cache never prevents a costume build
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _prune(self) -> None:
        entries = []
        for path in self.root.glob("*.npz"):
            try:
                stat = path.stat()
                entries.append((stat.st_mtime_ns, path, stat.st_size))
            except OSError:
                continue  # another build may be pruning the same directory
        size = 0
        for i, (_, path, count) in enumerate(sorted(entries, reverse=True)):
            size += count
            if i >= DISK_ENTRIES or size > DISK_BYTES:
                path.unlink(missing_ok=True)


def clip_digest(tree: Mapping) -> str:
    """Content identity of the decoded grid, including in-place curve edits."""
    muscle = tree["m_MuscleClip"]
    data = muscle["m_Clip"]["data"]
    streamed, dense, constant = data["m_StreamedClip"], data["m_DenseClip"], data["m_ConstantClip"]
    ns, nd = streamed["curveCount"], dense["m_CurveCount"]
    # Content keys invalidate even in-place edits of the writer's trees. No
    # graph, tree or asset reader survives a build; only numerical samples do.
    samples = dense.get("m_SampleArray", [])
    digest = hashlib.sha256(repr((CLIP_SCHEMA, muscle["m_StartTime"], muscle["m_StopTime"], tree.get("m_SampleRate"),
                                 ns, nd, dense["m_FrameCount"], dense.get("m_BeginTime"), dense.get("m_SampleRate"),
                                 len(streamed["data"]), len(samples), len(constant["data"]))).encode())
    for values, dtype in ((streamed["data"], "<u4"), (samples, "<f8"), (constant["data"], "<f8")):
        digest.update(np.asarray(values, dtype=dtype).tobytes())
    return digest.hexdigest()


def memoize_pose(pose, token: str, indices: tuple[int, ...] | None = None, count: int = 0):
    """Share sampled frames in the existing bounded pose memory, never on disk.

    The caller identifies the model revision, hierarchy, bindings and clip
    content. Copies keep solvers independent; the lock covers bookkeeping only.
    """
    def sampled(frame: int):
        key = f"frame|{token}|{frame}"
        found = _poses.get(key)
        if found is not None:
            # a fresh copy per call: callers may change the matrices they get in place
            matrices = found[0].copy()
            if indices is None:
                return list(matrices)
            result = [None] * count
            for node, matrix in zip(indices, matrices):
                result[node] = matrix
            return result
        result = pose(frame)
        matrices = np.asarray(result if indices is None else [result[n] for n in indices])
        _poses.put(key, (matrices, np.empty(0, dtype=np.int64)))
        return result

    return sampled


def decode_clip(tree: Mapping):
    """The runtime decoder's scalar arithmetic on NumPy arrays, in the same order.

    All stored curves remain present for read-back checks. Vectorizing the sample
    grid avoids Python's curve-by-frame loop without changing float precision,
    the endpoint epsilon, or the hold before a streamed curve's first key.
    """
    from .model import DecodedClip
    from .model_arm_clips import sample_curves

    muscle = tree["m_MuscleClip"]
    data = muscle["m_Clip"]["data"]
    streamed, dense, constant = data["m_StreamedClip"], data["m_DenseClip"], data["m_ConstantClip"]
    ns, nd = streamed["curveCount"], dense["m_CurveCount"]
    key = clip_digest(tree)
    duration = muscle["m_StopTime"] - muscle["m_StartTime"]
    found = _clips.get(key)
    if found is not None:
        values, layout = found
        count = int(layout[0])
        times, curves, at = values[:count].tolist(), {}, count
        for index, size in layout[1:].reshape(-1, 2):
            curves[int(index)] = values[at:at + size].tolist()
            at += size
        for k, value in enumerate(constant["data"]):
            curves[ns + nd + k] = [value]
        return DecodedClip(duration, times, curves)
    times, sampled = sample_curves(tree, range(ns + nd), hold_before=True)
    curves = {k: v.tolist() for k, v in sampled.items() if k < ns + nd}
    # Constants retain their one-value storage, rather than a repeated sample.
    for k, value in enumerate(constant["data"]):
        curves[ns + nd + k] = [value]
    layout = np.array([len(times), *(n for k, values in curves.items() for n in (k, len(values)))], dtype=np.int64)
    values = np.concatenate([times, *(np.asarray(v) for v in curves.values())])
    _clips.put(key, (values, layout))
    return DecodedClip(duration, times.tolist(), curves)
