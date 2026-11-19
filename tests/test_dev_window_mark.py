"""The dev mode mark on the native window title and the macOS Dock (bmpt.webui.native)."""

import sys
import types

import pytest

from bmpt.engine import t
from bmpt.webui import native


@pytest.fixture(autouse=True)
def no_dev_mode(monkeypatch):
    """Each test starts outside dev mode and turns it on itself."""
    monkeypatch.delenv("BAMT_DEV_ROOTS", raising=False)
    monkeypatch.delenv("BAMT_DEV_WRITABLE", raising=False)


def test_window_title_is_the_app_title_outside_dev_mode():
    assert native.window_title() == t("ui.app_title")


@pytest.mark.parametrize("variable", ["BAMT_DEV_ROOTS", "BAMT_DEV_WRITABLE"])
def test_window_title_ends_with_dev_in_dev_mode(monkeypatch, variable):
    monkeypatch.setenv(variable, "")
    assert native.window_title() == t("ui.app_title") + " (DEV)"


@pytest.fixture
def fake_appkit(monkeypatch):
    """Stand-ins for AppKit and AppHelper that record what the Dock mark would call."""
    calls = []
    tile = types.SimpleNamespace(setBadgeLabel_=lambda label: calls.append(("badge", label)))
    app = types.SimpleNamespace(dockTile=lambda: tile)
    running = types.SimpleNamespace(localizedName=lambda: "Python")
    appkit = types.SimpleNamespace(
        NSApplication=types.SimpleNamespace(sharedApplication=lambda: app),
        NSRunningApplication=types.SimpleNamespace(currentApplication=lambda: running),
    )
    helper = types.SimpleNamespace(callAfter=lambda function: (calls.append(("callAfter",)), function()))
    tools = types.ModuleType("PyObjCTools")
    tools.AppHelper = helper
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "PyObjCTools", tools)
    monkeypatch.setattr(native, "_set_launch_services_name", lambda name: calls.append(("name", name)) or True)
    return calls


def test_dock_mark_does_nothing_outside_dev_mode(monkeypatch, fake_appkit):
    monkeypatch.setattr(sys, "platform", "darwin")
    native.mark_dev_dock()
    assert fake_appkit == []


def test_dock_mark_does_nothing_off_macos(monkeypatch, fake_appkit):
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    monkeypatch.setattr(sys, "platform", "win32")
    native.mark_dev_dock()
    assert fake_appkit == []


def test_dock_mark_sets_the_badge_and_the_name_in_dev_mode(monkeypatch, fake_appkit):
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    monkeypatch.setattr(sys, "platform", "darwin")
    native.mark_dev_dock()
    assert fake_appkit == [("callAfter",), ("badge", "DEV"), ("name", "Python (DEV)")]


def test_dock_mark_survives_failures(monkeypatch, fake_appkit):
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    monkeypatch.setattr(sys, "platform", "darwin")

    def broken(name):
        raise OSError("no LaunchServices")

    monkeypatch.setattr(native, "_set_launch_services_name", broken)
    sys.modules["AppKit"].NSApplication.sharedApplication().dockTile().setBadgeLabel_ = broken
    native.mark_dev_dock()
    assert fake_appkit == [("callAfter",)]
