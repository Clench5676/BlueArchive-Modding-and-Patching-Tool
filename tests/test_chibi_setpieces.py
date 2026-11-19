"""Set pieces modelled into a costume stay with the slot they were made for (gameassets.chibi_setpieces)."""

from types import SimpleNamespace

import numpy as np
import pytest

from bmpt.gameassets import chibi_adapt as ma
from bmpt.gameassets import chibi_setpieces as sp
from bmpt.gameassets.chibi_adapt.costume import Costume, Flat
from test_model_props import _drawn, _kept, _own_skeletons, _skeleton, _switch, official, view  # noqa: F401

# --- the rule, on a made-up costume --------------------------------------------------------------------------------

ROOT, RIG, PELVIS, CHAIR, WHEEL, SKIRT, PROP = range(7)


def _made_up():
    """A body triangle on the pelvis, a chair under her (two bones), a skirt on a bone beside the pelvis sewn to the
    body, and a prop beside her."""
    s = _skeleton(
        ("root", None),
        ("Bip001", 0),
        ("Bip001 Pelvis", 1),
        ("bone_chair", 1),
        ("bone_wheel", 3),
        ("bone_skirt", 1),
        ("bone_prop", 0),
    )
    vertices, joints, triangles = [], [], []

    def add(bone, *points):
        first = len(vertices)
        vertices.extend(points)
        joints.extend([bone] * len(points))
        triangles.extend((first + k, first + k + 1, first + k + 2) for k in range(0, len(points) - 2, 3))

    add(PELVIS, (0, 0.4, 0), (0.1, 0.4, 0), (0, 0.5, 0))
    add(CHAIR, *[(x, y, z) for x in (-0.2, 0.2) for y in (0.1, 0.5) for z in (-0.2, 0.2)], (0, 0.3, 0))
    add(WHEEL, *[(x, y, 0) for x in (-0.25, 0.25) for y in (0.0, 0.3)], (0.25, 0.15, 0.1), (-0.25, 0.15, 0.1))
    add(SKIRT, (0, 0.4, 0), (0.1, 0.3, 0.1), (-0.1, 0.3, 0.1))  # its first vertex lies on the body's
    add(PROP, *[(0.6 + x, y, z) for x in (0, 0.1) for y in (0.0, 0.3) for z in (0, 0.1, 0.2)])
    count = len(vertices)
    joints = np.c_[np.array(joints), np.zeros((count, 3), int)]
    weights = np.c_[np.ones(count), np.zeros((count, 3))]
    flat = Flat(np.array(vertices, float), joints, weights, np.zeros((0, 2), int), np.array(triangles), [0])
    skin = SimpleNamespace(triangles=np.array(triangles))
    return s, Costume(s, [object()], [skin]), flat


def _pose(s, **scaled):
    world = s.world()
    for name, scale in scaled.items():
        i = s.first(name)
        world[i] = world[i] @ np.diag([scale, scale, scale, 1.0])
    return world


def test_what_she_sits_in_is_a_seat_and_a_sewn_skirt_is_clothing():
    s, costume, flat = _made_up()
    idle = _pose(s)
    found = sp.set_pieces(costume, flat, idle, [idle, idle])
    assert [(s.nodes[p.top].name, p.kind) for p in found] == [("bone_chair", "seat")]
    assert set(flat.joints[found[0].rows, 0].tolist()) == {CHAIR, WHEEL}  # its whole branch


def test_a_thing_the_idle_hides_is_shown_at_times():
    s, costume, flat = _made_up()
    idle = _pose(s, bone_prop=0.0)
    kinds = {s.nodes[p.top].name: p.kind for p in sp.set_pieces(costume, flat, idle, [_pose(s)])}
    assert kinds == {"bone_chair": "seat", "bone_prop": "times"}


def test_a_thing_standing_beside_her_all_the_time_is_none():
    s, costume, flat = _made_up()
    assert "bone_prop" not in {s.nodes[p.top].name for p in sp.set_pieces(costume, flat, _pose(s), [])}


def test_a_seat_most_poses_lift_off_the_floor_is_none():
    s, costume, flat = _made_up()
    idle = _pose(s)
    lifted = [m.copy() for m in idle]
    for i in (CHAIR, WHEEL):
        lifted[i][1, 3] += 0.5
    assert sp.set_pieces(costume, flat, idle, [lifted, lifted]) == []
    assert [p.kind for p in sp.set_pieces(costume, flat, idle, [idle, idle, idle, lifted])] == ["seat"]


def test_without_an_idle_nothing_is_found():
    s, costume, flat = _made_up()
    assert sp.set_pieces(costume, flat, None, []) == []


def test_the_triangles_of_a_set_piece_are_numbered_per_part():
    s, costume, flat = _made_up()
    found = sp.set_pieces(costume, flat, _pose(s), [])
    triangles = sp.part_triangles(costume, flat, found)
    assert list(triangles) == [0]
    hit = set(flat.joints[flat.triangles[triangles[0]], 0].ravel().tolist())
    assert hit == {CHAIR, WHEEL} and len(triangles[0]) == 5


# --- on official files ---------------------------------------------------------------------------------------------


def _triangles(mesh):
    from bmpt.gameassets.chibi_glb import read_mesh

    return [tri for sub in read_mesh(mesh.read()).triangles for tri in sub]


@official
def test_official_the_wheelchair_stays_behind(view, tmp_path):  # noqa: F811
    """Shigure <- Himari: Himari's wheelchair is modelled into her body; on Shigure, who stands, it draws nothing."""
    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "shigure_original", "ch0159")
    assert result.left == ["bone_wheelchair"] and result.kept == []
    _kept(slot, written)
    for name, t in _own_skeletons(graph, written, "shigure_original").items():
        [(_, mesh)] = _drawn(graph, t, "Shigure_Original_Body")
        triangles = _triangles(mesh)
        gone = sum(1 for tri in triangles if len(set(tri)) == 1)
        assert 3000 < gone < len(triangles) // 2, name


@official
def test_official_the_slot_keeps_its_wheelchair(view, tmp_path):  # noqa: F811
    """Himari <- Shigure: Himari's slot seats whoever wears it, so her wheelchair is drawn again: a renderer of its
    own draws her body mesh with only the wheelchair's triangles, on her own bones."""
    from bmpt.gameassets.chibi import _key

    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "ch0159", "shigure_original")
    assert result.kept == ["bone_wheelchair"] and result.left == []
    _kept(slot, written)
    for name, t in _own_skeletons(graph, written, "ch0159").items():
        drawn = _drawn(graph, t, "CH0159_Body")
        assert len(drawn) == 2, name  # the costume on her renderer, the wheelchair below it
        chairs = []
        for renderer, mesh in drawn:
            bones = {
                t.nodes[t.by_transform[_key(graph.deref(renderer, b))]].name for b in graph.tree(renderer)["m_Bones"]
            }
            if "bone_wheelchair" in bones:
                chairs.append(mesh)
        [chair] = chairs
        triangles = _triangles(chair)
        kept = sum(1 for tri in triangles if len(set(tri)) == 3)
        assert 3000 < kept < len(triangles) // 2, name


@official
@pytest.mark.parametrize("source,top", [("nodoka_original", "bone_telescope_main"), ("ch0280", "bone_acc")])
def test_official_a_prop_the_clips_bring_out_stays_behind(view, tmp_path, source, top):  # noqa: F811
    """A thing the source's clips show for a skill (Nodoka's telescope, Neru (School Uniform)'s prop) is hidden by
    her idle; another slot's clips never hide it, so it draws nothing there."""
    result, *_ = _switch(view, tmp_path, "shigure_original", source)
    assert result.left == [top]
