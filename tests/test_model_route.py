"""The /asset/model route: a variant's chibi, exported in memory and never cacheable."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from bmpt.config import Settings
from bmpt.errors import ServiceError
from bmpt.webui.api import build_api
from bmpt.webui.server import WebApp
from bmpt.webui.services import Services
from bmpt.webui.state import UiState


class RouteCatalog:
    def variant(self, key):
        if key not in ("ch0242", "ch0252"):
            raise ServiceError("catalog_variant_not_found", key=key)
        return SimpleNamespace(key=key, model="ch0242" if key == "ch0242" else None)


class RouteGame:
    resource_version = "rv"

    def __init__(self):
        self.asked = []

    def model_glb(self, core):
        self.asked.append(core)
        return b"glTF model"


def _get(app, path):
    seen = {}
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "", "HTTP_X_BAMT_TOKEN": "tok",
               "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b"")}
    body = b"".join(app(environ, lambda status, headers: seen.update(status=status, headers=dict(headers))))
    return seen["status"], seen["headers"], body


def test_model_route_is_never_cacheable(tmp_path):
    game = RouteGame()
    s = Services(Settings(tmp_path / "config.toml"), tmp_path, state=UiState(tmp_path / "webui.json"),
                 cache=object(), game=game, catalog=RouteCatalog(), library=object())
    s.state.set_consent(True)
    app = WebApp(build_api(s), s.events, assets=s.asset, token="tok", consent=lambda: s.state.consent)
    try:
        status, headers, body = _get(app, "/asset/model/ch0242.glb")
        assert status == "200 OK" and body == b"glTF model" and game.asked == ["ch0242"]
        assert headers["Content-Type"] == "model/gltf-binary" and headers["Cache-Control"] == "no-store"
        assert _get(app, "/asset/model/ch0252.glb")[0] == "404 Not Found"   # no model in this install
        assert _get(app, "/asset/model/missing.glb")[0] == "404 Not Found"
        assert _get(app, "/asset/model/ch0242.png")[0] == "404 Not Found"
    finally:
        s.close()


def test_model_route_uses_verified_official_or_own_mod_sources(tmp_path):
    # each source reads the files outside its own parts by the library's rule, never as the game folder holds them
    official = [tmp_path / 'official.bundle']
    mod_parts = [tmp_path / 'assets-_mx-characters-ch0242-_mxdependency-2026-01-01_004_assets_all_1.bundle']
    calls = []
    game = RouteGame()
    game.model_glb = lambda core, overrides=(), reads=None: calls.append((core, list(overrides), reads)) or b'glTF'
    library = SimpleNamespace(
        official_bundles=lambda core, **kwargs: official,
        preview_bundles=lambda mod_id: mod_parts,
        preview_reads=lambda policy: 'reads:' + policy,
        mods=lambda: [SimpleNamespace(id='own', kind='model', target='ch0242'),
                      SimpleNamespace(id='foreign', kind='model', target='ch0252'),
                      SimpleNamespace(id='portrait', kind='spr', target='ch0242')],
        costume_preview=lambda target, other: costume(target, other),
    )
    costume_parts = [tmp_path / 'character-ch0242-_mxload-2026-01-01_assets_all_2.bundle']

    def costume(target, other):  # another variant's costume on the slot (BL-117); the library refuses the rest
        if (target, other) != ('ch0242', 'ch0242_swimsuit'):
            raise ServiceError('source_unsupported', source=f'variant:{other}')
        return costume_parts
    s = Services(Settings(tmp_path / 'config.toml'), tmp_path, state=UiState(tmp_path / 'webui.json'),
                 cache=object(), game=game, catalog=RouteCatalog(), library=library)
    try:
        assert s.asset('model/ch0242.glb', {'source': 'official'}).body == b'glTF'
        assert calls[-1] == ('ch0242', official, 'reads:official')
        assert s.asset('model/ch0242.glb', {'source': 'mod:own'}).body == b'glTF'
        assert calls[-1] == ('ch0242', mod_parts, 'reads:configured')
        assert s.asset('model/ch0242.glb', {'source': 'variant:ch0242_swimsuit'}).body == b'glTF'
        assert calls[-1] == ('ch0242', costume_parts, 'reads:official')
        import pytest
        for source in ['mod:foreign', 'mod:portrait', 'mod:missing', '../elsewhere']:
            with pytest.raises(KeyError):
                s.asset('model/ch0242.glb', {'source': source})
        with pytest.raises(ServiceError, match='source_unsupported'):
            s.asset('model/ch0242.glb', {'source': 'variant:ch0252'})
    finally:
        s.close()


def test_indexed_npc_model_can_preview_without_a_student_entry(tmp_path):
    import pytest
    game = RouteGame()
    game.model_cores = lambda: {"en0002"}
    calls = []
    game.model_glb = lambda core, overrides=(), reads=None: calls.append((core, list(overrides), reads)) or b"glTF npc"
    sources = [tmp_path / 'assets-_mx-npcs-en0002-_mxdependency-2026-01-01_004_assets_all_1.bundle']
    s = Services(Settings(tmp_path / "config.toml"), tmp_path, state=UiState(tmp_path / "webui.json"),
                 cache=object(), game=game, catalog=RouteCatalog(),
                 library=SimpleNamespace(mods=lambda: [], preview_reads=lambda policy: 'reads:' + policy))
    s.batch_import = SimpleNamespace(preview=lambda key: ("en0002", sources))
    try:
        assert s.asset("model/en0002.glb", {}).body == b"glTF npc"
        assert calls[-1] == ("en0002", [], None)  # live: the game folder as it is
        assert s.asset("model/en0002.glb", {"source": "import:verified-token"}).body == b"glTF npc"
        assert calls[-1] == ("en0002", sources, "reads:configured")
        with pytest.raises(KeyError):
            s.asset("model/unindexed.glb", {"source": "import:verified-token"})
    finally:
        s.close()


def test_prefab_overrides_keep_every_dated_prefab_bundle_of_a_slot():
    """BL-122: Yuuka's prefabs span two dated bundles of one slot; the official copies of both stand in for each
    one, and a mod of another release for the newest only."""
    from bmpt.gameassets.service import prefab_overrides

    old = Path("character-yuuka_original-_mxload-2024-11-18_assets_all_3807259161.bundle")
    new = Path("character-yuuka_original-_mxload-2025-07-25_assets_all_3996351917.bundle")
    official = [Path("/official") / old.name, Path("/official") / new.name]
    assert prefab_overrides([old, new], official) == official
    assert prefab_overrides([old, new], official[::-1]) == official
    # an unfixed CRC: the same slot and date
    unfixed = Path("/mod/character-yuuka_original-_mxload-2025-07-25_assets_all_1.bundle")
    assert prefab_overrides([old, new], [unfixed]) == [None, unfixed]
    # another release: the newest bundle of the slot
    other = Path("/mod/character-yuuka_original-_mxload-2023-01-01_assets_all_2.bundle")
    assert prefab_overrides([old, new], [other]) == [None, other]
    assert prefab_overrides([new], [other]) == [other]
    assert prefab_overrides([old, new], []) == [None, None]
