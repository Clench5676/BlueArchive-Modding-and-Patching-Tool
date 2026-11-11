"""Game files are identified by what the build guarantees: an asset path lives in one bundle, a changed group is
rebuilt under its name and date with a new CRC, a part number only counts its own dated group's asset types."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from bmpt.library import service as service_module
from bmpt.library.integrity import BUILTIN_RESOURCES, Contents, missing_parts
from bmpt.library.targets import Readers, by_assets
from bmpt.library.judge import core_of

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


def dependency(core: str, date: str, part: str, crc: int = 1) -> Path:
    return Path("/game") / f"assets-_mx-characters-{core}-_mxdependency-{date}_{part}_assets_all_{crc}.bundle"


def no_keys(path: Path) -> set:
    return set()


def update_targets(sources, targets, *, paths, keys):
    """The parts the resolver's asset stage finds, or None when the sources show nothing to judge by."""
    found = by_assets(sources, targets, Readers(paths=paths, keys=keys))
    return None if found is None else found[1]


# The asset path decides, whatever the dates and part numbers

def test_asset_paths_pick_the_part_that_holds_them_whatever_its_date_and_number():
    older = dependency("x", "2023-01-01", "004")
    same_number = dependency("x", "2024-11-18", "004")  # the same number holds another type now
    holder = dependency("x", "2025-07-25", "000")
    paths = {older: {"assets/_mx/characters/x/model/x.fbx"},
             same_number: {"assets/_mx/characters/x/effect/prefab/fx.prefab"},
             holder: {"assets/_mx/characters/x/model/x.fbx"}}
    assert update_targets([older], [same_number, holder], paths=paths.get, keys=no_keys) == [holder]


def test_assets_split_over_several_parts_update_each_of_them():
    older = dependency("x", "2023-01-01", "003")
    first, second = dependency("x", "2024-11-18", "003"), dependency("x", "2025-07-25", "000")
    paths = {older: {"a.mat", "b.mat"}, first: {"a.mat"}, second: {"b.mat"}}
    assert update_targets([older], [first, second], paths=paths.get, keys=no_keys) == [first, second]


def test_asset_names_and_types_decide_when_no_asset_path_matches():
    older = dependency("x", "2023-01-01", "002")
    other, holder = dependency("x", "2024-11-18", "002"), dependency("x", "2025-07-25", "001")
    keys = {older: {("x_tex", "Texture2D")}, other: {("x", "Mesh")}, holder: {("x_tex", "Texture2D")}}
    assert update_targets([older], [other, holder], paths=lambda path: set(), keys=keys.get) == [holder]


def test_nothing_to_judge_by_is_none_and_evidence_without_a_holder_is_empty():
    older, target = dependency("x", "2023-01-01", "002"), dependency("x", "2024-11-18", "002")
    assert update_targets([older], [target], paths=lambda path: set(), keys=no_keys) is None
    paths = {older: {"assets/_mx/characters/x/new.png"}, target: {"assets/_mx/characters/x/old.png"}}
    assert update_targets([older], [target], paths=paths.get, keys=no_keys) == []


def test_unity_builtin_resources_are_never_missing():
    contents = Contents(references={*BUILTIN_RESOURCES, "cab-0123"}, files={"cab-4567"})
    assert missing_parts(contents) == {"cab-0123"}


# A game update rebuilds a changed group in place

def spr(date: str, part: str, crc: int) -> str:
    return f"assets-_mx-spinecharacters-a_spr-_mxdependency-{date}_{part}_assets_all_{crc}.bundle"


def test_the_group_rebuilt_under_a_new_crc_is_the_successor(tmp_path):
    gone, rebuilt = tmp_path / spr("2024-11-18", "003", 111), tmp_path / spr("2024-11-18", "003", 222)
    index = {"a_spr": [tmp_path / spr("2024-11-18", "002", 333), rebuilt]}
    assert service_module._rebuilt(index, gone) == rebuilt
    assert service_module._has_successor(index, gone)
    # another folder's file of the group is not this folder's successor
    elsewhere = {"a_spr": [tmp_path / "other" / rebuilt.name]}
    assert not service_module._has_successor(elsewhere, gone)


def test_bundles_of_one_asset_each_share_their_name_less_the_crc_and_name_no_successor(tmp_path):
    names = [f"ui-uilobbyelement-_mxload-prefabs-2025-07-02_assets_all_{crc}.bundle" for crc in (1, 2, 3)]
    gone, *present = (tmp_path / name for name in names)
    index = {core_of(names[0]): present}
    assert service_module._rebuilt(index, gone) is None
    assert not service_module._has_successor(index, gone)


def test_a_later_dated_file_of_the_part_is_still_a_successor_and_an_earlier_one_is_not(tmp_path):
    gone = tmp_path / spr("2024-11-18", "003", 111)
    assert service_module._has_successor({"a_spr": [tmp_path / spr("2025-07-25", "003", 2)]}, gone)
    assert not service_module._has_successor({"a_spr": [tmp_path / spr("2024-01-01", "003", 2)]}, gone)
    assert not service_module._has_successor({"a_spr": [tmp_path / spr("2024-11-18", "002", 2)]}, gone)


# On the official files

CASES = [  # dependency parts of cores whose parts sit under several dates
    "assets-_mx-characters-ch0100-_mxdependency-2024-11-18_004_",          # meshes; a 2025-07-25 group exists
    "assets-_mx-characters-ch0100-_mxdependency-2024-11-18_006_",          # textures
    "assets-_mx-characters-airi_original-_mxdependency-2024-11-18_003_",   # meshes; 2025-07-25 and 2025-11-07 groups
    "assets-_mx-spinelobbies-ch0164_home-_mxdependency-2024-11-18_002_",   # skeleton and atlas; a 2025-07-10 group
    "assets-_mx-spinelobbies-ch0164_home-_mxdependency-2024-11-18_003_",   # page texture
]


@pytest.fixture(scope="module")
def official(tmp_path_factory):
    from bmpt.catalog import Catalog
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.library import ModLibrary
    from ba_modding_toolkit.searching import get_search_dirs

    tmp = tmp_path_factory.mktemp("official")
    directories = get_search_dirs(GAME_DIR)
    game = GameAssets(directories, AssetCache(allowed=False, root=tmp / "cache"))
    settings = Settings(tmp / "config.toml")
    settings.set("game_resource_dir", str(GAME_DIR))
    library = ModLibrary(tmp / "library", Catalog(game, None, server="global"), game, settings)
    return library, library.current_pin(), directories


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
@pytest.mark.parametrize("prefix", CASES)
def test_a_part_of_an_older_release_updates_the_official_file_holding_its_assets(official, prefix):
    library, pin, directories = official
    source = next(path for folder in directories for path in sorted(folder.iterdir()) if path.name.startswith(prefix))
    older = re.sub(r"-\d{4}-\d{2}-\d{2}_", "-2023-01-01_", source.name)
    targets = library._spine_parts(core_of(source.name), pin)
    assert len({re.search(r"\d{4}-\d{2}-\d{2}", path.name)[0] for path in targets}) > 1
    group, = library._update_groups({older: source}, targets, pin)
    assert group.target_paths == [source]
    assert {file.action for file in group.files} == {"update"}


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
def test_a_preview_of_an_older_part_drops_only_the_part_its_assets_cover(official):
    library, pin, directories = official
    prefix = "assets-_mx-characters-ch0100-_mxdependency-"
    animations = next(path for folder in directories for path in sorted(folder.iterdir())
                      if path.name.startswith(prefix + "2024-11-18_000_"))
    material = next(path for folder in directories for path in sorted(folder.iterdir())
                    if path.name.startswith(prefix + "2025-07-25_000_"))  # the same number, another asset type
    targets = library._spine_parts("ch0100", pin)
    assert {animations, material} <= set(targets)
    older = re.sub(r"-\d{4}-\d{2}-\d{2}_", "-2023-01-01_", animations.name)
    assert library._replaced_parts({older: animations}, targets, pin) == {animations}
