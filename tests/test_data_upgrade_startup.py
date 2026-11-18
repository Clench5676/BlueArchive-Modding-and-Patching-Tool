"""Check the bootstrap HTTP surface and the Web and Classic path bindings."""

from io import BytesIO
import json
from pathlib import Path

from bmpt.host.data_upgrade import DataUpgrade
from bmpt.webui.events import EventBus
from bmpt.webui.rpc import RpcRegistry
from bmpt.webui.server import WebApp
from bmpt.webui.upgrade import UpgradeBootstrap


def _request(app, path, *, method="GET", body=b"", token="session"):
    status = []
    env = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "HTTP_X_BAMT_TOKEN": token,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
    }
    output = b"".join(app(env, lambda value, _: status.append(value)))
    return status[0], output


def test_notice_authentication_and_services_gate(tmp_path, monkeypatch):
    calls = []
    data = tmp_path / "data"
    coordinator = DataUpgrade(data, qualify=lambda _: "apfs")
    bootstrap = UpgradeBootstrap(
        coordinator,
        language=lambda: "en-US",
        ready=lambda: calls.append("ready"),
        quit_app=lambda: calls.append("quit"),
    )
    app = WebApp(RpcRegistry(), EventBus(), token="session", upgrade=bootstrap.dispatch, services_ready=False)
    monkeypatch.setattr("bmpt.webui.upgrade.reveal", lambda folder: calls.append(folder))
    try:
        assert _request(app, "/upgrade/state", token="")[0].startswith("403")
        assert _request(app, "/api/rpc", method="POST")[0].startswith("503")
        assert _request(app, "/asset/spine/portrait/a/skeleton")[0].startswith("503")
        assert _request(app, "/upgrade/consent", method="POST")[0].startswith("404")
        assert _request(app, "/upgrade/openLog", method="POST")[0].startswith("200")
        assert _request(app, "/upgrade/retry", method="POST")[0].startswith("200")
        assert json.loads(_request(app, "/upgrade/state")[1])["notice"] is None
        assert _request(app, "/upgrade/quit", method="POST")[0].startswith("200")
        assert calls == [coordinator.log_folder, "ready", "quit"]
        assert coordinator.paths.runtime.is_relative_to(data)
    finally:
        coordinator.close()


def test_completed_upgrade_notice_survives_restarts_until_authenticated_acknowledgement(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "webui.json").write_text(json.dumps({"prefs": {"data_id": "012345abcdef"}}))
    first = DataUpgrade(data, qualify=lambda _: "apfs", updated_in_app=False)
    try:
        assert first.run()["notice"] == "upgraded"
    finally:
        first.close()

    second = DataUpgrade(data, qualify=lambda _: "apfs", updated_in_app=False)
    bootstrap = UpgradeBootstrap(
        second, language=lambda: "en-US", ready=lambda: None, quit_app=lambda: None, opened=True
    )
    app = WebApp(RpcRegistry(), EventBus(), token="session", upgrade=bootstrap.dispatch)
    try:
        assert second.run()["status"] == "ready"
        assert json.loads(_request(app, "/upgrade/state")[1])["notice"] == "upgraded"
        assert _request(app, "/upgrade/acknowledge", method="POST", token="")[0].startswith("403")
        assert _request(app, "/upgrade/acknowledge")[0].startswith("405")
        assert json.loads(_request(app, "/upgrade/acknowledge", method="POST")[1])["notice"] is None
    finally:
        second.close()

    last = DataUpgrade(data, qualify=lambda _: "apfs", updated_in_app=False)
    try:
        assert last.run()["notice"] is None
        prefs = last.paths.ui_state
        assert json.loads(prefs.read_text())["prefs"]["data_upgrade_notice"] is None
        last.close()
        prefs.unlink()
        assert last.run()["status"] == "ready" and last.state()["notice"] is None
    finally:
        last.close()


def test_headless_pending_does_not_build_services(tmp_path, monkeypatch):
    import pytest

    from bmpt.webui import main

    monkeypatch.setattr(main, "EXE_DIR", tmp_path)
    monkeypatch.setattr(main, "DataUpgrade", lambda path: DataUpgrade(path, qualify=lambda _: "remote"))
    monkeypatch.setattr(main, "Services", lambda *_, **__: pytest.fail("Services initialized before the gate"))
    with pytest.raises(SystemExit) as result:
        main.main(["--serve", "--safe-mode"])
    assert result.value.code == 1


def test_existing_bootstrap_owns_pending_data_directory(tmp_path, monkeypatch):
    import pytest

    from bmpt.host.data_upgrade import NAMESPACE
    from bmpt.webui import main
    from bmpt.webui.instance import InstanceLock

    runtime = tmp_path / NAMESPACE / "runtime"
    raised = []
    monkeypatch.setattr(main, "EXE_DIR", tmp_path)
    monkeypatch.setattr(main, "raise_running", lambda path: raised.append(path) or True)
    monkeypatch.setattr(main, "DataUpgrade", lambda _: pytest.fail("second start created an upgrade"))
    instance = InstanceLock(tmp_path, runtime=runtime)
    assert instance.acquire(strict=True)
    try:
        instance.publish(4321, "secret")
        with pytest.raises(SystemExit) as browser:
            main.main(["--browser"])
        with pytest.raises(SystemExit) as headless:
            main.main(["--serve"])
        assert browser.value.code == 0 and headless.value.code == 1
        assert raised == [runtime]
    finally:
        instance.release()


def test_classic_standalone_uses_committed_paths(tmp_path, monkeypatch):
    from bmpt import launcher
    from bmpt.webui import main

    monkeypatch.setattr("bmpt.engine.EXE_DIR", tmp_path, raising=False)
    monkeypatch.setattr("bmpt.engine.classic_main", lambda _: None, raising=False)
    bound = []

    def engine(args):
        import os

        bound.append((args, Path(os.environ["BMPT_CLASSIC_CONFIG"]), Path(os.environ["BMPT_CLASSIC_ARTIFACTS"])))

    monkeypatch.setattr("bmpt.engine.classic_main", engine)
    monkeypatch.setattr("bmpt.host.data_upgrade.DataUpgrade", lambda path: DataUpgrade(path, qualify=lambda _: "apfs"))
    monkeypatch.setattr(main, "open_classic_upgrade", lambda upgrade: upgrade.run()["status"] == "ready")
    launcher.main(["--classic", "--settings"])
    assert bound[0][0] == ["--settings"]
    assert bound[0][1].is_relative_to(tmp_path / ".bmpt-upgrade" / "data")
    assert bound[0][2] == tmp_path / ".bmpt-upgrade" / "artifacts"


def test_newer_format_only_exposes_update_check_after_authentication(tmp_path):
    import json

    data = tmp_path / "data"
    data.mkdir()
    (data / "data.json").write_text(
        json.dumps({"format": "app_data", "format_version": "3.0", "min_reader_version": "3.0"}),
        encoding="utf-8",
    )
    coordinator = DataUpgrade(data, qualify=lambda _: "apfs")
    coordinator.run()
    actions = []
    bootstrap = UpgradeBootstrap(
        coordinator,
        language=lambda: "en-US",
        ready=lambda: None,
        quit_app=lambda: None,
        update=lambda: actions.append("update"),
    )
    app = WebApp(RpcRegistry(), EventBus(), token="session", upgrade=bootstrap.dispatch, services_ready=False)
    assert _request(app, "/upgrade/update", method="POST", token="")[0].startswith("403")
    assert not actions
    status, response = _request(app, "/upgrade/update", method="POST")
    assert status.startswith("200")
    assert json.loads(response)["reason"] == "newerFormat"
    assert actions == ["update"]
