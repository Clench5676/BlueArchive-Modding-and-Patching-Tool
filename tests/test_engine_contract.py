"""The contract between the App and the upstream engine, ``ba_modding_toolkit``.

The App reaches the engine only through ``bmpt.engine``; these tests pin that boundary and the engine behaviour the
App builds on: bundle file names (``parse_filename``, ``spine_kind``), the asset match keys, the CRC fix and a load
and save round trip. They are the first check after an upstream sync: a failure names the engine change the App has
to follow. The official-data tests read the verified view named by ``BAMT_TEST_GAME_DIR`` and skip without it.
"""

from __future__ import annotations

import ast
import importlib
import os
import zlib
from pathlib import Path

import pytest

import bmpt
from bmpt import engine
from bmpt.engine import (
    REPLACEABLE_ASSET_TYPES, Bundle, ContNameTypeKey, CRCUtils, SaveOptions, get_search_dirs, parse_filename,
    parse_hex_bytes, spine_kind,
)

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
needs_game = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
APP_DIR = Path(bmpt.__file__).parent
ENGINE_FILE = APP_DIR / "engine.py"


# --- the facade ---

def test_every_export_is_the_engine_object_itself():
    assert set(engine.__all__) == {"ENGINE_PACKAGE", *engine._EXPORTS}
    for name in engine._EXPORTS:
        module_name, attribute = engine.source_of(name)
        module = importlib.import_module(module_name)
        assert getattr(engine, name) is (module if attribute is None else getattr(module, attribute)), name


def _type_checking_imports() -> dict[str, tuple[str, str]]:
    tree = ast.parse(ENGINE_FILE.read_text(encoding="utf-8"))
    block = next(node for node in tree.body if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING")
    imports: dict[str, tuple[str, str]] = {}
    for node in block.body:
        assert isinstance(node, ast.ImportFrom), ast.unparse(node)
        for alias in node.names:
            imports[alias.asname or alias.name] = (node.module, alias.name)
    return imports


def test_the_type_checking_imports_list_the_same_names():
    imports = _type_checking_imports()
    assert set(imports) == set(engine._EXPORTS)
    for name, (module_name, attribute) in imports.items():
        assert getattr(importlib.import_module(module_name), attribute) is getattr(engine, name), name


def test_importing_the_facade_imports_nothing_from_the_engine():
    tree = ast.parse(ENGINE_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(module.startswith(engine.ENGINE_PACKAGE) for module in modules), ast.unparse(node)


def test_exports_follow_the_engine_module_and_unknown_names_fail(monkeypatch):
    naming = importlib.import_module(f"{engine.ENGINE_PACKAGE}.naming")
    replacement = lambda filename: None  # noqa: E731
    monkeypatch.setattr(naming, "parse_filename", replacement)
    assert engine.parse_filename is replacement  # read on every access, never copied
    with pytest.raises(AttributeError):
        engine.no_such_name  # noqa: B018
    with pytest.raises(ImportError):
        from bmpt.engine import no_such_name  # noqa: F401


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def test_app_modules_reach_the_engine_only_through_the_facade():
    package = engine.ENGINE_PACKAGE
    found = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == ENGINE_FILE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                names = [node.value]  # importlib, "-m", patch targets
            else:
                continue
            if any(name == package or name.startswith(package + ".") for name in names):
                found.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert not found, f"import these through bmpt.engine: {found}"


# --- file names ---

NAMES = {
    # numbered part after the date
    "assets-_mx-spinecharacters-ch0063_spr-_mxdependency-2024-11-18_002_assets_all_895560895.bundle":
        ("spinecharacters", "ch0063_spr", "002", "2024-11-18", "895560895",
         "assets-_mx-spinecharacters-ch0063_spr-_mxdependency-"),
    "assets-_mx-spinelobbies-ch0063_home-_mxdependency-2024-11-18_001_assets_all_8501068.bundle":
        ("spinelobbies", "ch0063_home", "001", "2024-11-18", "8501068",
         "assets-_mx-spinelobbies-ch0063_home-_mxdependency-"),
    "assets-_mx-characters-ch0063-_mxdependency-2025-07-25_000_assets_all_3434860653.bundle":
        ("characters", "ch0063", "000", "2025-07-25", "3434860653", "assets-_mx-characters-ch0063-_mxdependency-"),
    # resource type before the date: the prefix leaves it out, so both forms of a core share one prefix
    "assets-_mx-characters-ch0305-_mxdependency-textures-2026-04-06_assets_all_4018753285.bundle":
        ("characters", "ch0305", "textures", "2026-04-06", "4018753285",
         "assets-_mx-characters-ch0305-_mxdependency-"),
    # preload group
    "prologdepengroup-assets-_mx-3dobject-lope-_mxprolog-2024-11-18_assets_all_1299732014.bundle":
        ("3dobject", "lope", None, "2024-11-18", "1299732014",
         "prologdepengroup-assets-_mx-3dobject-lope-_mxprolog-"),
    # a generic bundle (the library's "other" kind, keyed by prefix and resource type)
    "uis-01_common-14_charactercollect-_mxload-2024-11-18_000_assets_all_159150400.bundle":
        ("uis", "01_common-14_charactercollect", "000", "2024-11-18", "159150400",
         "uis-01_common-14_charactercollect-_mxload-"),
    # no date: no prefix, the CRC still read
    "6befbbb5c811cb1575f22145d4f0a954_unitybuiltinshaders_3925052273.bundle":
        (None, "6befbbb5c811cb1575f22145d4f0a954_unitybuiltinshaders_3925052273", None, "", "3925052273", ""),
    # a loose asset of a mod
    "ch0063_spr.skel": (None, "ch0063_spr", None, "", "", ""),
}


@pytest.mark.parametrize("name", NAMES)
def test_parse_filename_splits_official_names(name):
    assert tuple(parse_filename(name)) == NAMES[name]
    assert parse_filename(name)._fields == ("category", "core", "res_type", "date", "crc", "prefix")


def test_a_new_date_or_crc_keeps_a_part_s_prefix_and_type():
    old = parse_filename("assets-_mx-spinecharacters-ch0063_spr-_mxdependency-2024-11-18_002_assets_all_895560895.bundle")
    new = parse_filename("assets-_mx-spinecharacters-ch0063_spr-_mxdependency-2026-11-10_002_assets_all_1.bundle")
    assert (new.prefix, new.res_type, new.core) == (old.prefix, old.res_type, old.core)
    assert (new.date, new.crc) == ("2026-11-10", "1")


@pytest.mark.parametrize("core, category, kind", [
    ("ch0063_spr", "spinecharacters", "spr"),
    ("ch0063_home", "spinelobbies", "lobby"),
    ("bg_special_01", "spinebackground", "lobby"),
    ("ch0063", "characters", None),
    ("mxcommon", "spinecharacters", None),
    ("ch0063_spr", None, "spr"),
    ("ch0063_home_gl", None, "lobby"),
    ("ch0063", None, None),
])
def test_spine_kind_tells_portraits_from_lobbies(core, category, kind):
    assert spine_kind(core, category) == kind


@needs_game
def test_every_official_bundle_name_parses():
    names = sorted(path.name for folder in get_search_dirs(GAME_DIR) if folder.is_dir()
                   for path in folder.glob("*.bundle"))
    assert len(names) > 1000
    types = importlib.import_module(f"{engine.ENGINE_PACKAGE}.naming")
    known = types.RESOURCE_TYPES_TEXT | types.RESOURCE_TYPES_NUM | {None}
    undated = []
    for name in names:
        parsed = parse_filename(name)
        assert parsed.crc.isdigit() and parsed.core and parsed.res_type in known, name
        if parsed.date:
            assert name.startswith(parsed.prefix) and parsed.prefix, name
        else:
            undated.append(name)
    assert all("unitybuiltinshaders" in name for name in undated), undated


# --- match keys ---

def test_unknown_match_strategy_is_refused():
    with pytest.raises(ValueError):
        Bundle._get_key_func("by_guess")


def _official_samples() -> list[Path]:
    """The smallest official portrait part with a texture, lobby part with a skeleton or atlas and model part with a
    mesh."""
    folders = [folder for folder in get_search_dirs(GAME_DIR) if folder.is_dir()]
    samples = []
    for pattern, kind in (("assets-_mx-spinecharacters-*_spr-*_003_*.bundle", "Texture2D"),
                          ("assets-_mx-spinelobbies-*_home-*_002_*.bundle", "TextAsset"),
                          ("assets-_mx-characters-*_004_*.bundle", "Mesh")):
        candidates = sorted((path for folder in folders for path in folder.glob(pattern)),
                            key=lambda path: (path.stat().st_size, path.name))
        for path in candidates[:50]:
            bundle = Bundle.load(path)
            if bundle and any(obj.type.name == kind for obj in bundle.env.objects):
                samples.append(path)
                break
        else:
            pytest.fail(f"no official {pattern} with a {kind}")
    return samples


@pytest.fixture(scope="module")
def official_samples():
    if not GAME_DIR.is_dir():
        pytest.skip("Set BAMT_TEST_GAME_DIR to a verified official view")
    return _official_samples()


@needs_game
def test_match_keys_name_an_asset_by_container_name_and_type(official_samples):
    for path in official_samples:
        bundle = Bundle.load(path)
        cont_name_type = Bundle._get_key_func("cont_name_type")
        name_type = Bundle._get_key_func("name_type")
        for obj in bundle.env.objects:
            key = cont_name_type(obj)
            assert isinstance(key, ContNameTypeKey)
            assert tuple(key) == (obj.container, obj.peek_name(), obj.type.name)
            assert tuple(name_type(obj)) == (obj.peek_name(), obj.type.name)
            assert Bundle._get_key_func("path_id")(obj) == obj.path_id
        assert bundle.get_asset_keys("cont_name_type") == {cont_name_type(obj) for obj in bundle.env.objects}


# --- CRC fix ---

@pytest.mark.parametrize("target", [0, 1, 0x7FFFFFFF, 0xFFFFFFFF, 3434860653])
def test_crc_fix_appends_four_bytes_that_reach_the_target(target):
    data = bytes(range(256)) * 17
    fixed = CRCUtils.apply_crc_fix(data, target)
    assert fixed is not None and fixed[:-4] == data and len(fixed) == len(data) + 4
    assert zlib.crc32(fixed) == CRCUtils.compute_crc32(fixed) == target


@needs_game
def test_official_bundles_carry_their_crc_in_their_name():
    names = sorted(path for folder in get_search_dirs(GAME_DIR) if folder.is_dir() for path in folder.glob("*.bundle"))
    sample = names[::97]
    assert len(sample) > 100
    for path in sample:
        assert zlib.crc32(path.read_bytes()) == int(parse_filename(path.name).crc), path.name
        assert Bundle.check_crc_match(path) and Bundle.get_trailing_bytes(path) == 0, path.name


# --- load and save ---

@needs_game
def test_load_save_round_trip_keeps_every_object_and_the_name_crc(official_samples, tmp_path):
    # the App's default save: LZMA, CRC fix with the settings' extra bytes
    extra = parse_hex_bytes("0x08080808")
    options = SaveOptions(perform_crc=True, extra_bytes=extra, compression="lzma")
    pixels = 0
    for path in official_samples:
        official = Bundle.load(path)
        assert official is not None and official.need_crc() and Bundle.check_need_crc(path)
        out = tmp_path / path.name
        ok, message = official.save(out, options)
        assert ok, message
        data = out.read_bytes()
        assert zlib.crc32(data) == int(official.crc) and Bundle.check_crc_match(out)
        assert Bundle.get_trailing_bytes(out) == len(extra) + 4
        again = Bundle.load(out)
        assert again is not None and again.platform_info == official.platform_info
        assert again.get_asset_keys("cont_name_type") == official.get_asset_keys("cont_name_type")
        before = {obj.path_id: (obj.type.name, obj.get_raw_data(), _stream(obj)) for obj in official.env.objects}
        after = {obj.path_id: (obj.type.name, obj.get_raw_data(), _stream(obj)) for obj in again.env.objects}
        assert after == before, path.name
        pixels += sum(len(stream or b"") for _, _, stream in before.values())
    assert pixels  # the portrait's texture came through, not only object headers


def _stream(obj) -> bytes | None:
    """A texture's pixels, kept outside the object in the bundle's resource file."""
    return bytes(obj.read().get_image_data()) if obj.type.name == "Texture2D" else None
