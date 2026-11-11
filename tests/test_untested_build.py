"""An untested game build: the tested build the App ships, and the confirmation that writes of mods onto the cores a
newer build changed wait for, while reads, Enable Mods Off and restores stay as they are (BL-163)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ba_modding_toolkit.searching import get_search_dirs
from ba_modding_toolkit.utils import no_log
from bmpt.errors import ServiceError
from bmpt.gameassets.discovery import resource_version
from bmpt.library import tested_build

from test_library import FakeVariant, game_bundle, make_mod, setup_library  # noqa: F401


# --- the data ---

def test_the_data_round_trips_and_matches_names_whatever_their_case():
    data = {"schema": tested_build.SCHEMA,
            "servers": {"global": tested_build.encode("123", ["v1", "v1", "v0"], ["a_1.bundle", "b_2.bundle"])}}
    found = tested_build.decode(json.loads(json.dumps(data)))["global"]
    assert found.build == "123" and found.versions == {"v1", "v0"} and len(found.keys) == 2
    assert found.has("a_1.bundle") and found.has("A_1.BUNDLE") and not found.has("a_2.bundle")
    assert tested_build.decode({"schema": 0, "servers": data["servers"]}) == {}


def test_the_shipped_data_is_the_build_the_tests_run_on():
    shipped = tested_build._load(tested_build.FILE)
    assert set(shipped) == {"global"}  # Japan: no tested build, never untested
    tested = shipped["global"]
    assert tested.build and len(tested.versions) == 1 and len(tested.keys) > 20000
    game = os.environ.get("BAMT_TEST_GAME_DIR")
    if not game:
        pytest.skip("BAMT_TEST_GAME_DIR is not set")
    dirs = get_search_dirs(Path(game))
    names = tested_build.bundle_names(dirs)
    assert resource_version(dirs) in tested.versions and {tested_build.name_key(n) for n in names} == tested.keys, (
        "The official game folder is another build than the one shipped as tested: once the suite passes on it, "
        "run scripts/build_tested_build.py on it")


# --- the library ---

def as_tested(env, monkeypatch, server="global"):
    """The install as it is now is the tested build of ``server``."""
    pin = env.library.current_pin()
    data = {"schema": tested_build.SCHEMA, "servers": {server: tested_build.encode(
        "1", [env.library.resource_version(pin)], tested_build.bundle_names(pin.dirs))}}
    found = tested_build.decode(data)
    monkeypatch.setattr(tested_build, "for_server", lambda name: found.get(name))
    return found[server]


def refusal(action):
    with pytest.raises(ServiceError) as caught:
        action()
    assert caught.value.code == "untested_build"
    json.dumps(caught.value.to_json())
    return caught.value.params


def key(env):
    return env.library.current_pin().key


def test_an_apply_onto_a_core_the_update_changed_waits_for_a_confirmation(setup_library, monkeypatch):
    env = setup_library
    mod, = env.library.add([make_mod(env.original, env.tmp / "input")])
    as_tested(env, monkeypatch)
    official = env.original.read_bytes()
    game_bundle(env.game_dir, part="textures")  # the update adds a part to the core
    pin = env.library.current_pin()
    assert env.library.untested_version(pin) == env.library.resource_version(pin)

    params = refusal(lambda: env.library.apply("test", f"mod:{mod.id}"))
    assert params["entries"] == [{"install": key(env), "core": "test_spr", "variant": "test", "kind": "spr"}]
    assert params["confirm"] == {key(env): {"version": env.library.resource_version(pin), "cores": ["test_spr"]}}
    assert env.original.read_bytes() == official and not env.library.configured("test", pin).get("spr")

    env.library.confirm_untested(params["confirm"])
    env.library.apply("test", f"mod:{mod.id}")
    assert env.original.read_bytes() == mod.file.read_bytes()
    assert env.library.configured("test", pin).get("spr") == f"mod:{mod.id}"
    env.library.restore("test")  # back to official: never asked
    assert env.original.read_bytes() == official


def test_a_tested_build_and_an_unchanged_core_need_no_confirmation(setup_library, monkeypatch):
    env = setup_library
    mod, = env.library.add([make_mod(env.original, env.tmp / "input")])
    as_tested(env, monkeypatch)
    env.library.apply("test", f"mod:{mod.id}")  # the tested build itself
    env.library.restore("test")
    game_bundle(env.game_dir, "other_spr")  # the update changes another core only
    assert env.library.untested_version(env.library.current_pin()) is not None
    env.library.apply("test", f"mod:{mod.id}")
    assert env.original.read_bytes() == mod.file.read_bytes()


def test_a_server_without_a_tested_build_is_never_untested(setup_library, monkeypatch):
    env = setup_library
    mod, = env.library.add([make_mod(env.original, env.tmp / "input")])
    as_tested(env, monkeypatch, server="japan")
    game_bundle(env.game_dir, part="textures")
    assert env.library.untested_version(env.library.current_pin()) is None
    env.library.apply("test", f"mod:{mod.id}")
    assert env.original.read_bytes() == mod.file.read_bytes()


def test_mods_on_waits_for_a_confirmation_and_mods_off_never_does(setup_library, monkeypatch):
    env = setup_library
    mod, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.library.apply("test", f"mod:{mod.id}")
    as_tested(env, monkeypatch)
    official = (env.root / "originals" / env.original.name).read_bytes()
    game_bundle(env.game_dir, part="textures")

    assert not env.library.toggle(False, True).failed  # Off: official files, no confirmation
    assert env.original.read_bytes() == official
    assert env.library.apply("test", f"mod:{mod.id}").deferred  # while off an apply only configures

    params = refusal(lambda: env.library.toggle(True, False))
    assert params["entries"] == [{"install": key(env), "core": "test_spr", "variant": "test", "kind": "spr"}]
    assert env.library.intent()["enabled"] is False and env.original.read_bytes() == official  # nothing changed

    env.library.confirm_untested(params["confirm"])
    result = env.library.toggle(True, False)
    assert [item["core"] for item in result.done] == ["test_spr"] and not result.failed
    assert env.original.read_bytes() == mod.file.read_bytes()


def test_apply_mod_configuration_waits_for_a_confirmation(setup_library, monkeypatch):
    env = setup_library
    mod, = env.library.add([make_mod(env.original, env.tmp / "input")])
    env.library.apply("test", f"mod:{mod.id}")
    as_tested(env, monkeypatch)
    game_bundle(env.game_dir, part="textures")
    written = []
    monkeypatch.setattr(env.library, "_switch", lambda pin, core, source, log: written.append((core, source)))
    entries = [{"install": key(env), "core": "test_spr", "state": "reset"}]
    params = refusal(lambda: env.library.restore_drift(entries))
    assert [entry["core"] for entry in params["entries"]] == ["test_spr"] and written == []


def test_the_write_itself_checks_last_and_a_rollback_is_never_asked(setup_library, monkeypatch):
    env = setup_library
    as_tested(env, monkeypatch)
    game_bundle(env.game_dir, part="textures")
    prepared = make_mod(env.original, env.tmp / "prepared")
    pin = env.library.current_pin()
    refusal(lambda: env.library._commit({env.original: prepared}, "mod:x", no_log, pin=pin))
    assert env.original.read_bytes() != prepared.read_bytes()
    env.library._commit({env.original: prepared}, "mod:x", no_log, pin=pin, untested=False)
    assert env.original.read_bytes() == prepared.read_bytes()


@pytest.mark.parametrize("confirm", [None, [], {"k": None}, {"k": {"version": "", "cores": []}},
                                     {"k": {"version": "v", "cores": "a"}}, {"k": {"version": "v", "cores": [1]}}])
def test_a_malformed_confirmation_is_refused(setup_library, confirm):
    with pytest.raises(ServiceError) as caught:
        setup_library.library.confirm_untested(confirm)
    assert caught.value.code == "bad_params"
