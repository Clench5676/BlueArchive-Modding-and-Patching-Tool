"""Check the bootstrap HTTP surface and the Web and Classic path bindings."""

from io import BytesIO
import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bmpt.host.data_upgrade import NAMESPACE, DataUpgrade
from bmpt.webui.events import EventBus
from bmpt.webui.instance import InstanceLock
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


def test_bootstrap_masks_ready_progress_while_services_open(tmp_path):
    coordinator = DataUpgrade(tmp_path, qualify=lambda _: "apfs")
    masked = []
    bootstrap = UpgradeBootstrap(
        coordinator, language=lambda: "en-US", ready=lambda: masked.append(bootstrap.state()), quit_app=lambda: None
    )
    try:
        completed = bootstrap.dispatch("retry")
        assert masked[0]["status"] == "running"
        assert masked[0]["percent"] == 99
        assert masked[0]["steps"][-1] == {"id": "open", "status": "running"}
        assert completed["status"] == "ready"
        assert completed["percent"] == 100
        assert completed["steps"][-1] == {"id": "open", "status": "done"}
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    ("compiled", "flags", "handoff"),
    [(True, [], "relaunch"), (False, [], None), (True, ["--browser"], None), (True, ["--safe-mode"], "safeMode")],
)
def test_first_start_handoff_and_bootstrap_run_log(tmp_path, monkeypatch, caplog, compiled, flags, handoff):
    from bmpt.webui import main, system

    runtime = tmp_path / NAMESPACE / "runtime"
    log = runtime / "webui.log"
    (tmp_path / "webui.json").write_text(json.dumps({"prefs": {"data_id": "012345abcdef"}}))
    coordinators = []

    def make_upgrade(path):
        coordinator = DataUpgrade(path, qualify=lambda _: "apfs", updated_in_app=False)
        coordinators.append(coordinator)
        return coordinator

    monkeypatch.setattr(main, "EXE_DIR", tmp_path)
    monkeypatch.setattr(main, "DataUpgrade", make_upgrade)
    monkeypatch.setattr(system, "can_restart_safe", lambda: compiled)
    monkeypatch.setattr(system, "program_command", lambda: ["bmpt-test-program"])
    monkeypatch.setattr(main.Services, "start", lambda self: None)
    monkeypatch.setattr(main, "_watch_config", lambda *_: None)
    caplog.set_level(logging.INFO, logger=main.__name__)

    class ImmediateThread:
        def __init__(self, target, args=(), **_):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(
        main, "threading", SimpleNamespace(Event=threading.Event, Thread=ImmediateThread, Timer=threading.Timer)
    )
    apps = []

    def make_server(app, _port):
        apps.append(app)

        def start():
            assert log.read_text().startswith("Environment ")
            logging.getLogger(main.__name__).info("Session credential %s", app.token)

        return SimpleNamespace(
            app=app, port=4321, url=f"http://127.0.0.1:4321/?t={app.token}", start=start, stop=lambda: None
        )

    monkeypatch.setattr(main, "Server", make_server)
    closed = threading.Event()

    class Shown(list):
        def __iadd__(self, callback):
            self.append(callback)
            return self

    window = SimpleNamespace(events=SimpleNamespace(shown=Shown()), destroy=closed.set)

    def check_bootstrap(app):
        state = json.loads(_request(app, "/upgrade/state", token=app.token)[1])
        assert state["status"] == "ready" and state["notice"] == "upgraded"
        assert app.services_ready is (handoff != "relaunch")
        if app.services_ready:
            assert app.on_exception.__self__.log_path == log

    def native_start(**_):
        app = apps[-1]
        assert app.recovery()["upgradeHandoff"] == handoff
        assert len(window.events.shown) == 1
        window.events.shown[0]()
        check_bootstrap(app)
        if handoff == "relaunch":
            assert closed.wait(2)

    def browser_open(_):
        app = apps[-1]
        check_bootstrap(app)
        assert _request(app, "/upgrade/quit", method="POST", token=app.token)[0].startswith("200")

    monkeypatch.setitem(
        sys.modules, "webview", SimpleNamespace(create_window=lambda *_, **__: window, start=native_start)
    )
    monkeypatch.setattr(main.webbrowser, "open", browser_open)
    popen = subprocess.Popen
    launches = []

    def launch(command, *args, **kwargs):
        if command[0] != "bmpt-test-program":
            return popen(command, *args, **kwargs)
        assert coordinators[-1].paths is None
        assert not (runtime / "instance.json").exists()
        probe = InstanceLock(tmp_path, runtime=runtime)
        assert probe.acquire(strict=True)
        probe.release()
        launches.append(command)

    monkeypatch.setattr(subprocess, "Popen", launch)
    if handoff == "relaunch":
        service = main.Services
        monkeypatch.setattr(main, "Services", lambda **_: pytest.fail("Bootstrap opened services before relaunch"))
    root = logging.getLogger()
    handlers = list(root.handlers)
    main.main(["--debug", "--mute", "--token", "private-bootstrap-token", *flags])
    assert root.handlers == handlers
    contents = log.read_text()
    assert "Data upgrade bootstrap listening" in contents
    assert "[redacted]" in contents and "private-bootstrap-token" not in contents
    assert not (runtime / "instance.json").exists()
    if handoff == "relaunch":
        assert launches == [["bmpt-test-program", "--debug", "--mute"]]
        monkeypatch.setattr(main, "Services", service)
        normal_window = []

        def open_window(services, server, debug):
            normal_window.append((services.safe_mode, debug, services.mute, services.log_path))
            notice = _request(server.app, "/upgrade/state", token=server.app.token)[1]
            assert json.loads(notice)["notice"] == "upgraded"

        monkeypatch.setattr(main, "_run_window", open_window)
        monkeypatch.setattr(main, "_trace_to_file", lambda _: None)
        main.main(launches[0][1:])
        assert normal_window == [(False, True, True, log)]
        assert "Application lifecycle: run finished" in log.read_text()
        assert (runtime / "webui.1.log").read_text() == contents
    else:
        assert launches == []


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
