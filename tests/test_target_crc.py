"""A mod file written whole onto a part whose name carries another CRC (BL-191).

A game update that rebuilds a part in place and keeps its structure changes only the CRC in its name; the library
then takes a mod made for the part's earlier name as the part's current release and writes it whole. Every write must
leave the part with the CRC of its own name, and a built copy kept from an older, unfixed write must not come back."""

from types import SimpleNamespace

import pytest

from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.naming import parse_filename
from ba_modding_toolkit.utils import CRCUtils
from bmpt.library import ModLibrary
from bmpt.library.install import Action, PlannedFile, PlannedGroup, install_staged
from bmpt.engine import SaveOptions

from test_library import game_bundle, make_mod, reload_library, setup_library  # noqa: F401


def crc_of_name(path):
    return int(parse_filename(path.name).crc)


def matches_name(path):
    return CRCUtils.compute_crc32(path) == crc_of_name(path)


@pytest.fixture
def resave(monkeypatch):
    """Bundle.load of a synthetic bundle: its save puts the CRC of the output's name on the bytes."""
    saved = []

    def load(path, *args, **kwargs):
        def save(output, options):
            saved.append((path.name, output.name, options.perform_crc))
            data = path.read_bytes()
            if options.perform_crc:
                data = CRCUtils.apply_crc_fix(data, crc_of_name(output))
            output.write_bytes(data)
            return True, "saved"
        return SimpleNamespace(save=save)

    monkeypatch.setattr(Bundle, "load", load)
    return saved


@pytest.fixture
def updated(setup_library, monkeypatch, resave):
    """A mod CRC-fixed for the part's name before a game update, and the part rebuilt in place under a new CRC."""
    env = setup_library
    env.settings.set("enable_crc_correction", "true")
    upload = make_mod(env.original, env.tmp / "input", fixed=True)
    assert matches_name(upload)
    env.mod = env.library.add([upload])[0]
    env.original.unlink()
    env.part = game_bundle(env.game_dir, payload=b"official, rebuilt")
    assert env.part.name != upload.name and parse_filename(env.part.name).core == parse_filename(upload.name).core
    env.library = reload_library(env)
    env.saved = resave
    return env


def test_direct_replacement_under_another_crc_is_saved_with_the_target_crc(tmp_path, resave):
    official = game_bundle(tmp_path / "game", payload=b"official, rebuilt")
    mod = make_mod(game_bundle(tmp_path / "old", payload=b"official"), tmp_path / "mod", fixed=True)
    assert matches_name(mod) and mod.name != official.name
    file = PlannedFile(mod, parse_filename(mod.name), Action.DIRECT_REPLACE, existing_path=official,
                       need_crc_fix=True)
    group = PlannedGroup("test", None, "test_spr", [file])
    assert not install_staged([group], tmp_path / "out", set(), SaveOptions(perform_crc=True))
    assert matches_name(official)
    assert resave == [(mod.name, official.name, True)]


def test_direct_replacement_under_its_own_name_is_copied(tmp_path, resave):
    official = game_bundle(tmp_path / "game")
    mod = make_mod(official, tmp_path / "mod", fixed=True)
    file = PlannedFile(mod, parse_filename(mod.name), Action.DIRECT_REPLACE, existing_path=official,
                       need_crc_fix=True)
    assert not install_staged([PlannedGroup("test", None, "test_spr", [file])], tmp_path / "out", set(),
                              SaveOptions(perform_crc=True))
    assert official.read_bytes() == mod.read_bytes()
    assert resave == []


def test_apply_after_a_crc_only_game_update_writes_the_part_with_its_own_crc(updated):
    env = updated
    result = env.library.apply("test", f"mod:{env.mod.id}")
    assert not result.unchanged
    assert matches_name(env.part)
    assert env.library.state("test").crc == "fixed"


def test_switch_after_a_crc_only_game_update_writes_the_part_with_its_own_crc(updated):
    env = updated
    assert not env.library.toggle(False, True).failed
    env.library.apply("test", f"mod:{env.mod.id}")  # configured only while mods are off
    assert not matches_name(env.part) or env.part.read_bytes() != env.mod.file.read_bytes()
    assert not env.library.toggle(True, False).failed
    assert matches_name(env.part)
    assert env.library.state("test").crc == "fixed"


def bad_built_key(env):
    """The built key an older build kept at an Off after such a write: the part is the mod file's exact bytes."""
    pin = env.library.current_pin()
    sha1 = env.library._hashes.get(env.mod.file).sha1

    def change(data):
        folder = data["folders"].setdefault(pin.key, {})
        folder.setdefault("built", {})["test_spr"] = {
            "source": f"mod:{env.mod.id}", "inputs": [],
            "parts": {env.part.name: {"sha1": sha1, "mod_file": env.mod.file.name}}}

    env.library.profiles.update(change)
    return pin


def test_on_does_not_write_back_a_built_copy_with_another_crc(updated):
    env = updated
    assert not env.library.toggle(False, True).failed
    env.library.apply("test", f"mod:{env.mod.id}")
    pin = bad_built_key(env)
    assert not env.library.toggle(True, False).failed
    assert matches_name(env.part)
    built = env.library.profiles.read()["folders"][pin.key]["built"]
    assert "test_spr" not in built or built["test_spr"]["parts"][env.part.name]["mod_file"] is None


def test_generic_mod_after_a_crc_only_game_update_writes_the_part_with_its_own_crc(setup_library, resave):
    env = setup_library
    env.settings.set("enable_crc_correction", "true")
    part = game_bundle(env.game_dir, "common", category="ui", part="textures", payload=b"official UI")
    library = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    mod, = library.add_files("other", [make_mod(part, env.tmp / "upload", payload=b"mod UI", fixed=True)])
    part.unlink()
    part = game_bundle(env.game_dir, "common", category="ui", part="textures", payload=b"official UI, rebuilt")
    library = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    library.apply(mod.target, f"mod:{mod.id}", kind="other")
    assert part.read_bytes() != (mod.file.parent / mod.file.name).read_bytes()
    assert matches_name(part)
