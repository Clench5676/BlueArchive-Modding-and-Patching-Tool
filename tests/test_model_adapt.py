"""Another variant's costume on a student's 3D model slot (gameassets.model_adapt, BL-117)."""

import hashlib
import json
import os
import re
import struct
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
@pytest.mark.parametrize("target,source,field,parts", [
    ("hina_original", "np0009", True, 1),            # Hina <- Hina (Pajamas), a field-mode model
    ("ch0172", "np0283", True, 1),                    # Niko <- Niko (FOX EATS): her separate hands go
    ("hoshino_original", "hoshino_swimsuit", False, 3),  # a three-part costume: two renderers added
    ("hoshino_swimsuit", "hoshino_original", False, 1),  # the swimsuit's own extra parts draw nothing
])
def test_official_costume_on_a_slot(view, tmp_path, target, source, field, parts):
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
                      source_dev=source, open_writable=Bundle.load)
    assert result.parts == parts
    assert result.plan.stretch <= max(1.5 * result.plan.own, result.plan.own + 1.0)
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
        assert changed <= {"SkinnedMeshRenderer", "AssetBundle", "Transform", "MonoBehaviour"}, changed
    # the slot read back: the costume drawn at rest exactly where the source draws it, and exportable
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
    s_rest, t_rest = s.world(), t.world()
    drawn, empty = [], 0
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
        want = (np.c_[ma.skin_of(part.mesh).vertices, np.ones(len(skin.vertices))] @ s_rest[part.node].T)[:, :3]
        assert np.abs(posed - want).max() < 1e-4
        drawn.append(mesh.peek_name())
    assert sorted(drawn) == sorted(p.mesh.peek_name() for p in s_parts)
    assert empty == {"ch0172": 1, "hoshino_swimsuit": 2}.get(target, 0)
    data = export_chibi(BundleGraph(adapted), prefab_files, target)
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert {m["name"] for m in doc["meshes"]} >= set(drawn) and doc["animations"]


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
    library = ModLibrary(tmp_path / "library", catalog, game, settings)
    slot = library._game_files("hina", "model")
    official_bytes = {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot}

    preview = library.costume_preview("hina", "np0009")
    assert preview and library.costume_preview("hina", "np0009") == preview  # built once per signature
    assert {p.name for p in preview} <= set(official_bytes)

    result = library.apply("hina", "variant:np0009", kind="model")
    assert result.files and not result.unchanged
    data = game.model_glb("hina_original")
    doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
    assert "OL_NP0009_Body" in {m["name"] for m in doc["meshes"]}
    assert library.configured("hina").get("model") == "variant:np0009"
    library.restore("hina", kind="model")
    assert {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in slot} == official_bytes
    with pytest.raises(Exception, match="source_unsupported"):
        library.apply("hina", "variant:hina", kind="model")
