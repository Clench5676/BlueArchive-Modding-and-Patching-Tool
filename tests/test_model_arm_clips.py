"""Streamed arm curves retain all other data and only slot consumers change."""

import copy
from types import SimpleNamespace

import numpy as np
import pytest

from bmpt.gameassets import chibi_arm_clips as ac, chibi_skirts as ms
from bmpt.gameassets.chibi_glb import decode_clip, read_streamed
from module_patches import patch_everywhere


def _binding(path, attr=1, type_id=4):
    return dict(
        path=path, attribute=attr, typeID=type_id, customType=0, isPPtrCurve=0, script={"m_FileID": 0, "m_PathID": 0}
    )


def _clip():
    # streamed position + arm quaternion; dense position + arm Euler;
    # constant hand quaternion + non-Transform scalar.
    keys = [(i, 0.2, -0.1, 0.3, float(i)) for i in range(7)]
    data = {
        "m_StreamedClip": {
            "curveCount": 7,
            "data": ac.encode_streamed(
                [
                    (-float("inf"), keys),
                    (0.0, keys),
                    (1.0, [(i, 0.0, 0.0, 0.0, float(i + 1)) for i in range(7)]),
                    (float("inf"), []),
                ]
            ),
        },
        "m_DenseClip": {
            "m_CurveCount": 6,
            "m_FrameCount": 4,
            "m_BeginTime": 0.0,
            "m_SampleRate": 3.0,
            "m_SampleArray": [float(i) / 8 for i in range(24)],
        },
        "m_ConstantClip": {"data": [0.0, 0.0, 0.0, 1.0, 0.25]},
    }
    tree = {
        "m_Name": "Synthetic",
        "m_SampleRate": 3.0,
        "m_Legacy": False,
        "m_ClipBindingConstant": {
            "genericBindings": [
                _binding(1),
                _binding(10, 2),
                _binding(2),
                _binding(20, 4),
                _binding(30, 2),
                _binding(50, 99, 114),
            ]
        },
        "m_MuscleClip": {
            "m_StartTime": 0.0,
            "m_StopTime": 1.0,
            "m_Clip": {"data": data},
            "m_ValueArrayDelta": [{"m_Start": float(i), "m_Stop": float(i + 1)} for i in range(18)],
            "m_ValueArrayReferencePose": [float(i) for i in range(18)],
        },
    }
    tree["m_MuscleClipSize"] = ms.muscle_size(tree)
    return tree


def _rotations():
    values = np.array([[0.0, 0.0, np.sin(x), np.cos(x)] for x in (0.0, 0.1, 0.2, 0.3)])
    return {path: values.copy() for path in (10, 20, 30, 40, 60, 70)}


def test_streamed_encoder_round_trip_preserves_cubics_dense_constants_and_bindings():
    tree = _clip()
    before = copy.deepcopy(tree)
    rotations = _rotations()
    ac.encode_rotations(tree, rotations)
    assert ac.edit_error(before, tree, rotations) is None
    data, streamed, dense = ac._layout(tree)
    assert (streamed, dense, len(data["m_ConstantClip"]["data"])) == (27, 3, 1)
    assert data["m_ConstantClip"]["data"] == [0.25]
    assert data["m_DenseClip"]["m_SampleArray"] == [float(i) / 8 for f in range(4) for i in range(f * 6, f * 6 + 3)]
    old_keys = [
        (t, [k for k in keys if k[0] < 3])
        for t, keys in read_streamed(before["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"])
    ]
    new_keys = [(t, [k for k in keys if k[0] < 3]) for t, keys in read_streamed(data["m_StreamedClip"]["data"])]
    assert [(t, keys) for t, keys in new_keys if keys or not np.isfinite(t)] == old_keys
    assert len(tree["m_MuscleClip"]["m_ValueArrayReferencePose"]) == 31
    for path in rotations:
        index = next(i for i, _, b in ac._bindings(tree) if b["path"] == path)
        keys = [
            k
            for t, frame in read_streamed(data["m_StreamedClip"]["data"])
            if np.isfinite(t)
            for k in frame
            if index <= k[0] < index + 4
        ]
        assert len(keys) == 16
    broken = copy.deepcopy(tree)
    broken["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"][0] += 1
    assert "curve" in ac.edit_error(before, broken, rotations)
    broken = copy.deepcopy(tree)
    broken["m_MuscleClip"]["m_ValueArrayDelta"][0]["m_Start"] += 1
    assert "value delta changed" in ac.edit_error(before, broken, rotations)
    broken = copy.deepcopy(tree)
    wrong = _rotations()
    wrong[10][:, 0] = 1
    assert "differs from solve" in ac.edit_error(before, broken, wrong)


def test_encoder_refuses_inconsistent_layout():
    tree = _clip()
    tree["m_MuscleClipSize"] += 4
    with pytest.raises(ValueError, match="layout"):
        ac.encode_rotations(tree, _rotations())


def test_repoint_changes_only_effective_overrides_and_field_lists():
    class Writer:
        @staticmethod
        def external(sf, cab):
            assert cab == "cab-slot"
            return 0

    owner = SimpleNamespace(
        type=SimpleNamespace(name="AnimatorOverrideController"),
        assets_file=SimpleNamespace(name="CAB-slot", externals=[]),
    )
    ptr = lambda n: {"m_FileID": 0, "m_PathID": n}
    tree = {
        "m_Controller": ptr(100),
        "m_Clips": [
            {"m_OriginalClip": ptr(1), "m_OverrideClip": ptr(2)},
            {"m_OriginalClip": ptr(3), "m_OverrideClip": ptr(0)},
            {"m_OriginalClip": ptr(4), "m_OverrideClip": ptr(5)},
        ],
    }
    before = copy.deepcopy(tree)
    copies = {("cab-slot", 2): ("cab-slot", 20), ("cab-slot", 3): ("cab-slot", 30)}
    assert ac.repoint(tree, owner, copies, Writer())
    assert [p["m_OverrideClip"]["m_PathID"] for p in tree["m_Clips"]] == [20, 30, 5]
    assert [p["m_OriginalClip"] for p in tree["m_Clips"]] == [p["m_OriginalClip"] for p in before["m_Clips"]]
    assert tree["m_Controller"] == before["m_Controller"]
    owner.type.name = "MonoBehaviour"
    field = {"animationClips": [ptr(2), ptr(5)], "other": ptr(2)}
    assert ac.repoint(field, owner, copies, Writer())
    assert field == {"animationClips": [ptr(20), ptr(5)], "other": ptr(2)}


from test_model_arms import official, view


@official
def test_official_copies_leave_originals_and_other_consumers_unchanged_and_export_uses_copies(
    view, tmp_path, monkeypatch, record_property
):
    import time
    from bmpt.engine import Bundle, SaveOptions
    from bmpt.gameassets import chibi_adapt as ma, chibi_arm_frames as arf, chibi_arm_solve as ars, chibi_arms as ar
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.chibi import BundleGraph, _prefabs, _key, _Exporter, export_chibi
    from test_model_adapt import _slot

    files, index = view
    captured = {}
    retarget = ars.retarget

    def capture(*args, **kwargs):
        changes = retarget(*args, **kwargs)
        captured.update({k: (c.clip.get_raw_data(), c.clip.peek_name()) for k, c in changes.items()})
        return changes

    monkeypatch.setattr(ars, "retarget", capture)
    slot = _slot(files, "hina_original")
    sources = [p for p in files if p.name.startswith("character-ch0288-_mxload")]
    started = time.perf_counter()
    result = ma.adapt(
        index=index,
        resolver=None,
        target_files=slot,
        target_dev="hina_original",
        source_files=sources,
        source_dev="ch0288",
        open_writable=Bundle.load,
        physics=True,
    )
    arm_build_seconds = time.perf_counter() - started
    adapted = dict(index)
    paths = []
    for path, bundle in result.changed.items():
        dest = tmp_path / path.name
        ok, why = bundle.save(dest, SaveOptions(perform_crc=False, compression="lz4"))
        assert ok, why
        for cab in cab_names(dest):
            adapted[cab] = dest
        paths.append(dest)
    assert captured
    with BundleGraph(adapted) as g:
        prefabs = _prefabs(g, [p for p in paths if p.name.startswith("character-")])
        own = ma.own_prefabs(prefabs, "hina_original")
        skeleton = ma.Skeleton.of(g, prefabs[ma.choose_prefab(prefabs, "hina_original")].deref())
        played = ma.prefab_clips(g, skeleton, own)
        by_name = {c.peek_name(): c for c in played}
        for (cab, pid), (raw, name) in captured.items():
            original = g.objects[(cab, pid)]
            assert original.get_raw_data() == raw, name
            assert name in by_name and _key(by_name[name]) != (cab, pid), name
        for obj in list(g.objects.values()):
            if obj.type.name != "AnimatorOverrideController":
                continue
            for pair in g.tree(obj)["m_Clips"]:
                effective = pair["m_OverrideClip"] if pair["m_OverrideClip"]["m_PathID"] else pair["m_OriginalClip"]
                clip = g.deref(obj, effective)
                assert clip is None or _key(clip) not in captured, obj.peek_name()
        # The exporter traverses the same effective overrides as the preview.
        exporter = _Exporter(g, "hina_original")
        exported = exporter.controller_clips(own)
        assert {_key(c) for c in played} <= {_key(c) for c in exported}
        # Timelines / other controllers keep their original references. Only
        # corrected clip objects are added; pre-existing clips never disappear.
        original_names = {v[1] for v in captured.values()}
        assert {c.peek_name() for c in exported} >= original_names
    data = export_chibi(BundleGraph(adapted), [p for p in paths if p.name.startswith("character-")], "hina_original")
    assert data[:4] == b"glTF"
    monkeypatch.setattr(ars, "retarget", lambda *args, **kwargs: {})
    started = time.perf_counter()
    baseline = ma.adapt(
        index=index,
        resolver=None,
        target_files=slot,
        target_dev="hina_original",
        source_files=sources,
        source_dev="ch0288",
        open_writable=Bundle.load,
    )
    baseline_seconds = time.perf_counter() - started
    assert baseline.parts == result.parts
    record_property("arm_build_added_seconds", arm_build_seconds - baseline_seconds)
    record_property("arm_build_seconds", arm_build_seconds)
    record_property("baseline_build_seconds", baseline_seconds)


def test_synthetic_copy_and_repoint_keeps_original_clip_enemy_and_timeline_bytes(monkeypatch):
    import json
    from bmpt.gameassets import chibi_adapt as ma

    class Obj:
        def __init__(self, sf, pid, kind, tree):
            self.assets_file, self.path_id = sf, pid
            self.type, self.tree = SimpleNamespace(name=kind), copy.deepcopy(tree)
            self.data = b""
            sf.objects[pid] = self

        def read_typetree(self):
            return copy.deepcopy(self.tree)

        def save_typetree(self, tree):
            self.tree = copy.deepcopy(tree)
            self.data = json.dumps(tree, sort_keys=True).encode()

        def peek_name(self):
            return "Synthetic"

    sf = SimpleNamespace(name="CAB-slot", externals=[], objects={})
    original = Obj(sf, 1, "AnimationClip", _clip())
    ptr = lambda n: {"m_FileID": 0, "m_PathID": n}
    tree = {"m_Clips": [{"m_OriginalClip": ptr(1), "m_OverrideClip": ptr(1)}]}
    controller = Obj(sf, 2, "AnimatorOverrideController", tree)
    enemy = Obj(sf, 3, "AnimatorOverrideController", tree)
    timeline = Obj(sf, 4, "MonoBehaviour", {"m_Clip": ptr(1)})
    preload = Obj(
        sf,
        5,
        "AssetBundle",
        {
            "m_PreloadTable": [ptr(1)],
            "m_Container": [["slot.prefab", {"preloadIndex": 0, "preloadSize": 1, "asset": ptr(2)}]],
        },
    )
    before = {o.path_id: json.dumps(o.tree, sort_keys=True) for o in (original, enemy, timeline)}

    class Writer:
        files = {"cab-slot": (None, sf)}

        def file(self, cab):
            return sf

        def add_like(self, dest, source, tree):
            obj = Obj(dest, 10, "AnimationClip", tree)
            obj.save_typetree(tree)
            return 10

        def external(self, dest, cab):
            assert cab == "cab-slot"
            return 0

    edited = copy.deepcopy(original.tree)
    rotations = _rotations()
    ac.encode_rotations(edited, rotations)
    change = SimpleNamespace(clip=original, tree=edited, rotations=rotations)
    patch_everywhere(monkeypatch, [ma], "saved_tree", lambda obj: copy.deepcopy(obj.tree))
    copies = ac.write_copies(Writer(), {("cab-slot", 1): change}, {("cab-slot", 1): "cab-slot"}, [controller], None, {})
    assert copies == {("cab-slot", 1): ("cab-slot", 10)}
    assert controller.tree["m_Clips"][0]["m_OverrideClip"] == ptr(10)
    assert {o.path_id: json.dumps(o.tree, sort_keys=True) for o in (original, enemy, timeline)} == before
    assert preload.tree["m_PreloadTable"] == [ptr(1), ptr(10)]
    assert ac.edit_error(original.tree, sf.objects[10].tree, rotations) is None


def test_duplicate_bindings_and_keys_keep_their_original_order():
    before = _clip()
    data = before["m_MuscleClip"]["m_Clip"]["data"]
    before["m_ClipBindingConstant"]["genericBindings"].append(_binding(50, 99, 114))
    data["m_ConstantClip"]["data"].append(0.75)
    muscle = before["m_MuscleClip"]
    muscle["m_ValueArrayDelta"].append({"m_Start": 0.75, "m_Stop": 0.75})
    muscle["m_ValueArrayReferencePose"].append(0.75)
    frames = read_streamed(data["m_StreamedClip"]["data"])
    frames.insert(2, (0.0, [(0, 0.0, 0.0, 0.0, -5.0)]))
    data["m_StreamedClip"]["data"] = ac.encode_streamed(frames)
    before["m_MuscleClipSize"] = ms.muscle_size(before)
    after = copy.deepcopy(before)
    ac.encode_rotations(after, _rotations())
    assert ac.edit_error(before, after, _rotations()) is None


@official
def test_official_repeated_binding_readback_remains_value_exact(view):
    import zlib
    from bmpt.gameassets import chibi_adapt as ma
    from bmpt.gameassets.chibi import BundleGraph
    from test_model_adapt import _slot

    files, index = view
    with BundleGraph(index) as graph:
        prefabs = ma._chibi_prefabs(graph, [p for p in _slot(files, "ch0266") if p.name.startswith("character-")])
        sk = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, "ch0266")].deref())
        clips = ma.prefab_clips(graph, sk, ma.own_prefabs(prefabs, "ch0266"))
        clip = ma.pick(clips, ["Move_End_Kneel"])[0]
        before = graph.tree(clip)
        times, pose, _ = sk.clip_frames(before)
        poses = [pose(f) for f in range(len(times))]
        rotations = {}
        for side in "LR":
            for name in ("UpperArm", "Forearm", "Hand"):
                bone = sk.first(f"Bip001 {side} {name}")
                parent = sk.nodes[bone].parent
                rotations[zlib.crc32(sk.nodes[bone].path.encode())] = ms.continuous(
                    np.array([ms.quaternion(ma._rotation(np.linalg.inv(w[parent]) @ w[bone])) for w in poses])
                )
        after = copy.deepcopy(before)
        ac.encode_rotations(after, rotations)
        assert ac.edit_error(before, after, rotations) is None


@official
def test_official_ungated_pair_build_is_byte_identical_to_the_previous_path(view, monkeypatch, record_property):
    import hashlib
    import time
    from bmpt.engine import Bundle
    from bmpt.gameassets import chibi_adapt as ma, chibi_arm_frames as arf, chibi_arm_solve as ars, chibi_arms as ar
    from test_model_adapt import _slot

    files, index = view
    arguments = dict(
        index=index,
        resolver=None,
        target_files=_slot(files, "ch0288"),
        target_dev="ch0288",
        source_files=[p for p in files if p.name.startswith("character-hina_original-_mxload")],
        source_dev="hina_original",
        open_writable=Bundle.load,
    )
    started = time.perf_counter()
    enabled = ma.adapt(**arguments)
    enabled_seconds = time.perf_counter() - started
    before = {p.name: hashlib.sha256(b.env.file.save(packer="none")).hexdigest() for p, b in enabled.changed.items()}
    monkeypatch.setattr(ars, "retarget", lambda *a, **k: {})
    started = time.perf_counter()
    disabled = ma.adapt(**arguments)
    disabled_seconds = time.perf_counter() - started
    after = {p.name: hashlib.sha256(b.env.file.save(packer="none")).hexdigest() for p, b in disabled.changed.items()}
    assert before == after
    record_property("ungated_build_added_seconds", enabled_seconds - disabled_seconds)


def _stored_curves_reference(tree):
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    out = {}
    for t, keys in read_streamed(data["m_StreamedClip"]["data"]):
        for index, *coefficients in keys:
            out.setdefault(index, []).append((t, *coefficients))
    return {k: np.array(v) for k, v in out.items()}


def test_array_read_stored_curves_equal_the_key_by_key_reader():
    tree = _clip()
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    frames = read_streamed(data["m_StreamedClip"]["data"])
    frames.insert(2, (0.0, [(3, 0.0, float("nan"), -0.0, -5.0), (0, 1e-30, 0.0, 0.0, 2.0)]))  # repeated keys, NaN, -0
    frames.insert(4, (0.5, [(6, 3.5, 0.0, 0.0, 1.0)]))
    data["m_StreamedClip"]["data"] = ac.encode_streamed(frames)
    got = ac.stored_curves(tree)
    want = _stored_curves_reference(tree)
    for index, rows in want.items():
        assert got[index].dtype == rows.dtype and got[index].shape == rows.shape
        assert got[index].tobytes() == rows.tobytes()
    ns = data["m_StreamedClip"]["curveCount"]
    assert set(got) == set(want) | set(range(ns, ns + 6 + 5))
    data["m_StreamedClip"]["data"] = []
    assert set(ac.stored_curves(tree)) == set(range(ns, ns + 6 + 5))


def _sample_curves_reference(tree, wanted, *, hold_before=False):
    """The frame-by-frame streamed reader the array reader replaced; dense and constant curves as before."""
    import struct

    times = np.array(ac.clip_times(tree))
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    streamed = data["m_StreamedClip"]
    raw = np.asarray(streamed["data"], dtype="<u4").tobytes()
    pos = 0
    dtype = np.dtype([("index", "<i4"), ("coefs", "<f4", (4,))])
    selected = np.zeros(streamed["curveCount"], bool)
    for k in wanted:
        if k < len(selected):
            selected[k] = True
    blocks, key_times = [], []
    while pos + 8 <= len(raw):
        t, count = struct.unpack_from("<fi", raw, pos)
        pos += 8
        if -1e30 < t < 1e30:
            rows = np.frombuffer(raw, dtype, count, pos)
            keep = selected[rows["index"]]
            blocks.append(rows[keep])
            key_times.append(np.full(keep.sum(), t))
        pos += count * 20
    curves = {}
    if blocks:
        rows = np.concatenate(blocks)
        key_at = np.concatenate(key_times)
        order = np.argsort(rows["index"], kind="stable")
        rows, key_at = rows[order], key_at[order]
        cuts = np.r_[0, np.flatnonzero(np.diff(rows["index"])) + 1, len(rows)]
        for begin, end in zip(cuts[:-1], cuts[1:]):
            if end == begin:
                continue
            ks = key_at[begin:end]
            at = np.maximum(0, np.searchsorted(ks, times + 1e-6, side="right") - 1)
            chosen = rows["coefs"][begin:end][at].astype(float)
            dt = np.maximum(0, times - ks[at])
            values = ((chosen[:, 0] * dt + chosen[:, 1]) * dt + chosen[:, 2]) * dt + chosen[:, 3]
            if hold_before:
                values = np.where(times < ks[at], chosen[:, 3], values)
            curves[int(rows["index"][begin])] = values
    return times, curves


def test_array_streamed_reader_samples_exactly_like_the_frame_by_frame_reader():
    tree = _clip()
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    frames = read_streamed(data["m_StreamedClip"]["data"])
    frames.insert(2, (0.0, [(3, 0.0, float("nan"), -0.0, -5.0), (0, 1e-30, 0.0, 0.0, 2.0)]))
    frames.insert(3, (0.25, []))
    frames.insert(4, (0.5, [(6, 3.5, -1.0, 0.0, 1.0)]))
    frames.insert(5, (float("nan"), [(1, 1.0, 1.0, 1.0, 1.0)]))
    data["m_StreamedClip"]["data"] = ac.encode_streamed(frames)
    for wanted in (range(7), {0, 3}, {6, 40}, set(), range(30)):
        for hold in (False, True):
            got = ac.sample_curves(tree, wanted, hold_before=hold)
            want = _sample_curves_reference(tree, wanted, hold_before=hold)
            assert np.array_equal(got[0], want[0])
            streamed = {k: v for k, v in got[1].items() if k < 7}
            assert set(streamed) == set(want[1])
            for k, values in want[1].items():
                assert streamed[k].tobytes() == values.tobytes()
    data["m_StreamedClip"]["data"] = ac.encode_streamed([(-float("inf"), []), (float("inf"), [])])
    assert not {k: v for k, v in ac.sample_curves(tree, range(7))[1].items() if k < 7}
    data["m_StreamedClip"]["data"] = data["m_StreamedClip"]["data"][:-1] + [5]
    with pytest.raises(ValueError):
        ac.sample_curves(tree, range(7))
