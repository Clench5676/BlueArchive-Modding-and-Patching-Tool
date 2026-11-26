"""Filename CRCs and verified official references for catalog hash names."""

import json
import shutil
import zlib
from pathlib import Path

import pytest

from bmpt.core.errors import ServiceError
from bmpt.engine import Bundle, SaveOptions, parse_filename
from bmpt.host.steam_records import OfficialFile
from bmpt.library.bundle_crc import name_crc, name_hash, name_suffix, target_crc
from bmpt.library.install import Action, PlannedFile, PlannedGroup, install_staged, matches_target_crc
from bmpt.library.integrity import replacement_key
from bmpt.library.judge import crc_state, resource_type_of_file
from bmpt.library.service.base import _rebuilt
from test_library import header_bundle, reload_library, setup_library
from test_target_crc import resave

HASH = "908885688f7bc516086bcb402c289abe"
JP_COPY = Path.home() / "Library/Caches/BAMT-Dev/jp/1.75-r98-16"
JP_BUNDLES = JP_COPY / "BlueArchive_JP/BlueArchive_Data/StreamingAssets/AssetBundles"
JP_CATALOG = JP_COPY / "BundlePackingInfo.json"


@pytest.mark.parametrize("suffix,crc,digest", [("000", 0, None), (HASH, None, HASH), ("7" * 32, None, "7" * 32)])
def test_bundle_name_suffixes(suffix, crc, digest):
    stem = "uis-01_common-14_charactercollect-_mxload-2025-07-02_000_assets_all"
    name = f"{stem}_{suffix}.bundle"
    assert name_crc(name) == crc
    assert name_hash(name) == digest
    assert name_suffix(name) == suffix
    assert target_crc(name) == crc
    assert replacement_key(name) == replacement_key(name + ".backup") == f"{stem}.bundle"
    assert resource_type_of_file(name) == "other"


def test_bundle_name_without_crc_or_hash():
    name = "uis-01_common-14_charactercollect-_mxload-2025-07-02_000_assets_all.bundle"
    assert name_crc(name) is None
    assert name_hash(name) is None
    assert name_suffix(name) is None
    assert replacement_key(name) == name
    assert resource_type_of_file(name) is None


@pytest.mark.parametrize("suffix,expected", [("42", None), (HASH, 42)])
def test_crc_state_uses_the_bundle_target_crc(suffix, expected):
    name = f"target_{suffix}.bundle"
    assert crc_state(42, 0, name, expected_crc=expected) == "ok"
    assert crc_state(42, 4, name, expected_crc=expected) == "fixed"
    assert crc_state(42, None, name, expected_crc=expected) == "needs_fix"
    assert crc_state(43, 0, name, expected_crc=expected) == "needs_fix"
    assert crc_state(42, 0, f"target_{HASH}.bundle") == "needs_fix"


@pytest.fixture(params=["decimal", "hash"])
def target(request, setup_library):
    env = setup_library
    env.settings.set("enable_crc_correction", "true")
    path = env.original
    if request.param == "hash":
        renamed = path.with_name(f"{replacement_key(path.name).removesuffix('.bundle')}_{HASH}.bundle")
        path.rename(renamed)
        assert _rebuilt({"test_spr": [renamed]}, path) == renamed
        path = renamed
        env.library = reload_library(env)
    return env, path


def test_needs_crc_fix_and_matches_target_crc(target):
    env, path = target
    library = env.library
    expected = zlib.crc32(path.read_bytes())
    source = env.tmp / "source.bundle"
    source.write_bytes(header_bundle(b"modified"))
    assert library._needs_crc_fix(source, path)
    assert not matches_target_crc(source, path)
    source.write_bytes(path.read_bytes() + b"tail")
    assert library._needs_crc_fix(source, path)
    source.write_bytes(path.read_bytes())
    assert not library._needs_crc_fix(source, path)
    assert matches_target_crc(source, path)
    assert target_crc(path.name, path) == expected


def test_hash_kept_original_remains_the_crc_reference(setup_library, resave):
    env = setup_library
    env.settings.set("enable_crc_correction", "true")
    path = env.original.with_name(f"{replacement_key(env.original.name).removesuffix('.bundle')}_{HASH}.bundle")
    env.original.rename(path)
    official = path.read_bytes()
    expected = zlib.crc32(official)
    library = reload_library(env)
    pin = library.current_pin()
    assert library._official_for(path, pin, full=False) == path
    assert library._official_for(path, pin) == path
    upload = env.tmp / "upload" / path.name
    upload.parent.mkdir()
    upload.write_bytes(header_bundle(b"changed portrait"))
    (mod,) = library.add([upload])
    assert mod.crc == HASH
    library.apply("test", f"mod:{mod.id}")
    assert zlib.crc32(path.read_bytes()) == expected
    assert library.state("test").crc == "fixed"
    assert library._original_checks.record(path.name)["crc"] == expected
    kept = library._original_path(path)
    assert kept.read_bytes() == official
    assert not matches_target_crc(kept, path)
    path.write_bytes(header_bundle(b"outside change"))
    assert library._target_crc(path, pin) == expected
    assert not library._is_official_as(path, path.name)
    assert matches_target_crc(kept, path, official=kept)
    with pytest.raises(ServiceError, match="crc_fix_failed"):
        library._check_target_crc({path: upload}, pin)
    backup = path.with_name(path.name + ".backup")
    backup.write_bytes(official)
    kept.write_bytes(header_bundle(b"changed original"))
    assert library._target_crc(path, pin) is None


def test_hash_fallback_requires_readable_header_and_respects_steam(target, monkeypatch):
    env, path = target
    library = env.library
    pin = library.current_pin()
    data = path.read_bytes()
    path.write_bytes(data + b"tail")
    assert not library._is_official_as(path, path.name)
    path.write_bytes(b"bad header")
    assert not library._is_official_as(path, path.name)
    path.write_bytes(data)
    assert library._is_official_as(path, path.name)
    monkeypatch.setattr(library, "_steam_official", lambda *args: OfficialFile(len(data), "0" * 40))
    assert not library._is_official_as(path, path.name)
    if name_hash(path.name):
        with pytest.raises(ServiceError, match="official_unavailable"):
            library._official_for(path, pin, full=False)


def test_japan_hoshino_resave_matches_catalog_crc(tmp_path):
    if not JP_BUNDLES.is_dir() or not JP_CATALOG.is_file():
        pytest.skip("Japan bundle copy and catalog are required")
    bundle_name = f"assets-_mx-spinecharacters-hoshino_spr-_mxdependency-assets-2025-07-02_assets_all_{HASH}.bundle"
    original = next(JP_BUNDLES.rglob(bundle_name), None)
    if original is None:
        pytest.skip("Japan Hoshino bundle is missing")
    catalog = json.loads(JP_CATALOG.read_text(encoding="utf-8"))
    records = [
        file
        for key in ("FullPatchPacks", "UpdatePacks")
        for pack in catalog[key]
        for file in pack["BundleFiles"]
        if file["Name"] == bundle_name
    ]
    assert records
    assert {file["Size"] for file in records} == {original.stat().st_size}
    (expected,) = {file["Crc"] for file in records}

    staged = tmp_path / original.name
    shutil.copyfile(original, staged)
    assert zlib.crc32(staged.read_bytes()) == expected
    assert Bundle.get_trailing_bytes(staged) == 0
    bundle = Bundle.load(staged)
    assert bundle is not None
    source = tmp_path / "source.bundle"
    ok, message = bundle.save(source, SaveOptions(perform_crc=False, compression="lz4"))
    assert ok, message
    assert not matches_target_crc(source, staged)
    planned = PlannedFile(source, parse_filename(source.name), Action.DIRECT_REPLACE, staged, need_crc_fix=True)
    group = PlannedGroup("", "spinecharacters", "hoshino_spr", [planned])
    assert not install_staged([group], tmp_path / "output", set(), SaveOptions(compression="lz4"))
    assert zlib.crc32(staged.read_bytes()) == expected
    assert Bundle.get_trailing_bytes(staged) == 4
    assert Bundle.load(staged) is not None
