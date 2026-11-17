"""Upgrade synthetic legacy records without opening an application or real library."""

import copy
import json
import logging
from types import SimpleNamespace

import pytest

from ba_modding_toolkit.i18n import I18n
from bmpt.library import ModLibrary, service
from bmpt.library.photographs import Photographs
from bmpt.library.profiles import Profiles
from bmpt.webui.state import UiState
from bmpt.library.migrations import (
    API_NOUNS,
    LEGACY_REMAP,
    migrate_journal_names,
    migrate_photo_names,
    migrate_profile_names,
    migrate_state_names,
    migrate_ui_names,
)
from ba_modding_toolkit.bundle import Bundle
from bmpt.core.errors import ServiceError

from test_library import game_bundle, reload_library, setup_library
from module_patches import patch_everywhere


OLD_SUFFIXES = {
    "en-US": " (remapped)",
    "zh-CN": "\uff08\u5df2\u91cd\u6392\uff09",
    "zh-TW": "\uff08\u5df2\u91cd\u6392\uff09",
    "ja-JP": "\uff08\u518d\u914d\u7f6e\u6e08\u307f\uff09",
    "ko-KR": " (\uc7ac\ubc30\uce58 \uc644\ub8cc)",
}


def legacy_record(
    root,
    number,
    label="test_spr",
    *,
    core="test_spr",
    target="test",
    kind="spr",
    date="2026-01-01",
    parts=("002",),
    added="2026-10-01T08:00:00+00:00",
):
    mod_id = f"{number:032x}"
    files = [
        game_bundle(root / "mods" / mod_id, core, part=part, date=date, payload=f"paint {number} {part}".encode()).name
        for part in parts
    ]
    return {
        "id": mod_id,
        "files": sorted(files),
        "label": label,
        "target": target,
        "kind": kind,
        "added": added,
        "seen": False,
    }


def write_legacy_state(root, records, **extra):
    state = {"version": 1, "mods": records, "favorites": ["test"], "applied": {}, **extra}
    path = root / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


@pytest.mark.parametrize("stored_language", OLD_SUFFIXES)
@pytest.mark.parametrize("language", OLD_SUFFIXES)
def test_legacy_pair_uses_current_repair_names_and_metadata_once(setup_library, monkeypatch, stored_language, language):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(
        env.root,
        2,
        "test_spr" + OLD_SUFFIXES[stored_language],
        date="2026-10-01",
        parts=("002", "003"),
        added="2026-10-01T09:00:00+00:00",
    )
    applied = {str(env.original): {"source": f"mod:{repaired['id']}", "sha1": "kept"}}
    path = write_legacy_state(env.root, [original, repaired], applied=applied)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in env.tmp.rglob("*.bundle")}
    translate = I18n(language).t
    patch_everywhere(monkeypatch, [service], "t", translate)
    monkeypatch.setattr(Bundle, "load", lambda *a, **kw: pytest.fail("Migration must not load bundles"))
    library = reload_library(env)
    mods = {m.id: m for m in library.mods()}
    assert mods[original["id"]].repaired_id == repaired["id"]
    fixed = mods[repaired["id"]]
    assert fixed.repaired_from == original["id"]
    assert fixed.label == translate("webui.remap.name", name="test_spr")
    assert fixed.to_json()["repaired_from"] == original["id"]
    assert not fixed.user_added
    assert len({m.repaired_from or m.id for m in mods.values()}) == 1
    for mod_id in mods:
        with pytest.raises(ServiceError, match="entry_changed"):
            library.remap(mod_id)
    monkeypatch.setattr(library, "_check_group", lambda *a, **kw: {"remappable": True})
    assert all(not library.compatibility(mod_id)["remappable"] for mod_id in mods)
    saved = json.loads(path.read_text())
    assert saved["migrations"] == [LEGACY_REMAP]
    assert saved["applied"] == applied and saved["favorites"] == ["test"]
    assert [m["files"] for m in saved["mods"]] == [original["files"], repaired["files"]]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in env.tmp.rglob("*.bundle")} == before
    contents = path.read_bytes()
    patch_everywhere(monkeypatch, [service], "legacy_remapped", lambda _: pytest.fail("Migration must run once"))
    assert {m.id: m for m in reload_library(env).mods()} == mods
    assert path.read_bytes() == contents
    library.rename(original["id"], "Original alias")
    assert next(m for m in reload_library(env).mods() if m.id == fixed.id).label == translate(
        "webui.remap.name", name="Original alias"
    )
    library.rename(original["id"], "")
    library.mark_seen(fixed.id)
    assert json.loads(path.read_text())["migrations"] == [LEGACY_REMAP]


@pytest.mark.parametrize("missing", ["record", "files"])
def test_deleted_original_leaves_a_tagged_standalone_repair(setup_library, missing):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(env.root, 2, "test_spr (remapped)")
    if missing == "files":
        for name in original["files"]:
            (env.root / "mods" / original["id"] / name).unlink()
    write_legacy_state(env.root, [repaired] if missing == "record" else [original, repaired])
    library = reload_library(env)
    (fixed,) = library.mods()
    assert fixed.repaired_from and fixed.repaired_from != fixed.id
    assert not fixed.repaired_id
    assert fixed.label == service.t("webui.remap.name", name="test_spr")
    with pytest.raises(ServiceError, match="entry_changed"):
        library.remap(fixed.id)
    assert reload_library(env).mods() == [fixed]


@pytest.mark.parametrize("mismatch", ["core", "target", "kind", "added", "ambiguous"])
def test_legacy_link_requires_unique_name_core_variant_kind_and_earlier_source(setup_library, mismatch):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(env.root, 2, "test_spr (remapped)")
    if mismatch == "core":
        original = legacy_record(env.root, 1, core="other_spr")
    elif mismatch == "kind":
        original = legacy_record(env.root, 1, core="test_home", kind="lobby")
    elif mismatch == "target":
        original["target"] = "other"
    elif mismatch == "added":
        original["added"] = "2026-10-02T08:00:00+00:00"
    records = [original, repaired]
    if mismatch == "ambiguous":
        records.append(legacy_record(env.root, 3))
    write_legacy_state(env.root, records)
    library = reload_library(env)
    mods = {m.id: m for m in library.mods()}
    fixed = mods[repaired["id"]]
    assert fixed.repaired_from not in mods
    assert all(not m.repaired_id for m in mods.values())
    assert len({m.repaired_from or m.id for m in mods.values()}) == len(records)


def test_migration_preserves_modern_metadata_and_ordinary_names(setup_library):
    env = setup_library
    original = legacy_record(env.root, 1)
    modern = legacy_record(env.root, 2, "test_spr (remapped)")
    modern.update(repaired_from=original["id"], alias="Own alias")
    original["repaired_id"] = modern["id"]
    records = [
        original,
        modern,
        legacy_record(env.root, 3, "test_spr (remapped) extra"),
        legacy_record(env.root, 4, "test_spr"),
        legacy_record(env.root, 5, "test_spr (remapped)"),
    ]
    records[-1]["user_added"] = True
    before = copy.deepcopy(records)
    path = write_legacy_state(env.root, records)
    reload_library(env)
    assert json.loads(path.read_text())["mods"] == before


def test_multiple_legacy_repairs_share_the_source_and_keep_an_existing_jump(setup_library):
    env = setup_library
    original = legacy_record(env.root, 1)
    repairs = [legacy_record(env.root, n, "test_spr (remapped)") for n in (2, 3)]
    original["repaired_id"] = repairs[1]["id"]
    write_legacy_state(env.root, [original, *repairs])
    mods = {m.id: m for m in reload_library(env).mods()}
    assert mods[original["id"]].repaired_id == repairs[1]["id"]
    assert all(mods[r["id"]].repaired_from == original["id"] for r in repairs)
    assert len({m.repaired_from or m.id for m in mods.values()}) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_repair_chains_are_independent_of_record_order(setup_library, reverse):
    env = setup_library
    original = legacy_record(env.root, 1)
    original["alias"] = "Original alias"
    first = legacy_record(env.root, 2, "test_spr (remapped)")
    second = legacy_record(env.root, 3, "test_spr (remapped)" + OLD_SUFFIXES["zh-CN"])
    first["repaired_id"] = second["id"]
    records = [original, first, second]
    write_legacy_state(env.root, list(reversed(records)) if reverse else records)
    library = reload_library(env)
    mods = {m.id: m for m in library.mods()}
    assert all(mods[r["id"]].repaired_from == original["id"] for r in (first, second))
    assert len({m.repaired_from or m.id for m in mods.values()}) == 1
    assert all(mods[r["id"]].label == service.t("webui.remap.name", name="Original alias") for r in (first, second))
    library.remove(original["id"])
    assert all(m.label == service.t("webui.remap.name", name="Original alias") for m in reload_library(env).mods())


def test_existing_empty_instance_migrates_legacy_state_on_refresh(setup_library):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(env.root, 2, "test_spr (remapped)")
    path = write_legacy_state(env.root, [original, repaired])
    mods = {m.id: m for m in env.library.mods()}
    assert mods[repaired["id"]].repaired_from == original["id"]
    assert json.loads(path.read_text())["migrations"] == [LEGACY_REMAP]


def test_done_marker_survives_other_instance_saves_and_skips_later_similar_names(setup_library):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(env.root, 2, "test_spr (remapped)")
    write_legacy_state(env.root, [original, repaired], migrations=["another_upgrade"])
    stale = reload_library(env)
    other = reload_library(env)
    other.rename(original["id"], "Alias from another instance")
    stale.set_favorite("other", True)
    path = env.root / "state.json"
    state = json.loads(path.read_text())
    assert set(state["migrations"]) == {"another_upgrade", LEGACY_REMAP}
    assert state["mods"][0]["alias"] == "Alias from another instance"
    later = legacy_record(env.root, 3, "test_spr (remapped)")
    state["mods"].append(later)
    path.write_text(json.dumps(state))
    assert not next(m for m in reload_library(env).mods() if m.id == later["id"]).repaired_from


def test_failed_migration_write_keeps_disk_and_memory_retryable(setup_library, monkeypatch):
    env = setup_library
    original = legacy_record(env.root, 1)
    repaired = legacy_record(env.root, 2, "test_spr (remapped)")
    path = write_legacy_state(env.root, [original, repaired])
    before = path.read_bytes()
    save = service.atomic_json
    with monkeypatch.context() as patch:
        patch_everywhere(patch, [service], "atomic_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        library = reload_library(env)
        assert path.read_bytes() == before
        assert not library._records[repaired["id"]].get("repaired_from")
    assert service.atomic_json is save
    assert next(m for m in library.mods() if m.id == repaired["id"]).repaired_from == original["id"]
    assert json.loads(path.read_text())["migrations"] == [LEGACY_REMAP]


PHOTO = "a" * 32


def _document(store):
    if store == "photo":
        return {
            "version": 1,
            "next": 2,
            "next_by_variant": {"hoshino": 2},
            "selected": {"hoshino": PHOTO},
            "photos": {
                PHOTO: {
                    "id": PHOTO,
                    "variant": "hoshino",
                    "expression": "02",
                    "source": "variant:hoshino_swimsuit",
                    "sha256": "d" * 64,
                }
            },
        }
    if store == "ui":
        return {
            "asset_previews": {"install": {"hoshino": {"spr": "variant:hoshino_swimsuit"}}},
            "prefs": {"student_hall": {"portraits": [{"variant": "hoshino", "source": "official"}]}},
            "aliases": {"variant": ["family", "expression"]},
        }
    if store == "profiles":
        entry = {"variant": "hoshino", "kind": "spr", "source": "variant:hoshino_swimsuit"}
        return {
            "version": 1,
            "folders": {"install": {"configured": {"hoshino_spr": entry}, "built": {"core": dict(entry)}}},
            "unfinished": [{"install": "install", "core": "core", "why": "pending", **entry}],
            "troubleshoot": {"folders": {"install": {"core": dict(entry)}}},
        }
    return {
        "version": 1,
        "mods": [
            {
                "id": "b" * 32,
                "files": ["missing.bundle"],
                "target": "hoshino",
                "label": "Camera",
                "kind": "other",
                "added": "2026-01-01",
                "seen": False,
                "lobby_camera": {"variant": "hoshino", "source": "official"},
                "roster_photography": {"choices": {"texture": {"variant": "hoshino", "photo": PHOTO}}},
                "optimized_from": "variant:hoshino_swimsuit",
            }
        ],
        "favorites": [],
        "applied": {"path": {"source": "variant:hoshino_swimsuit", "sha1": "d" * 40}},
        "migrations": sorted([LEGACY_REMAP, API_NOUNS]),
    }


MIGRATORS = {
    "photo": migrate_photo_names,
    "ui": migrate_ui_names,
    "profiles": migrate_profile_names,
    "state": migrate_state_names,
}


def _path(root, store):
    return (
        root
        / {"photo": "photographs/index.json", "ui": "webui.json", "profiles": "profiles.json", "state": "state.json"}[
            store
        ]
    )


def _load(root, store):
    if store == "photo":
        return Photographs(root).list("hoshino")
    if store == "ui":
        return UiState(root / "webui.json")._current()
    if store == "profiles":
        return Profiles(root).read()
    return ModLibrary(root, SimpleNamespace())._held


@pytest.mark.parametrize("store", MIGRATORS)
def test_old_store_rewrites_once_and_detects_reintroduced_names(tmp_path, caplog, store):
    path = _path(tmp_path, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = _document(store)
    raw = json.dumps(old, separators=(",", ":")).encode()
    path.write_bytes(raw)
    expected = copy.deepcopy(old)
    assert MIGRATORS[store](expected)
    if store == "photo":
        assert expected["next_by_alt"] == {"hoshino": 2}
        assert expected["photos"][PHOTO]["face"] == "02"
        assert expected["photos"][PHOTO]["alt"] == "hoshino"
    elif store == "ui":
        assert expected["prefs"]["student_hall"]["portraits"][0]["alt"] == "hoshino"
    elif store == "profiles":
        assert expected["folders"]["install"]["configured"]["hoshino_spr"]["alt"] == "hoshino"
        assert expected["troubleshoot"]["folders"]["install"]["core"]["alt"] == "hoshino"
    else:
        assert expected["mods"][0]["lobby_camera"]["alt"] == "hoshino"
        assert expected["mods"][0]["roster_photography"]["choices"]["texture"]["alt"] == "hoshino"
        assert expected["mods"][0]["optimized_from"] == "alt:hoshino_swimsuit"
    assert "variant:" not in json.dumps(expected)
    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, store)
    assert result is not None
    assert json.loads(path.read_bytes()) == expected
    migrated = path.read_bytes()
    assert migrated != raw
    if store != "photo":
        assert path.with_name(path.name + ".prev").read_bytes() == raw
    assert sum("Migrated API names in" in record.message for record in caplog.records) == 1
    _load(tmp_path, store)
    assert path.read_bytes() == migrated
    path.write_bytes(raw)
    _load(tmp_path, store)
    assert json.loads(path.read_bytes()) == expected


@pytest.mark.parametrize("store", MIGRATORS)
def test_current_store_is_byte_identical_across_loads(tmp_path, store):
    data = _document(store)
    assert MIGRATORS[store](data)
    assert not MIGRATORS[store](data)
    if store == "state":
        data["migrations"].remove(API_NOUNS)
    path = _path(tmp_path, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=4).encode()
    path.write_bytes(raw)
    for _ in range(2):
        _load(tmp_path, store)
        assert path.read_bytes() == raw
    assert not path.with_name(path.name + ".prev").exists()


def test_journal_snapshots_migrate_without_changing_identity_maps():
    data = {
        "record": _document("state")["mods"][0],
        "records": {"id": _document("state")["mods"][0]},
        "applied": _document("state")["applied"],
        "old_applied": _document("state")["applied"],
        "profiles": _document("profiles"),
        "photo": {"variant": "hoshino", "entry": _document("photo")["photos"][PHOTO]},
    }
    assert migrate_journal_names(data)
    assert data["record"]["lobby_camera"]["alt"] == "hoshino"
    assert data["records"]["id"]["optimized_from"] == "alt:hoshino_swimsuit"
    assert data["profiles"]["folders"]["install"]["configured"]["hoshino_spr"]["alt"] == "hoshino"
    assert data["photo"]["entry"]["face"] == "02"
    assert not migrate_journal_names(data)


def test_alias_ids_and_animation_names_are_preserved():
    data = _document("ui")
    assert migrate_ui_names(data)
    assert data["aliases"] == {"variant": ["family", "expression"]}
    data = {"photos": {PHOTO: {"variant": "hoshino", "action": "expression", "expression": "02"}}}
    assert migrate_photo_names(data)
    assert data["photos"][PHOTO] == {"alt": "hoshino", "action": "expression", "face": "02"}


@pytest.mark.parametrize("folder", ["import-transactions", "transactions", "asset-deletions"])
@pytest.mark.parametrize("legacy", [True, False])
def test_recovery_journal_files_rewrite_only_when_names_are_present(tmp_path, folder, legacy, caplog):
    path = tmp_path / folder / "entry" / "journal.json"
    path.parent.mkdir(parents=True)
    data = {
        "record": _document("state")["mods"][0],
        "records": {"id": _document("state")["mods"][0]},
        "old_applied": _document("state")["applied"],
        "profiles": _document("profiles"),
        "photo": {"variant": "hoshino", "entry": _document("photo")["photos"][PHOTO]},
    }
    expected = copy.deepcopy(data)
    assert migrate_journal_names(expected)
    raw = json.dumps(data if legacy else expected, indent=4).encode()
    path.write_bytes(raw)
    with caplog.at_level(logging.INFO):
        for load in range(2):
            loaded = json.loads(path.read_bytes())
            assert migrate_journal_names(loaded, path) is (legacy and load == 0)
            assert loaded == expected
            if load == 0:
                migrated = path.read_bytes()
            assert path.read_bytes() == migrated
    assert (migrated != raw) is legacy
    assert sum("Migrated API names in" in record.message for record in caplog.records) == int(legacy)


def test_photograph_counter_maps_keep_every_alt_and_the_highest_sequence(tmp_path):
    data = _document("photo")
    data["next_by_alt"] = {"hoshino": 4, "hina": 8}
    data["next_by_variant"]["shiroko"] = 3
    path = _path(tmp_path, "photo")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    _load(tmp_path, "photo")
    migrated = path.read_bytes()
    assert json.loads(migrated)["next_by_alt"] == {"hoshino": 4, "hina": 8, "shiroko": 3}
    assert "next_by_variant" not in json.loads(migrated)
    _load(tmp_path, "photo")
    assert path.read_bytes() == migrated


def test_old_field_written_by_an_older_build_wins_over_the_new_one(tmp_path):
    data = _document("photo")
    data["photos"][PHOTO]["alt"] = "hina"
    path = _path(tmp_path, "photo")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    _load(tmp_path, "photo")
    photo = json.loads(path.read_text())["photos"][PHOTO]
    assert photo["alt"] == "hoshino"
    assert "variant" not in photo
