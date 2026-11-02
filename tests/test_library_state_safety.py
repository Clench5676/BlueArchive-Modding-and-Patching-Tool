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


# --- FUN-2: a missing file never deletes a record ---

def chosen(world: World) -> dict:
    """The configured choices of every install: {(install, core): the choice}."""
    return {(key, core): choice for key, folder in world.profiles()["folders"].items()
            for core, choice in folder["configured"].items()}


def mod_dir(world: World, mod) -> Path:
    return world.root / "mods" / mod.id


def hide(world: World, mod) -> Path:
    """Take a mod's folder away (an antivirus quarantine, a partial restore); the path it moved to."""
    parked = world.tmp / "parked" / mod.id
    parked.parent.mkdir(exist_ok=True)
    mod_dir(world, mod).rename(parked)
    return parked


def unhide(world: World, mod, parked: Path) -> None:
    parked.rename(mod_dir(world, mod))


def test_a_missing_file_holds_its_record_and_the_mod_returns_when_the_file_does(world):
    first, second = two_mods(world)
    raw = record_of(world, second.id)
    parked = hide(world, second)
    library = world.reload()
    assert [mod.id for mod in library.mods()] == [first.id]  # the engine and the UI never see it
    library.set_favorite("test", True)  # the save that used to delete the record
    library.set_favorite("test", False)
    assert record_of(world, second.id) == raw
    unhide(world, second, parked)
    library.set_favorite("test", True)  # the next save that finds its files returns it
    assert sorted(mod.id for mod in library.mods()) == sorted([first.id, second.id])
    assert record_of(world, second.id) == raw
    assert sorted(mod.id for mod in world.reload().mods()) == sorted([first.id, second.id])


def test_the_report_reproduction_a_file_that_comes_back_finds_its_mod(world):
    mod = world.add(world.spr)
    parked = hide(world, mod)
    library = world.reload()
    assert library.mods() == []
    library.set_favorite("test", True)
    unhide(world, mod, parked)
    assert [m.id for m in world.reload().mods()] == [mod.id]  # mods() was empty after the file came back


def test_a_file_missing_at_save_time_does_not_drop_a_loaded_record(world):
    first, second = two_mods(world)
    raw = record_of(world, second.id)
    parked = hide(world, second)  # the library is running and holds both
    world.library.set_favorite("test", True)
    assert record_of(world, second.id) == raw
    assert [mod.id for mod in world.library.mods()] == [first.id]
    unhide(world, second, parked)
    assert sorted(mod.id for mod in world.reload().mods()) == sorted([first.id, second.id])


def test_alias_assignment_import_source_configuration_and_applied_record_survive_the_gap(world):
    world.consent = True
    first, second = two_mods(world)
    world.library.rename(second.id, "My alias")
    world.library.remember_import_source(second.id, {"kind": "test", "hash": "abc"})
    edit_state(world, lambda data: next(r for r in data["mods"] if r["id"] == second.id).update(assignment="other"))
    world.library.apply("other", f"mod:{second.id}")
    world.reload()
    chosen_before = chosen(world)
    assert chosen_before[(world.key(), "other_spr")]["source"] == f"mod:{second.id}"
    applied = read_state(world)["applied"]
    raw = record_of(world, second.id)
    parked = hide(world, second)
    library = world.reload()
    assert second.id not in [m.id for m in library.mods()]
    # everything the engine does with the configuration and the game files while the mod is held
    entries = world.entries()
    assert all(entry.state != "source_missing" for entry in entries.values())
    library.toggle(False, True)
    library.toggle(True, False)
    library.scan()
    library.set_favorite("test", True)
    library.prune()
    assert chosen(world) == chosen_before  # configured choices intact (built keys may differ: an Off keeps them)
    assert read_state(world)["applied"].keys() == applied.keys()
    assert {path: record["source"] for path, record in read_state(world)["applied"].items()} == \
        {path: record["source"] for path, record in applied.items()}
    assert record_of(world, second.id) == raw
    unhide(world, second, parked)
    library = world.reload()
    back = next(mod for mod in library.mods() if mod.id == second.id)
    assert back.alias == "My alias" and "other" in library.assigned_targets()
    assert library.find_import_source({"kind": "test", "hash": "abc"}) == second.id
    assert chosen(world) == chosen_before
    assert world.entries()[(world.key(), "other_spr")].state == "ok"


def test_a_held_source_is_not_a_source_the_library_lost(world):
    world.consent = True
    mod = world.add(world.spr)
    world.library.apply("test", f"mod:{mod.id}")
    parked = hide(world, mod)
    library = world.reload()
    view = library._view(library.installs(), "full")
    assert view.source_exists(f"mod:{mod.id}", world.key(), "spr")
    assert not view.source_exists(f"mod:{mod.id}", world.key(), "spr", held=False)
    assert not view.source_exists("mod:" + "f" * 32, world.key(), "spr")
    entry = world.entries()[(world.key(), "test_spr")]
    assert entry.state == "ok" and entry.configured == f"mod:{mod.id}" and entry.source == f"mod:{mod.id}"
    unhide(world, mod, parked)


def test_replacing_the_bytes_of_a_held_source_keeps_them_as_a_mod(world):
    held = world.add(world.spr, payload=b"held mod bytes")
    world.library.apply("test", f"mod:{held.id}")
    other = world.add(world.spr, payload=b"another mod!")
    parked = hide(world, held)
    library = world.reload()
    library.apply("test", f"mod:{other.id}")  # replaces the only copy of the held mod's bytes
    kept = [m for m in library.mods() if m.id not in (held.id, other.id)]
    assert len(kept) == 1 and kept[0].file.read_bytes() == header_bundle(b"held mod bytes")
    unhide(world, held, parked)


def test_removing_a_mod_saves_before_it_deletes_the_folder(world, monkeypatch):
    mod = world.add(world.spr)
    seen = []
    real = service.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if Path(path) == mod_dir(world, mod):
            seen.append(mod.id in [r["id"] for r in read_state(world)["mods"] if isinstance(r, dict)])
        return real(path, *args, **kwargs)

    monkeypatch.setattr(service.shutil, "rmtree", rmtree)
    assert not world.library.remove(mod.id, confirm=True).failed
    assert seen == [False]  # saved before the folder went
    assert world.reload().mods() == [] and mod.id not in json.dumps(read_state(world))


def test_a_removal_that_lost_its_folder_first_does_not_come_back_as_a_held_record(world):
    first, second = two_mods(world)
    library = world.library
    library._records.pop(second.id)
    import shutil
    shutil.rmtree(mod_dir(world, second))  # the folder goes before the save: the disk copy holds the record
    library._save()
    assert [r["id"] for r in read_state(world)["mods"]] == [first.id]
    assert library._held_ids() == frozenset()


def test_the_merge_rule_for_held_records():
    id_a, id_b, id_c = "a" * 32, "b" * 32, "c" * 32
    held = [{"id": id_a}, {"id": id_b}, {"id": id_c}, "junk", {"no": "id"}, 5]
    kept = service._held_after(held, {id_a: {}}, {id_b: {}, id_c: {}}, {id_c: {}})
    # id_a is among the merged records, id_b was removed by this instance; no usable id: always kept
    assert kept == [{"id": id_c}, "junk", {"no": "id"}, 5]
    assert service._held_after([], {}, {}, {}) == []


def test_a_rolled_back_import_does_not_leave_a_held_record_behind(world):
    mod = world.add(world.spr)
    record = record_of(world, mod.id)
    new_id = "d" * 32
    ghost = {**record, "id": new_id, "files": [next(iter(record["files"]))]}
    edit_state(world, lambda data: data["mods"].append(ghost))  # committed metadata, its folder never published
    journal = world.root / "import-transactions" / new_id / "journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"version": 1, "id": new_id, "phase": "publishing", "record": ghost,
                                   "hashes": {ghost["files"][0]: "0" * 40}, "repaired_from": "",
                                   "old_repaired_id": None}))
    library = world.reload()
    assert [m.id for m in library.mods()] == [mod.id]
    assert not journal.exists()
    assert [r["id"] for r in read_state(world)["mods"]] == [mod.id]  # not held, not resurrected
    library.set_favorite("test", True)
    assert [r["id"] for r in read_state(world)["mods"]] == [mod.id]


def test_a_failed_save_after_a_rollback_keeps_the_folders_of_records_it_could_not_drop(world, monkeypatch):
    kept = world.add(world.spr)
    library = world.library

    def fail(self):
        raise OSError(errno.ENOSPC, "Synthetic full disk")

    monkeypatch.setattr(ModLibrary, "_save", fail)
    library._drop_mods([kept.id])
    monkeypatch.undo()
    assert mod_dir(world, kept).is_dir()  # still listed in state.json: no ghost record
    assert [m.id for m in library.mods()] == [kept.id]
    library._drop_mods([kept.id])
    assert not mod_dir(world, kept).exists() and kept.id not in json.dumps(read_state(world))


def test_a_held_record_that_returns_during_an_asset_deletion_is_not_rolled_back_as_new(world, monkeypatch):
    victim = world.add(world.spr)
    held = world.add(game_bundle(world.game, "other_spr"))
    parked = hide(world, held)
    library = world.reload()
    assert library._held_ids() == {held.id}
    real = library._update_profiles
    calls = []

    def back_first(*args, **kwargs):
        if not calls:
            calls.append(True)
            unhide(world, held, parked)  # the file returns inside the transaction
        return real(*args, **kwargs)

    def reconcile():
        raise OSError("Synthetic failure after the record was saved")

    monkeypatch.setattr(library, "_update_profiles", back_first)
    with pytest.raises(ServiceError, match="library_io_error"):
        library.remove(victim.id, confirm=True, reconcile=reconcile)
    assert mod_dir(world, held).is_dir() and (mod_dir(world, held) / held.file.name).is_file()
    assert sorted(m.id for m in library.mods()) == sorted([victim.id, held.id])  # the rollback restored the victim
    assert held.id in [r["id"] for r in read_state(world)["mods"]]


def test_two_instances_sharing_a_library_with_a_held_record_see_it_return(world):
    first, second = two_mods(world)
    raw = record_of(world, second.id)
    parked = hide(world, second)
    one, two = world.reload(), world.build(data_id="B")
    assert one._held_ids() == two._held_ids() == {second.id}
    one.set_favorite("test", True)
    two.set_favorite("other", True)
    assert record_of(world, second.id) == raw and read_state(world)["favorites"] == ["other", "test"]
    unhide(world, second, parked)
    two.set_favorite("other", False)  # a save that finds the files
    assert sorted(m.id for m in two.mods()) == sorted([first.id, second.id])
    assert sorted(m.id for m in one.mods()) == sorted([first.id, second.id])  # the other instance reads it
    assert record_of(world, second.id) == raw


def test_switching_a_held_source_reports_it_and_drops_nothing(world):
    world.consent = True
    mod = world.add(world.spr)
    world.library.apply("test", f"mod:{mod.id}")
    world.toggle(False)  # keeps a built copy that names the mod's own file
    before = {key: choice["source"] for key, choice in chosen(world).items()}
    parked = hide(world, mod)
    library = world.reload()
    result = world.toggle(True)  # the mod cannot be written now: the entry fails, nothing is dropped
    assert not result.dropped and [item["code"] for item in result.failed] == ["mod_not_found"]
    assert {key: choice["source"] for key, choice in chosen(world).items()} == before
    assert record_of(world, mod.id)["id"] == mod.id
    unhide(world, mod, parked)
    library = world.reload()
    result = library.toggle(True, True)  # retry once the file is back
    assert not result.failed and not result.dropped
    assert world.spr.read_bytes() == next(m for m in library.mods() if m.id == mod.id).file.read_bytes()


def test_importing_the_same_files_while_a_record_is_held_leaves_it_alone(world):
    mod = world.add(world.spr)
    raw = record_of(world, mod.id)
    parked = hide(world, mod)
    library = world.reload()
    assert library._known() == {}  # the identity of a held record is not known: its files are not there
    again, = library.add([make_mod(world.spr, world.tmp / "again")])
    assert again.id != mod.id and record_of(world, mod.id) == raw
    unhide(world, mod, parked)
    library = world.reload()
    assert sorted(m.id for m in library.mods()) == sorted([mod.id, again.id])  # two mods of one content, kept
