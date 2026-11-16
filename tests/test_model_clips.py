"""One clip of a chibi exported on its own (model.export_clip, GameAssets.model_glb's clip, the model route's ?clip=):
the names the model's GLB lists, the clip's GLB bound to the model's nodes, its cache entry, and the route's sources."""

from __future__ import annotations

import json
import os
import re
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bmpt.config import Settings
from bmpt.gameassets import AssetCache, AssetNotFound, GameAssets
from bmpt.gameassets import model
from bmpt.webui.services import Services
from bmpt.webui.state import UiState
from ba_modding_toolkit.searching import get_search_dirs

from test_model import _Graph, _game, _game_object, _object
from test_model_route import RouteCatalog, RouteGame

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


def _doc(data: bytes) -> dict:
    assert data[:4] == b"glTF" and struct.unpack_from("<I", data, 8)[0] == len(data)
    return json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])


def test_a_clips_toggles_name_the_renderers_gameobject_and_bring_what_the_model_leaves_out(monkeypatch):
    # Miyu's EX skill: the drawn face turns off, the disabled gag face on (brought along), a GameObject switch reaches
    # the drawn renderer below it
    graph = _Graph(
        *_game_object(1, "CH0145", [], children=(10, 30, 40)),
        *_game_object(10, "Face", [_object(12, "SkinnedMeshRenderer", m_Enabled=1)]),
        *_game_object(30, "Face02", [_object(32, "SkinnedMeshRenderer", m_Enabled=0)]),
        *_game_object(40, "Props", [], children=(50,)),
        *_game_object(50, "Prop", [_object(52, "SkinnedMeshRenderer", m_Enabled=1)]),
    )
    exporter = model._ClipExporter(graph, "ch0145")
    exporter.visit(graph.objects[1], None, "", True)
    exporter.drawn = {node for node, _, _ in exporter.renderers}
    brought = []
    monkeypatch.setattr(exporter, "meshes", lambda renderers=None: brought.extend(n for n, _, _ in renderers))
    nodes = {path: i for i, path in exporter.path_of.items()}
    by_hash = {zlib.crc32(path.encode()): i for path, i in nodes.items()}
    enabled, active = zlib.crc32(b"m_Enabled"), zlib.crc32(b"m_IsActive")
    tt = {"m_MuscleClip": {"m_StartTime": 0.0}, "m_ClipBindingConstant": {"genericBindings": [
        {"typeID": 25, "attribute": enabled, "path": zlib.crc32(b"Face")},     # off after 0.5 s
        {"typeID": 25, "attribute": enabled, "path": zlib.crc32(b"Face02")},   # on after 0.5 s
        {"typeID": 1, "attribute": active, "path": zlib.crc32(b"Props")},      # 1 all along: the rest state
    ]}}
    decoded = model.DecodedClip(1.0, [0.0, 0.5, 1.0], {0: [1.0, 0.0, 0.0], 1: [0.0, 1.0, 1.0], 2: [1.0]})
    toggles = exporter.toggles(tt, decoded, by_hash)
    assert toggles == [{"node": nodes["Face"], "keys": [[0.0, 1], [0.5, 0]]}, {"node": nodes["Face02"], "keys": [[0.0, 0], [0.5, 1]]}]
    assert brought == [nodes["Face02"]] and exporter.brought == {nodes["Face02"]}
    # no node of the hierarchy is hidden for it: the hierarchy stays the model's
    assert not any(n.get("extras", {}).get("hidden") for n in exporter.nodes)


def test_model_glb_exports_a_clip_from_the_models_files_and_caches_it_per_clip(tmp_path, monkeypatch):
    prefab = "character-ch0242-_mxload-2024-11-18_assets_all_1.bundle"
    game, resources = _game(tmp_path, prefab)
    calls = []
    monkeypatch.setattr(model, "export_chibi", lambda graph, bundles, dev, **_: calls.append(("model", dev)) or b"glTF model")

    def export_clip(graph, bundles, dev, clip, **_):
        calls.append(("clip", dev, [p.name for p in bundles], clip))
        return b"glTF " + clip.encode()

    monkeypatch.setattr(model, "export_clip", export_clip)
    assert game.model_glb("ch0242") == b"glTF model"
    assert game.model_glb("ch0242", clip="CH0242_Exs") == b"glTF CH0242_Exs"
    assert game.model_glb("ch0242", clip="CH0242_Exs") == b"glTF CH0242_Exs"   # cached
    assert game.model_glb("ch0242", clip="CH0242_Formation Pickup") == b"glTF CH0242_Formation Pickup"
    assert calls == [("model", "ch0242"), ("clip", "ch0242", [prefab], "CH0242_Exs"),
                     ("clip", "ch0242", [prefab], "CH0242_Formation Pickup")]
    os.utime(resources / prefab, ns=(1, 1))  # the model's files changed: the clip is read again
    assert game.model_glb("ch0242", clip="CH0242_Exs") == b"glTF CH0242_Exs" and len(calls) == 4
    # a name is only compared with the model's clip names: anything else is refused before a file is opened
    for name in ["../x", "a/b", "", "x" * 129, "CH0242_Exs\n"]:
        with pytest.raises(AssetNotFound):
            game.model_glb("ch0242", clip=name)
    assert len(calls) == 4 and not (tmp_path / "cache").exists()


def test_the_model_route_reads_a_clip_from_every_source_the_model_takes(tmp_path):
    official = [tmp_path / "official.bundle"]
    costume_parts = [tmp_path / "character-ch0242-_mxload-2026-01-01_assets_all_2.bundle"]
    calls = []
    game = RouteGame()
    game.model_glb = lambda core, overrides=(), reads=None, clip=None: calls.append((core, list(overrides), reads, clip)) or b"glTF"
    library = SimpleNamespace(
        official_bundles=lambda core, **kwargs: official,
        preview_reads=lambda policy: "reads:" + policy,
        mods=lambda: [],
        costume_preview=lambda target, other: costume_parts,
        costume_verdict=lambda target, other: None,
    )
    s = Services(Settings(tmp_path / "config.toml"), tmp_path, state=UiState(tmp_path / "webui.json"),
                 cache=object(), game=game, catalog=RouteCatalog(), library=library)
    try:
        assert s.asset("model/ch0242.glb", {"clip": "CH0242_Exs"}).body == b"glTF"
        assert calls[-1] == ("ch0242", [], None, "CH0242_Exs")
        assert s.asset("model/ch0242.glb", {"source": "official", "clip": "CH0242_Exs"}).body == b"glTF"
        assert calls[-1] == ("ch0242", official, "reads:official", "CH0242_Exs")
        # a switched costume: the clip of the slot, from the same adapted bundles as the shown model
        assert s.asset("model/ch0242.glb", {"source": "variant:ch0242_swimsuit", "clip": "CH0242_Exs"}).body == b"glTF"
        assert calls[-1] == ("ch0242", costume_parts, "reads:official", "CH0242_Exs")
        # without ?clip= the model, as before
        assert s.asset("model/ch0242.glb", {"source": "official"}).body == b"glTF"
        assert calls[-1] == ("ch0242", official, "reads:official", None)
    finally:
        s.close()


# --- the official view ---

OWN = lambda dev, names: [n for n in names if n.lower().startswith(dev + "_")]  # noqa: E731


@pytest.fixture(scope="module")
def real_game(tmp_path_factory):
    if not GAME_DIR.is_dir():
        pytest.skip("No local game install")
    return GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path_factory.mktemp("cache"), allowed=True))


def test_real_clip_names_per_model(real_game):
    # Hifumi: the cover sets, the attack, the EX and the normal skill; the base controller's bare names are not hers
    hifumi = _doc(real_game.model_glb("hihumi_original"))["extras"]["clips"]
    own = OWN("hihumi_original", hifumi)
    for suffix in ("Normal_Attack_Start", "Kneel_Attack_Start", "Stand_Idle", "Exs", "Public01", "Move_Jump", "Formation_Idle"):
        assert f"Hihumi_Original_{suffix}" in own
    assert len(own) >= 40 and len(hifumi) == len(set(hifumi))
    # Hoshino (Swimsuit): two battle forms, numbered on different words
    hoshino = OWN("hoshino_swimsuit", _doc(real_game.model_glb("hoshino_swimsuit"))["extras"]["clips"])
    assert {"Hoshino_Swimsuit_Vital_Death01", "Hoshino_Swimsuit_Vital_Death02"} <= set(hoshino)
    assert any(re.search(r"Normal0?1_Idle|Normal_Idle0?1", n) for n in hoshino)
    assert any(re.search(r"Normal0?2_Idle|Normal_Idle0?2", n) for n in hoshino)
    # Ayane (a Special student): her own few clips only
    ayane = _doc(real_game.model_glb("ayane_original"))["extras"]["clips"]
    assert set(OWN("ayane_original", ayane)) >= {"Ayane_Original_Formation_Idle", "Ayane_Original_Cafe_Idle"}
    assert not any("Normal_Attack" in n for n in OWN("ayane_original", ayane))


def test_real_clip_export_binds_the_models_nodes(real_game):
    base = _doc(real_game.model_glb("hihumi_original"))
    data = real_game.model_glb("hihumi_original", clip="Hihumi_Original_Kneel_Attack_Start")
    doc = _doc(data)
    count = doc["extras"]["hierarchy"]
    assert doc["extras"]["clip"] == "Hihumi_Original_Kneel_Attack_Start" and count > 50
    # the hierarchy in the model's order, nothing drawn
    assert [n["name"] for n in doc["nodes"][:count]] == [n["name"] for n in base["nodes"][:count]]
    assert "meshes" not in doc and "images" not in doc and "skins" not in doc
    (animation,) = doc["animations"]
    assert animation["name"] == animation["extras"]["slot"] == "Hihumi_Original_Kneel_Attack_Start"
    bound = {c["target"]["node"] for c in animation["channels"]}
    assert len(bound) > 30 and max(bound) < count
    bones = {base["nodes"][i]["name"] for i in bound}
    assert {"Bip001", "Bip001 Head", "Bip001 L Calf"} <= bones
    assert 10_000 < len(data) < 1_000_000
    # cached per clip: asked again, the same bytes
    assert real_game.model_glb("hihumi_original", clip="Hihumi_Original_Kneel_Attack_Start") is data
    assert real_game.model_glb("hihumi_original", clip="Hihumi_Original_Exs") != data
    with pytest.raises(AssetNotFound):
        real_game.model_glb("hihumi_original", clip="Hoshino_Swimsuit_Exs")  # not a clip this model plays


def test_real_ex_skill_brings_the_face_the_model_leaves_out(real_game):
    if "ch0145" not in real_game.model_cores():
        pytest.skip("Miyu not in this install")
    base = _doc(real_game.model_glb("ch0145"))
    doc = _doc(real_game.model_glb("ch0145", clip="CH0145_Exs"))
    count = doc["extras"]["hierarchy"]
    toggles = doc["animations"][0]["extras"]["toggles"]
    assert toggles and all(t["node"] < count for t in toggles)
    # the gag face comes along, drawn by a mesh node under the root; the model's GLB does not draw it
    brought = [t["node"] for t in toggles if "renderer" in doc["nodes"][t["node"]].get("extras", {})]
    assert [doc["nodes"][n]["name"] for n in brought] == ["CH0145_Face01_Outline"]
    assert "renderer" not in base["nodes"][brought[0]].get("extras", {}) and len(doc["meshes"]) == 1


def _library_with_build(monkeypatch, adapt, root):
    """A library under ``root`` whose costume build is ``adapt`` (model_adapt.adapt), with nothing to stage."""
    from bmpt.gameassets import model_adapt
    from bmpt.library import service as library_service

    library = object.__new__(library_service.ModLibrary)
    library.root = root  # the build keeps its worn-item verdicts beside the library
    monkeypatch.setattr(model_adapt, "adapt", adapt)
    monkeypatch.setattr(library_service.ModLibrary, "_official_or_current", lambda self, path, pin: path)
    monkeypatch.setattr(library_service.ModLibrary, "_stage_copies", lambda self, originals, folder: {})
    monkeypatch.setattr(library_service.ModLibrary, "_costume_files", lambda self, other, pin: ("dev", [], []))
    monkeypatch.setattr(library_service.ModLibrary, "_cab_index", lambda self, pin: None)
    monkeypatch.setattr(library_service.ModLibrary, "_core_map",
                        lambda self, pin: type("M", (), {"slot": lambda s, c: c})())
    return library


def test_a_clip_export_starts_at_once_while_a_long_costume_build_runs(tmp_path, monkeypatch):
    """A costume build (seconds) holds no model lock: the shown model's clip export starts within one of the build's
    steps, and the build, a job, makes no new step while that foreground export runs."""
    import threading
    import time

    from bmpt import preview_gate
    from bmpt.gameassets import model_adapt

    prefab = "character-ch0242-_mxload-2024-11-18_assets_all_1.bundle"
    game, _ = _game(tmp_path, prefab)
    running, steps, during = threading.Event(), [], []

    def adapt(**kwargs):
        running.set()
        end = time.monotonic() + 3  # a build of 3 s in steps of 0.05 s
        while time.monotonic() < end:
            kwargs["step"]()
            steps.append(time.monotonic())
            time.sleep(0.05)
        raise model_adapt.refuse("rest pose")

    def export_clip(graph, bundles, dev, clip, **_):
        before = len(steps)
        time.sleep(0.3)  # the clip's own work
        during.append(len(steps) - before)
        return b"glTF " + clip.encode()

    monkeypatch.setattr(model, "export_clip", export_clip)
    library = _library_with_build(monkeypatch, adapt, tmp_path)
    target = tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_1.bundle"
    failure = []

    def build():
        try:
            library._stage_costume("other", [target], tmp_path / "build", print, None,
                                   step=lambda: preview_gate.step(None))
        except Exception as error:  # the stub refuses at its end
            failure.append(error)

    worker = threading.Thread(target=build)
    worker.start()
    try:
        assert running.wait(5)
        time.sleep(0.2)
        started = time.monotonic()
        with preview_gate.foreground():
            assert game.model_glb("ch0242", clip="CH0242_Exs") == b"glTF CH0242_Exs"
        took = time.monotonic() - started
    finally:
        worker.join(10)
    assert took < 0.3 + 0.5, took  # its own work and at most one step of the build
    assert during == [0] or during == [1]  # the step under way finishes, then the build waits
    assert steps[-1] - started > 0.5  # the build went on afterwards
    assert [getattr(error, "code", error) for error in failure] == ["source_unsupported"]


@pytest.mark.parametrize(("arms", "gain"), [(0, False), (3, True)])
def test_arms_pushed_out_count_as_an_optimize_physics_gain(tmp_path, monkeypatch, arms, gain):
    """Optimize Physics keeps a build whose only change is the arms pushed out of a bulkier costume; without any
    physics rule it reports no gain."""
    from bmpt.gameassets import model_adapt

    def adapt(**kwargs):
        plan = model_adapt.Plan({}, {}, 1.0, 1.0)
        return model_adapt.Result({}, plan, 1, None, arms=arms)

    library = _library_with_build(monkeypatch, adapt, tmp_path)
    report = {}
    library._stage_costume("other", [tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_1.bundle"],
                           tmp_path / "build", print, None, report=report, physics=True)
    assert report["physics"] is gain
