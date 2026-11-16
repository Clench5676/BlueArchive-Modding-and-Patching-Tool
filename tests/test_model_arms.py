"""Core clearance, contact constraints and official costume retarget regressions."""
import copy
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import pytest

from bmpt.gameassets import model_adapt as ma, model_arms as ar
from bmpt.gameassets.model import BundleGraph, _key
from test_model_adapt import GAME_DIR, official, _index, _slot, _skeleton


def _rig():
    bones = [("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)),
             ("Bip001 Pelvis", 1, (0, .5, 0), (0, 0, 0, 1)),
             ("Bip001 Spine1", 2, (0, .3, 0), (0, 0, 0, 1)),
             ("Bip001 Neck", 3, (0, .3, 0), (0, 0, 0, 1)),
             ("Bip001 Head", 4, (0, .1, 0), (0, 0, 0, 1))]
    for s, sign in (("L", 1), ("R", -1)):
        upper = len(bones) + 1
        bones += [(f"Bip001 {s} UpperArm", 3, (sign * .1, .2, 0), (0, 0, 0, 1)),
                  (f"Bip001 {s} Forearm", upper, (sign * .1, -.1, .02), (0, 0, 0, 1)),
                  (f"Bip001 {s} Hand", upper + 1, (-sign * .1, -.1, .05), (0, 0, 0, 1)),
                  (f"Bip001 {s} Finger0", upper + 2, (-sign * .02, 0, .02), (0, 0, 0, 1))]
    return ar.Rig(_skeleton(bones))


def _torso(radius, loose=False):
    # A closed box and one triangle for each arm region. Bone-axis samples
    # inside the torso measure the outer radial wall, independent of winding.
    vertices = np.array([(x, y, z) for x in (-radius, radius) for y in (.3, 1.3) for z in (-radius, radius)])
    triangles = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
                          (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)])
    return ar.Drawn(vertices, np.zeros((8, 1), int), np.ones((8, 1)), triangles, np.zeros(8, int),
                    lambda pose: np.eye(4)[None], np.full(8, loose))


def test_measure_core_and_need_on_a_box_ignores_secondary_cloth():
    rig = _rig()
    w = rig.rest
    pts = rig.samples(w)
    own, new = _torso(.11), _torso(.15)
    a = ar.measure(own, rig, w, other=False, core=True)
    b = ar.measure(new, rig, w, other=False, core=True)
    need = ar.push_of(a[0], b[0], np.full((2, 9), .04), .5)
    assert need[:, 6].min() > .035
    assert np.allclose(ar.push_of(b[0], a[0], b[1], .5), 0)
    loose = ar.measure(_torso(.15, loose=True), rig, w, other=False, core=True)
    assert np.allclose(loose[0], -loose[2])
    assert np.allclose(ar.push_of(np.array([-.08, .02]), np.array([.01, .03]), np.array([.04, .04]), .5), [.03, .01])
    axis, directions, rho = ar.radial(rig.axis(w), pts.reshape(-1, 3))
    assert np.allclose(np.linalg.norm(directions, axis=1), 1)
    assert np.allclose(np.linalg.norm(pts.reshape(-1, 3) - axis, axis=1), rho)


def test_gate_has_a_named_fifth_of_frames_threshold_and_a_strict_depth_threshold():
    need = np.zeros((10, 2, 9))
    need[0, 0, 4] = .03
    assert not ar.gated(need)
    need[1, 0, 4] = .020
    assert not ar.gated(need)
    need[1, 0, 4] = .020001
    assert ar.gated(need)
    assert ar.GATE_FRACTION == .2
    # Upper-arm-only differences cannot start a lower-arm solve.
    need[:] = 0
    need[:, :, :2] = .08
    assert not ar.gated(need)


def test_weapon_contact_requires_shown_near_and_still_in_mount_space():
    relative = np.zeros((25, 2, 3))
    dist = np.full((25, 2), .049)
    assert ar.weapon_holds(relative, dist).all()
    dist[:, 1] = .051
    assert not ar.weapon_holds(relative, dist)[:, 1].any()
    relative[12, 0, 0] = .02
    holds = ar.weapon_holds(relative, dist)
    assert not holds[7:18, 0].any()
    assert holds[:7, 0].all()
    relative[:] = np.nan  # hidden weapon
    assert not ar.weapon_holds(relative, dist).any()


def test_head_contact_blends_only_the_head_bones_own_surface():
    rig = _rig()
    wrist = rig.samples(rig.rest)[0, 6]
    class Own:
        @staticmethod
        def posed(w, rows=None):
            vertices = np.array([wrist + [.02, 0, 0], wrist + [0, 0, .5]])
            return vertices if rows is None else vertices[rows]
    pins = ar.head_pins(rig, Own(), [rig.rest] * 20, np.array([0]), True)
    assert np.allclose(pins[:, 0], 1)
    assert np.allclose(ar.head_pins(rig, Own(), [rig.rest] * 20, np.array([1]), False), 0)


def test_solver_keeps_lengths_hand_orientation_descendant_locals_and_weapon_holds():
    rig = _rig()
    poses = [rig.rest] * 12
    need = np.full((12, 2, 9), .025)
    hold = np.zeros((12, 2), bool)
    hold[:, 1] = True
    delta = ar.solve_many(rig, poses, need, hold, np.zeros((12, 2)), (0, 0))
    fixed = ar._repose(rig, poses, delta, hold)
    for w in fixed:
        for s in range(2):
            u, e, h = rig.joints(w, s)
            u0, e0, h0 = rig.joints(rig.rest, s)
            assert np.linalg.norm(e - u) == pytest.approx(np.linalg.norm(e0 - u0), abs=1e-9)
            assert np.linalg.norm(h - e) == pytest.approx(np.linalg.norm(h0 - e0), abs=1e-9)
            assert np.allclose(w[rig.arm[s][3]][:3, :3], rig.rest[rig.arm[s][3]][:3, :3])
        for bone in ar.Arms(rig).sub[1]:
            assert np.array_equal(w[bone], rig.rest[bone])
        hand = rig.arm[0][3]
        finger = hand + 1
        assert np.allclose(np.linalg.inv(w[hand]) @ w[finger], np.linalg.inv(rig.rest[hand]) @ rig.rest[finger])
    assert np.linalg.norm(delta[:, 0]).max() > .001


def test_blend_wraps_and_rate_limit_respects_contacts_and_need_caps():
    values = np.zeros((30, 2, 9))
    values[0] = .09
    assert ar.blend(values, True)[-1].min() > 0
    assert ar.blend(values, False)[-1].max() == 0
    hold = np.zeros((30, 2), bool)
    hold[15] = True
    before = ar.blend(values, True)
    out = ar.rate_limit(before, np.array([.005, .01]), hold, True)
    assert (out <= before).all() and (out >= 0).all()
    assert (out[hold] == 0).all()
    delta = np.abs(np.roll(out, 1, axis=0) - out)
    assert (delta <= np.array([.0015, .003])[None, :, None] + 1e-9).all()


def _open_pair(g, files, target_dev, source_dev):
    from bmpt.gameassets.model import model_root
    target_files = _slot(files, target_dev)
    prefabs = ma._model_prefabs(g, [f for f in target_files if f.name.startswith("character-")])
    sk = ma.Skeleton.of(g, prefabs[ma.choose_prefab(prefabs, target_dev)].deref())
    clips = ma.prefab_clips(g, sk, ma.own_prefabs(prefabs, target_dev))
    def costume(skeleton, cs):
        idle = next(iter(skeleton.poses(ma.pick(cs, ma.IDLE_CLIPS)[:1], frames=1)), None)
        parts = ma.roles(skeleton, idle)
        selected = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]
        return ma.Costume(skeleton, selected, [ma.skin_of(p.mesh) for p in selected]), parts
    target, parts = costume(sk, clips)
    source_files = [p for p in files if re.match(rf"(character-{re.escape(source_dev)}-_mxload|assets-_mx-npcs-{re.escape(source_dev)}-)", p.name)]
    if not source_files:
        source_files = [p for p in files if p.name.startswith("field-character-_mxload")]
    from bmpt.gameassets.model import AssetNotFound
    try:
        s_root, _, _ = model_root(g, source_files, source_dev)
    except AssetNotFound:
        raise ma.refuse("no prefab", dev=source_dev) from None
    ss = ma.Skeleton.of(g, s_root)
    if not ss.renderers:
        raise ma.refuse("static model", dev=source_dev)
    sp = ma._model_prefabs(g, source_files)
    sc = ma.prefab_clips(g, ss, ma.own_prefabs(sp, source_dev))
    source, _ = costume(ss, sc)
    plan = ma.plan(target, source, ma.probe_poses(sk, clips), fit=True, own_probes=ma.probe_poses(ss, sc), clips=clips)
    return target, source, plan, clips, parts


@pytest.fixture(scope="module")
def view():
    files = list(GAME_DIR.rglob("*.bundle"))
    return files, _index(files)


def _metrics(target, source, plan, change):
    rig = ar.Rig(target.skeleton)
    own, new = ar.own_drawn(target), ar.adapted_drawn(target, source, plan)
    times, pose, _ = rig.sk.clip_frames(rig.sk.graph.tree(change.clip))
    _, fixed, _ = rig.sk.clip_frames(change.tree)
    before, after, moved, inward, penetration = [], [], [], [], []
    for f in range(len(times)):
        w0, w1 = pose(f), fixed(f)
        p0, p1 = rig.samples(w0), rig.samples(w1)
        direction = ar.radial(rig.axis(w0), p0.reshape(-1, 3))[1].reshape(2, 9, 3)
        travel = ((p1 - p0) * direction).sum(axis=2)
        d = ar.measure(own, rig, w0, other=False, core=True)[0]
        b, radius, _ = ar.measure(new, rig, w0, other=False, core=True)
        if f in (0, len(times) // 2, len(times) - 1):
            assert np.allclose(ar.arm_radii(new, rig, w0), radius, atol=1e-6)
        a = ar.measure(new, rig, w1, other=False, core=True)[0]
        growth = np.maximum(a, 0) - np.maximum(b, 0)
        penetration.append(growth)
        inward.append(np.where(growth > ar.GUARD_TOLERANCE + 1e-7, travel, 0).min(axis=1))
        assert growth.max() <= ar.GUARD_TOLERANCE + 1e-7
        before.append(ar.push_of(d, b, radius, .5)[:, 2:])
        after.append(ar.push_of(d, a, radius, .5)[:, 2:])
        moved.append([np.linalg.norm(rig.joints(w1, s)[2] - rig.joints(w0, s)[2]) for s in range(2)])
    original = [pose(f) for f in range(len(times))]
    corrected = [fixed(f) for f in range(len(times))]
    loop = bool(change.tree["m_MuscleClip"].get("m_LoopTime"))
    peak0, peak1 = ar.wrist_peak(rig, original, loop), ar.wrist_peak(rig, corrected, loop)
    assert (peak1 <= ar.WRIST_SPEED_RATIO * peak0 + 2e-6).all(), (change.clip.peek_name(), peak0, peak1)
    return {"before_mm": (np.mean(before, axis=(0, 2)) * 1000).tolist(),
            "after_mm": (np.mean(after, axis=(0, 2)) * 1000).tolist(),
            "wrist_mm": (np.mean(moved, axis=0) * 1000).tolist(),
            "gate": float(np.mean(np.max(before, axis=(1, 2)) > ar.GATE_DEPTH)),
            "inward_mm": (np.min(inward, axis=0) * 1000).tolist(),
            "penetration_growth_mm": (np.max(penetration, axis=(0, 2)) * 1000).tolist(),
            "held": change.held.sum(axis=0).tolist(), "head_pins": (change.pins > .5).sum(axis=0).tolist()}


@official
def test_official_hina_aoba_clearance_and_unchanged_weapon_idle(view, record_property):
    files, index = view
    with BundleGraph(index) as g:
        target, source, plan, clips, parts = _open_pair(g, files, "hina_original", "ch0288")
        started = time.perf_counter()
        steps = []
        changes = ar.retarget(target, source, plan, clips, parts, step=lambda: steps.append(time.perf_counter()))
        record_property("arm_seconds", time.perf_counter() - started)
        assert steps, "long arm work must yield to the build step callback"
        record_property("arm_step_max_gap", max(np.diff([started, *steps, time.perf_counter()])))
        record_property("arm_corrected_clips", json.dumps([c.clip.peek_name() for c in changes.values()]))
        report = {}
        reference = {"Formation_Idle": [.30345, 2.73653], "Cafe_Idle": [.39311, 1.31697],
                     "Victory_End": [.11174, 2.10050]}
        for suffix in ("Formation_Idle", "Cafe_Idle", "Victory_End"):
            clip = ma.pick(clips, [suffix])[0]
            assert _key(clip) in changes, (suffix, [c.clip.peek_name() for c in changes.values()])
            change = changes[_key(clip)]
            report[suffix] = _metrics(target, source, plan, change)
            assert max(report[suffix]["after_mm"]) < 5, report[suffix]
            assert np.max(np.abs(np.array(report[suffix]["after_mm"]) - reference[suffix])) < 1., report[suffix]
        idle = ma.pick(clips, ["Normal_Idle"])[0]
        assert _key(idle) not in changes
        tree = g.tree(idle)
        rig, own = ar.Rig(target.skeleton), ar.own_drawn(target)
        times, pose, _ = rig.sk.clip_frames(tree)
        poses = [pose(f) for f in range(len(times))]
        weapon_parts = [p for p in parts if p.role == "weapon" and p.mesh is not None]
        weapons = [ar.own_drawn(ma.Costume(rig.sk, [p], [ma.skin_of(p.mesh)])) for p in weapon_parts]
        shown = ar.weapon_visibility(rig, tree, weapon_parts, poses)
        hold, pins = ar.contacts(rig, own, weapons, poses, ar.head_core(target), bool(tree["m_MuscleClip"]["m_LoopTime"]), shown)
        assert hold.all()
        report["Normal_Idle"] = _metrics(target, source, plan, ar.ArmClip(idle, tree, {}, 0., hold, pins))
        record_property("arm_metrics", json.dumps(report))
        print(json.dumps(report, indent=2), flush=True)


@official
@pytest.mark.parametrize("host,costume,only", [("ch0288", "hina_original", None),
                                               ("ch0184", "yuuka_original", "Cafe_Idle")])
def test_official_fine_direction_and_secondary_coat_unchanged(view, host, costume, only, record_property):
    files, index = view
    with BundleGraph(index) as g:
        target, source, plan, clips, parts = _open_pair(g, files, host, costume)
        if only:
            clips = ma.pick(clips, [only])
        before = {ma._key(c): c.get_raw_data() for c in clips}
        started = time.perf_counter()
        assert not ar.retarget(target, source, plan, clips, parts)
        record_property("arm_seconds", time.perf_counter() - started)
        assert {ma._key(c): c.get_raw_data() for c in clips} == before
        started = time.perf_counter()
        assert not ar.retarget(target, source, plan, clips, parts)
        record_property("arm_warm_seconds", time.perf_counter() - started)


_census_state = {}


def _census_init(files, index):
    _census_state.update(files=files, index=index)


def _census_pair(pair):
    from bmpt.errors import ServiceError
    host, costume = pair
    row = {"pair": pair}
    started = time.perf_counter()
    try:
        with BundleGraph(_census_state["index"]) as g:
            target, source, plan, clips, parts = _open_pair(g, _census_state["files"], host, costume)
            raw = {ma._key(c): c.get_raw_data() for c in clips}
            changes = ar.retarget(target, source, plan, clips, parts)
            row.update(changed=bool(changes), clips=len(changes), seconds=time.perf_counter() - started)
            if raw != {ma._key(c): c.get_raw_data() for c in clips}:
                row["error"] = "original clip bytes changed"
            rig = ar.Rig(target.skeleton)
            own, new = ar.own_drawn(target), ar.adapted_drawn(target, source, plan)
            inward, worsening, residual = 0., 0., 0.
            for key, change in changes.items():
                times, orig, _ = rig.sk.clip_frames(g.tree(change.clip))
                _, fixed, _ = rig.sk.clip_frames(change.tree)
                loop = bool(change.tree["m_MuscleClip"].get("m_LoopTime"))
                w0 = [orig(f) for f in range(len(times))]
                w1 = [fixed(f) for f in range(len(times))]
                assert np.isfinite(np.asarray(w1)).all(), change.clip.peek_name()
                peak0, peak1 = ar.wrist_peak(rig, w0, loop), ar.wrist_peak(rig, w1, loop)
                if (peak1 > ar.WRIST_SPEED_RATIO * peak0 + 2e-6).any():
                    row["error"] = f"wrist speed: {change.clip.peek_name()} {peak0} -> {peak1}"
                for f in range(len(times)):
                    p0, p1 = rig.samples(w0[f]), rig.samples(w1[f])
                    radial = ar.radial(rig.axis(w0[f]), p0.reshape(-1, 3))[1].reshape(2, 9, 3)
                    free = ~change.held[f]
                    if change.held[f].any():
                        assert np.max(np.abs(p1[change.held[f]] - p0[change.held[f]])) < 2e-6
                    d = ar.measure(own, rig, w0[f], other=False, core=True, radii=False)[0]
                    b, radius, _ = ar.measure(new, rig, w0[f], other=False, core=True)
                    a = ar.measure(new, rig, w1[f], other=False, core=True, radii=False)[0]
                    assert np.isfinite(a).all(), (change.clip.peek_name(), f)
                    before = ar.push_of(d, b, radius, .5)[free, 2:]
                    after = ar.push_of(d, a, radius, .5)[free, 2:]
                    growth = np.maximum(a, 0) - np.maximum(b, 0)
                    inward = min(inward, ((p1 - p0) * radial).sum(axis=2)[growth > ar.GUARD_TOLERANCE + 1e-7].min(initial=0))
                    worsening = max(worsening, growth.max(initial=0))
                    residual = max(residual, after.max(initial=0))
            row.update(inward_mm=inward * 1000, worsening_mm=worsening * 1000, residual_mm=residual * 1000)
    except ServiceError as error:
        row.update(refused=error.params.get("source"), detail=error.params)
    except Exception as error:
        import traceback
        row.update(error=f"{type(error).__name__}: {error}", trace=traceback.format_exc()[-1000:])
    return row


@official
def test_official_same_family_arm_census(view, tmp_path, record_property):
    """All frames/nine samples: only growth of max(D, 0) is a regression.

    Exterior clearance changes and radial travel without penetration growth
    are allowed. One millimetre is the guard tolerance (plus 0.1 micrometre
    for the independent float32 triangle-reader comparison).
    """
    from concurrent.futures import ProcessPoolExecutor
    from test_costume_census import _open, _jobs, _state
    _open(str(GAME_DIR), str(tmp_path))
    catalog = _state["catalog"]
    pairs = [(catalog.variant(a).model, catalog.variant(b).model or catalog.variant(b).field_model)
             for kind, a, b in _jobs() if kind == "pair"]
    files, index = view
    with ProcessPoolExecutor(3, initializer=_census_init, initargs=(files, index)) as pool:
        rows = []
        for row in pool.map(_census_pair, pairs, chunksize=1):
            rows.append(row)
            if len(rows) % 20 == 0:
                print(f"arm census {len(rows)}/{len(pairs)}", flush=True)
    (tmp_path / "arm-census.json").write_text(json.dumps(rows, indent=2))
    errors = [r for r in rows if r.get("error") or r.get("refused") not in (None, "static model", "no prefab")]
    inward = [r for r in rows if r.get("inward_mm", 0) < -1.0001]
    worsening = [r for r in rows if r.get("worsening_mm", 0) > 1.0001]
    measured = [r for r in rows if "changed" in r]
    summary = {"pairs": len(rows), "changed": sum(r["changed"] for r in measured),
               "unchanged": sum(not r["changed"] for r in measured), "refused": len(rows) - len(measured),
               "inward": inward, "worsening": worsening, "errors": errors, "tolerance_mm": 1.0}
    record_property("arm_census", json.dumps(summary))
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in summary.items()}), flush=True)
    assert not errors, errors
    assert not inward, inward
    assert not worsening, worsening


def test_head_core_excludes_hair_children_and_weapon_visibility_reads_switches():
    from types import SimpleNamespace
    from test_model_arm_clips import _clip, _binding
    import zlib
    rig = _rig()
    head = rig.head
    hair = len(rig.sk.nodes)
    rig.sk.nodes.append(ma.Node("Hair01", rig.sk.nodes[head].path + "/Hair01", head,
                                ((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), None, None, True))
    costume = SimpleNamespace(skeleton=rig.sk, parts=[SimpleNamespace(bones=[head, hair])],
                              skins=[SimpleNamespace(vertices=np.zeros((2, 3)), joints=np.array([[0], [1]]),
                                                     weights=np.ones((2, 1)))])
    assert ar.head_core(costume).tolist() == [0]
    part = SimpleNamespace(node=rig.arm[0][3], renderer={"m_Enabled": True})
    tree = _clip()
    data = tree["m_MuscleClip"]["m_Clip"]["data"]
    b = _binding(zlib.crc32(rig.sk.nodes[part.node].path.encode()), zlib.crc32(b"m_Enabled"), 137)
    tree["m_ClipBindingConstant"]["genericBindings"].append(b)
    data["m_ConstantClip"]["data"].append(0.)
    assert not ar.weapon_visibility(rig, tree, [part], [rig.rest] * 4).any()
    data["m_ConstantClip"]["data"][-1] = 1.
    assert ar.weapon_visibility(rig, tree, [part], [rig.rest] * 4).all()


def test_full_head_pin_is_a_fixed_wrist_during_temporal_projection():
    rig = _rig()
    poses = [rig.rest] * 12
    need = np.full((12, 2, 9), .02)
    hold = np.zeros((12, 2), bool)
    pins = np.ones((12, 2))
    delta = ar.solve_many(rig, poses, need, hold, pins, (0, 0))
    delta = ar.limit_motion(delta, np.array([.01, .01]), hold, True, pins)
    fixed = ar._repose(rig, poses, delta, hold)
    for w in fixed:
        for side in range(2):
            assert np.allclose(rig.joints(w, side)[2], rig.joints(rig.rest, side)[2], atol=1e-7)


def test_own_depth_cache_reuses_frames_and_invalidates_clips_bindposes_and_rig(monkeypatch):
    from collections import OrderedDict
    from types import SimpleNamespace
    rig = _rig()
    class Bytes:
        data = b"first"
        def get_raw_data(self):
            return self.data
    mesh, clip = Bytes(), Bytes()
    target = SimpleNamespace(parts=[SimpleNamespace(mesh=mesh)])
    calls = []
    class Frame:
        def __init__(self, own, rig, pose, indices=None):
            pass
        def depth(self, pose, indices=None):
            calls.append(pose)
            return np.full((2, len(indices) if indices is not None else 9), float(pose))
    monkeypatch.setattr(ar, "BodyFrame", Frame)
    monkeypatch.setattr(ar, "_own_cache", OrderedDict())
    poses = list(range(10))
    assert ar._own_depths(target, rig, None, clip, poses, [0, 3]).shape == (2, 2, 9)
    assert calls == [0, 3]
    ar._own_depths(target, rig, None, clip, poses, [0, 3, 6])
    assert calls == [0, 3, 6]
    mesh.data = b"other bindposes"
    ar._own_depths(target, rig, None, clip, poses, [0, 3])
    assert calls[-2:] == [0, 3] and len(calls) == 5
    clip.data = b"other curves"
    ar._own_depths(target, rig, None, clip, poses, [0, 3])
    assert len(calls) == 7
    rig.rest[0] = rig.rest[0].copy()
    rig.rest[0][0, 3] += 1
    ar._own_depths(target, rig, None, clip, poses, [0, 3])
    assert len(calls) == 9





def test_vectorized_clip_sampler_matches_existing_streamed_dense_constant_and_euler_reader():
    import zlib
    from test_model_arm_clips import _clip
    rig = _rig()
    tree = _clip()
    bindings = tree['m_ClipBindingConstant']['genericBindings']
    nodes = [rig.spine[1], rig.arm[0][1], rig.arm[0][2], rig.arm[1][1], rig.arm[1][3]]
    for binding, node in zip(bindings, nodes):
        binding['path'] = zlib.crc32(rig.sk.nodes[node].path.encode())
    times, pose, _ = rig.sk.clip_frames(tree)
    fast = ar.ClipFrames(rig.sk, tree)
    assert np.array_equal(fast.times, times)
    worlds = fast.world(range(len(times)))
    assert np.allclose(worlds, [pose(f) for f in range(len(times))], atol=1e-12, rtol=0)
    partial = ar.ClipFrames(rig.sk, tree, {rig.arm[1][3]})
    assert np.allclose(partial.world(range(len(times)))[:, rig.arm[1][3]], worlds[:, rig.arm[1][3]], atol=1e-12, rtol=0)


def test_guard_rolls_back_penetration_but_allows_exterior_clearance_changes():
    rig = _rig()
    poses = [rig.rest] * 12
    hold = np.zeros((12, 2), bool)
    deltas = np.zeros((12, 2, 2, 3))
    deltas[:, :, 1, 2] = -.03
    torso = _torso(.15)
    before = np.maximum(0, ar.measure(torso, rig, poses[0], other=False, core=True)[0])
    fixed = ar.guard_clip(rig, torso, poses, deltas, hold, True, np.zeros(2))
    for pose in fixed:
        after = np.maximum(0, ar.measure(torso, rig, pose, other=False, core=True)[0])
        assert (after - before).max() <= .001
    thin = _torso(.01)
    exterior = ar.guard_clip(rig, thin, poses, deltas, hold, True, np.zeros(2))
    assert max(np.linalg.norm(rig.samples(w) - rig.samples(rig.rest), axis=2).max() for w in exterior) > .005
    assert all((ar.measure(thin, rig, w, other=False, core=True)[0] <= 0).all() for w in exterior)


def test_cached_torso_depth_and_gradient_match_triangle_measure():
    rig = _rig()
    torso = _torso(.15)
    frame = ar.BodyFrame(torso, rig, rig.rest)
    assert np.allclose(frame.depth(rig.rest), ar.measure(torso, rig, rig.rest, other=False, core=True)[0], atol=1e-6)
    depth, gradient = frame.depth_gradient(rig.rest)
    assert np.isfinite(gradient).all()
    assert np.allclose(depth, frame.depth(rig.rest), atol=1e-6)
    # Rigid torso, translated arm bones: cached geometry still measures exactly.
    delta = np.zeros((1, 2, 2, 3))
    delta[:, :, 1, 2] = .01
    changed = ar._repose(rig, [rig.rest], delta, np.zeros((1, 2), bool))[0]
    assert np.allclose(frame.depth(changed), ar.measure(torso, rig, changed, other=False, core=True)[0], atol=1e-6)
    assert np.allclose(frame.penetration(changed), np.maximum(0, frame.depth(changed)), atol=1e-6)


def test_spatial_leaves_and_sparse_ray_pairs_match_all_triangle_intersections():
    rng = np.random.default_rng(884)
    triangles = rng.uniform(-.2, .2, (997, 3, 3)).astype(np.float32)
    points = rng.uniform(-.25, .25, (18, 3))
    dirs = rng.normal(size=points.shape)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    rho = rng.uniform(.01, .35, len(points))
    index = ar.TriangleIndex(triangles)
    # Keep the partition after a deformation; posed boxes must still be exact.
    for turn in (triangles, triangles + rng.normal(0, .04, triangles.shape)):
        prepared = ar.BodyFrame.prepare(turn)
        expected = ar.outer(ar.cast(points, dirs, turn), -rho, ar.CAP, -rho)
        prepared.append(index.bounds(prepared))
        depth, normal = ar.BodyFrame.query(points, dirs, rho, prepared, normals=True)
        assert np.allclose(depth, expected, atol=1e-6)
        assert np.isfinite(normal).all()
        penetration = ar.BodyFrame.query(points, dirs, np.zeros(len(points)), prepared)
        assert np.allclose(penetration, np.maximum(depth, 0), atol=1e-6)


def test_gate_exits_on_far_poses_without_measure_or_host_cache(monkeypatch):
    from types import SimpleNamespace
    rig = _rig()
    sampler = SimpleNamespace(times=np.arange(30), world=lambda frames: [rig.rest for f in frames])
    monkeypatch.setattr(ar, '_own_depths', lambda *a, **k: pytest.fail('far clip should not measure own depths'))
    assert not ar.clip_gate(None, rig, None, _torso(.01), None, sampler)[0]


def test_gate_stops_measuring_when_the_required_fifth_is_already_proven(monkeypatch):
    from types import SimpleNamespace
    rig = _rig()
    sampler = SimpleNamespace(times=np.arange(90), world=lambda frames: [rig.rest for f in frames])
    monkeypatch.setattr(ar, 'near_core', lambda *a: True)
    monkeypatch.setattr(ar, '_depth_key', lambda *a: b'gate')
    monkeypatch.setattr(ar, 'depth_batch', lambda d, r, p, i: np.full((len(p), 2, len(i)), .03))
    monkeypatch.setattr(ar, '_own_depths', lambda t, r, o, c, p, f, *a: np.zeros((len(f), 2, 9)))
    calls = []
    def radius(*args):
        calls.append(1)
        return np.zeros((2, len(ar.GATE_SAMPLES)))
    monkeypatch.setattr(ar, 'arm_radii', radius)
    assert ar.clip_gate(None, rig, None, None, None, sampler)[0]
    assert len(calls) == 6  # six of thirty sampled frames prove the gate


def _min_rotation_reference(a, b):
    a, b = a / max(np.linalg.norm(a), 1e-12), b / max(np.linalg.norm(b), 1e-12)
    v = np.cross(a, b)
    c = float(a @ b)
    if c < -1 + 1e-9:
        return ar._half_turn(a)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _repose_reference(rig, poses, deltas, hold):
    """The frame-by-frame arm turn the batched one replaced."""
    arms = ar.Arms(rig)
    out = []
    for f, w in enumerate(poses):
        source = np.asarray(w)
        result = source.copy()
        for s in range(2):
            if hold[f, s] or np.abs(deltas[f, s]).max() < 1e-7:
                continue
            u, e, wr = rig.joints(w, s)
            l1, l2 = np.linalg.norm(e - u), np.linalg.norm(wr - e)
            e2, w2 = np.array([e, wr]) + deltas[f, s]
            reach = np.linalg.norm(w2 - u)
            axis = (w2 - u) / max(reach, 1e-9)
            reach = np.clip(reach, abs(l1 - l2) + 1e-8, l1 + l2 - 1e-8)
            w2 = u + reach * axis
            along = (l1 * l1 - l2 * l2 + reach * reach) / (2 * reach)
            centre = u + along * axis
            normal = e2 - centre - np.dot(e2 - centre, axis) * axis
            if np.linalg.norm(normal) < 1e-9:
                normal = e - centre - np.dot(e - centre, axis) * axis
            normal /= max(np.linalg.norm(normal), 1e-9)
            e2 = centre + np.sqrt(max(0, l1 * l1 - along * along)) * normal
            _, upper, fore, hand = rig.arm[s]
            shoulder = source[upper, :3, 3]
            turn_upper = np.eye(4)
            turn_upper[:3, :3] = _min_rotation_reference(source[fore, :3, 3] - shoulder, e2 - shoulder)
            turn_upper[:3, 3] = shoulder - turn_upper[:3, :3] @ shoulder
            hand_world = turn_upper @ source[hand]
            anchor = (turn_upper @ source[fore])[:3, 3]
            turn_fore = np.eye(4)
            turn_fore[:3, :3] = _min_rotation_reference(hand_world[:3, 3] - anchor, w2 - anchor)
            turn_fore[:3, 3] = anchor - turn_fore[:3, :3] @ anchor
            combined = turn_fore @ turn_upper
            upper_rows, fore_rows, hand_rows = arms.groups[s]
            result[upper_rows] = turn_upper @ source[upper_rows]
            result[fore_rows] = combined @ source[fore_rows]
            result[hand_rows, :3, 3] += (combined @ source[hand])[:3, 3] - source[hand, :3, 3]
        out.append(result)
    return out


def _turned_poses(rig, rng, count):
    poses = []
    for _ in range(count):
        w = np.array(rig.rest)
        w[:, :3, 3] += rng.normal(0, .01, (len(w), 3))
        poses.append(w)
    return poses


def test_batched_arm_turn_is_bit_identical_to_the_frame_by_frame_turn():
    rig = _rig()
    rng = np.random.default_rng(253)
    poses = _turned_poses(rig, rng, 40)
    deltas = rng.normal(0, .02, (40, 2, 2, 3))
    deltas[3] = 0                     # an unchanged frame
    deltas[5, 1] = 1e-9               # below the threshold on one side
    deltas[7, 0, 0] = rig.joints(poses[7], 0)[2] - rig.joints(poses[7], 0)[1]  # the elbow onto the wrist line
    deltas[9, 1, 1] = np.nan          # a failed solve still turns, as before
    hold = rng.random((40, 2)) < .2
    got = ar._repose(rig, poses, deltas, hold)
    want = _repose_reference(rig, poses, deltas, hold)
    assert len(got) == len(want)
    for a, b in zip(got, want):
        assert np.array_equal(a, b, equal_nan=True)
    assert ar._repose(rig, [], np.zeros((0, 2, 2, 3)), np.zeros((0, 2), bool)) == []


def test_batched_minimum_rotations_match_the_single_rotation_and_the_half_turn():
    rng = np.random.default_rng(7)
    a, b = rng.normal(size=(500, 3)), rng.normal(size=(500, 3))
    b[:3] = -a[:3]                    # opposite directions take the half turn
    b[3] = a[3] * 2
    got = ar.min_rotations(a, b)
    for row in range(len(a)):
        assert np.array_equal(got[row], _min_rotation_reference(a[row], b[row]))
        assert np.array_equal(got[row], ar.min_rotation(a[row], b[row]))


def test_tree_clone_equals_a_deep_copy_and_shares_nothing_with_the_original():
    from bmpt.gameassets.model_arm_clips import clone_tree
    shared = [1.5, -0.0, float('nan'), 2 ** 70]
    tree = {"m_Name": "clip", "a": [{"x": 1, "y": [0.1, 0.2]}, shared, shared], "b": b"\x00\x01", "c": None,
            "d": True, "e": np.arange(3, dtype=np.float32)}
    clone = clone_tree(tree)
    reference = copy.deepcopy(tree)
    assert repr(clone) == repr(reference)
    assert clone["a"][1] is clone["a"][2] and clone["a"][1] is not shared
    assert np.array_equal(clone["e"], tree["e"]) and clone["e"].dtype == tree["e"].dtype
    clone["a"][0]["y"].append(3)
    assert tree["a"][0]["y"] == [0.1, 0.2]


def _skinned_body(rig, rng):
    """A noisy skinned cylinder around the spine with sleeves on both arms; the shoulder ring follows the upper arms
    a little, so arm turns re-skin part of the torso."""
    rest = np.array(rig.rest)
    pelvis, spine, neck = rig.spine[1], rig.spine[2], rig.spine[3]
    bones = [pelvis, spine, neck] + [rig.arm[s][k] for s in range(2) for k in (1, 2, 3)]
    vertices, joints, weights, triangles, regions = [], [], [], [], []

    def tube(centres, radius, segments, bone_of, second, region):
        base = len(vertices)
        for ring, (centre, axis) in enumerate(centres):
            axis = axis / np.linalg.norm(axis)
            side = np.cross(axis, [0, 0, 1.0])
            side /= np.linalg.norm(side)
            up = np.cross(axis, side)
            for k in range(segments):
                angle = 2 * np.pi * k / segments
                r = radius * (1 + rng.normal(0, .05))
                vertices.append(centre + r * (np.cos(angle) * side + np.sin(angle) * up))
                joints.append([bone_of(ring), second(ring)[0]])
                weights.append([1 - second(ring)[1], second(ring)[1]])
                regions.append(region)
        for ring in range(len(centres) - 1):
            for k in range(segments):
                a, b = base + ring * segments + k, base + ring * segments + (k + 1) % segments
                triangles.append((a, b, b + segments))
                triangles.append((a, b + segments, a + segments))

    heights = np.linspace(.35, 1.3, 14)
    tube([(np.array([0, h, 0.]), np.array([0, 1, 0.])) for h in heights], .15, 28,
         lambda ring: 0 if heights[ring] < .8 else 1 if heights[ring] < 1.1 else 2,
         lambda ring: (3 if ring % 2 else 6, .3) if heights[ring] > 1.15 else (0, 0.), 0)
    for s in range(2):
        for seg in range(3):
            a, b = rest[rig.arm[s][1 + seg]][:3, 3], rest[rig.arm[s][min(2 + seg, 3)]][:3, 3]
            if seg == 2:
                b = a + (a - rest[rig.arm[s][2]][:3, 3]) * .4
            bone = 3 + 3 * s + seg
            tube([(a + t * (b - a), b - a) for t in np.linspace(0, 1, 4)], .035, 10,
                 lambda ring, bone=bone: bone, lambda ring: (0, 0.), 2 + 3 * s + seg)
    bindings = np.linalg.inv(rest[bones])
    drawn = ar.Drawn(np.array(vertices), np.array(joints), np.array(weights), np.array(triangles), np.array(regions),
                     lambda pose: np.asarray(pose)[bones] @ bindings)
    drawn.pose_bones, drawn.bindings = np.array(bones), bindings
    return drawn


def _body_case(seed=31, count=12):
    rig = _rig()
    rng = np.random.default_rng(seed)
    poses = _turned_poses(rig, rng, count)
    drawn = _skinned_body(rig, rng)
    deltas = rng.normal(0, .03, (count, 2, 2, 3))
    hold = np.zeros((count, 2), bool)
    hold[::5, 0] = True
    turned = ar._repose(rig, poses, deltas, hold)
    return rig, drawn, poses, turned


def _same(a, b):
    return np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True)


def test_batched_torso_depths_penetrations_and_gradients_equal_the_frame_by_frame_queries():
    rig, drawn, poses, turned = _body_case()
    frames = list(range(len(poses)))
    single = ar.BodyFrames(drawn, rig, poses, lambda: None)
    batched = ar.BodyFrames(drawn, rig, poses, lambda: None)
    single.ensure(frames)
    batched.ensure(frames)
    assert len(drawn._body_layout[0])  # the shoulder ring is re-skinned after an arm turn
    mixed = [poses[f] if f % 3 == 0 else turned[f] for f in frames]
    for f in range(len(poses)):
        assert np.array_equal(single.frames[f].prepared[0], batched.frames[f].prepared[0])
    depth = ar.depths([batched[f] for f in frames], mixed)
    assert _same(depth, [single[f].depth(w) for f, w in zip(frames, mixed)])
    assert all(batched[f].baseline is not None for f in frames if f % 3 == 0)
    assert _same(ar.depths([batched[f] for f in frames], mixed), depth)  # cached baselines
    assert _same(ar.penetrations([batched[f] for f in frames], mixed),
                 [single[f].penetration(w) for f, w in zip(frames, mixed)])
    got, gradient = ar.depth_gradients([batched[f] for f in frames], turned)
    want = [single[f].depth_gradient(w) for f, w in zip(frames, turned)]
    assert _same(got, [m[0] for m in want]) and _same(gradient, [m[1] for m in want])
    assert (got > 0).any() and (got < 0).any()
    empty = ar.depth_gradients([], [])
    assert empty[0].shape == (0, 2, 9) and empty[1].shape == (0, 2, 9, 3)


def test_batched_sleeve_radii_equal_the_frame_by_frame_casts():
    rig, drawn, poses, turned = _body_case(seed=5, count=7)
    single = _skinned_body(rig, np.random.default_rng(5))
    for indices in (None, ar.GATE_SAMPLES):
        got = ar.arm_radii_many(drawn, rig, turned, indices)
        want = [ar.arm_radii(drawn, rig, w, indices) for w in turned]
        assert _same(got, want)
        assert (got > 0).any()
    assert ar.arm_radii_many(single, rig, [], None).shape == (0, 2, 9)


def _depth_batch_reference(drawn, rig, poses, indices):
    rows, triangles, _ = drawn.measure_mesh(True, False)
    matrices = (poses[:, drawn.pose_bones] @ drawn.bindings).astype(np.float32)
    joints, weights = drawn.joints[rows], drawn.weights[rows].astype(np.float32)
    h = drawn.h[rows].astype(np.float32)
    vertices = np.zeros((len(poses), len(rows), 3), np.float32)
    for k in range(joints.shape[1]):
        used = weights[:, k] != 0
        if used.any():
            vertices[:, used] += weights[None, used, k:k + 1] * np.einsum(
                'fvij,vj->fvi', matrices[:, joints[used, k]], h[used])[..., :3]
    tri = vertices[:, triangles]
    a, b, c = tri[:, :, 0], tri[:, :, 1], tri[:, :, 2]
    prepared = [a, b - a, c - a, np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)]
    out = []
    for f, pose in enumerate(poses):
        points = rig.samples(pose)[:, indices].reshape(-1, 3)
        _, direction, rho = ar.radial(rig.axis(pose), points)
        frame = [p[f] for p in prepared]
        frame.append(drawn._body_index.bounds(frame))
        out.append(ar.BodyFrame.query(points, direction, rho, frame).reshape(2, len(indices)))
    return np.array(out)


def test_batched_gate_depths_equal_the_frame_by_frame_casts():
    rig, drawn, poses, turned = _body_case(seed=9, count=10)
    stack = np.array(turned)
    for indices in (ar.GATE_SAMPLES, np.arange(9)):
        got = ar.depth_batch(drawn, rig, stack, indices)
        assert _same(got, _depth_batch_reference(drawn, rig, stack, indices))
    assert ar.depth_batch(drawn, rig, stack[:0], ar.GATE_SAMPLES).shape == (0, 2, 5)


def test_batched_query_equals_single_frame_queries_with_ties_and_misses():
    rng = np.random.default_rng(12)
    base = rng.uniform(-.2, .2, (500, 3, 3)).astype(np.float32)
    base[1] = base[0]                 # a duplicate triangle: the first index wins the normal
    index = ar.TriangleIndex(base)
    frames = [base + rng.normal(0, .02, base.shape).astype(np.float32) for _ in range(5)]
    frames[2] = base.copy()
    points = rng.uniform(-.25, .25, (5, 18, 3))
    points[:, 0] = frames[0][0].mean(axis=0) - [0, 0, .05]
    dirs = rng.normal(size=points.shape)
    dirs[:, 0] = [0, 0, 1.]
    dirs /= np.linalg.norm(dirs, axis=2)[..., None]
    rho = rng.uniform(0, .35, (5, 18))
    rho[3] = 0
    prepared = ar.BodyFrame.prepare(np.stack(frames))
    prepared.append(ar.leaf_bounds(index, prepared[3], prepared[4]))
    for normals in (False, True):
        got = ar.query_many(points, dirs, rho, prepared, normals=normals)
        for f, triangles in enumerate(frames):
            single = ar.BodyFrame.prepare(triangles)
            single.append(index.bounds(single))
            want = ar.BodyFrame.query(points[f], dirs[f], rho[f], single, normals=normals)
            if normals:
                assert _same(got[0][f], want[0]) and _same(got[1][f], want[1])
            else:
                assert _same(got[f], want)
    empty = ar.BodyFrame.prepare(np.zeros((5, 0, 3, 3)))
    empty.append(None)
    assert _same(ar.query_many(points, dirs, rho, empty), -rho)
