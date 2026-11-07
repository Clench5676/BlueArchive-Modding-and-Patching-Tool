"""Mobile builds of a Spine core on a Steam install, made from clones of the official files of the development view.

Global's mobile build names a core's parts by resource type (``-textassets-``, ``-textures-``) under its own date,
while the Steam build may keep the same assets in numbered parts under several dates: ch0242_spr keeps its skeleton,
atlas and page under 2024-11-18, and its newest date holds one added clip. Nothing here is committed: the mod files
are made at test time from the view's bundles, with new CAB names and the Android build target.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_batch_import import analyze, run

CORES = ("ch0242_spr", "ch0242_home")
MOBILE_DATE = "2025-07-02"


def types_of(path: Path) -> set[str]:
    import UnityPy
    return {obj.type.name for obj in UnityPy.load(str(path)).objects}


def mobile_copy(official: Path, folder: Path, part: str) -> Path:
    """The part as a mobile build ships it: named by resource type, another CAB, the Android build target; a mod's
    page differs from the official one, so the first block of each streamed page is changed."""
    import UnityPy
    from UnityPy.enums import BuildTarget
    from UnityPy.streams import EndianBinaryReader
    from ba_modding_toolkit.naming import parse_filename

    parsed = parse_filename(official.name)
    path = folder / f"{parsed.prefix}{part}-{MOBILE_DATE}_assets_all_{parsed.crc}.bundle"
    bundle = UnityPy.load(str(official)).file
    renames = {key: "CAB-" + hashlib.md5((key + part).encode()).hexdigest()
               for key in bundle.files if key.startswith("CAB-") and not key.endswith(".resS")}
    edits: dict[str, list[int]] = {}
    files = {}
    for key, inner in bundle.files.items():
        base = key.removesuffix(".resS")
        name = renames.get(base, base) + (".resS" if key.endswith(".resS") else "")
        if hasattr(inner, "objects"):
            inner.target_platform, inner._m_target_platform = BuildTarget.Android, int(BuildTarget.Android)
            for obj in inner.objects.values():
                if obj.type.name == "Texture2D":
                    data = obj.read()
                    if data.m_StreamData and data.m_StreamData.path:
                        edits.setdefault(data.m_StreamData.path.rsplit("/", 1)[-1], []).append(data.m_StreamData.offset)
                        for old, new in renames.items():
                            data.m_StreamData.path = data.m_StreamData.path.replace(old, new)
                        data.save()
        if hasattr(inner, "name"):
            inner.name = name
        files[name] = inner
    for key, offsets in edits.items():
        name = renames.get(key.removesuffix(".resS"), key.removesuffix(".resS")) + ".resS"
        data = bytearray(files[name].bytes)
        for offset in offsets:
            data[offset:offset + 16] = bytes(byte ^ 0x5A for byte in data[offset:offset + 16])
        changed = EndianBinaryReader(bytes(data))
        changed.flags = files[name].flags
        files[name] = changed
    bundle.files = files
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bundle.save())
    return path


@pytest.fixture
def mobile(tmp_path):
    from bmpt.catalog import Catalog
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.cabs import CabIndex
    from bmpt.library import ModLibrary, integrity
    from bmpt.library.judge import core_of
    from bmpt.library.storage import atomic_clone
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services
    from ba_modding_toolkit.naming import parse_filename
    from ba_modding_toolkit.searching import get_search_dirs

    root = os.environ.get("BAMT_TEST_GAME_DIR")
    if not root:
        pytest.skip("Set BAMT_TEST_GAME_DIR to a verified official development view")
    directories = get_search_dirs(Path(root))
    official = sorted(path for directory in directories for path in directory.glob("*.bundle")
                      if core_of(path.name) in CORES)
    parts = {}
    for core in CORES:
        own = [path for path in official if core_of(path.name) == core]
        text = [path for path in own if "TextAsset" in types_of(path)]
        pages = [path for path in own if "Texture2D" in types_of(path)]
        if len(text) != 1 or len(pages) != 1:
            pytest.skip(f"{core} no longer keeps one skeleton part and one page part")
        parts[core] = text[0], pages[0], max(parse_filename(path.name).date for path in own)
    if parse_filename(parts["ch0242_spr"][0].name).date == parts["ch0242_spr"][2]:
        pytest.skip("ch0242_spr no longer keeps its skeleton under an older date than its newest part")
    inspection = integrity.Inspection()
    references = set().union(*(inspection.inspect(path).references for path in official))
    suppliers = CabIndex(directories).resolve(references, official[0])
    clones = tmp_path / "game"
    clones.mkdir()
    for path in set(official) | {Path(path) for path in suppliers.values()}:
        assert atomic_clone(path, clones / path.name)
        (clones / path.name).chmod(0o644)
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(clones))
    settings.set("enable_crc_correction", "false")
    settings.set("compression_method", "none")
    game = GameAssets([clones], AssetCache(allowed=False, root=tmp_path / "cache"))
    catalog = Catalog(game, None, server="global")
    library = ModLibrary(tmp_path / "library", catalog, game, settings)
    services = Services(settings, tmp_path / "app", cache=object(), game=game, catalog=catalog, library=library)
    services.game_found = lambda: False
    folder = tmp_path / "mod"
    mods = {core: (mobile_copy(text, folder, "textassets"), mobile_copy(page, folder, "textures"))
            for core, (text, page, _) in parts.items()}
    try:
        yield SimpleNamespace(services=services, rpc=build_api(services), mods=mods, parts=parts, library=library)
    finally:
        services.close()


def test_mobile_parts_convert_into_parts_kept_under_older_dates_and_import(mobile):
    portrait, lobby = mobile.mods["ch0242_spr"], mobile.mods["ch0242_home"]
    rows = {row["kind"]: row for row in analyze(mobile, [*portrait, *lobby])["entries"]}
    assert set(rows) == {"spr", "lobby"}
    for row in rows.values():
        assert (row["state"], row["reason"]) == ("convert", None), row
        assert row["source"] == {"server": "global", "platform": "android", "detected": True}
    text, page, _ = mobile.parts["ch0242_spr"]
    assert sorted(path.name for path in mobile.services.batch_import._latest[rows["spr"]["id"]].targets) == sorted(
        [text.name, page.name])
    result, = run(mobile, [rows["spr"]], enableAfterImport=False)
    assert result["outcome"] == "updated" and result["libraryId"], result
    # The mod's page goes into the part dated 2024-11-18 that holds it; its unchanged skeleton part stays official.
    output = dict(mobile.library._record(result["libraryId"])["import_sources"][0]["output"])
    assert sorted(output) == sorted([text.name, page.name])
    assert output[text.name] == hashlib.sha1(text.read_bytes()).hexdigest()
    assert output[page.name] != hashlib.sha1(page.read_bytes()).hexdigest()


def test_one_selected_mobile_part_per_core_finds_the_other_in_its_folder(mobile):
    portrait, lobby = mobile.mods["ch0242_spr"], mobile.mods["ch0242_home"]
    for chosen in (0, 1):
        rows = {row["kind"]: row for row in analyze(mobile, [portrait[chosen], lobby[chosen]])["entries"]}
        for kind, files in (("spr", portrait), ("lobby", lobby)):
            row = rows[kind]
            assert row["state"] == "incomplete" and row["missing"]["unresolved"] == 0
            assert [found["path"] for found in row["missing"]["found"]] == [str(files[1 - chosen])]
