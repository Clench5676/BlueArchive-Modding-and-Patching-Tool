"""Bundle readers keep their files open until a garbage collection: removal and replacement release them first."""

from __future__ import annotations

import gc
import logging
import os
import shutil
import sys
import weakref
from pathlib import Path

import pytest

from bmpt import filelock, openfiles
from bmpt.library import staging

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


@pytest.fixture
def no_automatic_collection():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


class Cycle:
    def __init__(self, path: Path):
        self.stream = open(path, "rb")
        self.me = self


def test_release_closes_a_file_that_only_a_reference_cycle_holds(tmp_path, no_automatic_collection):
    path = tmp_path / "held.bundle"
    path.write_bytes(b"data")
    holder = Cycle(path)
    stream = weakref.ref(holder.stream)
    del holder
    assert stream() is not None and not stream().closed  # still open: nothing collected the cycle
    openfiles.release()
    assert stream() is None


def test_remove_tree_releases_and_retries_while_a_file_is_in_use(tmp_path, monkeypatch):
    folder = tmp_path / "work"
    folder.mkdir()
    calls, released = [], []
    real = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        real(path, *args, **kwargs)

    monkeypatch.setattr(openfiles.shutil, "rmtree", rmtree)
    monkeypatch.setattr(openfiles, "release", lambda: released.append(True))
    openfiles.remove_tree(folder, tries=3, wait=0)
    assert len(calls) == 2 and released == [True] and not folder.exists()


def test_remove_tree_gives_up_after_its_tries_and_ignores_a_missing_folder(tmp_path, monkeypatch):
    def refuse(path, *args, **kwargs):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(openfiles.shutil, "rmtree", refuse)
    monkeypatch.setattr(openfiles, "release", lambda: None)
    with pytest.raises(PermissionError):
        openfiles.remove_tree(tmp_path, tries=2, wait=0)
    monkeypatch.undo()
    openfiles.remove_tree(tmp_path / "missing")


def test_temporary_directory_logs_a_folder_it_cannot_remove_and_keeps_errors_from_its_block(tmp_path, monkeypatch,
                                                                                            caplog):
    def refuse(path, **kwargs):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(openfiles, "remove_tree", refuse)
    with caplog.at_level(logging.WARNING, logger="bmpt.openfiles"):
        with openfiles.temporary_directory(prefix="work-", dir=tmp_path) as folder:
            assert Path(folder).parent == tmp_path and Path(folder).name.startswith("work-")
    assert "Could not remove a temporary folder" in caplog.text
    with pytest.raises(ValueError, match="inside"):
        with openfiles.temporary_directory(dir=tmp_path):
            raise ValueError("inside")
    monkeypatch.undo()
    with openfiles.temporary_directory(dir=tmp_path) as folder:
        (Path(folder) / "file.bundle").write_bytes(b"x")
    assert not Path(folder).exists()


def test_import_workspace_survives_a_file_still_in_use(tmp_path, monkeypatch):
    def refuse(path, **kwargs):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(openfiles, "remove_tree", refuse)
    with staging.import_directory(tmp_path) as folder:
        (folder / "staged.bundle").write_bytes(b"x")
    # The folder stays for the startup cleanup; its lease is gone, so that cleanup may take it.
    assert folder.exists() and not (tmp_path / "staging" / (folder.name + ".lock")).exists()


def test_replace_releases_this_process_before_trying_again(tmp_path, monkeypatch):
    source, target = tmp_path / "new.bundle", tmp_path / "game.bundle"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    real, calls, released = os.replace, [], []

    def replace(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError(32, "in use")
        real(src, dst)

    monkeypatch.setattr(filelock.os, "replace", replace)
    monkeypatch.setattr(openfiles, "release", lambda: released.append(True))
    filelock.replace(source, target, tries=3, wait=0)
    assert released == [True] and target.read_bytes() == b"new"


def _holders(path: Path) -> int:
    """How many of this process's file descriptors name ``path``."""
    wanted, count = os.path.realpath(path), 0
    if sys.platform == "darwin":
        import fcntl
        for fd in map(int, os.listdir("/dev/fd")):
            try:
                name = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0].decode()
            except OSError:
                continue
            count += os.path.realpath(name) == wanted
    else:
        for fd in os.listdir("/proc/self/fd"):
            try:
                count += os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) == wanted
            except OSError:
                continue
    return count


@pytest.mark.skipif(not GAME_DIR.is_dir() or sys.platform not in ("darwin", "linux"),
                    reason="needs an official game folder and a way to list open files")
def test_a_loaded_bundle_stays_open_until_released(tmp_path, no_automatic_collection):
    from ba_modding_toolkit.bundle import Bundle
    from ba_modding_toolkit.searching import get_search_dirs

    bundles = sorted((path for folder in get_search_dirs(GAME_DIR) for path in folder.glob("*.bundle")),
                     key=lambda path: path.stat().st_size)
    source = next(path for path in bundles if path.stat().st_size > 4096)
    copy = tmp_path / source.name
    shutil.copyfile(source, copy)
    bundle = Bundle.load(copy)
    assert bundle is not None and _holders(copy) >= 1
    del bundle
    assert _holders(copy) >= 1  # the reader's cycles keep the file open
    openfiles.release()
    assert _holders(copy) == 0
