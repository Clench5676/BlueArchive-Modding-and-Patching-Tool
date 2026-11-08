"""Every Switch 3D Model pair of the official files, checked to its end the way the App offers it (BL-122): its
preview built (library.costume_preview) and exported, or refused by the rule with a named check; and every model's own
official preview exports (the cards' stills). Slow (minutes, three worker processes): fingerprint-gated by the dev tool.
"""

import json
import os
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
official = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")

# the checks the rule may refuse a pair on today: an unskinned source (Kei's Decagrammaton body, a static mesh in her
# own bundles). A costume stretching past the limit is built for a preview only (shown, never applied), and a shader
# the game does not load with the slot's prefab is carried with the costume.
RULE_REFUSALS: set[str] = {"static model"}
WORKERS = 3

_state = {}


def _open(view: str, work: str) -> None:
    from ba_modding_toolkit.searching import get_search_dirs
    from bmpt.catalog import Catalog
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.library import ModLibrary

    root = Path(work) / str(os.getpid())
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(root / "config.toml")
    settings.set("game_resource_dir", view)
    game = GameAssets(get_search_dirs(Path(view)), AssetCache(allowed=False, root=root / "cache"))
    catalog = Catalog(game, None, server="global")
    _state.update(game=game, catalog=catalog, library=ModLibrary(root / "library", catalog, game, settings))


def _jobs() -> list[tuple[str, str, str | None]]:
    catalog = _state["catalog"]
    jobs = []
    for family in catalog.families():
        models = [v for v in family.variants if v.model or getattr(v, "field_model", None)]
        jobs += [("own", v.key, None) for v in models]
        for target in models:
            if target.model:
                jobs += [("pair", target.key, source.key) for source in models if source.key != target.key
                         and (source.model or "").lower() != target.model.lower()]
    return jobs


def _outcome(fn) -> dict:
    from bmpt.errors import ServiceError

    started = time.perf_counter()
    try:
        fn()
        return {"ok": True}
    except ServiceError as error:
        return {"ok": False, "code": error.code, "check": error.params.get("source"), "s": time.perf_counter() - started}
    except Exception as error:  # a program error: the census names it
        return {"ok": False, "code": "exception", "error": f"{type(error).__name__}: {error}",
                "trace": traceback.format_exc()[-800:]}


def _run(job: tuple[str, str, str | None]) -> dict:
    kind, key, other = job
    game, library, catalog = _state["game"], _state["library"], _state["catalog"]
    variant = catalog.variant(key)
    if kind == "own":
        core = variant.model or variant.field_model
        if variant.model:
            fn = lambda: game.model_glb(core, overrides=library.official_bundles(core, current_fallback=True),
                                        reads=library.preview_reads("official"))
        else:
            fn = lambda: game.model_glb(core)
        return {"job": job, **_outcome(fn)}
    built = {}
    row = {"job": job, "build": _outcome(lambda: built.update(parts=library.costume_preview(key, other)))}
    if row["build"]["ok"]:
        row["export"] = _outcome(lambda: game.model_glb(variant.model, overrides=built["parts"],
                                                        reads=library.preview_reads("official")))
        verdict = library.costume_verdict(key, other)
        row["preview_only"] = bool(verdict and verdict.get("preview"))
    return row


def _census(work: Path) -> list[dict]:
    _open(str(GAME_DIR), str(work))
    jobs = _jobs()
    with ProcessPoolExecutor(WORKERS, initializer=_open, initargs=(str(GAME_DIR), str(work))) as pool:
        return list(pool.map(_run, jobs, chunksize=4))


@official
def test_official_every_costume_pair_builds_or_is_refused_by_the_rule(tmp_path, record_property):
    rows = _census(tmp_path)
    own = [r for r in rows if r["job"][0] == "own"]
    pairs = [r for r in rows if r["job"][0] == "pair"]
    failed_own = [(r["job"][1], r.get("code"), r.get("error")) for r in own if not r["ok"]]
    errors = [(r["job"], r["build"].get("error") or r["build"].get("code"), r["build"].get("trace"))
              for r in pairs if not r["build"]["ok"] and r["build"]["code"] != "source_unsupported"]
    unknown = [(r["job"], r["build"]["check"]) for r in pairs
               if r["build"].get("code") == "source_unsupported" and r["build"]["check"] not in RULE_REFUSALS]
    unexported = [(r["job"], r["export"]) for r in pairs if r["build"]["ok"] and not r["export"]["ok"]]
    offered = sum(1 for r in pairs if r["build"]["ok"] and r["export"]["ok"] and not r["preview_only"])
    shown = sum(1 for r in pairs if r["build"]["ok"] and r["export"]["ok"] and r["preview_only"])
    checks = Counter(r["build"]["check"] for r in pairs if r["build"].get("code") == "source_unsupported")
    record_property("costume_census", json.dumps({"models": len(own), "pairs": len(pairs), "offered": offered,
                                                  "preview_only": shown, "refused": dict(checks)}))
    assert not failed_own, failed_own
    assert not errors, errors
    assert not unknown, unknown
    assert not unexported, unexported
    assert own and pairs and offered
