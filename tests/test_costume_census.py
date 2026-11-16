"""Every Switch 3D Model pair of the official files, checked to its end the way the App offers it: its
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
# own bundles), and a slot with no prefab to build on (a model slot of the variant's own bundles: Arona's, Ayame's
# story forms'; mods apply there, costumes do not). A costume stretching past the limit is still offered, and
# a shader the game does not load with the slot's prefab is carried with the costume.
RULE_REFUSALS: set[str] = {"static model", "no prefab"}
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
        row["past_limit"] = bool(verdict and verdict.get("preview"))
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
    offered = sum(1 for r in pairs if r["build"]["ok"] and r["export"]["ok"])
    past_limit = sum(1 for r in pairs if r["build"]["ok"] and r["export"]["ok"] and r["past_limit"])
    checks = Counter(r["build"]["check"] for r in pairs if r["build"].get("code") == "source_unsupported")
    record_property("costume_census", json.dumps({"models": len(own), "pairs": len(pairs), "offered": offered,
                                                  "past_limit": past_limit, "refused": dict(checks)}))
    assert not failed_own, failed_own
    assert not errors, errors
    assert not unknown, unknown
    assert not unexported, unexported
    assert own and pairs and offered


# --- worn items ----------------------------------------------------------------------------------------------------

WORN = Path(__file__).parent / "fixtures" / "worn_items.json"


def _open_models(view: str) -> None:
    import re
    from collections import defaultdict
    from bmpt.gameassets.discovery import cab_names

    paths = list(Path(view).rglob("*.bundle"))
    index, models = {}, defaultdict(list)
    for path in paths:
        if re.search(r"characters|-_mx-npcs-|^character-|-_mx-shaders-|-_mx-program-animators-|^field-|animatorbase|"
                     r"prolog|-_mx-effectsfreezed-", path.name):
            for cab in cab_names(path):
                index[cab] = path
        if (match := re.match(r"character-(.+?)-_mx", path.name)):
            models[match.group(1)].append(path)
    _state.update(index=index, models=models)


def _worn(dev: str) -> tuple[str, list[str] | None]:
    """A model's worn items as "<renderer>/<item's top node>", " (packed)" after one the costume encloses (not
    carried); None for a model without a prefab of its own bundles."""
    from bmpt.gameassets import model_adapt as ma, model_props as mp
    from bmpt.gameassets.errors import AssetNotFound
    from bmpt.gameassets.model import model_root

    files = _state["models"].get(dev, [])
    with ma.BundleGraph(_state["index"]) as graph:
        try:
            root, _, _ = model_root(graph, files, dev)
        except AssetNotFound:
            return dev, None
        s = ma.Skeleton.of(graph, root)
        clips = ma.prefab_clips(graph, s, ma.own_prefabs(ma._model_prefabs(graph, files), dev))
        idle = next(iter(s.poses(ma.pick(clips, ma.IDLE_CLIPS)[:1], frames=1)), None)
        worn = mp.worn_items(s, ma.roles(s, idle), clips, idle)
        return dev, sorted(f"{s.nodes[i.part.node].name}/{s.nodes[i.top].name}{'' if i.carry else ' (packed)'}"
                           for i in worn.items)


@official
def test_official_the_worn_items_of_every_student_model_are_pinned(tmp_path):
    """The worn items (model_props) of every student model with a 3D model, against the pinned list: a game update or
    a change of the rule shows here as a diff to look at, then to pin."""
    _open(str(GAME_DIR), str(tmp_path))
    devs = sorted({v.model.lower() for f in _state["catalog"].families() for v in f.variants if v.model})
    with ProcessPoolExecutor(4, initializer=_open_models, initargs=(str(GAME_DIR),)) as pool:
        found = {dev: items for dev, items in pool.map(_worn, devs, chunksize=4) if items}
    pinned = json.loads(WORN.read_text())["worn"]
    devs = sorted({*pinned, *found})
    gained = {dev: sorted(set(found.get(dev, [])) - set(pinned.get(dev, []))) for dev in devs}
    lost = {dev: sorted(set(pinned.get(dev, [])) - set(found.get(dev, []))) for dev in devs}
    assert ({k: v for k, v in gained.items() if v}, {k: v for k, v in lost.items() if v}) == ({}, {}), (
        "worn items changed: check them, then pin the new list in tests/fixtures/worn_items.json")
