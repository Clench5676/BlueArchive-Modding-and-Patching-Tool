"""A game update that repacks a portrait's atlas and adds expressions under existing mods: what the library detects
(layout, animations, regions), when it offers the remap, and what the remap takes from the official files."""

from __future__ import annotations

import base64
import json

import pytest
from PIL import Image

from ba_modding_toolkit.models import FilePair
from bmpt.library.rebuild import RebuildResult
from bmpt import atlas_remap as remap
from bmpt.library import fingerprint, service
from bmpt.library.integrity import inspect as inspect_bundle

from rebake_geometry import geometry as rig_geometry
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
    assert known == {"test.atlas": part["atlases"]["test.atlas"]}  # no skeleton to keep
    drawn = fingerprint.painted([part], texture_only, {"Folder/Test.skel": b"skeleton bytes"})
    assert drawn["test.atlas"]["skeleton"] == base64.b64encode(b"skeleton bytes").decode()
    # a record of the branch before keeps no outlines
    assert "hulls" not in fingerprint.painted([{**part, "atlases": {"test.atlas": {**part["atlases"]["test.atlas"],
                                                                                  "hulls": None}}}], texture_only)["test.atlas"]
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
    # The synthetic rigs (rebake_geometry) draw every region at a place of its own name, an expression beside the
    # body rather than over it: the rest pictures of rigs that gained one do not overlap as real ones do.
    monkeypatch.setattr("bmpt.rebake.REDRAWN_BELOW", 0)
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
    preview = env.library.remap(entry.id, geometry=rig_geometry(env.library, entry.id))
    assert preview["report"]["official"] == ["test.atlas - smile"] and preview["report"]["moved"] == ["test.atlas - part"]
    assert preview["report"]["warnings"] == [] and preview["report"]["partial"]  # the expression is the official one
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
    report = env.library.remap(entry.id, geometry=rig_geometry(env.library, entry.id))["report"]
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
    assert env.library.remap(entry.id, geometry=rig_geometry(env.library, entry.id)) == {"unchanged": True}
    from bmpt.errors import ServiceError
    with pytest.raises(ServiceError, match="bad_params"):  # a portrait is baked from the page's geometry
        env.library.remap(entry.id)


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
    assert stored["painted"]["test.atlas"]["skeleton"] == base64.b64encode(rig).decode()  # the rig that drew it
    report = env.library.compatibility(entry.id)
    assert report["rig"]["layout"] == [] and not report["remappable"]
    # the game repacks the atlas on a page of the same size and adds an expression
    text_part.unlink()
    current.unlink()
    updated = split_official(_official_name(write_bundle(
        env.original, {"test.atlas": atlas_text(NEW)}, textures({"part": BLUE, "smile": (0, 255, 0, 255)}, NEW),
        skeleton(["00", "01"], ("part", "smile")))))
    # a record without the rig (written before it was kept) tells the repack but gives nothing to bake from
    record = json.loads(sidecar(env, entry.id).read_text())
    bare = json.loads(json.dumps(record))
    del bare["painted"]["test.atlas"]["skeleton"]
    sidecar(env, entry.id).write_text(json.dumps(bare))
    library = reload_library(env)
    report = library.compatibility(entry.id)
    assert report["rig"]["layout"] == ["test.atlas"] and not report["remappable"] and report["source"]["atlases"] == {}
    sidecar(env, entry.id).write_text(json.dumps(record))
    library = reload_library(env)
    report = library.compatibility(entry.id)
    assert report["rig"]["layout"] == ["test.atlas"] and report["remappable"] and not report["misfit"]
    assert report["source"]["atlases"] == {"test.atlas": atlas_text(OLD)}
    assert report["source"]["skeletons"]["test.skel"]["data"] == base64.b64encode(rig).decode()
    # the two rest pictures overlap as far as the rigs draw the same: here the body, not the new expression
    gate = library.redrawn(entry.id, rig_geometry(library, entry.id))
    assert not gate["redrawn"] and 0.4 < gate["overlap"] < 0.6
    preview = library.remap(entry.id, geometry=rig_geometry(library, entry.id))
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
        return RebuildResult(True, "updated", [FilePair(result, targets[0])])

    monkeypatch.setattr(service, "rebuild", update)
    result = env.library.update(entry.id)
    assert result.rig == {"animations": {"Test.skel": ["00"]}}
    assert result.to_json()["rig"] == {"animations": {"Test.skel": ["00"]}}
    # an update that changes nothing says it too
    monkeypatch.setattr(service, "rebuild", lambda *args, **kwargs: RebuildResult(True, "", [], all_unchanged=True))
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
        return RebuildResult(True, "updated", [FilePair(result, targets[0])])

    monkeypatch.setattr(service, "rebuild", update)
    assert env.library.update(entry.id).rig == {}
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert stored["event"] == "update" and stored["painted"] == data["painted"]
