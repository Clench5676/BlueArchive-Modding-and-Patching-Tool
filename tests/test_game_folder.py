"""A folder is a game install only when it holds UI atlas bundles; anything else is refused or starts on the
no-game screen. Decided by file names, through the real RPC registry and the real game_found."""

import os
from pathlib import Path

import pytest

from ba_modding_toolkit.config import Settings
from ba_modding_toolkit.searching import is_game_install
from ba_modding_toolkit.webui.api import build_api
from ba_modding_toolkit.webui.server import WebApp
from ba_modding_toolkit.webui.services import Services
from ba_modding_toolkit.webui.state import UiState
from test_webui import FakeCatalog, FakeLibrary, rpc

RESOURCE = Path("BlueArchive_Data/StreamingAssets/PUB/Resource/GameData/Windows")
ATLAS = "uis-atlas-common-2026-01-01_000_assets_all_1.bundle"


def install(root: Path, *, atlas: bool = True) -> Path:
    """A synthetic install: the Windows resource folder, with or without a UI atlas bundle (empty file)."""
    folder = root / RESOURCE
    folder.mkdir(parents=True)
    (folder / "character-test-2026-01-01_000_assets_all_1.bundle").touch()
    if atlas:
        (folder / ATLAS).touch()
    return root


def bump(folder: Path) -> None:
    """Change a folder's modification time, whatever the clock's resolution."""
    stat = folder.stat()
    os.utime(folder, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def env(tmp_path):
    """Real Services, so game_found runs for real (the game is not injected); the library and catalog are fakes."""
    data = tmp_path / "data"
    data.mkdir()
    services = Services(Settings(data / "config.toml"), data, state=UiState(data / "webui.json"), cache=object(),
                        catalog=FakeCatalog(), library=FakeLibrary())
    app = WebApp(build_api(services), services.events, assets=services.asset, token="tok",
                 consent=lambda: services.state.consent)
    yield type("Env", (), {"tmp": tmp_path, "services": services, "app": app, "settings": services.settings})
    services.close()


@pytest.mark.parametrize("key", ["gameDir.global", "gameDir.japan"])
def test_empty_and_parent_folders_are_refused_and_nothing_is_saved(env, key):
    empty = env.tmp / "empty"
    empty.mkdir()
    steam = env.tmp / "steamapps"
    install(steam / "common" / "Blue Archive")
    (steam / "libraryfolders.vdf").write_text("{}")
    setting = "game_resource_dir" if key.endswith("global") else "game_resource_dir_japan"
    env.settings.save()
    before = env.settings.path.read_bytes()
    for folder in (empty, steam, env.tmp / "missing"):
        assert rpc(env.app, "settings.set", key=key, value=str(folder))["error"]["code"] == "game_not_found"
        assert env.settings.get(setting) == ""
    assert rpc(env.app, "app.info")["result"]["gameFound"] is False
    assert env.settings.path.read_bytes() == before


def test_install_root_and_resource_folder_are_accepted(env):
    root = install(env.tmp / "installed")
    assert rpc(env.app, "settings.set", key="gameDir.global", value=str(root))["result"]["gameDirs"]["global"] == str(root)
    assert rpc(env.app, "app.info")["result"]["gameFound"] is True
    # choosing the resource folder itself stays valid
    resource = root / RESOURCE
    assert rpc(env.app, "settings.set", key="gameDir.global", value=str(resource))["result"]["gameDirs"]["global"] == str(resource)
    assert rpc(env.app, "app.info")["result"]["gameFound"] is True
    # the other server has its own folder
    japan = install(env.tmp / "japan")
    assert "result" in rpc(env.app, "settings.set", key="gameDir.japan", value=str(japan))


def test_only_atlas_bundle_files_count(env):
    folder = env.tmp / "folder"
    folder.mkdir()
    (folder / "uis-atlas-common.txt").touch()  # not a bundle
    (folder / "character-test.bundle").touch()  # a bundle, not an atlas
    (folder / "uis-atlas-folder.bundle").mkdir()  # a folder
    assert not is_game_install(folder)
    assert rpc(env.app, "settings.set", key="gameDir.global", value=str(folder))["error"]["code"] == "game_not_found"
    (folder / "uis-atlas-common.bundle").touch()
    assert is_game_install(folder)
    assert "result" in rpc(env.app, "settings.set", key="gameDir.global", value=str(folder))


def test_the_predicate_reads_names_only(tmp_path, monkeypatch):
    install(tmp_path)
    from ba_modding_toolkit.gameassets import discovery  # imported before files are forbidden
    assert discovery.is_atlas
    for name in ("open", "read_bytes", "read_text"):
        monkeypatch.setattr(Path, name, lambda *a, **k: pytest.fail("read a file"), raising=False)
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("opened a file"))
    assert is_game_install(tmp_path)


@pytest.mark.parametrize("setting", ["game_resource_dir", "game_resource_dir_japan"])
def test_an_old_bad_setting_starts_without_a_game_and_recovers(env, setting):
    empty = env.tmp / "empty"
    empty.mkdir()
    # a setting saved by an earlier version, which took any folder
    env.settings.set("file_source", "windows_japan" if setting.endswith("japan") else "windows_global")
    env.settings.set(setting, str(empty))
    env.settings.save()
    info = rpc(env.app, "app.info")["result"]
    assert info["gameFound"] is False and info["resourceVersion"] is None and info["resourceContext"] is None
    assert rpc(env.app, "settings.get")["result"]["gameFound"] is False
    assert env.services.game_found() is False
    # the no-game screen's folder button: a real install replaces it
    region = "japan" if setting.endswith("japan") else "global"
    root = install(env.tmp / "installed")
    assert "result" in rpc(env.app, "settings.set", key=f"gameDir.{region}", value=str(root))
    assert rpc(env.app, "app.info")["result"]["gameFound"] is True


def test_file_source_switch_refuses_a_folder_that_holds_no_game(env):
    empty = env.tmp / "empty"
    empty.mkdir()
    global_install = install(env.tmp / "global")
    env.settings.set("file_source", "windows_global")
    env.settings.set("game_resource_dir", str(global_install))
    env.settings.set("game_resource_dir_japan", str(empty))
    env.settings.save()
    before = env.settings.path.read_bytes()
    reply = rpc(env.app, "settings.set", key="fileSource", value="windows_japan")
    assert reply["error"] == {"code": "game_directory_missing", "params": {"region": "japan"}}
    assert env.settings.get("file_source") == "windows_global" and env.settings.path.read_bytes() == before
    japan = install(env.tmp / "japan")
    assert "result" in rpc(env.app, "settings.set", key="gameDir.japan", value=str(japan))
    assert "result" in rpc(env.app, "settings.set", key="fileSource", value="windows_japan")


def test_a_download_that_adds_the_atlas_is_seen_without_a_restart(env):
    root = install(env.tmp / "installed", atlas=False)
    env.settings.set("game_resource_dir", str(root))
    env.settings.save()
    assert env.services.game_found() is False
    resource = root / RESOURCE
    (resource / ATLAS).touch()
    assert env.services.game_found() is True  # a folder without the game is checked again, whatever its stamps
    assert env.services.game_found() is True  # the kept verdict
    (resource / ATLAS).unlink()
    bump(resource)
    assert env.services.game_found() is False


def test_the_verdict_is_kept_while_the_folders_are_unchanged(env, monkeypatch):
    root = install(env.tmp / "installed")
    env.settings.set("game_resource_dir", str(root))
    env.settings.save()
    calls = []
    from ba_modding_toolkit import searching
    real = searching.is_game_install
    monkeypatch.setattr(searching, "is_game_install", lambda base: calls.append(base) or real(base))
    assert all(env.services.game_found() for _ in range(5))
    assert len(calls) == 1
