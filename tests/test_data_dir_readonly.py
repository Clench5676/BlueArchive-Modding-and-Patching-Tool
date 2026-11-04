"""A data folder that cannot be written (FUN-14): tested at start, reported by app.info, and its failed writes answer
data_dir_readonly instead of internal."""

import errno
import json
import os
import time
from pathlib import Path

import pytest

from bmpt.webui import services as services_module
from bmpt.webui.services import WRITE_DENIED, folder_writable
from test_webui import app, call, rpc, services  # noqa: F401 - fixtures

LOCALES = Path(services_module.__file__).resolve().parents[1] / "locales"
posix_permissions = pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                                       reason="needs a folder the permission bits can make read-only")


@pytest.fixture
def readonly(tmp_path):
    """The data folder made read-only after the services were built, and restored for the clean-up."""
    def lock():
        tmp_path.chmod(0o500)

    yield lock
    tmp_path.chmod(0o700)


# --- the test of the folder ---

def test_a_writable_folder_passes_and_keeps_nothing(tmp_path):
    assert folder_writable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []


def test_a_missing_folder_is_made_and_a_file_in_its_place_fails(tmp_path):
    assert folder_writable(tmp_path / "a" / "b") is True and (tmp_path / "a" / "b").is_dir()
    (tmp_path / "file").write_bytes(b"x")
    assert folder_writable(tmp_path / "file") is False


@posix_permissions
def test_a_read_only_folder_fails(tmp_path):
    tmp_path.chmod(0o500)
    try:
        assert folder_writable(tmp_path) is False
    finally:
        tmp_path.chmod(0o700)
    assert list(tmp_path.iterdir()) == []


def test_the_probe_writes_and_removes_a_real_file(tmp_path, monkeypatch):
    seen = []
    real_unlink = os.unlink

    def unlink(name, *args, **kwargs):
        seen.append((Path(name).name, Path(name).read_bytes()))
        real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    assert folder_writable(tmp_path) is True
    assert len(seen) == 1 and seen[0][0].startswith(".write-test-") and seen[0][1] != b""
    assert list(tmp_path.iterdir()) == []


# --- app.info ---

def test_app_info_reports_the_result(app, services):
    assert services.data_dir_writable is True
    assert rpc(app, "app.info")["result"]["dataDirWritable"] is True
    services.data_dir_writable = False
    info = rpc(app, "app.info")["result"]
    assert info["dataDirWritable"] is False and info["dataDir"] == str(services.data_dir)


@posix_permissions
def test_the_services_test_the_folder_at_start(tmp_path):
    from bmpt.config import Settings
    from bmpt.webui.services import Services
    from bmpt.webui.state import UiState

    data = tmp_path / "data"
    data.mkdir()
    settings, state = Settings(data / "config.toml"), UiState(data / "webui.json")
    data.chmod(0o500)
    try:
        locked = Services(settings, data, state=state, cache=object(), game=object(), catalog=object(), library=object())
        locked.close()
    finally:
        data.chmod(0o700)
    assert locked.data_dir_writable is False
    unlocked = Services(settings, data, state=state, cache=object(), game=object(), catalog=object(), library=object())
    unlocked.close()
    assert unlocked.data_dir_writable is True and list(data.glob(".write-test-*")) == []


# --- failed writes ---

@posix_permissions
def test_writes_into_a_read_only_data_folder_answer_data_dir_readonly(app, services, readonly):
    rpc(app, "ui.visit", variant="hoshino")  # writes fine while the folder takes files
    readonly()
    answer = rpc(app, "ui.visit", variant="shiroko")
    assert answer["error"] == {"code": "data_dir_readonly", "params": {"path": str(services.data_dir)}}
    assert rpc(app, "app.info")["result"]["dataDir"] == str(services.data_dir)  # reads go on working
    assert UiState_recent(services) == ["hoshino"]


def UiState_recent(services):
    return json.loads(services.state.path.read_text(encoding="utf-8"))["recent"]


@posix_permissions
def test_a_job_that_cannot_write_fails_with_data_dir_readonly(services, readonly):
    def run(log, progress):
        services.state.visit("hoshino")

    readonly()
    key = services.jobs.submit("probe", run)
    deadline = time.monotonic() + 5
    while services.jobs.get(key).pending and time.monotonic() < deadline:
        time.sleep(0.01)
    job = services.jobs.get(key)
    assert job.state == "failed" and job.error == {"code": "data_dir_readonly", "params": {"path": str(services.data_dir)}}


def test_a_failure_elsewhere_stays_internal(app, services, tmp_path):
    def broken(variant):
        raise PermissionError(errno.EACCES, "denied", str(tmp_path.parent / "elsewhere" / "file"))

    services.state.visit = broken
    answer = rpc(app, "ui.visit", variant="hoshino")
    assert answer["error"]["code"] == "internal"


# --- what stands for a read-only data folder ---

def denied(path, code=errno.EACCES, **kwargs):
    return PermissionError(code, "denied", str(path), *([None, str(kwargs["other"])] if "other" in kwargs else []))


@pytest.fixture
def unwritable(monkeypatch):
    monkeypatch.setattr(services_module, "folder_writable", lambda folder: False)


def test_only_refused_writes_into_the_data_folder_are_translated(services, unwritable):
    data = services.data_dir
    for code in WRITE_DENIED:
        known = services.translate_error(denied(data / "webui.json", code))
        assert known is not None and known.code == "data_dir_readonly" and known.params == {"path": str(data)}
    assert services.translate_error(denied(data / "library" / "state.json")).code == "data_dir_readonly"
    assert services.translate_error(denied(data)).code == "data_dir_readonly"
    assert services.translate_error(denied(tmp_elsewhere(data), other=data / "x")).code == "data_dir_readonly"
    # not the data folder: a sibling that shares a prefix, another folder, no path at all
    assert services.translate_error(denied(Path(str(data) + "-other") / "webui.json")) is None
    assert services.translate_error(denied(data.parent / "game" / "a.bundle")) is None
    assert services.translate_error(PermissionError(errno.EACCES, "denied")) is None
    # not a refused write: out of space, a missing file, a bug
    assert services.translate_error(OSError(errno.ENOSPC, "full", str(data / "webui.json"))) is None
    assert services.translate_error(FileNotFoundError(errno.ENOENT, "gone", str(data / "webui.json"))) is None
    assert services.translate_error(ValueError("bug")) is None


def tmp_elsewhere(data):
    return data.parent / "elsewhere" / "file"


def test_a_busy_file_in_a_folder_that_takes_files_is_not_a_read_only_folder(services, monkeypatch):
    monkeypatch.setattr(services_module, "folder_writable", lambda folder: True)
    assert services.translate_error(denied(services.data_dir / "webui.json")) is None


# --- the notice's text ---

@pytest.mark.parametrize("code", ["en-US", "ja-JP", "ko-KR", "th-TH", "zh-CN", "zh-TW"])
def test_every_locale_has_the_message_with_the_path(code):
    text = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))["webui"]["error"]["data_dir_readonly"]
    assert "{path}" in text and text.count("{") == 1


def test_the_english_message():
    english = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))["webui"]["error"]
    assert english["data_dir_readonly"] == "Data folder not writable: {path}"
