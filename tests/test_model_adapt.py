"""Another variant's costume on a student's 3D model slot (gameassets.model_adapt, BL-117)."""

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
    ma._extend_preload(bundle, {(0, 2): [_pptr(9, 1)]})
    assert [p["m_PathID"] for p in bundle["m_PreloadTable"]] == [0, 1, 9, 2, 3, 4]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in bundle["m_Container"]] == [(0, 3), (3, 3)]


def test_preload_ranges_with_gaps_get_a_copy_at_the_end():
    bundle = {"m_PreloadTable": [_pptr(i) for i in range(6)],
              "m_Container": [["a.prefab", {"preloadIndex": 1, "preloadSize": 2, "asset": _pptr(1)}],
                              ["b.prefab", {"preloadIndex": 4, "preloadSize": 2, "asset": _pptr(2)}]]}
    ma._extend_preload(bundle, {(4, 2): [_pptr(9)]})
    assert [p["m_PathID"] for p in bundle["m_PreloadTable"]] == [0, 1, 2, 3, 4, 5, 4, 5, 9]
    assert [(i["preloadIndex"], i["preloadSize"]) for _, i in bundle["m_Container"]] == [(1, 2), (6, 3)]


def test_renderer_lists_that_name_the_primary_take_the_added_part():
    tree = {"renderers": [_pptr(1), _pptr(2)], "mouth": _pptr(1), "nested": {"list": [_pptr(3)]}, "bones": [_pptr(1)]}
    assert ma._append_beside(tree, _pptr(1), _pptr(7))
    assert tree["renderers"] == [_pptr(1), _pptr(2), _pptr(7)]
    assert tree["mouth"] == _pptr(1) and tree["nested"]["list"] == [_pptr(3)]


class _Graph:
    """Typetrees are the objects themselves."""

    def tree(self, obj):
        return obj


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
    """BL-128: a Battle NPC's ForeTwist hangs off the upper arm and turns with the forearm. On a slot without it the
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
    """BL-128: a costume made for a longer forearm, refitted to the slot's: the hand's vertices move by the hand's
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
    """BL-128: Midori (Battle)'s face on Midori: the eye bones (Bip001 bone_eye_D_L, named Bip001 like the Biped) rest
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


def test_a_skirt_on_the_biped_root_and_a_bag_off_a_prop_go_with_the_body():
    """BL-128: Sena (Casual)'s skirt hangs off Bip001 and her bag off a prop node Sena lacks. On a slot whose prefab
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
    """BL-128: a mesh whose vertex data lies 1 m off its renderer's space, its bindposes bringing it back (Koharu's);
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
    """BL-128: Midori (Battle)'s brow bones rest 1.4 cm above their bind (an expression her clips replace); the face is
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
                     r"prolog", path.name):
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
    ("ch0172", "np0283", True, 1, "blend 0.5", True),              # Niko <- Niko (FOX EATS): her separate hands go;
                                                                   # Rule B past the stretch limit since the refit
    ("hoshino_original", "hoshino_swimsuit", False, 3, "rigid", False),  # a three-part costume: two renderers added
    ("hoshino_swimsuit", "hoshino_original", False, 1, "rigid", False),  # the swimsuit's own extra parts draw nothing
    ("ch0186", "mari_original", False, 1, "skirt smooth", True),   # Mari (Track) <- Mari: a compressed mesh; Rule B
])                                                                 # within the limit once the seam keeps its length
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
    # the hem (BL-118): less pass-through than rigid weights, no clip 5 points worse, stretch within the limit
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
        assert changed <= allowed | ({"AnimationClip"} if skirt else set()), changed
    # the slot read back: the costume drawn at rest exactly in the shape it was bound and judged in (BL-128: as the
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
def test_official_costume_carries_a_shader_the_slot_does_not_load(view, tmp_path):
    """BL-122: Hina (Dress)'s frill shader lives in a shader bundle Hina's slot does not load. A copy goes with the
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
def test_official_costume_takes_its_own_materials_on_a_body_drawn_from_two_material_files(view, tmp_path):
    """BL-128: Hifumi (Swimsuit)'s body takes its materials from two files. The slot's body renderer was taken for a
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


def _cross_build(view, tmp_path, source):
    """Azusa's slot carrying ``source``'s costume as another student's (BL-131: non-strict, with its halo), saved with
    the CRC fix: the written files, the CAB index over them, the official graph and the read-back graph."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from ba_modding_toolkit.naming import parse_filename
    from ba_modding_toolkit.utils import compute_crc32
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph

    files, index = view
    slot = _slot(files, "azusa_original")
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev="azusa_original", source_files=sources,
                      source_dev=source, open_writable=Bundle.load, strict=False, halo=True)
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
        assert changed <= allowed, changed
        new.update({(after[pid].assets_file.name.lower(), pid): after[pid].type.name for pid in after.keys() - before.keys()})
    return new


@official
def test_official_cross_costume_carries_the_source_halo(view, tmp_path):
    """BL-131: another student's costume (Azusa <- Yukino's NPC model, ch0171_enemy) carries its halo: Yukino's halo
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


@official
def test_official_cross_costume_without_a_halo_hides_the_slots(view, tmp_path):
    """BL-131: another student's costume from a model without a halo (CH9999) hides the slot's: its renderer is turned
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
@pytest.mark.parametrize("target,source,refused", [
    ("hina_original", "ch0230", False),    # built: Hina <- Hina (Dress)
    ("hanako_original", "ch0209", True),   # refused for its stretch: Hanako <- Hanako (Swimsuit)
])
def test_official_costume_build_leaves_no_bundle_locked(view, target, source, refused):
    """BL-122: a build opens many bundles, each under its path lock; it closes them all when it ends, built or
    refused, so an export on another thread never waits for them (0.7.0-alpha.22 hung there)."""
    from ba_modding_toolkit.bundle import Bundle
    from bmpt.errors import ServiceError

    files, index = view
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    try:
        ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                 source_files=sources, source_dev=source, open_writable=Bundle.load)
        assert not refused
    except ServiceError as error:
        assert refused and error.params["source"] == "stretch"
    assert _locks_free() == []
    if refused:  # built anyway for a preview, marked unfit
        result = ma.adapt(index=index, resolver=None, target_files=_slot(files, target), target_dev=target,
                          source_files=sources, source_dev=source, open_writable=Bundle.load, strict=False)
        assert result.plan.unfit and result.plan.stretch > ma.stretch_limit(result.plan.own) and result.changed
        assert _locks_free() == []


def test_the_library_builds_a_costume_under_the_model_lock(monkeypatch, tmp_path):
    """Costume builds and exports hold many bundles at once: one at a time, under the model lock."""
    from bmpt.gameassets import model_adapt, service as gameassets_service
    from bmpt.library import service

    held = []

    def adapt(**kwargs):
        held.append(gameassets_service._MODEL_LOCK.locked())
        raise model_adapt.refuse("stretch", adapted=9.0, own=5.0)

    library = object.__new__(service.ModLibrary)
    monkeypatch.setattr(model_adapt, "adapt", adapt)
    monkeypatch.setattr(service.ModLibrary, "_stage_official", lambda self, targets, folder, pin, **kw: {})
    monkeypatch.setattr(service.ModLibrary, "_costume_files", lambda self, other, pin: ("dev", [], []))
    monkeypatch.setattr(service.ModLibrary, "_cab_index", lambda self, pin: None)
    monkeypatch.setattr(service.ModLibrary, "_core_map", lambda self, pin: type("M", (), {"slot": lambda s, c: c})())
    target = tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_1.bundle"
    with pytest.raises(Exception, match="source_unsupported"):
        library._stage_costume("other", [target], tmp_path, print, None)
    assert held == [True] and not gameassets_service._MODEL_LOCK.locked()


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


# BL-127: variants with neither a battle nor a field player model whose model sits elsewhere: (variant, prefab,
# source kind, a mesh it draws, whether it plays an idle)
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
        assert (variant.model, variant.field_model) == (None, key)
        data = game.model_glb(variant.field_model)
        doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
        assert (doc["extras"]["prefab"], doc["extras"]["source"]) == (prefab, kind), key
        assert mesh in {m["name"] for m in doc["meshes"]}, key
        assert bool(doc.get("animations")) == idle, key
    # Ayame's story forms move with their own clips of the story cutscenes (BL-127), as Story_1, Story_2, ..., about
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

    # Optimize Physics (BL-132): the switch above built Rule A; the action keeps a Rule B build as a model mod, once
    assert library.optimize_costume("hina", "official", "Hina") == {"unneeded": True}
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
