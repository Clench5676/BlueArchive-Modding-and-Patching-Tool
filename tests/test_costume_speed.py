"""Costume build speed: caches and skipped work leave the output unchanged."""

import copy
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from bmpt.gameassets import costume_cache as cc, model_adapt as ma


def _value(offset=0):
    poses = np.arange(2 * 3 * 16, dtype=float).reshape(2, 3, 4, 4) + offset
    return poses, np.array([0, 2], dtype=np.int64)


def test_pose_cache_keys_include_model_revision_and_schema(tmp_path, monkeypatch):
    value = _value()
    store = cc.PoseStore(tmp_path, {"one": "revision-a", "two": "revision-a"}, memory=cc.ArrayLRU())
    assert store.get("one", "clip") is None
    store.put("one", "clip", *value)
    assert np.array_equal(store.get("one", "clip")[0], value[0])
    assert store.get("two", "clip") is None
    assert store.get("one", "other-clip") is None
    assert cc.PoseStore(tmp_path, {"one": "revision-b"}).get("one", "clip") is None
    monkeypatch.setattr(cc, "POSE_SCHEMA", cc.POSE_SCHEMA + 1)
    assert store.get("one", "clip") is None


def test_pose_cache_disk_hit_and_corrupt_or_unreadable_entry_is_a_miss(tmp_path, monkeypatch):
    store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
    store.put("one", "clip", *_value())

    def cold():
        return cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())

    assert np.array_equal(cold().get("one", "clip")[0], _value()[0])
    path = next(tmp_path.glob("*.npz"))
    path.write_bytes(b"incomplete")
    assert cold().get("one", "clip") is None
    store.put("one", "clip", *_value())

    def denied(_):
        raise PermissionError("unreadable cache")

    monkeypatch.setattr(type(path), "read_bytes", denied)
    assert cold().get("one", "clip") is None


def test_pose_cache_does_not_expose_shared_arrays_or_retain_missing_revisions(tmp_path):
    store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
    poses, animated = _value()
    store.put("one", "clip", poses, animated)
    poses[:] = 0
    got = store.get("one", "clip")
    assert np.array_equal(got[0], _value()[0])
    got[0][:] = 0
    got[1][:] = 1
    assert all(np.array_equal(a, b) for a, b in zip(store.get("one", "clip"), _value()))
    store.put("missing", "clip", *_value())
    assert store.get("missing", "clip") is None
    assert len(list(tmp_path.glob("*.npz"))) == 1


def test_pose_cache_memory_is_byte_bounded_and_evicts_least_recently_used():
    value = _value()
    cache = cc.ArrayLRU(limit=2 * cc.ArrayLRU._cost("a", value))
    cache.put("a", value)
    cache.put("b", value)
    cache.get("a")
    cache.put("c", value)
    assert cache.get("a") is not None and cache.get("b") is None
    assert cache.size <= cache.limit
    cache.put("too-large", (np.ones((cache.limit,), float), value[1]))
    assert cache.get("too-large") is None and cache.size <= cache.limit


def test_pose_cache_disk_is_bounded_and_inability_to_write_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DISK_ENTRIES", 2)
    store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
    for i in range(5):
        store.put("one", str(i), *_value(i))
    assert len(list(tmp_path.glob("*.npz"))) == 2
    monkeypatch.setattr(cc, "DISK_BYTES", 1)
    store.put("one", "last", *_value())
    assert sum(p.stat().st_size for p in tmp_path.glob("*.npz")) <= cc.DISK_BYTES
    root = tmp_path / "file"
    root.write_text("not a directory")
    store = cc.PoseStore(root, {"one": "a"}, memory=cc.ArrayLRU())
    store.put("one", "clip", *_value())
    assert store.get("one", "clip") is not None


def test_pose_cache_concurrent_builds_get_whole_independent_results(tmp_path):
    def build(i):
        store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
        store.put("one", str(i % 3), *_value(i % 3))
        got = store.get("one", str(i % 3))
        assert all(np.array_equal(a, b) for a, b in zip(got, _value(i % 3)))

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(build, range(24)))


@pytest.mark.parametrize("begin,stop,rate", [(0.0, 1.0, 3.0), (-0.3, 1.4, 13.0), (0.0, 0.0, 30.0)])
def test_vectorized_decoder_is_exact_for_streamed_dense_and_constant_curves(begin, stop, rate):
    from test_model_arm_clips import _clip
    from bmpt.gameassets.model_glb import decode_clip

    tree = copy.deepcopy(_clip())
    tree["m_MuscleClip"].update(m_StartTime=begin, m_StopTime=stop)
    tree["m_SampleRate"] = rate
    before, after = decode_clip(tree), cc.decode_clip(tree)
    assert before.times == after.times and before.duration == after.duration
    assert before.curves.keys() == after.curves.keys()
    for k in before.curves:
        assert np.array_equal(before.curves[k], after.curves[k], equal_nan=True)


def _posed_model(store=None, dev="one"):
    from types import SimpleNamespace
    from test_model_adapt import _skeleton
    from test_model_arm_clips import _clip

    sk = _skeleton(
        [
            ("Bip001", 0, (1, 0, 0), (0, 0, 0, 1)),
            ("face", 1, (0, 1, 0), (0, 0, 0, 1)),
            ("prop", 1, (0, 0, 1), (0, 0, 0, 1)),
        ]
    )
    tree = _clip()
    sk.graph = SimpleNamespace(tree=lambda clip: tree)
    sk.pose_store, sk.dev = store, dev
    clip = SimpleNamespace(get_raw_data=lambda: repr(tree).encode())
    return sk, clip, tree


def test_cached_and_uncached_poses_are_exact_and_unreadable_cache_is_measured_again(tmp_path, monkeypatch):
    sk, clip, _ = _posed_model()
    expected, animated = sk.clip_poses(clip)
    sk.pose_store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
    measured, got_animated = sk.clip_poses(clip)
    assert np.array_equal(measured, expected) and animated == got_animated
    original = ma.Skeleton.clip_frames
    calls = []

    def measure(self, *args, **kwargs):
        calls.append(self.dev)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ma.Skeleton, "clip_frames", measure)

    def build(revision="a", dev="one"):
        store = cc.PoseStore(tmp_path, {dev: revision}, memory=cc.ArrayLRU())
        other, other_clip, _ = _posed_model(store, dev)
        result, changed = other.clip_poses(other_clip)
        assert np.array_equal(result, expected) and changed == animated

    build()
    assert not calls
    next(tmp_path.glob("*.npz")).write_bytes(b"broken")
    build()
    assert calls == ["one"]
    build("b")
    build(dev="two")
    assert calls == ["one", "one", "two"]
    # Local transforms are dependencies too, even when a caller keeps its revision.
    sk.nodes[1].local = ((2, 0, 0), (0, 0, 0, 1), (1, 1, 1))
    sk.clip_poses(clip)
    assert calls == ["one", "one", "two", "one"]


def test_selected_pose_nodes_equal_full_hierarchy_with_animated_ancestors():
    import zlib

    sk, _, tree = _posed_model()
    bindings = tree["m_ClipBindingConstant"]["genericBindings"]
    bindings[0]["path"] = zlib.crc32(sk.nodes[1].path.encode())
    bindings[1]["path"] = zlib.crc32(sk.nodes[2].path.encode())
    times, full, animated = sk.clip_frames(tree)
    selected_times, selected, selected_animated = sk.clip_frames(tree, [2])
    assert selected_times == times and selected_animated == animated
    for f in range(len(times)):
        a, b = full(f), selected(f)
        for node in (0, 1, 2):
            assert np.array_equal(a[node], b[node])
        assert b[3] is None


def test_shared_clip_frames_are_bit_identical_independent_and_memory_only(monkeypatch):
    monkeypatch.setattr(cc, "_poses", cc.ArrayLRU())
    sk, _, tree = _posed_model()
    times, original, _ = sk.clip_frames(tree)
    expected = [np.asarray(original(k)) for k in range(len(times))]
    calls = []
    world = ma.Skeleton.world

    def measured(self, *args, **kwargs):
        calls.append(True)
        return world(self, *args, **kwargs)

    monkeypatch.setattr(ma.Skeleton, "world", measured)
    for _ in range(2):
        _, sampled, _ = sk.clip_frames(tree, cache=True)
        for k, value in enumerate(expected):
            result = np.asarray(sampled(k))
            assert np.array_equal(result.view(np.uint64), value.view(np.uint64))
            result[0, 0, 0] = 999
    assert len(calls) == len(times)
    assert cc._poses.size <= cc._poses.limit


def test_selected_shared_frames_preserve_bits_and_do_not_alias_full_frames(monkeypatch):
    monkeypatch.setattr(cc, "_poses", cc.ArrayLRU())
    sk, _, tree = _posed_model()
    times, full, _ = sk.clip_frames(tree, cache=True)
    expected = [np.asarray(full(k)) for k in range(len(times))]
    for _ in range(2):
        _, selected, _ = sk.clip_frames(tree, [2], cache=True)
        for k, value in enumerate(expected):
            pose = selected(k)
            assert pose[3] is None
            for node in (0, 1, 2):
                assert np.array_equal(pose[node].view(np.uint64), value[node].view(np.uint64))
            pose[0][:] = 999


@pytest.mark.parametrize("changed", ["revision", "model", "binding", "hierarchy", "curve", "grid"])
def test_shared_frames_invalidate_all_pose_dependencies(monkeypatch, changed):
    import zlib

    monkeypatch.setattr(cc, "_poses", cc.ArrayLRU())
    store = cc.PoseStore(None, {"one": "a", "two": "a"})
    sk, _, tree = _posed_model(store, "one")
    sk.clip_frames(tree, cache=True)[1](0)
    if changed == "revision":
        store.revisions["one"] = "b"
    elif changed == "model":
        sk.dev = "two"
    elif changed == "binding":
        tree["m_ClipBindingConstant"]["genericBindings"][0]["path"] = zlib.crc32(sk.nodes[2].path.encode())
    elif changed == "hierarchy":
        sk.nodes[1].local = ((1, 2, 3), (0, 0, 0, 1), (1, 1, 1))
        sk._rest = []
    elif changed == "curve":
        tree["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_SampleArray"][0] += 0.25
    else:
        tree["m_SampleRate"] = 7.0
    expected = np.asarray(sk.clip_frames(tree)[1](0))
    calls = []
    original = ma.Skeleton.world

    def measured(self, *args, **kwargs):
        calls.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ma.Skeleton, "world", measured)
    actual = np.asarray(sk.clip_frames(tree, cache=True)[1](0))
    assert calls
    assert np.array_equal(actual.view(np.uint64), expected.view(np.uint64))


def test_selected_pose_nodes_keep_the_full_hierarchy_path_collision_order():
    import zlib

    sk, _, tree = _posed_model()
    sk.nodes[3].path = sk.nodes[2].path
    tree["m_ClipBindingConstant"]["genericBindings"][0]["path"] = zlib.crc32(sk.nodes[2].path.encode())
    times, full, _ = sk.clip_frames(tree)
    _, selected, _ = sk.clip_frames(tree, [2])
    for f in range(len(times)):
        assert np.array_equal(full(f)[2], selected(f)[2])


def test_vectorized_decoder_preserves_the_bits_of_a_hold_before_the_first_key():
    from test_model_arm_clips import _clip
    from bmpt.gameassets.model_glb import decode_clip
    from bmpt.gameassets.model_arm_clips import encode_streamed

    tree = _clip()
    stream = tree["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]
    stream["data"] = encode_streamed([(1.0, [(i, 0.2, -0.1, 0.3, -0.0) for i in range(7)])])
    before, after = decode_clip(tree), cc.decode_clip(tree)
    for k in before.curves:
        assert np.array_equal(np.asarray(before.curves[k]).view(np.uint64), np.asarray(after.curves[k]).view(np.uint64))


def test_clip_samples_cache_hits_and_invalidates_on_in_place_curve_or_grid_changes(monkeypatch):
    from test_model_arm_clips import _clip
    from bmpt.gameassets import model_arm_clips as ac
    from bmpt.gameassets.model_glb import decode_clip

    monkeypatch.setattr(cc, "_clips", cc.ArrayLRU())
    tree = _clip()
    expected = decode_clip(tree)
    first = cc.decode_clip(tree)
    original = ac.sample_curves
    calls = []

    def sample(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(ac, "sample_curves", sample)
    first.curves[0][0] = 999
    second = cc.decode_clip(tree)
    assert not calls and second.curves == expected.curves
    tree["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_SampleArray"][0] += 0.25
    assert cc.decode_clip(tree).curves == decode_clip(tree).curves
    tree["m_SampleRate"] = 7.0
    assert cc.decode_clip(tree).curves == decode_clip(tree).curves
    assert len(calls) == 2


@pytest.mark.parametrize(
    "poses,animated",
    [
        (np.zeros((0, 3, 4, 4)), np.array([], np.int64)),
        (np.full((2, 3, 4, 4), np.nan), np.array([0], np.int64)),
        (np.zeros((2, 3, 4, 4)), np.array([4], np.int64)),
    ],
)
def test_invalid_numeric_cache_entries_are_measured_again(tmp_path, poses, animated):
    store = cc.PoseStore(tmp_path, {"one": "a"}, memory=cc.ArrayLRU())
    key = store._key("one", "clip")
    np.savez_compressed(tmp_path / f"{key}.npz", poses=poses, animated=animated)
    assert store.get("one", "clip") is None
    store.put("one", "clip", *_value())
    assert np.array_equal(store.get("one", "clip")[0], _value()[0])


# --- saving a build's bundles in threads (library.bundle_saves) ---

import threading
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

from UnityPy.helpers import CompressionHelper

from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.models import SaveOptions
from bmpt.core.errors import ServiceError
from bmpt.library import bundle_saves as bs


def _engine_bundle(seed, size):
    """An engine Bundle whose environment packs ``size`` bytes the way a UnityFS save does (one LZMA block)."""
    rng = np.random.default_rng(seed)
    payload = bytes(rng.integers(0, 16, size, dtype=np.uint8))  # compressible, distinct per bundle

    def save(packer):
        assert packer == "lzma"
        data, blocks = CompressionHelper.chunk_based_compress(payload, 1)
        return b"UnityFS\0" + repr(blocks).encode() + data

    env = SimpleNamespace(file=SimpleNamespace(save=save), objects=[SimpleNamespace(byte_size=size)])
    return Bundle(Path(f"part{seed}.bundle"), env)


def _jobs(directory, sizes, crc=True):
    jobs = []
    for i, size in enumerate(sizes):
        target = Path(f"game/slot_{i}_assets_all_{1000 + i}.bundle")
        jobs.append((target, _engine_bundle(i, size), directory / target.name, SaveOptions(perform_crc=crc)))
    directory.mkdir()
    return jobs


@pytest.mark.parametrize("crc", [True, False])
def test_bundles_saved_in_threads_are_the_bytes_saved_in_turn(tmp_path, crc):
    sizes = [200_000, 3_000, 900_000, 40_000, 1, 500_000]
    serial, threaded = _jobs(tmp_path / "serial", sizes, crc), _jobs(tmp_path / "threaded", sizes, crc)
    bs.save_bundles(serial, workers=1)
    bs.save_bundles(threaded, workers=4)
    for (target, _, one, _), (_, _, other, _) in zip(serial, threaded):
        assert one.read_bytes() == other.read_bytes()
        if crc:  # the CRC fix still lands on the name's value
            assert zlib.crc32(other.read_bytes()) == int(target.stem.rsplit("_", 1)[1])
    assert sorted(p.name for p in (tmp_path / "threaded").iterdir()) == sorted(t.name for t, *_ in threaded)


class _Slow:
    """A bundle whose save takes ``delay`` seconds and records when it ran."""

    def __init__(self, name, delay, size, log, ok=True):
        self.name, self.delay, self.ok, self.log = name, delay, ok, log
        self.env = SimpleNamespace(objects=[SimpleNamespace(byte_size=size)])

    def save(self, output, options):
        self.log.append(("start", self.name, threading.current_thread().name))
        time.sleep(self.delay)
        if self.ok:
            Path(output).write_bytes(self.name.encode())
        self.log.append(("end", self.name))
        return self.ok, "message"


def _slow_jobs(tmp_path, specs, log):
    return [
        (Path(f"game/{name}"), _Slow(name, delay, size, log, ok), tmp_path / name, None)
        for name, delay, size, ok in specs
    ]


def test_concurrent_save_results_do_not_depend_on_finish_order_and_largest_starts_first(tmp_path):
    log, progress, steps = [], [], []
    specs = [("small", 0.15, 10, True), ("large", 0.05, 1000, True), ("mid", 0.01, 100, True), ("tiny", 0.0, 1, True)]
    jobs = _slow_jobs(tmp_path, specs, log)
    main = threading.current_thread()
    bs.save_bundles(
        jobs,
        workers=2,
        step=lambda: steps.append(threading.current_thread() is main),
        progress=lambda done, count: progress.append((done, count, threading.current_thread() is main)),
    )
    assert [e[1] for e in log if e[0] == "start"][:2] == ["large", "mid"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {name: name.encode() for name, *_ in specs}
    assert steps == [True] * 4  # one step per save, on the calling thread
    assert progress == [(i, 4, True) for i in range(5)]
    assert all(e[2] != main.name for e in log if e[0] == "start")


def test_a_failed_concurrent_save_names_its_file_and_starts_no_other(tmp_path):
    log = []
    specs = [("a", 0.2, 50, True), ("bad", 0.0, 40, False), ("c", 0.0, 30, True), ("d", 0.0, 20, True)]
    with pytest.raises(ServiceError) as error:
        bs.save_bundles(_slow_jobs(tmp_path, specs, log), workers=2)
    assert error.value.code == "mod_apply_failed"
    assert error.value.params["file"] == Path("game/bad")
    started = [e[1] for e in log if e[0] == "start"]
    assert started == ["a", "bad"]  # nothing started after the failure
    assert ("end", "a") in log  # the save under way ended before the error was raised
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a"]


def test_cancelling_between_concurrent_saves_waits_for_the_saves_under_way(tmp_path):
    log, calls = [], []

    def step():
        calls.append(1)
        if len(calls) == 3:
            raise ServiceError("busy")

    specs = [(name, 0.1, 10 - i, True) for i, name in enumerate("abcdef")]
    with pytest.raises(ServiceError) as error:
        bs.save_bundles(_slow_jobs(tmp_path, specs, log), workers=2, step=step)
    assert error.value.code == "busy"
    assert [e[1] for e in log if e[0] == "start"] == ["a", "b"]
    assert sorted(e[1] for e in log if e[0] == "end") == ["a", "b"]  # both ended before the error was raised
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b"]


def test_cancelling_before_the_first_save_writes_nothing(tmp_path):
    log = []

    def step():
        raise ServiceError("busy")

    for workers in (1, 3):
        with pytest.raises(ServiceError):
            bs.save_bundles(_slow_jobs(tmp_path, [("a", 0.0, 1, True)], log), workers=workers, step=step)
    assert log == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "count, cpus, expected",
    [(6, 12, 4), (6, 8, 4), (6, 4, 2), (6, 3, 1), (6, 2, 1), (6, 1, 1), (6, None, 1), (2, 12, 2), (1, 12, 1)],
)
def test_save_workers_leave_half_the_cores(count, cpus, expected, monkeypatch):
    if cpus is None:
        monkeypatch.setattr(bs.os, "cpu_count", lambda: None)
        assert bs.save_workers(count) == expected
    else:
        assert bs.save_workers(count, cpus) == expected


def test_fast_lzma_round_trips_and_stays_on_its_thread():
    import random
    import threading

    from UnityPy.helpers import CompressionHelper as helper

    from bmpt.library.bundle_saves import fast_lzma, install_fast_lzma

    rng = random.Random(7)
    data = bytes(rng.choice(b"abcdefgh") for _ in range(200_000)) * 3
    normal = helper.compress_lzma(data, True)
    install_fast_lzma()
    install_fast_lzma()  # idempotent
    assert helper.compress_lzma(data, True) == normal  # outside the context: UnityPy's own bytes
    with fast_lzma():
        fast = helper.compress_lzma(data, True)
        chunked = helper.COMPRESSION_MAP[helper.CompressionFlags.LZMA](data)  # the bundle writer's entry
        other: list[bytes] = []
        thread = threading.Thread(target=lambda: other.append(helper.compress_lzma(data, True)))
        thread.start()
        thread.join()
    assert other == [normal]  # another thread keeps the normal mode
    assert fast != normal and fast[:5] == normal[:5]  # same properties and dictionary in the header
    assert helper.decompress_lzma(fast, True) == data
    assert helper.decompress_lzma(chunked, False) == data
    assert helper.compress_lzma(data, True) == normal
