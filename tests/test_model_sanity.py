"""The sanity pass and the named refusals of a costume build (gameassets.model_adapt, BL-139)."""

import struct

import numpy as np
import pytest

from bmpt.errors import ServiceError
from bmpt.gameassets import model_adapt as ma
from bmpt.gameassets import model_geometry as mg


def _bounds(centre=(0.0, 1.0, 0.0), extent=(0.5, 1.0, 0.3)):
    return {"m_Center": dict(zip("xyz", centre)), "m_Extent": dict(zip("xyz", extent))}


def _written(**change):
    """A valid mesh of four vertices on three bones."""
    fields = dict(
        name="body", vertices=np.array([[0, 0, 0], [0, 1, 0], [0, 2, 0], [0.5, 1, 0]], float),
        linear=np.tile(np.eye(3), (4, 1, 1)),
        joints=np.array([[0, 1, 0, 0], [1, 2, 0, 0], [2, 2, 0, 0], [0, 0, 0, 0]]),
        weights=np.array([[0.5, 0.5, 0, 0], [0.25, 0.75, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0]]),
        bindposes=np.tile(np.eye(4), (3, 1, 1)), bones=["Bip001", "Bip001 Spine", "Bip001 Head"], submeshes=2,
        source_totals=np.array([1.0, 1.0, 1.0, 0.0]), source_materials=[1, 1])
    fields.update(change)
    return ma.Written(**fields)


def _rendered(**change):
    fields = dict(part=0, bones=3, materials=[11, 12], bounds=_bounds())
    fields.update(change)
    return ma.Rendered(**fields)


def _reason(meshes, renderers):
    with pytest.raises(ServiceError) as caught:
        ma._sanity(meshes, renderers)
    assert caught.value.code == "source_unsupported"
    return caught.value.params["source"]


def test_sanity_passes_a_valid_build():
    ma._sanity([_written()], [_rendered()])
    ma._sanity([_written(), _written(name="hair")], [_rendered(), _rendered(part=1, materials=[13])])
    ma._sanity([], [])


def test_sanity_accepts_a_mesh_without_materials_or_with_trailing_nulls():
    ma._sanity([_written(submeshes=2, source_materials=[1, 0])], [_rendered(materials=[11, 0])])
    ma._sanity([_written()], [_rendered(materials=[0, 0])])


@pytest.mark.parametrize("field,value", [
    ("bindposes", np.where(np.eye(4) == 1, np.nan, 0.0)[None].repeat(3, axis=0)),
    ("vertices", np.array([[0, 0, 0], [0, np.inf, 0], [0, 2, 0], [0.5, 1, 0]])),
    ("linear", np.tile(np.full((3, 3), np.nan), (4, 1, 1))),
])
def test_sanity_refuses_numbers_that_are_not_finite(field, value):
    assert _reason([_written(**{field: value})], [_rendered()]) == "invalid data"


@pytest.mark.parametrize("centre,extent", [((np.nan, 0, 0), (1, 1, 1)), ((0, 0, 0), (np.inf, 1, 1))])
def test_sanity_refuses_bounds_that_are_not_finite(centre, extent):
    assert _reason([_written()], [_rendered(bounds=_bounds(centre, extent))]) == "invalid data"


def test_sanity_refuses_a_bindpose_out_of_range_and_names_the_bone():
    poses = np.tile(np.eye(4), (3, 1, 1))
    poses[2, 0, 3] = -2 * ma.BINDPOSE_LIMIT
    with pytest.raises(ServiceError) as caught:
        ma._sanity([_written(bindposes=poses)], [_rendered()])
    assert caught.value.params["source"] == "bindpose out of range" and caught.value.params["bone"] == "Bip001 Head"
    poses[2, 0, 3] = ma.BINDPOSE_LIMIT  # on the bound
    ma._sanity([_written(bindposes=poses)], [_rendered()])


@pytest.mark.parametrize("centre,extent", [((0, 0, 0), (60, 1, 1)), ((0, 0, -80), (1, 1, 1)), ((30, 0, 0), (21, 1, 1))])
def test_sanity_refuses_vertices_far_from_the_root(centre, extent):
    assert _reason([_written()], [_rendered(bounds=_bounds(centre, extent))]) == "vertices out of range"


def test_sanity_refuses_a_renderer_whose_bones_differ_from_the_bindposes():
    assert _reason([_written()], [_rendered(bones=2)]) == "skin inconsistent"
    assert _reason([_written()], [_rendered(bones=4)]) == "skin inconsistent"


def test_sanity_refuses_a_bone_index_past_the_bindposes():
    joints = np.array([[0, 1, 0, 0], [1, 3, 0, 0], [2, 2, 0, 0], [0, 0, 0, 0]])
    assert _reason([_written(joints=joints)], [_rendered()]) == "skin inconsistent"
    joints[1, 1] = 2  # a bone index on a zero weight is not read
    ma._sanity([_written(joints=joints)], [_rendered()])
    joints = np.array([[0, 1, 0, 0], [1, 7, 0, 0], [2, 2, 0, 0], [0, 0, 0, 0]])
    weights = np.array([[0.5, 0.5, 0, 0], [1.0, 0.0, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0]])
    ma._sanity([_written(joints=joints, weights=weights)], [_rendered()])


def test_sanity_refuses_weights_that_do_not_add_up():
    weights = np.array([[0.5, 0.5, 0, 0], [0.25, 0.7, 0, 0], [1, 0, 0, 0], [0, 0, 0, 0]])
    assert _reason([_written(weights=weights)], [_rendered()]) == "skin inconsistent"
    weights[1] = [0.25, 0.7505, 0, 0]  # within the tolerance
    ma._sanity([_written(weights=weights)], [_rendered()])


def test_sanity_allows_a_vertex_without_weight_only_where_the_source_had_none():
    assert _reason([_written(source_totals=np.ones(4))], [_rendered()]) == "skin inconsistent"
    assert _reason([_written(source_totals=None)], [_rendered()]) == "skin inconsistent"


def test_sanity_refuses_more_materials_than_submeshes():
    assert _reason([_written()], [_rendered(materials=[11, 12, 13])]) == "materials inconsistent"


def test_sanity_refuses_a_null_material_between_others():
    assert _reason([_written(submeshes=3)], [_rendered(materials=[11, 0, 13])]) == "materials inconsistent"
    assert _reason([_written(source_materials=[1, 0, 1], submeshes=3)], [_rendered(materials=[11, 12])]) \
        == "materials inconsistent"


# --- the named refusals ---------------------------------------------------------------------------------------------

def _scaled(scale):
    matrix = np.eye(4)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = (0.1, 0.9, -0.2)
    return matrix


def test_inverse_of_a_sound_rest_matrix_is_the_plain_inverse():
    matrix = _scaled(0.01)
    assert np.array_equal(ma._inverse(matrix, "Bip001"), np.linalg.inv(matrix))
    flat = _scaled(1.0)
    flat[:3, :3] = np.diag([1.0, 1.0, 1e-4])  # squashed, not singular
    assert np.array_equal(ma._inverse(flat, "Bip001 Pelvis"), np.linalg.inv(flat))


@pytest.mark.parametrize("scale", [0.0, np.array([1.0, 1.0, 1e-10]), np.array([1.0, 0.0, 1.0])])
def test_inverse_refuses_a_bone_without_volume_and_names_it(scale):
    matrix = _scaled(1.0)
    matrix[:3, :3] = np.diag(np.broadcast_to(scale, 3))
    with pytest.raises(ServiceError) as caught:
        ma._inverse(matrix, "Bip001_Weapon")
    assert caught.value.code == "source_unsupported"
    assert caught.value.params == {"source": "singular bone", "bone": "Bip001_Weapon"}


def test_inverse_refuses_a_matrix_that_is_not_finite():
    matrix = _scaled(1.0)
    matrix[0, 3] = np.nan
    with pytest.raises(ServiceError, match="source_unsupported"):
        ma._inverse(matrix, "Bip001")


def test_a_position_past_half_precision_cannot_be_packed():
    # the build turns this OverflowError into the refusal "vertices out of range"
    raw = bytearray(8)
    with pytest.raises(OverflowError):
        mg._write_plain(raw, (0, 8, "e", 3), np.array([[7e4, 0.0, 0.0]]))
    mg._write_plain(raw, (0, 8, "e", 3), np.array([[6e4, 0.0, 0.0]]))
    assert struct.unpack_from("<e", raw, 0)[0] == pytest.approx(6e4, rel=1e-3)
