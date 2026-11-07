"""Groups packed one asset per bundle (BL-101): their bundles' names differ only in the CRC, so each bundle's slot is
keyed by its CAB; slots an earlier build shared move to the files' own slots."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

from bmpt.library import ModLibrary
from bmpt.library import judge
from bmpt.library.judge import core_of, other_target, part_of
from ba_modding_toolkit.utils import CRCUtils

from test_library import setup_library  # noqa: F401 (fixture)

GROUP = "ui-uilobbyelement-_mxload-prefabs-2025-07-02"


def cab_bundle(folder, cab: str, payload: bytes, *, name: str | None = None):
    """A UnityFS bundle with one node named ``cab``, named after its group and CRC (or ``name``)."""
    info = (bytes(16) + struct.pack(">iIIHiqqI", 1, len(payload), len(payload), 0, 1, 0, len(payload), 0)
            + cab.encode() + b"\0")
    header = b"UnityFS\0" + struct.pack(">I", 7) + b"5.x.x\0" + b"2021.3.0f1\0"
    padding = bytes(-(len(header) + 20) % 16)
    header += struct.pack(">qIII", len(header) + 20 + len(padding) + len(info) + len(payload), len(info), len(info), 64)
    data = header + padding + info + payload
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"{GROUP}_assets_all_{CRCUtils.compute_crc32(data)}.bundle")
    path.write_bytes(data)
    return path


def cab(letter: str) -> str:
    return "CAB-" + letter * 32


@pytest.fixture(autouse=True)
def fresh_registry():
    judge._packed.clear()
    yield
    judge._packed.clear()


@pytest.fixture
def packed(setup_library):
    env = setup_library
    env.prefabs = [cab_bundle(env.game_dir, cab(letter), f"official prefab {letter}".encode()) for letter in "abc"]
    env.library = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    return env


def test_a_learned_cab_gives_a_bundle_of_a_packed_group_its_own_slot():
    first, second = (f"{GROUP}_assets_all_{crc}.bundle" for crc in (1, 2))
    assert core_of(first) == core_of(second)  # nothing learned: the group's one slot
    assert judge.learn_packed(first, "CAB-A") and not judge.learn_packed(first, "cab-a")
    judge.learn_packed(second, "CAB-B")
    assert core_of(first) != core_of(second) and other_target([first]) != other_target([second])
    assert judge.packed_cab(first + ".backup") == "cab-a"


def test_mods_of_two_bundles_of_one_packed_group_are_two_slots_applied_side_by_side(packed):
    env = packed
    a, b, c = env.prefabs
    untouched = c.read_bytes()
    mods = []
    for path, letter in ((a, "a"), (b, "b")):
        source = cab_bundle(env.tmp / f"upload-{letter}", cab(letter), f"modded prefab {letter}".encode(), name=path.name)
        mod, = env.library.add([source], kind="other", direct=True)
        mods.append((mod, source))
    (first, first_source), (second, second_source) = mods
    assert first.target != second.target
    index = env.library._index(env.library.current_pin())
    assert len({core for core, paths in index.items() for path in paths if path in env.prefabs}) == 3
    env.library.apply(first.target, f"mod:{first.id}", kind="other")
    env.library.apply(second.target, f"mod:{second.id}", kind="other")
    assert a.read_bytes() == first_source.read_bytes()
    assert b.read_bytes() == second_source.read_bytes()  # applying the second mod left the first in place
    assert c.read_bytes() == untouched


def test_a_slot_an_earlier_build_shared_moves_to_the_files_own_slot(packed):
    env = packed
    a = env.prefabs[0]
    source = cab_bundle(env.tmp / "upload", cab("a"), b"modded prefab a", name=a.name)
    mod, = env.library.add([source], kind="other", direct=True)
    env.library.apply(mod.target, f"mod:{mod.id}", kind="other")
    pin = env.library.current_pin()
    own, shared = core_of(a.name), "other:" + hashlib.sha256(repr(part_of(a.name)).encode()).hexdigest()
    old_target = "other-set:" + hashlib.sha256(shared.encode()).hexdigest()

    def earlier(data):
        folder = data["folders"][pin.key]
        folder["configured"][shared] = folder["configured"].pop(own)
        for field in ("built", "updated"):
            if own in folder.get(field, {}):
                folder[field][shared] = folder[field].pop(own)

    env.library.profiles.update(earlier)
    env.library._record(mod.id)["target"] = old_target
    env.library._save()
    judge._packed.clear()  # a new start knows nothing yet
    before = a.read_bytes()
    fresh = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    configured = fresh.profiles.read()["folders"][pin.key]["configured"]
    assert own in configured and shared not in configured
    assert configured[own]["source"] == f"mod:{mod.id}"
    assert fresh._record(mod.id)["target"] == other_target([a.name]) != old_target
    assert a.read_bytes() == before  # no game file written


# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
def test_every_official_memorial_lobby_prefab_gets_its_own_slot(tmp_path):
    from bmpt.config import Settings
    from test_library import FakeCatalog, FakeVariant

    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(GAME_DIR))
    library = ModLibrary(tmp_path / "library", FakeCatalog(FakeVariant("test", "test_spr", "test_home")), object(),
                         settings)
    pin = library.current_pin()
    prefabs = sorted(path for folder in pin.dirs for path in folder.iterdir()
                     if path.name.startswith("ui-uilobbyelement-_mxload-prefabs-"))
    assert len({path.name.rsplit("_", 1)[0] for path in prefabs}) < len(prefabs)  # names that differ only in the CRC
    library.note_packed([prefabs[0]], pin)
    index = library._index(pin)
    members = set(prefabs)
    assert len({core for core, paths in index.items() for path in paths if path in members}) == len(prefabs)
