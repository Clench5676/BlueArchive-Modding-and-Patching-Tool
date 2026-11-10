"""One mod whose parts carry different dates is one mod, in the batch import and in Add."""

from __future__ import annotations

from pathlib import Path

import pytest

from bmpt.library.judge import mod_versions
from ba_modding_toolkit.naming import parse_filename

from test_batch_import import analyze, batch_model_env, env, rpc, run
from test_library import game_bundle, make_mod, setup_library
from test_library_model import model_env

TEXTURE_DATE, TEXT_DATE = "2026-01-01", "2026-03-01"


def name_of(core, part, date):
    return f"assets-_mx-spinecharacters-{core}-_mxdependency-{part}-{date}_assets_all_1.bundle"


def dated_targets(env):
    """A core whose text part is dated after its texture part, as 27 Spine and 617 model cores are in the game."""
    return [env.original, game_bundle(env.game_dir, part="textassets", date=TEXT_DATE, payload=b"official text")]


def dated_sources(env, targets, folder="input"):
    return [make_mod(target, env.tmp / folder, payload=f"{folder} {i}".encode()) for i, target in enumerate(targets)]


def stored_names(library, mod):
    return frozenset(path.name for path in library._paths(library._record(mod.id)))


def row_names(rows):
    return sorted(sorted(Path(path).name for path in row["paths"]) for row in rows)


def mod_names(library, mods):
    return sorted(sorted(stored_names(library, mod)) for mod in mods)


# The rule itself

def test_one_set_with_every_part_once_is_one_version_whatever_the_dates():
    names = [name_of("a_spr", "textures", TEXTURE_DATE), name_of("a_spr", "textassets", TEXT_DATE),
             name_of("a_spr", "materials", "2025-05-05")]
    assert mod_versions([("a", name) for name in names]) == ["", "", ""]
    assert mod_versions([]) == []


def test_a_part_repeated_under_other_dates_splits_the_whole_set_by_date():
    names = [name_of("a_spr", "textures", "2025-01-01"), name_of("a_spr", "textures", TEXTURE_DATE),
             name_of("a_spr", "textassets", TEXTURE_DATE)]
    assert mod_versions([("a", name) for name in names]) == ["2025-01-01", TEXTURE_DATE, TEXTURE_DATE]


def test_sets_are_judged_apart_and_the_same_part_under_one_date_is_not_a_repeat():
    first, second = name_of("a_spr", "textures", "2025-01-01"), name_of("a_spr", "textures", TEXTURE_DATE)
    other = [name_of("b_spr", "textures", TEXTURE_DATE), name_of("b_spr", "textassets", TEXT_DATE),
             name_of("b_spr", "textures", TEXTURE_DATE)]
    entries = [("a", first), ("b", other[0]), ("a", second), ("b", other[1]), ("b", other[2])]
    assert mod_versions(entries) == ["2025-01-01", "", TEXTURE_DATE, "", ""]


# The review's reproduction, through the batch import

def test_batch_import_analyzes_differently_dated_parts_as_one_row_and_applies_all_of_them(env):
    targets = dated_targets(env)
    sources = dated_sources(env, targets)
    entry, = analyze(env, [sources[0].parent])["entries"]
    assert entry["paths"] == sorted(map(str, sources))
    assert (entry["variant"], entry["core"], entry["kind"], entry["state"], entry["reason"]) == (
        "test", "test_spr", "spr", "ready", None)
    assert {file["target"] for file in entry["files"]} == {target.name for target in targets}
    result, = run(env, [entry], backup=False)
    assert (result["outcome"], result["applied"], result["configured"]) == ("imported", True, True)
    mod, = env.library.mods()
    assert result["libraryId"] == mod.id and stored_names(env.library, mod) == {target.name for target in targets}
    assert mod.date == TEXT_DATE
    assert env.library.state("test").applied == f"mod:{mod.id}"
    assert [target.read_bytes() for target in targets] == [source.read_bytes() for source in sources]
    env.library.restore("test")
    assert env.library.state("test").applied == "official"


def test_the_repeated_part_with_another_date_still_makes_one_mod_per_date(env):
    older = game_bundle(env.tmp / "old-release", date="2025-01-01", payload=b"old texture")
    text = game_bundle(env.game_dir, part="textassets", date=TEXTURE_DATE, payload=b"official text")
    sources = [make_mod(env.original, env.tmp / "input", payload=b"new texture"),
               make_mod(text, env.tmp / "input", payload=b"new text"),
               make_mod(older, env.tmp / "input", payload=b"old texture mod")]
    expected = sorted([[sources[2].name], sorted([sources[0].name, sources[1].name])])
    assert row_names(analyze(env, [sources[0].parent])["entries"]) == expected
    assert mod_names(env.library, env.library.add(sources)) == expected


def test_the_same_files_from_two_folders_stay_two_alternatives_in_the_batch_import(env):
    targets = dated_targets(env)
    first, second = dated_sources(env, targets, "first"), dated_sources(env, targets, "second")
    rows = analyze(env, [first[0].parent, second[0].parent])["entries"]
    assert sorted(row["paths"] for row in rows) == sorted([sorted(map(str, first)), sorted(map(str, second))])


def test_mixed_folders_with_two_cores_and_a_lobby_make_one_row_per_core(env):
    other = [game_bundle(env.game_dir, "other_spr", date=TEXTURE_DATE),
             game_bundle(env.game_dir, "other_spr", part="textassets", date=TEXT_DATE)]
    lobby = game_bundle(env.game_dir, "test_home_gl", date="2025-12-01")
    sources = [*dated_sources(env, dated_targets(env)), *dated_sources(env, other), *dated_sources(env, [lobby])]
    rows = analyze(env, [sources[0].parent])["entries"]
    assert sorted((row["core"], row["kind"], len(row["paths"])) for row in rows) == [
        ("other_spr", "spr", 2), ("test_home_gl", "lobby", 1), ("test_spr", "spr", 2)]
    assert {row["state"] for row in rows} == {"ready"}
    assert [result["outcome"] for result in run(env, rows, backup=False)] == ["imported"] * 3
    assert len(env.library.mods()) == 3


# The same files through Add, drag and drop

@pytest.mark.parametrize("method", ["add", "add_and_apply", "rpc"])
def test_add_stores_differently_dated_parts_as_one_mod_and_applies_all_of_them(env, method):
    targets = dated_targets(env)
    sources = dated_sources(env, targets)
    if method == "add":
        added = env.library.add(sources)
    elif method == "add_and_apply":
        added = env.library.add_and_apply(sources)
        assert [mod.id for mod in added.applied] == [mod.id for mod in added]
    else:
        result = rpc(env, "mods.add", paths=[str(source) for source in sources])
        assert [item["date"] for item in result["added"]] == [TEXT_DATE]
        assert [item["id"] for item in result["applied"]] == [item["id"] for item in result["added"]]
        added = env.library.mods()
    mod, = added
    assert len(env.library.mods()) == 1 and mod.date == TEXT_DATE
    assert stored_names(env.library, mod) == {target.name for target in targets}
    if method != "add":
        assert env.library.state("test").applied == f"mod:{mod.id}"
        assert [target.read_bytes() for target in targets] == [source.read_bytes() for source in sources]


def test_a_mod_is_dated_by_its_newest_part_not_by_the_part_listed_first(env):
    oldest = game_bundle(env.game_dir, part="000", date="2025-05-05", payload=b"official first part")
    sources = dated_sources(env, [oldest, *dated_targets(env)])
    mod, = env.library.add(sources)
    assert len(stored_names(env.library, mod)) == 3
    assert parse_filename(mod.file.name).date == "2025-05-05" and mod.date == TEXT_DATE


def test_add_does_not_store_the_same_complete_set_twice(env):
    sources = dated_sources(env, dated_targets(env))
    first, = env.library.add(sources)
    again = env.library.add(sources)
    assert not again and [mod.id for mod in again.duplicates] == [first.id]
    assert len(env.library.mods()) == 1


def test_drag_and_drop_groups_like_the_batch_import(env):
    older = game_bundle(env.tmp / "old-release", date="2025-01-01", payload=b"old texture")
    other = [game_bundle(env.game_dir, "other_spr", date=TEXTURE_DATE),
             game_bundle(env.game_dir, "other_spr", part="textassets", date=TEXT_DATE)]
    lobby = game_bundle(env.game_dir, "test_home_gl", date="2025-12-01")
    sources = [*dated_sources(env, dated_targets(env)), *dated_sources(env, other), *dated_sources(env, [lobby]),
               make_mod(older, env.tmp / "input", payload=b"old texture mod")]
    batch = row_names(analyze(env, [sources[0].parent])["entries"])
    # test_spr repeats its texture under two dates, so its set goes by date (3 rows); the other core and the lobby
    # keep one row each.
    assert len(batch) == 5
    assert mod_names(env.library, env.library.add(sources)) == batch


# Model cores

def test_model_prefab_and_dependencies_with_other_dates_are_one_row_and_one_mod(batch_model_env):
    env = batch_model_env
    prefab = env.parts[0]
    env.parts[0] = prefab.rename(prefab.with_name(prefab.name.replace("2026-01-01", "2026-02-01")))
    sources = [make_mod(path, env.tmp / "input", payload=f"mod {i}".encode()) for i, path in enumerate(env.parts)]
    entry, = analyze(env, [sources[0].parent])["entries"]
    assert (entry["kind"], entry["core"], entry["state"], entry["paths"]) == (
        "model", "hoshino_original", "ready", sorted(map(str, sources)))
    result, = run(env, [entry], backup=False)
    assert (result["outcome"], result["applied"]) == ("imported", True)
    mod, = env.library.mods()
    assert mod.kind == "model" and mod.date == "2026-02-01" and len(stored_names(env.library, mod)) == 3
    assert [path.read_bytes() for path in env.parts] == [source.read_bytes() for source in sources]
    assert len(env.library.add(sources).duplicates) == 1 and len(env.library.mods()) == 1


# A part with nothing the import transfers (BL-156)

def passengers(monkeypatch, *paths):
    """Makes the given files hold no asset of the import's types, every other file one texture."""
    from bmpt.library import integrity
    empty = {Path(path).resolve() for path in paths}
    monkeypatch.setattr(integrity.Inspection, "keys",
                        lambda self, path: set() if Path(path).resolve() in empty else {("texture", "Texture2D")})


def test_a_part_without_assets_of_the_import_types_under_another_date_stays_in_its_mod(env, monkeypatch):
    clips = game_bundle(env.tmp / "old-release", date="2025-01-01", payload=b"animation data")
    sources = [make_mod(env.original, env.tmp / "input", payload=b"new texture"),
               make_mod(clips, env.tmp / "input", payload=b"animation data")]
    passengers(monkeypatch, sources[1])
    entry, = analyze(env, [sources[0].parent])["entries"]
    assert entry["paths"] == sorted(map(str, sources))
    assert entry["reason"] != "no_matching_assets"


def test_a_part_without_assets_does_not_join_one_of_several_versions(env, monkeypatch):
    older = game_bundle(env.tmp / "old-release", date="2025-01-01", payload=b"old texture")
    clips = game_bundle(env.tmp / "older-release", date="2024-01-01", payload=b"animation data")
    sources = [make_mod(env.original, env.tmp / "input", payload=b"new texture"),
               make_mod(older, env.tmp / "input", payload=b"old texture mod"),
               make_mod(clips, env.tmp / "input", payload=b"animation data")]
    passengers(monkeypatch, sources[2])
    assert row_names(analyze(env, [sources[0].parent])["entries"]) == sorted([[source.name] for source in sources])


def test_parts_without_assets_alone_keep_the_date_rule(env, monkeypatch):
    older = game_bundle(env.tmp / "old-release", date="2025-01-01", payload=b"old data")
    sources = [make_mod(env.original, env.tmp / "input", payload=b"new data"),
               make_mod(older, env.tmp / "input", payload=b"old data mod")]
    passengers(monkeypatch, *sources)
    assert row_names(analyze(env, [sources[0].parent])["entries"]) == sorted([[source.name] for source in sources])
