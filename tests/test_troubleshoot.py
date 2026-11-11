"""Troubleshoot Mods (BL-185): Apply Official to All, Restore Configuration and the check against Steam's file list,
with synthetic header-only bundles in temporary game folders (never a real install, library or settings)."""

from __future__ import annotations

import hashlib

import pytest

from bmpt.library import troubleshoot
from bmpt.steam_records import OfficialFile

from test_library import game_bundle, header_bundle
from test_library_profiles import OFFICIAL, World, expect_error, world  # noqa: F401 (the fixture)


class FakeSteam:
    """Steam's file list: every given file as it is now (official), by name."""

    def __init__(self, *paths):
        self.files = {path.name: OfficialFile(len(data := path.read_bytes()), hashlib.sha1(data).hexdigest())
                      for path in paths}

    def bundles(self, _folder):
        return dict(self.files)


def _pins(world: World):
    return world.library.installs()


def _lobby(world: World):
    return game_bundle(world.game, "test_home", payload=b"lobby!!!")


def test_apply_official_writes_official_files_keeps_the_configuration_and_leaves_mods_on(world):
    lobby = _lobby(world)
    portrait, home = world.add(world.spr), world.add(lobby, payload=b"lobbymod")
    world.library.apply("test", f"mod:{portrait.id}")
    world.library.apply("test", f"mod:{home.id}", kind="lobby")
    result = troubleshoot.apply_official(world.library, _pins(world))
    assert {item["core"] for item in result.done} == {"test_spr", "test_home"} and not result.cancelled
    assert world.spr.read_bytes() == OFFICIAL and lobby.read_bytes() == header_bundle(b"lobby!!!")
    data = world.profiles()
    assert data["enabled"] is True and world.folder()["configured"] == {}
    saved = data["troubleshoot"]["folders"][world.key()]
    assert {core: entry["source"] for core, entry in saved.items()} == {
        "test_spr": f"mod:{portrait.id}", "test_home": f"mod:{home.id}"}
    assert troubleshoot.status(world.library) == {"active": True, "count": 2, "since": data["troubleshoot"]["since"]}
    # one mod at a time: a choice writes at once again
    applied = world.library.apply("test", f"mod:{portrait.id}")
    assert not applied.deferred and world.spr.read_bytes() == portrait.file.read_bytes()


def test_a_second_apply_official_keeps_the_first_snapshot(world):
    portrait = world.add(world.spr)
    world.library.apply("test", f"mod:{portrait.id}")
    troubleshoot.apply_official(world.library, _pins(world))
    first = world.profiles()["troubleshoot"]
    world.library.apply("test", f"mod:{portrait.id}")
    troubleshoot.apply_official(world.library, _pins(world))
    assert world.profiles()["troubleshoot"] == first and world.spr.read_bytes() == OFFICIAL


def test_restore_brings_back_the_saved_choices_and_unsets_the_ones_made_meanwhile(world):
    other = game_bundle(world.game, "other_spr", payload=b"other!!!")
    portrait, tried = world.add(world.spr), world.add(other, payload=b"tried!!!")
    world.library.apply("test", f"mod:{portrait.id}")
    troubleshoot.apply_official(world.library, _pins(world))
    world.library.apply("other", f"mod:{tried.id}")  # tried while troubleshooting, never in the snapshot
    assert other.read_bytes() == tried.file.read_bytes()
    result = troubleshoot.restore(world.library, _pins(world))
    assert [item["core"] for item in result.done] == ["test_spr"]
    assert world.spr.read_bytes() == portrait.file.read_bytes()
    assert other.read_bytes() == header_bundle(b"other!!!")
    assert set(world.folder()["configured"]) == {"test_spr"}
    assert "troubleshoot" not in world.profiles() and world.profiles()["enabled"] is True
    assert troubleshoot.status(world.library)["active"] is False
    expect_error("state_changed", lambda: troubleshoot.restore(world.library, _pins(world)))


def test_from_mods_off_restore_turns_them_off_again(world):
    world.consent = True
    world.library._steam_files = FakeSteam(world.spr)
    portrait = world.add(world.spr)
    world.library.apply("test", f"mod:{portrait.id}")
    world.toggle(False)
    troubleshoot.apply_official(world.library, _pins(world))
    assert world.profiles()["enabled"] is True and world.profiles()["troubleshoot"]["enabled"] is False
    world.library.apply("test", f"mod:{portrait.id}")
    result = troubleshoot.restore(world.library, _pins(world))
    assert world.profiles()["enabled"] is False and world.spr.read_bytes() == OFFICIAL
    assert world.folder()["configured"]["test_spr"]["source"] == f"mod:{portrait.id}"
    assert result.check["verified"] and result.check["changedCount"] == 0


def test_the_check_compares_every_listed_bundle_with_steam(world):
    world.consent = True
    lobby = _lobby(world)
    gone = game_bundle(world.game, "other_spr", payload=b"other!!!")
    world.library._steam_files = FakeSteam(world.spr, lobby, gone)
    gone.unlink()
    lobby.write_bytes(header_bundle(b"changed!"))
    check = troubleshoot.check(world.library, _pins(world))
    assert check["verified"] and check["checked"] == 3
    assert (check["changed"], check["missing"]) == ([lobby.name], [gone.name])
    assert (check["changedCount"], check["missingCount"], check["unverified"]) == (1, 1, [])


def test_after_apply_official_every_listed_bundle_matches_steam(world):
    world.consent = True
    lobby = _lobby(world)
    world.library._steam_files = FakeSteam(world.spr, lobby)
    portrait, home = world.add(world.spr), world.add(lobby, payload=b"lobbymod")
    world.library.apply("test", f"mod:{portrait.id}")
    world.library.apply("test", f"mod:{home.id}", kind="lobby")
    result = troubleshoot.apply_official(world.library, _pins(world))
    assert result.check == {"verified": True, "checked": 2, "changed": [], "changedCount": 0, "missing": [],
                            "missingCount": 0, "unverified": []}
    assert result.to_json()["check"]["verified"] is True


@pytest.mark.parametrize("consent", [False, True])
def test_without_a_steam_list_or_consent_the_check_is_unverified(world, consent):
    world.consent = consent
    world.library._steam_files = FakeSteam() if consent else FakeSteam(world.spr)
    check = troubleshoot.check(world.library, _pins(world))
    assert check["verified"] is False and check["unverified"] == [world.key()] and check["checked"] == 0


def test_troubleshooting_keeps_the_originals_of_the_saved_configuration(world):
    other = game_bundle(world.game, "other_spr", payload=b"other!!!")
    portrait, tried = world.add(world.spr), world.add(other, payload=b"tried!!!")
    world.library.apply("test", f"mod:{portrait.id}")
    assert world.original(world.spr).exists()
    troubleshoot.apply_official(world.library, _pins(world))
    world.library.apply("other", f"mod:{tried.id}")  # a write prunes the kept originals
    world.library.prune()
    assert world.original(world.spr).exists(), "kept while its choice is saved to restore"
    troubleshoot.restore(world.library, _pins(world))
    assert world.spr.read_bytes() == portrait.file.read_bytes() and world.original(world.spr).exists()
    assert world.original(world.spr).read_bytes() == OFFICIAL
