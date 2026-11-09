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
        costume_verdict=lambda target, other: None,
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


class CostumeCatalog:
    """Hina's family: her model, the Dress's, the Pajamas' field-mode model; Momoi elsewhere."""
    variants = {"hina": ("hina_original", None, "hina"), "ch0230": ("ch0230", None, "hina"),
                "np0009": (None, "np0009", "hina"), "momoi": ("momoi_original", None, "momoi")}

    def variant(self, key):
        if key not in self.variants:
            raise ServiceError("catalog_variant_not_found", key=key)
        model, field, family = self.variants[key]
        return SimpleNamespace(key=key, model=model, field_model=field, family=family, spr=None)


def _costume_services(tmp_path, outcomes, export_error=None):
    """Services over a library whose costume previews give ``outcomes[other]`` (parts, or an error to raise)."""
    saved, built, exported = {}, [], []
    part = tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_2.bundle"

    def preview(target, other):
        built.append(other)
        outcome = outcomes[other]
        if isinstance(outcome, ServiceError):
            if outcome.code == "source_unsupported":  # the library saves the rule's refusals itself
                saved[other] = {"ok": False, "reason": outcome.params["source"], "detail": {}}
            raise outcome
        return [part]

    def record(target, other, ok, reason=None, detail=None):
        saved[other] = {"ok": ok, "reason": reason, "detail": dict(detail or {})}
        return saved[other]

    def glb(core, overrides=(), reads=None):
        exported.append((core, list(overrides)))
        if export_error is not None:
            raise export_error
        return b"glTF"

    game = RouteGame()
    game.model_glb = glb
    library = SimpleNamespace(costume_verdict=lambda target, other: saved.get(other), costume_preview=preview,
                              record_costume_verdict=record, preview_reads=lambda policy: policy, mods=lambda: [],
                              assigned_targets=lambda: set())
    s = Services(Settings(tmp_path / "config.toml"), tmp_path, state=UiState(tmp_path / "webui.json"),
                 cache=object(), game=game, catalog=CostumeCatalog(), library=library)
    return s, saved, built, exported


def test_costume_check_saves_what_holds_for_these_files_and_retries_a_refusal_of_the_moment(tmp_path):
    """BL-122: a pair is judged by building and exporting it; the verdict is saved, except a refusal of the moment."""
    outcomes = {"np0009": "parts", "ch0230": ServiceError("source_unsupported", source="stretch"),
                "busy": ServiceError("original_unavailable"), "bug": ServiceError("library_operation_failed")}
    s, saved, built, exported = _costume_services(tmp_path, outcomes)
    try:
        assert s.costume_check("hina", "np0009")["ok"] is True
        assert exported == [("hina_original", [tmp_path / "character-hina_original-_mxload-2026-01-01_assets_all_2.bundle"])]
        assert s.costume_check("hina", "np0009")["ok"] is True and built == ["np0009"]  # saved: not built again
        assert s.costume_check("hina", "ch0230") == {"ok": False, "reason": "stretch", "detail": {}}
        assert s.costume_check("hina", "busy") == {"ok": False, "reason": "original_unavailable", "transient": True}
        assert "busy" not in saved
        assert s.costume_check("hina", "bug")["ok"] is False and saved["bug"]["reason"] == "library_operation_failed"
    finally:
        s.close()
    s, saved, _, _ = _costume_services(tmp_path, {"np0009": "parts"}, export_error=RuntimeError("no mesh"))
    try:
        assert s.costume_check("hina", "np0009") == {"ok": False, "reason": "export", "detail": {"error": "RuntimeError"}}
    finally:
        s.close()


def test_a_costume_past_the_stretch_limit_is_shown_for_a_preview_only(tmp_path):
    outcomes = {"np0009": "parts"}
    s, saved, built, _ = _costume_services(tmp_path, outcomes)
    preview = s.library.costume_preview

    def unfit(target, other):  # the library saves such a pair as shown only while it builds the preview
        saved[other] = {"ok": False, "preview": True, "reason": "stretch", "detail": {}}
        return preview(target, other)

    s.library.costume_preview = unfit
    api = build_api(s)
    try:
        job = api.call("model.checkCostumes", {"variant": "hina", "others": ["np0009"]})["result"]
        assert s.jobs.wait(job, 10) and s.jobs.get(job).result == {"np0009": "preview"}
        assert api.call("model.costumes", {"variant": "hina", "others": ["np0009"]})["result"] == {"np0009": "preview"}
        assert s.asset("model/hina.glb", {"source": "variant:np0009"}).body == b"glTF"  # its preview draws
    finally:
        s.close()


def test_costume_rpcs_offer_only_pairs_checked_to_their_end(tmp_path):
    outcomes = {"np0009": "parts", "ch0230": ServiceError("source_unsupported", source="shader outside the always-loaded bundles")}
    s, saved, built, _ = _costume_services(tmp_path, outcomes)
    api = build_api(s)
    try:
        others = ["ch0230", "np0009", "momoi", "hina"]
        assert api.call("model.costumes", {"variant": "hina", "others": others})["result"] == {
            "ch0230": None, "np0009": None, "momoi": "refused", "hina": "refused"}  # another family, itself
        job = api.call("model.checkCostumes", {"variant": "hina", "others": ["ch0230", "np0009", "momoi"]})["result"]
        assert s.jobs.wait(job, 10)
        assert s.jobs.get(job).result == {"ch0230": "refused", "np0009": "ok", "momoi": "refused"}
        assert api.call("model.costumes", {"variant": "hina", "others": others})["result"] == {
            "ch0230": "refused", "np0009": "ok", "momoi": "refused", "hina": "refused"}
        assert sorted(built) == ["ch0230", "np0009"]
        # a refused pair is no preview source, and its preview is not built again
        import pytest
        with pytest.raises(KeyError):
            s.asset("model/hina.glb", {"source": "variant:ch0230"})
        assert sorted(built) == ["ch0230", "np0009"]
        for bad in ({"variant": "hina", "others": "np0009"}, {"variant": "hina", "others": ["x"] * 65},
                    {"variant": "", "others": []}):
            assert api.call("model.costumes", bad)["error"]["code"] == "bad_params"
    finally:
        s.close()


def test_optimize_physics_runs_as_a_library_job_on_the_official_model_or_a_costume(tmp_path):
    """BL-132: model.optimize hands the source and its shown name to the library, as a library writer job."""
    s, _, _, _ = _costume_services(tmp_path, {})
    asked = []

    def optimize(variant, source, name, pin=None):
        asked.append((variant, source, name, pin))
        return {"unneeded": True} if source == "official" else {"mod": "copy"}

    s.library.optimize_costume = optimize
    s.library.current_pin = lambda: "pin"
    s.library.judge_unfinished = lambda: None  # what a library writer job's end asks
    s.library.take_pruned_cores = lambda: set()
    api = build_api(s)
    try:
        for source, result in (("official", {"unneeded": True}), ("variant:np0009", {"mod": "copy"})):
            job = api.call("model.optimize", {"variant": "hina", "source": source, "name": "Pajamas"})["result"]
            assert s.jobs.wait(job, 10) and s.jobs.get(job).result == result
        assert asked == [("hina", "official", "Pajamas", "pin"), ("hina", "variant:np0009", "Pajamas", "pin")]
        for bad in ({"variant": "hina", "source": "", "name": ""}, {"variant": "hina", "source": "official", "name": "x" * 201},
                    {"variant": "", "source": "official", "name": ""}):
            assert api.call("model.optimize", bad)["error"]["code"] == "bad_params"
        # another family's costume, a mod: not a source of Optimize Physics
        for source in ("variant:momoi", "mod:abc"):
            assert api.call("model.optimize", {"variant": "hina", "source": source, "name": ""})["error"]["code"] == "source_unsupported"
        assert len(asked) == 2
    finally:
        s.close()
