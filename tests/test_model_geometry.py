"""Vertex positions, normals and tangents rewritten in a Mesh typetree (gameassets.model_geometry, BL-128)."""

import struct
from types import SimpleNamespace

import numpy as np
import pytest
from UnityPy.helpers.PackedBitVector import unpack_floats, unpack_ints

from bmpt.gameassets import model_geometry as mg
from bmpt.gameassets.model_hems import pack_bits


def _turn(degrees):
    a = np.radians(degrees)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])


def _plain_mesh(positions, normals, tangents):
    """One stream: position, normal, tangent (float32), then a colour channel the rewrite must leave alone."""
    count = len(positions)
    raw = bytearray()
    for p, n, t in zip(positions, normals, tangents):
        raw += struct.pack("<3f3f4f4B", *p, *n, *t, 1, 2, 3, 4)
    raw += b"\0" * ((-len(raw)) % 16)
    channel = lambda offset, fmt, dim: {"stream": 0, "offset": offset, "format": fmt, "dimension": dim}  # noqa: E731
    channels = [channel(0, 0, 3), channel(12, 0, 3), channel(24, 0, 4), channel(40, 2, 4)]
    channels += [channel(0, 0, 0)] * 10
    return {"m_VertexData": {"m_VertexCount": count, "m_Channels": channels, "m_DataSize": bytes(raw)},
            "m_CompressedMesh": {"m_Vertices": {"m_NumItems": 0}}, "m_StreamData": {"size": 0}}


def _read_plain(mesh):
    raw = mesh["m_VertexData"]["m_DataSize"]
    rows = [struct.unpack_from("<3f3f4f4B", raw, 44 * v) for v in range(mesh["m_VertexData"]["m_VertexCount"])]
    return (np.array([r[:3] for r in rows]), np.array([r[3:6] for r in rows]), np.array([r[6:10] for r in rows]),
            [r[10:] for r in rows])


def test_plain_vertices_move_and_their_normals_and_tangents_turn():
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0]], float)
    normals = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    tangents = np.array([[0, 1, 0, 1], [1, 0, 0, -1], [1, 0, 0, 1]], float)
    mesh = _plain_mesh(positions, normals, tangents)
    layout = mg.geometry_of(mesh)
    assert isinstance(layout, mg.Plain) and layout.count == 3
    moved = positions + [0.5, -0.25, 1.0]
    linear = np.array([_turn(90), _turn(90), np.eye(3)])
    mg.write_geometry(mesh, layout, moved, linear)
    p, n, t, colours = _read_plain(mesh)
    assert np.allclose(p, moved, atol=1e-6)
    assert np.allclose(n, [[0, 1, 0], [-1, 0, 0], [0, 0, 1]], atol=1e-6)
    assert np.allclose(t, [[-1, 0, 0, 1], [0, 1, 0, -1], [1, 0, 0, 1]], atol=1e-6)
    assert colours == [(1, 2, 3, 4)] * 3
    assert mesh["m_LocalAABB"]["m_Center"] == pytest.approx({"x": 1.0, "y": 0.75, "z": 1.0})
    assert mesh["m_LocalAABB"]["m_Extent"] == pytest.approx({"x": 0.5, "y": 1.0, "z": 0.0})


def test_a_mirroring_transform_flips_the_tangent_handedness():
    mesh = _plain_mesh([[0, 0, 0]], [[0, 0, 1]], [[1, 0, 0, 1]])
    mirror = np.diag([-1.0, 1.0, 1.0])[None]
    mg.write_geometry(mesh, mg.geometry_of(mesh), np.zeros((1, 3)), mirror)
    _, _, t, _ = _read_plain(mesh)
    assert np.allclose(t, [[-1, 0, 0, -1]], atol=1e-6)


def _packed(values, bits, start, span):
    steps = np.clip(np.rint((np.asarray(values, float) - start) / span * ((1 << bits) - 1)), 0, (1 << bits) - 1)
    return {"m_NumItems": len(values), "m_BitSize": bits, "m_Start": start, "m_Range": span,
            "m_Data": pack_bits([int(s) for s in steps], bits)}


def _signs(values):
    return {"m_NumItems": len(values), "m_BitSize": 1, "m_Data": pack_bits([int(v) for v in values], 1)}


def _vector(packed):
    return SimpleNamespace(**packed)


def test_compressed_vertices_are_packed_as_unity_reads_them():
    positions = np.array([[0, 0, 0], [0.4, -0.2, 0.1], [-0.3, 0.6, 0.2]])
    normals = np.array([[0, 0, 1], [0.6, 0, -0.8], [0, 1, 0]])
    tangents = np.array([[1, 0, 0, 1], [0, 0.6, 0.8, -1], [0, 0, -1, 1]])
    compressed = {
        "m_Vertices": _packed(positions.reshape(-1), 20, -0.3, 0.9),
        "m_Normals": _packed(normals[:, :2].reshape(-1), 10, -1.0, 2.0),
        "m_NormalSigns": _signs(normals[:, 2] >= 0),
        "m_Tangents": _packed(tangents[:, :2].reshape(-1), 10, -1.0, 2.0),
        "m_TangentSigns": _signs(np.c_[tangents[:, 2] >= 0, tangents[:, 3] > 0].reshape(-1)),
    }
    mesh = {"m_CompressedMesh": compressed, "m_VertexData": {"m_VertexCount": 0, "m_Channels": []},
            "m_StreamData": {"size": 0}}
    layout = mg.geometry_of(mesh)
    assert isinstance(layout, mg.Packed) and layout.count == 3
    moved = positions * 2 + [1.0, 0.0, -1.0]
    linear = np.array([_turn(90)] * 3)
    mg.write_geometry(mesh, layout, moved, linear)
    got = np.array(unpack_floats(_vector(compressed["m_Vertices"]), shape=(3,)))
    assert np.allclose(got, moved, atol=2e-6)  # 20 bits over the new range
    xy = np.array(unpack_floats(_vector(compressed["m_Normals"]), shape=(2,)))
    z_positive = np.array(unpack_ints(_vector(compressed["m_NormalSigns"]))) > 0
    want = normals @ _turn(90).T
    assert np.allclose(xy, want[:, :2], atol=3e-3)
    assert list(z_positive) == list(want[:, 2] >= 0)
    txy = np.array(unpack_floats(_vector(compressed["m_Tangents"]), shape=(2,)))
    tsigns = np.array(unpack_ints(_vector(compressed["m_TangentSigns"]), shape=(2,)))
    want = tangents[:, :3] @ _turn(90).T
    assert np.allclose(txy, want[:, :2], atol=3e-3)
    assert [bool(s) for s in tsigns[:, 0]] == list(want[:, 2] >= 0)
    assert [bool(s) for s in tsigns[:, 1]] == [True, False, True]


def test_meshes_with_streamed_data_or_other_formats_are_not_rewritten():
    mesh = _plain_mesh([[0, 0, 0]], [[0, 0, 1]], [[1, 0, 0, 1]])
    assert mg.geometry_of({**mesh, "m_StreamData": {"size": 64}}) is None
    mesh["m_VertexData"]["m_Channels"][0] = {"stream": 0, "offset": 0, "format": 2, "dimension": 3}
    assert mg.geometry_of(mesh) is None


def test_blend_shape_deltas_turn_with_their_vertex():
    mesh = _plain_mesh([[0, 0, 0], [1, 0, 0]], [[0, 0, 1]] * 2, [[1, 0, 0, 1]] * 2)
    mesh["m_Shapes"] = {"vertices": [{"index": 1, "vertex": {"x": 1.0, "y": 0.0, "z": 0.0},
                                      "normal": {"x": 0.0, "y": 0.0, "z": 0.0}, "tangent": {"x": 0.0, "y": 0.0, "z": 0.0}}]}
    mg.write_geometry(mesh, mg.geometry_of(mesh), np.zeros((2, 3)), np.array([np.eye(3), _turn(90)]))
    assert mesh["m_Shapes"]["vertices"][0]["vertex"] == pytest.approx({"x": 0.0, "y": 1.0, "z": 0.0}, abs=1e-9)
