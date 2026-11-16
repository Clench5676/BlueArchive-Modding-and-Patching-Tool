"""Small things a 3D model wears with its costume go with it on a costume switch (gameassets.model_props)."""

import copy
import json
import os
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from bmpt.gameassets import model_adapt as ma
from bmpt.gameassets import model_props as mp

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
official = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")


# --- the rule ------------------------------------------------------------------------------------------------------

def _item(**values):
    part = ma.Part(0, object(), object(), [], [], [], 0)
    base = dict(part=part, top=1, bones=frozenset({1}), rows=np.arange(4), triangles=np.arange(2), native=0.2,
                clips=20, full=20, some=20, size=0.2, follow="Bip001 Pelvis", f_mean=0.03, w_mean=0.2, reach=0.15)
    base.update(values)
    return mp.Item(**base)


def test_a_thing_the_body_carries_in_every_clip_is_worn():
    assert _item().worn()


@pytest.mark.parametrize("values", [
    {"gear": True},                               # a gun, an effect item or in their renderer
    {"_active": False},                           # switched off in the prefab
    {"full": 14},                                 # shown in fewer than three clips in four: the animation's
    {"some": 0, "full": 0},                       # never shown
    {"size": 0.04},                               # never drawn at a quarter of its modelled size
    {"reach": 0.03},                              # parked inside the body
    {"follow": "Bip001 L Hand"},                  # held in a hand
    {"follow": "Bip001 R Forearm"},
    {"follow": "Bip001 L UpperArm"},
    {"follow": "Bip001 R Finger11"},
    {"f_mean": 0.17},                             # moves against the bone it follows almost as much as in space
])
def test_what_is_not_worn(values):
    assert not _item(**values).worn()


def test_three_clips_in_four_are_enough():
    assert _item(full=15).worn() and not _item(full=14).worn()


def _node(name, path, parent):
    return ma.Node(name, path, parent, ((0, 0, 0), (0, 0, 0, 1), (1, 1, 1)), None, None, True)


def _skeleton(*names_and_parents):
    s = ma.Skeleton(SimpleNamespace())
    for name, parent in names_and_parents:
        path = name if parent is None or parent == 0 else f"{s.nodes[parent].path}/{name}"
        s.nodes.append(_node(name, "" if parent is None else path, parent))
    return s


def test_gear_is_the_aim_ik_weapon_an_effect_bone_or_a_weapon_node():
    s = _skeleton(("root", None), ("Bip001", 0), ("Bip001 Pelvis", 1), ("gun", 2), ("bone_magazine", 3),
                  ("bone_muzzle", 2), ("Bip001_Weapon02", 2), ("bone_case", 6), ("bone_bottle", 2))
    item = lambda top, *bones: _item(top=top, bones=frozenset({top, *bones}))  # noqa: E731
    mounts, fx = [3], [5]
    assert mp._is_gear(s, item(4), mounts, fx)            # below the aim IK's weapon
    assert mp._is_gear(s, item(5), mounts, fx)            # holds an effect bone (a muzzle)
    assert mp._is_gear(s, item(7), mounts, fx)            # on a Bip001_Weapon node
    assert not mp._is_gear(s, item(8), mounts, fx)        # a bottle on the belt


# --- the measure, on a made-up model -------------------------------------------------------------------------------

def _translate(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


class _Mesh(str):
    def peek_name(self):
        return str(self)


def _made_up_model(monkeypatch):
    """A body (pelvis, spine, head, left arm and hand) with three things in one renderer and a hat in another: a
    bottle on the belt, a bag held in the hand, a magazine the clips show in one clip of four; the hat on the head."""
    names = [("root", None), ("Bip001", 0), ("Bip001 Pelvis", 1), ("Bip001 Spine", 2), ("Bip001 Head", 3),
             ("Bip001 L UpperArm", 3), ("Bip001 L Forearm", 5), ("Bip001 L Hand", 6),
             ("bone_bottle", 1), ("bone_bag", 1), ("bone_magazine", 1), ("bone_hat", 1),
             ("props", 0), ("hat", 0)]
    s = _skeleton(*names)
    places = {2: (0, 1, 0), 3: (0, 1.2, 0), 4: (0, 1.5, 0), 5: (0.2, 1.4, 0), 6: (0.45, 1.4, 0), 7: (0.7, 1.4, 0),
              8: (0.15, 0.9, 0), 9: (0.8, 1.3, 0), 10: (0.1, 1.0, 0.1), 11: (0, 1.7, 0)}
    rest = [np.eye(4) for _ in names]
    for n, p in places.items():
        rest[n] = _translate(*p)
    monkeypatch.setattr(ma.Skeleton, "world", lambda self, local=None: [m.copy() for m in rest])

    def pose(shift, swing, magazine_shown):
        """The body moved by ``shift``, the arm swung by ``swing``: the bottle with the pelvis, the bag with the
        hand, the hat with the head; the magazine scaled to nothing unless shown."""
        out = [m.copy() for m in rest]
        body = _translate(*shift)
        for n in (2, 3, 4, 8, 11):
            out[n] = body @ rest[n]
        for n in (5, 6, 7, 9):
            out[n] = body @ _translate(0, swing, 0) @ rest[n]
        out[10] = body @ rest[10] @ (np.eye(4) if magazine_shown else np.diag([1e-3, 1e-3, 1e-3, 1]))
        return out

    clips = []
    for k in range(4):
        poses = [pose((0.3 * f * (k + 1), 0, 0.1 * f), 0.2 * np.sin(f + k), k == 0) for f in range(mp.FRAMES)]
        clips.append(SimpleNamespace(name=f"clip{k}", poses=poses, peek_name=lambda k=k: f"clip{k}"))
    monkeypatch.setattr(ma.Skeleton, "clip_poses", lambda self, clip, frames=None: (clip.poses, {2, 3, 4, 5, 6, 7}))

    def box(centre):  # eight corners of a 10 cm box, as two triangles per face (only joined corners matter)
        corners = np.array([[x, y, z] for x in (-0.05, 0.05) for y in (-0.05, 0.05) for z in (-0.05, 0.05)])
        return corners + np.array(centre)

    skins, bones = {}, {}
    # renderer 1: the bottle, the bag, the magazine (vertex rows 0-7, 8-15, 16-23), each its own bone
    vertices = np.vstack([box(places[8]), box(places[9]), box(places[10])])
    tris = np.array([[a, a + 1, a + 2] for a in (0, 3, 8, 11, 16, 19)])
    joints = np.zeros((24, 4), int)
    joints[:, 0] = np.repeat(np.arange(3), 8)
    skins["props"] = ma.Skin(vertices, joints, np.tile([1.0, 0, 0, 0], (24, 1)), np.zeros((0, 2), int), tris)
    bones["props"] = [8, 9, 10]
    skins["hat"] = ma.Skin(box(places[11]), np.zeros((8, 4), int), np.tile([1.0, 0, 0, 0], (8, 1)),
                           np.zeros((0, 2), int), np.array([[0, 1, 2], [3, 4, 5]]))
    bones["hat"] = [11]
    monkeypatch.setattr(ma, "skin_of", lambda mesh: skins[mesh])
    # bindposes: each bone's inverse rest (the mesh in the root's space)
    monkeypatch.setattr(ma, "bindposes", lambda graph, mesh: np.array([np.linalg.inv(rest[b]) for b in bones[mesh]]))
    s.graph = SimpleNamespace(tree=lambda obj: {"m_Enabled": 1}, script=lambda b: None)
    parts = [ma.Part(12, "r1", _Mesh("props"), [], ["general"], bones["props"], 18, role="prop"),
             ma.Part(13, "r2", _Mesh("hat"), [], ["general"], bones["hat"], 6, role="prop")]
    return s, parts, clips, pose((0, 0, 0), 0, True)


def test_the_measure_finds_the_worn_things_of_a_model(monkeypatch):
    s, parts, clips, idle = _made_up_model(monkeypatch)
    items = {s.nodes[i.top].name: i for i in mp.measure(s, parts, clips, idle)}
    assert set(items) == {"bone_bottle", "bone_bag", "bone_magazine", "bone_hat"}
    bottle, bag, magazine, hat = (items[k] for k in ("bone_bottle", "bone_bag", "bone_magazine", "bone_hat"))
    assert bottle.follow == "Bip001 Pelvis" and bottle.worn()
    assert hat.follow in ("Bip001 Head", "Bip001 Spine", "Bip001 Pelvis") and hat.worn()
    assert bag.follow in ("Bip001 L Hand", "Bip001 L Forearm", "Bip001 L UpperArm") and not bag.worn()  # held
    assert magazine.full == 1 and magazine.clips == 4 and not magazine.worn()   # shown at times
    worn = mp.worn_items(s, parts, clips, idle)
    assert {s.nodes[i.top].name for i in worn.items} == {"bone_bottle", "bone_hat"}
    # the hat's renderer draws nothing else: cleared whole; the bottle's draws the bag too: a copy without it
    assert worn.whole == {13}


def _closed_box(centre, half):
    """A closed box: 8 corners, 12 triangles."""
    corners = np.array([[x, y, z] for x in (-half, half) for y in (-half, half) for z in (-half, half)]) + centre
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return corners, np.array([t for a, b, c, d in faces for t in ((a, b, c), (a, c, d))])


def _with_bag(monkeypatch, s, parts, centre, half=0.15):
    """The made-up model's costume gains a cool bag: a closed box on the pelvis around ``centre``."""
    corners, tris = _closed_box(np.array(centre), half)
    bag = ma.Skin(corners, np.full((8, 4), 0), np.tile([1.0, 0, 0, 0], (8, 1)), np.zeros((0, 2), int), tris)
    skin_of, bindposes = ma.skin_of, ma.bindposes
    pelvis = np.linalg.inv(ma.Skeleton.world(s)[2])
    monkeypatch.setattr(ma, "skin_of", lambda mesh: bag if mesh == "bag" else skin_of(mesh))
    monkeypatch.setattr(ma, "bindposes", lambda graph, mesh: np.array([pelvis]) if mesh == "bag" else bindposes(graph, mesh))
    return [ma.Part(1, "r0", _Mesh("bag"), [], ["character"], [2], 36, role="primary"), *parts]


def test_a_ray_from_inside_a_closed_box_meets_it_everywhere():
    corners, tris = _closed_box(np.zeros(3), 0.1)
    inside = mp.enclosed_points(np.array([[0.0, 0, 0], [0.05, -0.05, 0.02], [0.3, 0, 0], [0.0, 0.11, 0]]),
                                corners[tris])
    assert inside.tolist() == [True, True, False, False]
    open_box = corners[tris[2:]]  # one face left open
    assert not mp.enclosed_points(np.zeros((1, 3)), open_box).any()


def test_a_thing_the_costume_encloses_is_packed_goes_with_it_and_is_not_carried(monkeypatch):
    """The bottle on the belt sits in the costume's cool bag: it goes with the costume like a worn thing (the slot's
    draws nothing) but is not carried onto another slot, where it would stick out of the bag."""
    s, parts, clips, idle = _made_up_model(monkeypatch)
    parts = _with_bag(monkeypatch, s, parts, (0.15, 0.9, 0))
    items = {s.nodes[i.top].name: i for i in mp.measure(s, parts, clips, idle)}
    bottle, hat, magazine = items["bone_bottle"], items["bone_hat"], items["bone_magazine"]
    assert bottle.enclosed == 1.0 and bottle.packed() and bottle.worn()
    assert hat.enclosed == 0.0 and not hat.packed()
    assert not magazine.packed()   # beside the bag, not in it
    worn = mp.worn_items(s, parts, clips, idle)
    assert {s.nodes[i.top].name: i.carry for i in worn.items} == {"bone_bottle": False, "bone_hat": True}
    t = _skeleton(("root", None), ("Bip001", 0), ("Bip001 Pelvis", 1), ("Bip001 Head", 2))
    tree = _plain_mesh(ma.skin_of("props").vertices, ma.skin_of("props").triangles.tolist())
    hat_tree = _plain_mesh(ma.skin_of("hat").vertices, ma.skin_of("hat").triangles.tolist())
    out = mp.carried(t, s, worn, clips, idle, lambda mesh: copy.deepcopy(hat_tree if mesh == "hat" else tree))
    assert [c.part.mesh for c in out] == ["hat"]


def test_a_thing_shown_at_times_inside_the_costume_stays_with_the_slot(monkeypatch):
    """The magazine the clips show in one clip of four, inside a pouch when shown: the slot's clips take it out, so it
    stays as before; nor is a thing drawn over the face packed (eye sparkles the game draws through the hair)."""
    s, parts, clips, idle = _made_up_model(monkeypatch)
    parts = _with_bag(monkeypatch, s, parts, (0.1, 1.0, 0.1), half=0.08)
    items = {s.nodes[i.top].name: i for i in mp.measure(s, parts, clips, idle)}
    assert not items["bone_magazine"].packed() and items["bone_magazine"].enclosed is None
    s, parts, clips, idle = _made_up_model(monkeypatch)
    parts = _with_bag(monkeypatch, s, parts, (0, 1.7, 0))   # a head around the hat
    parts[-1].kinds = ["eyemouth"]
    items = {s.nodes[i.top].name: i for i in mp.measure(s, parts, clips, idle)}
    assert items["bone_hat"].worn() and not items["bone_hat"].packed()


def test_a_verdict_is_saved_and_restored(monkeypatch):
    s, parts, clips, idle = _made_up_model(monkeypatch)
    store = {}

    class Store:
        def get(self, dev):
            return store.get(dev)

        def put(self, dev, verdict):
            store[dev] = json.loads(json.dumps(verdict))

    first = mp.worn_of(s, parts, clips, idle, "made_up", Store())
    assert "made_up" in store
    monkeypatch.setattr(mp, "measure", lambda *a: pytest.fail("measured again"))
    again = mp.worn_of(s, parts, clips, idle, "made_up", Store())
    assert [(i.part.mesh, i.top, i.follow, i.ref, i.carry) for i in again.items] == \
        [(i.part.mesh, i.top, i.follow, i.ref, i.carry) for i in first.items]
    assert [i.rows.tolist() for i in again.items] == [i.rows.tolist() for i in first.items]
    # a verdict of another prefab is measured again
    store["made_up"]["prefab"] = "other"
    monkeypatch.setattr(mp, "measure", lambda *a: [])
    assert mp.worn_of(s, parts, clips, idle, "made_up", Store()).items == []


def test_the_library_keeps_a_verdict_per_model_revision_and_rule(tmp_path, monkeypatch):
    from bmpt.gameassets import costume_rule
    from bmpt.library.service import _WornVerdicts

    path = tmp_path / "worn-items.json"
    store = _WornVerdicts(path, {"ch0186": "r1", "mari_original": "r2"})
    verdict = {"version": 1, "prefab": "Echelon_CH0186", "renderers": []}
    store.put("ch0186", verdict)
    store.put("unknown", verdict)  # a model without a revision is not kept
    assert store.get("ch0186") == verdict and store.get("mari_original") is None and store.get("unknown") is None
    assert _WornVerdicts(path, {"ch0186": "r1"}).get("ch0186") == verdict
    assert _WornVerdicts(path, {"ch0186": "r9"}).get("ch0186") is None   # another game revision
    monkeypatch.setattr(costume_rule, "COSTUME_RULE", costume_rule.COSTUME_RULE + 1)
    assert _WornVerdicts(path, {"ch0186": "r1"}).get("ch0186") is None   # another rule
    path.write_text("{not json")
    assert store.get("ch0186") is None


def test_builds_at_once_keep_each_others_worn_verdicts(tmp_path):
    """Costume builds run outside the library and model locks: verdicts written at once all stay."""
    import threading
    from bmpt.library.service import _WornVerdicts

    path = tmp_path / "worn-items.json"
    devs = [f"m{k}" for k in range(16)]
    stores = [_WornVerdicts(path, {dev: "r"}) for dev in devs]
    threads = [threading.Thread(target=s.put, args=(dev, {"dev": dev})) for s, dev in zip(stores, devs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [s.get(dev) for s, dev in zip(stores, devs)] == [{"dev": dev} for dev in devs]


# --- mesh edits ----------------------------------------------------------------------------------------------------

def _plain_mesh(vertices, triangles, submeshes=None, wide=False):
    code, size = ("I", 4) if wide else ("H", 2)
    flat = [i for t in triangles for i in t]
    subs = submeshes or [(0, len(flat))]
    return {"m_IndexFormat": int(wide), "m_IndexBuffer": list(struct.pack(f"<{len(flat)}{code}", *flat)),
            "m_SubMeshes": [{"firstByte": first * size, "indexCount": count, "topology": 0} for first, count in subs],
            "m_VertexData": {"m_VertexCount": len(vertices), "m_Channels": [
                {"stream": 0, "offset": 0, "format": 0, "dimension": 3}],
                "m_DataSize": struct.pack(f"<{3 * len(vertices)}f", *np.ravel(vertices))},
            "m_CompressedMesh": {"m_Vertices": {"m_NumItems": 0}, "m_Triangles": {"m_NumItems": 0}},
            "m_StreamData": {"size": 0}}


def _indices(tree):
    raw = bytes(tree["m_IndexBuffer"])
    code, size = ("I", 4) if tree["m_IndexFormat"] else ("H", 2)
    return list(struct.unpack(f"<{len(raw) // size}{code}", raw))


@pytest.mark.parametrize("wide", [False, True])
def test_dropped_triangles_draw_nothing_and_keep_every_count(wide):
    tree = _plain_mesh(np.zeros((9, 3)), [(0, 1, 2), (3, 4, 5), (6, 7, 8)], [(0, 6), (6, 3)], wide)
    assert mp.drop_triangles(tree, np.array([1, 2]))  # the second of submesh 0 and the one of submesh 1
    assert _indices(tree) == [0, 1, 2, 3, 3, 3, 6, 6, 6]
    assert [s["indexCount"] for s in tree["m_SubMeshes"]] == [6, 3]


def test_dropped_triangles_of_a_compressed_mesh():
    from bmpt.gameassets.model_hems import pack_bits, unpack_bits
    flat = [0, 1, 2, 3, 4, 5]
    tree = {"m_IndexFormat": 0, "m_SubMeshes": [{"firstByte": 0, "indexCount": 6, "topology": 0}],
            "m_CompressedMesh": {"m_Triangles": {"m_NumItems": 6, "m_BitSize": 3, "m_Data": list(pack_bits(flat, 3))}}}
    assert mp.drop_triangles(tree, np.array([0]))
    assert unpack_bits(tree["m_CompressedMesh"]["m_Triangles"]) == [0, 0, 0, 3, 4, 5]


def test_other_topologies_are_left_as_they_are():
    tree = _plain_mesh(np.zeros((4, 3)), [(0, 1, 2)])
    tree["m_SubMeshes"][0]["topology"] = 2
    before = copy.deepcopy(tree)
    assert not mp.drop_triangles(tree, np.array([0])) and tree == before


def test_the_slot_s_worn_things_draw_nothing_or_a_copy_without_them():
    """A renderer drawing nothing else the clips show is cleared; another draws a copy of its mesh without the worn
    item's triangles, added as a new mesh; the original mesh is never changed."""
    original = _plain_mesh(np.zeros((6, 3)), [(0, 1, 2), (3, 4, 5)])
    kept = copy.deepcopy(original)
    hat = ma.Part(1, None, SimpleNamespace(assets_file=SimpleNamespace(name="CAB-a"), path_id=1), [], [], [], 3)
    drill = ma.Part(2, None, SimpleNamespace(assets_file=SimpleNamespace(name="CAB-a"), path_id=2), [], [], [], 6)
    worn = mp.Worn([_item(part=hat), _item(part=drill, triangles=np.array([1]))], {1})
    added = []
    out = mp.host_meshes(worn, lambda tree: added.append(tree) or 99, lambda mesh: copy.deepcopy(original))
    assert out == {("cab-a", 1): None, ("cab-a", 2): 99}
    assert _indices(added[0]) == [0, 1, 2, 3, 3, 3] and original == kept


def test_a_carried_thing_is_bound_rigid_where_the_source_holds_it(monkeypatch):
    """The source's bottle (on its pelvis) moves to the slot: at the slot's rest, against the slot's pelvis, where the
    source's reference pose holds it against hers; every bone entry on the slot's pelvis; the other things the
    renderer draws stay behind (their triangles draw nothing)."""
    s, parts, clips, idle = _made_up_model(monkeypatch)
    worn = mp.worn_items(s, parts, clips, idle)
    bottle = next(i for i in worn.items if s.nodes[i.top].name == "bone_bottle")
    worn = mp.Worn([bottle])
    t = _skeleton(("root", None), ("Bip001", 0), ("Bip001 Pelvis", 1))
    t_rest = [np.eye(4), np.eye(4), _translate(0, 0.8, 0) @ np.diag([1, 1, 1, 1.0])]
    s_rest = ma.Skeleton.world(s)
    monkeypatch.setattr(ma.Skeleton, "world", lambda self, local=None: [m.copy() for m in (t_rest if self is t else s_rest)])
    tree = _plain_mesh(ma.skin_of("props").vertices, ma.skin_of("props").triangles.tolist())
    out = mp.carried(t, s, worn, clips, idle, lambda mesh: copy.deepcopy(tree))
    assert len(out) == 1
    c = out[0]
    assert c.nodes == [2, 2, 2]
    held = (np.linalg.inv(idle[2]) @ idle[8] @ np.linalg.inv(s_rest[8]) @ np.c_[
        ma.skin_of("props").vertices[bottle.rows], np.ones(8)].T)
    at_rest = np.linalg.inv(t_rest[2]) @ np.c_[c.vertices[bottle.rows], np.ones(8)].T
    assert np.allclose(at_rest, held)
    assert np.allclose(c.bindposes[0], np.linalg.inv(t_rest[2]))
    drawn = [tuple(_indices(c.tree)[k:k + 3]) for k in range(0, 18, 3)]
    assert drawn[:2] == [(0, 1, 2), (3, 4, 5)] and all(len(set(tri)) == 1 for tri in drawn[2:])


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


def _switch(view, tmp_path, target, source):
    """``target``'s slot carrying ``source``'s costume, saved: the result, the official and read-back graphs, the
    written prefab files and the source's files."""
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.gameassets.discovery import cab_names
    from bmpt.gameassets.model import BundleGraph

    files, index = view
    slot = _slot(files, target)
    sources = [p for p in files if re.match(rf"character-{source}-_mxload", p.name)]
    result = ma.adapt(index=index, resolver=None, target_files=slot, target_dev=target, source_files=sources,
                      source_dev=source, open_writable=Bundle.load, halo=True)
    out = tmp_path / f"{target}-{source}"
    out.mkdir()
    adapted = dict(index)
    for path, bundle in result.changed.items():
        ok, message = bundle.save(out / path.name, SaveOptions(perform_crc=True, compression="lz4"))
        assert ok, message
        for cab in cab_names(out / path.name):
            adapted[cab] = out / path.name
    written = sorted(out.glob("*.bundle"))
    return result, slot, sources, written, BundleGraph(index), BundleGraph(adapted)


def _own_skeletons(graph, written, dev):
    from bmpt.gameassets.model import _prefabs
    prefabs = _prefabs(graph, [p for p in written if p.name.startswith("character-")])
    return {name: ma.Skeleton.of(graph, pointer.deref()) for name, pointer in ma.own_prefabs(prefabs, dev).items()}


def _drawn(graph, skeleton, name):
    """The renderers of a GameObject name in a prefab, with the mesh each draws (None for none)."""
    return [(renderer, graph.deref(renderer, graph.tree(renderer)["m_Mesh"]))
            for node, renderer in skeleton.renderers if skeleton.nodes[node].name == name]


def _kept(slot, written):
    """Every object of the official files is still there, byte for byte, but renderers, transforms, behaviours and
    the bundle's table (clips may gain curves)."""
    import UnityPy

    for path in written:
        before = {o.path_id: o for o in UnityPy.load(str(next(p for p in slot if p.name == path.name))).objects}
        after = {o.path_id: o for o in UnityPy.load(str(path)).objects}
        assert before.keys() <= after.keys()
        changed = {before[pid].type.name for pid in before if before[pid].get_raw_data() != after[pid].get_raw_data()}
        assert changed <= {"SkinnedMeshRenderer", "MeshRenderer", "MeshFilter", "AssetBundle", "Transform",
                           "MonoBehaviour", "AnimationClip"}, changed


def _exported(graph, written, dev):
    from bmpt.gameassets.model import export_chibi
    data = export_chibi(graph, [p for p in written if p.name.startswith("character-")], dev)
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert doc["animations"]
    # the renderers drawing a mesh: each a node "<GameObject>_mesh"
    return {n["name"][:-len("_mesh")] for n in doc["nodes"] if "mesh" in n and n["name"].endswith("_mesh")}


@official
def test_official_the_slots_bottles_draw_nothing_after_a_switch(view, tmp_path):
    """Mari (Track) <- Mari: the three bottles on Mari (Track)'s belt draw nothing in all four prefabs of her slot;
    the gun and its magazine stay."""
    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "ch0186", "mari_original")
    bottles = ["CH0186_Prop_01", "CH0186_Prop_02", "CH0186_Prop_03"]
    assert result.dropped == bottles and result.carried == []
    _kept(slot, written)
    skeletons = _own_skeletons(graph, written, "ch0186")
    assert len(skeletons) == 4
    for name, s in skeletons.items():
        for bottle in bottles:
            drawn = _drawn(graph, s, bottle)
            assert drawn and all(mesh is None for _, mesh in drawn), (name, bottle)
        assert all(mesh is not None for _, mesh in _drawn(graph, s, "Mari_Original_Weapon")), name
    assert not _exported(graph, written, "ch0186") & set(bottles)


@official
def test_official_the_sources_bottles_stay_in_her_bag(view, tmp_path):
    """Mari <- Mari (Track): the three bottles sit in the track suit's cool bag (packed), so they are not carried:
    carried rigidly on the pelvis they stuck out of the bag, which the slot's clips move otherwise."""
    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "mari_original", "ch0186")
    bottles = ["CH0186_Prop_01", "CH0186_Prop_02", "CH0186_Prop_03"]
    assert result.carried == [] and result.dropped == []
    _kept(slot, written)
    for name, t in _own_skeletons(graph, written, "mari_original").items():
        for bottle in bottles:
            assert _drawn(graph, t, bottle) == [], (name, bottle)
    assert not set(bottles) & _exported(graph, written, "mari_original")


@official
def test_official_a_mixed_renderer_draws_a_copy_without_its_worn_item(view, tmp_path):
    """Kasumi <- Mari: Kasumi's remote is worn, her drill in the same mesh is not: her drill renderer draws a new mesh,
    the remote's triangles drawing nothing and every other triangle as before; the official mesh stays."""
    from bmpt.gameassets.model import _key, read_mesh

    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "ch0089", "mari_original")
    assert result.dropped == ["CH0089_Drill_Mesh"]
    _kept(slot, written)
    o = next(iter(_own_skeletons(official_graph, slot, "ch0089").values()))
    [(_, before)] = _drawn(official_graph, o, "CH0089_Drill_Mesh")
    before_triangles = [t for sub in read_mesh(before.read()).triangles for t in sub]
    for name, t in _own_skeletons(graph, written, "ch0089").items():
        [(_, mesh)] = _drawn(graph, t, "CH0089_Drill_Mesh")
        assert _key(mesh) != _key(before), name
        after = [t_ for sub in read_mesh(mesh.read()).triangles for t_ in sub]
        assert len(after) == len(before_triangles)
        gone = [k for k, (a, b) in enumerate(zip(before_triangles, after)) if tuple(a) != tuple(b)]
        assert gone and all(len(set(after[k])) == 1 for k in gone)
        assert len(gone) < len(after) // 2  # the drill's triangles draw as before


@official
def test_official_a_carried_item_leaves_the_rest_of_its_renderer_behind(view, tmp_path):
    """Mari <- Saki (Swimsuit): her bag (body and strap) is worn, the other things of its renderer are not: the
    carried mesh draws the bag's triangles only."""
    from bmpt.gameassets.model import read_mesh

    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, "mari_original", "ch0217")
    assert result.carried == ["CH0217_SkillProp"]
    for name, t in _own_skeletons(graph, written, "mari_original").items():
        [(_, mesh)] = _drawn(graph, t, "CH0217_SkillProp")
        triangles = [tri for sub in read_mesh(mesh.read()).triangles for tri in sub]
        drawn = [tri for tri in triangles if len(set(tri)) == 3]
        assert drawn and len(drawn) < len(triangles), name


@official
@pytest.mark.parametrize("target,source,dropped,carried,bone", [
    ("ch0238", "mari_original", ["CH0238_Hat_Outline"], [], None),   # Chiaki <- Mari: her hat draws nothing
    ("mari_original", "ch0238", [], ["CH0238_Hat_Outline"], "Bip001 Head"),  # Mari <- Chiaki: the hat comes along
])
def test_official_a_hat_goes_with_its_costume(view, tmp_path, target, source, dropped, carried, bone):
    from bmpt.gameassets.model import _key

    result, slot, sources, written, official_graph, graph = _switch(view, tmp_path, target, source)
    assert result.dropped == dropped and result.carried == carried
    for name, t in _own_skeletons(graph, written, target).items():
        for hat in dropped:
            assert all(mesh is None for _, mesh in _drawn(graph, t, hat)), name
        for hat in carried:
            [(renderer, mesh)] = _drawn(graph, t, hat)
            assert {t.nodes[t.by_transform[_key(graph.deref(renderer, b))]].name
                    for b in graph.tree(renderer)["m_Bones"]} == {bone}
    names = _exported(graph, written, target)
    assert not names & set(dropped) and set(carried) <= names
