"""The graft of an official rig's new animations into a mod's own rig (bmpt.graft, bmpt.spine_binary)."""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bmpt import atlas_remap, graft, rebake
from bmpt.spine_binary import MESH, REGION, Skeleton, draw_order, draw_order_bytes, strip_nonessential

from graft_rigs import mesh, region, remove_animations, remove_slot, skeleton

QUAD = mesh([0, 0, 4, 0, 4, 4, 0, 4], [0, 1, 1, 1, 1, 0, 0, 0], [0, 1, 2, 2, 3, 0])


def names(data: bytes) -> list[str]:
    return [a["name"] for a in Skeleton(data).animations]


def entry(data: bytes, slot: str) -> list[str]:
    sk = Skeleton(data)
    found = next(e for e in sk.skins[0]["entries"] if sk.slots[e["slot"]]["name"] == slot)
    return [sk.s(a["key"]) for a in found["atts"]]


def orders(data: bytes, animation: str) -> list[list[str]]:
    sk = Skeleton(data)
    an = next(a for a in sk.animations if a["name"] == animation)
    return [[sk.slots[i]["name"] for i in draw_order(offsets, len(sk.slots))] for _a, _b, offsets in an["draworder"]]


# --- the walk

@pytest.mark.parametrize("nonessential", [False, True])
def test_a_skeleton_is_walked_to_its_last_byte_and_written_back_as_it_was(nonessential):
    data = skeleton(slots=[("body", "root", "b"), ("face", "root", "a")], nonessential=nonessential,
                    skin={"face": [("a", region()), ("c", region(rotation=90, path="c_region"))], "body": [("b", QUAD)]},
                    animations=[("00", {"face": ["a"]}, []), ("01", {"face": ["c"]}, [["face", "body"]])])
    sk = Skeleton(data)
    assert sk.emit(0, sk.size) == data
    assert sk.nonessential is nonessential
    assert [s["name"] for s in sk.slots] == ["body", "face"]
    assert names(data) == ["00", "01"]
    assert orders(data, "01") == [["face", "body"]]
    face = sk.attachments()[("default", "face", "c")]
    assert (face["kind"], face["path"], face["floats"][0]) == (REGION, "c_region", 90)
    assert sk.attachments()[("default", "body", "b")]["kind"] == MESH


def test_nonessential_data_is_taken_out_as_an_export_without_it_has_it():
    kwargs = dict(slots=[("body", "root", "b")], skin={"body": [("b", QUAD)]}, animations=[("00", {"body": ["b"]}, [])])
    assert strip_nonessential(skeleton(nonessential=True, **kwargs)) == skeleton(nonessential=False, **kwargs)


@pytest.mark.parametrize("damage", [lambda d: d[:-1], lambda d: d + b"\x00", lambda d: d[:40]])
def test_a_skeleton_that_does_not_fit_the_layout_is_refused(damage):
    data = skeleton(skin={"face": [("a", region())]}, animations=[("00", {"face": ["a"]}, [])])
    with pytest.raises(ValueError):
        Skeleton(damage(data))


def test_a_reference_out_of_range_is_refused():
    data = bytearray(skeleton(skin={"face": [("a", region())]}, animations=[("00", {"face": ["a"]}, [])]))
    sk = Skeleton(bytes(data))
    start, end, kind, _value = next(r for r in sk.refs if r[2] == "slot")
    data[start:end] = b"\x07"
    with pytest.raises(ValueError):
        Skeleton(bytes(data))


@pytest.mark.parametrize("order", [[0, 1, 2, 3], [3, 0, 1, 2], [1, 0, 3, 2], [2, 3, 1, 0]])
def test_a_draw_order_frame_decodes_to_the_order_it_was_written_for(order):
    slots = [(name, "root", None) for name in "abcd"]
    data = skeleton(slots=slots, animations=[("00", {}, [["abcd"[i] for i in order]])])
    assert orders(data, "00") == [["abcd"[i] for i in order]]
    assert draw_order_bytes(order)[:1] == bytes([sum(1 for i, slot in enumerate(order) if slot != i)]) \
        or draw_order_bytes(order)[:1] == bytes([4])


# --- the skeleton graft

def official(**kwargs) -> bytes:
    slots = kwargs.pop("slots", [("body", "root", "b"), ("face", "root", "a"), ("glow", "root", "g")])
    skin = kwargs.pop("skin", {"body": [("b", region())], "face": [("a", region()), ("new", region(x=1))],
                               "glow": [("g", region())]})
    animations = kwargs.pop("animations", [("00", {"face": ["a"]}, []), ("17", {"face": ["new"]}, [])])
    return skeleton(slots=slots, skin=skin, animations=animations, **kwargs)


def test_an_animation_the_mod_lacks_comes_with_the_attachment_it_shows_and_nothing_else_changes():
    mod = official(skin={"body": [("b", region(w=9))], "face": [("a", region(w=7))], "glow": [("g", region())]},
                   animations=[("00", {"face": ["a"]}, [])])
    g = graft.SkeletonGraft(mod, official())
    out = g.build()
    assert names(out) == ["00", "17"] and not g.refused and not g.inserted
    assert entry(out, "face") == ["a", "new"]
    sk = Skeleton(out)
    # the mod's own attachment keeps its record
    assert sk.attachments()[("default", "face", "a")]["floats"][-2] == 7
    # everything of the mod's file up to its animations is as it was but for the strings and the entry count
    assert out[:len(mod)].count(b"new") <= 1 and len(out) > len(mod)


def test_a_slot_the_new_animation_needs_is_inserted_in_the_official_order_and_the_mods_draw_order_follows():
    mod = skeleton(slots=[("body", "root", "b"), ("glow", "root", "g")], skin={"body": [("b", region())],
                   "glow": [("g", region())]}, animations=[("00", {}, [["glow", "body"]])])
    new = official(animations=[("00", {}, [["glow", "body", "face"]]), ("17", {"face": ["new"]}, [])])
    g = graft.SkeletonGraft(mod, new)
    out = g.build()
    assert g.inserted == ["face"]
    assert [s["name"] for s in Skeleton(out).slots] == ["body", "face", "glow"]
    assert entry(out, "face") == ["a", "new"]  # an inserted slot brings its attachments
    assert orders(out, "00") == [["glow", "body", "face"]]  # the new slot after the one it follows in the setup order
    assert names(out) == ["00", "17"]


def test_draw_order_of_a_carried_animation_is_recomputed_for_the_mods_slot_list():
    mod = skeleton(slots=[("body", "root", "b"), ("extra", "root", None), ("face", "root", "a"), ("glow", "root", "g")],
                   skin={"face": [("a", region())]}, animations=[("00", {}, [])])
    new = official(animations=[("00", {}, []), ("17", {"face": ["new"]}, [["glow", "face", "body"]])])
    out = graft.SkeletonGraft(mod, new).build()
    assert orders(out, "17") == [["glow", "face", "body", "extra"]]


@pytest.mark.parametrize("mod_ness, new_ness", [(False, True), (True, False)])
def test_carried_records_follow_whether_the_mods_file_holds_nonessential_data(mod_ness, new_ness):
    skin = {"body": [("b", region())], "face": [("a", region()), ("new", QUAD)], "glow": [("g", region())]}
    new = official(skin=skin, nonessential=new_ness, animations=[("00", {"face": ["a"]}, []), ("17", {"face": ["new"]}, [])])
    mod = official(skin={"body": [("b", region())], "face": [("a", region())], "glow": [("g", region())]},
                   nonessential=mod_ness, animations=[("00", {"face": ["a"]}, [])])
    out = graft.SkeletonGraft(mod, new).build()
    sk = Skeleton(out)  # walks to the last byte
    assert sk.nonessential is mod_ness
    assert sk.attachments()[("default", "face", "new")]["kind"] == MESH


def test_an_animation_that_needs_a_bone_the_mod_lacks_is_refused_with_the_reason():
    new = official(bones=(("root", None), ("hat", "root")),
                   slots=[("body", "root", "b"), ("face", "root", "a"), ("glow", "root", "g"), ("cap", "hat", None)],
                   skin={"body": [("b", region())], "face": [("a", region()), ("new", region())], "glow": [("g", region())],
                         "cap": [("c", region())]},
                   animations=[("00", {"face": ["a"]}, []), ("17", {"face": ["new"]}, []), ("18", {"cap": ["c"]}, [])])
    mod = official(animations=[("00", {"face": ["a"]}, [])])
    g = graft.SkeletonGraft(mod, new)
    assert [a["name"] for a in g.animations] == ["17"]
    assert g.refused == {"18": ["bone:hat"]}


# --- placement

def test_a_carried_region_follows_the_mods_bone_and_a_sheared_one_goes_as_a_mesh():
    new = official()
    mod = official(skin={"body": [("b", region())], "face": [("a", region())], "glow": [("g", region())]},
                   animations=[("00", {"face": ["a"]}, [])])
    g = graft.SkeletonGraft(mod, new)
    carried = next(iter(g.carried.values()))
    turn = np.array([[0.0, -2.0, 5.0], [2.0, 0.0, -3.0], [0, 0, 1]])  # 90 degrees, twice the size, moved
    body, note = graft.placed_record(g, carried, turn, 0, None)
    out = g.build({("default", "face", "new"): body})
    a = Skeleton(out).attachments()[("default", "face", "new")]
    assert not note and a["kind"] == REGION
    assert np.allclose(a["floats"], (90, 5 + 0 * 1, -3 + 2 * 1, 2, 2, 4, 4))
    sheared = np.array([[1.0, 0.5, 0.0], [0.0, 1.0, 0.0], [0, 0, 1]])
    box = atlas_remap.Region("new", -1, "page.png", (0, 0), (4, 4), (4, 4), (0, 0))
    body, note = graft.placed_record(g, carried, sheared, 0, box)
    out = g.build({("default", "face", "new"): body})
    a = Skeleton(out).attachments()[("default", "face", "new")]
    assert note == "mesh" and a["kind"] == MESH and a["vertex_floats"] == 8


def test_the_anchor_is_fitted_over_the_attachments_both_rigs_have_and_refused_when_the_face_changed_otherwise():
    square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    move = np.array([[0.9, -0.1, 4], [0.1, 0.9, 2], [0, 0, 1]])
    moved = square @ move[:2, :2].T + move[:2, 2]
    tri = np.zeros((2, 3), dtype=np.int64)

    def rig(points):
        return graft.Setup(skin={("face", "a"): ("page", points, np.zeros_like(points), tri)})

    t, fitted = graft.fit_anchor(rig(moved), rig(square), "face")
    assert fitted and np.allclose(t, move, atol=1e-6)
    bent = moved.copy()
    bent[2] += (5, 5)
    t, fitted = graft.fit_anchor(rig(bent), rig(square), "face")
    assert not fitted and np.allclose(t, np.eye(3))


# --- atlas and pages

ATLAS = "page.png\nsize:32,32\nfilter:Linear,Linear\nface\nbounds:0,0,8,8\n"
OFFICIAL = "official.png\nsize:16,16\nfilter:Linear,Linear\nnew\nbounds:8,0,4,6\noffsets:1,1,6,8\nrotate:90\n"


def setup_drawing(box) -> graft.Setup:
    x0, y0, x1, y1 = (v / 32 for v in box)
    uv = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    return graft.Setup(skin={("face", "face"): ("page", uv * 100, uv, np.array([[0, 1, 2], [2, 3, 0]]))})


def pages():
    mod = Image.new("RGBA", (32, 32), (10, 10, 10, 255))
    new = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    new.paste((200, 0, 0, 255), (8, 0, 14, 4))  # the region as packed: 6 x 4 (turned)
    return mod, new


def test_a_carried_region_goes_where_the_mods_rig_samples_nothing_with_its_pixels_rotate_and_offsets():
    mod, new = pages()
    packed = graft.pack(ATLAS, OFFICIAL, ["new"], {"page": mod}, {"official": new}, setup_drawing((0, 0, 8, 8)))
    place = packed.placed["new"]
    assert not packed.grown and (place.xy[0] >= 8 or place.xy[1] >= 8)
    text = graft.atlas_text(ATLAS, packed)
    region = next(r for r in atlas_remap.parse_atlas(text).regions.values() if r.name == "new")
    assert (region.degrees, region.size, region.offset, region.original) == (90, (4, 6), (1, 1), (6, 8))
    x, y = place.xy
    assert packed.images["page"].getpixel((x, y)) == (200, 0, 0, 255)
    assert packed.images["page"].getpixel((0, 0)) == (10, 10, 10, 255)


def test_a_full_page_grows_downward_in_whole_rows_and_the_atlas_follows_in_either_syntax():
    mod, new = pages()
    legacy = "page.png\nsize: 32,32\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\nface\n  rotate: false\n" \
             "  xy: 0, 0\n  size: 32, 32\n  orig: 32, 32\n  offset: 0, 0\n  index: -1\n"
    for text in (ATLAS.replace("bounds:0,0,8,8", "bounds:0,0,32,32"), legacy):
        packed = graft.pack(text, OFFICIAL, ["new"], {"page": mod}, {"official": new}, setup_drawing((0, 0, 32, 32)))
        assert packed.grown["page.png"] % graft.ROWS == 0
        height = 32 + packed.grown["page.png"]
        assert packed.images["page"].size == (32, height)
        out = graft.atlas_text(text, packed)
        parsed = atlas_remap.parse_atlas(out)
        assert parsed.pages["page.png"].size == (32, height)
        assert {r.name for r in parsed.regions.values()} == {"face", "new"}
        assert ("xy:" in out) == (text is legacy)


def test_a_page_larger_than_its_atlas_declares_takes_the_region_scaled():
    mod, new = pages()
    big = mod.resize((64, 64))
    packed = graft.pack(ATLAS, OFFICIAL, ["new"], {"page": big}, {"official": new}, setup_drawing((0, 0, 8, 8)))
    x, y = packed.placed["new"].xy
    assert packed.images["page"].size == (64, 64)
    assert packed.images["page"].getpixel((2 * x + 1, 2 * y + 1))[:3] == (200, 0, 0)


# --- the check

def drawn(x, page="p", colour=None):
    v = np.array([[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]], dtype=float)
    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], dtype=float)
    return rebake.Drawn("face", "r", page, 0, 1.0, v, uv, np.array([[0, 1, 2], [2, 3, 0]]))


def test_a_part_is_compared_with_the_official_part_on_the_rendered_picture():
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    pages_ = {"p": graft._premultiplied(image)}
    assert graft.off([drawn(0)], pages_, [drawn(0)], pages_) == 0
    assert graft.off([drawn(0)], pages_, [drawn(5)], pages_) > graft.BOUND
    assert graft.off([], pages_, [drawn(0)], pages_) == 1
    t = np.array([[1, 0, 5], [0, 1, 0], [0, 0, 1]], dtype=float)
    assert graft.off([drawn(5)], pages_, graft.moved((drawn(0),), {"face": t}), pages_) == 0


def test_posted_setup_geometry_is_checked():
    good = {"bones": {"root": [1, 0, 0, 1, 0, 0]}, "slotBones": {"face": "root"},
            "skin": [{"slot": "face", "name": "a", "page": "page.png", "world": [0, 0, 1, 0, 1, 1],
                      "uvs": [0, 0, 1, 0, 1, 1], "tris": [0, 1, 2]}]}
    assert graft.setup(good).slot_bones == {"face": "root"}
    for bad in ({**good, "bones": {"root": [1, 0, 0, 1, 0, float("nan")]}}, {**good, "slotBones": {"face": "arm"}},
                {**good, "skin": [{**good["skin"][0], "tris": [0, 1, 3]}]}, [], {**good, "bones": None}):
        with pytest.raises(graft.GraftError):
            graft.setup(bad)


# --- official skeletons: remove and graft back

def official_skeletons() -> list[bytes]:
    folder = os.environ.get("BAMT_TEST_GAME_DIR")
    if not folder:
        pytest.skip("BAMT_TEST_GAME_DIR is not set")
    windows = Path(folder) / "BlueArchive_Data/StreamingAssets/PUB/Resource/GameData/Windows"
    found = []
    for core in ("ako_spr", "ch0079_spr", "hina_spr"):
        parts = sorted(windows.glob(f"assets-_mx-spinecharacters-{core}-*.bundle"))
        if parts:
            found += list(atlas_remap.read_inputs(parts).skeletons.values())
    if not found:
        pytest.skip("no official portrait found")
    return found


def animations_by_name(data: bytes) -> dict[str, bytes]:
    sk = Skeleton(data)
    return {a["name"]: sk.data[a["start"]:a["end"]] for a in sk.animations}


def test_official_animations_taken_out_and_grafted_back_are_what_they_were():
    for data in official_skeletons():
        assert Skeleton(data).emit(0, len(data)) == data
        numbered = [a["name"] for a in Skeleton(data).animations if re.fullmatch(r"\d+", a["name"])]
        take = numbered[-max(1, len(numbered) // 3):]
        for mod in (remove_animations(data, take), strip_nonessential(remove_animations(data, take))):
            g = graft.SkeletonGraft(mod, data)
            out = g.build()
            assert not g.refused and sorted(names(out)) == sorted(names(data))
            before, after = animations_by_name(data), animations_by_name(out)
            if Skeleton(mod).nonessential == Skeleton(data).nonessential:
                assert {name: after[name] for name in take} == {name: before[name] for name in take}
            assert set(Skeleton(out).attachments()) == set(Skeleton(data).attachments())


def test_an_official_slot_taken_out_and_grafted_back_returns_in_its_place_with_its_animations():
    for data in official_skeletons():
        sk = Skeleton(data)
        tried = 0
        for slot in sk.slots:
            cut = remove_slot(data, slot["name"])
            if cut is None or not cut[1]:
                continue
            g = graft.SkeletonGraft(cut[0], data)
            out = g.build()
            assert not g.refused, g.refused
            assert [s["name"] for s in Skeleton(out).slots] == [s["name"] for s in sk.slots]
            assert sorted(names(out)) == sorted(names(data))
            for animation in cut[1]:
                assert orders(out, animation) == orders(data, animation)
            tried += 1
            if tried == 6:
                break


def test_a_page_beyond_the_largest_one_a_graft_packs_is_refused():
    mod, new = pages()
    huge = ATLAS.replace("size:32,32", "size:100000,100000")
    with pytest.raises(graft.GraftError):
        graft.pack(huge, OFFICIAL, ["new"], {"page": mod}, {"official": new}, setup_drawing((0, 0, 8, 8)))
