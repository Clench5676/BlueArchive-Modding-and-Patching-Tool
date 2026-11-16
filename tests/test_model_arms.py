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

        @staticmethod
        def posed_batch(poses, rows):
            return np.array([Own.posed(w, rows) for w in poses])
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
        # frames set up in batches equal frames set up one at a time
        alone = ar.BodyFrame(drawn, rig, poses[f], batched.frames[f].vertices)
        for got, want in zip(batched.frames[f].prepared[:5], alone.prepared[:5]):
            assert got.tobytes() == want.tobytes()
        for got, want in zip(batched.frames[f].prepared[5][1:], alone.prepared[5][1:]):
            assert got.tobytes() == want.tobytes()
        assert np.array_equal(batched.frames[f].vertices, drawn.posed(poses[f], batched.rows))
    assert np.array_equal(rig.samples_many(turned), [rig.samples(w) for w in turned])
    assert rig.samples_many(np.zeros((0, len(rig.rest), 4, 4))).shape == (0, 2, 9, 3)
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


def _solve_many_reference(rig, poses, needs, hold, pins, sleeves, guard=None, initial=None):
    """The solver loop before its per-step work was trimmed."""
    idx = [k for k, _, _ in ar.DRIVEN]
    tw = np.array([x for _, x, _ in ar.DRIVEN])[None, None, :, None]
    te = 1 - tw
    a = np.minimum(needs[:, :, idx], ar.NEED_CAP).copy()
    a[:, :, 4:] *= (1 - pins)[..., None]
    free = (~hold)[..., None]
    pts = np.array([rig.samples(w) for w in poses])
    p = pts[:, :, idx]
    diff = p[:, 0, :, None] - p[:, 1, None, :]
    c = np.linalg.norm(diff, axis=3)
    reach, grown = sleeves
    near = (np.clip((reach - c) / 0.02, 0, 1) * np.exp(-(c - c.min(axis=(1, 2))[:, None, None]) / 0.015)
            if grown > 0 else np.zeros_like(c))
    active = (a.max(axis=2) > 1e-5) & ~hold
    near *= active.any(axis=1)[:, None, None]
    normal = diff / np.maximum(c, 1e-9)[..., None]
    d = np.array([ar.push_directions(rig, w, pt)[:, idx] for w, pt in zip(poses, pts)])
    joints = np.array([[rig.joints(w, s) for s in range(2)] for w in poses])
    u, e, wr = (joints[:, :, k] for k in range(3))
    l1 = np.linalg.norm(e - u, axis=2)[..., None]
    l2 = np.linalg.norm(wr - e, axis=2)[..., None]
    de, dw = np.zeros_like(e), np.zeros_like(wr)
    if initial is not None:
        de, dw = .25 * initial[:, :, 0], .25 * initial[:, :, 1]
    guard_tw = np.array([0, 0, 0, .25, .5, .75, 1, 1, 1])[None, None, :, None]
    guard_te = np.array([.5, .75, 1, .75, .5, .25, 0, 0, 0])[None, None, :, None]
    step = ar.STEP if guard is None else .025
    steps = ar.STEPS if initial is None else 3 * ar.STEPS // 4
    for iteration in range(steps + (60 if guard is not None else 0)):
        dp = tw * dw[:, :, None] + te * de[:, :, None]
        v = np.maximum(0, a - (d * dp).sum(axis=3))
        v += 4 * np.maximum(0, -(d * dp).sum(axis=3))
        g = -v[..., None] * d
        rel = dp[:, 0, :, None] - dp[:, 1, None, :]
        vp = ar.PAIR_WEIGHT * near * np.maximum(0, grown - (normal * rel).sum(axis=3))
        g[:, 0] -= (vp[..., None] * normal).sum(axis=2)
        g[:, 1] += (vp[..., None] * normal).sum(axis=1)
        gw = (tw * g).sum(axis=2)
        ge = (te * g).sum(axis=2)
        if guard is not None:
            direction, target = guard
            all_dp = guard_tw * dw[:, :, None] + guard_te * de[:, :, None]
            penalty = 4 * np.maximum(0, target - (direction * all_dp).sum(axis=3))
            gradient = -penalty[..., None] * direction
            gw += (guard_tw * gradient).sum(axis=2)
            ge += (guard_te * gradient).sum(axis=2)
        if iteration >= steps:
            gw = (guard_tw * gradient).sum(axis=2)
            ge = (guard_te * gradient).sum(axis=2)
        else:
            gw += (ar.MOVE_COST + ar.PIN_COST * pins[..., None]) * dw
            ge += ar.MOVE_COST * de
        v1, v2 = e + de - u, wr + dw - e - de
        a1, a2 = v1 / l1, v2 / l2
        gu = ge + gw
        v1 -= step * (gu - (gu * a1).sum(axis=2)[..., None] * a1) * free
        v2 -= step * (gw - (gw * a2).sum(axis=2)[..., None] * a2) * free
        v1 *= l1 / np.maximum(np.linalg.norm(v1, axis=2), 1e-9)[..., None]
        v2 *= l2 / np.maximum(np.linalg.norm(v2, axis=2), 1e-9)[..., None]
        de = (u + v1 - e) * free
        dw = (u + v1 + v2 - wr) * free
    return np.stack((de, dw), axis=2)


def test_trimmed_solver_steps_are_bit_identical_with_and_without_guard_and_start():
    rig = _rig()
    rng = np.random.default_rng(41)
    poses = _turned_poses(rig, rng, 9)
    needs = np.abs(rng.normal(0, .03, (9, 2, 9)))
    needs[2] = 0
    hold = rng.random((9, 2)) < .15
    pins = np.clip(rng.normal(.2, .3, (9, 2)), 0, 1)
    initial = rng.normal(0, .01, (9, 2, 2, 3))
    direction = rng.normal(size=(9, 2, 9, 3))
    direction /= np.linalg.norm(direction, axis=3)[..., None]
    target = np.where(rng.random((9, 2, 9)) < .5, rng.normal(0, .01, (9, 2, 9)), -1e6)
    for sleeves in ((0, 0), (.3, .02)):
        for guard in (None, (direction, target)):
            for start in (None, initial):
                got = ar.solve_many(rig, poses, needs, hold, pins, sleeves, guard, start)
                want = _solve_many_reference(rig, poses, needs, hold, pins, sleeves, guard, start)
                assert got.tobytes() == want.tobytes()


def _contacts_reference(rig, own, weapons, poses, rows, loop, shown=None):
    """Weapon holds and head pins frame by frame, as before they were batched."""
    relative = np.full((len(poses), 2, 3), np.nan)
    distance = np.full((len(poses), 2), np.inf)
    if weapons and rig.weapon is not None:
        for f, w in enumerate(poses):
            mount = w[rig.weapon]
            if abs(np.linalg.det(mount[:3, :3])) < 1e-9:
                continue
            palms = rig.samples(w)[:, 7]
            visible = [weapon.posed(w, np.arange(0, len(weapon.h), 5)) for k, weapon in enumerate(weapons)
                       if shown is None or shown[f, k]]
            if not visible:
                continue
            vertices = np.concatenate(visible)
            distance[f] = np.linalg.norm(palms[:, None] - vertices[None], axis=2).min(axis=1)
            relative[f] = (np.c_[palms, np.ones(2)] @ np.linalg.inv(mount).T)[:, :3]
    pins = np.zeros((len(poses), 2))
    if len(rows):
        sampled = rows[::2]
        for f, w in enumerate(poses):
            hv = own.posed(w, sampled)
            pts = rig.samples(w)[:, 6:9].reshape(-1, 3)
            dist = np.linalg.norm(pts[:, None] - hv[None], axis=2).min(axis=1).reshape(2, 3).min(axis=1)
            pins[f] = np.clip((ar.PIN_FAR - dist) / (ar.PIN_FAR - ar.PIN_NEAR), 0.0, 1.0)
        pins = np.clip(ar.blend(pins, loop), 0.0, 1.0)
    return ar.weapon_holds(relative, distance, loop), pins


def test_batched_contacts_equal_the_frame_by_frame_weapon_and_head_checks():
    base = _rig()
    bones = [(n.name, (n.parent + 1) if n.parent is not None else 0, tuple(n.local[0]), tuple(n.local[1]))
             for n in base.sk.nodes]
    hand = base.arm[1][3]
    bones.append(("Bip001_Weapon", hand + 1, (0, -.03, .04), (0, 0, 0, 1)))
    rig = ar.Rig(_skeleton(bones))
    rng = np.random.default_rng(77)
    own = _skinned_body(rig, rng)
    rest = np.array(rig.rest)
    weapons = []
    palm = rig.samples(rest)[1, 7]
    for bone, size in ((rig.weapon, .03), (hand, .01)):
        vertices = palm + rng.normal(0, size, (40, 3))
        drawn = ar.Drawn(vertices, np.zeros((40, 1), int), np.ones((40, 1)), np.zeros((0, 3), int),
                         np.zeros(40, int), None)
        drawn.pose_bones, drawn.bindings = np.array([bone]), np.linalg.inv(rest[[bone]])
        weapons.append(drawn)
    poses = []
    for f in range(40):
        w = rest.copy()
        w[:, :3, 3] += np.sin(f / 6) * .004 + rng.normal(0, .0005, (len(w), 3))
        poses.append(w)
    poses[5][rig.weapon, :3, :3] = 0          # a hidden mount
    poses[9][rig.weapon] = np.nan             # an unreadable mount still counts as shown
    shown = rng.random((40, 2)) < .8
    shown[12] = False
    wrist = rig.samples(rest)[0, 6]
    head = np.argsort(np.linalg.norm(own.h[:, :3] - wrist, axis=1))[:60]  # a surface the left hand touches
    for loop in (False, True):
        for which in (None, shown):
            got = ar.contacts(rig, own, weapons, poses, head, loop, which)
            want = _contacts_reference(rig, own, weapons, poses, head, loop, which)
            assert got[0].tobytes() == want[0].tobytes() and got[1].tobytes() == want[1].tobytes()
    assert got[0].any() and (got[1] > 0).any()


class _FakeClip(dict):
    def __init__(self, number, tree):
        super().__init__(tree)
        from types import SimpleNamespace
        self.assets_file, self.path_id, self.number = SimpleNamespace(name="cab-clips"), number, number

    def peek_name(self):
        return f"clip{self.number}"


def _threaded_retarget_case(monkeypatch, delays, step=None):
    """retarget with its measuring parts replaced: solves of different lengths finish out of clip order."""
    import threading
    import zlib
    from types import SimpleNamespace
    from bmpt.gameassets import model_arm_clips as ac
    rig = _rig()
    body = _skinned_body(rig, np.random.default_rng(3))
    path = zlib.crc32(rig.sk.nodes[rig.arm[0][1]].path.encode())
    clips = [_FakeClip(k, {"m_ClipBindingConstant": {"genericBindings": [{"typeID": 4, "path": path}]},
                           "m_MuscleClip": {"m_LoopTime": 1}}) for k in range(len(delays))]
    count = 12
    poses = np.repeat(np.array(rig.rest)[None], count, axis=0)
    seen = {"stopped": 0, "threads": set()}

    class Frames:
        def __init__(self, sk, tree, nodes=None):
            self.times = np.arange(count) / 30.

        def world(self, frames):
            return poses[frames]

    def solve(rig_, own, new, poses_, loop, depth, before, frames, hold, pins, sleeves, tick):
        seen["threads"].add(threading.current_thread().name)
        number = int(depth[0, 0, 0])
        try:
            for _ in range(4):
                time.sleep(delays[number] / 4)
                tick()
        except Exception:
            seen["stopped"] += 1
            raise
        out = [w.copy() for w in poses_]
        for w in out:
            w[rig_.arm[0][2], :3, 3] += .01 * (number + 1)
        return out

    import time
    monkeypatch.setattr(ar, "Rig", lambda sk: rig)
    monkeypatch.setattr(ar, "own_drawn", lambda target: body)
    monkeypatch.setattr(ar, "adapted_drawn", lambda target, source, plan: body)
    monkeypatch.setattr(ar, "ClipFrames", Frames)
    monkeypatch.setattr(ar, "clip_gate", lambda *a: (True, {}, {}, .5))
    monkeypatch.setattr(ar, "head_core", lambda target: np.zeros(0, int))
    monkeypatch.setattr(ar, "girth", lambda drawn, rig_: {"fore": 30.})
    monkeypatch.setattr(ar, "contacts", lambda *a: (np.zeros((count, 2), bool), np.zeros((count, 2))))
    monkeypatch.setattr(ar, "_own_depths", lambda t, r, o, clip, *a: np.full((5, 2, 9), float(clip.number)))
    monkeypatch.setattr(ar, "solve_clip", solve)
    monkeypatch.setattr(ac, "encode_rotations", lambda tree, rotations: tree.update(edited=True))
    monkeypatch.setattr(ac, "edit_error", lambda before, after, rotations: None)
    monkeypatch.setattr(ar.ma, "_key", lambda clip: clip.number)
    reported = []
    target = SimpleNamespace(skeleton=rig.sk)
    out = ar.retarget(target, None, None, clips, [], step=step,
                      progress=lambda done, total: reported.append((done, total)))
    return out, reported, seen


def test_threaded_solves_keep_clip_order_progress_and_single_thread_results(monkeypatch):
    import threading
    delays = [.08, .01, .05, .0, .03]
    monkeypatch.setattr(ar, "ARM_WORKERS", 3)
    out, reported, seen = _threaded_retarget_case(monkeypatch, delays)
    assert list(out) == list(range(len(delays)))
    assert all(change.tree["edited"] for change in out.values())
    assert [d for d, _ in reported] == sorted(d for d, _ in reported) and reported[-1] == (len(delays), len(delays))
    assert len(seen["threads"]) > 1 and threading.current_thread().name not in seen["threads"]
    monkeypatch.setattr(ar, "ARM_WORKERS", 1)
    alone, _, _ = _threaded_retarget_case(monkeypatch, delays)
    assert list(alone) == list(out)
    for key in out:
        for path, values in out[key].rotations.items():
            assert values.tobytes() == alone[key].rotations[path].tobytes()
    assert not [t for t in threading.enumerate() if t.name.startswith("arm-solve")]


def test_a_stopping_step_stops_the_running_solves_and_is_passed_on(monkeypatch):
    import threading
    calls = []

    class Cancelled(Exception):
        pass

    def step():
        calls.append(1)
        if len(calls) > 3:
            raise Cancelled

    monkeypatch.setattr(ar, "ARM_WORKERS", 2)
    monkeypatch.setattr(ar._Progress, "__call__", lambda self: self.step())
    started = time.perf_counter()
    with pytest.raises(Cancelled):
        _threaded_retarget_case(monkeypatch, [.4] * 6, step)
    assert time.perf_counter() - started < .3  # the running solves stopped at their next round
    assert not [t for t in threading.enumerate() if t.name.startswith("arm-solve")]
