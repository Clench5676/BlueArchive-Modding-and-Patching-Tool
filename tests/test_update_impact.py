"""What a game update did under each library mod: the classification from the fingerprint it was built against, the
summary of an install's update (once per resource version) and the hold of entries whose rig changed (BL-159)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bmpt.library import fingerprint

from test_atlas_remap import atlas_text  # noqa: F401
from test_built_against import MOVED, OLD, animation, binary_skeleton, part_of, spine  # noqa: F401
from test_library import game_bundle, header_bundle, make_mod, reload_library, setup_library  # noqa: F401


# --- the classification ---

def document(*parts, complete=True):
    return fingerprint.document("import", [dict(part) for part in parts], complete=complete)


def named(part, name):
    return {**part, "name": name}


def test_the_same_files_are_unchanged():
    built = document(named(part_of(), "a_1.bundle"))
    assert fingerprint.impact(built, document(named(part_of(), "a_1.bundle"))) == fingerprint.UNCHANGED


def test_a_part_rebuilt_with_the_same_rig():
    built = document(named(part_of(), "a_1.bundle"))
    # another CRC, other skeleton bytes of the same bones, slots and animations
    now = named(part_of(structure="s2"), "a_2.bundle")
    now["skeletons"] = {"test.skel": {**now["skeletons"]["test.skel"], "sha1": "f" * 40}}
    assert fingerprint.impact(built, document(now)) == fingerprint.REBUILT


@pytest.mark.parametrize("change", ["layout", "animations", "texture", "gone_asset", "unread_skeleton"])
def test_a_part_rebuilt_with_another_rig(change):
    built = document(named(part_of(), "a_1.bundle"))
    now = {"layout": lambda: part_of(MOVED), "animations": lambda: part_of(animations=("Idle_01", "00")),
           "texture": lambda: part_of(size=(32, 16)), "gone_asset": lambda: {**part_of(), "textures": {}},
           "unread_skeleton": lambda: {**part_of(), "skeletons": {"test.skel": {
               "sha1": "f" * 40, "version": "4.2.33", "rig": None, "animations": None}}}}[change]()
    assert fingerprint.impact(built, document(named(now, "a_2.bundle"))) == fingerprint.RIG_CHANGED


def test_the_changes_name_what_an_update_did_to_the_official_parts():
    built = document(named(part_of(), "a_1.bundle"))
    # repacked, a new animation, another texture size: the summary names each
    now = named(part_of(MOVED, animations=("Idle_01", "00"), size=(32, 16)), "a_2.bundle")
    assert fingerprint.changes(built, document(now)) == {"repacked": True, "animations": 1, "textures": True}
    # the same rig rebuilt changes nothing the summary names
    same = named(part_of(structure="s2"), "a_2.bundle")
    assert fingerprint.changes(built, document(same)) == {}
    # an asset gone, a new one beside it
    swapped = named({**part_of(), "textures": {"page2": [8, 8]}}, "a_2.bundle")
    assert fingerprint.changes(built, document(swapped)) == {"removed": True, "added": True}
    unread = named({**part_of(), "skeletons": {"test.skel": {
        "sha1": "f" * 40, "version": "4.2.33", "rig": None, "animations": None}}}, "a_2.bundle")
    assert fingerprint.changes(built, document(unread)) == {"rig": True}
    assert fingerprint.changes(built, None) == {"gone": True}
    assert fingerprint.changes(None, document(now)) == fingerprint.changes(built, {"parts": []}) == {}


def test_new_official_parts_beside_the_ones_the_mod_knows():
    built = document(named(part_of(), "a_1.bundle"))
    extra = {"name": "b_1.bundle", "sha1": None, "structure": "x", "atlases": {}, "skeletons": {},
             "textures": {"page2": [8, 8]}}
    now = document(named(part_of(), "a_1.bundle"), extra)
    assert fingerprint.impact(built, now) == fingerprint.NEW_PARTS
    # a fingerprint that does not know every part then cannot tell a new one
    assert fingerprint.impact(document(named(part_of(), "a_1.bundle"), complete=False), now) == fingerprint.UNCHANGED


def test_no_baseline_tells_nothing_and_no_part_left_is_gone():
    now = document(named(part_of(), "a_1.bundle"))
    assert fingerprint.impact(None, now) == fingerprint.UNKNOWN
    assert fingerprint.impact(document(named(part_of(), "a_1.bundle")), None) == fingerprint.GONE
    assert fingerprint.impact(document(named(part_of(), "a_1.bundle")), {"parts": []}) == fingerprint.UNKNOWN


# --- the library ---

def updated(env, assets, payload=b"updated official"):
    """A game update of the core: the official part replaced by a later release holding ``assets``."""
    env.original.unlink()
    env.assets[header_bundle(payload)] = assets
    return game_bundle(env.game_dir, date="2026-02-01", payload=payload)


def configured(env):
    entry, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.library.apply("test", f"mod:{entry.id}")
    return entry


def key(env):
    return env.library.current_pin().key


def unfinished(env):
    return json.loads((env.root / "profiles.json").read_text())["unfinished"]


def test_the_summary_of_an_update_names_the_mods_whose_rig_changed(spine):
    env = spine
    entry = configured(env)
    first = env.library.update_impact()
    assert first["due"] is False and first["counts"]["unchanged"] == 1  # nothing to tell: seen at once
    assert env.library.update_impact()["counts"]["unchanged"] == 0  # the same version is not classified again
    updated(env, (MOVED, ("Idle_01", "00")))
    library = reload_library(env)
    summary = library.update_impact()
    assert summary["due"] is True and summary["previous"] == first["version"] != summary["version"]
    assert summary["mods"] == [{"id": entry.id, "label": summary["mods"][0]["label"], "target": "test",
                                "kind": "spr", "core": "test_spr", "impact": "rig_changed",
                                "changes": {"repacked": True, "animations": 1}, "configured": True,
                                "repaired_id": ""}]
    assert summary["counts"]["rig_changed"] == 1
    assert library.update_impact()["due"] is True  # told until the page says it was shown
    library.impact_seen(summary["install"], summary["version"])
    assert library.update_impact()["due"] is False
    assert library.update_impact(force=True)["mods"][0]["impact"] == "rig_changed"
    assert library.impacts([entry.id, "nope"]) == [{"id": entry.id, "impact": "rig_changed"}]


def test_a_retry_holds_an_entry_whose_rig_changed_and_apply_mod_configuration_writes_it(spine, monkeypatch):
    env = spine
    entry = configured(env)
    successor = updated(env, (MOVED, ("Idle_01", "00")))
    library = reload_library(env)
    official = successor.read_bytes()
    result = library.toggle(True, True)  # Retry
    assert [item["core"] for item in result.held] == ["test_spr"] and result.held[0]["mod"] == entry.id
    assert not result.done and successor.read_bytes() == official
    assert unfinished(env) == [{"install": key(env), "core": "test_spr", "why": "held"}]
    assert library.intent()["unfinished"]["held"] == 1
    assert library.intent()["held"] == [{"install": key(env), "core": "test_spr", "variant": "test", "kind": "spr",
                                         "source": f"mod:{entry.id}", "label": entry.label}]

    written = []
    monkeypatch.setattr(library, "_switch", lambda pin, core, source, log: written.append((core, source)))
    result = library.restore_drift([{"install": key(env), "core": "test_spr", "state": "updated"}])
    assert [item["core"] for item in result.done] == ["test_spr"] and not result.held, result
    assert written == [("test_spr", f"mod:{entry.id}")]
    assert unfinished(env) == []


def test_a_rebuild_with_the_same_rig_is_not_held(spine, monkeypatch):
    env = spine
    entry = configured(env)
    updated(env, (OLD, ("Idle_01",)))
    library = reload_library(env)
    assert library.update_impact()["mods"][0]["impact"] == "rebuilt"
    written = []
    monkeypatch.setattr(library, "_switch", lambda pin, core, source, log: written.append((core, source)))
    result = library.toggle(True, True)
    assert [item["core"] for item in result.done] == ["test_spr"] and not result.held, result
    assert written == [("test_spr", f"mod:{entry.id}")]


def test_a_mod_without_a_baseline_is_not_held(spine, monkeypatch):
    env = spine
    entry = configured(env)
    (env.root / "mods" / entry.id / fingerprint.FILE_NAME).unlink()
    updated(env, (MOVED, ("Idle_01", "00")))
    library = reload_library(env)
    monkeypatch.setattr(library, "_backfill_built_against", lambda *args: None)
    assert library.impacts([entry.id]) == [{"id": entry.id, "impact": "unknown"}]
    assert not library._hold(library.current_pin(), f"mod:{entry.id}")


def test_a_held_entry_survives_a_reload_of_the_configuration(tmp_path):
    from bmpt.library.profiles import normalized

    data = normalized({"unfinished": [{"install": "k", "core": "c", "why": "held"},
                                      {"install": "k", "core": "d", "why": "other"}]})
    assert data["unfinished"] == [{"install": "k", "core": "c", "why": "held"}]


def test_the_start_asks_only_after_a_seen_version_changed_and_with_mods(spine):
    env = spine
    library = env.library
    first = library.update_due()
    # a first start (no version seen) asks nothing and records the version
    assert first["due"] is False and first["previous"] is None
    assert library.update_due()["previous"] == first["version"]
    configured(env)
    updated(env, (MOVED, ("Idle_01", "00")))
    library = reload_library(env)
    due = library.update_due()
    assert due["due"] is True and due["previous"] == first["version"] != due["version"]
    assert library.update_due()["due"] is True  # asked until checked
    library.impact_seen(due["install"], due["version"])
    assert library.update_due()["due"] is False


def test_the_impact_check_names_each_mod_in_its_progress(spine):
    env = spine
    entry = configured(env)
    steps = []
    env.library.update_impact(force=True, progress=steps.append)
    assert steps == [{"current": 1, "total": 1, "step": "import.step.check", "variant": "test", "kind": "spr",
                      "label": entry.label}]


def test_an_update_that_changes_nothing_ends_the_hold(spine, monkeypatch):
    env = spine
    entry = configured(env)
    updated(env, (MOVED, ("Idle_01", "00")))
    library = reload_library(env)
    assert library.impacts([entry.id]) == [{"id": entry.id, "impact": "rig_changed"}]
    # the rebuild gives the mod's own files back: the parts now are its baseline
    from bmpt.library import service as service_module

    class Same:
        success, all_unchanged, file_pairs = True, True, []

    monkeypatch.setattr(service_module, "rebuild", lambda *args, **kwargs: Same())
    result = library.update(entry.id)
    assert result.unchanged
    assert library.impacts([entry.id]) == [{"id": entry.id, "impact": "unchanged"}]
    assert not library._hold(library.current_pin(), f"mod:{entry.id}")
