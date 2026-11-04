"""Tests for the Tk-free settings in ba_modding_toolkit.config."""

import itertools
import os
from pathlib import Path

import pytest
import toml

from ba_modding_toolkit.config import FIELDS, Field, Settings
from ba_modding_toolkit.spine import SpineViewer


def test_defaults_when_file_missing(tmp_path: Path):
    s = Settings(tmp_path / "config.toml")
    assert s.get("max_workers") == 4
    assert s.get("enable_crc_correction") == "auto"
    assert s.get("skip_unchanged") is True


def test_values_are_coerced_to_the_default_type(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('[SaveOptions]\nskip_unchanged = "false"\n[AppSettings]\nmax_workers = "6"\n', encoding="utf-8")
    s = Settings(p)
    assert s.get("skip_unchanged") is False
    assert s.get("max_workers") == 6


def test_save_keeps_unknown_keys_and_uses_classic_groups(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('[Custom]\nx = 1\n[SaveOptions]\nfuture_option = "y"\n', encoding="utf-8")
    s = Settings(p)
    s.set("compression_method", "lz4")
    s.save()
    data = toml.loads(p.read_text(encoding="utf-8"))
    assert data["Custom"] == {"x": 1}
    assert data["SaveOptions"]["future_option"] == "y"
    assert data["SaveOptions"]["compression_method"] == "lz4"
    for key, field in FIELDS.items():
        assert key in data[field.group]


def test_reload_if_changed_notifies(tmp_path: Path):
    p = tmp_path / "config.toml"
    s = Settings(p)
    s.save()
    seen = []
    s.on_change(lambda: seen.append(True))
    assert s.reload_if_changed() is False
    p.write_text('[AppSettings]\nmax_workers = 2\n', encoding="utf-8")
    import os
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert s.reload_if_changed() is True
    assert s.get("max_workers") == 2
    assert seen == [True]


def test_asset_types(tmp_path: Path):
    s = Settings(tmp_path / "config.toml")
    assert s.asset_types() == {"Texture2D", "TextAsset", "Mesh", "AnimationClip"}
    s.set("replace_all", True)
    assert s.asset_types() == {"ALL"}


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("path", ["", " \t ", "missing/SpineViewerCLI.exe"])
def test_anim_check_options_without_a_tool_has_no_backend(tmp_path, enabled, path):
    settings = Settings(tmp_path / "config.toml")
    settings.set("check_animations", enabled)
    settings.set("spine_viewer_path", path)

    options = settings.anim_check_options()

    assert options.enabled is enabled
    assert options.backend is None
    assert options.is_valid() is False


@pytest.mark.parametrize("enabled", [False, True])
def test_anim_check_options_uses_the_configured_viewer(tmp_path, enabled):
    settings = Settings(tmp_path / "config.toml")
    path = tmp_path / "SpineViewerCLI.exe"
    path.touch()
    settings.set("check_animations", enabled)
    settings.set("spine_viewer_path", f"  {path}  ")

    options = settings.anim_check_options()

    assert options.enabled is enabled
    assert isinstance(options.backend, SpineViewer) and options.backend.tool_path == path
    assert options.is_valid() is enabled


def test_anim_check_key_is_stable_and_follows_the_settings(tmp_path):
    """Build signatures take this key: it must not change between calls, only with the settings."""
    settings = Settings(tmp_path / "config.toml")
    path = tmp_path / "SpineViewerCLI.exe"
    path.touch()
    settings.set("spine_viewer_path", str(path))

    assert settings.anim_check_key() == settings.anim_check_key() == (False, str(path))
    settings.set("check_animations", True)
    assert settings.anim_check_key() == (True, str(path))
    settings.set("spine_viewer_path", "")
    assert settings.anim_check_key() == (True, "")


def test_explicit_directory_pair_never_runs_game_detection(tmp_path: Path, monkeypatch):
    """A value read from the file is coerced by the field's type: the detection default runs only for a missing key."""
    from ba_modding_toolkit import config

    def detect(region="auto"):
        raise AssertionError("game detection ran")

    monkeypatch.setattr(config, "get_BA_path", detect)
    p = tmp_path / "config.toml"
    p.write_text('[Directories]\nfile_source = "windows_global"\ngame_resource_dir = "/games/ba"\n'
                 'game_resource_dir_japan = "/games/japan"\n', encoding="utf-8")
    s = Settings(p)
    assert s.get("game_resource_dir") == "/games/ba"
    assert s.get("game_resource_dir_japan") == "/games/japan"
    s.set("game_resource_dir", Path("/games/other"))
    assert s.get("game_resource_dir") == "/games/other"
    s.set("file_source", "windows_japan")
    assert s.get("file_source") == "windows_japan"


def test_a_missing_key_still_runs_its_default(tmp_path: Path, monkeypatch):
    from ba_modding_toolkit import config

    monkeypatch.setattr(config, "get_BA_path", lambda region="auto": "/found" if region == "global" else None)
    s = Settings(tmp_path / "config.toml")
    assert s.get("game_resource_dir") == "/found"
    assert s.get("game_resource_dir_japan") == ""
    assert s.get("file_source") == "windows_global"


@pytest.mark.parametrize("found", [None, "global", "japan"])
def test_each_install_is_detected_at_most_once_per_settings_load(tmp_path, monkeypatch, found):
    calls = []

    def detect(region):
        calls.append(region)
        return f"/games/{region}" if region == found else None

    monkeypatch.setattr("ba_modding_toolkit.config.get_BA_path", detect)
    settings = Settings(tmp_path / "config.toml")
    assert calls == ["global", "japan"]
    assert settings.get("file_source") == ("windows_japan" if found == "japan" else "windows_global")
    settings.load()
    assert calls == ["global", "japan", "global", "japan"]


def test_field_kinds():
    assert Field("G", True).kind is bool and Field("G", 4).kind is int and Field("G", "x").kind is str
    assert Field("G", lambda: "x", str).kind is str
    with pytest.raises(TypeError):
        Field("G", lambda: "x")
    for key, field in FIELDS.items():
        if not callable(field.default):
            assert field.kind is type(field.default), key
        assert field.kind in (bool, int, str), key


@pytest.mark.parametrize("identified", ["global", "japan", "unknown", "ambiguous"])
@pytest.mark.parametrize("japan_key", [False, True])
def test_legacy_directory_migration_is_lossless_and_idempotent(tmp_path, monkeypatch, identified, japan_key):
    install = tmp_path / "install"
    resources = install / "GameData" / "Windows"
    resources.mkdir(parents=True)
    monkeypatch.setattr("ba_modding_toolkit.config.get_BA_path", lambda region:
                        str(install) if identified in (region, "ambiguous") else None)
    path = tmp_path / "config.toml"
    dirs = {"game_resource_dir": str(resources), "file_source": "windows_global", "future": "kept"}
    if japan_key:
        dirs["game_resource_dir_japan"] = ""
    path.write_text(toml.dumps({"Directories": dirs}))
    original = path.read_bytes()
    settings = Settings(path)
    # Loading does not write the file; saving retains keys understood by previous versions.
    assert path.read_bytes() == original
    region = "japan" if identified == "japan" else "global"
    assert settings.game_dir(region) == resources
    assert settings.get("file_source") == "windows_global"
    if region == "japan":
        assert settings.game_dir("global") is None
    settings.save()
    saved = toml.loads(path.read_text())["Directories"]
    assert saved["future"] == "kept"
    assert str(resources) in (saved["game_resource_dir"], saved["game_resource_dir_japan"])
    assert Settings(path).as_dict() == settings.as_dict()


def test_migration_keeps_two_explicit_folders_and_classic_uses_same_rule(tmp_path, monkeypatch):
    from ba_modding_toolkit.gui.configs import ConfigManager, ConfigMeta
    from types import SimpleNamespace

    legacy = tmp_path / "japan"
    legacy.mkdir()
    monkeypatch.setattr("ba_modding_toolkit.gui.configs.get_BA_path", lambda region: str(legacy) if region == "japan" else None)
    path = tmp_path / "config.toml"
    path.write_text(toml.dumps({"Directories": {"game_resource_dir": str(legacy)}}))
    class Variable:
        def set(self, value):
            self.value = value
    app = SimpleNamespace(_config_specs={key + "_var": ConfigMeta("Directories", "")
                          for key in ("game_resource_dir", "game_resource_dir_japan")},
                          game_resource_dir_var=Variable(), game_resource_dir_japan_var=Variable())
    assert ConfigManager(path).load_config(app)
    assert app.game_resource_dir_var.value == ""
    assert app.game_resource_dir_japan_var.value == str(legacy)


@pytest.mark.parametrize("source,region", [("windows_global", "global"), ("windows_japan", "japan")])
def test_selected_server_resolution_never_falls_back(tmp_path, source, region):
    settings = Settings(tmp_path / "config.toml")
    for name in ("global", "japan"):
        folder = tmp_path / name
        folder.mkdir()
        settings.set("game_resource_dir" + ("_japan" if name == "japan" else ""), str(folder))
    settings.set("file_source", source)
    assert settings.game_dir() == tmp_path / region
    assert settings.resource_dirs() == [tmp_path / region]
    settings.set("game_resource_dir" + ("_japan" if region == "japan" else ""), "")
    assert settings.game_dir() is None and settings.resource_dirs() == []
    settings.set("file_source", "adb_" + region)
    assert settings.game_dir() is None


# --- a bad config.toml (FUN-10) ---

@pytest.fixture
def detection(monkeypatch):
    """No game is detected, so every default is the same on every machine."""
    monkeypatch.setattr("ba_modding_toolkit.config.get_BA_path", lambda region="auto": None)


def _write_bad(path: Path, kind: str) -> bytes:
    content = {
        "non-utf8": b'[AppSettings]\nlanguage = "caf\xe9"\n',
        "syntax": b'[AppSettings\nmax_workers = 2\n',
        "half-written": b'[Directories]\nfile_source = "windows_gl',
    }[kind]
    path.write_bytes(content)
    return content


@pytest.mark.parametrize("kind", ["non-utf8", "syntax", "half-written"])
def test_a_file_that_cannot_be_parsed_starts_on_defaults(tmp_path, detection, kind):
    path = tmp_path / "config.toml"
    original = _write_bad(path, kind)
    settings = Settings(path)
    assert settings.get("max_workers") == 4 and settings.get("language") == ""
    assert settings.get("file_source") == "windows_global" and settings.get("game_resource_dir") == ""
    assert path.read_bytes() == original and not path.with_name("config.toml.bad").exists(), "loading writes nothing"


@pytest.mark.parametrize("kind", ["non-utf8", "syntax", "half-written"])
def test_a_save_keeps_the_unparsable_file_as_bad_first(tmp_path, detection, kind):
    path, bad = tmp_path / "config.toml", tmp_path / "config.toml.bad"
    original = _write_bad(path, kind)
    settings = Settings(path)
    settings.set("max_workers", 7)
    settings.save()
    assert bad.read_bytes() == original
    assert Settings(path).get("max_workers") == 7
    # the file parses now: later saves do not touch the copy
    settings.set("max_workers", 8)
    settings.save()
    assert bad.read_bytes() == original and Settings(path).get("max_workers") == 8
    assert not list(tmp_path.glob("*.tmp"))


def test_an_unparsable_file_is_kept_even_when_it_was_hand_fixed_and_broken_again(tmp_path, detection):
    path, bad = tmp_path / "config.toml", tmp_path / "config.toml.bad"
    settings = Settings(path)
    settings.save()
    path.write_bytes(b"[Broken")  # broken after the load: the check is made at the save, not remembered
    settings.save()
    assert bad.read_bytes() == b"[Broken"


def test_a_save_has_nothing_to_keep_of_a_missing_or_empty_file(tmp_path, detection):
    path, bad = tmp_path / "config.toml", tmp_path / "config.toml.bad"
    settings = Settings(path)
    settings.save()
    path.write_bytes(b"")
    settings.save()
    assert not bad.exists()


def test_a_save_goes_ahead_when_the_bad_copy_cannot_be_made(tmp_path, detection, monkeypatch, caplog):
    path = tmp_path / "config.toml"
    _write_bad(path, "syntax")
    settings = Settings(path)

    def refuse(*args, **kwargs):
        raise PermissionError("no copy")

    monkeypatch.setattr("ba_modding_toolkit.config.shutil.copyfileobj", refuse)
    settings.save()
    assert Settings(path).get("max_workers") == 4
    assert "Cannot keep the unreadable config.toml" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("text", [
    'Directories = 3\n[SaveOptions]\ncompression_method = "lz4"\n',
    '[Directories]\ngame_resource_dir = 2020-01-01\ngame_resource_dir_japan = [1, 2]\n[SaveOptions]\n'
    'compression_method = "lz4"\n',
    'AppSettings = "x"\nTools = []\n[SaveOptions]\ncompression_method = "lz4"\n',
])
def test_a_group_or_value_of_the_wrong_type_reads_as_missing_and_the_rest_is_read(tmp_path, detection, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    settings = Settings(path)
    assert settings.get("compression_method") == "lz4"
    assert settings.get("game_resource_dir") == "" and settings.get("game_resource_dir_japan") == ""
    assert settings.get("max_workers") == 4 and settings.get("report_format") == "list"
    settings.save()  # does not stop either
    assert toml.loads(path.read_text(encoding="utf-8"))["SaveOptions"]["compression_method"] == "lz4"
    assert not path.with_name("config.toml.bad").exists(), "a file that parses is not a bad file"


def test_a_value_of_the_wrong_type_runs_the_detection_default_like_a_missing_key(tmp_path, monkeypatch):
    monkeypatch.setattr("ba_modding_toolkit.config.get_BA_path", lambda region="auto": "/found" if region == "global" else None)
    path = tmp_path / "config.toml"
    path.write_text("[Directories]\ngame_resource_dir = 2020-01-01\nfile_source = {a = 1}\n", encoding="utf-8")
    settings = Settings(path)
    assert settings.get("game_resource_dir") == "/found" and settings.get("file_source") == "windows_global"


def test_the_legacy_directory_migration_ignores_a_value_that_is_not_text():
    from ba_modding_toolkit.config import migrate_game_directories

    for directories in (3, [], {"game_resource_dir": 3}, {"game_resource_dir": ["x"]}, {"game_resource_dir": "\0"}):
        assert migrate_game_directories(directories, lambda region: "/games") == directories


_ticks = itertools.count(1)


def _poll_after_write(path: Path, content: bytes, settings: Settings) -> bool:
    """Write the file, move its modification time forward (a coarse clock must not hide the change) and poll."""
    path.write_bytes(content)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + next(_ticks) * 5_000_000_000))
    return settings.reload_if_changed()


@pytest.mark.parametrize("content", [b"", b"  \n", b"[AppSettings\nmax_workers = 9\n", b'[AppSettings]\nlanguage = "\xff"\n',
                                     b'[Directories]\nfile_source = "windows_jap'])
def test_the_poll_never_applies_a_file_that_cannot_be_read_as_a_whole(tmp_path, detection, content, caplog):
    path = tmp_path / "config.toml"
    settings = Settings(path)
    settings.set("max_workers", 6)
    settings.set("game_resource_dir", "/games/ba")
    settings.save()
    seen = []
    settings.on_change(lambda: seen.append(settings.as_dict()))
    before = settings.as_dict()
    with caplog.at_level("WARNING"):
        assert _poll_after_write(path, content, settings) is False
        assert settings.reload_if_changed() is False and settings.reload_if_changed() is False
    assert settings.as_dict() == before and seen == []
    assert caplog.text.count("Cannot read config.toml") == 1, "logged once per state of the file"
    # the writer finishes: the complete file is applied
    complete = toml.dumps({"AppSettings": {"max_workers": 2}}).encode()
    assert _poll_after_write(path, complete, settings) is True
    assert settings.get("max_workers") == 2 and len(seen) == 1


def test_a_file_that_was_bad_at_the_start_is_applied_once_it_is_fixed(tmp_path, detection):
    path = tmp_path / "config.toml"
    _write_bad(path, "syntax")
    settings = Settings(path)
    seen = []
    settings.on_change(lambda: seen.append(True))
    assert settings.reload_if_changed() is False and seen == []
    assert _poll_after_write(path, b"[AppSettings]\nmax_workers = 3\n", settings) is True
    assert settings.get("max_workers") == 3 and seen == [True]


def test_an_empty_file_at_the_start_is_all_defaults_but_a_missing_one_still_resets_a_reload(tmp_path, detection):
    path = tmp_path / "config.toml"
    path.write_bytes(b"")
    assert Settings(path).get("max_workers") == 4
    path.write_text("[AppSettings]\nmax_workers = 3\n", encoding="utf-8")
    settings = Settings(path)
    path.unlink()
    assert settings.reload_if_changed() is True and settings.get("max_workers") == 4


def test_the_poll_still_reloads_a_change_in_a_file_of_the_same_size(tmp_path, detection):
    path = tmp_path / "config.toml"
    path.write_text("[AppSettings]\nmax_workers = 2\n", encoding="utf-8")
    settings = Settings(path)
    assert _poll_after_write(path, b"[AppSettings]\nmax_workers = 3\n", settings) is True
    assert settings.get("max_workers") == 3
