"""Verify generation boundaries with private data, library and game fixtures."""

import hashlib
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from catalog_fixture import synthetic_catalog

from bmpt.core.errors import ServiceError
from bmpt.host import format_manifest, store_formats, upgrade_files
from bmpt.host.data_upgrade import DataUpgrade
from bmpt.library.storage import atomic_json
from bmpt.unity import ccdb
from bmpt.host.store_formats import (
    FutureFormat,
    InvalidStore,
    documents_equal,
    inverse_translate,
    json_bytes,
    load_document,
    managed_bytes,
    managed_document,
    parse_json,
    translate,
)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(data))
    return path


def _data(data, library=None):
    data.mkdir()
    _write(
        data / "webui.json",
        {
            "prefs": {
                "data_id": "0123456789ab",
                "data_path": str(data),
                **({"library_dir": str(library)} if library else {}),
            }
        },
    )
    return data


def _open(data, **options):
    return DataUpgrade(data, qualify=lambda _: "apfs", **options)


def test_upgrade_reports_weighted_steps_and_marks_skipped_staging_done(tmp_path, monkeypatch):
    data = _data(tmp_path / "data")
    _write(data / "library" / "state.json", {"version": 1, "mods": []})
    _write(data / "library" / "profiles.json", {"version": 1, "folders": {}})
    samples = []
    upgrade = _open(data, fault=lambda label: samples.append((label, upgrade.state())))
    report = upgrade._status

    def observe(**values):
        report(**values)
        if values.get("progress"):
            samples.append(("progress", upgrade.state()))

    monkeypatch.setattr(upgrade, "_status", observe)
    assert upgrade.state()["steps"] is None and upgrade.state()["percent"] == 0
    try:
        ready = upgrade.run()
        ids = ["check", "settings", "library", "verify", "open"]
        states = [state for _, state in samples] + [ready]
        assert all([step["id"] for step in state["steps"]] == ids for state in states)
        assert [state["percent"] for state in states] == sorted(state["percent"] for state in states)
        assert all(state["percent"] < 100 for state in states[:-1])
        running = []
        for state in states[:-1]:
            statuses = [step["status"] for step in state["steps"]]
            index = statuses.index("running")
            assert statuses == ["done"] * index + ["running"] + ["pending"] * (len(ids) - index - 1)
            if not running or running[-1] != ids[index]:
                running.append(ids[index])
        assert running == ids
        assert {state["percent"] for label, state in samples if label == "progress"} >= {5, 10, 50, 90}
        assert ready["status"] == "ready" and ready["percent"] == 100
        assert all(step["status"] == "done" for step in ready["steps"])

        upgrade.close()
        samples.clear()
        assert upgrade.run()["status"] == "ready"
        opening = next(state for label, state in samples if label == "open")
        assert opening["percent"] == 90
        assert [step["status"] for step in opening["steps"]] == ["done", "done", "done", "running", "pending"]
    finally:
        upgrade.close()


def test_upgrade_copies_revision_keyed_library_caches_when_absent(tmp_path):
    data = _data(tmp_path / "data")
    legacy = data / "library"
    entries = {
        "costume-verdicts.json": b'{"version":2,"verdicts":{}}',
        "worn-items.json": b'{"version":2,"chibis":{}}',
        "costume-poses/" + "a" * 64 + ".npz": b"cached pose",
    }
    for name, value in entries.items():
        path = legacy / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    original_times = {name: (legacy / name).stat().st_mtime_ns for name in entries}
    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        root = upgrade.paths.library_cache
        for name, value in entries.items():
            assert (root / name).read_bytes() == (legacy / name).read_bytes() == value
            assert (legacy / name).stat().st_mtime_ns == original_times[name]
            (root / name).write_bytes(b"new cache contents")
        upgrade.close()
        assert upgrade.run()["status"] == "ready"
        assert all((root / name).read_bytes() == b"new cache contents" for name in entries)
    finally:
        upgrade.close()


@pytest.mark.parametrize(
    ("store", "original", "path", "expected"),
    [
        (
            "U",
            {
                "prefs": {
                    "roster": {"merged": True, "recruit": "yes", "filters": {"portrait": ["official"]}},
                    "lobby_zoom_out": False,
                    "lobby_zoom_custom": 900,
                }
            },
            ("prefs", "student_list", "group_alts"),
            True,
        ),
        (
            "U",
            {"prefs": {"hall_viewed": ["foo:variant:bar"], "batch_import": {"strategy": "cont_name_type"}}},
            ("prefs", "student_hall_viewed"),
            ["foo:alt:bar"],
        ),
        (
            "S",
            {
                "version": 1,
                "mods": [
                    {
                        "target": "other:ID",
                        "kind": "other",
                        "label": "variant:mine",
                        "assignment": "x",
                        "optimized_from": "variant:foo",
                        "import_sources": [{"source": {"files": [], "originals": [], "extra": 1}}],
                        "roster_photography": {"choices": {"collection": {"variant": "x", "photo": "capture"}}},
                    }
                ],
            },
            ("mods", 0, "generic_target"),
            "other:ID",
        ),
        (
            "P",
            {
                "version": 1,
                "folders": {
                    "/game": {"configured": {"core": {"variant": "a", "kind": "lobby", "source": "variant:b"}}}
                },
            },
            ("folders", "/game", "configured", "core", "resource_type"),
            "memorialLobby",
        ),
        (
            "F",
            {
                "version": 1,
                "photos": {"capture": {"mode": "lobby", "variant": "a", "expression": "variant:custom"}},
                "selected": {"a": None, "b": "official"},
                "next": 5,
            },
            ("thumbnails", "capture", "mode"),
            "memorialLobby",
        ),
        (
            "O",
            {"version": 1, "originals": {"unchanged.bundle": {"by": "failed"}}},
            ("officials", "unchanged.bundle", "by"),
            "failed",
        ),
        (
            "I",
            {"version": 1, "phase": "preparing", "id": "a", "record": {"repaired_id": "b"}, "hashes": {}},
            ("record", "repaired_copy_id"),
            "b",
        ),
        (
            "T",
            {
                "version": 1,
                "phase": "preparing",
                "pin": {},
                "files": [],
                "kept": [],
                "old_applied": {"a": {"source": "variant:foo"}},
            },
            ("old_applied", "a", "source"),
            "alt:foo",
        ),
        (
            "D",
            {
                "version": 1,
                "phase": "active",
                "records": {},
                "applied": {},
                "mods": [],
                "built": [],
                "games": [],
                "profiles": {"version": 1},
                "photo": {"variant": "a", "entry": {"mode": "portrait"}},
            },
            ("thumbnail", "alt"),
            "a",
        ),
    ],
)
def test_formats(store, original, path, expected, tmp_path):
    converted, shape = translate(store, original)
    member = converted
    for key in path:
        member = member[key]
    assert member == expected
    assert documents_equal(inverse_translate(store, parse_json(json_bytes(converted)), shape), original)
    current = _write(tmp_path / "current.json", converted)
    assert managed_bytes(store, managed_document(store, current)) == current.read_bytes()


def test_strict_documents(tmp_path):
    with pytest.raises(InvalidStore, match="Duplicate"):
        parse_json('{"outer": {"key": 1, "key": 2}}')
    opaque = parse_json('{"x": 0.1234567890123456789}')
    assert b"0.1234567890123456789" in json_bytes(opaque)
    same = {"version": 1, "mods": [{"label": "a", "name": "a"}]}
    current, shape = translate("S", same)
    assert inverse_translate("S", current, shape) == same
    with pytest.raises(InvalidStore, match="Conflicting"):
        translate("S", {"version": 1, "mods": [{"label": "a", "name": "b"}]})
    with pytest.raises(FutureFormat):
        load_document("S", _write(tmp_path / "state.json", {"version": 3}))


def test_runtime_state_reuses_validated_records_and_preserves_opaque_numbers(monkeypatch):
    encoder = store_formats.StateEncoder()
    document = parse_json(
        '{"version":2,"mods":[{"resource_type":"portrait","name":"one","opaque":0.1234567890123456789}],'
        '"applied":{"a":{"source":"alt:a","sha1":"old"},"b":{"source":"official","sha1":"kept"}}}'
    )
    assert parse_json(encoder.encode(document)) == parse_json(store_formats.managed_bytes("S", document))
    checked = []
    original = store_formats._check_applied

    def check(record):
        checked.append(record)
        return original(record)

    monkeypatch.setattr(store_formats, "_check_applied", check)
    document["applied"]["a"]["sha1"] = "new"
    encoded = encoder.encode(document)
    assert checked == [document["applied"]["a"]]
    assert parse_json(encoded)["applied"] == {
        "a": {"source": "alt:a", "sha1": "new"},
        "b": {"source": "official", "sha1": "kept"},
    }
    assert b"0.1234567890123456789" in encoded
    document["applied"]["a"].pop("sha1")
    with pytest.raises(InvalidStore, match="Invalid applied entry"):
        encoder.encode(document)


def test_publication_and_retained_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_files, "native_clone", lambda *_: False)
    data = _data(tmp_path / "data")
    legacy = data / "library"
    state = _write(legacy / "state.json", {"version": 1, "mods": [], "opaque": {"a": "variant:text"}})
    raw = state.read_bytes()
    payload = legacy / "mods" / ("a" * 32) / "previous" / ("b" * 32) / "old_spr.bundle"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"retained Mod")
    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        paths = upgrade.paths
        assert state.read_bytes() == raw
        assert load_document("S", paths.library_epoch / "state.json")["opaque"] == {"a": "variant:text"}
        pinned = paths.library_epoch / payload.relative_to(legacy)
        assert pinned.stat().st_ino == payload.stat().st_ino
        assert paths.cache(data / "cache") == data / "cache-bmpt-g2"
    finally:
        upgrade.close()
    again = _open(data)
    try:
        assert again.run()["status"] == "ready"
        assert again.paths == paths
    finally:
        again.close()


@pytest.mark.parametrize(("boundary", "committed"), [("route:durable", False), ("head:durable", True)])
def test_publication_fault_restarts(tmp_path, boundary, committed):
    data = _data(tmp_path / "data")

    def fault(phase):
        if phase == boundary:
            raise OSError("injected failure")

    failure = _open(data, fault=fault)
    assert failure.run()["status"] == "failed"
    assert failure.paths is None
    head = data / "library" / "library.json"
    assert head.is_file() is committed
    fresh = _open(data)
    try:
        assert fresh.run()["status"] == "ready"
    finally:
        fresh.close()


def test_upgrade_prefers_a_detached_clone_when_available(tmp_path, monkeypatch):
    data = _data(tmp_path / "data")
    original = data / "library" / "originals" / "x.bundle"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"official")

    def clone(source, target):
        target.write_bytes(source.read_bytes())
        return True

    monkeypatch.setattr(upgrade_files, "native_clone", clone)
    upgrade = DataUpgrade(data, qualify=lambda _: "ntfs")
    try:
        assert upgrade.run()["status"] == "ready"
        pinned = upgrade.paths.library_epoch / "originals" / original.name
        original.write_bytes(b"external edit")
        assert pinned.read_bytes() == b"official"
    finally:
        upgrade.close()


def test_payload_that_cannot_be_cloned_or_linked_leaves_legacy_data_intact(tmp_path, monkeypatch):
    import errno

    data = _data(tmp_path / "data")
    original = data / "library" / "originals" / "x.bundle"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"official")

    def different_volume(source, target, **options):
        raise OSError(errno.EXDEV, "Different volume", source)

    monkeypatch.setattr(upgrade_files, "native_clone", lambda *_: False)
    monkeypatch.setattr(upgrade_files.os, "link", different_volume)
    upgrade = _open(data)
    try:
        state = upgrade.run()
        assert state["status"] == "failed" and state["reason"] == "unsupportedStorage"
        assert state["notice"] is None
        assert state["steps"] is None and state["percent"] < 100
        assert not (data / "data.json").exists()
        assert not (data / "library" / "library.json").exists()
        assert original.read_bytes() == b"official"
    finally:
        upgrade.close()


def test_notice_acknowledgement_survives_restoring_an_older_generation_2_backup(tmp_path):
    data = _data(tmp_path / "data")
    upgrade = _open(data)
    try:
        assert upgrade.run()["notice"] == "upgraded"
        upgrade.close()
        legacy = data / "webui.json"
        document = load_document("U", legacy, generation=1)
        document["prefs"]["test_preference"] = "imported"
        _write(legacy, document)
        state = upgrade.run()
        assert state["status"] == "changed"
        assert state["steps"] is None and state["percent"] < 100
        assert upgrade.run("import")["notice"] == "upgraded"
        upgrade.acknowledge()
        paths = upgrade.restore_backup(0)
        prefs = load_document("U", paths.ui_state)["prefs"]
        assert "test_preference" not in prefs
        assert prefs["data_upgrade_notice"] is None and upgrade.state()["notice"] is None
    finally:
        upgrade.close()


def test_shared_library_keep_import_and_relocation(tmp_path):
    legacy = tmp_path / "library"
    legacy.mkdir()
    first = _data(tmp_path / "first", legacy)
    second = _data(tmp_path / "second", legacy)
    state = _write(legacy / "state.json", {"version": 1, "favorites": ["one"]})
    primary, peer = _open(first), _open(second)
    try:
        assert primary.run()["status"] == "ready"
        initial = primary.paths
        assert peer.run()["reason"] == "busy"
        primary.close()
        assert peer.run()["status"] == "ready"
        assert peer.paths.library_epoch == initial.library_epoch
        peer.close()
        _write(state, {"version": 1, "favorites": ["two"]})
        changed = primary.run()
        assert changed["status"] == "changed"
        assert sorted(changed["affected"]) == sorted([str(first), str(second)])
        assert primary.run("keep")["status"] == "ready"
        assert primary.paths.library_epoch == initial.library_epoch
        primary.close()
        _write(state, {"version": 1, "favorites": ["three"]})
        assert primary.run()["status"] == "changed"
        assert primary.run("import")["status"] == "ready"
        assert primary.paths.library_epoch != initial.library_epoch
        moved = primary.relocate(tmp_path / "new")
        assert (moved.library_epoch / "state.json").is_file() and state.is_file()
        assert load_document("S", moved.library_epoch / "state.json")["favorites"] == ["three"]
        assert load_document("S", initial.library_epoch / "state.json")["favorites"] == ["one"]
        assert peer.run()["status"] == "ready"
        assert peer.paths.library_root == legacy
        restored = primary.restore_backup(0)
        assert load_document("S", restored.library_epoch / "state.json")["favorites"] == ["one"]
    finally:
        peer.close()
        primary.close()


def test_ambiguous_write_keeps_the_pending_journal(tmp_path):
    data = _data(tmp_path / "data")
    game = tmp_path / "game"
    game.mkdir()
    target = game / "x.bundle"
    target.write_bytes(b"outside")
    folder = data / "library" / "transactions" / ("b" * 32)
    folder.mkdir(parents=True)
    backup = folder / "0.bundle"
    backup.write_bytes(b"before")
    sha1 = lambda raw: hashlib.sha1(raw).hexdigest()
    original = _write(
        folder / "journal.json",
        {
            "version": 1,
            "phase": "writing",
            "pin": {"root": str(game), "key": str(game), "dirs": [str(game)], "server": "global"},
            "files": [
                {
                    "target": str(target),
                    "before": sha1(b"before"),
                    "after": sha1(b"after"),
                    "official": False,
                    "rollback": str(backup.relative_to(data / "library")),
                    "kept_now": False,
                    "restore": None,
                }
            ],
            "old_applied": {str(target): None},
            "kept": [],
        },
    )
    upgrade = _open(data)
    try:
        assert upgrade.run()["reason"] == "recoveryPending"
        assert upgrade.paths is None
        namespace = data / "library" / ".bmpt-upgrade"
        head = parse_json((namespace.parent / "library.json").read_bytes())
        pinned = namespace / "library" / "g2" / head["active_epoch"] / original.relative_to(data / "library")
        assert load_document("T", pinned)["version"] == 2
        assert pinned.with_name("0.bundle").read_bytes() == b"before"
        assert load_document("T", original, generation=1)["version"] == 1
        assert target.read_bytes() == b"outside"
    finally:
        upgrade.close()


def _mod_record(key, names):
    return {
        "id": key,
        "files": sorted(names),
        "label": "Portrait Mod",
        "target": "test",
        "kind": "spr",
        "added": "2026-01-01T00:00:00+00:00",
        "seen": False,
        "user_added": True,
    }


def _write_payloads(folder, payloads):
    folder.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (folder / name).write_bytes(payload)


@pytest.mark.parametrize("boundary", ["preparing", "publishing", "published", "rollback"])
@pytest.mark.parametrize("repair", ["plain", "first-repair", "existing-repair"])
def test_import_journal_survives_upgrade_and_recovers(tmp_path, boundary, repair):
    from bmpt.catalog import Catalog
    from bmpt.library import ModLibrary

    data = _data(tmp_path / "data")
    root = data / "library"
    key = "1" * 32
    payloads = {"test_spr.bundle": b"imported portrait"}
    record = _mod_record(key, payloads)
    source_id = "" if repair == "plain" else "2" * 32
    previous_copy_id = "3" * 32 if repair == "existing-repair" else None
    published_files = boundary in ("published", "rollback")
    records = []
    if source_id:
        record["repaired_from"] = source_id
        source = _mod_record(source_id, payloads)
        link = key if published_files else previous_copy_id
        if link:
            source["repaired_id"] = link
        records.append(source)
        _write_payloads(root / "mods" / source_id, {"test_spr.bundle": b"source portrait"})
        if previous_copy_id:
            previous = {**_mod_record(previous_copy_id, payloads), "repaired_from": source_id}
            records.append(previous)
            _write_payloads(root / "mods" / previous_copy_id, {"test_spr.bundle": b"previous repair"})
    journal = {
        "version": 1,
        "id": key,
        "phase": "publishing" if boundary == "published" else boundary,
        "record": record,
        "hashes": {name: hashlib.sha1(payload).hexdigest() for name, payload in payloads.items()},
        "repaired_from": source_id,
        "old_repaired_id": previous_copy_id,
    }
    relative = Path("import-transactions") / key / "journal.json"
    source_journal = _write(root / relative, journal)
    if published_files:
        _write_payloads(root / "mods" / key, payloads)
        records.append(record)
    elif boundary == "publishing":
        _write_payloads(source_journal.parent / "files", payloads)
    legacy_state = _write(root / "state.json", {"version": 1, "mods": records})
    original_state = legacy_state.read_bytes()

    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        active = upgrade.paths.library_epoch
        staged = load_document("I", active / relative)
        assert staged["repair_source_id"] == source_id
        assert staged["old_repaired_copy_id"] == previous_copy_id
        assert ("repair_source_id" in staged["record"]) == bool(source_id)
        assert inverse_translate("I", staged) == journal

        ModLibrary(active, Catalog(None, None))

        assert not (active / relative).exists()
        contents = load_document("S", active / "state.json")
        remaining = {row["id"]: row for row in contents["mods"]}
        committed = boundary == "published"
        assert (key in remaining) == committed
        assert (active / "mods" / key).exists() == committed
        if source_id:
            assert remaining[source_id].get("repaired_copy_id") == (key if committed else previous_copy_id)
        assert source_journal.read_bytes() == json_bytes(journal)
        assert legacy_state.read_bytes() == original_state
    finally:
        upgrade.close()


@pytest.mark.parametrize("boundary", ["copying", "replacing", "published"])
def test_update_journal_survives_upgrade_and_recovers(tmp_path, boundary):
    from bmpt.catalog import Catalog
    from bmpt.library import ModLibrary

    data = _data(tmp_path / "data")
    root = data / "library"
    key, previous = "1" * 32, "2" * 32
    old = {"test_spr.bundle": b"old portrait"}
    new = {"test_mx_spr.bundle": b"new part", "test_spr.bundle": b"rebuilt portrait"}
    committed = boundary == "published"
    journal = {
        "version": 1,
        "operation": "update",
        "id": key,
        "phase": "copying" if boundary == "copying" else "replacing",
        "old": {name: hashlib.sha1(payload).hexdigest() for name, payload in old.items()},
        "new": {name: hashlib.sha1(payload).hexdigest() for name, payload in new.items()},
        "previous": previous,
    }
    relative = Path("import-transactions") / key / "journal.json"
    source_journal = _write(root / relative, journal)
    folder = root / "mods" / key
    _write_payloads(folder, old if boundary == "copying" else new)
    if boundary != "copying":
        _write_payloads(folder / "previous" / previous, old)
    expected = new if committed else old
    _write(root / "state.json", {"version": 1, "mods": [_mod_record(key, expected)]})

    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        active = upgrade.paths.library_epoch
        staged = load_document("I", active / relative)
        assert inverse_translate("I", staged) == journal

        ModLibrary(active, Catalog(None, None))

        assert not (active / relative).exists()
        assert load_document("S", active / "state.json")["mods"][0]["files"] == sorted(expected)
        saved = active / "mods" / key
        assert {path.name: path.read_bytes() for path in saved.glob("*.bundle")} == expected
        assert (saved / "previous" / previous).is_dir() == committed
        assert source_journal.read_bytes() == json_bytes(journal)
    finally:
        upgrade.close()


def test_import_journal_still_rejects_malformed_repair_source(tmp_path):
    from bmpt.host.upgrade_journals import verify_journals

    key = "1" * 32
    _write(
        tmp_path / "import-transactions" / key / "journal.json",
        {
            "version": 2,
            "id": key,
            "phase": "preparing",
            "record": {"id": key, "files": ["test.bundle"]},
            "hashes": {"test.bundle": "a" * 40},
            "repair_source_id": "invalid-id",
            "old_repaired_copy_id": None,
        },
    )
    with pytest.raises(InvalidStore, match="Invalid repair source"):
        verify_journals(tmp_path, {}, check_game=False)


@pytest.mark.parametrize("updated", [False, True])
def test_upgrade_hard_links_but_refuses_changed_official(tmp_path, monkeypatch, updated):
    from bmpt.library import storage
    from bmpt.library.original_checks import OriginalChecks
    from bmpt.library.service.originals import OriginalsMixin
    from bmpt.library.storage import Fingerprints

    data = _data(tmp_path / "data")
    source = data / "library" / "originals" / "original.bundle"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"official")
    game = tmp_path / "game"
    game.mkdir()
    live = game / source.name
    live.write_bytes(b"existing")
    monkeypatch.setattr(upgrade_files, "native_clone", lambda *_: False)
    coordinator = DataUpgrade(data, qualify=lambda _: "ntfs", updated_in_app=updated)
    try:
        state = coordinator.run()
        assert state["status"] == "ready"
        assert state["notice"] == (None if updated else "upgraded")
        root = coordinator.paths.library_epoch
        original = root / "originals" / source.name
        assert original.stat().st_ino == source.stat().st_ino

        class Reader(OriginalsMixin):
            pass

        reader = Reader()
        reader.root = root
        reader._hashes = Fingerprints()
        reader._original_checks = OriginalChecks(root, reader._hashes)
        reader._steam_official = lambda *_args, **_kwargs: None
        reader._crc_official = lambda *_: True
        pin = SimpleNamespace(dirs=[game])
        checked = []
        read_header = storage.Bundle.get_trailing_bytes

        def read(path):
            checked.append(path)
            return read_header(path)

        monkeypatch.setattr(storage.Bundle, "get_trailing_bytes", staticmethod(read))
        assert reader._official_for(live, pin, full=False) == original
        staged = tmp_path / "verified-input"
        reader._copy_verified_official(original, staged)
        assert reader._hashes.get(staged).sha1 == reader._hashes.get(original).sha1
        if os.name != "nt":  # creation time cannot attest unchanged bytes on Windows
            assert checked == [original, staged]
        original_stat = original.stat()
        source.write_bytes(b"modified")
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert original.stat().st_size == original_stat.st_size
        assert original.stat().st_mtime_ns == original_stat.st_mtime_ns
        for full in (False, True):
            with pytest.raises(ServiceError, match="official_invalid"):
                reader._official_for(live, pin, full=full)
        destination = tmp_path / "build-input"
        with pytest.raises(ServiceError, match="official_invalid"):
            reader._copy_verified_official(original, destination)
        if os.name != "nt":
            assert checked == [original, staged, original]
        assert not destination.exists()
        assert live.read_bytes() == b"existing"
    finally:
        coordinator.close()


def test_relocation_adoption_pins_verified_payloads_without_a_prompt(tmp_path, monkeypatch):
    data = _data(tmp_path / "data")
    target = tmp_path / "target"
    old = target / "originals" / "kept.bundle"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"kept")
    monkeypatch.setattr(upgrade_files, "native_clone", lambda *_: False)
    coordinator = DataUpgrade(data, qualify=lambda _: "ntfs")
    try:
        assert coordinator.run()["status"] == "ready"
        paths = coordinator.relocate(target, adopt=True)
        kept = paths.library_epoch / "originals" / old.name
        assert kept.read_bytes() == b"kept"
        assert kept.stat().st_ino == old.stat().st_ino
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    ("owner", "format_version", "minimum"),
    [
        ("data", "3.0", "2.0"),
        ("library", "3.0", "2.0"),
        ("library", "2.2", "2.2"),
    ],
)
def test_newer_manifest_refuses_before_creating_any_files(tmp_path, owner, format_version, minimum):
    from bmpt.host.format_manifest import FORMAT_NAMES

    data = _data(tmp_path / "data")
    root = data if owner == "data" else data / "library"
    manifest = _write(
        root / f"{owner}.json",
        {
            "format": FORMAT_NAMES[owner],
            "format_version": format_version,
            "min_reader_version": minimum,
        },
    )
    before = {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()}
    coordinator = _open(data)
    assert coordinator.run()["reason"] == "newerFormat"
    assert coordinator.state()["phase"] == owner
    assert coordinator.paths is None
    assert manifest.is_file()
    assert not (data / ".bmpt-upgrade").exists()
    assert {path.relative_to(data): path.read_bytes() for path in data.rglob("*") if path.is_file()} == before


def _matches_schema(value, schema):
    """Evaluate the Draft 2020-12 vocabulary used by the checked-in store schemas."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if type(value) not in [types[name] for name in allowed]:
            return False
    if "anyOf" in schema and not any(_matches_schema(value, child) for child in schema["anyOf"]):
        return False
    if "oneOf" in schema and sum(_matches_schema(value, child) for child in schema["oneOf"]) != 1:
        return False
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "pattern" in schema and not re.search(schema["pattern"], value):
        return False
    if "minimum" in schema and value < schema["minimum"]:
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        extras = schema.get("additionalProperties", {})
        return all(key in value for key in schema.get("required", [])) and all(
            _matches_schema(item, properties.get(key, extras)) for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_matches_schema(item, schema.get("items", {})) for item in value)
    return True


def _catalog_document(payload):
    sha1 = hashlib.sha1(payload).hexdigest()
    return {
        "version": 1,
        "installs": {
            "game": {
                "mode": "catalog",
                "reason": None,
                "migrated": None,
                "rv": "a" * 40,
                "official": {"sha1": sha1, "size": len(payload), "crc32": 0, "mtime": 1},
                "mods": None,
                "live": "official",
                "redirects": {},
            }
        },
    }


def test_catalog_stores_are_preserved_and_fenced(tmp_path, monkeypatch):
    data = _data(tmp_path / "data")
    library = data / "library"
    payload = ccdb.write(synthetic_catalog())
    document = _catalog_document(payload)
    tree = Path("catalogs") / ("b" * 12) / ("a" * 40 + ".bytes")
    official = library / tree
    official.parent.mkdir(parents=True)
    official.write_bytes(payload)
    path = library / "catalogs.json"
    atomic_json(path, document)
    assert store_formats.read_runtime(path) == document
    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        epoch = upgrade.paths.library_epoch
        assert (epoch / tree).read_bytes() == payload
        assert store_formats.runtime_store(epoch / "catalogs.json") == "CS"
        assert store_formats.read_runtime(epoch / "catalogs.json") == document
        manifest = parse_json((library / "library.json").read_bytes())
        assert manifest["format_version"] == manifest["min_reader_version"] == "2.1"
        for name, relative in (("catalog_state", "catalogs.json"), ("catalogs", "catalogs")):
            assert manifest["stores"][name] == {
                "path": (epoch / relative).relative_to(library).as_posix(),
                "version": "1.0",
            }
        assert parse_json((data / "data.json").read_bytes())["min_reader_version"] == "2.0"
    finally:
        upgrade.close()
    monkeypatch.setattr(format_manifest, "FORMAT_VERSION", "2.0")
    assert _open(data).run()["reason"] == "newerFormat"


def test_old_library_manifest_stays_readable_until_catalog_stores_are_fenced(tmp_path):
    data = _data(tmp_path / "data")
    root = data / "library"
    upgrade = _open(data)
    try:
        assert upgrade.run()["status"] == "ready"
        epoch = upgrade.paths.library_epoch
    finally:
        upgrade.close()
    path = root / "library.json"
    manifest = parse_json(path.read_bytes())
    manifest["format_version"] = manifest["min_reader_version"] = "2.0"
    del manifest["stores"]["catalogs"], manifest["stores"]["catalog_state"]
    _write(path, manifest)
    old_head = path.read_bytes()
    assert not (epoch / "catalogs.json").exists()
    try:
        assert upgrade.run()["status"] == "ready"
        assert path.read_bytes() == old_head
        format_manifest.fence_catalog_stores(epoch)
        fenced = parse_json(path.read_bytes())
        assert fenced["format_version"] == fenced["min_reader_version"] == "2.1"
        assert fenced["active_epoch"] == manifest["active_epoch"]
        assert fenced["sequence"] == manifest["sequence"] + 1
        assert fenced["predecessor"] == hashlib.sha256(old_head).hexdigest()
        assert not (epoch / "catalogs.json").exists()
    finally:
        upgrade.close()


def test_store_schemas_validate_upgrade_fixtures(tmp_path):
    schemas = Path(__file__).parents[1] / "docs" / "webui" / "schemas"
    data = _data(tmp_path / "data")
    library = data / "library"
    coordinator = _open(data)
    assert coordinator.run()["status"] == "ready"
    coordinator.close()
    key, sha = "a" * 32, "b" * 40
    fixtures = {
        "library-v2": parse_json((library / "library.json").read_bytes()),
        "data-v2": parse_json((data / "data.json").read_bytes()),
        "config-v1": {"AppSettings": {"language": "en-US"}},
        "preferences-v2": {"prefs": {"student_list": {"group_alts": True}}},
        "state-v2": {"version": 2, "mods": [], "applied": {}},
        "profiles-v2": {"version": 2, "folders": {"game": {"configured": {"core": {"source": "alt:fixture"}}}}},
        "thumbnails-v2": {"version": 2, "thumbnails": {}, "selected": {}, "next": 1},
        "original_checks-v2": {"version": 2, "officials": {}, "digests": {"kept.bundle": "b" * 64}},
        "catalogs-v1": _catalog_document(ccdb.write(synthetic_catalog())),
        "rigs-v1": {"version": 1, "rigs": {}, "stamps": {}},
        "build_fingerprints-v1": {"version": 1, "parts": [], "files": {"kept.bundle": sha}},
        "transactions-v2": {"version": 2, "phase": "writing", "pin": {}, "files": [], "kept": [], "old_applied": {}},
        "import_transactions-v2": {
            "version": 2,
            "phase": "preparing",
            "id": key,
            "record": {},
            "hashes": {"kept.bundle": sha},
        },
        "asset_deletions-v2": {
            "version": 2,
            "phase": "active",
            "records": {},
            "applied": {},
            "profiles": None,
            "mods": [],
            "built": [],
            "games": [],
        },
    }
    assert {path.name.removesuffix(".schema.json") for path in schemas.glob("*.schema.json")} == fixtures.keys()
    for name, fixture in fixtures.items():
        schema = parse_json((schemas / f"{name}.schema.json").read_bytes())
        assert _matches_schema(fixture, schema), name
        assert not _matches_schema([], schema), name


def test_compatible_minor_manifest_extensions_survive_commit(tmp_path):
    data = _data(tmp_path / "data")
    library = data / "library"
    upgrade = _open(data)
    assert upgrade.run()["status"] == "ready"
    upgrade.close()
    for path in (data / "data.json", library / "library.json"):
        manifest = parse_json(path.read_bytes())
        manifest["format_version"] = "2.2"
        manifest["extension"] = {"data": [1, None]}
        next(iter(manifest["stores"].values()))["extension"] = {"flag": True}
        _write(path, manifest)
    _write(data / "webui.json", {"prefs": {"data_id": "0123456789ab", "theme": "dark"}})
    try:
        assert upgrade.run()["status"] == "changed"
        assert upgrade.run("import")["status"] == "ready"
        for path in (data / "data.json", library / "library.json"):
            manifest = parse_json(path.read_bytes())
            assert manifest["format_version"] == "2.2"
            assert manifest["extension"] == {"data": [1, None]}
            assert next(iter(manifest["stores"].values()))["extension"] == {"flag": True}
    finally:
        upgrade.close()


def test_an_older_windows_updater_restart_is_recognized_without_the_flag(tmp_path, monkeypatch):
    from bmpt.host import updater

    program = tmp_path / "BMPT.exe"
    program.write_bytes(b"new build")
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_program_path", lambda: program)
    monkeypatch.setattr(updater, "_has_older_neighbor", lambda _: False)
    monkeypatch.delenv("BAMT_UPDATE_APPLIED", raising=False)
    monkeypatch.setenv("BAMT_UPDATE_EXE", str(program))
    monkeypatch.setenv("BAMT_UPDATE_MD5", updater.md5_of(program).upper())
    assert updater.consume_upgrade_handoff() is True
    assert updater.consume_upgrade_handoff() is False
    monkeypatch.setenv("BAMT_UPDATE_EXE", str(program))
    monkeypatch.setenv("BAMT_UPDATE_MD5", "0" * 32)
    assert updater.consume_upgrade_handoff() is False


def test_update_restart_requires_no_older_neighbor(tmp_path, monkeypatch):
    import plistlib

    from bmpt.host import updater

    app = tmp_path / "Blue Archive Modding.app"
    app.mkdir()
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater, "_program_path", lambda: app)
    monkeypatch.setattr(updater, "current_version", lambda: "0.7.0")
    monkeypatch.setenv("BAMT_UPDATE_APPLIED", "1")
    assert updater.consume_upgrade_handoff() is True
    assert updater.consume_upgrade_handoff() is False
    info = tmp_path / "Renamed app.app" / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(
        plistlib.dumps({"CFBundleName": "Blue Archive Modding Tool", "CFBundleShortVersionString": "0.6.0"})
    )
    monkeypatch.setenv("BAMT_UPDATE_APPLIED", "1")
    assert updater.consume_upgrade_handoff() is False
