"""The dev mode mark on the native window title, the macOS Dock (bmpt.webui.native) and the served page."""

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
def test_window_title_starts_with_dev_in_dev_mode(monkeypatch, variable):
    monkeypatch.setenv(variable, "")
    assert native.window_title() == "[DEV] " + t("ui.app_title")


def test_dev_title_keeps_a_single_prefix(monkeypatch):
    from bmpt.core import devmode

    assert devmode.title("[DEV] App") == "[DEV] App"
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    assert devmode.title("App") == "[DEV] App"
    assert devmode.title("[DEV] App") == "[DEV] App"


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
    assert fake_appkit == [("callAfter",), ("badge", "DEV"), ("name", "[DEV] Python")]


def test_dock_mark_survives_failures(monkeypatch, fake_appkit):
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    monkeypatch.setattr(sys, "platform", "darwin")

    def broken(name):
        raise OSError("no LaunchServices")

    monkeypatch.setattr(native, "_set_launch_services_name", broken)
    sys.modules["AppKit"].NSApplication.sharedApplication().dockTile().setBadgeLabel_ = broken
    native.mark_dev_dock()
    assert fake_appkit == [("callAfter",)]


def test_dock_mark_leaves_a_marked_name(monkeypatch, fake_appkit):
    monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    monkeypatch.setattr(sys, "platform", "darwin")
    running = types.SimpleNamespace(localizedName=lambda: "[DEV] Python")
    monkeypatch.setattr(sys.modules["AppKit"].NSRunningApplication, "currentApplication", lambda: running)
    native.mark_dev_dock()
    assert fake_appkit == [("callAfter",), ("badge", "DEV")]


def _page(monkeypatch, dev: bool) -> bytes:
    import io

    from bmpt.webui.events import EventBus
    from bmpt.webui.rpc import RpcRegistry
    from bmpt.webui.server import WebApp

    if dev:
        monkeypatch.setenv("BAMT_DEV_ROOTS", "")
    app = WebApp(RpcRegistry(), EventBus(), token="tok")
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/index.html",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
        "HTTP_X_BAMT_TOKEN": "tok",
    }
    status = []
    body = b"".join(app(environ, lambda s, headers: status.append(s)))
    assert status == ["200 OK"]
    return body


def test_page_has_no_dev_mark_outside_dev_mode(monkeypatch):
    body = _page(monkeypatch, dev=False)
    assert b"<title>Blue Archive Modding and Patching Tool</title>" in body
    assert b"dev-mark" not in body
    assert b"[DEV]" not in body


def test_page_carries_the_title_prefix_and_the_badge_in_dev_mode(monkeypatch):
    body = _page(monkeypatch, dev=True)
    assert b"<title>[DEV] Blue Archive Modding and Patching Tool</title>" in body
    assert body.count(b'<div class="dev-mark" aria-hidden="true">DEV</div>') == 1
    assert body.index(b"dev-mark") > body.index(b"<body>")
