"""The graft of an official rig's new animations into a memorial lobby mod's own rig, through the library: what the
compatibility check tells, the graft (``graft.plain``), the pose check's result and the repaired copy."""

from __future__ import annotations

import pytest
from PIL import Image

from bmpt import atlas_remap as remap
from bmpt.errors import ServiceError
from bmpt.library.integrity import inspect as inspect_bundle
from bmpt.spine_binary import Skeleton

from graft_rigs import region, skeleton
from test_atlas_remap import _official_name, write_bundle
from test_library import game_bundle, setup_library  # noqa: F401 - fixture

SLOTS = [("body", "root", "b"), ("face", "root", "a"), ("cap", "hat", None)]
BONES = (("root", None), ("hat", "root"))


def official_rig() -> bytes:
    return skeleton(bones=BONES, slots=SLOTS, events=["voice"],
                    skin={"body": [("b", region())], "face": [("a", region()), ("new", region(x=2))],
                          "cap": [("c", region())]},
                    animations=[("Idle_01", {"face": ["a"]}, []), ("Talk_01", {"face": ["new"]}, [], ["voice"]),
                                ("Look_01", {}, [["face", "body", "cap"]]), ("Hat_01", {"cap": ["c"]}, [])])


def mod_rig() -> bytes:
    # the mod's own rig: no hat bone, its own body, the idle only
    return skeleton(slots=SLOTS[:2], skin={"body": [("b", region(w=9))], "face": [("a", region())]},
                    animations=[("Idle_01", {"face": ["a"]}, [])])


OFFICIAL_ATLAS = ("page.png\nsize:16,16\nfilter:Linear,Linear\nb\nbounds:0,0,4,4\na\nbounds:4,0,4,4\n"
                  "new\nbounds:8,0,4,4\nc\nbounds:12,0,4,4\n")
MOD_ATLAS = "page.png\nsize:16,16\nfilter:Linear,Linear\nb\nbounds:0,0,8,8\na\nbounds:8,0,4,4\n"


@pytest.fixture
def lobby(setup_library, monkeypatch):
    monkeypatch.setattr("bmpt.library.integrity.inspect", inspect_bundle)
    env = setup_library
    env.original.unlink()
    official = game_bundle(env.game_dir, "test_home_gl", part="003")
    official.unlink()
    page = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    page.paste((0, 200, 0, 255), (8, 0, 12, 4))  # the official new region
    current = _official_name(write_bundle(official, {"test.atlas": OFFICIAL_ATLAS}, {"page": page}, official_rig().decode("utf-8", "surrogateescape")))
    art = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    art.paste((200, 0, 0, 255), (0, 0, 12, 8))
    source = write_bundle(env.tmp / "upload" / current.name.replace("2026-01-01", "2025-01-01"),
                          {"test.atlas": MOD_ATLAS}, {"page": art}, mod_rig().decode("utf-8", "surrogateescape"))
    entry, = env.library.add([source])
    env.entry, env.current = entry, current
    return env


def test_the_check_tells_which_new_animations_a_lobby_mod_takes_and_why_not_the_others(lobby):
    report = lobby.library.compatibility(lobby.entry.id)
    graft = report["graft"]
    assert graft["kind"] == "own" and graft["available"]
    assert graft["animations"] == {"test.skel": ["Talk_01", "Look_01"]}
    assert graft["refused"] == {"test.skel": {"Hat_01": ["bone:hat"]}}


def test_a_lobby_graft_carries_the_animations_their_events_and_regions_and_keeps_the_mods_rig(lobby):
    library = lobby.library
    first = library.graft(lobby.entry.id)
    assert first["plain"] and first["rigs"]["test.atlas"]["animations"] == ["Talk_01", "Look_01"]
    preview = library.graft_check(first["token"], {"test.atlas": {"failed": []}})
    report = preview["report"]
    assert report["grafted"] == ["Talk_01", "Look_01"] and report["animations"]
    assert report["refused"] == {"Hat_01": ["bone:hat"]} and report["partial"]
    saved = library.accept_remap(preview["preview"])
    built = remap.read_inputs(sorted(saved.mod.file.parent.glob("*.bundle")))
    sk = Skeleton(built.skeletons["test.skel"])
    assert [a["name"] for a in sk.animations] == ["Idle_01", "Talk_01", "Look_01"]
    assert sk.events == ["voice"] and [b["name"] for b in sk.bones] == ["root"]
    assert sk.attachments()[("default", "body", "b")]["floats"][-2] == 9  # the mod's own record
    text = built.atlases["test.atlas"].decode()
    assert "\nnew\n" in text and "b\nbounds:0,0,8,8\na\nbounds:8,0,4,4\n" in text  # the mod's regions as they were
    assert "size:16,20" in text and report["grown"] == 4  # no room left: the page grew
    placed = next(r for r in remap.parse_atlas(text).regions.values() if r.name == "new")
    assert built.textures["page"].getpixel(placed.xy) == (0, 200, 0, 255)
    assert built.textures["page"].getpixel((1, 1)) == (200, 0, 0, 255)  # the mod's art as it was


def test_an_animation_the_page_cannot_pose_is_built_out_and_reported(lobby):
    library = lobby.library
    first = library.graft(lobby.entry.id)
    preview = library.graft_check(first["token"], {"test.atlas": {"failed": ["Talk_01"]}})
    report = preview["report"]
    assert report["grafted"] == ["Look_01"] and report["refused"]["Talk_01"] == ["pose:"]
    saved = library.accept_remap(preview["preview"])
    sk = Skeleton(remap.read_inputs(sorted(saved.mod.file.parent.glob("*.bundle"))).skeletons["test.skel"])
    assert [a["name"] for a in sk.animations] == ["Idle_01", "Look_01"] and sk.events == []


def test_a_pose_check_naming_what_the_graft_did_not_carry_is_refused(lobby):
    library = lobby.library
    first = library.graft(lobby.entry.id)
    with pytest.raises(ServiceError) as caught:
        library.graft_check(first["token"], {"test.atlas": {"failed": ["Hat_01"]}})
    assert caught.value.code == "bad_params"
    with pytest.raises(ServiceError) as caught:
        library.graft_build(library.graft(lobby.entry.id)["token"], {})
    assert caught.value.code == "bad_params"
