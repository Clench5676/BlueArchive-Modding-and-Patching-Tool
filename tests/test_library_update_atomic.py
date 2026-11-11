"""A mod update replaces its files atomically per mod: fault injection at each boundary, then recovery."""

from __future__ import annotations

import errno
import json

import pytest

from bmpt.errors import ServiceError
from bmpt.library import service
from ba_modding_toolkit.models import FilePair
from bmpt.library.rebuild import RebuildResult

from test_library import game_bundle, header_bundle, make_mod, reload_library, setup_library


def journals(env):
    return sorted((env.root / "import-transactions").glob("*/journal.json"))


def raw_record(env, mod_id):
    state = json.loads((env.root / "state.json").read_text(encoding="utf-8"))
    return next(record for record in state["mods"] if record["id"] == mod_id)


@pytest.fixture
def two_part_mod(setup_library, monkeypatch):
    """An applied two-part mod and a rebuild that changes both parts, keeping their names (split: renaming one)."""
    env = setup_library
    second = game_bundle(env.game_dir, part="textassets", payload=b"official text")
    entry = env.library.add([make_mod(target, env.tmp / "first", payload=b"first " + target.name.encode()[:8])
                             for target in (env.original, second)])[0]
    env.library.apply("test", f"mod:{entry.id}")
    directory = env.root / "mods" / entry.id
    old = {path.name: path.read_bytes() for path in directory.glob("*.bundle")}
    assert len(old) == 2

    def rebuild(sources, current, output, *args, **kwargs):
        result = output / current[0].name
        result.write_bytes(header_bundle(b"rebuilt " + current[0].name.encode()[-20:]))
        return RebuildResult(True, "updated", [FilePair(result, current[0])])

    monkeypatch.setattr(service, "rebuild", rebuild)
    env.entry, env.directory, env.old = entry, directory, old
    env.record = raw_record(env, entry.id)
    return env


def assert_old_mod_whole(env):
    assert not journals(env)
    assert {path.name: path.read_bytes() for path in env.directory.glob("*.bundle")} == env.old
    assert raw_record(env, env.entry.id) == env.record
    mod, = env.library.mods()
    assert mod.id == env.entry.id and sorted(path.name for path in env.library._paths(env.record)) == sorted(env.old)
    assert env.library.configured("test")["spr"] == f"mod:{env.entry.id}"
    previous = env.directory / "previous"
    assert not previous.exists() or not any(previous.iterdir())


@pytest.mark.parametrize("boundary", ["journal", "backup", "replace", "metadata"])
def test_an_interrupted_update_leaves_the_old_mod_whole_or_finishes_after_its_commit(two_part_mod, monkeypatch,
                                                                                     boundary):
    env = two_part_mod
    copy, write = service.atomic_copy, service.atomic_json
    copies = 0

    def copying(source, target):
        nonlocal copies
        if boundary == "backup" and "previous" in target.parts:
            raise SystemExit("Synthetic termination while keeping the old files")
        result = copy(source, target)
        if target.parent == env.directory:
            copies += 1
            if boundary == "replace" and copies == 1:
                raise SystemExit("Synthetic termination with old and new parts mixed")
        return result

    def writing(path, data):
        result = write(path, data)
        if boundary == "journal" and path.name == "journal.json" and "import-transactions" in path.parts:
            raise SystemExit("Synthetic termination after the journal")
        if boundary == "metadata" and path == env.root / "state.json":
            raise SystemExit("Synthetic termination after the commit")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(service, "atomic_copy", copying)
        patch.setattr(service, "atomic_json", writing)
        with pytest.raises(SystemExit):
            env.library.update(env.entry.id)
    journal, = journals(env)
    assert journal.parent.name == env.entry.id and json.loads(journal.read_text())["operation"] == "update"
    if boundary == "replace":
        mixed = {path.name: path.read_bytes() for path in env.directory.glob("*.bundle")}
        assert mixed != env.old and set(mixed.values()) & set(env.old.values())
    env.library = reload_library(env)
    if boundary == "metadata":
        mod, = env.library.mods()
        assert not journals(env)
        assert mod.id == env.entry.id
        assert all(b"rebuilt" in path.read_bytes() for path in env.library._paths(raw_record(env, mod.id)))
        record = raw_record(env, mod.id)
        assert {key: value for key, value in record.items() if key != "files"} == {
            key: value for key, value in env.record.items() if key != "files"}
        assert len(list((env.directory / "previous").iterdir())) == 1
        assert env.library.configured("test")["spr"] == f"mod:{env.entry.id}"
    else:
        assert_old_mod_whole(env)


def test_a_failed_replace_rolls_back_in_process(two_part_mod, monkeypatch):
    env = two_part_mod
    copy, failed = service.atomic_copy, []

    def full(source, target):
        copy(source, target)
        if target.parent == env.directory and not failed:
            failed.append(target)  # the first new part lands, then the disk is full; the rollback's copies work
            raise OSError(errno.ENOSPC, "Synthetic disk full", str(target))

    with monkeypatch.context() as patch:
        patch.setattr(service, "atomic_copy", full)
        with pytest.raises(ServiceError, match="library_io_error"):
            env.library.update(env.entry.id)
    assert_old_mod_whole(env)


def test_a_split_update_interrupted_after_a_new_part_removes_it(setup_library, monkeypatch):
    env = setup_library
    entry = env.library.add([make_mod(env.original, env.tmp / "input")])[0]
    old = entry.file.read_bytes()
    record = raw_record(env, entry.id)
    env.original.unlink()
    for part in ("002", "003"):
        game_bundle(env.game_dir, part=part, date="2026-10-01", payload=part.encode())
    from ba_modding_toolkit.core import mod_install
    monkeypatch.setattr(mod_install, "asset_match", lambda sources, candidates, log: (candidates, ""))

    def rebuild(sources, current, output, *args, **kwargs):
        result = output / current[0].name
        result.write_bytes(header_bundle(b"split " + current[0].name.encode()[-12:]))
        return RebuildResult(True, "updated", [FilePair(result, current[0])])

    monkeypatch.setattr(service, "rebuild", rebuild)
    copy = service.atomic_copy

    def copying(source, target):
        result = copy(source, target)
        if target.parent == entry.file.parent:
            raise SystemExit("Synthetic termination after the first new part")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(service, "atomic_copy", copying)
        with pytest.raises(SystemExit):
            env.library.update(entry.id)
    assert len(list(entry.file.parent.glob("*.bundle"))) == 2
    env.library = reload_library(env)
    assert [mod.id for mod in env.library.mods()] == [entry.id]
    assert [path.name for path in entry.file.parent.glob("*.bundle")] == [entry.file.name]
    assert entry.file.read_bytes() == old and raw_record(env, entry.id) == record and not journals(env)


def test_only_the_newest_previous_copy_is_kept(two_part_mod, monkeypatch):
    env = two_part_mod
    first = env.library.update(env.entry.id)
    rebuilt = {path.name: path.read_bytes() for path in first.files}

    def again(sources, current, output, *args, **kwargs):
        result = output / current[0].name
        result.write_bytes(header_bundle(b"again " + current[0].name.encode()[-20:]))
        return RebuildResult(True, "updated", [FilePair(result, current[0])])

    monkeypatch.setattr(service, "rebuild", again)
    second = env.library.update(env.entry.id)
    folders = list((env.directory / "previous").iterdir())
    assert folders == [second.previous[0].parent]
    assert {path.name: path.read_bytes() for path in second.previous} == rebuilt
    assert not first.previous[0].exists()


def test_the_update_publishes_under_the_mod_writer_and_refuses_changed_files(two_part_mod, monkeypatch):
    env = two_part_mod
    write, seen = service.atomic_json, []

    def writing(path, data):
        if "import-transactions" in path.parts:
            seen.append(env.library._writing_mods)
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(service, "atomic_json", writing)
        env.library.update(env.entry.id)
    assert seen and all(seen)
    current = {path.name: path.read_bytes() for path in env.directory.glob("*.bundle")}
    rebuild = service.rebuild

    def changing(sources, targets, output, *args, **kwargs):
        sources[0].write_bytes(header_bundle(b"changed meanwhile"))
        return rebuild(sources, targets, output, *args, **kwargs)

    monkeypatch.setattr(service, "rebuild", lambda *args, **kwargs: changing(*args, **kwargs))
    with pytest.raises(ServiceError, match="entry_changed"):
        env.library.update(env.entry.id)
    assert not journals(env)
    changed = {path.name: path.read_bytes() for path in env.directory.glob("*.bundle")}
    assert sorted(changed) == sorted(current)


@pytest.mark.parametrize("damage", ["previous", "name", "phase"])
def test_an_invalid_update_journal_is_left_pending_and_deletes_nothing(two_part_mod, damage):
    env = two_part_mod
    old = dict(env.old)
    data = {"version": 1, "operation": "update", "id": env.entry.id, "phase": "replacing",
            "old": {name: "0" * 40 for name in old}, "new": {name: "1" * 40 for name in old}, "previous": "a" * 32}
    if damage == "previous":
        data["previous"] = "../../outside"
    elif damage == "name":
        data["new"] = {"../escape.bundle": "1" * 40}
    else:
        data["phase"] = "publishing"
    journal = env.root / "import-transactions" / env.entry.id / "journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps(data))
    env.library = reload_library(env)
    assert [mod.id for mod in env.library.mods()] == [env.entry.id]
    with pytest.raises(ServiceError, match="library_recovery_pending"):
        env.library.update(env.entry.id)
    assert journal.exists()
    assert {path.name: path.read_bytes() for path in env.directory.glob("*.bundle")} == old
