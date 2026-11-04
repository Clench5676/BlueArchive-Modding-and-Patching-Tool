"""Revealing a path shows it in the file manager and never starts what it points at."""

import subprocess

import pytest

from bmpt.webui import system


@pytest.fixture
def commands(monkeypatch):
    calls = []
    monkeypatch.setattr(system.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append(command))
    return calls


@pytest.mark.parametrize("name", ["Tool.app", "Setup.PKG", "Helper.workflow", "Viewer.bundle"])
def test_mac_packages_are_selected_not_opened(tmp_path, commands, name):
    (tmp_path / name).mkdir()
    system.reveal(tmp_path / name)
    assert commands == [["open", "-R", str((tmp_path / name).resolve())]]


def test_mac_files_are_selected_and_plain_folders_opened(tmp_path, commands):
    (tmp_path / "out.v2").mkdir()
    (tmp_path / "a.png").write_bytes(b"")
    system.reveal(tmp_path / "a.png")
    system.reveal(tmp_path / "out.v2")
    assert commands == [["open", "-R", str((tmp_path / "a.png").resolve())], ["open", str((tmp_path / "out.v2").resolve())]]
