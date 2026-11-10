"""A game update that repacks a portrait's atlas and adds expressions under existing mods: what the library detects
(layout, animations, regions), when it offers the remap, and what the remap takes from the official files."""

from __future__ import annotations

import json
import struct

import pytest
from PIL import Image

from ba_modding_toolkit import core
from ba_modding_toolkit.models import FilePair, ModUpdateResult
from bmpt import atlas_remap as remap
from bmpt import spine_names
from bmpt.library import fingerprint
from bmpt.library.integrity import inspect as inspect_bundle

from test_atlas_remap import (_official_name, atlas_text, pack, repacked, split_official,  # noqa: F401 - fixture
                              write_bundle)
from test_built_against import animation, binary_skeleton, sidecar, spine  # noqa: F401 - fixture
from test_library import header_bundle, make_mod, reload_library, setup_library  # noqa: F401 - fixture

OLD = [("page.png", (16, 16), [("part", -1, (1, 1), (4, 4), (4, 4), (0, 0), 0)])]
# the same page size: the part moved and an expression's region came with it
NEW = [("page.png", (16, 16), [("part", -1, (8, 8), (4, 4), (4, 4), (0, 0), 0),
                               ("smile", -1, (1, 1), (4, 4), (4, 4), (0, 0), 0)])]
RED, BLUE = (255, 0, 0, 255), (0, 0, 255, 255)


def skeleton(animations, regions=("part",)):
    return json.dumps({"skeleton": {"spine": "4.2.33"}, "bones": [{"name": "root"}],
                       "slots": [{"name": "slot", "bone": "root"}],
                       "skins": [{"name": "default", "attachments": {"slot": {name: {} for name in regions}}}],
                       "animations": {name: {} for name in animations}}).encode()


# --- what a skeleton draws of each region ---

L_SHAPE = [0.0, 0.0, 0.3, 0.0, 0.3, 0.7, 1.0, 0.7, 1.0, 1.0, 0.0, 1.0]


def mesh_skeleton(sequence=False, linked=False):
    """A JSON skeleton: an L-shaped mesh on ``part``, a region attachment on ``whole``."""
    mesh = {"type": "mesh", "path": "part", "uvs": L_SHAPE + [0.1, 0.9], "hull": 6, "triangles": [], "vertices": []}
    if sequence:
        mesh = {**mesh, "path": "frame", "sequence": {"count": 2, "start": 3, "digits": 2}}
    slot = {"body": mesh, "whole": {}}
    if linked:
        slot["other"] = {"type": "linkedmesh", "parent": "body", "path": "costume"}
    return json.dumps({"skeleton": {"spine": "4.2.33"}, "bones": [{"name": "root"}],
                       "slots": [{"name": "slot", "bone": "root"}],
                       "skins": [{"name": "default", "attachments": {"slot": slot}}]}).encode()


def binary_mesh_skeleton():
    """A 4.2 binary skeleton whose one slot holds the L-shaped mesh on ``part`` (by path), a linked mesh of it on
    ``costume`` and a region attachment named ``whole``."""
    def varint(value):
        return bytes([value])

    def text(value):
        return varint(len(value) + 1) + value.encode()

    def floats(*values):
        return struct.pack(f">{len(values)}f", *values)

    head = bytes(8) + text("4.2.33") + floats(0, 0, 10, 10, 100) + b"\x00"
    strings = varint(5) + b"".join(map(text, ("body", "part", "whole", "other", "costume")))
    bones = varint(1) + text("root") + floats(*[0] * 8) + b"\x00\x00"
    slots = varint(1) + text("slot") + varint(0) + bytes(4) + b"\xff\xff\xff\xff" + varint(0) + varint(0)
    uvs = L_SHAPE + [0.1, 0.9]
    mesh = (bytes([2 | 16]) + varint(2)  # mesh with a path: "part"
            + varint(6) + varint(7) + floats(*[0] * 14) + floats(*uvs) + varint(0) * ((14 - 6 - 2) * 3))
    linked = bytes([3 | 16]) + varint(5) + varint(0) + varint(1)  # path "costume", default skin, parent "body"
    region = bytes([0]) + floats(*[0] * 6)
    skin = (varint(1) + varint(0) + varint(3) + varint(1) + mesh + varint(4) + linked + varint(3) + region)
    return head + strings + bones + slots + varint(0) * 4 + skin + varint(0) + varint(0) + varint(0)


@pytest.mark.parametrize("data", [mesh_skeleton(linked=True), binary_mesh_skeleton()], ids=["json", "binary"])
def test_hulls_name_the_outline_of_each_region_a_mesh_draws(data):
    found = spine_names.hulls(data)
    assert set(found) == {"part", "costume", "whole"}
    assert found["whole"] is None  # a region attachment draws its whole rectangle
    assert found["part"] == [pytest.approx(L_SHAPE)] and found["costume"] == found["part"]  # the parent's outline
    assert spine_names.read(data).bones == [("root", "")]  # the names are read as before


def test_hulls_of_sequences_and_of_unreadable_skeletons():
    found = spine_names.hulls(mesh_skeleton(sequence=True))
    assert found == {"frame03": [L_SHAPE], "frame04": [L_SHAPE], "whole": None}
    for damaged in (b"3.8.99 old", binary_mesh_skeleton()[:-9], b'{"skins": [{"attachments": {"slot": {"a": {"type": "mesh"}}}}]}'):
        with pytest.raises(ValueError):
            spine_names.hulls(damaged)
    data = json.loads(mesh_skeleton(sequence=True))
    data["skins"][0]["attachments"]["slot"]["body"]["sequence"]["digits"] = 10 ** 9
    with pytest.raises(ValueError):
        spine_names.hulls(json.dumps(data).encode())
    # a mesh whose hull is no outline is drawn whole
    data = json.loads(mesh_skeleton())
    data["skins"][0]["attachments"]["slot"]["body"]["hull"] = 2
    assert spine_names.hulls(json.dumps(data).encode())["part"] is None


# --- the engine ---

def nested():
    """Two layouts of one page. The L-shaped ``part`` has ``eye`` packed into the empty corner of its rectangle in
    the old one; in the new one ``part`` and ``eye`` moved and the new ``smile`` sits in that corner."""
    old = [("page.png", (64, 64), [("part", -1, (0, 0), (40, 40), (40, 40), (0, 0), 0),
                                   ("eye", -1, (20, 4), (12, 12), (12, 12), (0, 0), 0)])]
    new = [("page.png", (64, 64), [("part", -1, (20, 20), (40, 40), (40, 40), (0, 0), 0),
                                   ("eye", -1, (2, 2), (12, 12), (12, 12), (0, 0), 0),
                                   ("smile", -1, (40, 24), (12, 12), (12, 12), (0, 0), 0)])]
    mod = Image.new("RGBA", (64, 64))
    mod.paste(RED, (0, 0, 12, 40))
    mod.paste(RED, (0, 28, 40, 40))
    mod.paste((0, 255, 0, 255), (20, 4, 32, 16))
    official = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    official.paste(BLUE, (40, 24, 52, 36))
    return remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new)), {"page": mod}, {"page": official}


def test_only_the_pixels_a_region_draws_move_and_are_written():
    old, new, mod, official = nested()
    hulls = {"part": [L_SHAPE], "eye": None}
    pages, report = remap.remap_pages(old, new, mod, official, fill=True, hulls=hulls, official_hulls={"smile": None})
    page = pages["page.png"]
    assert report["official"] == ["smile"] and report["moved"] == ["eye", "part"]
    assert page.getpixel((24, 40)) == RED and page.getpixel((50, 56)) == RED  # the L, where the new layout has it
    assert page.getpixel((8, 8)) == (0, 255, 0, 255)  # the eye, at its own new place
    assert page.getpixel((46, 30)) == BLUE  # the new expression in the corner: neither the old eye nor erased
    assert page.getpixel((56, 26)) == (0, 0, 0, 0)  # the rest of the corner stays empty
    # as whole rectangles the old eye comes along with the L and lands on the new expression
    unmasked, _ = remap.remap_pages(old, new, mod, official, fill=True)
    assert unmasked["page.png"].getpixel((46, 30)) == (0, 255, 0, 255)


def test_an_official_region_is_filled_inside_its_outline_only():
    old, new, mod, official = nested()
    old.regions = {key: region for key, region in old.regions.items() if region.name != "part"}
    # the official texture holds a neighbour's pixels (white) in the corner of the L's rectangle
    pages, report = remap.remap_pages(old, new, mod, official, fill=True, official_hulls={"part": [L_SHAPE]})
    page = pages["page.png"]
    assert report["official"] == ["part", "smile"]
    assert page.getpixel((24, 40)) == (255, 255, 255, 255)  # the official L
    assert page.getpixel((56, 26)) == (0, 0, 0, 0)  # not the rest of its rectangle
    assert page.getpixel((46, 30)) == BLUE


def test_outlines_that_are_not_outlines_move_the_whole_rectangle():
    old, new, mod, official = nested()
    for hulls in ({"part": "junk"}, {"part": [[0.0, 0.0, 1.0]]}, {"part": [["a"] * 6]}, {"part": []}, None,
                  {"part": [[float("nan")] * 6]}, {"part": [[float("inf"), 0, 1, 0, 1, 1]]}, {"part": [[1e30] * 6]}):
        pages, _ = remap.remap_pages(old, new, mod, official, fill=True, hulls=hulls)
        assert pages["page.png"].getpixel((46, 30)) == (0, 255, 0, 255)

def test_a_required_region_the_mod_lacks_takes_the_official_pixels(repacked):
    old, new, art, official_art, _ = repacked
    source, target = remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new))
    source.regions = {key: region for key, region in source.regions.items() if region.name != "added"}
    official = pack(new, official_art)
    pages, report = remap.remap_pages(source, target, pack(old, art), official, {"body", "added"}, fill=True)
    assert report["official"] == ["added"] and report["added"] == []
    assert pages["extra.png"].crop((8, 8, 12, 11)).tobytes() == official["extra"].crop((8, 8, 12, 11)).tobytes()
    # the mod's own regions still move
    assert "body" in report["moved"]
    assert pages["current.png"].crop((20, 1, 23, 6)).tobytes() != official["current"].crop((20, 1, 23, 6)).tobytes()


def test_a_mod_with_no_region_to_move_is_still_refused(repacked):
    old, new, art, official_art, _ = repacked
    source, target = remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new))
    source.regions = {key: region for key, region in source.regions.items() if region.name == "removed"}
    with pytest.raises(remap.RemapError, match="atlas_remap_regions_missing"):
        remap.remap_pages(source, target, pack(old, art), pack(new, official_art), fill=True)


def test_rig_gains_name_what_the_official_rig_has_and_the_mod_lacks():
    old, new = binary_skeleton([animation("Idle_01")]), binary_skeleton([animation("Idle_01"), animation("00")])
    atlases = {"Test.atlas": atlas_text(OLD).encode()}
    official = {"Test.atlas": atlas_text(NEW).encode()}
    gains = remap.rig_gains(atlases, {"Test.skel": old}, official, {"Test.skel": new})
    assert gains == {"animations": {"Test.skel": ["00"]}, "regions": {"Test.atlas": ["smile"]}}
    assert remap.rig_gains(official, {"Test.skel": new}, official, {"Test.skel": new}) == {"animations": {}, "regions": {}}
    # nothing of the mod's own, a skeleton this build cannot walk: nothing is told
    assert remap.rig_gains({}, {}, official, {"Test.skel": new}) == {"animations": {}, "regions": {}}
    assert remap.rig_gains({}, {"Test.skel": b"3.8.99 old"}, {}, {"Test.skel": new})["animations"] == {}

    def rerigged(gains, skeletons, textures=("page",)):
        return remap.rerigged(gains, atlases, skeletons, official, {"Test.skel": new}, textures)

    assert rerigged(gains, {"Test.skel": old}) == {"Test.atlas": "Test.skel"}
    assert rerigged(gains, {"Test.skel": old}, textures=()) == {}  # no page of the mod's to move
    # a mod with its own complete skeleton and a smaller atlas of its own is whole as it is
    only_regions = {"animations": {}, "regions": gains["regions"]}
    assert rerigged(only_regions, {"Test.skel": new}) == {}
    assert rerigged(only_regions, {}) == {"Test.atlas": "Test.skel"}  # the official skeleton draws the new region


def test_an_earlier_official_skeleton_is_told_from_a_custom_one():
    old, new = binary_skeleton([animation("Idle_01")]), binary_skeleton([animation("Idle_01"), animation("00")])
    assert remap.same_rig(old, new)
    assert not remap.same_rig(binary_skeleton([animation("Idle_01"), animation("Dance")]), new)
    assert not remap.same_rig(b"3.8.99 old", new)


def test_the_fingerprint_keeps_the_layout_texture_only_files_follow():
    part = {"name": "a", "atlases": {"test.atlas": fingerprint.atlas(atlas_text(OLD).encode())}, "skeletons": {},
            "textures": {"page": [16, 16]}}
    texture_only = {"atlases": {}, "skeletons": {}, "textures": {"page": [16, 16]}}
    known = fingerprint.painted([part], texture_only)
    assert known == {"test.atlas": {**part["atlases"]["test.atlas"], "hulls": None}}
    drawn = fingerprint.painted([part], texture_only, {"Folder/Test.skel": mesh_skeleton()})
    assert drawn["test.atlas"]["hulls"] == {"part": [L_SHAPE], "whole": None}
    assert fingerprint.painted([part], texture_only, {"Test.skel": b"not a skeleton"})["test.atlas"]["hulls"] is None
    assert fingerprint.painted([part], {**texture_only, "atlases": {"test.atlas": {}}}) == {}  # carries its own
    assert fingerprint.painted([part], {**texture_only, "textures": {"other": [4, 4]}}) == {}
    data = fingerprint.document("import", [part], complete=True, painted=known)
    assert fingerprint.changed_layouts(data, {"Test.atlas": atlas_text(OLD).encode()}) == {}
    assert fingerprint.changed_layouts(data, {"Test.atlas": atlas_text(NEW).encode()}) == {"Test.atlas": known["test.atlas"]}
    # a fingerprint that does not say, and none at all
    assert fingerprint.changed_layouts(fingerprint.document("import", [part], complete=True),
                                       {"Test.atlas": atlas_text(NEW).encode()}) == {}
    assert fingerprint.changed_layouts(None, {"Test.atlas": atlas_text(NEW).encode()}) == {}


# --- the library, on real bundles ---

@pytest.fixture
def real(setup_library, monkeypatch):
    monkeypatch.setattr("bmpt.library.integrity.inspect", inspect_bundle)
    env = setup_library
    env.original.unlink()
    return env


def textures(colour, pages=OLD):
    """One page: ``colour`` in every region, transparent around them."""
    image = Image.new("RGBA", pages[0][1])
    for name, _index, xy, size, *_rest in pages[0][2]:
        image.paste(colour[name] if isinstance(colour, dict) else colour, (*xy, xy[0] + size[0], xy[1] + size[1]))
    return {"page": image}


def test_a_mod_with_its_own_old_rig_moves_onto_the_official_one(real):
    env = real
    current = _official_name(write_bundle(env.original, {"test.atlas": atlas_text(NEW)},
                                          textures({"part": BLUE, "smile": (0, 255, 0, 255)}, NEW),
                                          skeleton(["00", "01"], ("part", "smile"))))
    source = write_bundle(env.tmp / "upload" / current.name.replace("2026-01-01", "2025-01-01"),
                          {"test.atlas": atlas_text(OLD)}, textures(RED), skeleton(["00"]))
    entry, = env.library.add([source])
    report = env.library.compatibility(entry.id)
    assert report["rig"] == {"layout": [], "animations": {"test.skel": ["01"]}, "regions": {"test.atlas": ["smile"]}}
    assert report["remappable"] and not report["misfit"]
    preview = env.library.remap(entry.id)
    assert preview["report"]["official"] == ["test.atlas - smile"] and preview["report"]["moved"] == ["test.atlas - part"]
    assert preview["report"]["warnings"] == [] and not preview["report"]["partial"]
    result = env.library.accept_remap(preview["preview"])
    built = remap.read_inputs(sorted(result.mod.file.parent.glob("*.bundle")))
    official = remap.read_inputs([current])
    assert built.skeletons == official.skeletons and built.atlases == official.atlases
    page = built.textures["page"]
    assert page.getpixel((9, 9)) == RED  # the mod's part, where the official atlas has it now
    assert page.getpixel((2, 2)) == (0, 255, 0, 255)  # the new expression, from the official texture
    assert page.getpixel((14, 2)) == (0, 0, 0, 0)
    assert entry.file.read_bytes() == source.read_bytes()  # the mod itself is as it was
    assert not env.library.compatibility(result.mod.id)["remappable"]


def test_a_custom_skeleton_that_gives_way_is_reported(real):
    env = real
    current = _official_name(write_bundle(env.original, {"test.atlas": atlas_text(NEW)},
                                          textures(BLUE, NEW), skeleton(["00", "01"], ("part", "smile"))))
    source = write_bundle(env.tmp / "upload" / current.name.replace("2026-01-01", "2025-01-01"),
                          {"test.atlas": atlas_text(OLD)}, textures(RED), skeleton(["00", "Dance"]))
    entry, = env.library.add([source])
    report = env.library.remap(entry.id)["report"]
    assert report["warnings"] == [{"key": "rig", "name": "test.skel"}] and report["partial"]


def test_a_mod_with_its_own_complete_rig_is_left_alone(real):
    env = real
    current = _official_name(write_bundle(env.original, {"test.atlas": atlas_text(NEW)},
                                          textures(BLUE, NEW), skeleton(["00"], ("part", "smile"))))
    # its own layout, every official animation: drawn whole by its own files
    source = write_bundle(env.tmp / "upload" / current.name.replace("2026-01-01", "2025-01-01"),
                          {"test.atlas": atlas_text(OLD)}, textures(RED), skeleton(["00", "Dance"]))
    entry, = env.library.add([source])
    report = env.library.compatibility(entry.id)
    assert report["rig"] == {"layout": [], "animations": {}, "regions": {}} and not report["remappable"]
    assert env.library.remap(entry.id) == {"unchanged": True}


def test_a_texture_drawn_on_the_layout_before_a_repack_is_found_and_moved(real):
    env = real
    rig = skeleton(["00"])
    current = split_official(_official_name(write_bundle(env.original, {"test.atlas": atlas_text(OLD)},
                                                         textures(BLUE), rig)))
    text_part = next(path for path in env.game_dir.glob("*.bundle") if "textassets" in path.name)
    # a whole-file copy of a file made for the current part: the library knows the layout its texture follows
    source = write_bundle(env.tmp / "upload" / current.name, {}, textures(RED), skeleton=None)
    entry, = env.library.add([source], direct=True)
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert stored["painted"]["test.atlas"]["text"] == atlas_text(OLD)
    assert stored["painted"]["test.atlas"]["hulls"] == {"part": None}  # drawn whole by the official skeleton
    report = env.library.compatibility(entry.id)
    assert report["rig"]["layout"] == [] and not report["remappable"]
    # the game repacks the atlas on a page of the same size and adds an expression
    text_part.unlink()
    current.unlink()
    updated = split_official(_official_name(write_bundle(
        env.original, {"test.atlas": atlas_text(NEW)}, textures({"part": BLUE, "smile": (0, 255, 0, 255)}, NEW),
        skeleton(["00", "01"], ("part", "smile")))))
    library = reload_library(env)
    report = library.compatibility(entry.id)
    assert report["rig"]["layout"] == ["test.atlas"] and report["remappable"] and not report["misfit"]
    preview = library.remap(entry.id)
    assert preview["report"]["moved"] == ["test.atlas - part"]
    assert preview["report"]["official"] == ["test.atlas - smile"]
    result = library.accept_remap(preview["preview"])
    page = remap.read_inputs(sorted(result.mod.file.parent.glob("*.bundle"))).textures["page"]
    assert page.getpixel((9, 9)) == RED and page.getpixel((2, 2)) == (0, 255, 0, 255)
    assert updated.is_file() and not library.compatibility(result.mod.id)["remappable"]


def test_files_the_library_rebuilt_do_not_claim_a_layout(real):
    env = real
    current = split_official(_official_name(write_bundle(env.original, {"test.atlas": atlas_text(OLD)},
                                                         textures(BLUE), skeleton(["00"]))))
    source = write_bundle(env.tmp / "upload" / current.name, {}, textures(RED), skeleton=None)
    entry, = env.library.add([source])  # not a whole-file copy: nothing says which release drew the texture
    assert "painted" not in json.loads(sidecar(env, entry.id).read_text())


# --- update ---

def test_update_tells_what_the_official_rig_gained_and_keeps_the_layout_of_the_textures(spine, monkeypatch):
    env = spine
    env.assets[header_bundle(b"official")] = ([("page.png", (16, 16), [("body", -1, (1, 1), (4, 4), (4, 4), (0, 0), 0)])],
                                             ("Idle_01", "00"))
    env.assets[header_bundle(b"old mod!")] = (env.assets[header_bundle(b"official")][0], ("Idle_01",))
    entry, = env.library.add([make_mod(env.original, env.tmp / "input", payload=b"old mod!")])
    env.assets[header_bundle(b"updated!")] = env.assets[header_bundle(b"old mod!")]

    def update(sources, targets, output, *args, **kwargs):
        result = output / targets[0].name
        result.write_bytes(header_bundle(b"updated!"))
        return ModUpdateResult(True, "updated", [FilePair(result, targets[0])])

    monkeypatch.setattr(core, "process_mod_update", update)
    result = env.library.update(entry.id)
    assert result.rig == {"animations": {"Test.skel": ["00"]}}
    assert result.to_json()["rig"] == {"animations": {"Test.skel": ["00"]}}
    # an update that changes nothing says it too
    monkeypatch.setattr(core, "process_mod_update",
                        lambda *args, **kwargs: ModUpdateResult(True, "all_targets_unchanged", []))
    again = env.library.update(entry.id)
    assert again.unchanged and again.rig == {"animations": {"Test.skel": ["00"]}}


def test_update_carries_the_layout_texture_only_files_follow(spine, monkeypatch):
    env = spine
    # neither the mod nor its rebuild carries an atlas: only the official part does
    entry, = env.library.add([make_mod(env.original, env.tmp / "input", payload=b"texture!")])
    record = env.library._record(entry.id)
    data = env.library._built_against({entry.file.name: entry.file}, env.library.current_pin(), "import", painted=True)
    assert list(data["painted"]) == ["test.atlas"]
    assert env.library._write_built_against(record, data)

    def update(sources, targets, output, *args, **kwargs):
        result = output / targets[0].name
        result.write_bytes(header_bundle(b"rebuilt!"))
        return ModUpdateResult(True, "updated", [FilePair(result, targets[0])])

    monkeypatch.setattr(core, "process_mod_update", update)
    assert env.library.update(entry.id).rig == {}
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert stored["event"] == "update" and stored["painted"] == data["painted"]


# --- art exported again on another canvas ---

def region(size, original, offset=(0, 0)):
    return remap.Region("part", -1, "page.png", (0, 0), size, original, offset, 0)


@pytest.mark.parametrize("source, target, expected", [
    # the same image, trimmed the same: scaled or not
    (region((100, 80), (120, 100), (10, 10)), region((200, 160), (240, 200), (20, 20)), ("frame", False)),
    # a margin of 12 pixels added around the canvas: the art keeps its size and sits elsewhere in the image
    (region((180, 150), (232, 205), (26, 27)), region((180, 150), (244, 217), (38, 39)), ("trim", False)),
    # exported larger on a canvas of other proportions
    (region((90, 70), (114, 91), (12, 10)), region((205, 160), (259, 211), (20, 30)), ("trim", False)),
    # the same canvas, trimmed anew: the trimmed rectangle of a few pixels grows to the whole image
    (region((4, 4), (6, 6), (1, 1)), region((3, 3), (6, 6), (2, 1)), ("frame", False)),
    # the same canvas at another scale, cut into another piece
    (region((1729, 1257), (1729, 1257)), region((1706, 764), (2075, 1508), (0, 744)), ("frame", False)),
    # ... into a piece the old one held only half of
    (region((1000, 1000), (1000, 2000), (0, 1000)), region((1000, 1000), (1000, 2000), (0, 500)), ("frame", True)),
    # another canvas and nearly the same share of it, in another shape: the art changed a little
    (region((203, 92), (203, 92)), region((206, 102), (217, 108), (5, 3)), ("trim", True)),
    # another canvas and another piece of it: nothing says where the pixels belong
    (region((1056, 1339), (1136, 2729), (40, 1390)), region((1125, 1687), (1128, 2686), (0, 999)), ("frame", True)),
])
def test_how_a_region_is_laid_on_its_new_rectangle(source, target, expected):
    assert remap._placement(source, target) == expected


def test_art_on_a_canvas_with_another_margin_keeps_its_place_on_the_art():
    # the old canvas is the art alone; the new one adds 2 pixels on every side, at the same scale
    old = [("page.png", (64, 64), [("part", -1, (2, 2), (40, 32), (40, 32), (0, 0), 0)])]
    new = [("page.png", (64, 64), [("part", -1, (20, 28), (40, 32), (44, 36), (2, 2), 0)])]
    mod = Image.new("RGBA", (64, 64))
    mod.paste(RED, (2, 2, 42, 34))
    mod.putpixel((2, 2), BLUE)  # the art's top left corner
    pages, report = remap.remap_pages(remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new)),
                                      {"page": mod}, {"page": Image.new("RGBA", (64, 64))})
    page = pages["page.png"]
    assert report["changed"] == [] and report["moved"] == ["part"]
    assert page.getpixel((20, 28)) == BLUE and page.getpixel((59, 59)) == RED  # the whole art, corner on corner
    assert page.crop((20, 28, 60, 60)).tobytes() == mod.crop((2, 2, 42, 34)).tobytes()


def test_art_of_another_shape_keeps_the_official_pixels_and_is_reported():
    old = [("page.png", (64, 64), [("part", -1, (2, 2), (30, 20), (40, 40), (5, 10), 0)])]
    new = [("page.png", (64, 64), [("part", -1, (30, 30), (20, 30), (50, 35), (10, 5), 0)])]
    mod = Image.new("RGBA", (64, 64))
    mod.paste(RED, (2, 2, 32, 22))
    atlases = remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new))
    official = {"page": Image.new("RGBA", (64, 64), BLUE)}
    pages, report = remap.remap_pages(*atlases, {"page": mod}, official, fill=True)
    assert report["changed"] == ["part"] and report["moved"] == []
    assert set(pages["page.png"].crop((30, 30, 50, 60)).getdata()) == {BLUE}  # not the mod's pixels in the wrong place
    # without the official pixels to fall back on, it is placed all the same and reported
    pages, report = remap.remap_pages(*atlases, {"page": mod}, official)
    assert report["changed"] == ["part"] and RED in set(pages["page.png"].crop((30, 30, 50, 60)).getdata())


# --- an image the game cut into other pieces ---

def cut_two_ways():
    """One 20 x 40 picture (top half red, bottom half green, a blue dot at its very top). The old atlas cuts it in a
    top and a bottom piece; the new one moves the seam down and swaps the pieces' names."""
    old = [("page.png", (64, 64), [("halo", -1, (52, 2), (10, 10), (12, 12), (1, 1), 0),      # a picture of its own
                                   ("body", -1, (2, 2), (20, 20), (20, 40), (0, 20), 0),      # the top half
                                   ("body_c", -1, (30, 2), (20, 20), (20, 40), (0, 0), 0)])]  # the bottom half
    new = [("page.png", (64, 64), [("halo", -1, (40, 44), (10, 10), (12, 12), (1, 1), 0),
                                   ("body_c", -1, (2, 30), (20, 30), (20, 40), (0, 10), 0),   # the top three quarters
                                   ("body", -1, (40, 30), (20, 10), (20, 40), (0, 0), 0)])]   # the bottom quarter
    mod = Image.new("RGBA", (64, 64))
    mod.paste((255, 255, 0, 255), (52, 2, 62, 12))
    mod.paste(RED, (2, 2, 22, 22))
    mod.putpixel((2, 2), BLUE)
    mod.paste((0, 255, 0, 255), (30, 2, 50, 22))
    return remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new)), {"page": mod}


def test_pieces_of_an_image_cut_another_way_are_cut_from_the_image_put_together():
    old, new, mod = cut_two_ways()
    pages, report = remap.remap_pages(old, new, mod, {"page": Image.new("RGBA", (64, 64), (255, 255, 255, 255))},
                                      fill=True)
    page = pages["page.png"]
    assert report["moved"] == ["body", "body_c", "halo"] and report["changed"] == [] and report["official"] == []
    assert report["dropped"] == []
    assert page.getpixel((45, 49)) == (255, 255, 0, 255)  # a region moved the ordinary way, before the pieces
    # the new top piece: the picture's top 30 rows, red then green from row 20 on
    assert page.getpixel((2, 30)) == BLUE
    assert page.getpixel((10, 40)) == RED and page.getpixel((10, 49)) == RED
    assert page.getpixel((10, 50)) == (0, 255, 0, 255) and page.getpixel((10, 59)) == (0, 255, 0, 255)
    # the new bottom piece: its last 10 rows, all green, though its namesake held the red half
    assert set(page.crop((40, 30, 60, 40)).getdata()) == {(0, 255, 0, 255)}


def test_pictures_of_one_size_are_not_pieces_of_one_image():
    # two expressions of one size lie on each other: each keeps to its namesake
    old = [("page.png", (64, 64), [("01", -1, (2, 2), (20, 16), (20, 16), (0, 0), 0),
                                   ("02", -1, (30, 2), (20, 16), (20, 16), (0, 0), 0)])]
    new = [("page.png", (64, 64), [("02", -1, (2, 30), (20, 16), (20, 16), (0, 0), 0),
                                   ("01", -1, (30, 30), (20, 16), (20, 16), (0, 0), 0),
                                   ("03", -1, (2, 48), (20, 16), (20, 16), (0, 0), 0)])]
    left, right = remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new))
    assert remap._recut(left, right, remap._pair_regions(left, right)) == {}
    mod = Image.new("RGBA", (64, 64))
    mod.paste(RED, (2, 2, 22, 18))
    mod.paste(BLUE, (30, 2, 50, 18))
    pages, report = remap.remap_pages(left, right, {"page": mod}, {"page": Image.new("RGBA", (64, 64), (9, 9, 9, 255))},
                                      fill=True)
    assert pages["page.png"].getpixel((40, 38)) == RED and pages["page.png"].getpixel((10, 38)) == BLUE
    assert report["official"] == ["03"] and pages["page.png"].getpixel((10, 56)) == (9, 9, 9, 255)


def test_a_piece_that_kept_its_cut_moves_as_before():
    old = [("page.png", (64, 64), [("body", -1, (2, 2), (20, 20), (20, 40), (0, 20), 0)])]
    new = [("page.png", (64, 64), [("body", -1, (30, 30), (20, 20), (20, 40), (0, 20), 90)])]
    left, right = remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new))
    assert remap._recut(left, right, remap._pair_regions(left, right)) == {}


# --- written inside the outline, layers in their draw order ---

def test_a_region_moved_whole_does_not_erase_the_neighbour_packed_into_its_rectangle():
    old, new, mod, official = nested()
    # the old skeleton draws the L as a whole rectangle (no outline to cut its pixels by); the new one outlines it,
    # and packs the new expression into its empty corner
    pages, report = remap.remap_pages(old, new, mod, official, fill=True, hulls={"part": None, "eye": None},
                                      official_hulls={"part": [L_SHAPE], "eye": None, "smile": None})
    page = pages["page.png"]
    assert page.getpixel((24, 40)) == RED and page.getpixel((8, 8)) == (0, 255, 0, 255)
    assert page.getpixel((46, 30)) == BLUE  # the official expression is still there
    # without an outline of the new L its rectangle is written whole, as before
    whole, _ = remap.remap_pages(old, new, mod, official, fill=True, hulls={"part": None, "eye": None},
                                 official_hulls={"eye": None, "smile": None})
    assert whole["page.png"].getpixel((46, 30)) == (0, 255, 0, 255)


def layers():
    """One 20 x 40 picture drawn as a red base over all of it and a small green layer on its upper half; the new
    atlas cuts the same picture into a top and a bottom half."""
    old = [("page.png", (64, 64), [("base", -1, (2, 2), (20, 40), (20, 40), (0, 0), 0),
                                   ("base_2", -1, (30, 2), (10, 10), (20, 40), (5, 25), 0)])]  # rows 5..15, columns 5..15
    new = [("page.png", (64, 64), [("base", -1, (2, 44), (20, 20), (20, 40), (0, 20), 90),    # the top half
                                   ("base_02", -1, (30, 20), (20, 20), (20, 40), (0, 0), 0)])]  # the bottom half
    mod = Image.new("RGBA", (64, 64))
    mod.paste(RED, (2, 2, 22, 42))
    mod.paste((0, 255, 0, 255), (30, 2, 40, 12))
    return remap.parse_atlas(atlas_text(old)), remap.parse_atlas(atlas_text(new)), {"page": mod}


@pytest.mark.parametrize("order, top", [(["base", "base_2"], (0, 255, 0, 255)), (["base_2", "base"], RED)])
def test_layers_of_an_image_are_laid_in_the_draw_order_of_their_skeleton(order, top):
    old, new, mod = layers()
    # one lies wholly on the other: parts of one image all the same, not two pictures of one size
    assert set(remap._recut(old, new, remap._pair_regions(old, new))) == set(new.regions)
    pages, report = remap.remap_pages(old, new, mod, {"page": Image.new("RGBA", (64, 64), (9, 9, 9, 255))}, fill=True,
                                      hulls={name: None for name in order})
    assert report["moved"] == ["base", "base_02"] and report["official"] == [] and report["dropped"] == []
    upper = remap._rotate(pages["page.png"].crop((2, 44, 22, 64)), -90)  # the new top half, upright
    assert upper.getpixel((10, 10)) == top and upper.getpixel((1, 1)) == RED and upper.getpixel((10, 18)) == RED
    assert set(pages["page.png"].crop((30, 20, 50, 40)).getdata()) == {RED}  # the bottom half holds no layer


def test_a_part_the_old_ones_do_not_hold_is_not_cut_from_them():
    old, new, mod = layers()
    old.regions = {key: region for key, region in old.regions.items() if region.name != "base"}
    # only the small layer is left of the image: neither half of it can be cut from that
    assert remap._recut(old, new, remap._pair_regions(old, new)) == {}


def test_hulls_come_in_the_draw_order_of_the_slots():
    data = json.loads(mesh_skeleton())
    data["slots"] = [{"name": "front", "bone": "root"}, {"name": "slot", "bone": "root"}, {"name": "back", "bone": "root"}]
    data["skins"][0]["attachments"] = {"back": {"z": {}}, "slot": data["skins"][0]["attachments"]["slot"], "front": {"a": {}}}
    assert list(spine_names.hulls(json.dumps(data).encode())) == ["a", "part", "whole", "z"]
