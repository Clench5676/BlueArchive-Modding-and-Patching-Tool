"""Analyzed source previews use the existing revisioned, memory-only Spine asset flow."""

import json
from types import SimpleNamespace

import pytest

from bmpt.errors import ServiceError
from bmpt.gameassets import AssetCache, GameAssets
from bmpt.webui.server import WebApp

from test_batch_import import analyze, env, batch_model_env
from test_library import game_bundle, make_mod, setup_library
from test_webui import call
from test_library_model import model_env


def preview_route(env, entry):
    preview = entry["preview"]
    assert preview["kind"] == "import" and len(preview["key"]) == 64
    app = WebApp(env.rpc, env.services.events, assets=env.services.asset, token="tok", consent=lambda: True)
    return app, f"/asset/spine/import/{preview['key']}"


def test_source_preview_composes_missing_official_parts_and_never_writes_art_to_disk(env, monkeypatch):
    source = make_mod(env.original, env.tmp / "input")
    text = game_bundle(env.game_dir, part="textassets", payload=b"official skeleton")
    assets = {text: {"test_spr.skel": b"official skeleton", "test_spr.atlas": b"mod.png\n"},
              source: {"mod.png": b"incoming pixels"}, env.original: {"mod.png": b"official pixels"}}
    seen = []

    def extract(paths):
        seen.append(list(paths))
        return {name: data for path in paths for name, data in assets[path].items()}

    cache = AssetCache(allowed=True, root=env.tmp / "art-cache")
    env.services._game = GameAssets([env.game_dir], cache)
    monkeypatch.setattr("bmpt.gameassets.service.extract_spine", extract)
    before = {path: path.read_bytes() for path in [env.original, text, source]}
    entry, = analyze(env, [source])["entries"]
    app, prefix = preview_route(env, entry)
    status, headers, body = call(app, prefix + "/manifest.json")
    assert status == "200 OK" and headers["Cache-Control"] == "no-store"
    revision = json.loads(body)["revision"]
    for name, expected in {"skeleton.skel": b"official skeleton", "skeleton.atlas": b"mod.png\n",
                           "mod.png": b"incoming pixels"}.items():
        status, headers, body = call(app, f"{prefix}/{revision}/{name}")
        assert (status, body) == ("200 OK", expected)
        assert headers["Cache-Control"] == "no-store"
    assert seen == [[text, source]], "Extraction is shared in memory and source pixels override the replaced part"
    assert cache.memory_bytes > 0 and not cache.root.exists()
    assert not env.library.mods()
    assert all(path.read_bytes() == data for path, data in before.items())


def test_a_transferring_import_previews_only_the_asset_types_it_writes(env, monkeypatch):
    from test_batch_import import rpc
    source = game_bundle(env.tmp / "input", date="2025-01-01", payload=b"older mod")
    text = game_bundle(env.game_dir, part="textassets", payload=b"official skeleton")
    assets = {text.resolve(): {"test_spr.skel": ("TextAsset", b"official skeleton"),
                               "test_spr.atlas": ("TextAsset", b"page.png\n")},
              env.original.resolve(): {"page.png": ("Texture2D", b"official pixels")},
              source.resolve(): {"test_spr.skel": ("TextAsset", b"mod skeleton"), "page.png": ("Texture2D", b"mod pixels")}}

    def extract(paths, skip=None):
        files = {}
        for path in paths:
            left = (skip or {}).get(path, frozenset())
            files.update({name: data for name, (kind, data) in assets[path].items() if kind not in left})
        return files

    env.services._game = GameAssets([env.game_dir], AssetCache(allowed=True, root=env.tmp / "art-cache"))
    monkeypatch.setattr("bmpt.gameassets.service.extract_spine", extract)
    options = rpc(env, "import.options")
    for types, skeleton in ((["Texture2D"], b"official skeleton"), (["Texture2D", "TextAsset"], b"mod skeleton")):
        rpc(env, "import.setOptions", **{**options, "types": types})
        entry, = analyze(env, [source])["entries"]
        assert entry["state"] == "update", entry
        app, prefix = preview_route(env, entry)
        revision = json.loads(call(app, prefix + "/manifest.json")[2])["revision"]
        # the import leaves the types its options do not name official, and so does its preview
        assert call(app, f"{prefix}/{revision}/skeleton.skel")[2] == skeleton
        assert call(app, f"{prefix}/{revision}/page.png")[2] == b"mod pixels"


def test_source_preview_uses_the_identified_core_for_renamed_uploads(env, monkeypatch):
    target = game_bundle(env.game_dir, "test_home_gl")
    source = game_bundle(env.tmp / "renamed", date="2025-01-01", payload=b"renamed lobby")
    monkeypatch.setattr("bmpt.library.identity.content_cores", lambda path: {"test_home_gl"})
    env.services._game = SimpleNamespace(spine_files_from=lambda paths: {
        "background.skel": b"background", "test_home_gl.skel": b"incoming lobby"})
    entry, = analyze(env, [source])["entries"]
    assert (entry["kind"], entry["core"], entry["files"][0]["target"]) == ("lobby", "test_home_gl", target.name)
    app, prefix = preview_route(env, entry)
    assert call(app, prefix + "/skeleton.skel")[2] == b"incoming lobby"


@pytest.mark.parametrize("change", ["modified", "deleted", "reanalyzed", "folder"])
def test_source_preview_expires_when_the_analyzed_context_changes(env, monkeypatch, change):
    source = make_mod(env.original, env.tmp / "input")
    env.services._game = SimpleNamespace(spine_files_from=lambda paths: {"test_spr.skel": b"source"})
    entry, = analyze(env, [source])["entries"]
    app, prefix = preview_route(env, entry)
    revision = json.loads(call(app, prefix + "/manifest.json")[2])["revision"]
    if change == "modified":
        source.write_bytes(source.read_bytes() + b"changed")
    elif change == "deleted":
        source.unlink()
    elif change == "reanalyzed":
        new_entry, = analyze(env, [source])["entries"]
        assert new_entry["id"] == entry["id"] and new_entry["preview"] != entry["preview"]
    else:
        monkeypatch.setattr(env.library, "current_pin", lambda: None)
    for name in ("manifest.json", "skeleton.skel", f"{revision}/skeleton.skel"):
        assert call(app, f"{prefix}/{name}")[0] == "404 Not Found"


def test_revisioned_source_preview_rejects_a_composition_changed_during_extraction(env):
    source = make_mod(env.original, env.tmp / "input")

    def extract(paths):
        source.write_bytes(source.read_bytes() + b"changed while extracting")
        return {"test_spr.skel": b"stale"}

    env.services._game = SimpleNamespace(spine_files_from=extract)
    entry, = analyze(env, [source])["entries"]
    app, prefix = preview_route(env, entry)
    revision = json.loads(call(app, prefix + "/manifest.json")[2])["revision"]
    assert call(app, f"{prefix}/{revision}/skeleton.skel")[0] == "404 Not Found"


def test_preview_can_use_a_self_contained_source_without_recoverable_originals(env, monkeypatch):
    source = make_mod(env.original, env.tmp / "input")
    env.services._game = SimpleNamespace(spine_files_from=lambda paths: {"test_spr.skel": paths[-1].read_bytes()})
    entry, = analyze(env, [source])["entries"]

    def missing(*args, **kwargs):
        raise ServiceError("original_unavailable")

    monkeypatch.setattr(env.library, "official_bundles", missing)
    app, prefix = preview_route(env, entry)
    assert call(app, prefix + "/skeleton.skel")[2] == source.read_bytes()
    assert env.services.batch_import.preview(entry["preview"]["key"])[1] == [source]


def test_source_preview_tokens_never_resolve_arbitrary_paths(env):
    for key in ("unknown", "..", str(env.original), "a" * 64):
        with pytest.raises(KeyError):
            env.services.batch_import.preview(key)


@pytest.mark.parametrize('problem', ['platform', 'source', 'conflict'])
def test_blocked_sources_still_serve_renderable_preview_assets(env, monkeypatch, problem):
    from bmpt.library import integrity
    if problem == "source":
        monkeypatch.setattr("bmpt.library.targets.by_assets", lambda *args, **kwargs: ("asset_path", []))
        monkeypatch.setattr("bmpt.library.targets.sharing_keys", lambda *args, **kwargs: [])

    source = game_bundle(env.tmp / 'input', date="2025-01-01", payload=b"old mod")
    inspect = integrity.inspect
    if problem == 'conflict':
        monkeypatch.setattr('bmpt.library.identity.content_cores',
                            lambda path: {'test_spr', 'other_spr'})
    else:
        monkeypatch.setattr(integrity, 'inspect', lambda path, **kwargs:
            integrity.Contents(platforms={'StandaloneOSX' if problem == 'platform' else 'UnknownPlatform'})
            if path == source else inspect(path, **kwargs))
    env.services._game = SimpleNamespace(spine_files_from=lambda paths: {
        'test_spr.skel': b'preview skeleton', 'test_spr.atlas': b'page.png\n', 'page.png': b'preview pixels'})
    entry, = analyze(env, [source])['entries']
    assert entry['state'] == 'blocked'
    app, prefix = preview_route(env, entry)
    assert call(app, prefix + '/skeleton.skel')[2] == b'preview skeleton'
    assert call(app, prefix + '/page.png')[2] == b'preview pixels'


def test_blocked_model_source_has_an_import_preview_route(batch_model_env, monkeypatch):
    env = batch_model_env
    source = make_mod(env.parts[0], env.tmp / 'input')
    monkeypatch.setattr(env.library, 'supports_kind', lambda kind: False)
    entry, = analyze(env, [source])['entries']
    assert entry['state'] == 'blocked' and entry['kind'] == 'model' and entry['preview']
    seen = []
    env.services._game = SimpleNamespace(model_glb=lambda core, **kwargs: seen.append((core, kwargs)) or b'model preview')
    response = env.services.asset('model/hoshino.glb', {'source': 'import:' + entry['preview']['key']})
    assert response.body == b'model preview'
    assert seen[0][0] == 'hoshino_original' and seen[0][1]['overrides'][-1] == source


def test_import_lobby_still_route_supplies_the_identified_game_camera(env):
    target = game_bundle(env.game_dir, "test_home_gl")
    source = make_mod(target, env.tmp / "incoming")
    entry, = analyze(env, [source])["entries"]
    info = {"view": {"x": -1480, "y": -832.5, "w": 2960, "h": 1665}, "main": "test_home",
            "layers": [{"name": "test_home", "x": 37.5, "y": -880, "scale": .72}]}
    requested = []
    env.services._game = SimpleNamespace(lobby_info=lambda core: requested.append(core) or info)
    app, prefix = preview_route(env, entry)
    status, _, manifest = call(app, prefix + "/manifest.json")
    revision = json.loads(manifest)["revision"]
    status, _, body = call(app, f"{prefix}/{revision}/framing.json")
    assert status == "200 OK"
    assert json.loads(body) == {"view": info["view"], "layer": info["layers"][0]}
    assert requested == ["test_home_gl"]


import shutil
import subprocess
from pathlib import Path

NODE = shutil.which("node")
TESTS = Path(__file__).parent / "js"

@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_missing_files_with_verified_prefabs_and_shipped_font(tmp_path):
    """Control assertions on official geometry and shipped WOFF2 metrics, in all six languages, without a browser."""
    import json
    import os

    from PIL import ImageFont

    from ba_modding_toolkit.searching import get_search_dirs
    from bmpt.gameassets import AssetCache, GameAssets

    root = os.environ.get("BAMT_TEST_GAME_DIR")
    if not root:
        pytest.skip("Set BAMT_TEST_GAME_DIR to a verified official development view")
    game = GameAssets(get_search_dirs(Path(root)), AssetCache(root=tmp_path / "cache", allowed=False))
    names = ["UINonEquipmentInventory", "UIMailBox", "UIMailBoxContent", "UIPopup_FilterSort",
             "UINonEquipmentInventoryListItem", "UIPopup_GachaCharList.Btn_Checkbox", "UICharacterCollection"]

    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)

    prefabs = {}
    for name in names:
        root_name, _, part = name.partition(".")
        tree = game.layout(root_name)
        prefabs[name] = next(node for node in walk(tree) if node["name"] == part) if part else tree

    def numeric_geometry(node):
        for widget in node.get("widgets", []):
            if "text" in widget:
                widget["text"] = ""
        for child in node.get("children", []):
            numeric_geometry(child)

    for prefab in prefabs.values():
        numeric_geometry(prefab)
    project = TESTS.parent.parent
    font = ImageFont.truetype(str(project / "src/bmpt/web/fonts/ResourceHanRoundedCN-Medium.woff2"), 1000)
    # app.css explicitly uses system Thai and Korean fonts: RHR has no glyphs for either script.
    # Measure the configured macOS faces rather than their .notdef boxes in the shipped font.
    fallback_fonts = {}
    for key, path in [("thai", "/System/Library/Fonts/Supplemental/Thonburi.ttc"),
                      ("hangul", "/System/Library/Fonts/AppleSDGothicNeo.ttc")]:
        if Path(path).is_file():
            fallback_fonts[key] = ImageFont.truetype(path, 1000)
    chars = set(chr(i) for i in range(32, 127))
    for file in [project / "src/bmpt/locales" / f"{locale}.json" for locale in ("en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH")]:
        chars.update(file.read_text(encoding="utf-8"))
    chars.update((project / "tests/js/import_badge_ink.test.mjs").read_text(encoding="utf-8"))
    chars.difference_update("\n\r\t")
    glyphs = {}
    for char in chars:
        face = fallback_fonts.get("thai", font) if "\u0e00" <= char <= "\u0e7f" else (
            fallback_fonts.get("hangul", font) if "\uac00" <= char <= "\ud7af" else font)
        _, top, _, bottom = face.getbbox(char, anchor="ls")
        glyphs[char] = [face.getlength(char), -top, bottom]
    # Preserve real pair kerning in Latin text. CJK advances do not kern in this font.
    kern = {}
    for left in map(chr, range(32, 127)):
        for right in map(chr, range(32, 127)):
            delta = font.getlength(left + right) - glyphs[left][0] - glyphs[right][0]
            if delta:
                kern[left + right] = delta
    ascent, descent = font.getmetrics()
    proof = tmp_path / "choose-fit.json"
    proof.write_text(json.dumps({"prefabs": prefabs, "atlases": game.atlases(),
                                 "font": {"glyphs": glyphs, "kern": kern, "ascent": ascent, "descent": descent}}), encoding="utf-8")
    environment = dict(os.environ, BAMT_IMPORT_FIT_PROOF=str(proof))
    result = subprocess.run([NODE, "--test", "--test-name-pattern=badge-line:|fallback:|clipping:|footer|filter|prompt|confirmation",
                             str(TESTS / "import_badge_ink.test.mjs")], env=environment,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
