"""A memorial lobby's own camera (BL-137): the prefab patch and its round trip through read_prefab."""

import copy
import math
import os
import shutil
from pathlib import Path

import pytest

from bmpt.errors import ServiceError
from bmpt.gameassets import lobby
from bmpt.library import lobby_camera
from test_lobby import ASPECT, BODY, CHARACTER, CHAT, SCRIPTS, SKELETONS, Prefab, SA, aspect_component, core_clips, vec

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
T = 5 / 13  # 16:9 between the standard aspect and 4:3 (lobby.aspect_rule)


def official_like(dialog_at=(0.0, 0.0)) -> Prefab:
    """A prefab laid out like the game's: the skeletons and touch boxes below <X>LobbySpine at the root's origin, the
    dialogue anchors below ChatDialog, the root's UIManualScaleByAspect scaling LobbySpine and moving the anchors."""
    p = Prefab()
    p.node(1, "LobbyTest")
    p.node(2, "TestLobbySpine", father=1)
    p.node(3, "Test_home", father=2, pos=(0.0, -962.0), scale=(100.0, 100.0))
    p.mb(3, 301, SA, skeletonDataAsset={"m_FileID": 3, "m_PathID": 9001}, initialSkinName="", _animationName="")
    p.add(3, 302, "MeshRenderer", {"m_Enabled": True, "m_SortingOrder": 0})
    p.node(4, "Test_bg", father=2)
    p.mb(4, 401, SA, skeletonDataAsset={"m_FileID": 3, "m_PathID": 9002}, initialSkinName="", _animationName="Idle_01")
    p.add(4, 402, "MeshRenderer", {"m_Enabled": True, "m_SortingOrder": -1})
    p.node(6, "BodyTouch", father=2, pos=(193.0, -600.0), rot=10.0)
    p.add(6, 601, "BoxCollider", {"m_Enabled": True, "m_Size": vec(1000.0, 1000.0), "m_Center": vec(0.0, -39.0)})
    p.mb(6, 603, BODY, SpineCharacter={"m_FileID": 0, "m_PathID": 303})
    p.mb(3, 303, CHARACTER, SkeletonAnimation={"m_FileID": 0, "m_PathID": 301}, idleBaseRandomClipPool=[], idleClipPool=[])
    p.node(8, "ChatDialog", father=1, pos=dialog_at)
    p.node(11, "Talk", father=8, pos=(98.0, 277.0))
    p.node(12, "Think", father=8, pos=(68.0, 277.0))
    p.mb(8, 801, CHAT, DialogBoxes=[{"Type": 0, "GameObject": {"m_FileID": 0, "m_PathID": 11}},
                                    {"Type": 1, "GameObject": {"m_FileID": 0, "m_PathID": 12}}])
    p.mb(1, 1002, ASPECT, **aspect_component(scale=(2,), offsets=[(11, (0, 0, 0), (14.7, 41.55, 0)),
                                                                  (12, (0, 0, 0), (10.2, 41.55, 0))]))
    return p


def kinds_of(p: Prefab) -> dict:
    return {pid: kind for pid, (kind, _) in p.objects.items()}


def patched(p: Prefab, k: float, offset) -> Prefab:
    trees = lobby_camera.camera_trees(kinds_of(p), lambda pid: p.objects[pid][1], SCRIPTS, "LobbyTest", k, offset)
    out = Prefab()
    out.objects = {pid: (kind, trees.get(pid, copy.deepcopy(tree))) for pid, (kind, tree) in p.objects.items()}
    return out


def point(k, offset, x, y):
    return k * x + offset[0], k * y + offset[1]


def assert_mapped(before: dict, after: dict, k: float, offset, anchors=("Talk", "Think")) -> None:
    """after == before mapped by p -> k p + offset, scales times k: the skeletons, touch boxes, pins, particle
    systems and dialogue anchors; the anchors within the game's whole-unit rounding of its aspect offsets."""
    assert after["prefab"] == before["prefab"] and after["main"] == before["main"]
    old, new = {l["name"]: l for l in before["layers"]}, {l["name"]: l for l in after["layers"]}
    assert old.keys() == new.keys()
    for name, layer in old.items():
        x, y = point(k, offset, layer["x"], layer["y"])
        assert new[name]["x"] == pytest.approx(x, abs=0.02 * (1 + k)) and new[name]["y"] == pytest.approx(y, abs=0.02 * (1 + k))
        assert new[name]["scale"] == pytest.approx(layer["scale"] * k, rel=1e-4, abs=6e-6 * (1 + k))
        assert new[name]["z"] == pytest.approx(layer["z"] * k, abs=1e-3)
        rest = {key for key in layer if key not in ("x", "y", "scale", "z")}
        assert {key: new[name][key] for key in rest} == {key: layer[key] for key in rest}
    for key in ("drags", "touches"):
        assert [b["name"] for b in after[key]] == [b["name"] for b in before[key]]
        for a, b in zip(before[key], after[key]):
            l, bt = point(k, offset, a["rect"][0], a["rect"][1])
            r, t = point(k, offset, a["rect"][2], a["rect"][3])
            assert b["rect"] == pytest.approx([l, bt, r, t], abs=0.1 * (1 + k))
            assert b["rot"] == pytest.approx(a["rot"], abs=0.02)
            assert {f: v for f, v in b.items() if f != "rect" and f != "rot"} == {f: v for f, v in a.items() if f != "rect" and f != "rot"}
    for name in anchors:
        x, y = point(k, offset, *before["dialog"][name])
        # the game rounds k A t where the original rounded A t: at most half a unit each, k times the one
        assert after["dialog"][name] == pytest.approx([x, y], abs=0.6 + 0.5 * k)
    assert len(after["pins"]) == len(before["pins"])
    for a, b in zip(before["pins"], after["pins"]):
        assert {f: v for f, v in b.items() if not isinstance(v, (int, float))} == {f: v for f, v in a.items() if not isinstance(v, (int, float))}
    systems = before.get("fx", {}).get("systems", [])
    assert len(after.get("fx", {}).get("systems", [])) == len(systems)
    for a, b in zip(systems, after["fx"]["systems"]):
        assert b["name"] == a["name"] and b["path"].replace(f"/{lobby_camera.NODE}/", "/") == a["path"]
        if a.get("anchor"):  # relative to the bone it follows; else in the root's space, like static
            assert b["rel"] == a["rel"]
        for matrix_of in (lambda s: s.get("static"), lambda s: (s.get("anchor") or {}).get("go"),
                          lambda s: None if s.get("anchor") else s.get("rel")):
            m, n = matrix_of(a), matrix_of(b)
            if m is None:
                assert n is None
                continue
            want = [v * k for v in m]
            want[3], want[7] = point(k, offset, m[3], m[7])
            assert n == pytest.approx(want, rel=1e-4, abs=0.02 * (1 + k))


@pytest.mark.parametrize("k, offset", [(1.0, (0.0, 0.0)), (1.25, (0.0, -180.0)), (0.8, (40.0, 96.5)), (2.0, (0.0, 1200.0))])
def test_camera_is_a_similarity_of_the_lobby(k, offset):
    for dialog_at in ((0.0, 0.0), (10.0, -20.0)):
        before = official_like(dialog_at).read("LobbyTest", SKELETONS, core_clips())
        after = patched(official_like(dialog_at), k, offset).read("LobbyTest", SKELETONS, core_clips())
        assert_mapped(before, after, k, offset)


def test_camera_changes_only_the_component_and_its_targets():
    p = official_like()
    trees = lobby_camera.camera_trees(kinds_of(p), lambda pid: p.objects[pid][1], SCRIPTS, "LobbyTest", 1.5, (3.0, -40.0))
    assert set(trees) == {1002, 100002, 100011, 100012}
    spine, talk, aspect = trees[100002], trees[100011], trees[1002]
    assert spine["m_LocalScale"] == {"x": 1.5, "y": 1.5, "z": 1.5} and spine["m_LocalPosition"] == {"x": 3.0, "y": -40.0, "z": 0.0}
    assert talk["m_LocalPosition"] == {"x": 1.5 * 98 + 3, "y": 1.5 * 277 - 40, "z": 0.0} and talk["m_LocalScale"]["x"] == 1.0
    assert aspect["offsetSettings"][0]["MostNarrowAdd"] == pytest.approx({"x": 14.7 * 1.5, "y": 41.55 * 1.5, "z": 0.0})
    assert (aspect["mostNarrowScale"], aspect["mostWideScale"]) == (1.15, 1.3)
    # the prefab's own trees are left as they are
    assert p.objects[100002][1]["m_LocalScale"]["x"] == 1.0 and p.objects[1002][1]["offsetSettings"][0]["MostNarrowAdd"]["x"] == 14.7


def refuse(p: Prefab, root="LobbyTest"):
    with pytest.raises(ServiceError) as caught:
        lobby_camera.camera_trees(kinds_of(p), lambda pid: p.objects[pid][1], SCRIPTS, root, 1.2, (0.0, 0.0))
    assert caught.value.code == "lobby_camera_unsupported"


def test_prefabs_the_camera_cannot_frame_are_refused():
    refuse(official_like(), root="LobbyOther")
    no_component = official_like()
    no_component.objects[1][1]["m_Component"] = [c for c in no_component.objects[1][1]["m_Component"]
                                                 if c["component"]["m_PathID"] != 1002]
    refuse(no_component)
    for field in ("mostNarrowScale", "scaleTransforms", "offsetSettings"):
        missing = official_like()
        del missing.objects[1002][1][field]
        refuse(missing)
    external = official_like()
    external.objects[1002][1]["scaleTransforms"] = [{"m_FileID": 1, "m_PathID": 100002}]
    refuse(external)
    rect = official_like()
    rect.objects[100002] = ("RectTransform", rect.objects[100002][1])
    refuse(rect)
    scaled_parent = official_like()
    scaled_parent.objects[100008][1]["m_LocalScale"] = {"x": 2.0, "y": 2.0, "z": 1.0}
    refuse(scaled_parent)
    two = official_like()
    two.mb(1, 1003, ASPECT, **aspect_component(scale=(2,)))
    refuse(two)
    for k, offset in ((0.0, (0, 0)), (math.inf, (0, 0)), (1.0, (0, math.nan)), (1.0, (0,)), (True, (0, 0)), (1.0, (0, 1e9))):
        with pytest.raises(ServiceError) as caught:
            lobby_camera.check_params(k, offset)
        assert caught.value.code == "bad_params"


def test_compose_frames_a_camera_on_a_camera():
    k, offset = lobby_camera.compose((1.2, (0.0, -100.0)), (1.5, (10.0, 20.0)))
    assert k == pytest.approx(1.8) and offset == pytest.approx([10.0, -130.0])
    x, y = 30.0, 40.0
    once = point(1.5, (10.0, 20.0), *point(1.2, (0.0, -100.0), x, y))
    assert point(k, offset, x, y) == pytest.approx(once)


# --- the game's own prefabs ---

@pytest.fixture(scope="module")
def real_game(tmp_path_factory):
    if not GAME_DIR.is_dir():
        pytest.skip("No local game install")
    from bmpt.gameassets import AssetCache, GameAssets
    from ba_modding_toolkit.searching import get_search_dirs
    return GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path_factory.mktemp("camera") / "cache", allowed=False))


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="No local game install")
@pytest.mark.parametrize("core, k, offset", [("azusa_home", 1.3, (0.0, -150.0)), ("hina_home", 1.6, (0.0, 260.0)),
                                             ("shiroko_home", 0.9, (0.0, 40.0))])
def test_real_prefab_round_trip(real_game, tmp_path, core, k, offset):
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.naming import parse_filename

    before = real_game.lobby_info(core)
    name, root = real_game.lobby_prefab(core)
    assert root == before["prefab"]
    base = real_game._bundle_path(name)
    out = tmp_path / "out"
    out.mkdir()
    output = out / base.name
    lobby_camera.patch_prefab(base, output, before["prefab"], k, offset)
    # the name's CRC holds, the base file is untouched
    assert Bundle.check_crc_match(output) and parse_filename(output.name).crc == parse_filename(base.name).crc
    after = real_game.lobby_info(core, prefab=output)
    assert_mapped(before, after, k, offset)
    if core == "shiroko_home":  # the wrap Hold intro keeps its plan
        assert after["intro"].keys() == before["intro"].keys()
    # read again from the game's own prefab: the official framing, not the camera's
    assert real_game.lobby_info(core) == before
    shutil.rmtree(out)



def _without_node(intro: dict) -> dict:
    """An intro with the camera node and its curves taken out, the other nodes' indices and paths as before it."""
    nodes = intro.get("nodes", [])
    cam = next((i for i, n in enumerate(nodes) if n["path"].rsplit("/", 1)[-1] == lobby_camera.NODE), None)
    if cam is None:
        return intro

    def index(value):
        return value - (value > cam) if isinstance(value, int) and not isinstance(value, bool) else value

    def fix(value, key=None):
        if isinstance(value, dict):
            node = value.get("on") in (None, "node")  # props index nodes, volumes, systems or sprites by "on"
            # what lies below the camera node is moved by it during the intro: only that move is new
            return {k: (v if k == "i" and not node else fix(v, k)) for k, v in value.items()
                    if not (k == "moves" and isinstance(v, dict) and v.get("nodes") == [cam])}
        if isinstance(value, list):
            return [fix(v, key) for v in value]
        if key in ("i", "node", "gate"):
            return index(value)
        if key == "path" and isinstance(value, str):
            return value.replace(f"/{lobby_camera.NODE}/", "/")
        return value

    out = {k: v for k, v in intro.items() if k not in ("nodes", "props")}
    out["nodes"] = [n for i, n in enumerate(nodes) if i != cam]
    out["props"] = [p for p in intro.get("props", []) if not (p.get("on") == "node" and p.get("i") == cam)]
    return fix(out)


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="No local game install")
@pytest.mark.parametrize("core, k, offset, held", [("ch0294_home", 1.3, (0.0, 0.0), True),
                                                   ("azusa_home", 0.85, (0.0, 120.0), True),
                                                   ("shiroko_home", 1.2, (0.0, -60.0), True),
                                                   ("hina_home", 1.4, (0.0, 200.0), True),
                                                   ("ch0247_home", 1.2, (0.0, 0.0), False)])
def test_real_camera_keeps_the_official_intro(real_game, tmp_path, core, k, offset, held):
    """BL-187: the camera node holds the official framing through the intro and the camera from its rest pose on; the
    intro is otherwise the official one. A lobby whose root Animator animates below its targets keeps the targets'
    own camera (the intro framed too)."""
    from bmpt.gameassets import intro

    before = real_game.lobby_info(core)
    name, root = real_game.lobby_prefab(core)
    base = real_game._bundle_path(name)
    cab = lobby_camera.timeline_cab(base)
    timeline = Path(real_game._resolve_cabs({cab})[cab])
    output = tmp_path / base.name
    assert lobby_camera.patch_prefab(base, output, root, k, offset, timeline=timeline) is held
    after = real_game.lobby_info(core, prefab=output)
    assert_mapped(before, after, k, offset)
    if not held:
        assert not any(n["path"].endswith(lobby_camera.NODE) for n in after["intro"].get("nodes", []))
        return
    assert _without_node(after["intro"]) == before["intro"]
    nodes = after["intro"]["nodes"]
    cam = next(i for i, n in enumerate(nodes) if n["path"] == f"{root}/{lobby_camera.NODE}")
    assert nodes[cam]["trs"][:2] == pytest.approx(list(offset)) and nodes[cam]["trs"][7:] == pytest.approx([k, k, k])
    curves = {(p["attr"], p["k"]): p["c"] for p in after["intro"]["props"] if p.get("on") == "node" and p["i"] == cam}
    rest = before["intro"]["idle"]
    official, camera = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (*offset, 0.0, k, k, k)
    for (attr, axis), curve in curves.items():
        slot = (0 if attr == "position" else 3) + axis
        assert intro.evaluate(curve, 0.0) == pytest.approx(official[slot])
        assert intro.evaluate(curve, rest - 0.01) == pytest.approx(official[slot])
        assert intro.evaluate(curve, rest + 0.5) == pytest.approx(camera[slot], abs=1e-4)
    assert len(curves) == 6

# --- the library: a camera is a Memorial Lobby choice of its student ---

@pytest.fixture(scope="module")
def lobby_sources(real_game):
    """Azusa's lobby art and prefab bundles and Shiroko's prefab bundle (another student's, of the same group)."""
    prefabs = real_game._lobby_prefabs()
    try:
        art = real_game.spine_cores("lobby")["azusa_home"]
        azusa, shiroko = (real_game._bundle_path(prefabs[name]) for name in ("lobbyazusa", "lobbyshiroko"))
    except KeyError:
        pytest.skip("Lobby fixture is unavailable")
    return art, azusa, shiroko


@pytest.fixture
def camera_env(tmp_path, monkeypatch, lobby_sources):
    from types import SimpleNamespace
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.library import ModLibrary, safety, storage
    from test_library import FakeCatalog, FakeVariant

    art, azusa, shiroko = lobby_sources
    folder = tmp_path / "game"
    folder.mkdir()
    for source in (*art, azusa, shiroko):
        if not storage.atomic_clone(source, folder / source.name):
            shutil.copy2(source, folder / source.name)
        (folder / source.name).chmod(0o644)
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(folder))
    settings.set("compression_method", "lz4")
    settings.set("enable_crc_correction", "true")
    game = GameAssets([folder], AssetCache(tmp_path / "cache", allowed=False))
    monkeypatch.setattr(safety, "check_game_not_running", lambda: None)
    library = ModLibrary(tmp_path / "library", FakeCatalog(FakeVariant("azusa", None, "Azusa_home")), game, settings)
    return SimpleNamespace(library=library, game=game, folder=folder, prefab=folder / azusa.name,
                           other=folder / shiroko.name, official=azusa.read_bytes(), other_bytes=shiroko.read_bytes(),
                           art={folder / p.name: p.read_bytes() for p in art})


def save(e, source="official", k=1.25, offset=(0.0, -120.0)):
    pin = e.library.current_pin()
    return e.library.save_camera("azusa", source, k, list(offset), e.prefab.name, "LobbyAzusa", pin=pin)


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="No local game install")
def test_camera_choice_applies_switches_and_restores(camera_env):
    from bmpt.library import lobby_camera as lc
    from bmpt.library.judge import core_of

    e, lib = camera_env, camera_env.library
    pin = lib.current_pin()
    record = save(e)
    meta = record[lc.TAG]
    assert (meta["variant"], meta["source"], meta["number"], meta["base"], meta["root"]) == ("azusa", "official", 1, "official", "LobbyAzusa")
    # the intro keeps the official framing (BL-187): the prefab copies the timeline of the art's own bundle
    cab = lc.timeline_cab(e.prefab)
    assert meta["timeline"] in {path.name for path in e.art} and cab is not None
    # its own slot, by the prefab's CAB: never Shiroko's
    assert meta["slot"] == core_of(e.prefab.name) != core_of(e.other.name)
    # listed as a lobby choice of Azusa; hidden from the generic sources
    entry = next(m for m in lib.mods() if m.id == record["id"])
    assert (entry.kind, entry.target, entry.core, entry.camera["number"], entry.camera["source"]) == ("lobby", "azusa", "azusa_home", 1, "official")
    assert not lib.other_mods(pin)
    assert e.prefab.read_bytes() == e.official  # saving writes nothing in the game
    # the preview: the official art, the camera's layout
    assert lib.preview_bundles(record["id"]) == lib.official_bundles("azusa_home")
    before, after = e.game.lobby_info("azusa_home"), e.game.lobby_info("azusa_home", prefab=lib.camera_prefab(record["id"])[1])
    assert_mapped(before, after, 1.25, (0.0, -120.0))

    source = f"mod:{record['id']}"
    result = lib.apply("azusa", source, kind="lobby", pin=pin)
    assert not result.deferred and e.prefab.read_bytes() == lib._paths(lib._record(record["id"]))[0].read_bytes()
    assert all(path.read_bytes() == data for path, data in e.art.items()) and e.other.read_bytes() == e.other_bytes
    assert lib.configured("azusa")["lobby"] == source
    assert lib.selection("azusa")["lobby"] == {"source": source, "installed": True, "error": None}
    # the other choices' previews read the official prefab while the camera holds the game's
    assert lib.lobby_prefab_base("azusa_home").read_bytes() == e.official
    # the master switch: off puts the official prefab back, on the camera again
    lib.toggle(False, True, [pin])
    assert e.prefab.read_bytes() == e.official and lib.configured("azusa")["lobby"] == source
    assert lib.apply("azusa", source, kind="lobby", pin=pin).deferred
    lib.toggle(True, False, [pin])
    assert e.prefab.read_bytes() != e.official and lib.selection("azusa")["lobby"]["installed"]
    # another lobby choice of Azusa puts the prefab back
    lib.apply("azusa", "official", kind="lobby", pin=pin)
    assert e.prefab.read_bytes() == e.official and lib.configured("azusa")["lobby"] is None
    assert lib.lobby_prefab_base("azusa_home") is None
    # and so does a restore of her lobby
    lib.apply("azusa", source, kind="lobby", pin=pin)
    lib.restore("azusa", kind="lobby", pin=pin)
    assert e.prefab.read_bytes() == e.official and lib.configured("azusa")["lobby"] is None
    # a second camera is numbered after the first; framed on the first, it composes
    second = save(e, source=source, k=1.2, offset=(0.0, 10.0))[lc.TAG]
    assert second["number"] == 2 and second["source"] == "official"
    assert second["scale"] == pytest.approx(1.5) and second["offset"] == pytest.approx([0.0, -134.0])
    # deleting the applied camera puts the official prefab back
    lib.apply("azusa", source, kind="lobby", pin=pin)
    lib.remove(record["id"], confirm=True)
    assert e.prefab.read_bytes() == e.official and lib.configured("azusa")["lobby"] is None


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="No local game install")
def test_camera_on_a_lobby_mod_and_after_a_game_update(camera_env):
    import zlib
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.library import lobby_camera as lc

    e, lib = camera_env, camera_env.library
    pin = lib.current_pin()
    # a lobby mod of Azusa: her art saved again (other bytes, the same assets)
    art = sorted(e.art)[0]
    made = e.folder.parent / "mod"
    made.mkdir()
    bundle = Bundle.load(art)
    assert bundle.save(made / art.name, SaveOptions(perform_crc=True, compression="lz4"))[0]
    mod = lib._store({art.name: made / art.name}, "Azusa mod", "azusa", "lobby")
    record = save(e, source=f"mod:{mod['id']}")
    assert record[lc.TAG]["source"] == f"mod:{mod['id']}"
    assert lib.preview_bundles(record["id"])[-1] == lib._paths(mod)[0]
    lib.apply("azusa", f"mod:{record['id']}", kind="lobby", pin=pin)
    assert art.read_bytes() == (made / art.name).read_bytes() and e.prefab.read_bytes() != e.official
    assert lib.configured("azusa")["lobby"] == f"mod:{record['id']}"
    # the lobby mod alone: the prefab goes back, the art stays the mod's
    lib.apply("azusa", f"mod:{mod['id']}", kind="lobby", pin=pin)
    assert e.prefab.read_bytes() == e.official and lib.configured("azusa")["lobby"] == f"mod:{mod['id']}"
    lib.apply("azusa", "official", kind="lobby", pin=pin)

    # a game update gives Azusa a new prefab file: the camera is made again on it, in place
    rebuilt = Bundle.load(e.prefab)
    stage = e.folder.parent / "update"
    stage.mkdir()
    assert rebuilt.save(stage / "new.bundle", SaveOptions(perform_crc=False, compression="lz4"))[0]
    crc = zlib.crc32((stage / "new.bundle").read_bytes())
    updated = e.folder / e.prefab.name.replace(e.prefab.name.rsplit("_", 1)[1], f"{crc}.bundle")
    assert updated != e.prefab
    e.prefab.unlink()
    shutil.move(stage / "new.bundle", updated)
    lc.refresh(lib, [pin], print)
    fresh = lib._record(record["id"])
    assert fresh["files"] == [updated.name] and fresh[lc.TAG]["prefab"] == updated.name
    assert lib._paths(fresh)[0].exists() and not (lib._paths(fresh)[0].parent / e.prefab.name).exists()
    lib.apply("azusa", f"mod:{record['id']}", kind="lobby", pin=pin)
    assert updated.read_bytes() == lib._paths(fresh)[0].read_bytes()
    assert Bundle.check_crc_match(updated)


def test_deleting_a_lobby_mod_deletes_the_cameras_framed_on_it(camera_env):
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.models import SaveOptions
    from bmpt.library import lobby_camera as lc

    e, lib = camera_env, camera_env.library
    pin = lib.current_pin()
    art = sorted(e.art)[0]
    made = e.folder.parent / "mod"
    made.mkdir()
    assert Bundle.load(art).save(made / art.name, SaveOptions(perform_crc=True, compression="lz4"))[0]
    mod = lib._store({art.name: made / art.name}, "Azusa mod", "azusa", "lobby")
    on_mod = save(e, source=f"mod:{mod['id']}")
    on_official = save(e)
    lib.apply("azusa", f"mod:{on_mod['id']}", kind="lobby", pin=pin)
    assert e.prefab.read_bytes() != e.official
    result = lib.remove(mod["id"], confirm=True)
    assert not result.failed
    left = {r["id"] for r in lc.cameras(lib)}
    assert on_mod["id"] not in left and on_official["id"] in left
    assert mod["id"] not in lib._records
    assert e.prefab.read_bytes() == e.official  # the camera's prefab went back with it
