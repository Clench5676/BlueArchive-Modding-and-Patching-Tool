"""Another variant's costume on a student's 3D model slot (gameassets.model_adapt)."""

import copy
import hashlib
import json
import os
import re
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from bmpt.gameassets import model_adapt as ma

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
official = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")


# --- pure parts ----------------------------------------------------------------------------------------------------

def _pptr(pid, fid=0):
    return {"m_FileID": fid, "m_PathID": pid}


def test_preload_ranges_grow_in_place_when_contiguous():
    bundle = {"m_PreloadTable": [_pptr(i) for i in range(5)],
              "m_Container": [["a.prefab", {"preloadIndex": 0, "preloadSize": 2, "asset": _pptr(1)}],
                              ["b.prefab", {"preloadIndex": 2, "preloadSize": 3, "asset": _pptr(2)}]]}
    ma._extend_preload(bundle, {0: [_pptr(9, 1)]})
    assert [p["m_PathID"] for p in bundle["m_PreloadTable"]] == [0, 1, 9, 2, 3, 4]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in bundle["m_Container"]] == [(0, 3), (3, 3)]


def test_preload_ranges_with_gaps_get_a_copy_at_the_end():
    bundle = {"m_PreloadTable": [_pptr(i) for i in range(6)],
              "m_Container": [["a.prefab", {"preloadIndex": 1, "preloadSize": 2, "asset": _pptr(1)}],
                              ["b.prefab", {"preloadIndex": 4, "preloadSize": 2, "asset": _pptr(2)}]]}
    ma._extend_preload(bundle, {1: [_pptr(9)]})
    assert [p["m_PathID"] for p in bundle["m_PreloadTable"]] == [0, 1, 2, 3, 4, 5, 4, 5, 9]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in bundle["m_Container"]] == [(1, 2), (6, 3)]


def test_entries_that_share_a_preload_range_keep_their_own_additions():
    """Two prefabs of one preload range: contiguous ranges grow by both entries' objects; with gaps, each entry gets a
    copy of its own range with its own objects."""
    shared = {"m_PreloadTable": [_pptr(i) for i in range(4)],
              "m_Container": [["a.prefab", {"preloadIndex": 0, "preloadSize": 2, "asset": _pptr(1)}],
                              ["b.prefab", {"preloadIndex": 0, "preloadSize": 2, "asset": _pptr(2)}],
                              ["c.prefab", {"preloadIndex": 2, "preloadSize": 2, "asset": _pptr(3)}]]}
    ma._extend_preload(shared, {0: [_pptr(8)], 1: [_pptr(9), _pptr(8)]})
    assert [p["m_PathID"] for p in shared["m_PreloadTable"]] == [0, 1, 8, 9, 2, 3]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in shared["m_Container"]] == [(0, 4), (0, 4), (4, 2)]
    gaps = {"m_PreloadTable": [_pptr(i) for i in range(5)],
            "m_Container": [["a.prefab", {"preloadIndex": 1, "preloadSize": 2, "asset": _pptr(1)}],
                            ["b.prefab", {"preloadIndex": 1, "preloadSize": 2, "asset": _pptr(2)}]]}
    ma._extend_preload(gaps, {0: [_pptr(8)], 1: [_pptr(9)]})
    assert [p["m_PathID"] for p in gaps["m_PreloadTable"]] == [0, 1, 2, 3, 4, 1, 2, 8, 1, 2, 9]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in gaps["m_Container"]] == [(5, 3), (8, 3)]


def test_a_models_own_prefabs_are_chosen_by_their_names():
    prefabs = dict.fromkeys(["ch0336", "echelon_ch0336", "ch0336_carrier", "echelon_ch0336_carrier",
                             "field_npc_np0060_justice_student", "field_npc_np00601_other"])
    assert set(ma.own_prefabs(prefabs, "ch0336")) == {"ch0336", "echelon_ch0336"}
    assert set(ma.own_prefabs(prefabs, "np0060")) == {"field_npc_np0060_justice_student"}


def test_renderer_lists_that_name_the_primary_take_the_added_part():
    tree = {"renderers": [_pptr(1), _pptr(2)], "mouth": _pptr(1), "nested": {"list": [_pptr(3)]}, "bones": [_pptr(1)]}
    assert ma._append_beside(tree, _pptr(1), _pptr(7))
    assert tree["renderers"] == [_pptr(1), _pptr(2), _pptr(7)]
    assert tree["mouth"] == _pptr(1) and tree["nested"]["list"] == [_pptr(3)]


class _Graph:
    """Typetrees are the objects themselves."""

    def tree(self, obj):
        return obj


class _Object:
    """An object of a fake file: its typetree in ``tree``; pointers resolve within the file."""

    def __init__(self, cab, path_id, tree):
        from types import SimpleNamespace
        self.assets_file, self.path_id, self.tree = SimpleNamespace(name=cab), path_id, tree


class _Objects:
    def __init__(self, *objects):
        self.by = {(o.assets_file.name, o.path_id): o for o in objects}

    def tree(self, obj):
        return obj.tree

    def deref(self, obj, pointer):
        return self.by.get((obj.assets_file.name, pointer["m_PathID"]))


def _mouth_model(graph, cab, atlas, scale, grid, uv):
    """A model's costume (one primary part, an eye-mouth material second) and its mouth receiver."""
    texture = _Object(cab, 1, {"m_Name": "atlas"}) if atlas is None else atlas
    material = _Object(cab, 2, {"m_SavedProperties": {
        "m_TexEnvs": [("_MainTex", {"m_Texture": _pptr(0), "m_Scale": {"x": 1, "y": 1}, "m_Offset": {"x": 0, "y": 0}}),
                      ("_MouthTileTex", {"m_Texture": _pptr(texture.path_id), "m_Scale": {"x": scale, "y": scale},
                                         "m_Offset": dict(uv)})],
        "m_Floats": [("_MouthTileCols", grid), ("_MouthTileRows", grid)]}})
    renderer = _Object(cab, 3, {})
    receiver = _Object(cab, 4, {"MouthRenderer": _pptr(3), "MouthMaterialIndex": 1, "MouthDefaultUV": dict(uv)})
    graph.by.update({(o.assets_file.name, o.path_id): o for o in (texture, material, renderer, receiver)})
    skeleton = ma.Skeleton(graph)
    skeleton.behaviours.append(receiver)
    part = ma.Part(0, renderer, None, [None, material], ["body", "eyemouth"], [], 0, role="primary")
    return skeleton, [part], material


def test_a_mouth_on_another_atlas_takes_the_slots_atlas_and_default_tile():
    """The slot's clips name mouth tiles as cells of the slot's atlas: a source eye-mouth material on another atlas
    (Hina's 4 x 4 one under the shared 8 x 8 one) takes the slot's, and the receivers keep the slot's default tile;
    on the same atlas the source's material and default tile stay."""
    graph = _Objects()
    slot, slot_costume, _ = _mouth_model(graph, "slot", None, 1.0, 4.0, {"x": 0.0, "y": 0.75})
    source, source_costume, material = _mouth_model(graph, "source", None, 0.5, 8.0, {"x": 0.625, "y": 0.25})
    mouth = ma.mouth_of(source, source_costume)
    assert (mouth.part, mouth.index, mouth.uv) == (0, 1, {"x": 0.625, "y": 0.25})
    atlas, retiled, carried = ma.retiled_mouths(graph, slot, slot_costume, source_costume, mouth)
    assert retiled == {ma._key(material)}
    assert atlas.material is slot_costume[0].materials[1] and atlas.grid == {"_MouthTileCols": 4.0, "_MouthTileRows": 4.0}
    assert atlas.env["m_Scale"] == {"x": 1.0, "y": 1.0}
    assert carried.uv == {"x": 0.0, "y": 0.75} and mouth.uv == {"x": 0.625, "y": 0.25}

    # one atlas object, one scale and grid: nothing changes
    shared = _Object("shared", 9, {"m_Name": "Character_Mouth"})
    graph = _Objects(shared)
    slot, slot_costume, _ = _mouth_model(graph, "shared", shared, 0.5, 8.0, {"x": 0.5, "y": 0.875})
    source = ma.Skeleton(graph)
    source.behaviours.append(_Object("shared", 14, {"MouthRenderer": _pptr(13), "MouthMaterialIndex": 1,
                                                    "MouthDefaultUV": {"x": 0.125, "y": 0.5}}))
    other = _Object("shared", 12, copy.deepcopy(slot_costume[0].materials[1].tree))
    renderer = _Object("shared", 13, {})
    graph.by.update({("shared", o.path_id): o for o in (other, renderer, source.behaviours[0])})
    source_costume = [ma.Part(0, renderer, None, [None, other], ["body", "eyemouth"], [], 0, role="primary")]
    mouth = ma.mouth_of(source, source_costume)
    atlas, retiled, carried = ma.retiled_mouths(graph, slot, slot_costume, source_costume, mouth)
    assert atlas is not None and not retiled and carried.uv == {"x": 0.125, "y": 0.5}
    # the same texture drawn at another scale (a mirrored atlas) is another layout
    other.tree["m_SavedProperties"]["m_TexEnvs"][1][1]["m_Scale"] = {"x": -0.5, "y": 0.5}
    assert ma.retiled_mouths(graph, slot, slot_costume, source_costume, mouth)[1] == {ma._key(other)}


def _skeleton(bones):
    """``bones``: (name, parent index, local position, local quaternion) after a root at the origin."""
    skeleton = ma.Skeleton(_Graph())
    skeleton.nodes.append(ma.Node("bone_root", "", None, ((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), None, None, True))
    for name, parent, position, rotation in bones:
        path = f"{skeleton.nodes[parent].path}/{name}" if parent else name
        skeleton.nodes.append(ma.Node(name, path, parent, (position, rotation, (1, 1, 1)), None, None, True))
    return skeleton


def _arm(forearm=1.0, twist=True):
    """An arm along +X: upper arm at 1 m height, forearm ``forearm`` m on, hand 1 m further; a Biped ForeTwist hangs off
    the upper arm, half way along the forearm."""
    bones = [("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)),
             ("Bip001 L UpperArm", 1, (0, 1, 0), (0, 0, 0, 1)),
             ("Bip001 L Forearm", 2, (forearm, 0, 0), (0, 0, 0, 1)),
             ("Bip001 L Hand", 3, (1, 0, 0), (0, 0, 0, 1))]
    if twist:
        bones.append(("Bip001 L ForeTwist", 2, (forearm + 0.5, 0, 0), (0, 0, 0, 1)))
    return _skeleton(bones)


def _bent(skeleton):
    """The elbow bent by 90 degrees about Z, the wrist by 45 about Y; the ForeTwist keyed to turn with the forearm."""
    turn = (0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4))
    forearm, twist = skeleton.first("Bip001 L Forearm"), skeleton.first("Bip001 L ForeTwist")
    hand = skeleton.first("Bip001 L Hand")
    length = skeleton.nodes[forearm].local[0][0]
    local = {forearm: ma.trs(skeleton.nodes[forearm].local[0], turn, (1, 1, 1)),
             hand: ma.trs(skeleton.nodes[hand].local[0], (0, np.sin(np.pi / 8), 0, np.cos(np.pi / 8)), (1, 1, 1))}
    if twist is not None:
        local[twist] = ma.trs((length, 0, 0), turn, (1, 1, 1)) @ ma.trs((0.5, 0, 0), (0, 0, 0, 1), (1, 1, 1))
    return skeleton.world(local)


def _flat(points, joints):
    points = np.asarray(points, float)
    joints = np.asarray(joints, int).reshape(-1, 1)
    return ma.Flat(points, np.c_[joints, np.zeros((len(points), 3), int)],
                   np.c_[np.ones(len(points)), np.zeros((len(points), 3))], np.zeros((0, 2), int),
                   np.zeros((0, 3), int), [0], np.repeat(np.eye(3)[None], len(points), axis=0))


def test_a_biped_bone_the_slot_lacks_goes_with_the_bone_it_moves_with():
    """A Battle NPC's ForeTwist hangs off the upper arm and turns with the forearm. On a slot without it the
    sleeve goes with the forearm (by the source's own clips), not with the upper arm, its parent."""
    source, target = _arm(), _arm(twist=False)
    twist, forearm = source.first("Bip001 L ForeTwist"), source.first("Bip001 L Forearm")
    flat = _flat([[1.4, 1.0, 0.05], [1.6, 1.0, -0.05]], [twist, twist])
    carried = ma.carriers(target, source, flat, [_bent(source)])
    assert carried == {twist: forearm}
    mapping = ma.map_bones(target, source, [twist], carried)
    assert mapping[twist] == target.first("Bip001 L Forearm")
    assert ma.map_bones(target, source, [twist])[twist] == target.first("Bip001 L UpperArm")  # its parent's, before
    assert ma.carriers(target, source, flat, []) == {}  # no clips, no carrier


def test_the_refit_carries_each_vertex_by_its_anchor():
    """A costume made for a longer forearm, refitted to the slot's: the hand's vertices move by the hand's
    joint gap, the upper arm's stay; a secondary bone's vertices go rigidly with its Biped anchor."""
    source = _arm(forearm=1.2, twist=False)
    source.nodes.append(ma.Node("bone_cuff", "x", source.first("Bip001 L Hand"), ((0.1, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
                                None, None, True))
    target = _arm(forearm=1.0, twist=False)
    hand, upper, cuff = source.first("Bip001 L Hand"), source.first("Bip001 L UpperArm"), len(source.nodes) - 1
    flat = _flat([[0.5, 1.0, 0.0], [2.2, 1.0, 0.0], [2.4, 1.0, 0.0]], [upper, hand, cuff])
    fit = ma.refit(target, source, flat)
    assert np.allclose(fit.vertices, [[0.5, 1.0, 0.0], [2.0, 1.0, 0.0], [2.2, 1.0, 0.0]])
    assert fit.shape == "refitted"
    # the shape change past the common move (the median: 0.2 m back): the upper arm's vertex, 0.2 m
    assert fit.largest == pytest.approx(0.2) and fit.size == pytest.approx(1.9)
    # a costume that only sits elsewhere as a whole changes no shape
    whole = ma.refit(_arm(forearm=1.0, twist=False), _skeleton([("Bip001", 0, (0.5, 0, 0), (0, 0, 0, 1)),
                                                               ("Bip001 L UpperArm", 1, (0, 1, 0), (0, 0, 0, 1))]),
                     _flat([[0.7, 1.0, 0.0], [1.0, 1.2, 0.0]], [2, 2]), shape="moved")
    assert np.allclose(whole.vertices, [[0.2, 1.0, 0.0], [0.5, 1.2, 0.0]]) and whole.largest == pytest.approx(0.0)


def test_a_face_goes_with_the_head_whatever_its_eye_bones():
    """Midori (Battle)'s face on Midori: the eye bones (Bip001 bone_eye_D_L, named Bip001 like the Biped) rest
    elsewhere on the slot's face rig; refitting the eyelid by them pulled the eye out of the face. The face's vertices
    go rigidly with the head."""
    def head(eye_y):
        return _skeleton([("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Head", 1, (0, 1, 0), (0, 0, 0, 1)),
                          ("Bip001 bone_eye_D_L", 2, (0.05, eye_y, 0.08), (0, 0, 0, 1))])
    source, target = head(0.0), head(-0.035)
    eye, top = source.first("Bip001 bone_eye_D_L"), source.first("Bip001 Head")
    assert ma.fit_anchor(target, source, eye) == (top, target.first("Bip001 Head"))
    flat = _flat([[0.05, 1.0, 0.08], [0.0, 1.1, 0.0]], [eye, top])
    assert np.allclose(ma.refit(target, source, flat).vertices, flat.vertices)  # same head: the face keeps its shape


def test_the_head_goes_with_the_neck_not_onto_the_slots_head_joint():
    """Yuzu's costume on Aris: Aris's Head joint sits higher inside her head than Yuzu's (5 cm above the neck joint
    against 3.2). Carried onto the slot's Head joint, Yuzu's whole head rose 1.8 cm against the collar the neck and
    spine carry, and the neck between stretched. The head's vertices take the neck's move; the source's head joint
    lands below the slot's, and a halo following the head moves along."""
    def body(neck_y, head_y):
        return _skeleton([("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Spine1", 1, (0, 0.9, 0), (0, 0, 0, 1)),
                          ("Bip001 Neck", 2, (0, neck_y - 0.9, 0), (0, 0, 0, 1)),
                          ("Bip001 Head", 3, (0, head_y - neck_y, 0), (0, 0, 0, 1)),
                          ("bone_hair_00", 4, (0, 0.05, -0.03), (0, 0, 0, 1))])
    source, target = body(1.0, 1.032), body(0.98, 1.03)  # the slot's neck: 2 cm lower, its head joint as high
    spine, neck, head, hair = (source.first(n) for n in ("Bip001 Spine1", "Bip001 Neck", "Bip001 Head", "bone_hair_00"))
    # the collar, the neck, the chin, the crown and a lock of hair
    flat = _flat([[0, 0.99, 0.02], [0, 1.01, 0.01], [0, 1.015, 0.04], [0, 1.1, 0], [0, 1.08, -0.04]],
                 [spine, neck, head, head, hair])
    for shape in ("moved", "refitted"):
        fit = ma.refit(target, source, flat, shape=shape)
        assert np.allclose(fit.vertices[:2], flat.vertices[:2] - [[0, 0, 0], [0, 0.02, 0]])  # the spine's, the neck's
        # the head and the hair go down with the neck, 2 cm: no longer left 0.2 cm down at the slot's head joint
        assert np.allclose(fit.vertices[2:], flat.vertices[2:] - [0, 0.02, 0])
        assert np.allclose(fit.head, [0, -0.018, 0])  # its joint 1.8 cm below the slot's
    # a halo following the head moves along, in the head's space, its clamp box with it
    turned = _skeleton([("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)),
                        ("Bip001 Head", 1, (0, 1.0, 0), (0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)))])
    follower = {"TargetRelativePosition": {"x": 0.0, "y": 0.2, "z": 0.0},
                "TargetRelativeRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                "ClampMinOffset": {"x": -0.1, "y": 0.1, "z": -0.1}}
    ma._halo_shift(turned, follower, np.array([0, -0.018, 0]))
    assert [follower["TargetRelativePosition"][k] for k in "xyz"] == pytest.approx([-0.018, 0.2, 0.0])
    assert [follower["ClampMinOffset"][k] for k in "xyz"] == pytest.approx([-0.118, 0.1, -0.1])
    assert follower["TargetRelativeRotation"] == {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}


def test_a_skirt_on_the_biped_root_and_a_bag_off_a_prop_go_with_the_body():
    """Sena (Casual)'s skirt hangs off Bip001 and her bag off a prop node Sena lacks. On a slot whose prefab
    rests 0.74 m aside, the refit took the skirt nowhere (no anchor on the body chain) and the bag stayed with the
    prefab's root: both were left beside her. The skirt is refitted by the Biped's root; the bag, carried by the spine
    under the source's clips, goes with the spine as one branch, never with a loose slot bone that happens to rest near it."""
    def body(x, prop):
        bones = [("Bip001", 0, (x, 1.0, 0), (0, 0, 0, 1)), ("Bip001 Pelvis", 1, (0, 0, 0), (0, 0, 0, 1)),
                 ("Bip001 Spine", 2, (0, 0.2, 0), (0, 0, 0, 1)), ("bone_skirt_F_00", 1, (0, -0.1, 0.1), (0, 0, 0, 1))]
        if prop:
            bones += [("prop_root", 0, (0, 0, 0), (0, 0, 0, 1)), ("bone_bag_00", 5, (x + 0.2, 1.2, 0), (0, 0, 0, 1))]
        else:
            bones += [("Glow", 0, (x + 0.2, 1.2, 0), (0, 0, 0, 1))]
        return _skeleton(bones)
    source, target = body(0.0, True), body(0.74, False)
    skirt, bag, spine = source.first("bone_skirt_F_00"), source.first("bone_bag_00"), source.first("Bip001 Spine")
    assert ma.fit_anchor(target, source, skirt) == (source.first("Bip001"), target.first("Bip001"))
    flat = _flat([[0.0, 0.9, 0.1], [0.2, 1.2, 0.0]], [skirt, bag])
    moved = ma.refit(target, source, flat, shape="moved")
    assert np.allclose(moved.vertices[0], [0.74, 0.9, 0.1])  # the skirt moves with the body
    # the source's clips: the spine bends and the bag goes along with it
    turn = (0, 0, np.sin(np.pi / 8), np.cos(np.pi / 8))
    local = {spine: ma.trs((0, 0.2, 0), turn, (1, 1, 1))}
    pose = source.world(local)
    pose[bag] = pose[spine] @ np.linalg.inv(source.world()[spine]) @ source.world()[bag]
    carried = ma.carriers(target, source, flat, [pose])
    assert carried == {source.first("prop_root"): spine}  # the prop's branch goes as one
    mapping = ma.map_bones(target, source, [skirt, bag], carried)
    assert mapping[bag] == target.first("Bip001 Spine")  # not the slot's Glow resting where the bag does
    assert ma.fit_anchor(target, source, bag, carried) == (spine, target.first("Bip001 Spine"))


def test_the_costume_is_taken_where_its_bindposes_draw_it():
    """A mesh whose vertex data lies 1 m off its renderer's space, its bindposes bringing it back (Koharu's);
    the costume is the shape the prefab draws, not the vertex data in the renderer's space."""
    skeleton = _arm(twist=False)
    hand = skeleton.first("Bip001 L Hand")
    rest = skeleton.world()
    bindpose = np.linalg.inv(rest[hand]) @ ma.trs((-1, 0, 0), (0, 0, 0, 1), (1, 1, 1))
    mesh = {"m_BindPose": [{f"e{r}{c}": float(bindpose[r, c]) for r in range(4) for c in range(4)}]}
    part = ma.Part(0, None, mesh, [], ["body"], [hand], 3)
    vertices = np.array([[3.0, 1.0, 0.0], [3.0, 1.1, 0.0]])
    skin = ma.Skin(vertices, np.zeros((2, 4), int), np.c_[np.ones(2), np.zeros((2, 3))], np.array([[0, 1]]))
    flat = ma.flatten(ma.Costume(skeleton, [part], [skin]), rest)
    assert np.allclose(flat.vertices, vertices - [1, 0, 0])
    assert np.allclose(flat.linear, np.eye(3))


def test_a_face_is_drawn_as_bound_whatever_its_bones_rest_in():
    """Midori (Battle)'s brow bones rest 1.4 cm above their bind (an expression her clips replace); the face is
    drawn as bound, rigidly with the head, not with its brows raised (they came through Midori's bangs)."""
    skeleton = _skeleton([("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Head", 1, (0, 1, 0), (0, 0, 0, 1)),
                          ("Bip001 Xtra_eyeblowL1", 2, (0.05, 0.114, 0.08), (0, 0, 0, 1))])  # bound at 0.1 up
    head, brow = skeleton.first("Bip001 Head"), skeleton.first("Bip001 Xtra_eyeblowL1")
    rest = skeleton.world()
    bound = [np.linalg.inv(rest[head]), np.linalg.inv(ma.trs((0.05, 1.1, 0.08), (0, 0, 0, 1), (1, 1, 1)))]
    mesh = {"m_BindPose": [{f"e{r}{c}": float(m[r, c]) for r in range(4) for c in range(4)} for m in bound]}
    part = ma.Part(0, None, mesh, [], ["face"], [head, brow], 3)
    vertices = np.array([[0.05, 1.1, 0.09], [0.0, 1.05, 0.1]])  # a brow vertex, a cheek vertex
    skin = ma.Skin(vertices, np.array([[1, 0, 0, 0], [0, 0, 0, 0]]), np.c_[np.ones(2), np.zeros((2, 3))], np.array([[0, 1]]))
    flat = ma.flatten(ma.Costume(skeleton, [part], [skin]), rest)
    assert np.allclose(flat.vertices, vertices)  # not 1.4 cm up
    assert ma.face_bone(skeleton, brow) and not ma.face_bone(skeleton, head)


def test_names_resolve_inside_the_rig_the_renderers_skin_to():
    """A halo with a rig of its own (Haruna (Track)'s, Hasumi's) comes first in the prefab: names resolve inside the
    Biped the renderers skin to, and names only the other rig has still resolve."""
    from types import SimpleNamespace

    class Graph(_Graph):
        def deref(self, owner, pointer):
            return pointer

    skeleton = _skeleton([("HaloRoot", 0, (0, 2, 0), (0, 0, 0, 1)), ("Bip001", 1, (0, 0, 0), (0, 0, 0, 1)),
                          ("Bip001 Head", 2, (0, 0.1, 0), (0, 0, 0, 1)), ("bone_halo", 2, (0, 0.2, 0), (0, 0, 0, 1)),
                          ("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Head", 5, (0, 1.5, 0), (0, 0, 0, 1))])
    skeleton.graph = Graph()
    bones = [SimpleNamespace(assets_file=SimpleNamespace(name="cab"), path_id=i) for i in range(len(skeleton.nodes))]
    skeleton.by_transform = {("cab", i): i for i in range(len(skeleton.nodes))}
    skeleton.renderers = [(5, {"m_Bones": [bones[5], bones[6]]}), (3, {"m_Bones": [bones[4]]})]
    assert skeleton.main_rig() == 5
    assert skeleton.first("Bip001") == 5 and skeleton.first("Bip001 Head") == 6
    assert skeleton.first("bone_halo") == 4


def test_stretch_counts_shrinking_as_growing():
    rest = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    edges = np.array([[0, 1], [0, 2]])
    assert ma.stretch(rest, rest, edges) == pytest.approx(1.0)
    assert ma.stretch(rest, rest * np.array([2, 1, 1]), edges) == pytest.approx(2.0, rel=1e-3)
    assert ma.stretch(rest, rest * np.array([0.5, 1, 1]), edges) == pytest.approx(2.0, rel=1e-3)


# --- official files ------------------------------------------------------------------------------------------------

def _index(files):
    from bmpt.gameassets.discovery import cab_names
    index = {}
    for path in files:
        if re.search(r"characters|-_mx-npcs-|^character-|-_mx-shaders-|-_mx-program-animators-|^field-|animatorbase|"
                     r"prolog|-_mx-effectsfreezed-", path.name):
            for cab in cab_names(path):
                index[cab] = path
    return index


@pytest.fixture(scope="module")
def view():
    files = [path for path in GAME_DIR.rglob("*.bundle")]
    return files, _index(files)


def _slot(files, dev):
    return [path for path in files if re.match(rf"(character|assets-_mx-characters)-{re.escape(dev)}-_mx", path.name)]


@official
@pytest.mark.parametrize("target,source,field,parts,hem,physics", [
    ("hina_original", "np0009", True, 1, "skirt smooth", True),   # Hina <- Hina (Pajamas), a field-mode model: Rule B
    ("hina_original", "np0009", True, 1, "blend 0.5", False),     # the same without Optimize Physics: Rule A only
    ("ch0172", "np0283", True, 1, "skirt smooth", True),           # Niko <- Niko (FOX EATS): her separate hands go;
                                                                   # Rule B with the chains off her thighs
    ("hoshino_original", "hoshino_swimsuit", False, 3, "rigid", False),  # a three-part costume: two renderers added
    ("hoshino_swimsuit", "hoshino_original", False, 1, "rigid", False),  # the swimsuit's own extra parts draw nothing
    ("ch0186", "mari_original", False, 1, "skirt smooth", True),   # Mari (Track) <- Mari: a compressed mesh; Rule B
                                                                   # within the limit once the seam keeps its length
    ("airi_original", "ch0251", False, 1, "rigid", False),         # Airi <- Airi (Band): past the limit (12.1) until
])                                                                 # its worst edges are stiffened
def test_official_costume_on_a_slot(view, tmp_path, target, source, field, parts, hem, physics):
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from ba_modding_toolkit.naming import parse_filename
    from ba_modding_toolkit.utils import compute_crc32
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _key, _prefabs, export_chibi
    import UnityPy

    files, index = view
    slot = _slot(files, target)
    sources = [p for p in files if p.name.startswith("field-character-_mxload")] if field else [
        p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev=target, source_files=sources,
                      source_dev=source, open_writable=Bundle.load, physics=physics)
    assert result.parts == parts
    assert result.plan.stretch <= ma.stretch_limit(result.plan.own)
    # the hem: less pass-through than rigid weights, no clip 5 points worse, stretch within the limit
    hems = result.hems
    assert hems.rule == hem, hems.summary()
    if hem != "rigid":
        rigid, chosen = hems.scores[0], hems.score(hem)
        assert chosen.mean < rigid.mean
        assert all(chosen.deep[clip] <= rigid.deep[clip] + 5 for clip in rigid.deep)
        assert chosen.stretch <= max(hems.limit, rigid.stretch)
        # Rule A rewrites weights; Rule B adds bones (its smooth variant also blends weights) and curves
        assert any(change is not None for change in hems.parts) or hem == "skirt"
    skirt = hems.skirt
    assert (skirt is not None) == hem.startswith("skirt")
    # Optimize Physics: knees deep inside the skirt (Mari's) are moved toward the leg's axis; Niko's skirt clears
    # hers once its chains off the thighs turn too
    assert (hems.thin is not None) == (physics and target == "ch0186"), hems.summary()
    if hems.thin is not None:
        assert hems.thin_score.mean <= hems.score(hem).mean - ma.THIN_GAIN
    if skirt is not None:
        assert skirt.clips >= 20
    out = tmp_path / "out"
    out.mkdir()
    for path, bundle in result.changed.items():
        ok, message = bundle.save(out / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
    written = sorted(out.glob("*.bundle"))
    assert written and all(compute_crc32(p) == int(parse_filename(p.name).crc) for p in written)
    # every object of the official files is still there, byte for byte, but the renderers and the preload table
    for path in written:
        before = {o.path_id: o for o in UnityPy.load(str(next(p for p in slot if p.name == path.name))).objects}
        after = {o.path_id: o for o in UnityPy.load(str(path)).objects}
        assert before.keys() <= after.keys()
        changed = {before[pid].type.name for pid in before if before[pid].get_raw_data() != after[pid].get_raw_data()}
        allowed = {"SkinnedMeshRenderer", "AssetBundle", "Transform", "MonoBehaviour"}
        # Rule B's skirt and another face's bones (face_retarget) give the slot's clips curves
        assert changed <= allowed | ({"AnimationClip"} if skirt or hems.face else set()), changed
    # the slot read back: the costume drawn at rest exactly in the shape it was bound and judged in (as the
    # source draws it, refitted to the slot's joints), and exportable
    adapted = dict(index)
    for path in written:
        for cab in cab_names(path):
            adapted[cab] = path
    graph = BundleGraph(adapted)
    prefab_files = [p for p in written if p.name.startswith("character-")]
    t_prefabs, s_prefabs = _prefabs(graph, prefab_files), _prefabs(graph, sources)
    t = ma.Skeleton.of(graph, t_prefabs[ma.choose_prefab(t_prefabs, target)].deref())
    s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, source)].deref())
    s_parts = [p for p in ma.roles(s, None) if p.role in ("primary", "costume")]
    t_rest = t.world()
    # the hem's weights by slot bone path, in the order adapt takes the parts (the primary first)
    official = BundleGraph(index)
    o_prefabs = _prefabs(official, [p for p in slot if p.name.startswith("character-")])
    t0 = ma.Skeleton.of(official, o_prefabs[ma.choose_prefab(o_prefabs, target)].deref())
    ordered = [p for p in s_parts if p.role == "primary"] + [p for p in s_parts if p.role == "costume"]
    paths = [n.path for n in t0.nodes]  # the slot's node paths, Rule B's new bones after its own
    if skirt is not None:
        for name, parent in zip(skirt.names, skirt.parents):
            paths.append(f"{paths[skirt.node(parent)] if parent >= 0 else paths[skirt.anchor]}/{name}")
        # every prefab of the slot has the new bones, and the slot's clips turn the chains' first bones
        roots = {paths[skirt.node(k)] for k, parent in enumerate(skirt.parents) if parent < 0}
        for pointer in t_prefabs.values():
            assert set(paths[skirt.base:]) <= {n.path for n in ma.Skeleton.of(graph, pointer.deref()).nodes}
        own = {k: v for k, v in t_prefabs.items() if target in k}
        hashes = {zlib.crc32(path.encode()): path for path in roots}
        turned = [c for c in ma.prefab_clips(graph, t, own) if all(roots == {hashes[b["path"]] for b in graph.tree(c)[
            "m_ClipBindingConstant"]["genericBindings"] if b["path"] in hashes and b["attribute"] == attribute}
            for attribute in (1, 2))]  # each first bone turned and moved
        assert len(turned) == skirt.clips
    changes = {p.mesh.peek_name(): change for p, change in zip(ordered, hems.parts) if change is not None}
    drawn, empty, reweighted = [], 0, 0
    for node, renderer in t.renderers:
        tree = graph.tree(renderer)
        mesh = graph.deref(renderer, tree["m_Mesh"])
        if mesh is None:
            empty += 1
            continue
        part = next((p for p in s_parts if p.mesh.peek_name() == mesh.peek_name()), None)
        if part is None:
            continue
        skin = ma.skin_of(mesh)
        bones = [t.by_transform[_key(graph.deref(renderer, b))] for b in tree["m_Bones"]]
        posed = ma.deform(skin, np.array([t_rest[b] for b in bones]) @ ma.bindposes(graph, mesh))
        fit = result.plan.fit
        k = [p.mesh.peek_name() for p in ordered].index(mesh.peek_name())
        want = fit.vertices[fit.starts[k]:(fit.starts + [len(fit.vertices)])[k + 1]]
        assert np.abs(posed - want).max() < 1e-4
        drawn.append(mesh.peek_name())
        change = changes.get(mesh.peek_name())
        if change is not None:  # the written skin is the hem's, merged per slot bone (compressed: 1/31 steps)
            rows, nodes, weights = change
            for row, n_row, w_row in zip(rows, nodes, weights):
                got, wanted = {}, {}
                for j, w in zip(skin.joints[row], skin.weights[row]):
                    got[t.nodes[bones[j]].path] = got.get(t.nodes[bones[j]].path, 0.0) + w
                for n, w in zip(n_row, w_row):
                    wanted[paths[n]] = wanted.get(paths[n], 0.0) + w
                assert all(abs(got.get(k, 0.0) - wanted.get(k, 0.0)) <= 1 / 31 for k in set(got) | set(wanted))
            reweighted += 1
    assert sorted(drawn) == sorted(p.mesh.peek_name() for p in s_parts)
    assert reweighted == len(changes)
    assert empty == {"ch0172": 1, "hoshino_swimsuit": 2}.get(target, 0)
    data = export_chibi(BundleGraph(adapted), prefab_files, target)
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert {m["name"] for m in doc["meshes"]} >= set(drawn) and doc["animations"]


@official
def test_official_rule_b_clip_edit_is_read_back_and_a_changed_curve_is_refused(view, monkeypatch):
    """Every clip Rule B edits is read back from its saved bytes and compared with what it was. Hina <- Hina
    (Pajamas) with Optimize Physics edits 31 clips; one whose old constant curve changes on its way to the object is
    refused, naming the clip, and the pair is not returned."""
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.errors import ServiceError

    files, index = view
    slot = _slot(files, "hina_original")
    sources = [p for p in files if p.name.startswith("field-character-_mxload")]

    def build():
        return ma.adapt(index=index, resolver=None, target_files=slot, target_dev="hina_original",
                        source_files=sources, source_dev="np0009", open_writable=Bundle.load, physics=True)

    checked = []
    real_error = ma.clip_edit_error

    def watched(before, tree, added):
        problem = real_error(before, tree, added)
        checked.append((problem, added))
        return problem

    monkeypatch.setattr(ma, "clip_edit_error", watched)
    result = build()
    assert result.hems.skirt.clips >= 20 and len(checked) == result.hems.skirt.clips
    # each clip: the skirt's first bones and the face bones it moves (face_retarget), 7 curves each
    assert all(problem is None and added % 7 == 0 and added >= 7 * len(result.hems.skirt.solver.roots)
               for problem, added in checked)

    real_curves, edited = ma.skirt_curves, []

    def damaging(skeleton, skirt, tree, key=None):
        done = real_curves(skeleton, skirt, tree, key)
        edited.append(tree["m_Name"])
        if done and len(edited) == 3:  # the third clip loses an old constant on its way
            tree["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"][0] += 0.25
        return done

    monkeypatch.setattr(ma, "skirt_curves", damaging)
    with pytest.raises(ServiceError) as caught:
        build()
    assert caught.value.code == "source_unsupported"
    assert caught.value.params["source"] == "clip edit not verified"
    assert caught.value.params["clip"] == edited[2]


@official
def test_official_costume_carries_a_shader_the_slot_does_not_load(view, tmp_path):
    """Hina (Dress)'s frill shader lives in a shader bundle Hina's slot does not load. A copy goes with the
    costume into the materials' file, its type taken from the shader's file and its fallback still the player's own
    resource; the copy is the shader as it is."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from ba_modding_toolkit.naming import parse_filename
    from ba_modding_toolkit.utils import compute_crc32
    import UnityPy

    files, index = view
    slot = _slot(files, "hina_original")
    sources = [p for p in files if re.match(r"character-ch0230-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev="hina_original", source_files=sources,
                      source_dev="ch0230", open_writable=Bundle.load)
    assert ma.CARRIED_SHADER in result.cautions  # the switch says so: the game holds two shaders of one name
    out = tmp_path / "out"
    out.mkdir()
    for path, bundle in result.changed.items():
        ok, message = bundle.save(out / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
    written = sorted(out.glob("*.bundle"))
    assert written and all(compute_crc32(p) == int(parse_filename(p.name).crc) for p in written)
    originals = {}
    for path in files:
        if "-_mx-shaders-" in path.name:
            for obj in UnityPy.load(str(path)).objects:
                if obj.type.name == "Shader":
                    tree = obj.read_typetree()
                    originals.setdefault(tree["m_ParsedForm"]["m_Name"], tree)
    carried = []
    for path in written:
        for obj in UnityPy.load(str(path)).objects:
            if obj.type.name != "Material":
                continue
            pointer = obj.read_typetree()["m_Shader"]
            if pointer["m_FileID"] or not pointer["m_PathID"]:
                continue
            shader = obj.assets_file.objects[pointer["m_PathID"]]
            tree = shader.read_typetree()
            name = tree["m_ParsedForm"]["m_Name"]
            carried.append(name)
            assert {k: v for k, v in tree.items() if k != "m_Dependencies"} == {
                k: v for k, v in originals[name].items() if k != "m_Dependencies"}
            assert [(obj.assets_file.externals[d["m_FileID"] - 1].path, d["m_PathID"]) for d in tree["m_Dependencies"]] == [
                ("Library/unity default resources", d["m_PathID"]) for d in originals[name]["m_Dependencies"]]
    assert carried == ["MX/C-Simple-Transparent"]


@official
def test_official_added_parts_hang_below_the_primary_and_the_bounds_hold_the_poses(view, tmp_path):
    """Hoshino <- Hoshino (Swimsuit): the two parts added beside the primary are its children, so a clip or the EX
    timeline that hides the primary hides them too; every renderer's culling box holds its part at rest and in every
    probe pose of the slot's clips."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _key, _prefabs

    files, index = view
    target, source = "hoshino_original", "hoshino_swimsuit"
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=sources, source_dev=source, open_writable=Bundle.load)
    adapted = dict(index)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(tmp_path / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(tmp_path / path.name):
            adapted[cab] = tmp_path / path.name
    with BundleGraph(adapted) as graph:
        prefabs = _prefabs(graph, sorted(tmp_path.glob("character-*")))
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, target)].deref())
        clips = ma.prefab_clips(graph, t, {k: v for k, v in prefabs.items() if target in k})
        poses = [t.world(), *ma.probe_poses(t, clips)]
        s_prefabs = _prefabs(graph, sources)
        s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, source)].deref())
        names = {p.mesh.peek_name() for p in ma.roles(s, None) if p.role in ("primary", "costume")}
        drawn = {}
        for node, renderer in t.renderers:
            tree = graph.tree(renderer)
            mesh = graph.deref(renderer, tree["m_Mesh"])
            if mesh is not None and mesh.peek_name() in names:
                drawn[node] = (renderer, tree, mesh)
        assert len(drawn) == 3
        added = [n for n in drawn if t.nodes[n].parent in drawn]
        assert len(added) == 2 and len({t.nodes[n].parent for n in added}) == 1
        for node, (renderer, tree, mesh) in drawn.items():
            skin = ma.skin_of(mesh)
            bones = [t.by_transform[_key(graph.deref(renderer, b))] for b in tree["m_Bones"]]
            root = t.by_transform[_key(graph.deref(renderer, tree["m_RootBone"]))]
            box = tree["m_AABB"]
            lo = np.array([box["m_Center"][a] - box["m_Extent"][a] for a in "xyz"]) - 1e-4
            hi = np.array([box["m_Center"][a] + box["m_Extent"][a] for a in "xyz"]) + 1e-4
            weighted = skin.weights.sum(axis=1) > 1e-9
            for pose in poses:
                posed = ma.deform(skin, np.array([pose[b] for b in bones]) @ ma.bindposes(graph, mesh))[weighted]
                local = (np.c_[posed, np.ones(len(posed))] @ np.linalg.inv(pose[root]).T)[:, :3]
                assert (local >= lo).all() and (local <= hi).all()


@official
@pytest.mark.parametrize("target,source", [("haruna_original", "ch0193"), ("ch0193", "haruna_original")])
def test_official_a_costume_binds_to_the_rig_the_slot_draws_with(view, tmp_path, target, source):
    """Haruna (Track) holds a second Bip001 rig below her halo: the costume is bound to the rig her body skins to,
    either way round, never to the halo's."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _key, _prefabs

    files, index = view
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=sources, source_dev=source, open_writable=Bundle.load)
    adapted = dict(index)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(tmp_path / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(tmp_path / path.name):
            adapted[cab] = tmp_path / path.name
    with BundleGraph(adapted) as graph:
        prefabs = _prefabs(graph, sorted(tmp_path.glob("character-*")))
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, target)].deref())
        rig = t.nodes[t.main_rig()].path
        primary = next(p for p in ma.roles(t, None) if p.role == "primary")
        bones = [t.nodes[b].path for b in primary.bones if b is not None]
    assert len(t.named("Bip001")) == (2 if target == "ch0193" else 1)
    assert bones and not any(path.startswith("HaloRoot") for path in bones)
    assert not rig.startswith("HaloRoot")


@official
def test_official_optimize_physics_swings_the_chains_the_source_turns(view, tmp_path):
    """Aris <- Yuzu with Optimize Physics: the chain the group choice left rigid off the head, which Yuzu's own clips
    turn, becomes new bones of every prefab of the slot, and the slot's clips that move the head turn it; the rest
    pose stays as bound."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _prefabs

    files, index = view
    target, source = "aris_original", "yuzu_original"
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=sources, source_dev=source, open_writable=Bundle.load, physics=True)
    springs = result.hems.springs
    assert springs and all(s.solver.roots and s.clips >= 20 for s in springs), result.hems.summary()
    assert all(isinstance(s.solver, ma.ms.Spring) for s in springs)
    adapted = dict(index)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(tmp_path / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(tmp_path / path.name):
            adapted[cab] = tmp_path / path.name
    with BundleGraph(adapted) as graph:
        prefabs = _prefabs(graph, sorted(tmp_path.glob("character-*")))
        for spring in springs:
            for pointer in prefabs.values():
                names = {n.name for n in ma.Skeleton.of(graph, pointer.deref()).nodes}
                assert set(spring.names) <= names
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, target)].deref())
        roots = {zlib.crc32(t.nodes[t.first(spring.names[r])].path.encode()) for spring in springs
                 for r in spring.solver.roots}
        turned = [c for c in ma.prefab_clips(graph, t, ma.own_prefabs(prefabs, target))
                  if roots <= {b["path"] for b in graph.tree(c)["m_ClipBindingConstant"]["genericBindings"]
                               if b["attribute"] == 2}]
        # a clip Yuzu has by name plays Yuzu's own keys, for every bone of the chain they turn
        bones = {zlib.crc32(t.nodes[t.first(name)].path.encode()) for spring in springs for name in spring.names}
        keyed = [c for c in turned if len(bones & {b["path"] for b in graph.tree(c)["m_ClipBindingConstant"][
            "genericBindings"] if b["attribute"] == 2}) > len(roots)]
    assert len(turned) == springs[0].clips
    assert keyed  # (Yuzu has every clip Aris has, by name)


@official
def test_official_rule_b_takes_a_skirt_chain_off_a_thigh(view):
    """Ibuki <- Shun with Optimize Physics: beside the skirt's chains off the pelvis, a chain hanging off a thigh
    becomes a new bone below the slot's thigh, turned like the others."""
    from ba_modding_toolkit.bundle import Bundle

    files, index = view
    sources = [p for p in files if re.match(r"character-shun_original-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, "ibuki_original"),
                      target_dev="ibuki_original", source_files=sources, source_dev="shun_original",
                      open_writable=Bundle.load, physics=True)
    skirt = result.hems.skirt
    assert skirt is not None, result.hems.summary()
    with ma.BundleGraph(index) as graph:
        prefabs = ma._model_prefabs(graph, [p for p in _slot(files, "ibuki_original") if p.name.startswith("character-")])
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, "ibuki_original")].deref())
    hangs = {t.nodes[skirt.anchor_of(k)].name for k, parent in enumerate(skirt.parents) if parent < 0}
    assert hangs == {"Bip001 Pelvis", "Bip001 R Thigh"}
    assert skirt.clips >= 20


@official
def test_official_another_characters_face_follows_the_slots_expressions_about_its_own_places(view, tmp_path):
    """Aris <- Yuzu: Yuzu's eye, brow and lid bones rest one to two centimetres from Aris's of the same names. They
    become new bones of the slot below the head, where Yuzu's face has them, and in the saved clips each turns and
    moves against the head as Aris's bone of its name does, about its own place; the same student's face (Midori
    <- Midori (Game Dev)) keeps the slot's bones."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _key, _prefabs

    files, index = view
    same = ma.adapt(index=index, resolver=None, target_files=_slot(files, "midori_original"),
                    target_dev="midori_original", source_files=[p for p in files if re.match(
                        r"character-midori_en0004-_mxload", p.name)], source_dev="midori_en0004",
                    open_writable=Bundle.load)
    assert same.hems.face is None
    target, source = "aris_original", "yuzu_original"
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=[p for p in files if re.match(rf"character-{source}-_mxload", p.name)],
                      source_dev=source, open_writable=Bundle.load)
    face = result.hems.face
    assert face is not None and len(face.sources) >= 12 and face.clips >= 10
    solver = face.solver
    adapted = dict(index)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(tmp_path / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(tmp_path / path.name):
            adapted[cab] = tmp_path / path.name
    with BundleGraph(adapted) as graph:
        prefabs = _prefabs(graph, sorted(tmp_path.glob("character-*")))
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, target)].deref())
        head = t.first("Bip001 Head")
        new = [t.first(name) for name in face.names]
        slots = [t.first(name.removesuffix("_costume")) for name in face.names]
        rest = t.world()
        rel0 = [np.linalg.inv(rest[head]) @ rest[m] for m in slots]
        new0 = [np.linalg.inv(rest[head]) @ rest[n] for n in new]
        assert all(np.linalg.norm(a[:3, 3] - b[:3, 3]) > 1e-3 for a, b in zip(rel0, new0))  # apart at rest
        hashes = {zlib.crc32(t.nodes[n].path.encode()) for n in new}
        clip = next(c for c in ma.prefab_clips(graph, t, ma.own_prefabs(prefabs, target))
                    if hashes & {b["path"] for b in graph.tree(c)["m_ClipBindingConstant"]["genericBindings"]})
        times, pose, _ = t.clip_frames(graph.tree(clip))
        moved = 0
        for f in range(len(times)):
            world = pose(f)
            back = np.linalg.inv(world[head])
            for k, (n, m) in enumerate(zip(new, slots)):
                slot_rel, new_rel = back @ world[m], back @ world[n]
                turn = ma._rotation(slot_rel) @ ma._rotation(rel0[k]).T
                assert np.allclose(ma._rotation(new_rel) @ ma._rotation(new0[k]).T, turn, atol=2e-3)
                assert np.allclose(new_rel[:3, 3] - new0[k][:3, 3], slot_rel[:3, 3] - rel0[k][:3, 3], atol=2e-4)
                moved += not np.allclose(turn, np.eye(3), atol=1e-3)
        assert moved
    assert isinstance(solver, ma.FaceRetarget)


@official
def test_official_a_bag_mounted_on_an_upper_arm_hangs_from_the_shoulder(view):
    """Airi <- Airi (Band): the shoulder bag and its strap hang from a mount below Airi (Band)'s right upper arm that
    Airi lacks and that skins nothing itself. On Airi they go with the right clavicle, not with the arm, so the bag
    stays at her side when she lifts the arm."""
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.gameassets.model import _prefabs

    files, index = view
    sources = [p for p in files if re.match(r"character-ch0251-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, "airi_original"),
                      target_dev="airi_original", source_files=sources, source_dev="ch0251", open_writable=Bundle.load)
    with ma.BundleGraph(index) as graph:
        s_prefabs = _prefabs(graph, sources)
        s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, "ch0251")].deref())
        t_prefabs = _prefabs(graph, [p for p in _slot(files, "airi_original") if p.name.startswith("character-")])
        t = ma.Skeleton.of(graph, t_prefabs[ma.choose_prefab(t_prefabs, "airi_original")].deref())
    bag = [n for n in result.plan.mapping if s.nodes[n].name.startswith("bone_bag")]
    assert bag and {t.nodes[result.plan.mapping[n]].name for n in bag} == {"Bip001 R Clavicle"}
    # a sleeve's twist bone the slot lacks still goes with its arm: only a mount holding nothing but props moves
    assert ma._shoulder_mount(t, s, s.first("Bip001 R UpperArm"), np.zeros(0, int)) is None


@official
def test_official_a_prop_the_clips_hold_on_the_body_goes_with_the_body(view):
    """Hina <- Aoba: the pipe wrench, skinned in Aoba's body mesh below a Prop_root Hina lacks, rests 40 cm beside
    her, and every clip of hers keys it to her hip. On Hina it goes with the pelvis and sits against it where Aoba's
    idle holds it against hers, so Hina's clips take it along instead of leaving it where it rests."""
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.gameassets.model import _prefabs

    files, index = view
    sources = [p for p in files if re.match(r"character-ch0288-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, "hina_original"),
                      target_dev="hina_original", source_files=sources, source_dev="ch0288", open_writable=Bundle.load)
    with ma.BundleGraph(index) as graph:
        s_prefabs = _prefabs(graph, sources)
        s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, "ch0288")].deref())
        t_prefabs = _prefabs(graph, [p for p in _slot(files, "hina_original") if p.name.startswith("character-")])
        t = ma.Skeleton.of(graph, t_prefabs[ma.choose_prefab(t_prefabs, "hina_original")].deref())
        idle = s.poses(ma.pick(ma.prefab_clips(graph, s, ma.own_prefabs(s_prefabs, "ch0288")), ["Formation_Idle"]),
                       frames=1)[0]
        parts = ma.roles(s, idle)
        parts = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]  # as adapt does
        flat = ma.flatten(ma.Costume(s, parts, [ma.skin_of(p.mesh) for p in parts]), s.world())
    wrench, t_pelvis, s_pelvis = s.first("bone_pipe wrench"), t.first("Bip001 Pelvis"), s.first("Bip001 Pelvis")
    assert result.plan.mapping[wrench] == t_pelvis
    # its vertices, as bound on the slot, against the slot's pelvis at rest: where Aoba's idle holds them against hers
    s_rest, t_rest = s.world(), t.world()
    rows = np.where(((flat.joints == wrench) & (flat.weights > 0)).any(axis=1))[0]
    assert len(rows)
    bound = np.c_[result.plan.fit.vertices[rows], np.ones(len(rows))] @ np.linalg.inv(t_rest[t_pelvis]).T
    held = np.c_[flat.vertices[rows], np.ones(len(rows))] @ (
        np.linalg.inv(idle[s_pelvis]) @ idle[wrench] @ np.linalg.inv(s_rest[wrench])).T
    assert np.abs(bound - held).max() < 0.01
    assert np.linalg.norm(bound[:, :3].mean(axis=0)) < 0.25  # at her hip, not 40 cm beside her
    # the cart, the bag and the screwdriver are props of their own renderers, not of the costume: never carried
    assert not {s.nodes[n].name for n in result.plan.mapping} & {"bone_cart_body", "bone_bag", "bone_screwdriver"}


@official
def test_official_a_head_set_on_a_longer_neck_keeps_its_neck(view):
    """Aris <- Yuzu: Aris's Head joint sits 1.8 cm higher inside her head than Yuzu's. Carried onto it, Yuzu's
    head rose out of her high collar (its yellow lining showed from above) and the neck stretched up to 1.8 times.
    The head goes with the neck: at the slot's rest no edge of the neck and head grows past 1.1 times, and Yuzu's head
    joint lands 1.8 cm below Aris's."""
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.gameassets.model import _prefabs

    files, index = view
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, "aris_original"),
                      target_dev="aris_original", source_files=_slot(files, "yuzu_original"),
                      source_dev="yuzu_original", open_writable=Bundle.load)
    with ma.BundleGraph(index) as graph:
        s_prefabs = _prefabs(graph, [p for p in _slot(files, "yuzu_original") if p.name.startswith("character-")])
        s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, "yuzu_original")].deref())
        idle = s.poses(ma.pick(ma.prefab_clips(graph, s, ma.own_prefabs(s_prefabs, "yuzu_original")),
                               ma.IDLE_CLIPS)[:1], frames=1)[0]
        parts = ma.roles(s, idle)
        parts = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]  # as adapt does
        flat = ma.flatten(ma.Costume(s, parts, [ma.skin_of(p.mesh) for p in parts]), s.world())
    fit = result.plan.fit
    assert fit.shape in ("moved", "refitted") and fit.head is not None
    assert fit.head[1] == pytest.approx(-0.018, abs=0.002) and abs(fit.head[0]) < 1e-4
    dominant = np.array([row[np.argmax(w)] for row, w in zip(flat.joints, flat.weights)])
    upper = np.isin(dominant, [s.first("Bip001 Neck"), s.first("Bip001 Head")])
    edges = flat.edges[upper[flat.edges[:, 0]] & upper[flat.edges[:, 1]]]
    before = np.linalg.norm(flat.vertices[edges[:, 0]] - flat.vertices[edges[:, 1]], axis=1)
    after = np.linalg.norm(fit.vertices[edges[:, 0]] - fit.vertices[edges[:, 1]], axis=1)
    kept = before > 1e-6
    assert kept.sum() > 1000
    assert (after[kept] / before[kept]).max() <= 1.1  # it stretched 1.77 times


@official
def test_official_bones_any_clip_scales_away_are_hidden(view):
    """The probes see six clips at three frames each; the slot's other clips hide bones too (a hair ornament in the
    cafe, a weapon bone in the cafe, skewers in a public skill). They and the bones below them are hidden, so no
    costume bone maps onto them and collapses there; a clip that hides the body is no reason to hide it."""
    from bmpt.gameassets.model import _prefabs

    files, index = view
    with ma.BundleGraph(index) as graph:
        prefabs = _prefabs(graph, [p for p in _slot(files, "ch0215") if p.name.startswith("character-")])
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, "ch0215")].deref())
        clips = ma.prefab_clips(graph, t, {k: v for k, v in prefabs.items() if "ch0215" in k})
        probed = ma.hidden_bones(t, ma.probe_poses(t, clips))
        hidden = ma.scaled_away(t, clips)
    names = {t.nodes[i].name for i in hidden - probed}
    assert {"bone_hair_acc", "Bip001_Weapon"} <= names
    acc = t.first("bone_hair_acc")
    assert set(t.descendants(acc)) <= hidden
    assert not any(ma.BIPED_CHAIN.match(t.nodes[i].name) for i in hidden)


@official
@pytest.mark.parametrize("target,source,index,uv,on_primary,default,atlas", [
    # one shared mouth atlas: the source's default tile
    ("azusa_original", "mashiro_original", 2, (0.125, 0.5), True, (0.125, 0.5), ("Character_Mouth", 0.5, 8.0)),
    ("aris_original", "yuzu_original", 3, (0.125, 0.5), True, (0.125, 0.5), ("Character_Mouth", 0.5, 8.0)),
    # the mouth on a part beside the primary; Hina's clips name cells of her own 4 x 4 atlas, Aoba draws the shared
    # 8 x 8 one: the mouth takes Hina's atlas and default tile
    ("hina_original", "ch0288", 3, (0.75, 0.625), False, (0.0, 0.75), ("Hina_Mouth", 1.0, 4.0)),
])
def test_official_mouth_receivers_follow_the_costume(view, tmp_path, target, source, index, uv, on_primary, default,
                                                     atlas):
    """The slot's mouth receivers index the renderer's materials, now the source's in the source's order: they take
    the source receiver's material index, and point at the renderer drawing the source's mouth, an added part where
    the source's mouth is not on its primary. The slot's clips name mouth tiles as cells of the slot's own atlas: on
    the same atlas the receivers take the source's default tile; a source mouth on another atlas draws the slot's
    (its texture, scale and grid) and the receivers keep the slot's default tile, so every SetMouthTile event of the
    slot's clips draws the mouth the slot draws."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _prefabs

    files, index_ = view
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    with ma.BundleGraph(index_) as graph:
        s_prefabs = _prefabs(graph, sources)
        s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, source)].deref())
        parts = ma.roles(s, None)
        costume = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]
        mouth = ma.mouth_of(s, costume)
        assert mouth is not None and (mouth.index, (mouth.uv["x"], mouth.uv["y"])) == (index, uv)
        mouth_mesh = costume[mouth.part].mesh.peek_name()
    assert (mouth.part == 0) == on_primary
    with ma.BundleGraph(index_) as graph:
        t_prefabs = _prefabs(graph, [p for p in _slot(files, target) if p.name.startswith("character-")])
        t = ma.Skeleton.of(graph, t_prefabs[ma.choose_prefab(t_prefabs, target)].deref())
        parts = ma.roles(t, None)
        t_costume = [p for p in parts if p.role == "primary"] + [p for p in parts if p.role == "costume"]
        t_mouth = ma.mouth_of(t, t_costume)
        slot_atlas = ma.mouth_atlas(graph, t_costume[t_mouth.part].materials[t_mouth.index])
        slot_name = graph.deref(slot_atlas.material, slot_atlas.env["m_Texture"]).peek_name()
    result = ma.adapt(index=index_, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=sources, source_dev=source, open_writable=Bundle.load)
    adapted = dict(index_)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(tmp_path / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(tmp_path / path.name):
            adapted[cab] = tmp_path / path.name
    receivers = 0
    with BundleGraph(adapted) as graph:
        for pointer in _prefabs(graph, sorted(tmp_path.glob("character-*"))).values():
            t = ma.Skeleton.of(graph, pointer.deref())
            for behaviour in t.behaviours:
                tree = graph.tree(behaviour)
                if "MouthRenderer" not in tree:
                    continue
                renderer = graph.deref(behaviour, tree["MouthRenderer"])
                drawn = graph.deref(renderer, graph.tree(renderer)["m_Mesh"])
                materials = [graph.deref(renderer, m) for m in graph.tree(renderer)["m_Materials"]]
                assert drawn.peek_name() == mouth_mesh
                assert tree["MouthMaterialIndex"] == index
                assert (tree["MouthDefaultUV"]["x"], tree["MouthDefaultUV"]["y"]) == default
                assert ma._shader_kind(graph, materials[index]) == "eyemouth"
                drawn_atlas = ma.mouth_atlas(graph, materials[index])
                assert drawn_atlas.texture == slot_atlas.texture and slot_name == atlas[0]  # the slot's own texture
                texture = graph.deref(materials[index], drawn_atlas.env["m_Texture"])
                assert (texture.peek_name(), drawn_atlas.env["m_Scale"]["x"], drawn_atlas.grid["_MouthTileCols"]) == atlas
                receivers += 1
    assert receivers == 4  # one per prefab of the slot


@official
def test_official_costume_takes_its_own_materials_on_a_body_drawn_from_two_material_files(view, tmp_path):
    """Hifumi (Swimsuit)'s body takes its materials from two files. The slot's body renderer was taken for a
    copy and kept its own materials in its own order, so Hifumi's face drew with the swimsuit's hair material. The
    body renderer now draws the costume with the costume's materials, in its submeshes' order."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph, _prefabs

    files, index = view
    slot = _slot(files, "ch0058")
    sources = [p for p in files if re.match(r"character-hihumi_original-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev="ch0058", source_files=sources,
                      source_dev="hihumi_original", open_writable=Bundle.load)
    out = tmp_path / "out"
    out.mkdir()
    for path, bundle in result.changed.items():
        ok, message = bundle.save(out / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
    adapted = dict(index)
    for path in out.glob("*.bundle"):
        for cab in cab_names(path):
            adapted[cab] = path
    graph = BundleGraph(adapted)
    t_prefabs = _prefabs(graph, [p for p in out.glob("*.bundle") if p.name.startswith("character-")])
    s_prefabs = _prefabs(graph, sources)
    t = ma.Skeleton.of(graph, t_prefabs[ma.choose_prefab(t_prefabs, "ch0058")].deref())
    s = ma.Skeleton.of(graph, s_prefabs[ma.choose_prefab(s_prefabs, "hihumi_original")].deref())
    names = lambda part: [graph.tree(m)["m_Name"] for m in part.materials]  # noqa: E731
    body = next(p for p in ma.roles(t, None) if p.role == "primary")
    own = next(p for p in ma.roles(s, None) if p.role == "primary")
    assert names(body) == names(own) == ["Hihumi_Original_Face", "Hihumi_Original_Eyebrow",
                                         "Hihumi_Original_EyeMouth", "Hihumi_Original_Hair", "Hihumi_Original_Body"]


def _cross_build(view, tmp_path, source, target="azusa_original", results=None):
    """``target``'s slot (Azusa's) carrying ``source``'s costume as another student's (with its halo), saved
    with the CRC fix: the written files, the CAB index over them, the official graph and the read-back graph; the
    build's result goes into ``results``."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from ba_modding_toolkit.naming import parse_filename
    from ba_modding_toolkit.utils import compute_crc32
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph

    files, index = view
    slot = _slot(files, target)
    sources = [p for p in files if re.match(rf"(character-{source}-_mxload|assets-_mx-npcs-{source}-)", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev=target, source_files=sources,
                      source_dev=source, open_writable=Bundle.load, halo=True)
    if results is not None:
        results.append(result)
    out = tmp_path / "out"
    out.mkdir()
    for path, bundle in result.changed.items():
        ok, message = bundle.save(out / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
    written = sorted(out.glob("*.bundle"))
    assert written and all(compute_crc32(p) == int(parse_filename(p.name).crc) for p in written)
    adapted = dict(index)
    for path in written:
        for cab in cab_names(path):
            adapted[cab] = path
    return slot, sources, written, adapted, BundleGraph(index), BundleGraph(adapted)


def _fx(graph):
    """The halo particles' reader of a model export (service.model_glb's)."""
    from bmpt.gameassets.service import _FxResolver

    return lambda objects: _FxResolver(graph, dict(objects), {}).resolve


def _official_objects_kept(slot, written, allowed):
    """Every object of the official files is still there, byte for byte, but those of ``allowed`` types; the new
    objects by (file, path id) with their types."""
    import UnityPy

    new = {}
    for path in written:
        before = {o.path_id: o for o in UnityPy.load(str(next(p for p in slot if p.name == path.name))).objects}
        after = {o.path_id: o for o in UnityPy.load(str(path)).objects}
        assert before.keys() <= after.keys()
        changed = {before[pid].type.name for pid in before if before[pid].get_raw_data() != after[pid].get_raw_data()}
        assert changed <= set(allowed) | {"AnimationClip"}, changed  # another face's bones get curves (face_retarget)
        new.update({(after[pid].assets_file.name.lower(), pid): after[pid].type.name for pid in after.keys() - before.keys()})
    return new


@official
def test_official_cross_costume_carries_the_source_halo(view, tmp_path):
    """Another student's costume (Azusa <- Yukino's NPC model, ch0171_enemy) carries its halo: Yukino's halo
    mesh (streamed from its bundle's resource file, taken along), material and texture are new objects in the slot's
    files, Azusa's halo renderer in every prefab of the slot draws them where Yukino's draws her own (her extra level
    folded into its node), and the follower takes Yukino's offset. Azusa's own halo objects stay as they were; the slot
    exports, the halo with it."""
    from bmpt.gameassets.model import _key, _prefabs, export_chibi, model_root, mpos, read_mesh

    slot, sources, written, adapted, official, graph = _cross_build(view, tmp_path, "ch0171_enemy")
    new = _official_objects_kept(slot, written, {"SkinnedMeshRenderer", "MeshRenderer", "MeshFilter", "AssetBundle",
                                                 "Transform", "MonoBehaviour"})
    o_prefabs = _prefabs(official, [p for p in slot if p.name.startswith("character-")])
    before = ma.halo_of(ma.Skeleton.of(official, o_prefabs[ma.choose_prefab(o_prefabs, "azusa_original")].deref()))
    s = ma.Skeleton.of(official, model_root(official, sources, "ch0171_enemy")[0])
    source = ma.halo_of(s)
    assert before.shape == source.shape == "single"
    assert s.nodes[source.node].path == "HaloRoot/CH0171_Enemy_Halo/CH0171_Halo"  # an extra level
    s_world = s.world()
    placed = np.linalg.inv(s_world[source.root]) @ s_world[source.node]
    prefab_files = [p for p in written if p.name.startswith("character-")]
    t_prefabs = _prefabs(graph, prefab_files)
    for name, pointer in t_prefabs.items():
        if "azusa_original" not in name:
            continue
        t = ma.Skeleton.of(graph, pointer.deref())
        halo = ma.halo_of(t)
        assert halo.shape == "single" and t.nodes[halo.node].path == "HaloRoot/Azusa_Original_Halo", name
        # new objects: Yukino's halo mesh with its vertex data, her materials and the texture they draw
        assert new.get(_key(halo.mesh)) == "Mesh" and _key(halo.mesh) != _key(before.mesh)
        assert halo.mesh.peek_name() == source.mesh.peek_name()
        got, want = read_mesh(halo.mesh.read()), read_mesh(source.mesh.read())
        assert np.allclose(got.vertices, want.vertices) and got.triangles == want.triangles and got.uv0 == want.uv0
        assert [new.get(_key(m)) for m in halo.materials] == ["Material"] * len(source.materials)
        assert [graph.tree(m)["m_Name"] for m in halo.materials] == [official.tree(m)["m_Name"] for m in source.materials]
        textures = [graph.deref(m, v["m_Texture"]) for m in halo.materials
                    for _, v in graph.tree(m)["m_SavedProperties"]["m_TexEnvs"] if v["m_Texture"]["m_PathID"]]
        assert textures and all(new.get(_key(texture)) == "Texture2D" for texture in textures)
        # drawn where Yukino's is, from the slot's own nodes; the follower takes her offsets
        world = t.world()
        assert np.allclose(np.linalg.inv(world[halo.root]) @ world[halo.node], placed, atol=1e-5)
        for field in ma.FOLLOWER_OFFSETS:
            assert graph.tree(halo.follower)[field] == pytest.approx(official.tree(source.follower)[field])
    data = export_chibi(graph, prefab_files, "azusa_original")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    names = {m["name"] for m in doc["meshes"]}
    assert source.mesh.peek_name() in names and before.mesh.peek_name() not in names and doc["animations"]
    follower = doc["extras"]["haloFollower"]
    assert follower["relativePosition"] == pytest.approx(
        list(mpos([official.tree(source.follower)["TargetRelativePosition"][k] for k in "xyz"])))


def test_only_leg_anchor_groups_set_to_follow_are_judged_again_by_pass_through():
    """Optimize Physics judges the leg-anchor groups the group choice set to follow again; a rigid group, a
    group of another Biped bone and an empty group are not among them."""
    skeleton = _skeleton([("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Pelvis", 1, (0, 1, 0), (0, 0, 0, 1)),
                          ("Bip001 L Thigh", 2, (0.1, 0, 0), (0, 0, 0, 1)), ("Bip001 R Thigh", 2, (-0.1, 0, 0), (0, 0, 0, 1)),
                          ("Bip001 Spine", 2, (0, 0.2, 0), (0, 0, 0, 1))])
    pelvis, left, right, spine = (skeleton.first(n) for n in ("Bip001 Pelvis", "Bip001 L Thigh", "Bip001 R Thigh", "Bip001 Spine"))
    plan = ma.Plan({}, {pelvis: "follow", left: "rigid", right: "follow", spine: "follow"}, 1.0, 1.0,
                   {pelvis: [10, 11], left: [12], right: [], spine: [13]})
    anchors = {pelvis, left, right}
    assert ma.followed_groups(plan, skeleton, anchors) == {pelvis: [10, 11]}
    assert ma.followed_groups(plan, skeleton, anchors | {spine}) == {pelvis: [10, 11], spine: [13]}


@official
def test_optimize_physics_judges_a_followed_skirt_again_by_pass_through(view):
    """Azusa <- Yukino's NPC model. The group choice sets Yukino's skirt (18 bones off the pelvis) to follow
    Azusa's own skirt chains, which leaves nothing rigid: the build without Optimize Physics measures nothing, and
    the skirt passes through the legs in 19 % of the measured frames' covered samples (Vital_Death 40 %). With
    ``physics`` the pelvis group is judged again as if it were rigid: a rule of the regrouped build (the skirt as new
    bones) beats the follow result within the same stretch ceiling and the no-clip-worse rule."""
    from ba_modding_toolkit.bundle import Bundle

    files, index = view
    slot = _slot(files, "azusa_original")
    sources = [p for p in files if re.match(r"character-ch0171_enemy-_mxload", p.name)]

    def build(physics):
        return ma.adapt(index=index, resolver=None, target_files=slot, target_dev="azusa_original", source_files=sources,
                        source_dev="ch0171_enemy", open_writable=Bundle.load, halo=True, physics=physics).hems

    default = build(False)
    assert (default.rule, default.note, default.skirt, default.regrouped) == ("rigid", "no rigid cloth", None, False)
    hems = build(True)
    assert hems.regrouped and hems.skirt is not None and hems.rule.startswith(ma.REGROUP + "skirt"), hems.summary()
    follow, chosen = hems.scores[0], hems.score(hems.rule)
    assert follow.rule == "rigid" and follow.mean > 10  # the baseline: Yukino's skirt follows Azusa's, through her legs
    assert chosen.mean < follow.mean
    assert all(chosen.deep[clip] <= follow.deep[clip] + 5 for clip in follow.deep)
    assert chosen.stretch <= max(hems.limit, follow.stretch)
    # her skirt chains become bones of the slot, and the vertices that left the followed bones are written
    assert hems.skirt.clips >= 20 and any(change is not None for change in hems.parts)


def _skirt_case():
    """A source with a front skirt chain off the pelvis and a strap off the left thigh, on a slot of the same Biped
    without them; the cloth, two leg vertices per leg, and the plan's mapping and groups."""
    biped = [("Bip001", 0, (0, 0, 0), (0, 0, 0, 1)), ("Bip001 Pelvis", 1, (0, 1, 0), (0, 0, 0, 1)),
             ("Bip001 L Thigh", 2, (0.1, 0, 0), (0, 0, 0, 1)), ("Bip001 R Thigh", 2, (-0.1, 0, 0), (0, 0, 0, 1)),
             ("Bip001 L Calf", 3, (0, -0.45, 0), (0, 0, 0, 1)), ("Bip001 R Calf", 4, (0, -0.45, 0), (0, 0, 0, 1)),
             ("Bip001 L Foot", 5, (0, -0.45, 0), (0, 0, 0, 1)), ("Bip001 R Foot", 6, (0, -0.45, 0), (0, 0, 0, 1))]
    chains = [("skirt_F_1", 2, (0, -0.05, 0.15), (0, 0, 0, 1)), ("skirt_F_2", 9, (0, -0.2, 0), (0, 0, 0, 1)),
              ("strap_L_1", 3, (0.08, -0.05, 0), (0, 0, 0, 1)), ("strap_L_2", 11, (0, -0.2, 0), (0, 0, 0, 1))]
    ts, ss = _skeleton(biped), _skeleton(biped + chains)
    points = [(-0.05, 0.95, 0.16), (0.05, 0.95, 0.16), (-0.05, 0.75, 0.16), (0.05, 0.75, 0.16),  # the skirt
              (0.18, 0.95, 0.02), (0.19, 0.95, -0.02), (0.18, 0.75, 0.02), (0.19, 0.75, -0.02),  # the strap
              (0.1, 0.8, 0.05), (0.1, 0.5, 0.05), (-0.1, 0.8, 0.05), (-0.1, 0.5, 0.05)]  # the legs
    joints = [9, 9, 10, 10, 11, 11, 12, 12, 3, 5, 4, 6]
    flat = _flat(points, joints)
    flat.edges = np.array([[0, 1], [0, 2], [1, 3], [2, 3], [4, 5], [4, 6], [5, 7], [6, 7], [1, 4]])
    mapping = {**{n: n for n in range(1, 9)}, 9: 2, 10: 2, 11: 3, 12: 3}
    groups = {2: [9, 10], 3: [11, 12]}
    return ts, ss, flat, mapping, groups


def test_a_regrouped_skirt_build_carries_its_own_new_bones():
    """Optimize Physics builds Rule B twice when the pelvis group follows: as the group choice left it (the strap off
    the thigh alone) and as if the followed group were rigid (the skirt off the pelvis too). Each candidate carries
    the new bones of its own build: the regrouped one has more, and the rule chosen writes the bones its weights'
    columns name (Shigure <- Hina failed to build when a regrouped rule won with the default build's bones)."""
    ts, ss, flat, mapping, groups = _skirt_case()
    t_legs, s_legs = ma.legs_of(ts), ma.legs_of(ss)
    pelvis, thigh_l, thigh_r = s_legs["pelvis"], s_legs["thigh L"], s_legs["thigh R"]
    plan = ma.Plan(mapping, {1: "rigid", pelvis: "follow", thigh_l: "rigid", thigh_r: "rigid"}, 1.0, 1.0, groups)
    lowered = ma.Plan(mapping, {**plan.choices, pelvis: "rigid"}, 1.0, 1.0, groups)
    dominant = flat.joints[:, 0]
    cloth = np.arange(len(flat.vertices)) < 8
    legs = np.arange(8, 12)
    mapped = np.vectorize(lambda n: mapping.get(int(n), 0), otypes=[int])(flat.joints)
    columns = sorted(set(mapped[flat.weights > 0].tolist()) | {t_legs[k] for k in ma.FIT_KEYS})
    writable = np.ones(len(flat.vertices), bool)
    built = {}
    for name, p in (("default", plan), ("regrouped", lowered)):
        built[name] = ma._skirt_candidates(ts, ss, p, flat, flat.vertices, dominant, cloth, legs, t_legs, s_legs,
                                           mapped, columns, writable, [pelvis, thigh_l, thigh_r])
        assert built[name] and len({id(c.skirt) for c in built[name]}) == 1  # one build's candidates share its bones
        for candidate in built[name]:
            assert candidate.weights.shape[1] == len(columns) + len(candidate.skirt.sources)
            assert candidate.skirt.solver is not None
    default, regrouped = built["default"][0].skirt, built["regrouped"][0].skirt
    assert [ss.nodes[n].name for n in default.sources] == ["strap_L_1", "strap_L_2"]
    assert [ss.nodes[n].name for n in regrouped.sources] == ["skirt_F_1", "skirt_F_2", "strap_L_1", "strap_L_2"]
    assert default.anchor_of(0) == thigh_l and regrouped.anchor_of(0) == pelvis


@official
def test_official_optimize_physics_builds_a_regrouped_skirt_with_its_own_bones(view):
    """Shigure <- Hina with Optimize Physics: Hina's skirt follows Shigure's own chains, and the rule chosen is built
    as if that group were rigid, its chains more than the default build's (it failed to build: the weights of one
    build, the bones of the other). It builds, its new bones are the regrouped build's, and the written weights name
    only the slot's nodes and those bones."""
    from ba_modding_toolkit.bundle import Bundle

    files, index = view
    slot = _slot(files, "shigure_original")
    sources = [p for p in files if re.match(r"character-hina_original-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev="shigure_original",
                      source_files=sources, source_dev="hina_original", open_writable=Bundle.load, halo=True,
                      physics=True)
    hems = result.hems
    assert hems.regrouped and hems.rule.startswith(ma.REGROUP + "skirt"), hems.summary()
    skirt = hems.skirt
    assert len(skirt.sources) == len(skirt.names) == len(skirt.parents) == len(skirt.locals) > 0
    assert skirt.clips > 0 and skirt.solver.roots
    new = set(range(skirt.base, skirt.base + len(skirt.sources)))
    written = {int(n) for change in hems.parts if change is not None for n in change[1].ravel().tolist()}
    assert written & new and all(n < skirt.base or n in new for n in written)
    assert all(result.plan.mapping[n] == skirt.node(k) for k, n in enumerate(skirt.sources))


@official
def test_official_cross_costume_without_a_halo_hides_the_slots(view, tmp_path):
    """Another student's costume from a model without a halo (CH9999) hides the slot's: its renderer is turned
    off and draws no mesh, so a clip that turns it on shows nothing either; its nodes and objects stay."""
    from bmpt.gameassets.model import _key, _prefabs, export_chibi

    slot, sources, written, adapted, official, graph = _cross_build(view, tmp_path, "ch9999")
    s_prefabs = _prefabs(official, sources)
    assert ma.halo_of(ma.Skeleton.of(official, s_prefabs[ma.choose_prefab(s_prefabs, "ch9999")].deref())).shape == "none"
    new = _official_objects_kept(slot, written, {"SkinnedMeshRenderer", "MeshRenderer", "MeshFilter", "AssetBundle",
                                                 "Transform", "MonoBehaviour"})
    assert "Mesh" in new.values()  # the costume's
    o_prefabs = _prefabs(official, [p for p in slot if p.name.startswith("character-")])
    before = ma.halo_of(ma.Skeleton.of(official, o_prefabs[ma.choose_prefab(o_prefabs, "azusa_original")].deref()))
    prefab_files = [p for p in written if p.name.startswith("character-")]
    for name, pointer in _prefabs(graph, prefab_files).items():
        if "azusa_original" not in name:
            continue
        t = ma.Skeleton.of(graph, pointer.deref())
        node = t.nodes[t.first("Azusa_Original_Halo")]
        renderer, filt = graph.component(node.go, "MeshRenderer"), graph.component(node.go, "MeshFilter")
        assert not graph.tree(renderer)["m_Enabled"] and not graph.tree(filt)["m_Mesh"]["m_PathID"], name
        assert ma.halo_of(t).shape == "no mesh"
    data = export_chibi(graph, prefab_files, "azusa_original")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert before.mesh.peek_name() not in {m["name"] for m in doc["meshes"]} and doc["animations"]


@official
def test_official_cross_costume_carries_a_skinned_halo(view, tmp_path):
    """Miyu (ch0145) <- Ayame (Twilight)'s NPC model (np0229), whose halo has no follower: one halo-shaded
    renderer skinned to bone_halo below the head. Its mesh, material and texture are new objects in the slot's files;
    Miyu's halo renderer draws it, unskinned, where Ayame's bone draws it against the head (the follower moves the
    followed node there), moved along with the head where the bound costume's head sits off Miyu's head joint;
    Miyu's own halo mesh is gone from the export."""
    from bmpt.gameassets.model import _key, _prefabs, export_chibi, model_root, read_mesh

    results = []
    slot, sources, written, adapted, official, graph = _cross_build(view, tmp_path, "np0229", "ch0145", results)
    moved = results[0].plan.fit.head if results[0].plan.fit.head is not None else np.zeros(3)
    new = _official_objects_kept(slot, written, {"SkinnedMeshRenderer", "MeshRenderer", "MeshFilter", "AssetBundle",
                                                 "Transform", "MonoBehaviour"})
    s = ma.Skeleton.of(official, model_root(official, sources, "np0229")[0])
    source = ma.halo_of(s)
    assert source.shape == "skinned" and s.nodes[source.bone].name == "bone_halo"
    s_world = s.world()
    bind = np.array([[official.tree(source.mesh)["m_BindPose"][0][f"e{r}{c}"] for c in range(4)] for r in range(4)])
    want = np.linalg.inv(s_world[source.head]) @ s_world[source.bone] @ bind  # the mesh against the head
    o_prefabs = _prefabs(official, [p for p in slot if p.name.startswith("character-")])
    before = ma.halo_of(ma.Skeleton.of(official, o_prefabs[ma.choose_prefab(o_prefabs, "ch0145")].deref()))
    assert before.shape == "single"
    prefab_files = [p for p in written if p.name.startswith("character-")]
    checked = 0
    for name, pointer in _prefabs(graph, prefab_files).items():
        t = ma.Skeleton.of(graph, pointer.deref())
        halo = ma.halo_of(t)
        if halo.shape != "single":
            continue
        checked += 1
        assert new.get(_key(halo.mesh)) == "Mesh" and halo.mesh.peek_name() == source.mesh.peek_name(), name
        got, mesh = read_mesh(halo.mesh.read()), read_mesh(source.mesh.read())
        assert np.allclose(got.vertices, mesh.vertices) and got.triangles == mesh.triangles
        assert [new.get(_key(m)) for m in halo.materials] == ["Material"]
        # the follower keeps the followed node at the bone's place against the head: the mesh lands where Ayame's is
        f = graph.tree(halo.follower)
        target = t.by_transform[_key(graph.deref(halo.follower, f["FollowTarget"]))]
        assert t.nodes[target].name == ma.HEAD
        world = t.world()
        p, q = f["TargetRelativePosition"], f["TargetRelativeRotation"]
        # inside the follower's clamp box, which moved with the offset: the follower leaves it there
        assert all(f["ClampMinOffset"][k] <= p[k] <= f["ClampMaxOffset"][k] for k in "xyz")
        head = world[target]
        turn = head[:3, :3] / np.linalg.norm(head[:3, :3], axis=0)
        followed = np.eye(4)
        followed[:3, :3] = turn @ ma.trs((0, 0, 0), [q[k] for k in "xyzw"], (1, 1, 1))[:3, :3] * np.linalg.norm(
            world[halo.root][:3, :3], axis=0)
        followed[:3, 3] = (head @ [p["x"], p["y"], p["z"], 1])[:3]
        drawn = followed @ np.linalg.inv(world[halo.root]) @ world[halo.node]
        head_s = np.linalg.norm(head[:3, :3], axis=0)
        source_head = np.linalg.norm(s_world[source.head][:3, :3], axis=0)
        assert np.allclose(head_s, source_head, rtol=1e-3)  # same head scale, so the placements compare directly
        shifted = want.copy()
        shifted[:3, 3] += np.linalg.inv(head)[:3, :3] @ moved  # with the head, in the head's space
        assert np.allclose(np.linalg.inv(head) @ drawn, shifted, atol=1e-4), name
    assert checked
    data = export_chibi(graph, prefab_files, "ch0145")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    names = {m["name"] for m in doc["meshes"]}
    assert source.mesh.peek_name() in names and before.mesh.peek_name() not in names and doc["animations"]


@official
@pytest.mark.parametrize("source,shape", [
    ("binah_original", "halo without a follower"),  # a boss: no head bone
    ("chesed_original", "halo without a follower"),  # a boss: skinned to two bones of the body
])
def test_official_a_halo_that_cannot_be_carried_hides_the_slots(view, source, shape):
    """After a swap the halo is the source's or none, never the slot's own. A source halo that cannot be
    carried (a boss's) hides Azusa's: the change carries nothing and hides her halo's node."""
    from bmpt.gameassets.model import BundleGraph, _prefabs, model_root

    files, index = view
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    slot = [p for p in _slot(files, "azusa_original") if p.name.startswith("character-")]
    with BundleGraph(index) as graph:
        s = ma.Skeleton.of(graph, model_root(graph, sources, source)[0])
        prefabs = _prefabs(graph, slot)
        t = ma.Skeleton.of(graph, prefabs[ma.choose_prefab(prefabs, "azusa_original")].deref())
        assert ma.halo_of(s).shape == shape
        carried = []
        change = ma._halo_change(t, s, carried.append, carried.append, (source, "azusa_original"))
    assert change.new_mesh is None and not carried and change.hide == ["HaloRoot/Azusa_Original_Halo"]


@official
@pytest.mark.parametrize("source,nodes,particles,drawn", [
    ("tsurugi_original", ["Tsurugi_Original_Halo", "FX_blood"], 1, 1),  # her mesh and a particle system beside it
    ("ch0069", ["CH0069_Halo", "FX_CH0069_Halo"], 9, 8),  # Mika: nested particle systems, an Animator left out
])
def test_official_a_particle_halo_is_carried_with_its_nodes(view, tmp_path, source, nodes, particles, drawn):
    """A halo with particles (Tsurugi's, Mika's) is carried with its nodes: below Azusa's followed node, new
    GameObjects, Transforms, renderers and particle systems as the source has them below its own, their meshes and
    materials carried into the slot's files; the follower takes the source's offsets. Azusa's own halo draws nothing
    and the slot still exports, with the source's halo mesh."""
    from bmpt.gameassets.model import _key, _prefabs, export_chibi, model_root

    slot, sources, written, adapted, official, graph = _cross_build(view, tmp_path, source)
    new = _official_objects_kept(slot, written, {"SkinnedMeshRenderer", "MeshRenderer", "MeshFilter", "AssetBundle",
                                                 "Transform", "MonoBehaviour", "ParticleSystem",
                                                 "ParticleSystemRenderer"})
    s = ma.Skeleton.of(official, model_root(official, sources, source)[0])
    halo = ma.halo_of(s)
    below = [s.nodes[i] for i in s.descendants(halo.root)]
    prefab_files = [p for p in written if p.name.startswith("character-")]
    checked = 0
    for name, pointer in _prefabs(graph, prefab_files).items():
        if "azusa_original" not in name:
            continue
        checked += 1
        t = ma.Skeleton.of(graph, pointer.deref())
        root = t.first("HaloRoot")
        own = t.nodes[t.first("Azusa_Original_Halo")]
        renderer, filt = graph.component(own.go, "MeshRenderer"), graph.component(own.go, "MeshFilter")
        assert not graph.tree(renderer)["m_Enabled"] and not graph.tree(filt)["m_Mesh"]["m_PathID"], name
        added = [t.nodes[i] for i in t.descendants(root) if t.nodes[i].name != "Azusa_Original_Halo"]
        assert [n.path.split("/", 1)[1] for n in added] == [n.path.split("/", 1)[1] for n in below], name
        assert all(new.get(_key(n.go)) == "GameObject" and new.get(_key(n.transform)) == "Transform" for n in added)
        for a, b in zip(added, below):
            assert np.allclose(np.concatenate(a.local), np.concatenate(b.local)) and a.active == b.active
        kinds = [o.type.name for n in added for c in graph.tree(n.go)["m_Component"]
                 if (o := graph.deref(n.go, c["component"])) is not None]
        assert kinds.count("ParticleSystem") == particles and "Animator" not in kinds
        drawing = 0
        for n in added:
            for c in graph.tree(n.go)["m_Component"]:
                o = graph.deref(n.go, c["component"])
                assert o is not None and new.get(_key(o)) == o.type.name
                if o.type.name.endswith("Renderer"):  # every material the source's draws, carried
                    materials = [graph.deref(o, m) for m in graph.tree(o)["m_Materials"] if m["m_PathID"]]
                    assert all(m is not None and new.get(_key(m)) == "Material" for m in materials)
                    drawing += len(materials)
        assert drawing >= len(nodes) and all(t.first(x) is not None for x in nodes)
        follower = graph.tree(ma.halo_of(t).follower)
        for field in ma.FOLLOWER_OFFSETS:
            assert follower[field] == pytest.approx(official.tree(halo.follower)[field])
    assert checked
    data = export_chibi(graph, prefab_files, "azusa_original", fx=_fx(graph))
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    names = {m["name"] for m in doc["meshes"]}
    assert nodes[0] in names and not any(n.startswith("Azusa_Original_Halo") for n in names) and doc["animations"]
    # The carried particles play in the slot's 3D preview, read from the slot's files
    systems = doc["extras"]["haloFx"]["systems"]
    assert len(systems) == drawn and all(doc["nodes"][s["node"]]["name"] == s["name"] for s in systems)


@official
def test_official_a_particle_slot_hides_its_halo_and_takes_the_sources(view, tmp_path):
    """Tsurugi's slot <- Azusa: Tsurugi's halo mesh draws nothing and her particle system emits nothing;
    Azusa's halo comes as new nodes below Tsurugi's followed node."""
    from bmpt.gameassets.model import _prefabs, export_chibi

    slot, sources, written, adapted, official, graph = _cross_build(view, tmp_path, "azusa_original", "tsurugi_original")
    prefab_files = [p for p in written if p.name.startswith("character-")]
    checked = 0
    for name, pointer in _prefabs(graph, prefab_files).items():
        if "tsurugi_original" not in name:
            continue
        t = ma.Skeleton.of(graph, pointer.deref())
        if t.first("HaloRoot") is None:
            continue
        checked += 1
        own = t.nodes[t.first("Tsurugi_Original_Halo")]
        assert not graph.tree(graph.component(own.go, "MeshRenderer"))["m_Enabled"], name
        blood = t.nodes[t.first("FX_blood")]
        assert graph.tree(graph.component(blood.go, "ParticleSystem"))["InitialModule"]["maxNumParticles"] == 0
        assert not graph.tree(graph.component(blood.go, "ParticleSystemRenderer"))["m_Enabled"]
        added = t.nodes[t.first("Azusa_Original_Halo")]
        assert added.path == "HaloRoot/Azusa_Original_Halo"
        assert graph.tree(graph.component(added.go, "MeshRenderer"))["m_Enabled"]
    assert checked
    data = export_chibi(graph, prefab_files, "tsurugi_original", fx=_fx(graph))
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    names = {m["name"] for m in doc["meshes"]}
    assert "Azusa_Original_Halo" in names and "Tsurugi_Original_Halo" not in names
    assert "haloFx" not in doc["extras"]  # Her silenced drops do not play in the preview


def _locks_free() -> list[str]:
    """The bundle path locks another thread cannot take at once (held by a reader that never closed)."""
    import threading
    from bmpt.gameassets import unity

    stuck = []

    def probe():
        for path, lock in list(unity._locks.items()):
            if lock.acquire(timeout=0.2):
                lock.release()
            else:
                stuck.append(path.name)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return stuck


@official
@pytest.mark.parametrize("target,source,unfit", [
    ("hina_original", "ch0230", False),    # built: Hina <- Hina (Dress)
    ("hanako_original", "ch0209", True),   # past the stretch limit: Hanako <- Hanako (Swimsuit)
])
def test_official_costume_build_leaves_no_bundle_locked(view, target, source, unfit):
    """A build opens many bundles, each under its path lock; it closes them all when it ends, built or
    refused, so an export on another thread never waits for them (an earlier build hung there). Past-limit pairs build
    as well, with their stretch kept as information."""
    from ba_modding_toolkit.bundle import Bundle

    files, index = view
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                      source_files=sources, source_dev=source, open_writable=Bundle.load)
    assert result.changed and result.plan.unfit == unfit
    assert (result.plan.stretch > ma.stretch_limit(result.plan.own)) == unfit
    assert _locks_free() == []


def test_the_library_builds_a_costume_without_the_model_lock(monkeypatch, tmp_path):
    """A costume build reads its bundles from memory (one path lock per load): exports never wait for it, so it
    builds without the model lock, and its caller's steps reach the build."""
    from bmpt.gameassets import model_adapt, service as gameassets_service
    from bmpt.library import service

    held, steps = [], []

    def adapt(**kwargs):
        held.append(gameassets_service._MODEL_LOCK.locked())
        kwargs["step"]()
        raise model_adapt.refuse("rest pose")

    library = object.__new__(service.ModLibrary)
    monkeypatch.setattr(model_adapt, "adapt", adapt)
    monkeypatch.setattr(service.ModLibrary, "_official_or_current", lambda self, path, pin: path)
    monkeypatch.setattr(service.ModLibrary, "_stage_copies", lambda self, originals, folder: {})
    monkeypatch.setattr(service.ModLibrary, "_costume_files", lambda self, other, pin: ("dev", [], []))
    monkeypatch.setattr(service.ModLibrary, "_cab_index", lambda self, pin: None)
    monkeypatch.setattr(service.ModLibrary, "_core_map", lambda self, pin: type("M", (), {"slot": lambda s, c: c})())
    target = tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_1.bundle"
    with pytest.raises(Exception, match="source_unsupported"):
        library._stage_costume("other", [target], tmp_path, print, None, step=lambda: steps.append(1))
    assert held == [False] and steps == [1]


def test_a_costume_build_holds_one_path_lock_for_one_load_at_a_time(monkeypatch, tmp_path):
    """The build's graph loads each bundle under its path lock and reads it afterwards without the lock."""
    from bmpt.gameassets import unity

    loaded = []

    class Loaded:
        env = "env"

    def load(path, log=None):
        lock = unity._locks[path.resolve()]
        loaded.append(lock._is_owned())
        return Loaded()

    monkeypatch.setattr(unity, "Bundle", type("Engine", (), {"load": staticmethod(load)}))
    path = tmp_path / "a.bundle"
    with unity.load_bundle(path) as env:
        assert env == "env"
        assert not unity._locks[path.resolve()]._is_owned()
    assert loaded == [True]


@official
def test_official_field_models_are_found_and_exported(tmp_path):
    from bmpt.catalog import Catalog
    from bmpt.gameassets import AssetCache, GameAssets
    from ba_modding_toolkit.searching import get_search_dirs

    game = GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path / "cache", allowed=False))
    assert {"np0009", "np0283"} <= game.field_model_cores()
    catalog = Catalog(game, None, server="global")
    pajamas, delivery = catalog.variant("np0009"), catalog.variant("np0283")
    assert (pajamas.model, pajamas.field_model) == (None, "np0009")
    assert (delivery.model, delivery.field_model) == (None, "np0283")
    assert catalog.variant("hina").field_model is None  # a battle model wins
    data = game.model_glb("np0009")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert doc["extras"]["prefab"] == "Field_Player_NP0009"
    assert "OL_NP0009_Body" in {m["name"] for m in doc["meshes"]}
    # its own Animancer clips, never another student's from the shared field bundle
    assert [a["name"] for a in doc["animations"]] == ["Field_Player_NP0009_Normal_Idle"]


# Variants with neither a battle nor a field player model whose model sits elsewhere (the "asset" ones, in
# their own bundles, are their slot): (variant, prefab, source kind, a mesh it draws, whether it plays
# an idle)
NO_SLOT_MODELS = [
    ("ch0171", "Field_NPC_CH0171_NonWeapon", "npc", "OL_CH0171_Body", True),
    ("np0060", "Field_NPC_NP0060_Justice_Student", "npc", "OL_NP0144_Body", True),
    ("np0132", "Field_NPC_NP0132", "npc", "OL_Field_NPC_NP0132", True),
    ("np0133", "Field_NPC_NP0133", "npc", "OL_Field_NPC_NP0133", True),
    ("np0138", "Field_NPC_NP0138_NonWeapon", "npc", "OL_Attendant_Highlander_HG", True),
    ("np0143", "Field_NPC_NP0143_AR_Weapon", "npc", "OL_Soldier_Helmet_Aranchino_AR_Mesh", True),
    ("np0206", "NP0206_Mesh", "asset", "NP0206_Body", True),        # Ayame (Corrupted), a story cutscene's model
    ("np0229", "NP0229_Mesh", "asset", "NP0229_Body", True),        # Ayame (Twilight): both with their story clips
    ("arona", "Arona_Original_Gacha", "asset", "OL_Arona_Original_Body", True),
    ("np0269", "NP0269_Body_Test", "asset", "NP0269_Body", False),  # Kei (Decagrammaton Body), a static mesh
]


@official
def test_official_no_slot_models_are_found_and_exported(tmp_path):
    from bmpt.catalog import Catalog
    from bmpt.gameassets import AssetCache, GameAssets
    from ba_modding_toolkit.searching import get_search_dirs

    game = GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path / "cache", allowed=False))
    catalog = Catalog(game, None, server="global")
    for key, prefab, kind, mesh, idle in NO_SLOT_MODELS:
        variant = catalog.variant(key)
        # a model of the variant's own bundles is its slot; a field-mode one has none
        assert (variant.model, variant.field_model) == ((key, None) if kind == "asset" else (None, key)), key
        data = game.model_glb(variant.model or variant.field_model)
        doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
        assert (doc["extras"]["prefab"], doc["extras"]["source"]) == (prefab, kind), key
        assert mesh in {m["name"] for m in doc["meshes"]}, key
        assert bool(doc.get("animations")) == idle, key
    # Ayame's story forms move with their own clips of the story cutscenes, as Story_1, Story_2, ..., about
    # where they rest: never in the bind pose, never placed off the view where the shot needed them
    for core, prefix in (("np0206", "NP0206_Cutin_A_"), ("np0229", "NP0229_Cutin_B_")):
        data = game.model_glb(core)
        doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
        animations = doc["animations"]
        assert 1 < len(animations) <= 12 and all(a["name"].startswith(prefix) for a in animations), core
        assert [a["extras"]["slot"] for a in animations] == [f"Story_{k}" for k in range(1, len(animations) + 1)]
        names = [n.get("name") for n in doc["nodes"]]
        start = 20 + struct.unpack_from("<I", data, 12)[0]
        binary = data[start + 8:]

        def values(index):
            accessor, view = doc["accessors"][index], None
            view = doc["bufferViews"][accessor["bufferView"]]
            offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            return np.frombuffer(binary, np.float32, accessor["count"] * 3, offset).reshape(-1, 3)

        for animation in animations:
            for channel in animation["channels"]:
                target = channel["target"]
                if target["path"] == "translation" and names[target["node"]] in ("bone_root", "Bip001"):
                    rest = np.array(doc["nodes"][target["node"]].get("translation", [0, 0, 0]))
                    moved = values(animation["samplers"][channel["sampler"]]["output"]).mean(axis=0)
                    assert abs(moved[0] - rest[0]) < 1e-3 and abs(moved[2] - rest[2]) < 1e-3, (core, animation["name"])
    # Arona's idle switches the gacha faces off: her own clip, never the sack's beside it
    arona = game.model_glb("arona")
    doc = json.loads(arona[20:20 + struct.unpack_from("<I", arona, 12)[0]])
    assert [a["name"] for a in doc["animations"]] == ["Arona_Original_AroPla_Cacha_02_Idle"]


@official
def test_official_costume_apply_restore_and_preview_on_clones(tmp_path):
    from bmpt.catalog import Catalog
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.cabs import CabIndex
    from bmpt.library import ModLibrary
    from bmpt.library.integrity import Inspection
    from bmpt.library.storage import atomic_clone
    from ba_modding_toolkit.searching import get_search_dirs

    directories = get_search_dirs(GAME_DIR)
    every = [path for directory in directories for path in directory.glob("*.bundle")]
    wanted = set(_slot(every, "hina_original")) | {p for p in every if p.name.startswith("field-character-_mxload")}
    wanted |= {p for p in every if re.match(r"assets-_mx-spinecharacters-(hina|np0009)_spr-", p.name)}
    # everything the slot and the field bundles reference, to the end
    inspection, cabs, todo = Inspection(), CabIndex(directories), set(wanted)
    while todo:
        references = set().union(*(inspection.inspect(path).references for path in todo))
        found = {Path(p) for p in cabs.resolve(references, next(iter(todo))).values()} - wanted
        wanted |= found
        todo = found
    inspection.validate()
    clone_dir = tmp_path / "game"
    clone_dir.mkdir()
    for path in wanted:
        assert atomic_clone(path, clone_dir / path.name), "The write check requires native clones"
        (clone_dir / path.name).chmod(0o644)
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(clone_dir))
    game = GameAssets([clone_dir], AssetCache(allowed=False, root=tmp_path / "cache"))
    catalog = Catalog(game, None, server="global")
    library = ModLibrary(tmp_path / "library", catalog, game, settings, scan_allowed=lambda: True)
    slot = library._game_files("hina", "model")
    official_bytes = {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot}

    preview = library.costume_preview("hina", "np0009")
    assert preview and library.costume_preview("hina", "np0009") == preview  # built once per signature
    assert {p.name for p in preview} <= set(official_bytes)

    # with mods off the costume is saved, nothing is written, and the choice stays (a field-only source has no slot)
    assert not library.toggle(False, True).failed
    result = library.apply("hina", "variant:np0009", kind="model")
    assert result.deferred
    library._prune_quietly()
    assert library.configured("hina").get("model") == "variant:np0009"
    assert {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot} == official_bytes
    # mods on: the master switch writes it
    assert not library.toggle(True, False).failed
    assert library.configured("hina").get("model") == "variant:np0009"
    switched = {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot}
    assert switched != official_bytes
    # the written costume is the library's own: never offered as an outside mod to add
    states = {e.state for e in library.scan().entries if e.core == "hina_original"}
    assert "untracked" not in states, states
    result = library.apply("hina", "variant:np0009", kind="model")
    assert result.unchanged or result.files
    data = game.model_glb("hina_original")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert "OL_NP0009_Body" in {m["name"] for m in doc["meshes"]}
    assert library.configured("hina").get("model") == "variant:np0009"
    library.restore("hina", kind="model")
    assert {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot} == official_bytes
    with pytest.raises(Exception, match="source_unsupported"):
        library.apply("hina", "variant:hina", kind="model")

    # Optimize Physics: the switch above built Rule A; the action keeps a Rule B build as a model mod, once
    assert library.optimize_costume("hina", "official", "Hina") == {"unneeded": True, "reason": "official"}
    made = library.optimize_costume("hina", "variant:np0009", "Pajamas")
    entry = next(m for m in library.mods() if m.id == made["mod"])
    assert (entry.kind, entry.target, entry.optimized_from, entry.label_base) == ("model", "hina", "variant:np0009", "Pajamas")
    assert "Pajamas" in entry.label and entry.label != "Pajamas"  # model.optimizedName
    copy = {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in library._paths(library._record(entry.id))}
    assert copy.keys() <= official_bytes.keys() and any(switched[name] != sha for name, sha in copy.items())
    assert library.optimize_costume("hina", "variant:np0009", "Pajamas") == made
    library.apply("hina", f"mod:{entry.id}", kind="model")
    assert {name: switched_now for name, switched_now in ((p.name, hashlib.sha1(p.read_bytes()).hexdigest()) for p in slot)
            if name in copy} == copy
    data = game.model_glb("hina_original")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert "OL_NP0009_Body" in {m["name"] for m in doc["meshes"]}
    library.restore("hina", kind="model")
    assert {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot} == official_bytes
