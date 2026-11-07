"""Mods made for other builds of the game, replicated at test time from the official files of the development view,
through Import Mod's analysis, the preview's rule and the import (BL-113).

Every build keeps an asset path in exactly one bundle, while names, dates, grouping and CABs differ between builds. A
replica keeps a part's assets and asset paths and gives it another build's name, date, CABs and build target, and one
changed block per streamed page so its import is not "unchanged":

- Global mobile (Android 13, iOS 9): parts named by resource type (``-textassets-``, ``-textures-``) under another date;
- Japan (Android): Steam's names under another date, with Japan's package name among the bundle's dependencies;
- an older Steam release: Steam's names under older dates.

Sets per core: a typical mod (the skeleton, atlas and page parts), a complete one (every part; numbered builds), and
one part per row (incomplete, its siblings found in its folder). Expected: no row is refused for its targets, the
preview shows what the import writes, and the imports succeed.

Sample mode covers every portrait and lobby core with parts under several dates and a fixed set of others;
``BAMT_REPLICA_FULL=1`` covers every playable student. ``BAMT_REPLICA_CORES`` (comma-separated cores) narrows a run
while developing. Nothing made here is kept or committed.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

#: seconds an analysis or import job of every sampled core may take
JOB_SECONDS = 1800


def call(env, method, **params):
    answer = env.rpc.call(method, params)
    assert "error" not in answer, answer
    return answer["result"]


def finish(env, jid):
    assert env.services.jobs.wait(jid, JOB_SECONDS), f"job {jid} still running after {JOB_SECONDS} s"
    job = call(env, "jobs.get", id=jid)
    assert job["state"] == "done", job
    return job["result"]


def analyze(env, paths):
    return finish(env, call(env, "import.analyze", paths=list(map(str, paths)), recursive=False))


def run(env, entries, **options):
    options = {**call(env, "import.options"), "output": None, **options}
    return finish(env, call(env, "import.run", ids=[entry["id"] for entry in entries], options=options,
                            revision=env.services.batch_import._revision))["results"]

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
FIXED = {"hoshino", "shiroko", "aru", "hina", "yuuka", "mika", "nagisa", "cherino", "airi", "ch0164", "hihumi",
         "serika", "iori", "koharu"}
#: refusals that say the analysis found no official part for the files
TARGET_CODES = {"no_matching_assets", "conversion_names", "no_target", "game_file_not_found", "mod_target_unknown",
                "mod_target_ambiguous", "mod_target_mismatch", "source_unknown"}
IMPORTABLE = {"convert", "update", "ready"}
IMPORTED = {"imported", "updated"}
#: cores whose imports fail for a known reason outside the target rule, with its backlog key
KNOWN: dict[str, str] = {}


@pytest.fixture(scope="module", autouse=True)
def view():
    if not GAME_DIR.is_dir():
        pytest.skip("Set BAMT_TEST_GAME_DIR to a verified official development view")


BUILDS = {
    "global-android": {"target": "Android", "naming": "type", "shift": 1},
    "global-ios": {"target": "iOS", "naming": "type", "shift": 2},
    "japan-android": {"target": "Android", "naming": "number", "shift": 3, "marker": "com.YostarJP.BlueArchive"},
    "steam-older": {"target": "StandaloneWindows64", "naming": "number", "shift": 10},
}


#: the stream reference of streamed objects other than textures: (field, path key)
STREAMED = {"Mesh": ("m_StreamData", "path"), "AudioClip": ("m_Resource", "m_Source")}


def types_of(path: Path) -> set[str]:
    import UnityPy
    return {obj.type.name for obj in UnityPy.load(str(path)).objects}


def word_of(types: set[str]) -> str:
    """The resource-type word a mobile build names a part by."""
    for kind, word in (("TextAsset", "textassets"), ("Texture2D", "textures"), ("Material", "materials"),
                       ("MonoBehaviour", "monobehaviours"), ("AnimationClip", "animationclips")):
        if kind in types:
            return word
    return "assets"


def shifted(date: str, years: int) -> str:
    return f"{int(date[:4]) - years:04d}{date[4:]}"


def replica(official: Path, folder: Path, build: str, types: set[str]) -> Path:
    """The part as another build ships it (see the module's docstring)."""
    import UnityPy
    from UnityPy.enums import BuildTarget
    from UnityPy.streams import EndianBinaryReader
    from ba_modding_toolkit.naming import parse_filename

    spec = BUILDS[build]
    parsed = parse_filename(official.name)
    date = shifted(parsed.date, spec["shift"])
    if spec["naming"] == "type":
        name = f"{parsed.prefix}{word_of(types)}-{date}_assets_all_{parsed.crc}.bundle"
    else:
        name = official.name.replace(parsed.date, date, 1)  # the official name's own form, under another date
    path = folder / name
    bundle = UnityPy.load(str(official)).file
    renames = {key: "CAB-" + hashlib.md5((key + build).encode()).hexdigest()
               for key in bundle.files if key.startswith("CAB-") and not key.endswith(".resS")}
    target = getattr(BuildTarget, spec["target"])
    edits: dict[str, list[int]] = {}
    files = {}
    for key, inner in bundle.files.items():
        base = key.removesuffix(".resS")
        renamed = renames.get(base, base) + (".resS" if key.endswith(".resS") else "")
        if hasattr(inner, "objects"):
            inner.target_platform, inner._m_target_platform = target, int(target)
            for obj in inner.objects.values():
                if obj.type.name == "Texture2D":
                    data = obj.read()
                    if data.m_StreamData and data.m_StreamData.path:
                        edits.setdefault(data.m_StreamData.path.rsplit("/", 1)[-1], []).append(data.m_StreamData.offset)
                        for old, new in renames.items():
                            data.m_StreamData.path = data.m_StreamData.path.replace(old, new)
                        data.save()
                elif obj.type.name in STREAMED:
                    # meshes and clips name their stream file by the CAB as well
                    tree = obj.read_typetree()
                    stream, key = STREAMED[obj.type.name]
                    if (tree.get(stream) or {}).get(key):
                        for old, new in renames.items():
                            tree[stream][key] = tree[stream][key].replace(old, new)
                        obj.save_typetree(tree)
                elif obj.type.name == "AssetBundle" and spec.get("marker"):
                    tree = obj.read_typetree()
                    tree["m_Dependencies"] = [*tree["m_Dependencies"], spec["marker"]]
                    obj.save_typetree(tree)
        if hasattr(inner, "name"):
            inner.name = renamed
        files[renamed] = inner
    for key, offsets in edits.items():
        stream = renames.get(key.removesuffix(".resS"), key.removesuffix(".resS")) + ".resS"
        data = bytearray(files[stream].bytes)
        for offset in offsets:
            data[offset:offset + 16] = bytes(byte ^ 0x5A for byte in data[offset:offset + 16])
        changed = EndianBinaryReader(bytes(data))
        changed.flags = files[stream].flags
        files[stream] = changed
    bundle.files = files
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bundle.save())
    return path


def chosen_cores(library, catalog, pin) -> list[str]:
    from ba_modding_toolkit.naming import parse_filename

    wanted = {core.strip().lower() for core in os.environ.get("BAMT_REPLICA_CORES", "").split(",") if core.strip()}
    full = os.environ.get("BAMT_REPLICA_FULL") == "1"
    cores = []
    for family in catalog.families():
        for variant in family.variants:
            variant = catalog.variant(variant.key)
            if not variant.playable:
                continue
            for core in (variant.spr, variant.lobby):
                if not core or core.lower() in cores:
                    continue
                core = core.lower()
                parts = library._spine_parts(core, pin)
                dated = len({parse_filename(path.name).date for path in parts}) > 1
                if wanted and core not in wanted:
                    continue
                if wanted or full or dated or variant.key in FIXED:
                    cores.append(core)
    return cores


@pytest.fixture(scope="module")
def roster(tmp_path_factory):
    from bmpt.catalog import Catalog
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.cabs import CabIndex
    from bmpt.library import ModLibrary, integrity
    from bmpt.library.storage import atomic_clone
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services
    from ba_modding_toolkit.searching import get_search_dirs

    tmp = tmp_path_factory.mktemp("replica")
    directories = get_search_dirs(GAME_DIR)
    game = GameAssets(directories, AssetCache(allowed=False, root=tmp / "probe-cache"))
    settings = Settings(tmp / "probe.toml")
    settings.set("game_resource_dir", str(GAME_DIR))
    catalog = Catalog(game, None, server="global")
    probe = ModLibrary(tmp / "probe", catalog, game, settings)
    pin = probe.current_pin()
    cores = chosen_cores(probe, catalog, pin)
    official = {core: probe._spine_parts(core, pin) for core in cores}
    # the roster knows a student from the cores the install holds: clone the student's other core as well
    companions = {other.lower() for core in cores if (variant := catalog.variant_for_asset_core(core))
                  for other in (variant.spr, variant.lobby) if other}
    inspection = integrity.Inspection()
    every = [path for core in sorted(set(cores) | companions) for path in probe._spine_parts(core, pin)]
    references = set().union(*(inspection.inspect(path).references for path in every))
    suppliers = CabIndex(directories).resolve(references, every[0]) if references else {}
    clones = tmp / "game"
    clones.mkdir()
    for path in set(every) | {Path(path) for path in suppliers.values()}:
        assert atomic_clone(path, clones / path.name)
        (clones / path.name).chmod(0o644)
    settings = Settings(tmp / "config.toml")
    settings.set("game_resource_dir", str(clones))
    settings.set("enable_crc_correction", "false")
    settings.set("compression_method", "none")
    game = GameAssets([clones], AssetCache(allowed=False, root=tmp / "cache"))
    catalog = Catalog(game, None, server="global")
    library = ModLibrary(tmp / "library", catalog, game, settings)
    services = Services(settings, tmp / "app", cache=object(), game=game, catalog=catalog, library=library)
    services.game_found = lambda: False
    parts = {core: [clones / path.name for path in paths] for core, paths in official.items()}
    types = {path: types_of(path) for paths in parts.values() for path in paths}
    try:
        yield SimpleNamespace(services=services, rpc=build_api(services), library=library, cores=cores, parts=parts,
                              types=types, tmp=tmp, pin=library.current_pin())
    finally:
        services.close()


def typical(parts: list[Path], types: dict[Path, set[str]]) -> list[Path]:
    return [path for path in parts if types[path] & {"TextAsset", "Texture2D"}]


def make_set(env, build: str, kind: str) -> dict[str, list[Path]]:
    """Per core, the replica files of one set, each core in its own folder; cores whose replica names collide (two
    parts of one date named by the same resource type) are left out."""
    sets = {}
    for core in env.cores:
        chosen = env.parts[core] if kind == "complete" else typical(env.parts[core], env.types)
        folder = env.tmp / "mods" / build / kind / core
        paths = [replica(path, folder, build, env.types[path]) for path in chosen]
        if len({path.name for path in paths}) == len(paths) and paths:
            sets[core] = paths
    return sets


#: what an import preview reads of a mod's files (``gameassets.spine.extract_spine``): materials and spine-unity's
#: assets always come from the official files
SHOWN = {"TextAsset", "Texture2D"}


def check_rows(env, rows: list[dict], sets: dict[str, list[Path]], label: str, kind: str) -> list[str]:
    """Each row analyses as importable, and its preview shows what its import writes: the preview drops only parts
    the import resolved, every one of them for whole copies of parts, and the parts it drops that the import leaves
    official (no asset of the import's types to transfer) hold nothing the preview shows."""
    problems = []
    by_path = {str(path): core for core, paths in sets.items() for path in paths}
    batch = env.services.batch_import
    seen = set()
    for row in rows:
        core = by_path.get(row["paths"][0])
        seen.add(core)
        evidence = {key: row.get(key) for key in ("reason", "error", "resolution", "attempts")}
        if row["state"] not in IMPORTABLE:
            code = (row.get("error") or {}).get("code") or row.get("reason")
            refusal = "target" if code in TARGET_CODES else "other"
            problems.append(f"{label}/{core}: {row['state']} ({refusal}) {evidence}")
            continue
        candidate = batch._latest[row["id"]]
        live = env.library._spine_parts(core, env.pin)
        resolved = set(env.library._resolve(candidate.files, live, env.pin).updated)
        dropped = env.library._replaced_parts(candidate.files, live, env.pin)
        written = set(candidate.targets)
        names = lambda paths: sorted(path.name for path in paths)  # noqa: E731
        shown = {part for part in dropped - written if env.types[part] & SHOWN}
        if not dropped <= resolved or not written <= resolved or (kind == "complete" and dropped != resolved):
            problems.append(f"{label}/{core}: resolved {names(resolved)}, preview drops {names(dropped)}, "
                            f"import writes {names(written)}")
        elif shown or (kind == "typical" and dropped != written):
            problems.append(f"{label}/{core}: preview drops {names(dropped)}, import writes {names(written)}")
    for core in set(sets) - seen:
        problems.append(f"{label}/{core}: no row")
    return problems


@pytest.mark.parametrize("build", list(BUILDS))
def test_replicas_of_other_builds_analyse_preview_and_import(roster, build):
    started = time.perf_counter()
    problems = []
    kinds = ("typical", "complete") if BUILDS[build]["naming"] == "number" else ("typical",)
    for kind in kinds:
        sets = make_set(roster, build, kind)
        assert sets, f"{build}/{kind}: no core replicated"
        result = analyze(roster, [path for paths in sets.values() for path in paths])
        rows = result["entries"]
        problems += check_rows(roster, rows, sets, f"{build}/{kind}", kind)
        importable = [row for row in rows if row["state"] in IMPORTABLE]
        if importable:
            cores = {row["id"]: row["core"] for row in importable}
            for outcome in run(roster, importable, enableAfterImport=False):
                if cores.get(outcome["id"]) in KNOWN:
                    continue
                if outcome.get("outcome") not in IMPORTED or not outcome.get("libraryId"):
                    problems.append(f"{build}/{kind}/{cores.get(outcome['id'])}: import {outcome}")
            for mod in roster.library.mods():
                roster.library.remove(mod.id)
    print(f"Replica {build}: cores={len(roster.cores)}; seconds={time.perf_counter() - started:.1f}", flush=True)
    assert not problems, "\n".join(problems[:40])


def test_one_replica_part_per_row_finds_its_siblings(roster):
    build = "global-android"
    sets = {core: paths for core, paths in make_set(roster, build, "typical").items() if len(paths) > 1}
    problems = []
    for index in range(max((len(paths) for paths in sets.values()), default=0)):
        chosen = {core: paths[index] for core, paths in sets.items() if index < len(paths)}
        rows = analyze(roster, list(chosen.values()))["entries"]
        for row in rows:
            core = next(core for core, path in chosen.items() if str(path) == row["paths"][0])
            siblings = sorted(str(path) for path in sets[core] if path != chosen[core])
            found = sorted(found["path"] for found in (row.get("missing") or {}).get("found", []))
            if row["state"] != "incomplete" or found != siblings:
                problems.append(f"{core}/{index}: {row['state']} {row.get('reason')} found={found}")
    assert not problems, "\n".join(problems[:40])
