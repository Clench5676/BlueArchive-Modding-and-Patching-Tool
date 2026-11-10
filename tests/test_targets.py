"""The one rule for the official parts a set of mod files replaces or updates (``bmpt.library.targets``), and the
paths that must ask it."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bmpt.errors import ServiceError
from bmpt.library import targets as resolver
from bmpt.library.targets import Readers, resolve

ROOT = Path(__file__).resolve().parents[1]


def spr(date: str, part: str, crc: int = 1, core: str = "a_spr") -> Path:
    return Path("/game") / f"assets-_mx-spinecharacters-{core}-_mxdependency-{date}_{part}_assets_all_{crc}.bundle"


def mobile(date: str, word: str, crc: int = 7, core: str = "a_spr") -> Path:
    return Path("/mod") / f"assets-_mx-spinecharacters-{core}-_mxdependency-{word}-{date}_assets_all_{crc}.bundle"


def readers(paths: dict | None = None, keys: dict | None = None, cabs: dict | None = None) -> Readers:
    paths, keys, cabs = paths or {}, keys or {}, cabs or {}
    return Readers(cabs=lambda path: cabs.get(path, set()), paths=lambda path: set(paths.get(path, set())),
                   keys=lambda path: set(keys.get(path, set())))


# A core keeping its parts under two dates, as Steam keeps ch0242_spr: skeleton and page under 2024-11-18, one added
# clip under 2025-07-10
MONO, SKEL, PAGE = spr("2024-11-18", "000"), spr("2024-11-18", "002"), spr("2024-11-18", "003")
CLIP = spr("2025-07-10", "000")
CORE = [MONO, SKEL, PAGE, CLIP]
HELD = {MONO: {"a/mono.asset"}, SKEL: {"a/a.skel.bytes", "a/a.atlas.txt"}, PAGE: {"a/a.png"}, CLIP: {"a/a-17.bytes"}}


def test_current_releases_by_name_name_without_crc_and_cab():
    renamed = SKEL.with_name(SKEL.name.replace("_1.bundle", "_99.bundle"))
    other = Path("/mod/whatever.bundle")
    # the exact name and the name without CRC both point at SKEL: two files on one part
    with pytest.raises(ServiceError, match="mod_target_ambiguous"):
        resolve({SKEL.name: Path("/mod") / SKEL.name, renamed.name: renamed}, [SKEL, PAGE], readers())
    resolution = resolve({renamed.name: renamed, other.name: other}, [SKEL, PAGE],
                         readers(cabs={other: {"cab-page"}, PAGE: {"cab-page"}}))
    assert resolution.current == {SKEL: renamed, PAGE: other}
    assert {name: match.method for name, match in resolution.matches().items()} == {
        renamed.name: resolver.NAME_CRC, other.name: resolver.CAB}


def test_a_rebuilt_parts_older_release_is_not_its_current_release():
    # a game update rebuilt SKEL and PAGE in place: same names but for the CRC, same CABs
    old_skel = Path("/mod") / SKEL.name.replace("_1.bundle", "_99.bundle")
    old_page = Path("/mod/renamed.bundle")
    files = {old_skel.name: old_skel, old_page.name: old_page}
    held = {**HELD, old_skel: HELD[SKEL], old_page: HELD[PAGE]}
    cabs = {old_page: {"cab-page"}, PAGE: {"cab-page"}}
    asked = []

    def rebuilt(source, target):
        asked.append((source, target))
        return False

    resolution = resolve(files, CORE, Readers(cabs=lambda path: cabs.get(path, set()),
                                              paths=lambda path: set(held.get(path, set())), release=rebuilt))
    assert asked == [(old_skel, SKEL), (old_page, PAGE)]
    assert resolution.current == {} and set(resolution.older) == set(files)
    # they update the parts through their assets, as the files of any older release do
    assert resolution.updated == [SKEL, PAGE]
    assert {match.method for match in resolution.matches().values()} == {resolver.ASSET_PATH}
    # built on the parts as they are now, they replace them whole
    same = resolve(files, CORE, Readers(cabs=lambda path: cabs.get(path, set()), release=lambda source, target: True))
    assert same.current == {SKEL: old_skel, PAGE: old_page}


def test_an_exact_name_is_the_current_release_without_asking_for_the_structure():
    source = Path("/mod") / SKEL.name

    def never(source, target):
        raise AssertionError("an exact name needs no structure")

    assert resolve({source.name: source}, CORE, Readers(release=never)).current == {SKEL: source}


def test_a_generic_file_of_a_rebuilt_slot_updates_it_through_its_assets():
    target = Path("/game/uis-03_scenario-01_background-bg_a-2024-11-18_assets_all_1.bundle")
    old = Path("/mod") / target.name.replace("_1.bundle", "_99.bundle")
    held = {target: {"bg/a.png"}, old: {"bg/a.png"}}
    reads = Readers(paths=lambda path: set(held.get(path, set())), release=lambda source, target: False)
    found = resolver.generic({old.name: old}, [target], reads, container_keys=lambda path, source: set())
    assert found[old.name].method == resolver.ASSET_PATH and found[old.name].targets == [target]
    whole = resolver.generic({old.name: old}, [target], Readers(), container_keys=lambda path, source: set())
    assert whole[old.name].method == resolver.NAME_CRC


def test_another_builds_files_update_the_parts_holding_their_asset_paths_whatever_their_date():
    text, page = mobile("2025-07-02", "textassets"), mobile("2025-07-02", "textures")
    paths = {**HELD, text: HELD[SKEL], page: HELD[PAGE]}
    resolution = resolve({text.name: text, page.name: page}, CORE, readers(paths))
    # the newest date holds only the clip; the parts holding the assets are under the older date
    assert resolution.updated == [SKEL, PAGE]
    assert resolution.replaced == {SKEL, PAGE}
    assert {match.method for match in resolution.matches().values()} == {resolver.ASSET_PATH}
    assert not resolution.unresolved and not resolution.unplaced


def test_a_part_updated_in_part_is_written_but_not_dropped_by_the_preview():
    text = mobile("2025-07-02", "textassets")
    resolution = resolve({text.name: text}, CORE, readers({**HELD, text: {"a/a.skel.bytes"}}))
    assert resolution.updated == [SKEL]
    assert resolution.replaced == set()


def test_asset_names_and_types_decide_when_no_asset_path_is_held():
    text = mobile("2025-07-02", "textassets")
    keys = {text: {("a.skel", "TextAsset")}, SKEL: {("a.skel", "TextAsset")}, PAGE: {("a", "Texture2D")}}
    resolution = resolve({text.name: text}, CORE, readers({**HELD, text: {"x/elsewhere.bytes"}}, keys))
    assert resolution.updated == [SKEL]
    assert resolution.matches()[text.name].method == resolver.ASSET_KEY
    # another server's file placed only by asset names is judged by its name: the core has no textassets part
    assert resolution.unplaced == [text.name]


def test_unreadable_files_take_their_part_at_its_newest_date_only():
    older = spr("2023-01-01", "000", core="a_spr")
    resolution = resolve({older.name: older}, CORE, readers())
    assert resolution.updated == [CLIP]
    assert resolution.replaced == {CLIP}
    assert resolution.matches()[older.name].method == resolver.PART


def test_readable_assets_no_part_holds_resolve_to_nothing():
    stray = mobile("2025-07-02", "textures")
    resolution = resolve({stray.name: stray}, CORE, readers({**HELD, stray: {"b/b.png"}}))
    assert resolution.updated == [] and resolution.replaced == set()
    assert resolution.unresolved == [stray.name]
    assert resolution.evidence() == [{"file": stray.name, "method": resolver.NOTHING, "targets": [], "dates": []}]


def test_the_preview_drops_only_parts_the_import_writes():
    text, page = mobile("2025-07-02", "textassets"), mobile("2025-07-02", "textures")
    for carried in ({text: HELD[SKEL], page: HELD[PAGE]}, {text: {"a/a.skel.bytes"}, page: HELD[PAGE] | {"x"}},
                    {text: HELD[SKEL] | HELD[CLIP]}):
        files = {path.name: path for path in carried}
        resolution = resolve(files, CORE, readers({**HELD, **carried}))
        assert resolution.replaced <= set(resolution.updated)


def test_evidence_names_methods_targets_and_dates():
    text = mobile("2025-07-02", "textassets")
    resolution = resolve({text.name: text, PAGE.name: Path("/mod") / PAGE.name}, CORE,
                         readers({**HELD, text: HELD[SKEL]}))
    assert resolution.evidence()[:1] == [{"file": text.name, "method": None, "targets": [], "dates": []}]
    resolution.updated
    assert resolution.evidence() == [
        {"file": text.name, "method": resolver.ASSET_PATH, "targets": [SKEL.name], "dates": ["2024-11-18"]},
        {"file": PAGE.name, "method": resolver.NAME, "targets": [PAGE.name], "dates": ["2024-11-18"]}]


def test_older_releases_without_the_name_stage():
    resolution = resolve({SKEL.name: SKEL}, CORE, readers(HELD), whole=False)
    assert resolution.current == {} and resolution.updated == [SKEL]


# Generic slots

def ui(date: str, crc: int, word: str = "prefabs") -> Path:
    return Path("/game") / f"ui-uilobbyelement-_mxload-{word}-{date}_assets_all_{crc}.bundle"


def test_generic_files_find_the_bundle_holding_their_asset_path_among_every_date_of_their_prefix():
    old, new = ui("2024-01-01", 1), ui("2025-07-02", 2)
    source = Path("/mod") / ui("2023-01-01", 3).name
    matches = resolver.generic({source.name: source}, [old, new], readers({old: {"p/a.prefab"}, new: {"p/b.prefab"},
                               source: {"p/a.prefab"}}), container_keys=lambda path, own: set())
    assert matches[source.name].targets == [old] and matches[source.name].method == resolver.ASSET_PATH


def test_generic_files_fall_back_to_the_newest_file_of_a_slot_sharing_a_container_name_and_type():
    old, new = ui("2024-01-01", 1), ui("2025-07-02", 2)
    source = Path("/mod") / ui("2023-01-01", 3).name
    keys = {source: {"k"}, new: {"k"}, old: {"k"}}
    matches = resolver.generic({source.name: source}, [old, new], readers(),
                               container_keys=lambda path, own: keys.get(path, set()))
    assert matches[source.name].targets == [new]
    with pytest.raises(ServiceError, match="game_file_not_found"):
        resolver.generic({source.name: source}, [old, new], readers(), container_keys=lambda path, own: set())
    with pytest.raises(ServiceError, match="game_file_conflict"):
        resolver.generic({source.name: source}, [old, new, ui("2025-07-02", 4)], readers(),
                         container_keys=lambda path, own: set())


def test_a_generic_slot_holds_its_newest_file():
    old, new = ui("2024-01-01", 1), ui("2025-07-02", 2)
    assert resolver.slot_files({"slot": [old, new]}, ["slot"]) == [new]
    with pytest.raises(ServiceError, match="game_file_conflict"):
        resolver.slot_files({"slot": [new, ui("2025-07-02", 3)]}, ["slot"])


# Every path that asks which official parts a mod's files replace or update asks the resolver

#: (file, class, function): the call sites of the resolver
CALL_SITES = [
    ("src/bmpt/library/service.py", "ModLibrary", name) for name in (
        "_check_group", "_replacements", "_update_groups", "_groups_of", "_replaced_parts", "direct_import",
        "preview_complements", "_plan", "_other_targets", "_other_matches", "_game_files", "_install_direct")
] + [("src/bmpt/webui/batch_import.py", "BatchImport", "_judge")]
#: what only the resolver may do: upstream's planner and matcher, the newest-date and part rules
FORBIDDEN_CALLS = {"plan_mod_install", "asset_match", "_asset_match", "search_prefix", "_search_latest_targets",
                   "part_of", "date_of", "update_targets", "replacements"}
RESOLVER_CALLS = {"_resolve", "_replacements", "_replaced_parts", "_groups_of", "_other_targets", "_other_matches",
                  "resolve", "generic", "slot_files", "same_prefix"}


def functions(path: str) -> dict[tuple[str, str], ast.FunctionDef]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return {(cls.name, node.name): node for cls in tree.body if isinstance(cls, ast.ClassDef)
            for node in cls.body if isinstance(node, ast.FunctionDef)}


def called(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


@pytest.mark.parametrize("path,cls,name", CALL_SITES)
def test_target_call_sites_delegate_to_the_resolver(path, cls, name):
    node = functions(path)[(cls, name)]
    calls = called(node)
    assert not calls & FORBIDDEN_CALLS, f"{cls}.{name} decides targets itself: {sorted(calls & FORBIDDEN_CALLS)}"
    assert calls & RESOLVER_CALLS or any(isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
                                         and item.value.id == "resolver" for item in ast.walk(node)), name


def test_only_the_resolver_uses_upstreams_target_planner_and_matcher():
    offenders = []
    for path in sorted((ROOT / "src/bmpt").rglob("*.py")):
        if path.name == "targets.py" and path.parent.name == "library":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                if imported & {"plan_mod_install", "asset_match", "_asset_match", "search_prefix",
                               "_search_latest_targets"}:
                    offenders.append(f"{path.relative_to(ROOT)}: imports {sorted(imported)}")
            if isinstance(node, ast.Attribute) and node.attr in {"plan_mod_install", "asset_match", "_asset_match"}:
                offenders.append(f"{path.relative_to(ROOT)}: uses {node.attr}")
    assert not offenders, offenders
