"""Registered App strings preserve engine messages and per-language fallback."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_modding_toolkit import asset, i18n
from ba_modding_toolkit.models import AssetType, SaveOptions
from bmpt.errors import ServiceError
from bmpt.library import rebuild
from bmpt.localization import LOCALES, register_locales
from bmpt.webui.strings import ui_strings

EXPECTED = json.loads((Path(__file__).parent / "fixtures" / "package_split" / "messages.json").read_text())


@pytest.mark.parametrize("lang", ["en-US", "zh-CN", "th-TH"])
def test_engine_and_library_callers_keep_their_original_app_messages(lang, monkeypatch, tmp_path):
    register_locales()
    manager = i18n.i18n_manager
    previous = manager.lang
    manager.set_language(lang)
    try:
        data = SimpleNamespace(m_Name="missing", m_StreamData=SimpleNamespace(path=""))
        obj = SimpleNamespace(type=AssetType.Texture2D, read=lambda: data)
        with pytest.raises(ValueError) as caught:
            asset.extract_asset(obj, asset.ExtractContext(SimpleNamespace(files={}), None, lambda *_: None))
        assert str(caught.value) == EXPECTED[lang]["stream"]

        monkeypatch.setattr(rebuild, "collect_stream_companions", lambda *_: None)
        obj = SimpleNamespace(get_raw_data=lambda: b"synthetic")
        with pytest.raises(ValueError) as caught:
            rebuild.UpdateScope.serialized(SimpleNamespace(env=SimpleNamespace(file=None)), obj)
        assert str(caught.value) == EXPECTED[lang]["stream"]

        failure = ServiceError("required_asset_extract_failed", file="example.bundle", name="required.atlas", error="synthetic")
        def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(rebuild.Bundle, "load", fail)
        result = rebuild.rebuild([tmp_path / "source"], [tmp_path / "target"], tmp_path, {"ALL"}, SaveOptions())
        assert not result.success and result.error is failure
        assert result.message == EXPECTED[lang]["extract"]
        assert rebuild.t("webui.import.notTransferred", names="example") == EXPECTED[lang]["not_transferred"]
    finally:
        manager.set_language(previous)


def test_registration_reloads_cached_messages_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_locale_dirs", [])
    manager = i18n.I18n("en-US", str(tmp_path))
    monkeypatch.setattr(i18n, "i18n_manager", manager)
    assert manager.t("app.title") == "app.title"
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "en-US.json").write_text(json.dumps({"app": {"title": "Added"}}))
    i18n.register_locale_dir(tmp_path / "extra")
    assert manager.t("app.title") == "Added"
    i18n.register_locale_dir(tmp_path / "extra" / ".")
    assert i18n._locale_dirs == [(tmp_path / "extra").resolve()]
    assert manager.t("app.title") == "Added"


def test_engine_values_win_nested_collisions_and_languages_fall_back(tmp_path, monkeypatch):
    engine, extra = tmp_path / "engine", tmp_path / "extra"
    engine.mkdir()
    extra.mkdir()
    (engine / "en-US.json").write_text(json.dumps({"shared": {"value": "Engine", "scalar": "Keep scalar"}}))
    (extra / "en-US.json").write_text(json.dumps({"shared": {"value": "App", "added": "Extra", "scalar": {"nested": "App"}}, "app": {"fallback": "Fallback {name}"}}))
    (extra / "th-TH.json").write_text(json.dumps({"app": {"own": "Thai locale"}}))
    monkeypatch.setattr(i18n, "_locale_dirs", [])
    monkeypatch.setattr(i18n, "i18n_manager", i18n.I18n("en-US", str(engine)))
    i18n.register_locale_dir(extra)
    manager = i18n.I18n("th-TH", str(engine))
    assert manager.t("shared.value") == "Engine"
    assert manager.t("shared.scalar") == "Keep scalar"
    assert manager.t("shared.added") == "Extra"
    assert manager.t("app.own") == "Thai locale"
    assert manager.t("app.fallback", name="example") == "Fallback example"
    manager.set_language("debug")
    assert manager.t("app.own") == "app.own"


def test_page_strings_read_the_app_directory_without_registration(monkeypatch):
    monkeypatch.setattr(i18n, "_locale_dirs", [])
    assert ui_strings("th-TH", fallback=False)["webui.languageName"]
    assert set(json.loads((LOCALES / "th-TH.json").read_text())) == {"webui"}


@pytest.mark.parametrize("lang", ["en-US", "ja-JP", "ko-KR", "th-TH", "zh-CN", "zh-TW"])
def test_every_locale_string_and_key_survives_the_split(lang):
    hashes = json.loads((Path(__file__).parent / "fixtures" / "package_split" / "locale_hashes.json").read_text())
    engine = json.loads((Path(i18n.__file__).parent / "locales" / f"{lang}.json").read_text())
    app = json.loads((LOCALES / f"{lang}.json").read_text())
    assert "webui" not in engine and set(app) == {"webui"}
    original = json.dumps({**engine, **app}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(original.encode()).hexdigest() == hashes[lang]
