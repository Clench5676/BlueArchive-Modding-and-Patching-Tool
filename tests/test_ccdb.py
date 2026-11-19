"""Check catalog serialization, redirect guards, and CRC preservation."""

import os
import struct
import zlib
from dataclasses import replace
from pathlib import Path
from random import Random
from time import perf_counter

import pytest
from catalog_fixture import LOAD, PRELOAD, bundle_id, synthetic_catalog

from bmpt.library.catalog_switch import MODS_DIR
from bmpt.unity import ccdb
from bmpt.unity.ccdb import CatalogFormatError, bundle_ids, crc_suffix, parse, redirect, write

PLACED_A = "a" * 40 + ".bundle"
PLACED_B = "b" * 40 + ".bundle"


@pytest.fixture
def catalog():
    return synthetic_catalog()


def test_roundtrip(catalog):
    raw = write(catalog)
    assert parse(raw) == catalog
    assert parse(memoryview(raw)) == catalog
    interleaved = bytearray(2 * len(raw))
    interleaved[::2] = raw
    assert parse(memoryview(interleaved)[::2]) == catalog
    assert write(parse(raw)) == raw


def test_bundle_ids_reports_both_prefixes(catalog):
    assert bundle_ids(catalog) == {
        "a.bundle": (0, LOAD),
        "preload.bundle": (2, PRELOAD),
        "b.bundle": (3, LOAD),
    }


@pytest.mark.parametrize("redirects", [{"a.bundle": PLACED_A}, {"a.bundle": PLACED_A, "b.bundle": PLACED_B}])
def test_redirect_changes_only_requested_ids_and_keeps_crc(catalog, redirects):
    official = write(catalog)
    changed = redirect(official, redirects, MODS_DIR)
    expected_ids = catalog.ids.copy()
    for name, placed in redirects.items():
        expected_ids[catalog.ids.index(bundle_id(LOAD, name))] = bundle_id(LOAD, f"{MODS_DIR}\\{placed}")
    assert parse(changed[:-4]) == replace(catalog, ids=expected_ids)
    assert zlib.crc32(changed) == zlib.crc32(official)


@pytest.mark.parametrize("body_crc, target", [(0, 0), (0, 0xFFFFFFFF), (0xFFFFFFFF, 0), (0x12345678, 0x9ABCDEF0)])
def test_crc_suffix(body_crc, target):
    suffix = crc_suffix(body_crc, target)
    assert len(suffix) == 4
    assert zlib.crc32(suffix, body_crc) == target


@pytest.mark.parametrize("header", [b"XDBB\x01", b"CCDB\x02"])
def test_redirect_refuses_unsupported_catalog_header(catalog, header):
    with pytest.raises(CatalogFormatError) as refused:
        redirect(header + write(catalog)[5:], {}, MODS_DIR)
    assert refused.value.reason == "version"


def test_parse_checks_every_truncation(catalog):
    raw = write(catalog)
    for end in range(len(raw)):
        with pytest.raises(CatalogFormatError) as refused:
            parse(raw[:end])
        assert refused.value.reason == ("version" if end < 5 else "roundtrip")


@pytest.mark.parametrize(
    "body",
    [
        b"\x80" * 5,
        b"\xff\xff\xff\xff\x07",
        b"\x80\x80\x80\x80\x08",
        b"\0" * 3 + struct.pack("<i", -1),
        b"\0" * 3 + struct.pack("<i", 2**31 - 1),
        b"\0" * 15 + struct.pack("<i", -1),
        b"\0" * 15 + struct.pack("<i", 2**31 - 1),
    ],
)
def test_parse_refuses_out_of_bounds_counts_and_lengths(body):
    with pytest.raises(CatalogFormatError) as refused:
        parse(b"CCDB\x01" + body)
    assert refused.value.reason == "roundtrip"


def test_redirect_requires_exact_official_roundtrip(catalog):
    raw = write(catalog)
    # A redundant length byte preserves the decoded locator but changes its encoding.
    noncanonical = raw[:5] + bytes((raw[5] | 0x80, 0)) + raw[6:]
    assert parse(noncanonical) == catalog
    with pytest.raises(CatalogFormatError) as refused:
        redirect(noncanonical, {}, MODS_DIR)
    assert refused.value.reason == "roundtrip"


def test_redirect_refuses_trailing_bytes(catalog):
    with pytest.raises(CatalogFormatError) as refused:
        redirect(write(catalog) + b"\0", {}, MODS_DIR)
    assert refused.value.reason == "trailing"


@pytest.mark.parametrize("prefix", [LOAD, PRELOAD])
def test_redirect_refuses_duplicate_official_bundle_name(catalog, prefix):
    catalog.ids.append(bundle_id(prefix, "a.bundle"))
    with pytest.raises(CatalogFormatError) as refused:
        redirect(write(catalog), {"a.bundle": PLACED_A}, MODS_DIR)
    assert refused.value.reason == "ambiguous"


@pytest.mark.parametrize(
    "redirects, reason",
    [
        ({"unknown.bundle": PLACED_A}, "not_found"),
        ({"preload.bundle": PLACED_A}, "preload"),
        ({"a.bundle": "A" * 40 + ".bundle"}, "name"),
        ({"a.bundle": PLACED_A + "\n"}, "name"),
        ({"a.bundle": "../" + PLACED_A}, "name"),
        ({"a.bundle": PLACED_A, "b.bundle": PLACED_A}, "ambiguous"),
    ],
)
def test_redirect_refuses_invalid_mappings(catalog, redirects, reason):
    with pytest.raises(CatalogFormatError) as refused:
        redirect(write(catalog), redirects, MODS_DIR)
    assert refused.value.reason == reason


def test_redirect_refuses_a_path_as_subfolder(catalog):
    with pytest.raises(CatalogFormatError) as refused:
        redirect(write(catalog), {"a.bundle": PLACED_A}, "../elsewhere")
    assert refused.value.reason == "name"


def test_redirect_checks_result_crc(catalog, monkeypatch):
    def wrong_suffix(body_crc, target_crc):
        correct = crc_suffix(body_crc, target_crc)
        return bytes([correct[0] ^ 1]) + correct[1:]

    monkeypatch.setattr(ccdb, "crc_suffix", wrong_suffix)
    with pytest.raises(CatalogFormatError) as refused:
        redirect(write(catalog), {"a.bundle": PLACED_A}, MODS_DIR)
    assert refused.value.reason == "crc"


@pytest.mark.parametrize("truncate", [False, True])
def test_redirect_checks_reparsed_fields(catalog, monkeypatch, truncate):
    official = write(catalog)

    def corrupt_write(fields):
        if fields == catalog:
            return write(fields)
        if truncate:
            return write(fields)[:-1]
        return write(replace(fields, keys=b"changed"))

    monkeypatch.setattr(ccdb, "write", corrupt_write)
    with pytest.raises(CatalogFormatError) as refused:
        redirect(official, {"a.bundle": PLACED_A}, MODS_DIR)
    assert refused.value.reason == "reparse"


def test_official_catalog_roundtrip_and_redirect():
    game_dir = os.environ.get("BAMT_TEST_GAME_DIR")
    if not game_dir:
        pytest.skip("Set BAMT_TEST_GAME_DIR to a verified official view")
    path = Path(game_dir) / "BlueArchive_Data/StreamingAssets/PUB/Resource/Catalog/Windows/catalog_Windows.bytes"
    if not path.is_file():
        pytest.skip("The game folder holds no catalog file")
    official = path.read_bytes()

    start = perf_counter()
    catalog = parse(official)
    assert write(catalog) == official
    names = [name for name, (_, prefix) in bundle_ids(catalog).items() if prefix == LOAD]
    sample = Random(0).sample(names, 100)
    redirects = {name: f"{index:040x}.bundle" for index, name in enumerate(sample)}
    changed = redirect(official, redirects, MODS_DIR)
    assert zlib.crc32(changed) == zlib.crc32(official)
    assert perf_counter() - start < 2
