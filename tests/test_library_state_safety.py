"""State and recovery safety of the mod library (FUN-1 to FUN-3 of the functional review): state.json is never
overwritten by a failed read, a missing mod file never deletes a record, and a journal that cannot roll back stops
writes without stopping reads. Synthetic header-only bundles in temporary folders only."""

from __future__ import annotations

import copy
import errno
import json
import logging
import os
from pathlib import Path

import pytest

import test_webui_mods
from ba_modding_toolkit import data_paths, i18n
from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.errors import ServiceError
from ba_modding_toolkit.library import ModLibrary, safety, service
from ba_modding_toolkit.library.service import LEGACY_REMAP
from ba_modding_toolkit.webui.services import LIBRARY_ITEMS

from test_library import FakeCatalog as LibraryCatalog
from test_library import game_bundle, header_bundle, make_mod
from test_library_profiles import OFFICIAL, World, expect_error, world  # noqa: F401 - the world fixture
from test_webui import FakeFamily, FakeGame, FakeVariant
from test_webui_mods import Env, rpc

LOCALES = Path(__file__).resolve().parents[1] / "src" / "ba_modding_toolkit" / "locales"


# --- helpers ---

def state_file(world: World) -> Path:
    return world.root / "state.json"


def read_state(world: World) -> dict:
    return json.loads(state_file(world).read_text(encoding="utf-8"))


def write_state(world: World, data: dict) -> None:
    state_file(world).write_text(json.dumps(data), encoding="utf-8")


def edit_state(world: World, change) -> None:
    data = read_state(world)
    change(data)
    write_state(world, data)


def record_of(world: World, mod_id: str) -> dict:
    return next(record for record in read_state(world)["mods"] if isinstance(record, dict) and record["id"] == mod_id)


def two_mods(world: World):
    """Two mods of two cores, the first one applied (so state.json holds an applied record too)."""
    first = world.add(world.spr)
    second = world.add(game_bundle(world.game, "other_spr"))
    world.library.apply("test", f"mod:{first.id}")
    return first, second


def incoming(world: World, core: str, payload: bytes = b"modded!!"):
    """A mod file of a game core, outside the library."""
    source = next(world.game.glob(f"*{core}*"), None) or game_bundle(world.game, core)
    return make_mod(source, world.tmp / "incoming", payload)


def siblings(world: World) -> set[str]:
    return {path.name for path in world.root.iterdir() if path.name.startswith("state.json")}


class NoRead:
    """Make reading a file of one name fail with an OSError, until closed."""

    def __init__(self, monkeypatch, name: str, error: OSError):
        self.error, self.name, self.active = error, name, True
        self.real = Path.read_text

        def read_text(path, *args, **kwargs):
            if self.active and path.name == name:
                raise error
            return self.real(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)

    def close(self) -> None:
        self.active = False


# --- FUN-1: record by record ---

def test_one_bad_record_costs_that_record_only_through_the_real_library(world):
    first, second = two_mods(world)
    applied_before = read_state(world)["applied"]
    assert applied_before
    edit_state(world, lambda data: next(r for r in data["mods"] if r["id"] == second.id).pop("seen"))
    broken = record_of(world, second.id)
    library = world.reload()
    assert [mod.id for mod in library.mods()] == [first.id]
    library.set_favorite("test", True)  # any change that saves: this erased the library
    data = read_state(world)
    assert [r["id"] for r in data["mods"]] == [first.id, second.id]
    assert data["mods"][1] == broken  # held, unchanged
    assert data["applied"] == applied_before and data["favorites"] == ["test"]
    library.set_favorite("other", True)  # and on every later save
    assert read_state(world)["mods"][1] == broken
    assert [mod.id for mod in world.reload().mods()] == [first.id]


BAD_RECORDS = [
    {}, "text", 7, None, [],
    {"id": "x"},
    {"id": "A" * 32, "files": ["a.bundle"], "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": True},
    {"id": "b" * 32, "files": [], "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": True},
    {"id": "b" * 32, "files": ["a/b.bundle"], "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": True},
    {"id": "b" * 32, "files": ["a.bundle"], "label": 4, "target": "t", "kind": "spr", "added": "x", "seen": True},
    {"id": "b" * 32, "files": ["a.bundle"], "label": "L", "target": "t", "kind": "nope", "added": "x", "seen": True},
    {"id": "b" * 32, "files": ["a.bundle"], "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": "yes"},
    {"id": "b" * 32, "files": ["a.bundle"], "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": True,
     "assignment": ""},
    {"id": "b" * 32, "files": "a.bundle", "label": "L", "target": "t", "kind": "spr", "added": "x", "seen": True},
]


@pytest.mark.parametrize("bad", BAD_RECORDS, ids=lambda bad: json.dumps(bad)[:40])
def test_every_kind_of_bad_record_is_held_and_written_back_unchanged(world, bad, caplog):
    mod = world.add(world.spr)
    edit_state(world, lambda data: data["mods"].insert(0, copy.deepcopy(bad)))
    with caplog.at_level(logging.WARNING, logger="ba_modding_toolkit.library.service"):
        library = world.reload()
    assert any("Holding a library record" in record.getMessage() for record in caplog.records)
    assert [m.id for m in library.mods()] == [mod.id]
    for number in (1, 2):
        library.set_favorite(["test", "other"][number - 1], True)
        data = read_state(world)
        assert bad in data["mods"] and mod.id in [r.get("id") for r in data["mods"] if isinstance(r, dict)]
        assert data["mods"].count(bad) == 1


def test_two_instances_sharing_a_library_with_a_held_record_keep_it_and_each_others_changes(world):
    first, second = two_mods(world)
    edit_state(world, lambda data: next(r for r in data["mods"] if r["id"] == second.id).pop("seen"))
    held = record_of(world, second.id)
    one, two = world.reload(), world.build(data_id="B")
    one.set_favorite("test", True)
    two.set_favorite("other", True)  # reads the other's write first, then adds its own
    data = read_state(world)
    assert data["favorites"] == ["other", "test"] and data["mods"][-1] == held
    assert [r["id"] for r in data["mods"]] == [first.id, second.id]
    one.set_favorite("test", False)
    assert read_state(world)["mods"][-1] == held and two.favorites() == {"other"}
    assert one.favorites() == {"other"}


def test_an_invalid_applied_entry_may_stay_dropped_and_the_rest_stays(world):
    mod = world.add(world.spr)
    world.library.apply("test", f"mod:{mod.id}")
    kept = read_state(world)["applied"]
    edit_state(world, lambda data: data["applied"].update({"/x": "text", "/y": {"source": 3, "sha1": "a"}}))
    library = world.reload()
    library.set_favorite("test", True)
    assert read_state(world)["applied"] == kept


# --- FUN-1: .prev and .bad ---

def test_the_last_good_file_is_kept_as_prev_before_every_write(world):
    world.add(world.spr)
    for number in range(3):
        before = state_file(world).read_bytes()
        world.library.set_favorite("test", number % 2 == 0)
        assert (world.root / "state.json.prev").read_bytes() == before


def test_an_unreadable_file_reads_its_prev_and_is_kept_as_bad_at_the_next_write(world, caplog):
    mod = world.add(world.spr)
    world.library.set_favorite("test", True)
    world.library.set_favorite("other", True)
    good = state_file(world).read_bytes()
    prev = (world.root / "state.json.prev").read_bytes()
    garbage = b'{"version": 1, "mods": [tru'
    state_file(world).write_bytes(garbage)
    with caplog.at_level(logging.WARNING, logger="ba_modding_toolkit.library.service"):
        library = world.reload()
    assert any("previous" in record.getMessage() for record in caplog.records)
    assert [m.id for m in library.mods()] == [mod.id] and library.favorites() == {"test"}  # what prev held
    assert not (world.root / "state.json.bad").exists()  # nothing is written by reading
    assert state_file(world).read_bytes() == garbage
    library.set_favorite("test", False)
    assert (world.root / "state.json.bad").read_bytes() == garbage
    assert (world.root / "state.json.prev").read_bytes() == prev  # never replaced by bad bytes
    assert read_state(world)["favorites"] == []
    assert good != state_file(world).read_bytes()
    after = state_file(world).read_bytes()
    library.set_favorite("test", True)
    assert (world.root / "state.json.prev").read_bytes() == after  # the file is good again


def test_both_files_unreadable_blocks_every_write_and_changes_nothing(world):
    world.add(world.spr)
    world.library.set_favorite("test", True)
    mods_before = sorted(p.name for p in (world.root / "mods").iterdir())
    garbage, junk = b"{broken", b"[]"
    state_file(world).write_bytes(garbage)
    (world.root / "state.json.prev").write_bytes(junk)
    library = world.reload()
    assert library.mods() == [] and library.favorites() == set()  # reads answer an empty library
    error = expect_error("library_state_unreadable", lambda: library.set_favorite("test", True))
    assert error.params["file"] == str(state_file(world))
    expect_error("library_state_unreadable", lambda: library.add([incoming(world, "other_spr")]))
    assert state_file(world).read_bytes() == garbage
    assert (world.root / "state.json.prev").read_bytes() == junk
    assert not (world.root / "state.json.bad").exists()
    assert sorted(p.name for p in (world.root / "mods").iterdir()) == mods_before  # no folder for the import
    assert not list((world.root / "import-transactions").glob("*/journal.json"))


def test_an_unreadable_file_without_prev_is_blocked_and_unblocks_when_fixed_or_removed(world):
    mod = world.add(world.spr)
    good = state_file(world).read_bytes()
    (world.root / "state.json.prev").unlink(missing_ok=True)
    state_file(world).write_bytes(b"{broken")
    library = world.reload()
    assert library.mods() == []
    expect_error("library_state_unreadable", lambda: library.set_favorite("test", True))
    state_file(world).write_bytes(good)  # the user fixes it: the next call reads it again
    assert [m.id for m in library.mods()] == [mod.id]
    library.set_favorite("test", True)
    assert read_state(world)["favorites"] == ["test"]
    # or removes it
    state_file(world).write_bytes(b"{broken")
    (world.root / "state.json.prev").unlink(missing_ok=True)
    other = world.reload()
    expect_error("library_state_unreadable", lambda: other.set_favorite("other", True))
    state_file(world).unlink()
    other.set_favorite("other", True)
    assert read_state(world)["favorites"] == ["other"]


def test_a_state_of_a_newer_version_is_blocked_without_prev_or_bad(world):
    mod = world.add(world.spr)
    world.library.set_favorite("test", True)
    prev = (world.root / "state.json.prev").read_bytes()
    newer = json.dumps({"version": 2, "mods": [], "future": {"kept": 1}}).encode()
    state_file(world).write_bytes(newer)
    library = world.reload()
    assert library.mods() == [] and library.favorites() == set()  # the previous copy was not read
    expect_error("library_state_unreadable", lambda: library.set_favorite("test", True))
    expect_error("library_state_unreadable", lambda: library.add([world.spr]))
    assert state_file(world).read_bytes() == newer
    assert (world.root / "state.json.prev").read_bytes() == prev
    assert not (world.root / "state.json.bad").exists()
    assert siblings(world) <= {"state.json", "state.json.prev", "state.json.lock"}
    del mod


def test_a_file_turning_newer_under_a_running_instance_is_not_overwritten(world):
    mod = world.add(world.spr)
    library = world.library
    newer = json.dumps({"version": 3, "mods": [], "applied": {}}).encode()
    state_file(world).write_bytes(newer)
    assert [m.id for m in library.mods()] == [mod.id]  # it keeps what it holds for reading
    expect_error("library_state_unreadable", lambda: library.set_favorite("test", True))
    expect_error("library_state_unreadable", lambda: library.mark_seen(mod.id))
    assert library.favorites() == set()
    assert state_file(world).read_bytes() == newer
    assert not (world.root / "state.json.bad").exists()


def test_a_file_that_cannot_be_opened_is_not_corruption_at_load(world, monkeypatch):
    mod = world.add(world.spr)
    before = state_file(world).read_bytes()
    locked = NoRead(monkeypatch, "state.json", PermissionError(errno.EACCES, "locked", "state.json"))
    library = world.reload()
    assert library.mods() == []
    error = expect_error("library_io_error", lambda: library.set_favorite("test", True))
    assert error.params["file"] == str(state_file(world))
    locked.close()  # the file did not change: it is read again at the next call
    assert [m.id for m in library.mods()] == [mod.id]
    library.set_favorite("test", True)
    assert read_state(world)["favorites"] == ["test"]
    assert not (world.root / "state.json.bad").exists() and before != state_file(world).read_bytes()


def test_a_file_that_cannot_be_opened_at_save_raises_and_writes_nothing(world, monkeypatch):
    world.add(world.spr)
    world.library.set_favorite("other", True)
    before, prev = state_file(world).read_bytes(), (world.root / "state.json.prev").read_bytes()
    locked = NoRead(monkeypatch, "state.json", PermissionError(errno.EACCES, "locked", "state.json"))
    expect_error("library_io_error", lambda: world.library.set_favorite("test", True))
    locked.close()
    assert state_file(world).read_bytes() == before
    assert (world.root / "state.json.prev").read_bytes() == prev
    assert not (world.root / "state.json.bad").exists()
    assert world.library.favorites() == {"other"}  # the change was taken back


def test_an_instance_holding_good_state_replaces_an_unreadable_file_and_keeps_it_as_bad(world):
    mod = world.add(world.spr)
    world.library.set_favorite("test", True)
    library = world.library
    (world.root / "state.json.prev").unlink()
    garbage = b"\x00\x00 not json"
    state_file(world).write_bytes(garbage)
    library.set_favorite("other", True)  # holds state from a good read: the disk is None branch
    assert (world.root / "state.json.bad").read_bytes() == garbage
    data = read_state(world)
    assert [r["id"] for r in data["mods"]] == [mod.id] and data["favorites"] == ["other", "test"]
    assert not (world.root / "state.json.prev").exists()  # nothing bad went there


def test_an_instance_holding_good_state_keeps_its_last_write_when_the_file_breaks_under_it(world):
    mod = world.add(world.spr)
    library = world.library
    library.set_favorite("test", True)
    last = world.add(incoming(world, "other_spr"))  # the last write: the previous copy does not hold it yet
    prev = (world.root / "state.json.prev").read_bytes()
    assert last.id not in prev.decode()
    garbage = b"{broken"
    state_file(world).write_bytes(garbage)
    library.set_favorite("test", False)
    assert (world.root / "state.json.bad").read_bytes() == garbage
    assert (world.root / "state.json.prev").read_bytes() == prev  # an older copy than the memory is not touched
    data = read_state(world)
    assert [r["id"] for r in data["mods"]] == [mod.id, last.id] and data["favorites"] == []
    assert sorted(m.id for m in world.reload().mods()) == sorted([mod.id, last.id])


def test_the_same_failure_through_the_real_rpc_registry(rpc_env):
    env = rpc_env
    env.add(env.spr)
    assert "error" not in rpc(env.app, "favorites.set", variant="test", on=True)
    folder = env.s.library_dir()
    (folder / "state.json.prev").unlink(missing_ok=True)
    (folder / "state.json").write_bytes(b"{broken")
    env.reload_library()
    roster = rpc(env.app, "roster.list")
    assert "error" not in roster
    family = rpc(env.app, "family.get", key="test")
    assert "error" not in family and family["result"]["variants"][0]["mods"] == 0
    answer = rpc(env.app, "favorites.set", variant="test", on=True)
    assert answer["error"]["code"] == "library_state_unreadable"
    assert answer["error"]["params"]["file"] == str(folder / "state.json")
    assert (folder / "state.json").read_bytes() == b"{broken" and not (folder / "state.json.bad").exists()
    assert "error" not in rpc(env.app, "mods.state")


# --- FUN-1: the places that list or move state files ---

def test_a_library_move_takes_the_state_copies_along(rpc_env):
    env = rpc_env
    source = env.tmp / "lib"
    source.mkdir()
    env.s.state.set("library_dir", str(source))
    names = ("state.json", "state.json.prev", "state.json.bad", "profiles.json", "profiles.json.prev",
             "profiles.json.bad")
    assert set(names) <= set(LIBRARY_ITEMS)
    for name in names:
        (source / name).write_text('{"version": 1, "mods": []}' if name.startswith("state") else
                                   '{"version": 1, "folders": {}}')
    target = env.tmp / "moved"
    assert "error" not in rpc(env.app, "settings.set", key="libraryDir", value=str(target))
    for name in names:
        assert (target / name).exists() and not (source / name).exists(), name


def test_a_data_directory_move_rewrites_the_state_copies(tmp_path):
    old, new = tmp_path / data_paths.OLD_APP_DIR_NAME, tmp_path / data_paths.APP_DIR_NAME
    old.mkdir()
    (old / "library").mkdir()
    content = json.dumps({"path": str(old / "game")})
    for name in ("state.json", "state.json.prev", "state.json.bad"):
        (old / "library" / name).write_text(content)
    assert data_paths.migrate_data_dir(old.parent) == new
    for name in ("state.json", "state.json.prev", "state.json.bad"):
        assert json.loads((new / "library" / name).read_text()) == {"path": str(new / "game")}


def test_the_error_has_its_text_in_every_locale():
    expected = {"en-US": "Unreadable mod library: {file}"}
    for path in sorted(LOCALES.glob("*.json")):
        text = json.loads(path.read_text(encoding="utf-8"))["webui"]["error"]
        assert "{file}" in text["library_state_unreadable"], path.name
        assert text["library_state_unreadable"] != text["profiles_unreadable"], path.name
        if path.stem in expected:
            assert text["library_state_unreadable"] == expected[path.stem]
    assert len(list(LOCALES.glob("*.json"))) == 6


def test_a_rollback_with_a_raw_held_record_in_the_file_still_retires_the_import(world, monkeypatch):
    mod = world.add(world.spr)
    edit_state(world, lambda data: data["mods"].extend(["junk", 5, {"id": "c" * 32}]))
    library = world.reload()
    held = [r for r in read_state(world)["mods"] if r != record_of(world, mod.id)]

    def fail(self):
        raise OSError(errno.ENOSPC, "Synthetic full disk")

    upload = incoming(world, "other_spr")
    monkeypatch.setattr(ModLibrary, "_save", fail)
    with pytest.raises(ServiceError) as caught:
        library.add([upload])
    assert caught.value.code == "library_io_error"  # the cause, not "recovery pending"
    monkeypatch.undo()
    assert not list((world.root / "import-transactions").glob("*/journal.json"))
    assert [r for r in read_state(world)["mods"] if r != record_of(world, mod.id)] == held


# --- the RPC fixtures ---

class RosterCatalog(LibraryCatalog):
    """The library's fake catalog with the families the roster and the detail page list."""

    def __init__(self, *ignored):
        names = {"en-US": {"full": "Test", "short": "Test"}}
        self.v = FakeVariant("test", "test", "test", names, spr="test_spr", lobby="test_home")
        self.o = FakeVariant("other", "other", "other", names, spr="other_spr")
        self.f = FakeFamily("test", {"en-US": "Test"}, "Abydos", [self.v])
        self.g = FakeFamily("other", {"en-US": "Other"}, "Abydos", [self.o])
        super().__init__(self.v, self.o)

    def families(self):
        return [self.f, self.g]

    def family(self, key):
        return next(family for family in self.families() if family.key == key)


@pytest.fixture
def rpc_env(tmp_path, monkeypatch, header_only_bundles):
    """The web UI's services over a real library and the real RPC registry (roster.list, family.get, mods.*)."""
    monkeypatch.setattr(i18n.i18n_manager, "lang", "en-US")
    monkeypatch.setattr(safety, "check_game_not_running", lambda: None)
    monkeypatch.setattr(Bundle, "check_need_crc", lambda *args, **kwargs: False)
    monkeypatch.setattr(test_webui_mods, "FakeCatalog", RosterCatalog)

    class RosterEnv(Env):
        def services(self, data=None, settings=None, game=None):
            return super().services(data, settings, game=FakeGame() if game is None else game)

        def reload_library(self):
            """A new library over the same folder, as a restart builds it."""
            self.s.close()
            self.s, self.app = self.services()

    env = RosterEnv(tmp_path)
    yield env
    env.close()
