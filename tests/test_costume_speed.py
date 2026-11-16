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
    store = cc.PoseStore(tmp_path, {'one': 'revision-a', 'two': 'revision-a'}, memory=cc.ArrayLRU())
    assert store.get('one', 'clip') is None
    store.put('one', 'clip', *value)
    assert np.array_equal(store.get('one', 'clip')[0], value[0])
    assert store.get('two', 'clip') is None
    assert store.get('one', 'other-clip') is None
    assert cc.PoseStore(tmp_path, {'one': 'revision-b'}).get('one', 'clip') is None
    monkeypatch.setattr(cc, 'POSE_SCHEMA', cc.POSE_SCHEMA + 1)
    assert store.get('one', 'clip') is None


def test_pose_cache_disk_hit_and_corrupt_or_unreadable_entry_is_a_miss(tmp_path, monkeypatch):
    store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    store.put('one', 'clip', *_value())
    def cold():
        return cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    assert np.array_equal(cold().get('one', 'clip')[0], _value()[0])
    path = next(tmp_path.glob('*.npz'))
    path.write_bytes(b'incomplete')
    assert cold().get('one', 'clip') is None
    store.put('one', 'clip', *_value())
    def denied(_):
        raise PermissionError('unreadable cache')
    monkeypatch.setattr(type(path), 'read_bytes', denied)
    assert cold().get('one', 'clip') is None


def test_pose_cache_does_not_expose_shared_arrays_or_retain_missing_revisions(tmp_path):
    store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    poses, animated = _value()
    store.put('one', 'clip', poses, animated)
    poses[:] = 0
    got = store.get('one', 'clip')
    assert np.array_equal(got[0], _value()[0])
    got[0][:] = 0
    got[1][:] = 1
    assert all(np.array_equal(a, b) for a, b in zip(store.get('one', 'clip'), _value()))
    store.put('missing', 'clip', *_value())
    assert store.get('missing', 'clip') is None
    assert len(list(tmp_path.glob('*.npz'))) == 1


def test_pose_cache_memory_is_byte_bounded_and_evicts_least_recently_used():
    value = _value()
    cache = cc.ArrayLRU(limit=2 * cc.ArrayLRU._cost('a', value))
    cache.put('a', value)
    cache.put('b', value)
    cache.get('a')
    cache.put('c', value)
    assert cache.get('a') is not None and cache.get('b') is None
    assert cache.size <= cache.limit
    cache.put('too-large', (np.ones((cache.limit,), float), value[1]))
    assert cache.get('too-large') is None and cache.size <= cache.limit


def test_pose_cache_disk_is_bounded_and_inability_to_write_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, 'DISK_ENTRIES', 2)
    store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    for i in range(5):
        store.put('one', str(i), *_value(i))
    assert len(list(tmp_path.glob('*.npz'))) == 2
    monkeypatch.setattr(cc, 'DISK_BYTES', 1)
    store.put('one', 'last', *_value())
    assert sum(p.stat().st_size for p in tmp_path.glob('*.npz')) <= cc.DISK_BYTES
    root = tmp_path / 'file'
    root.write_text('not a directory')
    store = cc.PoseStore(root, {'one': 'a'}, memory=cc.ArrayLRU())
    store.put('one', 'clip', *_value())
    assert store.get('one', 'clip') is not None


def test_pose_cache_concurrent_builds_get_whole_independent_results(tmp_path):
    def build(i):
        store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
        store.put('one', str(i % 3), *_value(i % 3))
        got = store.get('one', str(i % 3))
        assert all(np.array_equal(a, b) for a, b in zip(got, _value(i % 3)))
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(build, range(24)))


@pytest.mark.parametrize('begin,stop,rate', [(0., 1., 3.), (-.3, 1.4, 13.), (0., 0., 30.)])
def test_vectorized_decoder_is_exact_for_streamed_dense_and_constant_curves(begin, stop, rate):
    from test_model_arm_clips import _clip
    from bmpt.gameassets.model import decode_clip
    tree = copy.deepcopy(_clip())
    tree['m_MuscleClip'].update(m_StartTime=begin, m_StopTime=stop)
    tree['m_SampleRate'] = rate
    before, after = decode_clip(tree), cc.decode_clip(tree)
    assert before.times == after.times and before.duration == after.duration
    assert before.curves.keys() == after.curves.keys()
    for k in before.curves:
        assert np.array_equal(before.curves[k], after.curves[k], equal_nan=True)


def _posed_model(store=None, dev='one'):
    from types import SimpleNamespace
    from test_model_adapt import _skeleton
    from test_model_arm_clips import _clip
    sk = _skeleton([('Bip001', 0, (1, 0, 0), (0, 0, 0, 1)),
                    ('face', 1, (0, 1, 0), (0, 0, 0, 1)),
                    ('prop', 1, (0, 0, 1), (0, 0, 0, 1))])
    tree = _clip()
    sk.graph = SimpleNamespace(tree=lambda clip: tree)
    sk.pose_store, sk.dev = store, dev
    clip = SimpleNamespace(get_raw_data=lambda: repr(tree).encode())
    return sk, clip, tree


def test_cached_and_uncached_poses_are_exact_and_unreadable_cache_is_measured_again(tmp_path, monkeypatch):
    sk, clip, _ = _posed_model()
    expected, animated = sk.clip_poses(clip)
    sk.pose_store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    measured, got_animated = sk.clip_poses(clip)
    assert np.array_equal(measured, expected) and animated == got_animated
    original = ma.Skeleton.clip_frames
    calls = []
    def measure(self, *args, **kwargs):
        calls.append(self.dev)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ma.Skeleton, 'clip_frames', measure)
    def build(revision='a', dev='one'):
        store = cc.PoseStore(tmp_path, {dev: revision}, memory=cc.ArrayLRU())
        other, other_clip, _ = _posed_model(store, dev)
        result, changed = other.clip_poses(other_clip)
        assert np.array_equal(result, expected) and changed == animated
    build()
    assert not calls
    next(tmp_path.glob('*.npz')).write_bytes(b'broken')
    build()
    assert calls == ['one']
    build('b')
    build(dev='two')
    assert calls == ['one', 'one', 'two']
    # Local transforms are dependencies too, even when a caller keeps its revision.
    sk.nodes[1].local = ((2, 0, 0), (0, 0, 0, 1), (1, 1, 1))
    sk.clip_poses(clip)
    assert calls == ['one', 'one', 'two', 'one']


def test_selected_pose_nodes_equal_full_hierarchy_with_animated_ancestors():
    import zlib
    sk, _, tree = _posed_model()
    bindings = tree['m_ClipBindingConstant']['genericBindings']
    bindings[0]['path'] = zlib.crc32(sk.nodes[1].path.encode())
    bindings[1]['path'] = zlib.crc32(sk.nodes[2].path.encode())
    times, full, animated = sk.clip_frames(tree)
    selected_times, selected, selected_animated = sk.clip_frames(tree, [2])
    assert selected_times == times and selected_animated == animated
    for f in range(len(times)):
        a, b = full(f), selected(f)
        for node in (0, 1, 2):
            assert np.array_equal(a[node], b[node])
        assert b[3] is None


def test_selected_pose_nodes_keep_the_full_hierarchy_path_collision_order():
    import zlib
    sk, _, tree = _posed_model()
    sk.nodes[3].path = sk.nodes[2].path
    tree['m_ClipBindingConstant']['genericBindings'][0]['path'] = zlib.crc32(sk.nodes[2].path.encode())
    times, full, _ = sk.clip_frames(tree)
    _, selected, _ = sk.clip_frames(tree, [2])
    for f in range(len(times)):
        assert np.array_equal(full(f)[2], selected(f)[2])


def test_vectorized_decoder_preserves_the_bits_of_a_hold_before_the_first_key():
    from test_model_arm_clips import _clip
    from bmpt.gameassets.model import decode_clip
    from bmpt.gameassets.model_arm_clips import encode_streamed
    tree = _clip()
    stream = tree['m_MuscleClip']['m_Clip']['data']['m_StreamedClip']
    stream['data'] = encode_streamed([(1., [(i, .2, -.1, .3, -0.) for i in range(7)])])
    before, after = decode_clip(tree), cc.decode_clip(tree)
    for k in before.curves:
        assert np.array_equal(np.asarray(before.curves[k]).view(np.uint64),
                              np.asarray(after.curves[k]).view(np.uint64))


def test_clip_samples_cache_hits_and_invalidates_on_in_place_curve_or_grid_changes(monkeypatch):
    from test_model_arm_clips import _clip
    from bmpt.gameassets import model_arm_clips as ac
    from bmpt.gameassets.model import decode_clip
    monkeypatch.setattr(cc, '_clips', cc.ArrayLRU())
    tree = _clip()
    expected = decode_clip(tree)
    first = cc.decode_clip(tree)
    original = ac.sample_curves
    calls = []
    def sample(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(ac, 'sample_curves', sample)
    first.curves[0][0] = 999
    second = cc.decode_clip(tree)
    assert not calls and second.curves == expected.curves
    tree['m_MuscleClip']['m_Clip']['data']['m_DenseClip']['m_SampleArray'][0] += .25
    assert cc.decode_clip(tree).curves == decode_clip(tree).curves
    tree['m_SampleRate'] = 7.
    assert cc.decode_clip(tree).curves == decode_clip(tree).curves
    assert len(calls) == 2


@pytest.mark.parametrize('poses,animated', [(np.zeros((0, 3, 4, 4)), np.array([], np.int64)),
                                            (np.full((2, 3, 4, 4), np.nan), np.array([0], np.int64)),
                                            (np.zeros((2, 3, 4, 4)), np.array([4], np.int64))])
def test_invalid_numeric_cache_entries_are_measured_again(tmp_path, poses, animated):
    store = cc.PoseStore(tmp_path, {'one': 'a'}, memory=cc.ArrayLRU())
    key = store._key('one', 'clip')
    np.savez_compressed(tmp_path / f'{key}.npz', poses=poses, animated=animated)
    assert store.get('one', 'clip') is None
    store.put('one', 'clip', *_value())
    assert np.array_equal(store.get('one', 'clip')[0], _value()[0])
