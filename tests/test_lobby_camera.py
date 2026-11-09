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
        assert b["name"] == a["name"] and b["path"] == a["path"]
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
    base = real_game._bundle_path(real_game._lobby_prefabs()[before["prefab"].lower()])
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
