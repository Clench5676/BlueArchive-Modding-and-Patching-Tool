"""Skirts and hems of a switched-in costume (gameassets.model_hems, BL-118)."""

import struct
from types import SimpleNamespace

import numpy as np
import pytest
from UnityPy.helpers.PackedBitVector import unpack_ints

from bmpt.gameassets import model_hems as hm
from bmpt.gameassets.model import compressed_skin


def _rotation(axis, angle):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    x, y, z = axis
    c, s, t = np.cos(angle), np.sin(angle), 1 - np.cos(angle)
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


def _matrix(rotation, translation):
    m = np.eye(4)
    m[:3, :3] = rotation
    m[:3, 3] = translation
    return m


# --- weights -------------------------------------------------------------------------------------------------------

def test_fit_recovers_known_weights_and_single_bones():
    rng = np.random.default_rng(7)
    points = rng.normal(size=(3, 3)) * 0.2
    truth = np.array([[0.2, 0.5, 0.3, 0.0, 0.0],   # a blend of three bones
                      [0.0, 0.0, 1.0, 0.0, 0.0],   # follows one bone only
                      [0.6, 0.0, 0.0, 0.0, 0.4]])
    normal = hm.Normal(len(points), 5)
    homogeneous = np.c_[points, np.ones(len(points))]
    for _ in range(40):
        bones = np.array([_matrix(_rotation(rng.normal(size=3), rng.uniform(-1, 1)), rng.normal(size=3) * 0.1)
                          for _ in range(5)])
        basis = np.stack([(homogeneous @ m.T)[:, :3] for m in bones], axis=2)
        normal.add(basis, np.einsum("nrb,nb->nr", basis, truth))
    weights, residual = hm.fit_simplex(normal)
    assert weights == pytest.approx(truth, abs=1e-6)
    assert (weights >= 0).all() and weights.sum(axis=1) == pytest.approx(1.0)
    assert residual == pytest.approx(0.0, abs=1e-4)
    assert np.count_nonzero(weights[1]) == 1


def test_fit_stays_non_negative_when_the_best_plain_solution_is_not():
    # the target lies beyond bone 0 away from bone 1: the unconstrained answer puts a negative weight on bone 1
    normal = hm.Normal(1, 2)
    for t in np.linspace(0.1, 1.0, 10):
        basis = np.array([[[t, 0.0], [0.0, 0.0], [0.0, 0.0]]])
        normal.add(basis, np.array([[1.5 * t, 0.0, 0.0]]))
    weights, _ = hm.fit_simplex(normal)
    assert weights[0] == pytest.approx([1.0, 0.0])


def test_densify_merges_bones_driving_the_same_slot_bone():
    # four source bones on two slot bones: nothing is lost when four influences are kept
    columns = np.array([[0, 0, 1, 1], [2, 2, 2, 2]])
    weights = np.array([[0.4, 0.1, 0.3, 0.2], [0.25, 0.25, 0.25, 0.25]])
    dense = hm.densify(columns, weights, 3)
    assert dense == pytest.approx(np.array([[0.5, 0.5, 0.0], [0.0, 0.0, 1.0]]))
    joints, kept = hm.keep_four(dense)
    assert kept.sum(axis=1) == pytest.approx(1.0)
    assert sorted(zip(joints[0][:2].tolist(), kept[0][:2].tolist())) == [(0, 0.5), (1, 0.5)]
    # unused influences repeat the first bone with weight 0
    assert joints[1].tolist() == [2, 2, 2, 2] and kept[1].tolist() == [1.0, 0.0, 0.0, 0.0]


def test_keep_four_drops_the_smallest_and_renormalises():
    dense = np.array([[0.05, 0.3, 0.25, 0.2, 0.2]])
    joints, weights = hm.keep_four(dense)
    assert joints[0].tolist() == [1, 2, 3, 4]
    assert weights[0] == pytest.approx(np.array([0.3, 0.25, 0.2, 0.2]) / 0.95)


def test_ramp_moves_nothing_at_zero_or_without_rigid_weight():
    height = np.linspace(-0.5, 2.5, 7)
    near, left, right = np.ones(7), np.linspace(0, 1, 7), np.linspace(1, 0, 7)
    dense = np.tile([0.6, 0.4, 0.0, 0.0, 0.0, 0.0], (7, 1))  # pelvis, a skirt bone's pelvis share, legs
    rigid = np.zeros_like(dense)
    rigid[3:, 1] = 0.4  # only the lower rows have a rigid part
    fraction, moves = hm.ramp(height, near, left, right, 0.0, 1.0, (1.0, 2.0))
    assert hm.reweight(dense, rigid, fraction, moves, [2, 3, 4, 5]) == pytest.approx(dense)
    fraction, moves = hm.ramp(height, near, left, right, 0.7, 0.3, (0.9, 1.3))
    assert moves.sum(axis=1) == pytest.approx(1.0)
    out = hm.reweight(dense, rigid, fraction, moves, [2, 3, 4, 5])
    assert out[:3] == pytest.approx(dense[:3])
    assert out.sum(axis=1) == pytest.approx(1.0)
    assert out[6, 1] == pytest.approx(0.4 * 0.3)  # 70 % of the rigid part moved below the knee
    assert out[6, 4] + out[6, 5] == pytest.approx(0.4 * 0.7)  # to the calves


# --- measures ------------------------------------------------------------------------------------------------------

def _skirt(radius=0.1, low=-0.3, high=0.0, sides=24, rings=6):
    """A cylinder of triangles around the vertical (+Y) axis through the origin."""
    points, triangles = [], []
    for r in range(rings + 1):
        y = high + (low - high) * r / rings
        for k in range(sides):
            a = 2 * np.pi * k / sides
            points.append((radius * np.cos(a), y, radius * np.sin(a)))
    for r in range(rings):
        for k in range(sides):
            a, b = r * sides + k, r * sides + (k + 1) % sides
            c, d = a + sides, b + sides
            triangles += [(a, b, c), (b, d, c)]
    return np.array(points), np.array(triangles)


def test_cover_tells_legs_inside_outside_and_below_the_cloth():
    cloth, triangles = _skirt()
    legs = np.array([[0.05, -0.1, 0.0],    # inside
                     [0.13, -0.1, 0.0],    # 3 cm outside
                     [0.105, -0.2, 0.0],   # 5 mm outside: not deep
                     [0.05, -0.5, 0.0],    # below the hem
                     [0.0, -0.1, 0.0]])    # on the axis
    points = np.vstack([cloth, legs])
    index = np.arange(len(cloth), len(points))
    state, depth = hm.cover(points, index, triangles, np.zeros(3), np.array([0.0, 1.0, 0.0]))
    assert state.tolist() == [1, -1, -1, 0, 0]
    assert depth[1] == pytest.approx(0.13 - 0.1 * np.cos(np.pi / 24), abs=2e-3)
    assert hm.deep_share(state, depth) == pytest.approx(20.0)  # one of five more than 1 cm outside


def test_cover_follows_a_tilted_axis():
    cloth, triangles = _skirt()
    rotation = _rotation((0, 0, 1), 0.6)
    points = np.vstack([cloth, [[0.05, -0.1, 0.0], [0.13, -0.1, 0.0]]]) @ rotation.T + np.array([1.0, 2.0, 3.0])
    state, _ = hm.cover(points, np.array([len(cloth), len(cloth) + 1]), triangles, np.array([1.0, 2.0, 3.0]),
                        rotation @ np.array([0.0, 1.0, 0.0]))
    assert state.tolist() == [1, -1]


def test_stretch_p99_counts_growth():
    rest = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    edges = np.array([[0, 1], [0, 2]])
    assert hm.stretch_p99(rest, rest, edges) == pytest.approx(1.0)
    assert hm.stretch_p99(rest, rest * np.array([2, 1, 1]), edges) == pytest.approx(2.0, rel=2e-2)
    assert hm.stretch_p99(rest, rest, np.zeros((0, 2), int)) == 1.0


def test_choose_keeps_the_limit_the_clip_rule_and_ties():
    rigid = hm.Score("rigid", {"a": 20.0, "b": 0.0}, 1.0)
    over_limit = hm.Score("short 1.0", {"a": 0.0, "b": 0.0}, 3.0)
    worse_clip = hm.Score("fit 0.5", {"a": 0.0, "b": 6.0}, 1.2)
    good = hm.Score("blend 0.5", {"a": 8.0, "b": 2.0}, 1.4)
    assert hm.choose([rigid, over_limit, worse_clip, good], 1.5).rule == "blend 0.5"
    # within rigid's own stretch when that is above the limit
    stretched = hm.Score("rigid", {"a": 20.0, "b": 0.0}, 4.0)
    assert hm.choose([stretched, over_limit, worse_clip], 1.5).rule == "short 1.0"
    # equal means go to rigid, then to less stretch
    assert hm.choose([rigid, hm.Score("blend 0.5", {"a": 20.0, "b": 0.0}, 1.1)], 1.5).rule == "rigid"
    tie = [rigid, hm.Score("short 0.5", {"a": 5.0, "b": 0.0}, 1.4), hm.Score("fit 1.0", {"a": 5.0, "b": 0.0}, 1.2)]
    assert hm.choose(tie, 1.5).rule == "fit 1.0"


# --- vertex data ---------------------------------------------------------------------------------------------------

def _channel(stream=0, offset=0, fmt=0, dimension=0):
    return {"stream": stream, "offset": offset, "format": fmt, "dimension": dimension}


def _vertex_mesh(count=5, seed=3):
    """A mesh typetree laid out like the game's bodies: positions, normals, tangents (stream 0), colour, two UVs
    (stream 1), four float weights and four UInt32 indices (stream 2); streams start on 16 bytes."""
    channels = [_channel()] * 14
    channels[0], channels[1], channels[2] = _channel(0, 0, 0, 3), _channel(0, 12, 0, 3), _channel(0, 24, 0, 4)
    channels[3], channels[4], channels[5] = _channel(1, 0, 2, 4), _channel(1, 4, 1, 2), _channel(1, 8, 0, 2)
    channels[12], channels[13] = _channel(2, 0, 0, 4), _channel(2, 16, 10, 4)
    size = 0
    for stride in (40, 16, 32):
        size = (size + count * stride + 15) & ~15
    raw = bytearray(np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes())
    start2 = ((count * 40 + 15) & ~15) + count * 16
    start2 = (start2 + 15) & ~15
    for v in range(count):
        struct.pack_into("<4f", raw, start2 + v * 32, 0.5, 0.5, 0.0, 0.0)
        struct.pack_into("<4I", raw, start2 + v * 32 + 16, v, v + 1, 0, 0)
    return {"m_VertexData": {"m_VertexCount": count, "m_Channels": channels, "m_DataSize": bytes(raw)},
            "m_CompressedMesh": {"m_Weights": {"m_NumItems": 0, "m_Data": [], "m_BitSize": 0}},
            "m_StreamData": {"offset": 0, "size": 0, "path": ""}}


def test_vertex_skin_round_trip_leaves_every_other_byte():
    mesh = _vertex_mesh()
    before = mesh["m_VertexData"]["m_DataSize"]
    layout = hm.store_of(mesh)
    assert isinstance(layout, hm.SkinLayout)
    joints, weights = hm.read_skin(mesh, layout)
    assert joints[:, :2].tolist() == [[v, v + 1] for v in range(5)] and weights[:, :2].tolist() == [[0.5, 0.5]] * 5
    rows = np.array([1, 3])
    new_joints = np.array([[7, 8, 9, 7], [2, 2, 2, 2]])
    new_weights = np.array([[0.5, 0.25, 0.25, 0.0], [1.0, 0.0, 0.0, 0.0]])
    hm.write_weights(mesh, layout, rows, new_joints, new_weights)
    joints2, weights2 = hm.read_skin(mesh, layout)
    assert joints2[rows].tolist() == new_joints.tolist() and weights2[rows].tolist() == new_weights.tolist()
    keep = [0, 2, 4]
    assert joints2[keep].tolist() == joints[keep].tolist() and weights2[keep].tolist() == weights[keep].tolist()
    after = mesh["m_VertexData"]["m_DataSize"]
    start = layout.weights[0]
    touched = {start + r * 32 + k for r in rows.tolist() for k in range(32)}
    assert len(after) == len(before)
    assert all(a == b for i, (a, b) in enumerate(zip(before, after)) if i not in touched)


@pytest.mark.parametrize("change", [
    lambda m: m["m_CompressedMesh"]["m_Weights"].update(m_NumItems=4),
    lambda m: m["m_StreamData"].update(size=100),
    lambda m: m["m_VertexData"]["m_Channels"][12].update(dimension=2),
    lambda m: m["m_VertexData"]["m_Channels"][12].update(format=2),
    lambda m: m["m_VertexData"].update(m_DataSize=m["m_VertexData"]["m_DataSize"][:-32]),
])
def test_vertex_skin_refuses_what_it_cannot_write(change):
    mesh = _vertex_mesh()
    change(mesh)
    assert hm.skin_layout(mesh) is None


def _packed(values, bits):
    return {"m_NumItems": len(values), "m_BitSize": bits, "m_Data": list(hm.pack_bits(values, bits))}


def test_packed_bits_read_as_unitypy_reads_them():
    values = [0, 31, 5, 17, 30, 1, 2, 3, 29]
    for bits in (5, 7, 12):
        packed = _packed(values, bits)
        assert hm.unpack_bits(packed) == values
        assert unpack_ints(SimpleNamespace(**{**packed, "m_Data": bytes(packed["m_Data"])})) == values


def _compressed_mesh(rows):
    """A compressed mesh typetree whose skin stores ``rows`` (per vertex: weight items, bone items)."""
    w_items = [w for items, _ in rows for w in items]
    b_items = [b for _, items in rows for b in items]
    return {"m_CompressedMesh": {"m_Vertices": {"m_NumItems": 3 * len(rows)},
                                 "m_Weights": _packed(w_items, 5), "m_BoneIndices": _packed(b_items, 6)},
            "m_VertexData": {"m_VertexCount": 0, "m_Channels": [], "m_DataSize": b""},
            "m_StreamData": {"offset": 0, "size": 0, "path": ""}}


def _decode(mesh):
    compressed = mesh["m_CompressedMesh"]
    weights, joints = compressed_skin(hm.unpack_bits(compressed["m_Weights"]),
                                      hm.unpack_bits(compressed["m_BoneIndices"]),
                                      compressed["m_Vertices"]["m_NumItems"] // 3)
    return np.array(joints), np.array(weights)


def test_compressed_skin_rewrites_only_the_given_vertices():
    stored = [([31], [3]), ([20, 11], [1, 2]), ([10, 10, 5], [4, 5, 6, 7]), ([16, 15], [9, 8])]
    mesh = _compressed_mesh(stored)
    layout = hm.store_of(mesh)
    assert isinstance(layout, hm.CompressedLayout) and layout.rows == stored
    joints, weights = _decode(mesh)
    rows = np.array([0, 2])
    new_joints = np.array([[40, 41, 42, 43], [5, 5, 5, 5]])
    new_weights = np.array([[0.4, 0.3, 0.2, 0.1], [1.0, 0.0, 0.0, 0.0]])
    hm.write_weights(mesh, layout, rows, new_joints, new_weights)
    joints2, weights2 = _decode(mesh)
    assert weights2[0] * 31 == pytest.approx([13, 9, 6, 3]) and joints2[0].tolist() == [40, 41, 42, 43]
    assert weights2[2].tolist() == [1.0, 0.0, 0.0, 0.0] and joints2[2][0] == 5
    for row in (1, 3):
        assert joints2[row].tolist() == joints[row].tolist() and weights2[row].tolist() == weights[row].tolist()
    assert mesh["m_CompressedMesh"]["m_BoneIndices"]["m_BitSize"] == 6
    assert hm.compressed_layout(mesh).rows[1] == stored[1]


def test_compressed_skin_widens_the_bone_index_bits():
    mesh = _compressed_mesh([([31], [3]), ([31], [4])])
    layout = hm.store_of(mesh)
    hm.write_weights(mesh, layout, np.array([1]), np.array([[200, 200, 200, 200]]), np.array([[1.0, 0, 0, 0]]))
    assert mesh["m_CompressedMesh"]["m_BoneIndices"]["m_BitSize"] == 8
    joints, weights = _decode(mesh)
    assert joints[:, 0].tolist() == [3, 200] and weights[:, 0].tolist() == [1.0, 1.0]


def test_compressed_skin_refuses_items_that_do_not_make_the_vertices():
    mesh = _compressed_mesh([([31], [3]), ([20, 11], [1, 2])])
    mesh["m_CompressedMesh"]["m_Vertices"]["m_NumItems"] = 9  # three vertices claimed, two stored
    assert hm.compressed_layout(mesh) is None


def test_quantize_sums_to_31():
    for weights in ([0.4, 0.3, 0.2, 0.1], [0.33, 0.33, 0.34, 0.0], [0.97, 0.01, 0.01, 0.01], [1.0, 0, 0, 0]):
        steps = hm.quantize(np.array(weights))
        assert sum(steps) == 31 and all(s >= 0 for s in steps)
