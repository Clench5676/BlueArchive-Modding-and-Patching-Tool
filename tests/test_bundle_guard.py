"""Bundle sizes are bounded before UnityPy loads a bundle (synthetic files; the official sweep needs BAMT_TEST_GAME_DIR)."""

import os
import struct
from pathlib import Path

import pytest
from UnityPy.helpers.CompressionHelper import compress_lz4

from ba_modding_toolkit import bundle as bundle_module, bundle_guard, unityfs
from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.bundle_guard import check_bundle
from ba_modding_toolkit.errors import ServiceError

from test_atlas_remap import write_bundle

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


def unity_fs(path, blocks=((100, 50, 2),), *, count=None, directory_size=None, nodes=("CAB-test",), padding=0):
    """A UnityFS file with a compressed block table; the data blocks it declares are not written."""
    table = bytes(16) + struct.pack(">i", len(blocks) if count is None else count)
    table += b"".join(struct.pack(">IIH", *block) for block in blocks)
    table += struct.pack(">i", len(nodes))
    table += b"".join(struct.pack(">qqI", 0, 0, 4) + name.encode() + b"\0" for name in nodes)
    packed = compress_lz4(table)
    prefix = b"UnityFS\0" + struct.pack(">I", 7) + b"5.x.x\0" + b"2022.3.55f1\0"
    head_size = (len(prefix) + 20 + 15) & ~15
    length = head_size + len(packed) + padding
    head = prefix + struct.pack(">qIII", length, len(packed), len(table) if directory_size is None else directory_size, 2)
    path.write_bytes(head.ljust(head_size, b"\0") + packed + bytes(padding))
    return path


def test_layout_reports_the_declared_sizes(tmp_path):
    blocks = [(131072, 900, 2), (7, 7, 0), (4096, 20, 2)]
    path = unity_fs(tmp_path / "ok.bundle", blocks, padding=33)
    layout = unityfs.read_layout(path)
    assert (layout.blocks, layout.largest_block, layout.total) == (3, 131072, 131072 + 7 + 4096)
    assert layout.size == path.stat().st_size and layout.directory > 0
    assert check_bundle(path) == layout
    assert unityfs.read_cab_names(path) == ["CAB-test"]


def test_a_bundle_without_blocks_has_an_empty_layout(tmp_path):
    layout = check_bundle(unity_fs(tmp_path / "empty.bundle", ()))
    assert (layout.blocks, layout.largest_block, layout.total) == (0, 0, 0)


def test_ceilings_are_far_above_ordinary_bundles():
    assert 0 < bundle_guard.MAX_BLOCK_BYTES <= bundle_guard.MAX_TOTAL_BYTES
    assert bundle_guard.MAX_BUNDLE_BYTES >= 1 << 30


@pytest.mark.parametrize("blocks", [
    [(bundle_guard.MAX_BLOCK_BYTES + 1, 10, 2)],
    [(1, 1, 0), (bundle_guard.MAX_BLOCK_BYTES + 1, 10, 2)],
    [(bundle_guard.MAX_BLOCK_BYTES, 10, 2)] * (bundle_guard.MAX_TOTAL_BYTES // bundle_guard.MAX_BLOCK_BYTES + 1),
], ids=["one block", "second block", "total"])
def test_oversized_declarations_are_refused_before_loading(tmp_path, monkeypatch, blocks):
    path = unity_fs(tmp_path / "crafted.bundle", blocks)
    assert path.stat().st_size < 4096
    with pytest.raises(ServiceError) as refused:
        check_bundle(path)
    assert (refused.value.code, refused.value.params) == ("bundle_invalid", {"file": "crafted.bundle"})
    with pytest.raises(ServiceError) as renamed:
        check_bundle(path, file="shown.bundle")
    assert renamed.value.params == {"file": "shown.bundle"}
    # Bundle.load treats the refusal as an unreadable bundle and loads nothing
    loaded, logged = [], []
    monkeypatch.setattr(bundle_module.UnityPy, "load", lambda *args: loaded.append(args))
    monkeypatch.setattr(Path, "read_bytes", lambda self: loaded.append(self))
    assert Bundle.load(path, logged.append) is None
    assert loaded == [] and len(logged) == 1 and "crafted.bundle" in logged[0]


def test_values_at_the_ceilings_pass(tmp_path):
    # One block at the single-block ceiling, and blocks adding up to exactly the total ceiling
    assert check_bundle(unity_fs(tmp_path / "block.bundle", [(bundle_guard.MAX_BLOCK_BYTES, 10, 2)]))
    count = bundle_guard.MAX_TOTAL_BYTES // bundle_guard.MAX_BLOCK_BYTES
    assert check_bundle(unity_fs(tmp_path / "total.bundle", [(bundle_guard.MAX_BLOCK_BYTES, 10, 2)] * count))


def test_oversized_files_are_refused(tmp_path, monkeypatch):
    path = unity_fs(tmp_path / "large.bundle", padding=1000)
    monkeypatch.setattr(bundle_guard, "MAX_BUNDLE_BYTES", path.stat().st_size)
    assert check_bundle(path)
    monkeypatch.setattr(bundle_guard, "MAX_BUNDLE_BYTES", path.stat().st_size - 1)
    with pytest.raises(ServiceError) as refused:
        check_bundle(path)
    assert refused.value.code == "bundle_invalid"
    assert Bundle.load(path) is None


def test_the_block_table_is_bounded_before_it_is_read(tmp_path, monkeypatch):
    called = []

    def decompress(*args):
        called.append(args)
        raise AssertionError("the block table must not be decompressed")

    monkeypatch.setattr(unityfs, "DECOMPRESSION_MAP", {n: decompress for n in range(5)})
    # A declared uncompressed block table above the ceiling is refused before any decompression
    big = unity_fs(tmp_path / "big.bundle", directory_size=bundle_guard.MAX_DIRECTORY_BYTES + 1)
    with pytest.raises(ServiceError):
        check_bundle(big)
    assert called == []


@pytest.mark.parametrize("count", [1 << 30, -1])
def test_a_block_count_the_table_cannot_hold_is_refused(tmp_path, count):
    with pytest.raises(ServiceError):
        check_bundle(unity_fs(tmp_path / "count.bundle", count=count))


def test_reading_a_block_table_over_the_ceiling_never_allocates_it(tmp_path):
    # read_layout takes the ceiling as a parameter, so the bound is the one the caller sets
    path = unity_fs(tmp_path / "table.bundle", [(10, 10, 0)] * 100)
    assert unityfs.read_layout(path, 1 << 20).blocks == 100
    with pytest.raises(ValueError):
        unityfs.read_layout(path, 100)


def test_corrupt_unityfs_files_are_refused(tmp_path):
    good = unity_fs(tmp_path / "good.bundle", padding=40)
    data = good.read_bytes()
    for name, content in {"short": data[:30], "no directory": data[:48], "cut": data[:len(data) - 60]}.items():
        path = tmp_path / f"{name}.bundle"
        path.write_bytes(content)
        with pytest.raises(ServiceError):
            check_bundle(path)
        assert Bundle.load(path) is None


@pytest.mark.parametrize("content", [b"", b"plain text", b"UnityWeb\0" + bytes(64), b"UnityRaw\0" + bytes(64)])
def test_files_that_are_not_unityfs_go_on_as_before(tmp_path, content):
    path = tmp_path / "other.bundle"
    path.write_bytes(content)
    assert check_bundle(path) is None
    assert unityfs.read_layout(path) is None
    assert Bundle.load(path) is None  # unreadable as before, not through the size bounds
    with pytest.raises(FileNotFoundError):
        check_bundle(tmp_path / "missing.bundle")


def test_valid_bundles_still_load(tmp_path):
    path = write_bundle(tmp_path / "valid.bundle", {"test.atlas": "page"}, {})
    layout = check_bundle(path)
    assert layout and layout.blocks == 1 and layout.total == layout.largest_block
    bundle = Bundle.load(path)
    assert bundle is not None and bundle.env.objects
    # trailing bytes after the declared bundle, as a CRC correction appends them
    path.write_bytes(path.read_bytes() + b"\x01\x02\x03\x04")
    assert Bundle.load(path) is not None


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")
def test_every_official_bundle_passes_with_room_to_spare():
    largest = dict.fromkeys(("size", "directory", "blocks", "largest_block", "total"), 0)
    bundles = 0
    for folder, _, names in os.walk(GAME_DIR):
        for name in names:
            path = Path(folder) / name
            layout = check_bundle(path)
            if layout is None:
                continue
            bundles += 1
            for key in largest:
                largest[key] = max(largest[key], getattr(layout, key))
    assert bundles > 1000
    assert largest["largest_block"] > 0
    # Each ceiling is at least four times the largest official value
    assert bundle_guard.MAX_BUNDLE_BYTES >= 4 * largest["size"]
    assert bundle_guard.MAX_BLOCK_BYTES >= 4 * largest["largest_block"]
    assert bundle_guard.MAX_TOTAL_BYTES >= 4 * largest["total"]
    assert bundle_guard.MAX_DIRECTORY_BYTES >= 4 * largest["directory"]
