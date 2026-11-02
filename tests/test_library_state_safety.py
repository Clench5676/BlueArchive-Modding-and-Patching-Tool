"""State and recovery safety of the mod library (FUN-1 to FUN-3 of the functional review): state.json is never
overwritten by a failed read, a missing mod file never deletes a record, and a journal that cannot roll back stops
writes without stopping reads. Synthetic header-only bundles in temporary folders only."""

from __future__ import annotations

import copy
import errno
import json
import logging
from pathlib import Path

import pytest

import test_webui_mods
from ba_modding_toolkit import core, data_paths, i18n
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
    good = state_file(world).read_bytes()
    newer = json.dumps({"version": 3, "mods": [], "applied": {}}).encode()
    state_file(world).write_bytes(newer)
    assert [m.id for m in library.mods()] == [mod.id]  # it keeps what it holds for reading
    expect_error("library_state_unreadable", lambda: library.set_favorite("test", True))
    expect_error("library_state_unreadable", lambda: library.mark_seen(mod.id))
    assert library.favorites() == set()
    assert state_file(world).read_bytes() == newer
    assert not (world.root / "state.json.bad").exists()
    state_file(world).write_bytes(good)  # the newer build's file is replaced by a readable one: writes work again
    library.set_favorite("test", True)
    assert read_state(world)["favorites"] == ["test"]


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


# --- FUN-3: reads keep working while a journal is pending, writes stay protected ---

class Crash(BaseException):
    """A process exit, bypassing the ordinary exception rollback."""


def journals_of(world: World, folder: str = "transactions") -> list[Path]:
    return sorted((world.root / folder).glob("*/journal.json"))


def crash_during(world: World, source: str, kind: str = "spr", core: str = "test") -> Path:
    """Apply a source and die right after its game files were written: the write journal stays pending."""
    def log(message):
        if message.startswith("Installed "):
            raise Crash()

    with pytest.raises(Crash):
        world.library.apply(core, source, log=log, kind=kind)
    journal, = journals_of(world)
    assert json.loads(journal.read_text())["phase"] == "writing"
    return journal


def two_sources(world: World):
    """Two mods of one core, the first applied: the second one's switch is the write that dies."""
    first = world.add(world.spr, payload=b"modded!!")
    second = world.add(world.spr, payload=b"modded2!")
    world.library.apply("test", f"mod:{first.id}")
    return first, second


def refuse_recovery(library: ModLibrary, code: str = "classic_open") -> None:
    """Make every recovery attempt fail with a real error, as an open classic window or a running game does."""
    def refuse(pin):
        raise ServiceError(code)

    library._write_check = refuse


def allow_recovery(library: ModLibrary) -> None:
    library._write_check = None


WRITES = {
    "favorite": lambda lib, w, mods: lib.set_favorite("test", True),
    "alias": lambda lib, w, mods: lib.rename(mods[0].id, "alias"),
    "seen": lambda lib, w, mods: lib.mark_seen(mods[0].id),
    "apply": lambda lib, w, mods: lib.apply("test", f"mod:{mods[1].id}"),
    "apply-off": lambda lib, w, mods: lib.apply("test", f"mod:{mods[1].id}"),  # mods off: configured only
    "restore": lambda lib, w, mods: lib.restore("test"),
    "toggle": lambda lib, w, mods: lib.toggle(False, True),
    "add": lambda lib, w, mods: lib.add([incoming(w, "other_spr")]),
    "remove": lambda lib, w, mods: lib.remove(mods[0].id, confirm=True),
    "dismiss": lambda lib, w, mods: lib.dismiss("untracked", [{"install": w.key(), "core": "test_spr"}]),
    "clear-built": lambda lib, w, mods: lib.clear_built(),
}


def test_the_report_reproduction_a_steam_repair_after_a_crash_no_longer_blocks_the_library(world):
    first, second = two_sources(world)
    crash_during(world, f"mod:{second.id}")
    world.spr.write_bytes(OFFICIAL)  # Steam's verify: the bytes match neither the before nor the after
    library = world.reload()
    # reads and writes alike: nothing answers entry_changed any more
    assert [m.id for m in library.mods()] == [first.id, second.id]
    library.set_favorite("test", True)
    assert world.spr.read_bytes() == OFFICIAL  # the outside change is left as it is
    assert not journals_of(world)  # the rollback finished and retired its journal
    assert library._recovery_error is None


def test_a_left_file_keeps_its_old_record_without_a_new_date_so_it_reads_as_changed(world):
    first, second = two_sources(world)
    key = str(world.spr.resolve())
    old = copy.deepcopy(read_state(world)["applied"][key])
    crash_during(world, f"mod:{second.id}")
    world.spr.write_bytes(header_bundle(b"outside!"))  # the same size as the recorded bytes, other content
    library = world.reload()
    record = library._applied[key]
    assert record["mtime"] == old["mtime"] != world.spr.stat().st_mtime_ns  # no new date stamped from the file
    assert record["sha1"] == old["sha1"] and record["source"] == f"mod:{first.id}"
    assert library.state("test").applied != f"mod:{first.id}"  # the outside bytes are not the recorded ones
    assert not journals_of(world)


def test_a_file_that_is_back_to_its_before_or_after_is_restored_as_before(world):
    first, second = two_sources(world)
    before = world.spr.read_bytes()
    crash_during(world, f"mod:{second.id}")
    assert world.spr.read_bytes() != before
    library = world.reload()
    assert world.spr.read_bytes() == before and not journals_of(world)
    assert library._applied[str(world.spr.resolve())]["source"] == f"mod:{first.id}"


def forget_configuration(world: World) -> None:
    """Nothing configured keeps the original of a name any more (the user chose again, another instance cleared it)."""
    edit = json.loads((world.root / "profiles.json").read_text())
    for folder in edit["folders"].values():
        folder["configured"] = {}
    (world.root / "profiles.json").write_text(json.dumps(edit))


def test_a_restored_write_releases_the_original_it_kept_but_a_left_file_keeps_it(world):
    """The trap of _release_failed: the original kept by a failed write is released once the file is back, but it is
    the only official copy of a file left as it is."""
    mod = world.add(world.spr, payload=b"modded!!")
    original = world.original(world.spr)
    crash_during(world, f"mod:{mod.id}")  # the first write over an official file kept it in originals/
    assert original.read_bytes() == OFFICIAL and any(
        item["kept_now"] for item in json.loads(journals_of(world)[0].read_text())["files"])
    forget_configuration(world)
    world.reload()  # nothing changed outside: the file is back as it was, the copy is not needed
    assert world.spr.read_bytes() == OFFICIAL and not original.exists()


def test_the_only_official_original_of_a_left_file_is_not_released(world):
    mod = world.add(world.spr, payload=b"modded!!")
    original = world.original(world.spr)
    crash_during(world, f"mod:{mod.id}")
    forget_configuration(world)
    world.spr.write_bytes(header_bundle(b"outside!"))
    world.reload()
    assert original.read_bytes() == OFFICIAL  # the only official copy stays
    assert world.spr.read_bytes() == header_bundle(b"outside!") and not journals_of(world)


def test_a_failed_write_whose_file_changed_meanwhile_leaves_it_and_keeps_the_original(world, monkeypatch):
    """The same rule on the write's own failure path (not a recovery)."""
    mod = world.add(world.spr, payload=b"modded!!")
    original = world.original(world.spr)
    library = world.library
    real, calls = library._save, []

    def failing_save():
        calls.append(1)
        if len(calls) == 1:
            forget_configuration(world)
            world.spr.write_bytes(header_bundle(b"outside!"))  # an outside change while the state is saved
            raise OSError(errno.ENOSPC, "Synthetic full disk")
        return real()

    monkeypatch.setattr(library, "_save", failing_save)
    with pytest.raises(ServiceError, match="library_io_error"):
        library.apply("test", f"mod:{mod.id}")
    monkeypatch.undo()
    assert world.spr.read_bytes() == header_bundle(b"outside!") and not journals_of(world)
    assert original.read_bytes() == OFFICIAL


def kept_write(world: World):
    """A write that dies after it kept an outside mod's bytes as a library mod: (the kept mod's id, those bytes)."""
    first = world.add(world.spr, payload=b"modded!!")
    second = world.add(world.spr, payload=b"modded2!")
    world.library.apply("test", f"mod:{first.id}")
    unknown = header_bundle(b"someone's mod")
    world.spr.write_bytes(unknown)  # an outside mod over the library's: nothing in the library holds its bytes
    journal = crash_during(world, f"mod:{second.id}")
    data = json.loads(journal.read_text())
    assert len(data["kept"]) == 1  # the outside bytes were kept as a mod before the write
    return data["kept"][0], unknown


def test_a_mod_kept_from_a_left_file_is_not_forgotten(world):
    kept_id, unknown = kept_write(world)
    world.spr.write_bytes(header_bundle(b"outside!"))  # changed again behind the crash: the file is left as it is
    library = world.reload()
    kept = next(m for m in library.mods() if m.id == kept_id)  # its bytes are in no game file: it stays
    assert kept.file.read_bytes() == unknown
    assert world.spr.read_bytes() == header_bundle(b"outside!") and not journals_of(world)


def test_a_restored_write_forgets_the_mods_it_kept(world):
    kept_id, unknown = kept_write(world)
    library = world.reload()  # nothing changed outside: the file is back as it was, the kept mod is not needed
    assert world.spr.read_bytes() == unknown
    assert kept_id not in [m.id for m in library.mods()] and not journals_of(world)
    assert not (world.root / "mods" / kept_id).exists()


def build(world: World, write_check=None, data_id: str = "A") -> ModLibrary:
    """A library over the world's folders (a restart), with the caller's own write check."""
    return ModLibrary(world.root, world.catalog, None, world.settings, scan_allowed=lambda: world.consent,
                      data_id=data_id, write_check=write_check)


def refusing(code: str = "classic_open"):
    def refuse(pin):
        raise ServiceError(code)

    return refuse


def pending(world: World, code: str = "classic_open"):
    """A crashed switch whose recovery fails with a real error: (the library, the two mods)."""
    first, second = two_sources(world)
    crash_during(world, f"mod:{second.id}")
    library = build(world, refusing(code))
    assert library._recovery_error is not None and library._recovery_error.code == code
    return library, [first, second]


def snapshot_of(world: World) -> dict:
    return {name: (world.root / name).read_bytes() for name in ("state.json", "profiles.json")
            if (world.root / name).exists()}


def test_reads_work_while_a_journal_cannot_roll_back(world):
    world.consent = True
    library, mods = pending(world)
    journal, = journals_of(world)
    before = snapshot_of(world)
    assert [m.id for m in library.mods()] == [m.id for m in mods]
    assert library.favorites() == set() and library.state("test") and library.selection("test") is not None
    assert library.configured("test") and library.roster_snapshot(["test"])["mods"]
    assert library.status()["current"] and library.modified() is not None and library.intent()["enabled"]
    assert library.scan().entries is not None and library.entries() is not None
    assert library.judge_unfinished() == 0 and library.take_pruned_cores() == set()
    assert journal.exists() and snapshot_of(world) == before  # the reads changed nothing


@pytest.mark.parametrize("name", sorted(WRITES))
def test_every_write_is_refused_with_the_real_error_and_goes_through_once_recovery_works(world, name):
    library, mods = pending(world)
    if name == "apply-off":  # mods off: the choice is only configured, a write of the configuration alone
        edit = json.loads((world.root / "profiles.json").read_text())
        edit["enabled"] = False
        (world.root / "profiles.json").write_text(json.dumps(edit))
        library = build(world, refusing())
    journal, = journals_of(world)
    before = snapshot_of(world)
    expect_error("classic_open", lambda: WRITES[name](library, world, mods))
    assert journal.exists() and snapshot_of(world) == before  # refused before anything was written
    assert library._recovery_error is not None
    allow_recovery(library)
    WRITES[name](library, world, mods)  # the closed window: the next write recovers first, then goes ahead
    assert not journals_of(world) and library._recovery_error is None


def test_a_successful_recovery_clears_the_remembered_failure_for_reads_too(world, monkeypatch):
    library, mods = pending(world)
    allow_recovery(library)
    monkeypatch.setattr(service, "RECOVERY_RETRY", 0.0)
    library.mods()
    assert library._recovery_error is None and not journals_of(world)
    library.set_favorite("test", True)


def test_reads_try_a_failed_recovery_again_only_when_the_journals_change_or_the_limit_passes(world, monkeypatch):
    assert service.RECOVERY_RETRY == 60.0
    library, mods = pending(world)
    attempts = []
    real = library._recover_transactions
    monkeypatch.setattr(library, "_recover_transactions", lambda: attempts.append(1) or real())
    for _ in range(4):
        library.mods()
        library.state("test")
        library.favorites()
    assert attempts == []  # reads stay fast: no retry within the limit
    other = world.root / "transactions" / ("e" * 32) / "journal.json"
    other.parent.mkdir()
    other.write_text(json.dumps({"version": 1, "phase": "committed"}))  # another journal appears
    library.mods()
    assert len(attempts) == 1  # the set changed: one try
    library.mods()
    library.mods()
    assert len(attempts) == 1
    monkeypatch.setattr(service, "RECOVERY_RETRY", 0.0)  # the limit has passed
    library.mods()
    assert len(attempts) == 2


def test_a_failure_is_logged_once_not_at_every_attempt(world, caplog, monkeypatch):
    library, mods = pending(world)
    monkeypatch.setattr(service, "RECOVERY_RETRY", 0.0)
    with caplog.at_level(logging.WARNING, logger="ba_modding_toolkit.library.service"):
        caplog.clear()
        for _ in range(5):
            library.mods()
    assert [r for r in caplog.records if "Mod recovery pending" in r.getMessage()] == []


def test_the_migration_save_of_a_read_skips_instead_of_raising(world):
    library, mods = pending(world)
    library._migrations.discard(LEGACY_REMAP)  # a migration still to save, as when the file was read late
    library._migration_pending = True
    before = snapshot_of(world)
    assert [m.id for m in library.mods()] == [m.id for m in mods]  # the refresh skips it instead of raising
    assert library._migration_pending and snapshot_of(world) == before
    allow_recovery(library)
    library.set_favorite("test", True)
    assert not library._migration_pending and read_state(world)["migrations"] == [LEGACY_REMAP]


def test_a_remembered_failure_is_cleared_when_the_journals_are_gone(world, monkeypatch):
    library, mods = pending(world)
    for journal in journals_of(world):
        service.shutil.rmtree(journal.parent)  # the user removes it by hand
    library.mods()
    assert library._recovery_error is None
    library.set_favorite("test", True)


# --- FUN-3: folder_changed ---

def edit_journal(journal: Path, change) -> None:
    data = json.loads(journal.read_text())
    change(data)
    journal.write_text(json.dumps(data))


def test_a_new_resource_folder_alone_is_no_folder_change(world):
    first, second = two_sources(world)
    before = world.spr.read_bytes()
    journal = crash_during(world, f"mod:{second.id}")
    edit_journal(journal, lambda data: data["pin"]["dirs"].append(str(world.tmp / "a-new-resource-folder")))
    library = world.reload()  # same install, same key, other dirs: every target is still in a resource folder
    assert library._recovery_error is None and not journals_of(world)
    assert world.spr.read_bytes() == before


def test_changed_folders_stay_folder_changed_when_a_target_is_outside_the_resource_folders(world):
    first, second = two_sources(world)
    journal = crash_during(world, f"mod:{second.id}")

    def change(data):
        data["pin"]["dirs"].append(str(world.tmp / "a-new-resource-folder"))
        data["files"][0]["target"] = str(world.tmp / "elsewhere" / Path(data["files"][0]["target"]).name)

    edit_journal(journal, change)
    before = snapshot_of(world)
    library = world.reload()
    assert library._recovery_error.code == "folder_changed"
    assert [m.id for m in library.mods()] == [first.id, second.id]  # reads work
    expect_error("folder_changed", lambda: library.set_favorite("test", True))  # writes refuse
    expect_error("folder_changed", lambda: library.apply("test", f"mod:{first.id}"))
    assert journal.exists() and snapshot_of(world) == before


def test_a_journal_of_an_install_that_is_not_known_waits_while_other_installs_are_written(world, monkeypatch):
    folder = japan(world)
    other = pin_of(world, folder)
    mod = world.add(world.spr, payload=b"modded!!")
    world.library.apply("test", f"mod:{mod.id}", pin=other)
    second = world.add(world.spr, payload=b"modded2!")
    japan_file = folder / world.spr.name
    before = japan_file.read_bytes()

    def log(message):
        if message.startswith("Installed "):
            raise Crash()

    with pytest.raises(Crash):
        world.library.apply("test", f"mod:{second.id}", pin=other, log=log)
    journal, = journals_of(world)
    moved = world.tmp / "japan-moved"
    folder.rename(moved)  # the install's folder moved away: its pin is gone
    library = world.reload()
    assert library._recovery_error is None  # it waits, no failure
    assert journal.exists() and (journal.parent / "0.bundle").exists()  # with its files
    library.set_favorite("test", True)
    library.apply("test", f"mod:{mod.id}")  # a write to the other install goes on
    assert world.spr.read_bytes() == mod.file.read_bytes()
    assert journal.exists()
    attempts = []
    real = library._recover_transactions
    monkeypatch.setattr(library, "_recover_transactions", lambda: attempts.append(1) or real())
    for _ in range(3):
        library.mods()
    assert attempts == []  # a retry that raises nothing follows the same limit as a failed one
    monkeypatch.setattr(service, "RECOVERY_RETRY", 0.0)
    library.mods()
    assert len(attempts) == 1 and journal.exists()
    moved.rename(folder)  # the install is back: the next write rolls its journal back, restoring only its own keys
    library.set_favorite("test", False)
    assert not journals_of(world) and japan_file.read_bytes() == before
    assert world.spr.read_bytes() == mod.file.read_bytes()  # the other install was not touched


def test_a_journal_whose_install_key_differs_waits_too(world):
    first, second = two_sources(world)
    journal = crash_during(world, f"mod:{second.id}")
    written = world.spr.read_bytes()
    edit_journal(journal, lambda data: data["pin"].update(key="/not/this/install"))
    library = world.reload()
    assert library._recovery_error is None and journal.exists()
    assert world.spr.read_bytes() == written  # untouched
    assert [m.id for m in library.mods()] == [first.id, second.id]


def japan(world: World, *cores: str) -> Path:
    folder = world.tmp / "japan"
    for core in cores or ("test_spr",):
        game_bundle(folder, core)
    world.settings.set("game_resource_dir_japan", str(folder))
    return folder


def pin_of(world: World, folder: Path):
    return next(pin for pin in world.library.installs() if pin.key == world.key(folder))


# --- FUN-3: an asset deletion's journal ---

def crash_during_removal(world: World, mod, monkeypatch) -> Path:
    """Remove an applied mod and die while undoing the removal: the deletion journal stays pending."""
    from ba_modding_toolkit.library import asset_delete

    def die(self):
        raise Crash()

    monkeypatch.setattr(asset_delete.Deletion, "rollback", die)

    def reconcile():
        raise Crash()

    with pytest.raises(Crash):
        world.library.remove(mod.id, confirm=True, reconcile=reconcile)
    monkeypatch.undo()
    journal, = journals_of(world, "asset-deletions")
    assert json.loads(journal.read_text())["games"]
    return journal


def test_an_asset_deletion_whose_target_is_gone_rolls_back_without_recreating_it(world, monkeypatch):
    world.consent = True
    mod = world.add(world.spr, payload=b"modded!!")
    world.library.apply("test", f"mod:{mod.id}")
    crash_during_removal(world, mod, monkeypatch)
    world.spr.unlink()  # a game update renamed the bundle: hashing the target used to raise library_io_error
    library = world.reload()
    assert library._recovery_error is None and not journals_of(world, "asset-deletions")
    assert not world.spr.exists()  # not recreated
    assert [m.id for m in library.mods()] == [mod.id]  # the records are back
    library.set_favorite("test", True)


def test_an_asset_deletion_target_changed_outside_is_left_and_its_record_has_no_new_date(world, monkeypatch):
    mod = world.add(world.spr, payload=b"modded!!")
    world.library.apply("test", f"mod:{mod.id}")
    key = str(world.spr.resolve())
    recorded = copy.deepcopy(read_state(world)["applied"][key])
    crash_during_removal(world, mod, monkeypatch)
    outside = header_bundle(b"outside!")  # the same size as the recorded bytes
    world.spr.write_bytes(outside)
    library = world.reload()
    assert library._recovery_error is None and not journals_of(world, "asset-deletions")
    assert world.spr.read_bytes() == outside
    assert library._applied[key]["mtime"] == recorded["mtime"] != world.spr.stat().st_mtime_ns
    assert library.state("test").applied != f"mod:{mod.id}"
    assert [m.id for m in library.mods()] == [mod.id]


@pytest.mark.parametrize("how", ["folder gone", "key differs"])
def test_an_asset_deletion_of_an_unknown_install_stays_a_remembered_failure(world, monkeypatch, how):
    mod = world.add(world.spr, payload=b"modded!!")
    world.library.apply("test", f"mod:{mod.id}")
    journal = crash_during_removal(world, mod, monkeypatch)
    if how == "key differs":
        edit_journal(journal, lambda data: data["games"][0]["pin"].update(key="/not/this/install"))
    else:
        edit_journal(journal, lambda data: data["games"][0]["pin"].update(root=str(world.tmp / "gone")))
    before = snapshot_of(world)
    library = world.reload()
    assert library._recovery_error.code == "folder_changed"  # it cannot wait: its rollback restores a whole snapshot
    library.mods()
    library.favorites()  # reads work
    expect_error("folder_changed", lambda: library.set_favorite("test", True))  # writes refuse
    assert journal.exists() and snapshot_of(world) == before


# --- FUN-3: through the real RPC registry ---

def crash_in_services(env) -> tuple:
    """Two mods of the test core, the first applied, a switch to the second dying after its game files: the library
    of the services holds a pending write journal."""
    library = env.s.library
    first = env.add(env.spr, payload=b"modded!!")
    second = env.add(env.spr, payload=b"modded2!")
    library.apply("test", f"mod:{first.id}")

    def log(message):
        if message.startswith("Installed "):
            raise Crash()

    with pytest.raises(Crash):
        library.apply("test", f"mod:{second.id}", log=log)
    assert list((library.root / "transactions").glob("*/journal.json"))
    return first, second


ANSWERS = (("roster.list", {}), ("family.get", {"key": "test"}), ("mods.state", {}), ("settings.get", {}))


def test_after_a_steam_repair_every_call_answers_through_the_rpc_registry(rpc_env):
    env = rpc_env
    first, second = crash_in_services(env)
    env.spr.write_bytes(OFFICIAL)  # the bytes match neither the before nor the after
    env.reload_library()  # a restart: the library recovers at its build
    for method, params in ANSWERS:
        assert "error" not in rpc(env.app, method, **params), method
    job = env.job("mods.toggle", enabled=False, expect=True)  # this answered entry_changed
    assert job.state == "done", job.error
    assert env.spr.read_bytes() == OFFICIAL
    assert not list((env.s.library.root / "transactions").glob("*/journal.json"))


def test_reads_answer_and_writes_refuse_through_the_rpc_registry_while_a_recovery_fails(rpc_env):
    env = rpc_env
    first, second = crash_in_services(env)
    library = env.s.library
    refuse = refusing()
    library._write_check = refuse
    for method, params in ANSWERS:
        assert "error" not in rpc(env.app, method, **params), method
    family = rpc(env.app, "family.get", key="test")["result"]["variants"][0]
    assert {mod["id"] for mod in family["modList"]} == {first.id, second.id}
    answer = rpc(env.app, "favorites.set", variant="test", on=True)
    assert answer["error"]["code"] == "classic_open"  # a write: the real error
    job = env.job("mods.toggle", enabled=False, expect=True)
    assert job.state == "failed" and job.error["code"] == "classic_open"
    assert "error" not in rpc(env.app, "mods.state")
    library._write_check = None
    assert "error" not in rpc(env.app, "favorites.set", variant="test", on=True)
    job = env.job("mods.toggle", enabled=False, expect=True)
    assert job.state == "done", job.error
    assert not list((library.root / "transactions").glob("*/journal.json"))


def test_a_mod_update_is_refused_before_the_rebuild_while_a_recovery_has_failed(world, monkeypatch):
    library, mods = pending(world)
    monkeypatch.setattr(core, "process_mod_update", lambda *a, **k: pytest.fail("the rebuild started"))
    expect_error("classic_open", lambda: library.update(mods[0].id))
