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
