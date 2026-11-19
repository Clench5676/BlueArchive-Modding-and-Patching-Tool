"""Check the catalog switch layout against synthetic pinned installs."""

import pytest

from bmpt.library.catalog_switch import MODE_CATALOG, MODE_FILES, MODS_DIR, catalog_paths, mode_of, placed_name
from bmpt.library.service.folders import FoldersMixin

STREAMING = "BlueArchive_Data/StreamingAssets"
RESOURCE = f"{STREAMING}/PUB/Resource"
CATALOG = f"{RESOURCE}/Catalog/Windows/catalog_Windows.bytes"
BUNDLES = f"{RESOURCE}/GameData/Windows"


def _pin(root, server="global"):
    pin = FoldersMixin()._pin(root, server)
    assert pin is not None
    return pin


@pytest.fixture
def global_install(tmp_path):
    root = tmp_path / "Global"
    live = root / CATALOG
    live.parent.mkdir(parents=True)
    live.write_bytes(b"catalog")
    (root / BUNDLES).mkdir(parents=True)
    return root


def test_global_pub_layout_uses_catalog_mode(global_install):
    pin = _pin(global_install)
    assert mode_of(pin) == (MODE_CATALOG, None)
    paths = catalog_paths(pin)
    assert paths is not None
    assert paths.live == pin.root / CATALOG
    assert paths.hash == (pin.root / CATALOG).with_suffix(".hash")
    assert paths.mods_dir == pin.root / BUNDLES / MODS_DIR
    assert all(pin.root in path.resolve().parents for path in (paths.live, paths.hash, paths.mods_dir))
    assert not paths.mods_dir.exists()


def test_japan_asset_bundles_layout_uses_files_mode(tmp_path):
    root = tmp_path / "Japan"
    (root / STREAMING / "AssetBundles").mkdir(parents=True)
    pin = _pin(root, "japan")
    assert mode_of(pin) == (MODE_FILES, "unsupported_server")
    assert catalog_paths(pin) is None


def test_loose_folder_uses_files_mode(tmp_path):
    (tmp_path / "loose.bundle").touch()
    pin = _pin(tmp_path)
    assert pin.dirs == (pin.root,)
    assert mode_of(pin) == (MODE_FILES, "missing_catalog")
    assert catalog_paths(pin) is None


def test_global_requires_a_catalog_file(global_install):
    live = global_install / CATALOG
    live.unlink()
    live.mkdir()
    pin = _pin(global_install)
    assert mode_of(pin) == (MODE_FILES, "missing_catalog")
    assert catalog_paths(pin) is None


def test_global_requires_a_bundle_directory(global_install):
    pin = _pin(global_install)
    (global_install / BUNDLES).rmdir()
    assert mode_of(pin) == (MODE_FILES, "missing_bundle_folder")
    assert catalog_paths(pin) is None
    (global_install / BUNDLES).mkdir()
    assert mode_of(pin) == (MODE_CATALOG, None)


def test_global_bundle_directory_must_be_in_the_pin(global_install):
    preload = global_install / RESOURCE / "Preload/Windows"
    preload.mkdir(parents=True)
    pin = _pin(preload)
    assert pin.root == global_install.resolve()
    assert mode_of(pin) == (MODE_FILES, "missing_bundle_folder")
    assert catalog_paths(pin) is None


@pytest.mark.parametrize("suffix", [CATALOG, CATALOG.replace(".bytes", ".hash"), f"{BUNDLES}/{MODS_DIR}"])
def test_catalog_paths_refuse_symlinks_outside_the_root(global_install, tmp_path, suffix):
    outside = tmp_path / "outside"
    link = global_install / suffix
    if link.is_file():
        link.unlink()
    directory = link.name == MODS_DIR
    if directory:
        outside.mkdir()
    else:
        outside.touch()
    link.symlink_to(outside, target_is_directory=directory)
    pin = _pin(global_install)
    assert mode_of(pin) == (MODE_FILES, "outside_root")
    assert catalog_paths(pin) is None


def test_placed_name_uses_the_digest():
    sha1 = "0123456789abcdef0123456789abcdef01234567"
    assert placed_name(sha1) == f"{sha1}.bundle"


@pytest.mark.parametrize("sha1", ["", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "a" * 40 + "\n", "../" + "a" * 37, None])
def test_placed_name_rejects_invalid_digests(sha1):
    with pytest.raises(ValueError):
        placed_name(sha1)
