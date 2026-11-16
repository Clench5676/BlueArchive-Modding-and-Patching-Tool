"""Rule B of the costume switch: the skirt solver and the dense clip writer, on synthetic data."""

import copy
import math
import types
import zlib

import numpy as np
import pytest

from bmpt.gameassets import model_skirts as ms
from bmpt.gameassets.model import bind_tracks, decode_clip
from bmpt.gameassets import model_adapt as ma
from bmpt.gameassets.model_adapt import trs


def _rotation(axis, angle):
    axis = np.asarray(axis, float)
    return ms.axis_angle(axis / np.linalg.norm(axis), angle)


@pytest.mark.parametrize("axis,angle", [((1, 0, 0), 0.3), ((0, 1, 1), 2.0), ((1, -2, 0.5), 3.1), ((0, 0, 1), 0.0)])
def test_quaternion_builds_the_same_matrix_as_the_skeleton(axis, angle):
    r = _rotation(axis, angle)
    assert np.allclose(trs((0, 0, 0), ms.quaternion(r), (1, 1, 1))[:3, :3], r, atol=1e-9)


def test_rotation_vectors_round_trip():
    for axis, angle in (((0.2, 1, -0.4), 0.7), ((1, 0, 0), 3.0), ((0, 1, 0), 1e-12)):
        r = _rotation(axis, angle)
        assert np.allclose(ms.from_vector(ms.rotation_vector(r)), r, atol=1e-9)


def test_smooth_weights_blend_across_a_seam_and_keep_each_share():
    # vertices 0-1 on chain 0, 2-3 on chain 1 (0.6 of vertex 3 on a fixed bone), 4 off the chains
    chain = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.4], [0.0, 0.0]])
    edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4]])
    out = ms.smooth_weights(chain, edges, sweeps=2)
    assert np.allclose(out.sum(axis=1), chain.sum(axis=1))
    assert 0 < out[1, 1] < out[2, 1] < 1 and 0 < out[2, 0]  # the seam between vertices 1 and 2 blends
    assert np.allclose(out[4], 0)
    assert np.allclose(ms.smooth_weights(chain, edges, sweeps=0), chain)


def test_the_legs_reach_per_cell_around_the_axis():
    frame = ms.Frame(np.zeros(3), ms.UP, ms.FRONT)
    legs = np.array([[0.0, -0.2, 0.1], [0.0, -0.3, 0.12]])  # in front, below the pelvis
    grid = ms.envelope(frame, legs)
    reach, distance, direction = ms.reach(grid, frame, np.array([[0.0, -0.2, 0.3], [0.0, -0.2, -0.3], [0.0, 0.3, 0.3]]))
    assert reach[0] == pytest.approx(0.1) and distance[0] == pytest.approx(0.3)
    assert np.allclose(direction[0], ms.FRONT)
    assert reach[1] == -np.inf and reach[2] == -np.inf  # behind the body, and above the legs


def _setup(leg_forward=0.0, knee=None):
    """A pelvis (node 0) at the origin, a thigh (node 1) and a front skirt chain of two bones hanging from the pelvis
    at 12 cm, its cloth a strip down the front; the thigh's vertices 3-8 cm in front of the axis. ``knee``: a knee
    (node 2, 2-6 cm in front of the axis 25 cm down at rest) moved by that offset."""
    t_rest = [np.eye(4), np.eye(4), np.eye(4)]
    rest = np.array([trs((0, 0, 0.12), (0, 0, 0, 1), (1, 1, 1)), trs((0, -0.15, 0.12), (0, 0, 0, 1), (1, 1, 1))])
    local = np.array([rest[0], np.linalg.inv(rest[0]) @ rest[1]])
    chains = ms.Chains(0, [-1, 0], local, rest)
    heights = np.linspace(-0.02, -0.3, 15)
    strip = np.array([[x, y, 0.12] for y in heights for x in (-0.03, 0.0, 0.03)])
    joints = np.array([[0 if y > -0.15 else 1, 2, 2, 2] for y in strip[:, 1]])
    weights = np.tile([1.0, 0.0, 0.0, 0.0], (len(strip), 1))
    edges = np.array([[i, i + 3] for i in range(len(strip) - 3)] + [[i, i + 1] for i in range(len(strip) - 1) if i % 3 != 2])
    cloth = ms.Cloth(strip, joints, weights, [0], edges)
    leg = np.array([[x, y, z] for x in (-0.04, 0.0, 0.04) for y in np.linspace(-0.05, -0.4, 15) for z in (0.03, 0.08)])
    nodes = np.ones((len(leg), 1), int)
    kneecap = np.array([[x, -0.25, z] for x in (-0.03, 0.0, 0.03) for z in (0.02, 0.04, 0.06)])
    leg, nodes = np.concatenate([leg, kneecap]), np.concatenate([nodes, np.full((len(kneecap), 1), 2)])
    legs = ms.Legs(leg, nodes, np.ones((len(leg), 1)))
    solver = ms.Solver(chains, cloth, legs, t_rest, pelvis=0)
    pose = [np.eye(4), trs((0, 0, leg_forward), (0, 0, 0, 1), (1, 1, 1)),
            trs(knee if knee is not None else (0, 0, 0), (0, 0, 0, 1), (1, 1, 1))]
    return solver, pose


@pytest.mark.parametrize("loop,with_edges", [(False, True), (True, True), (True, False)])
def test_batched_joint_equals_scalar_reference_bit_for_bit(loop, with_edges):
    solver, pushed = _setup(leg_forward=0.1, knee=(0, 0.235, 0.09))
    # Two roots, descendants, mixed influences and a fixed influence exercise
    # every Jacobian block and the cross-root normal equations.
    chains, cloth = solver.chains, solver.cloth
    local = np.concatenate([chains.local, chains.local.copy()])
    local[2, 0, 3] += 0.02
    rest = np.array([local[0], local[0] @ local[1], local[2], local[2] @ local[3]])
    chains = ms.Chains(0, [-1, 0, -1, 2], local, rest)
    joints = np.column_stack([cloth.joints[:, 0], cloth.joints[:, 0] + 2, np.full((len(cloth.rest), 2), 4)])
    weights = np.tile([0.6, 0.3, 0.1, 0.0], (len(cloth.rest), 1))
    edges = cloth.edges if with_edges else np.empty((0, 2), int)
    solver = ms.Solver(chains, ms.Cloth(cloth.rest, joints, weights, [0], edges), solver.legs,
                       [np.eye(4)] * 3, 0)
    reference = copy.deepcopy(solver)
    reference._joint = types.MethodType(_reference_joint, reference)
    _, free = _setup()
    poses = [pushed, pushed, free, free]
    times = [0.0, 0.05, 0.1, 0.2]
    expected = reference.sequence(times, lambda k: poses[k], loop)
    yielded = []
    actual = solver.sequence(times, lambda k: poses[k], loop, step=lambda: yielded.append(True))
    assert yielded
    for measured, old in zip(actual, expected):
        assert measured.keys() == old.keys()
        for root in old:
            assert np.array_equal(measured[root].view(np.uint64), old[root].view(np.uint64))


def test_solver_sequence_yields_and_propagates_cancellation(monkeypatch):
    solver, pose = _setup(leg_forward=0.1)
    solved = []
    original = solver.solve
    def measured(*args):
        result = original(*args)
        solved.append(result)
        return result
    monkeypatch.setattr(solver, "solve", measured)
    def cancel():
        if solved:
            raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        solver.sequence([0.0, 0.1], lambda _: pose, step=cancel)
    assert len(solved) == 1


def test_judged_sequence_reuse_writes_identical_clip_curves(monkeypatch):
    solver, pushed = _setup(leg_forward=0.1)
    _, free = _setup()
    poses = [pushed, free, pushed, free]
    prepared = ([k / 30 for k in range(4)], lambda k: poses[k], set())
    skeleton = types.SimpleNamespace(nodes=[types.SimpleNamespace(path=f"bone/{k}") for k in range(5)],
                                     clip_frames=lambda tree: prepared)
    tree = _clip()
    skirt = ma.Skirt([0, 1], ["a", "b"], [-1, 0], [], 0, solver, 3)
    measured = ma.skirt_sequence(skeleton, solver, tree, prepared=prepared)
    old = copy.deepcopy(tree)
    assert ma.skirt_curves(skeleton, skirt, old) == 1
    skirt.solved[("clip", 1)] = measured
    def unexpected(*args, **kwargs):
        raise AssertionError("judged sequence was solved again")
    monkeypatch.setattr(ma, "skirt_sequence", unexpected)
    assert ma.skirt_curves(skeleton, skirt, tree, ("clip", 1)) == 1
    assert tree == old


def test_shared_curve_dependencies_write_the_same_solver_and_face_curves():
    solver, _ = _setup(leg_forward=0.1)
    skeleton = ma.Skeleton(None)
    for k in range(7):
        skeleton.nodes.append(ma.Node(f"bone{k}", f"bone/{k}", 0 if k else None,
                                      ((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), None, None, True))
    tree = _clip()
    tree['m_ClipBindingConstant']['genericBindings'][0]['path'] = zlib.crc32(skeleton.nodes[1].path.encode())
    face = ma.FaceRetarget(solver.chains, 0, [1, 2], skeleton.world())
    skirts = [ma.Skirt([0, 1], ["a", "b"], [-1, 0], [], 0, solver, 3),
              ma.Skirt([0, 1], ["c", "d"], [-1, 0], [], 0, face, 5)]
    expected = copy.deepcopy(tree)
    for skirt in skirts:
        ma.skirt_curves(skeleton, skirt, expected)
    prepared = ma.curve_frames(skeleton, skirts, tree)
    assert all(prepared[1](0)[n] is None for n in range(3, 7))
    for skirt in skirts:
        ma.skirt_curves(skeleton, skirt, tree, prepared=prepared)
    assert tree == expected


def test_shared_spring_frames_pose_only_the_anchor():
    skeleton = ma.Skeleton(None)
    for k in range(8):
        skeleton.nodes.append(ma.Node(f"bone{k}", f"bone/{k}", 0 if k else None,
                                      ((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), None, None, True))
    spring = ms.Spring(_hanging_chain(), {0: math.radians(20)})
    skirt = ma.Skirt([0, 1], ["a", "b"], [-1, 0], [], 0, spring, 4)
    tree = _clip(3)
    bindings = tree['m_ClipBindingConstant']['genericBindings']
    bindings[0]['path'] = zlib.crc32(skeleton.nodes[1].path.encode())
    prepared = ma.curve_frames(skeleton, [skirt], tree)
    assert prepared[1](0)[spring.chains.anchor] is not None
    assert all(prepared[1](0)[n] is None for n in range(8) if n != spring.chains.anchor)
    frames, expected = ma.skirt_sequence(skeleton, spring, tree)
    actual_frames, actual = ma.skirt_sequence(skeleton, spring, tree, prepared=prepared)
    assert frames == actual_frames
    for old, measured in zip(expected, actual):
        assert old.keys() == measured.keys()
        for root in old:
            assert np.array_equal(old[root].view(np.uint64), measured[root].view(np.uint64))


def test_the_solver_leaves_the_rest_pose_as_it_is():
    solver, pose = _setup()
    turns = solver.solve(pose)
    assert np.allclose(turns[0], solver.chains.hanging(0), atol=1e-9)


def test_a_leg_pushing_forward_turns_the_skirt_out_of_it():
    solver, pose = _setup(leg_forward=0.1)  # the thigh now reaches 18 cm, past the cloth at 12 cm
    turns = solver.solve(pose)
    angle = ms.angle_of(turns[0][:3, :3])
    assert 0.02 < angle <= ms.MAX_ANGLE + 1e-9  # with a shift of the head (see below) where the turn falls short
    axis = ms.rotation_axis(turns[0][:3, :3])
    assert abs(axis[0]) > 0.9  # a turn about the side axis: the hem swings forward
    world = solver.posed(pose, turns)
    matrices = np.concatenate([world @ solver.inverse, np.eye(4)[None]])
    cloth = ms.skin(solver.cloth.rest, solver.cloth.joints, solver.cloth.weights, matrices)
    hem = cloth[solver.cloth.rest[:, 1] < -0.2]
    assert (hem[:, 2] > 0.14).all()  # carried forward, toward the leg's reach


def test_a_knee_at_the_height_of_the_chain_head_moves_the_head():
    # the knee comes up to just below the head, 11-15 cm in front: no turn about the head carries the cloth there
    solver, pose = _setup(knee=(0, 0.235, 0.09))
    turns = solver.solve(pose)
    shift = turns[0][:3, 3] - solver.chains.hanging(0)[:3, 3]
    assert 0.02 < shift[2] and np.linalg.norm(shift) <= ms.MAX_SHIFT + 1e-9
    world = solver.posed(pose, turns)
    matrices = np.concatenate([world @ solver.inverse, np.eye(4)[None]])
    cloth = ms.skin(solver.cloth.rest, solver.cloth.joints, solver.cloth.weights, matrices)
    top = cloth[solver.cloth.rest[:, 1] > -0.03]
    assert (top[:, 2] > 0.15).all()  # the top of the strip carried in front of the knee


def test_in_time_a_lifted_skirt_falls_back_over_a_few_frames():
    solver, pushed = _setup(leg_forward=0.1)
    _, free = _setup()
    sequence = solver.sequence([0.0, 0.1, 0.2, 0.3], lambda k: pushed if k == 0 else free)
    angles = [ms.angle_of(t[0][:3, :3]) for t in sequence]
    assert angles[0] > angles[1] > angles[2] > angles[3] > 0
    assert angles[1] == pytest.approx(angles[0] * ms.RELEASE, rel=0.05)
    hanging = solver.chains.hanging(0)[:3, 3]
    shifts = [float(np.linalg.norm(t[0][:3, 3] - hanging)) for t in sequence]
    assert all(b <= a * ms.RELEASE + 1e-9 for a, b in zip(shifts, shifts[1:]))  # a moved head goes back as well


# --- clips ---------------------------------------------------------------------------------------------------------

def _binding(path, attribute):
    return {"path": zlib.crc32(path.encode()), "attribute": attribute, "script": {"m_FileID": 0, "m_PathID": 0},
            "typeID": 4, "customType": 0, "isPPtrCurve": 0}


def _clip(dense_curves=0):
    """A generic clip of 0.1 s at 30 fps: a constant position (3 curves) and ``dense_curves`` dense Euler curves."""
    frames = 4
    bindings = ([_binding("bone_root/Euler", 4)] if dense_curves else []) + [_binding("bone_root/Bip001", 1)]
    samples = [float(f * 10 + c) for f in range(frames) for c in range(dense_curves)]
    curves = dense_curves + 3
    tree = {
        "m_Name": "Test", "m_Legacy": False, "m_SampleRate": 30.0,
        "m_MuscleClip": {
            "m_StartTime": 0.0, "m_StopTime": 0.1, "m_LoopTime": False,
            "m_Clip": {"data": {
                "m_StreamedClip": {"data": [], "curveCount": 0},
                "m_DenseClip": {"m_FrameCount": frames, "m_CurveCount": dense_curves, "m_SampleRate": 30.0,
                                "m_BeginTime": 0.0, "m_SampleArray": samples},
                "m_ConstantClip": {"data": [1.0, 2.0, 3.0]}}},
            "m_IndexArray": [-1] * 200,
            "m_ValueArrayDelta": [{"m_Start": 0.0, "m_Stop": 0.0} for _ in range(curves)],
            "m_ValueArrayReferencePose": [],
        },
        "m_ClipBindingConstant": {"genericBindings": bindings, "pptrCurveMapping": []},
    }
    tree["m_MuscleClipSize"] = ms.muscle_size(tree)
    return tree


def _hanging_chain():
    """A two-bone chain hanging 0.2 m down from an anchor at the origin (anchor node 0)."""
    local = np.array([trs((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), trs((0, -0.2, 0), (0, 0, 0, 1), (1, 1, 1))])
    rest = np.array([local[0], local[0] @ local[1]])
    return ms.Chains(0, [-1, 0], local, rest)


def _swing(spring, path, rate=30.0):
    """The root's turn angle per frame while the anchor moves along ``path`` (its x position per frame)."""
    times = [k / rate for k in range(len(path))]
    pose = lambda k: [trs((path[k], 0, 0), (0, 0, 0, 1), (1, 1, 1))]  # noqa: E731
    return [ms.angle_of(turns[0][:3, :3]) for turns in spring.sequence(times, pose)], spring.sequence(times, pose)


def test_a_chain_swings_after_its_anchor_within_its_limit_and_comes_back_to_rest():
    spring = ms.Spring(_hanging_chain(), {0: math.radians(20)})
    assert spring.roots == [0] and np.isclose(spring.lengths[0], 0.2)
    still, _ = _swing(spring, [0.0] * 30)
    assert max(still) < 1e-9  # an anchor at rest leaves the chain at rest
    path = [0.0] * 5 + [0.3] * 85  # the anchor jumps 30 cm to the side and stays
    angles, sequence = _swing(spring, path)
    assert angles[0] < 1e-9
    assert max(angles) > math.radians(5)  # the tip lags behind
    assert max(angles) <= math.radians(20) + 1e-9
    assert angles[-1] < 0.1 * max(angles)  # and settles
    # the tip trails the motion: the root turns its tip toward -x while the anchor moves to +x
    peak = int(np.argmax(angles))
    tip = spring.posed([trs((path[peak], 0, 0), (0, 0, 0, 1), (1, 1, 1))], sequence[peak])[1][:3, 3]
    assert tip[0] < path[peak]


def test_a_slot_clip_the_source_has_by_name_plays_the_source_keys():
    chains = _hanging_chain()
    turned = _rotation((0, 0, 1), math.radians(30))
    q = ms.quaternion(turned)
    keys = {("formation", "idle"): (np.array([0.0, 1.0]), {1: np.array([[0, 0, 0, 1], q])})}
    spring = ms.Spring(chains, {0: math.radians(20)}, keys, np.array(chains.local))
    turns = spring.keyed("CH0001_Formation_Idle", [0.0, 0.5, 1.0])
    assert [sorted(t) for t in turns] == [[1], [1], [1]]
    assert np.allclose(turns[0][1], chains.local[1], atol=1e-9)  # the source's first frame: its rest
    assert np.allclose(turns[2][1][:3, :3], turned, atol=1e-9) and np.allclose(turns[2][1][:3, 3], (0, -0.2, 0))
    assert 0 < ms.angle_of(turns[1][1][:3, :3]) < math.radians(30)  # half way, by its time
    assert spring.keyed("CH0001_Cafe_Walk", [0.0, 1.0]) is None  # no clip of that name: the swing
    assert spring.keyed("Idle", [0.0, 1.0]) is None


def test_each_root_hangs_off_its_own_anchor():
    """Rule B's chains off the pelvis and off the spine: each root follows its own anchor."""
    local = np.array([trs((0, -0.1, 0), (0, 0, 0, 1), (1, 1, 1)), trs((0, -0.3, 0), (0, 0, 0, 1), (1, 1, 1))])
    pose = [trs((0, 1, 0), (0, 0, 0, 1), (1, 1, 1)), trs((0.5, 1.3, 0), (0, 0, 0, 1), (1, 1, 1))]
    chains = ms.Chains(0, [-1, -1], local, np.array([pose[0] @ local[0], pose[1] @ local[1]]), anchors=[0, 1])
    world = chains.posed_in(pose)
    assert np.allclose(world[0][:3, 3], (0, 0.9, 0)) and np.allclose(world[1][:3, 3], (0.5, 1.0, 0))
    assert chains.anchor_of(1) == 1 and ms.Chains(0, [-1], local[:1], local[:1]).anchor_of(0) == 0


def test_a_hanging_chain_keeps_part_of_its_world_direction_and_an_upright_one_its_own():
    """Gravity: with the anchor turned 90 degrees, hair hanging down turns only half as far as the head; an ear
    pointing up turns with the head."""
    hair = _hanging_chain()
    local = np.array([trs((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), trs((0, 0.1, 0), (0, 0, 0, 1), (1, 1, 1))])
    ear = ms.Chains(0, [-1, 0], local, np.array([local[0], local[0] @ local[1]]))
    turned = trs((0, 0, 0), ms.quaternion(_rotation((0, 0, 1), math.pi / 2)), (1, 1, 1))
    for chains, hanging, expected in ((hair, True, 45.0), (ear, False, 90.0)):
        spring = ms.Spring(chains, {0: math.radians(20)})
        assert bool(spring.hanging[0]) is hanging
        settled = spring.settle([{}], [turned])[0]
        world = spring.posed([turned], settled)
        tip = world[1][:3, 3] - world[0][:3, 3]
        rest = chains.rest[1][:3, 3] - chains.rest[0][:3, 3]
        angle = math.degrees(math.acos(float(tip @ rest) / (np.linalg.norm(tip) * np.linalg.norm(rest))))
        assert angle == pytest.approx(expected, abs=0.5)


def test_a_chain_too_short_or_without_a_limit_does_not_swing():
    spring = ms.Spring(_hanging_chain(), {})
    assert spring.roots == []


@pytest.mark.parametrize("dense_curves", [0, 3])
def test_dense_curves_go_between_the_dense_and_the_constant_ones(dense_curves):
    tree = _clip(dense_curves)
    grid = ms.dense_grid(tree)
    assert np.allclose(grid, np.arange(4) / 30)
    samples = np.array([[0.0, 0.0, math.sin(a / 2), math.cos(a / 2)] for a in (0.0, 0.1, 0.2, 0.3)])
    template = tree["m_ClipBindingConstant"]["genericBindings"][0]
    shift = np.array([[0.0, 0.01 * f, 0.02] for f in range(4)])
    ms.add_dense_curves(tree, [ms.rotation_binding(template, "bone_root/Skirt"), ms.position_binding(template, "bone_root/Skirt")],
                        np.concatenate([samples, shift], axis=1))
    muscle = tree["m_MuscleClip"]
    dense = muscle["m_Clip"]["data"]["m_DenseClip"]
    assert dense["m_CurveCount"] == dense_curves + 7 and len(dense["m_SampleArray"]) == 4 * (dense_curves + 7)
    assert tree["m_MuscleClipSize"] == ms.MUSCLE_FIXED + 4 * (4 * (dense_curves + 7) + 3) + 8 * (dense_curves + 10)
    assert muscle["m_ValueArrayDelta"][dense_curves]["m_Stop"] == pytest.approx(samples[-1, 0])
    assert muscle["m_ValueArrayDelta"][dense_curves + 3]["m_Start"] == pytest.approx(1.0, abs=1e-6)  # the new w
    nodes = {zlib.crc32(p.encode()): k for k, p in enumerate(("bone_root/Skirt", "bone_root/Bip001", "bone_root/Euler"))}
    decoded = decode_clip(tree)
    tracks = bind_tracks(tree["m_ClipBindingConstant"]["genericBindings"], decoded.curves, nodes)
    assert np.allclose(np.array(tracks[0][2]).T, samples, atol=1e-6)
    assert np.allclose(np.array(tracks[0][1]).T, shift, atol=1e-6)
    assert [part[0] for part in tracks[1][1]] == [1.0, 2.0, 3.0]  # the constants still read as before
    if dense_curves:
        assert np.allclose(np.array(tracks[2][4]).T, [[f * 10 + c for c in range(3)] for f in range(4)])


def test_a_clip_of_another_layout_is_left_alone():
    tree = _clip()
    tree["m_MuscleClipSize"] += 4  # a size this writer cannot account for
    assert ms.dense_grid(tree) is None
    with pytest.raises(ValueError):
        ms.add_dense_curves(tree, [_binding("bone_root/Skirt", 2)], np.zeros((4, 4)))
    split = _clip(3)
    split["m_ClipBindingConstant"]["genericBindings"][0]["attribute"] = 2  # a binding across the dense/constant seam
    assert ms.dense_grid(split) is None


# --- the read-back of an edited clip ----------------------------------------------------------------------

def _edited(dense_curves):
    """A clip as Rule B edits it: (the snapshot before, the tree after, the number of added curves)."""
    tree = _clip(dense_curves)
    before = ma.clip_snapshot(tree)
    template = tree["m_ClipBindingConstant"]["genericBindings"][0]
    samples = np.array([[0.0, 0.0, math.sin(a / 2), math.cos(a / 2), 0.0, 0.01 * f, 0.02]
                        for f, a in enumerate((0.0, 0.1, 0.2, 0.3))])
    ms.add_dense_curves(tree, [ms.rotation_binding(template, "bone_root/Skirt"),
                               ms.position_binding(template, "bone_root/Skirt")], samples)
    return before, tree, 7


@pytest.mark.parametrize("dense_curves", [0, 3])
def test_a_clip_that_kept_every_curve_reads_back_as_edited(dense_curves):
    before, tree, added = _edited(dense_curves)
    assert ma.clip_edit_error(before, tree, added) is None


@pytest.mark.parametrize("change,named", [
    (lambda t: t["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"].__setitem__(1, 2.5), "curve 4 changed"),
    (lambda t: t["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_SampleArray"].__setitem__(12, 99.0),
     "curve 2 changed"),  # frame 1 of an old dense curve: ten curves a frame now
    (lambda t: t["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"].pop(), "curves"),
    (lambda t: t.__setitem__("m_MuscleClipSize", t["m_MuscleClipSize"] + 4), "muscle clip size"),
    (lambda t: (t["m_MuscleClip"]["m_ValueArrayDelta"].pop(), t.__setitem__("m_MuscleClipSize", ms.muscle_size(t))),
     "12 value deltas"),
    (lambda t: t["m_ClipBindingConstant"]["genericBindings"].pop(), "bindings and curves differ"),
])
def test_a_clip_that_lost_or_moved_a_curve_is_named(change, named):
    before, tree, added = _edited(3)
    change(tree)
    assert ma.clip_edit_error(before, tree, added).startswith(named)


def test_a_clip_with_other_curves_than_were_added_is_named():
    before, tree, added = _edited(3)
    assert ma.clip_edit_error(before, tree, added - 1).startswith("curves")


def test_dense_values_compare_to_a_millionth_and_stored_values_exactly():
    before, tree, added = _edited(3)
    clip = tree["m_MuscleClip"]["m_Clip"]["data"]
    clip["m_DenseClip"]["m_SampleArray"][12] += 5e-7
    assert ma.clip_edit_error(before, tree, added) is None
    clip["m_ConstantClip"]["data"][0] += 5e-7
    assert ma.clip_edit_error(before, tree, added) == "curve 3 changed"


# Scalar reference for the solver arithmetic before batching constant work.

def _reference_joint(self, world: np.ndarray, base: np.ndarray, fixed: np.ndarray, grid: np.ndarray, frame: ms.Frame) -> None:
    """All chains at once: Gauss-Newton steps on the roots' turns (rotation vectors about their heads) and the
    heads' shifts that carry the cloth inside the legs out, hold the cloth already clear, and keep the cloth's edges
    from growing."""
    c, roots = self.cloth, self.roots
    if not roots:
        return
    count, width = len(c.rest), 6 * len(roots)
    everything = np.arange(count)
    homogeneous = np.c_[c.rest, np.ones(count)]
    edges = c.edges
    for _ in range(ms.ITERATIONS):
        matrices = world @ self.inverse
        if len(fixed):
            matrices = np.concatenate([matrices, fixed])
        # each influence's place, the vertices, and each root's share of them
        places = np.einsum("vkij,vj->vki", matrices[c.joints], homogeneous, optimize=True)[..., :3]
        points = (c.weights[..., None] * places).sum(axis=1)
        target, moved = self.push(points, everything, grid, frame)
        if not moved.any():
            break
        partial = np.einsum("vkr,vki->rvi", self.parts, places, optimize=True)
        heads = np.array([world[r][:3, 3] for r in roots])
        arm = partial - self.part_share.T[:, :, None] * heads[:, None, :]  # (roots, vertices, 3)
        # a vertex moves by sum_r (omega_r x arm_r + share_r shift_r) = sum_r (-[arm_r]x omega_r + share_r shift_r)
        jacobian = np.zeros((count, 3, len(roots), 6))
        jacobian[:, 0, :, 1], jacobian[:, 0, :, 2] = arm[:, :, 2].T, -arm[:, :, 1].T
        jacobian[:, 1, :, 0], jacobian[:, 1, :, 2] = -arm[:, :, 2].T, arm[:, :, 0].T
        jacobian[:, 2, :, 0], jacobian[:, 2, :, 1] = arm[:, :, 1].T, -arm[:, :, 0].T
        for axis in range(3):
            jacobian[:, axis, :, 3 + axis] = self.part_share
        jacobian = jacobian.reshape(3 * count, width)
        weight = np.repeat(np.where(moved, 1.0, ms.HOLD), 3)
        residual = np.where(moved[:, None], target - points, 0.0).ravel()
        a = (jacobian * weight[:, None]).T @ jacobian
        b = jacobian.T @ (weight * residual)
        if len(edges):
            d = points[edges[:, 1]] - points[edges[:, 0]]
            length = np.linalg.norm(d, axis=1)
            unit = d / np.maximum(length, 1e-12)[:, None]
            # an edge grows by unit . (move_b - move_a)
            #   = sum_r omega_r . ((arm_rb - arm_ra) x unit) + shift_r . unit (share_rb - share_ra)
            turn = np.cross(arm[:, edges[:, 1]] - arm[:, edges[:, 0]], unit[None])
            shift = (self.part_share[edges[:, 1]] - self.part_share[edges[:, 0]]).T[:, :, None] * unit[None]
            rows = np.concatenate([turn, shift], axis=2).transpose(1, 0, 2).reshape(len(edges), width)
            stretch = np.minimum(0.0, self.lengths - length)  # pull grown edges back, keep the others
            a += ms.LENGTH_WEIGHT * rows.T @ rows
            b += ms.LENGTH_WEIGHT * rows.T @ stretch
        # a shift costs against the turns: the heads move only where a turn cannot reach
        shifted = np.array([world[r][:3, 3] - base[r][:3, 3] for r in roots])
        for k in range(len(roots)):
            block = slice(6 * k + 3, 6 * k + 6)
            a[block, block] += ms.SHIFT_COST * np.eye(3)
            b[block] -= ms.SHIFT_COST * shifted[k]
        a += ms.DAMPING * np.eye(width)
        step = np.linalg.solve(a, b).reshape(len(roots), 6)
        if float(np.abs(step[:, :3]).max()) < ms.MIN_ANGLE and float(np.abs(step[:, 3:]).max()) < ms.MIN_ANGLE * 0.1:
            break
        for k, r in enumerate(roots):
            turned = ms.from_vector(step[k, :3]) @ world[r][:3, :3]
            total = turned @ base[r][:3, :3].T
            if ms.angle_of(total) > ms.MAX_ANGLE:
                turned = ms.axis_angle(ms.rotation_axis(total), ms.MAX_ANGLE) @ base[r][:3, :3]
            world[r][:3, :3] = turned
            moved = shifted[k] + step[k, 3:]
            length = float(np.linalg.norm(moved))
            if length > ms.MAX_SHIFT:
                moved *= ms.MAX_SHIFT / length
            world[r][:3, 3] = base[r][:3, 3] + moved
            self._carry(world, r)
