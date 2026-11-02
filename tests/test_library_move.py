"""Moving the mod library to another folder (FUN-17): copy, verify, publish, then clear the source. A failed move
removes its copies and can be retried at once; a failure clearing the source loses nothing."""

import errno
import json
import logging
import os
from pathlib import Path

import pytest

from ba_modding_toolkit.errors import ServiceError
from ba_modding_toolkit.library import relocate as relocate_module
from ba_modding_toolkit.webui.services import LIBRARY_ITEMS
from test_webui import services  # noqa: F401 - fixture

DIRECTORIES = ("mods", "originals", "built", "transactions", "import-transactions", "asset-deletions", "photographs")
FILES = ("state.json", "state.json.prev", "state.json.bad", "profiles.json", "profiles.json.prev", "profiles.json.bad")


def make_library(root: Path) -> None:
    """A library with every kind of item, nested folders and a few files of different sizes."""
    root.mkdir(parents=True)
    for number, name in enumerate(DIRECTORIES):
        folder = root / name
        (folder / "a" / "b").mkdir(parents=True)
        (folder / "top.bin").write_bytes(bytes([number]) * 3000)
        (folder / "a" / "b" / "deep.bin").write_bytes(os.urandom(5000 + number))
    for name in ("transactions", "import-transactions", "asset-deletions"):
        (root / name / "0123456789abcdef" / "").mkdir()
        (root / name / "0123456789abcdef" / "journal.json").write_text(json.dumps({"version": 1, "phase": "active"}))
    (root / "photographs" / "index.json").write_text(json.dumps({"version": 1, "photos": {}, "selected": {}, "next": 1}))
    for name in FILES:
        (root / name).write_text(json.dumps({"version": 1, "name": name}))
    # modification times the library's records compare
    for index, path in enumerate(sorted(p for p in root.rglob("*") if p.is_file())):
        os.utime(path, ns=(1_700_000_000_000_000_000 + index, 1_700_000_000_000_000_000 + index * 1_000_000_007))


def tree(root: Path) -> dict:
    """Everything below a folder: kind, size, bytes and modification time of files."""
    out = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            out[relative] = ("dir",)
        else:
            out[relative] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    return out


@pytest.fixture
def library(services, tmp_path):
    source = tmp_path / "old-library"
    make_library(source)
    services.state.set("library_dir", str(source))
    return source


def test_the_list_holds_the_deletion_journals():
    assert "asset-deletions" in LIBRARY_ITEMS
    assert set(DIRECTORIES) | set(FILES) == set(LIBRARY_ITEMS)
    assert LIBRARY_ITEMS.index("photographs") < LIBRARY_ITEMS.index("state.json"), "folders before state files"


def test_a_move_copies_every_item_and_clears_the_source(services, library, tmp_path):
    before = tree(library)
    target = tmp_path / "volume" / "new-library"
    assert services.move_library(target) == target.resolve()
    assert tree(target) == before, "bytes, sizes and modification times are the same"
    assert [p.name for p in sorted(library.iterdir())] == [], "the old folder keeps nothing of the library"
    assert services.library_dir() == target.resolve()
    assert (target / "asset-deletions" / "0123456789abcdef" / "journal.json").is_file()
    assert not [p for p in target.iterdir() if p.name.startswith(".")], "no staging folder is left"


def test_a_library_that_is_already_in_the_target_is_only_switched_to(services, library, tmp_path):
    target = tmp_path / "other"
    make_library(target)
    before, other = tree(library), tree(target)
    services.move_library(target)
    assert tree(library) == before and tree(target) == other and services.library_dir() == target.resolve()


# --- a failed move ---

def fail_on(monkeypatch, match, error=None):
    """Make the copy of the files that match fail, after the earlier ones were copied."""
    real = relocate_module._copy_file

    def copy(source, target):
        if match(source):
            raise error or OSError(errno.ENOSPC, "No space left on device")
        real(source, target)

    monkeypatch.setattr(relocate_module, "_copy_file", copy)


@pytest.mark.parametrize("existing_target", [False, True])
def test_running_out_of_space_in_mods_removes_the_copies_and_the_retry_works(
        services, library, tmp_path, monkeypatch, existing_target):
    """The review's reproduction: the first move fails during `mods`, the retry used to find a partial target/mods."""
    before = tree(library)
    target = tmp_path / "volume" / "new-library"
    if existing_target:
        target.mkdir(parents=True)
    with monkeypatch.context() as patch:
        fail_on(patch, lambda source: "mods" in source.parts and source.name == "deep.bin")
        with pytest.raises(ServiceError) as caught:
            services.move_library(target)
    assert caught.value.code == "library_io_error" and caught.value.params == {"file": str(target.resolve())}
    assert tree(library) == before, "the source is intact"
    assert services.library_dir() == library
    if existing_target:
        assert list(target.iterdir()) == [], "nothing is left in the target"
    else:
        assert not (tmp_path / "volume").exists(), "the folders the move made are gone too"
    # the space is back: the same call goes through at once, with no "folder not empty" in the way
    assert services.move_library(target) == target.resolve()
    assert tree(target) == before and not any(library.iterdir())


def test_a_copy_that_differs_from_the_original_is_refused_and_removed(services, library, tmp_path, monkeypatch):
    before = tree(library)
    real = relocate_module._copy_file

    def truncating(source, target):
        real(source, target)
        if source.name == "state.json.prev":
            target.write_bytes(target.read_bytes()[:-3])

    monkeypatch.setattr(relocate_module, "_copy_file", truncating)
    target = tmp_path / "new-library"
    with pytest.raises(ServiceError) as caught:
        services.move_library(target)
    assert caught.value.code == "library_io_error"
    assert tree(library) == before and not target.exists()


def test_a_failure_while_publishing_takes_back_what_was_published(services, library, tmp_path, monkeypatch):
    before = tree(library)
    real = relocate_module.replace
    target = tmp_path / "new-library"

    def replace(source, destination, *args):
        if Path(destination) == target.resolve() / "photographs":
            raise PermissionError(errno.EACCES, "denied", str(destination))
        real(source, destination, *args)

    monkeypatch.setattr(relocate_module, "replace", replace)
    with pytest.raises(ServiceError) as caught:
        services.move_library(target)
    assert caught.value.code == "library_io_error"
    assert tree(library) == before and not target.exists()
    monkeypatch.setattr(relocate_module, "replace", real)
    services.move_library(target)
    assert tree(target) == before


def test_a_failure_switching_the_library_takes_the_copies_back(services, library, tmp_path, monkeypatch):
    before = tree(library)
    real_set = services.state.set

    def refuse(key, value):
        if key == "library_dir":
            raise PermissionError(errno.EACCES, "denied", str(services.state.path))
        real_set(key, value)

    monkeypatch.setattr(services.state, "set", refuse)
    target = tmp_path / "new-library"
    with pytest.raises(PermissionError):
        services.move_library(target)
    assert tree(library) == before and not target.exists() and services.library_dir() == library
    monkeypatch.setattr(services.state, "set", real_set)
    services.move_library(target)
    assert tree(target) == before


def test_a_target_inside_an_item_is_refused_before_anything_is_copied(services, library, tmp_path):
    before = tree(library)
    target = library / "mods" / "inside"
    with pytest.raises(ServiceError) as caught:
        services.move_library(target)
    assert caught.value.code == "library_io_error"
    assert tree(library) == before


def test_a_staging_folder_left_by_a_cut_short_move_is_cleared_by_the_next(services, library, tmp_path):
    target = tmp_path / "new-library"
    stale = target / f"{relocate_module.STAGING}deadbeef" / "mods"
    stale.mkdir(parents=True)
    (stale / "half.bin").write_bytes(b"x" * 100)
    before = tree(library)
    services.move_library(target)
    assert tree(target) == before


@pytest.mark.skipif(os.name == "nt", reason="links need a privilege on Windows")
def test_links_are_moved_as_links(services, library, tmp_path):
    big = tmp_path / "big-disk" / "originals"
    (library / "originals").rename(tmp_path / "gone")
    big.mkdir(parents=True)
    (big / "kept.bin").write_bytes(b"kept")
    (library / "originals").symlink_to(big, target_is_directory=True)
    target = tmp_path / "new-library"
    services.move_library(target)
    assert (target / "originals").is_symlink() and os.readlink(target / "originals") == str(big)
    assert (big / "kept.bin").read_bytes() == b"kept", "what the link points to is never deleted"
    assert not os.path.lexists(library / "originals")


# --- a failure clearing the source ---

def test_a_source_item_that_cannot_be_removed_stays_whole_and_the_target_is_complete(
        services, library, tmp_path, monkeypatch, caplog):
    """The library is complete at the target, which is in use; the old folder keeps the item it could not remove."""
    before = tree(library)
    real = relocate_module.replace
    locked = library / "mods"

    def replace(source, destination, *args):
        if Path(source) == locked:
            raise PermissionError(errno.EACCES, "in use", str(source))
        real(source, destination, *args)

    monkeypatch.setattr(relocate_module, "replace", replace)
    target = tmp_path / "new-library"
    with caplog.at_level(logging.WARNING):
        assert services.move_library(target) == target.resolve()
    assert tree(target) == before, "the library is complete at the target"
    assert services.library_dir() == target.resolve(), "and in use there"
    assert tree(library) == {name: value for name, value in before.items() if name.split("/")[0] == "mods"}, \
        "the old folder keeps the item whole and nothing half-deleted"
    assert "still holds mods" in caplog.text


def test_files_that_cannot_be_deleted_after_the_rename_leave_hidden_leftovers_only(
        services, library, tmp_path, monkeypatch):
    before = tree(library)
    real = relocate_module.remove

    def remove(path):
        if relocate_module.ASIDE in path.name:
            raise PermissionError(errno.EACCES, "in use", str(path))
        real(path)

    monkeypatch.setattr(relocate_module, "remove", remove)
    target = tmp_path / "new-library"
    services.move_library(target)
    assert tree(target) == before and services.library_dir() == target.resolve()
    leftovers = sorted(p.name for p in library.iterdir())
    assert leftovers and all(name.startswith(".") and relocate_module.ASIDE in name for name in leftovers)
    assert not any((library / name).exists() for name in LIBRARY_ITEMS), "nothing looks like a library there"
