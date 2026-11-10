"""The fingerprint of the official parts a mod was built against: the skeleton name reader, the fingerprint itself
and the library recording it at import, update and remap and making it up from kept originals."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from ba_modding_toolkit import core
from ba_modding_toolkit.models import FilePair, ModUpdateResult
from bmpt import spine_names
from bmpt.library import fingerprint, service
from bmpt.library.integrity import Contents

from test_atlas_remap import atlas_text, repair_review  # noqa: F401 - fixture
from test_library import game_bundle, header_bundle, kept_copy, make_mod, reload_library, setup_library  # noqa: F401

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


# --- skeleton names ---

def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if not value:
            return bytes(out + bytes([byte]))
        out.append(byte | 0x80)


def text(value: str | None) -> bytes:
    if value is None:
        return varint(0)
    data = value.encode()
    return varint(len(data) + 1) + data


def floats(*values: float) -> bytes:
    return struct.pack(f">{len(values)}f", *values)


def animation(name: str, *, bones: bytes = varint(0), events: bytes = varint(0)) -> bytes:
    # timeline count, slots, bones, IK, transform, path, physics, attachments, draw order, events
    return text(name) + varint(1) + varint(0) + bones + varint(0) * 5 + varint(0) + events


def binary_skeleton(animations: list[bytes], version: str = "4.2.33", first: int = 1) -> bytes:
    head = bytes([first]) + bytes(7) + text(version) + floats(0, 0, 10, 10, 100) + b"\x00"
    strings = varint(1) + text("body")
    bone = floats(*[0] * 8) + b"\x00\x00"
    bones = varint(2) + text("root") + bone + text("arm") + varint(0) + bone
    slots = varint(1) + text("skin") + varint(1) + bytes(4) + b"\xff\xff\xff\xff" + varint(1) + varint(0)
    constraints = varint(0) * 4
    # default skin: one slot with one region attachment (no flags, six floats)
    skin = varint(1) + varint(0) + varint(1) + varint(1) + b"\x00" + floats(*[0] * 6)
    events = varint(1) + text("sound") + varint(0) + floats(0) + text(None) + text("clip.ogg") + floats(1, 0)
    return (head + strings + bones + slots + constraints + skin + varint(0) + events
            + varint(len(animations)) + b"".join(animations))


def rotate_timeline() -> bytes:
    """One bone, one rotate timeline of three frames: a Bezier segment, then a stepped one."""
    frames = floats(0, 0) + floats(1, 5) + b"\x02" + floats(0, 0, 1, 1) + floats(2, 9) + b"\x01"
    return varint(1) + varint(1) + varint(1) + b"\x00" + varint(3) + varint(1) + frames


def event_timeline() -> bytes:
    """One key of the event with audio: its volume and balance follow."""
    return varint(1) + floats(0.5) + varint(0) + varint(0) + floats(0) + text(None) + floats(1, 0)


def test_binary_skeleton_names_are_read_to_the_last_byte():
    data = binary_skeleton([animation("Idle_01", bones=rotate_timeline()),
                            animation("00", events=event_timeline()), animation("Eye_Close_01")])
    names = spine_names.read(data)
    assert names.version == "4.2.33"
    assert names.bones == [("root", ""), ("arm", "root")]
    assert names.slots == [("skin", "arm")]
    assert names.skins == ["default"]
    assert names.animations == ["Idle_01", "00", "Eye_Close_01"]


def test_a_binary_skeleton_whose_hash_starts_like_json_is_read_as_binary():
    data = binary_skeleton([animation("Idle_01")], first=ord("{"))
    assert spine_names.read(data).animations == ["Idle_01"]


@pytest.mark.parametrize("damage", ["cut", "longer", "version", "noise"])
def test_a_skeleton_that_does_not_fit_the_layout_is_refused(damage):
    data = binary_skeleton([animation("Idle_01", bones=rotate_timeline())])
    data = {"cut": data[:-3], "longer": data + b"\x00",
            "version": binary_skeleton([animation("Idle_01")], version="3.8.99"),
            "noise": bytes(range(256)) * 4}[damage]
    with pytest.raises(ValueError):
        spine_names.read(data)


def test_json_skeleton_names():
    data = json.dumps({"skeleton": {"spine": "4.2.33"}, "bones": [{"name": "root"}, {"name": "arm", "parent": "root"}],
                       "slots": [{"name": "skin", "bone": "arm"}], "skins": [{"name": "default"}],
                       "animations": {"Idle_01": {}, "00": {}}}).encode()
    names = spine_names.read(data)
    assert (names.version, names.bones, names.slots) == ("4.2.33", [("root", ""), ("arm", "root")], [("skin", "arm")])
    assert (names.skins, names.animations) == (["default"], ["Idle_01", "00"])
    # 3.x keeps its skins by name
    assert spine_names.read(b'{"skins": {"default": {}, "alt": {}}}').skins == ["default", "alt"]
    with pytest.raises(ValueError):
        spine_names.read(b'{"bones": ')


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
def test_official_skeletons_are_read_to_their_last_byte():
    from bmpt.library import integrity
    from ba_modding_toolkit.searching import get_search_dirs

    bundles = sorted(path for folder in get_search_dirs(GAME_DIR) if folder.is_dir()
                     for path in folder.glob("*-spine*-*.bundle"))
    assert bundles
    read = 0
    for path in bundles[::12]:
        for name, data in integrity.inspect(path, allow_unknown=True).skeletons.items():
            names = spine_names.read(data)  # ValueError names the file in the traceback
            assert names.bones and names.animations, (path.name, name)
            read += 1
    assert read > 20


# --- the fingerprint ---

OLD = [("page.png", (16, 16), [("body", -1, (1, 1), (4, 4), (4, 4), (0, 0), 0)])]
MOVED = [("page.png", (16, 16), [("body", -1, (8, 8), (4, 4), (4, 4), (0, 0), 0),
                                 ("smile", -1, (1, 1), (4, 4), (4, 4), (0, 0), 0)])]


def test_atlas_fingerprint_follows_the_layout_not_the_bytes():
    old = fingerprint.atlas(atlas_text(OLD).encode())
    assert old["regions"] == ["body"]
    assert fingerprint.atlas(atlas_text(OLD, modern=True).encode()) == old  # another syntax, the same layout
    moved = fingerprint.atlas(atlas_text(MOVED).encode())
    assert moved["layout"] != old["layout"] and moved["regions"] == ["body", "smile"]
    unreadable = fingerprint.atlas(b"\xff\xfe")
    assert unreadable == {"layout": hashlib.sha1(b"\xff\xfe").hexdigest(), "regions": None}


def test_skeleton_fingerprint_names_the_animations_and_hashes_the_rig():
    data = binary_skeleton([animation("Idle_01"), animation("00")])
    more = binary_skeleton([animation("Idle_01"), animation("00"), animation("01")])
    one, two = fingerprint.skeleton(data), fingerprint.skeleton(more)
    assert one["sha1"] == hashlib.sha1(data).hexdigest() and one["version"] == "4.2.33"
    assert one["animations"] == ["Idle_01", "00"] and two["animations"] == ["Idle_01", "00", "01"]
    assert one["rig"] == two["rig"] and one["sha1"] != two["sha1"]
    unknown = fingerprint.skeleton(binary_skeleton([animation("Idle_01")], version="3.8.99"))
    assert unknown["version"] == "3.8.99" and unknown["rig"] is None and unknown["animations"] is None


def part_of(atlas=OLD, animations=("Idle_01",), size=(16, 16), structure="s1"):
    return {"name": "part.bundle", "sha1": None, "structure": structure,
            "atlases": {"test.atlas": fingerprint.atlas(atlas_text(atlas).encode())},
            "skeletons": {"test.skel": fingerprint.skeleton(binary_skeleton([animation(a) for a in animations]))},
            "textures": {"page": list(size)}}


def test_diff_names_what_a_game_update_changed_under_a_mod():
    before = part_of()
    assert fingerprint.diff(before, part_of()) == {"structure": False, "atlases": {}, "skeletons": {}, "textures": {}}
    after = part_of(MOVED, ("Idle_01", "00"), (32, 16), "s2")
    after["atlases"]["extra.atlas"] = after["atlases"]["test.atlas"]
    assert fingerprint.diff(before, after) == {
        "structure": True,
        "atlases": {"extra.atlas": {"present": "after"},
                    "test.atlas": {"layout": True, "added": ["smile"], "removed": []}},
        "skeletons": {"test.skel": {"changed": True, "rig": False, "added": ["00"], "removed": []}},
        "textures": {"page": {"before": [16, 16], "after": [32, 16]}}}
    assert fingerprint.diff({**before, "structure": None}, before)["structure"] is None


def test_standing_compares_only_what_the_mod_carries():
    official = part_of()
    nothing = {"atlases": {}, "skeletons": {}, "textures": {}}
    assert fingerprint.standing(official, nothing) == fingerprint.ABSENT
    assert fingerprint.standing(official, {**nothing, "textures": {"page": [16, 16]}}) == fingerprint.SAME
    assert fingerprint.standing(official, {**nothing, "textures": {"page": [16, 8]}}) == fingerprint.DIFFERS
    assert fingerprint.standing(official, part_of()) == fingerprint.SAME
    assert fingerprint.standing(official, part_of(MOVED)) == fingerprint.DIFFERS
    assert fingerprint.standing(official, part_of(animations=("Idle_01", "00"))) == fingerprint.DIFFERS


def test_a_stored_fingerprint_is_only_read_for_the_files_it_describes(tmp_path):
    data = fingerprint.document("import", [part_of()], complete=True)
    fingerprint.write(tmp_path, data, {"a.bundle": "1" * 40})
    stored = fingerprint.read(tmp_path, {"a.bundle": "1" * 40})
    assert stored["parts"] == [part_of()] and stored["event"] == "import" and stored["complete"] is True
    assert fingerprint.read(tmp_path, {"a.bundle": "2" * 40}) is None  # the mod was rebuilt since
    assert fingerprint.read(tmp_path / "missing", {}) is None
    for damaged in ("{", "[]", json.dumps({**stored, "version": 99}), json.dumps({**stored, "parts": [{"name": 1}]})):
        (tmp_path / fingerprint.FILE_NAME).write_text(damaged)
        assert fingerprint.read(tmp_path, {"a.bundle": "1" * 40}) is None


# --- the library ---

@pytest.fixture
def spine(setup_library, monkeypatch):
    """Header-only bundles whose text assets are told by their payload: {bundle bytes: (atlas pages, animations)}."""
    env = setup_library
    env.assets = {header_bundle(b"official"): (OLD, ("Idle_01",)), header_bundle(b"modded!!"): (OLD, ("Idle_01",))}

    def inspect(path, **kwargs):
        contents = Contents(platforms={"StandaloneWindows64"}, files={"cab-test"})
        found = env.assets.get(Path(path).read_bytes())
        if found:
            contents.atlases["Test.atlas"] = atlas_text(found[0]).encode()
            contents.skeletons["Test.skel"] = binary_skeleton([animation(name) for name in found[1]])
        contents.textures["page"] = (16, 16)
        return contents

    monkeypatch.setattr("bmpt.library.integrity.inspect", inspect)
    return env


def sidecar(env, mod_id):
    return env.root / "mods" / mod_id / fingerprint.FILE_NAME


def test_import_records_the_official_parts_beside_the_mod(spine):
    env = spine
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert (stored["version"], stored["event"], stored["complete"]) == (1, "import", True)
    assert stored["files"] == {entry.file.name: hashlib.sha1(entry.file.read_bytes()).hexdigest()}
    part, = stored["parts"]
    assert part["name"] == env.original.name and part["mod"] == "same"
    assert part["sha1"] == hashlib.sha1(env.original.read_bytes()).hexdigest() and part["structure"]
    assert part["atlases"]["test.atlas"]["regions"] == ["body"]
    assert part["skeletons"]["test.skel"]["animations"] == ["Idle_01"]
    assert part["textures"] == {"page": [16, 16]}
    # no part of the mod: its files, its identity and a reload are as before
    assert env.library._record(entry.id)["files"] == [entry.file.name]
    library = reload_library(env)
    assert [mod.id for mod in library.mods()] == [entry.id]
    assert library.built_against(entry.id) == stored
    assert not library.add([make_mod(env.original, env.tmp / "again")])  # the same mod, not stored twice


def test_a_mod_of_another_layout_is_recorded_as_differing(spine):
    env = spine
    env.assets[header_bundle(b"old mod!")] = (MOVED, ("Idle_01",))
    entry, = env.library.add([make_mod(env.original, env.tmp / "input", payload=b"old mod!")])
    part, = env.library.built_against(entry.id)["parts"]
    assert part["name"] == env.original.name and part["mod"] == "differs"


def test_the_baseline_outlives_the_original_a_game_update_released(spine):
    env = spine
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.library.apply("test", f"mod:{entry.id}")
    before = env.library.built_against(entry.id)
    # a game update repacks the atlas and adds an expression; the kept original of the release before goes
    env.original.unlink()
    env.assets[header_bundle(b"updated official")] = (MOVED, ("Idle_01", "00"))
    game_bundle(env.game_dir, date="2026-02-01", payload=b"updated official")
    library = reload_library(env)
    assert library.prune() == [kept_copy(env, env.original)]
    assert library.built_against(entry.id) == before
    now = library.official_parts_now(entry.id)
    assert now["event"] == "now" and now["parts"][0]["mod"] == "differs"
    assert json.loads(sidecar(env, entry.id).read_text()) == before  # asking stores nothing
    changed = fingerprint.diff(before["parts"][0], now["parts"][0])
    assert changed["atlases"] == {"test.atlas": {"layout": True, "added": ["smile"], "removed": []}}
    assert changed["skeletons"]["test.skel"] == {"changed": True, "rig": False, "added": ["00"], "removed": []}


def test_a_mod_stored_without_a_fingerprint_gets_one_before_its_original_is_released(spine):
    env = spine
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.library.apply("test", f"mod:{entry.id}")
    sidecar(env, entry.id).unlink()  # a mod of a build before the fingerprint
    old_name = env.original.name
    env.original.unlink()
    env.assets[header_bundle(b"updated official")] = (MOVED, ("Idle_01", "00"))
    game_bundle(env.game_dir, date="2026-02-01", payload=b"updated official")
    library = reload_library(env)
    assert library.prune() == [kept_copy(env, env.original)]
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert (stored["event"], stored["complete"]) == ("backfill", False)
    part, = stored["parts"]
    assert part["name"] == old_name and part["mod"] == "same" and part["atlases"]["test.atlas"]["regions"] == ["body"]
    assert library.built_against(entry.id) == stored


def test_a_fingerprint_is_made_up_only_from_a_file_that_shows_it(spine):
    env = spine
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.assets[header_bundle(b"old mod!")] = (MOVED, ("Idle_01",))
    other, = env.library.add([make_mod(env.original, env.tmp / "other", payload=b"old mod!")])
    for mod in (entry, other):
        sidecar(env, mod.id).unlink()
    made = env.library.built_against(entry.id)  # the install's part carries what the mod carries
    assert made["event"] == "backfill" and [part["name"] for part in made["parts"]] == [env.original.name]
    assert sidecar(env, entry.id).is_file()
    assert env.library.built_against(other.id) is None and not sidecar(env, other.id).exists()


def test_update_records_the_parts_it_rebuilt_the_mod_on(spine, monkeypatch):
    env = spine
    env.assets[header_bundle(b"old mod!")] = (MOVED, ("Idle_01",))
    entry, = env.library.add([make_mod(env.original, env.tmp / "input", payload=b"old mod!")])
    assert env.library.built_against(entry.id)["parts"][0]["mod"] == "differs"
    env.assets[header_bundle(b"updated!")] = (OLD, ("Idle_01",))

    def update(sources, targets, output, *args, **kwargs):
        result = output / targets[0].name
        result.write_bytes(header_bundle(b"updated!"))
        return ModUpdateResult(True, "updated", [FilePair(result, targets[0])])

    monkeypatch.setattr(core, "process_mod_update", update)
    result = env.library.update(entry.id)
    stored = json.loads(sidecar(env, entry.id).read_text())
    assert stored["event"] == "update" and stored["parts"][0]["mod"] == "same"
    assert stored["files"] == {result.mod.file.name: hashlib.sha1(header_bundle(b"updated!")).hexdigest()}
    assert env.library.built_against(entry.id) == stored
    # an update that changes nothing gives a mod stored without a fingerprint its baseline
    sidecar(env, entry.id).unlink()
    assert env.library.update(entry.id).unchanged
    assert json.loads(sidecar(env, entry.id).read_text())["event"] == "update"


def test_a_fingerprint_that_cannot_be_made_never_fails_the_import(spine, monkeypatch):
    env = spine

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(service.fingerprint, "part", broken)
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    assert entry.file.is_file() and not sidecar(env, entry.id).exists()
    assert env.library.built_against(entry.id) is None


def test_remap_records_the_layout_it_rebuilt_the_mod_on(repair_review):
    env, entry, current, source = repair_review
    imported = env.library.built_against(entry.id)
    # the upload carries a page of the layout before: it differs from the part as it is now
    assert imported["event"] == "import"
    assert {part["mod"] for part in imported["parts"]} == {"differs", "absent"}
    result = env.library.accept_remap(env.library.remap(entry.id)["preview"])
    stored = env.library.built_against(result.mod.id)
    assert stored["event"] == "remap" and stored["complete"] is True
    assert {part["mod"] for part in stored["parts"]} == {"same"}
    assert sorted(part["name"] for part in stored["parts"]) == sorted(
        path.name for path in env.game_dir.glob("*.bundle"))
    assert env.library.built_against(entry.id) == imported
