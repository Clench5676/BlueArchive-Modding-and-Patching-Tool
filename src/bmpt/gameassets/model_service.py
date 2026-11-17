"""The 3D chibi models and memorial lobbies of the game asset service, a mixin of ``GameAssets``."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from ..core import spans
from ..core.errors import ServiceError
from bmpt.engine import parse_filename
from ..unity.texture_guard import texture_image
from .cache import pack_preview_files, unpack_preview_files
from . import lobby
from .discovery import (
    cab_names,
    group_bundles,
    is_lobby_prefab,
    is_model_dependency,
    may_hold_model_root,
)
from .errors import AssetNotFound
from .preview_reads import PreviewReads
from .spine import png_bytes, spine_stamps
from .unity import open_bundle, reference
from .service_base import _MODEL_LOCK, _SCHEMA, _FxResolver


logger = logging.getLogger("bmpt.gameassets.service")  # the game asset service logs under one name

STORY_CLIP = re.compile(r"^((?:np|ch)\d{4})_", re.IGNORECASE)  # a story cutscene's clip named after its character
# a clip asked for by name (model_glb's clip): the characters the official clip names use ("CH0271_Formation Pickup");
# the name is only compared with the model's clip names, never used as a path
CLIP_NAME = re.compile(r"[A-Za-z0-9_ ]{1,128}")


def _stamp(path: str | Path) -> tuple[int, int, int] | None:
    """A file's size, modification and change times, or None when it cannot be read."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def prefab_overrides(bundles: list[Path], overrides: list[Path]) -> list[Path | None]:
    """Match one optional override to each prefab bundle using library.targets replacement rules.

    Try the
    override of the same name, else of the same slot (prefix and resource type) and date; an override of another
    release stands in for its slot's newest prefab bundle that has none. A model whose prefabs span dated bundles of
    one slot (2024-11-18 and 2025-07-25) keeps each of them: one override never stands in for two bundles.

    Args:
        bundles: Official prefab parts, retaining coexisting dates in their input order.
        overrides: Mod parts matched by exact name, then slot/date, then an unclaimed newest slot part.

    Returns:
        One replacement path or None per input bundle.
    """

    def slot(path: Path) -> tuple:
        parsed = parse_filename(path.name)
        return parsed.prefix, parsed.res_type

    by_name = {path.name: path for path in overrides}
    by_date = {(*slot(path), parse_filename(path.name).date): path for path in overrides}
    out: list[Path | None] = [
        by_name.get(path.name) or by_date.get((*slot(path), parse_filename(path.name).date)) for path in bundles
    ]
    used = {path for path in out if path is not None}
    by_slot: dict[tuple, list[Path]] = {}
    for path in overrides:
        if path not in used:
            by_slot.setdefault(slot(path), []).append(path)
    for key, rest in by_slot.items():
        free = [i for i, path in enumerate(bundles) if slot(path) == key and out[i] is None]
        if len(rest) == 1 and free:
            out[max(free, key=lambda i: parse_filename(bundles[i].name).date)] = rest[0]
    return out


class ModelsMixin:
    """The 3D chibi models, story clips and memorial lobbies of ``GameAssets``."""

    def _model_index(self) -> dict[str, list[str]]:
        return self._index(
            "model-cores",
            lambda: {
                core: [str(path) for path in paths]
                for core, paths in group_bundles(self._paths(), "character").items()
                if re.fullmatch(r"[a-z0-9_]+", core)
            },
            disk=False,
        )

    def model_cores(self) -> set[str]:
        """Return lower-case dev names with a 3D chibi prefab bundle (character-<dev>-_mxload-*)."""
        return set(self._model_index())

    def _field_index(self) -> dict[str, list[str]]:
        """Field-mode models (event and story variants with no battle model) by lower-case dev name.

        Each entry lists the field character bundles holding ``Field_Player_<dev>[_NonWeapon]``, or a field NPC named by
        the character's id (``Field_NPC_<id>[_...]``, keyed by the lower-case id), read from their containers.
        """
        from .model import NPC_PREFAB

        def build() -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for path in self._paths():
                if not path.name.startswith("field-character-_mxload"):
                    continue
                try:
                    with open_bundle(path) as env:
                        names = list(env.container.keys())
                except (ServiceError, OSError, ValueError) as error:
                    logger.warning("Field character bundle %s unreadable: %s", path.name, error)
                    continue
                for name in names:
                    stem = name.rsplit("/", 1)[-1]
                    match = re.fullmatch(r"Field_Player_([A-Za-z0-9_]+?)(_NonWeapon)?\.prefab", stem) or (
                        NPC_PREFAB.fullmatch(stem[:-7]) if stem.lower().endswith(".prefab") else None
                    )
                    if match and "/character/" in name.lower():
                        paths = out.setdefault(match.group(1).lower(), [])
                        if str(path) not in paths:
                            paths.append(str(path))
            return out

        return self._index("field-models", build, disk=False)

    def field_model_cores(self) -> set[str]:
        """Return lower-case dev names of field players and NPCs (Field_Player_<dev>, Field_NPC_<id>)."""
        return set(self._field_index())

    def _asset_families(self) -> dict[str, list[str]]:
        """The characters' and NPCs' own dependency bundles by lower-case dev name.

        They are ``assets-_mx-npcs|characters-<dev>-_mxdependency-*``, found from the file names alone.
        """

        def build() -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for category in ("npcs", "characters"):
                for core, paths in group_bundles(self._paths(), category).items():
                    out.setdefault(core, []).extend(str(path) for path in paths)
            return out

        return self._index("asset-families", build, disk=False)

    def _asset_model(self, core: str, paths: list[str]) -> list[str] | None:
        """The bundles to export a model of ``core``'s own dependency bundles from.

        See :func:`model.asset_root`. The one holding its root, then those holding clips. Parts that cannot hold a root
        are not opened (:func:`discovery.may_hold_model_root`).
        """
        from .model import BundleGraph, asset_roots

        best, owner, clips = None, None, []
        # one bundle open at a time (a root's components and children are its own bundle's objects), so this needs
        # no model lock and can run while the catalog builds
        for path in (Path(path) for path in paths if may_hold_model_root(Path(path).name)):
            try:
                with BundleGraph({}) as graph:
                    found = asset_roots(graph, [path], core)
                    if any(obj.type.name == "AnimationClip" for obj in graph.objects.values()):
                        clips.append(str(path))
            except (ServiceError, OSError, ValueError) as error:
                logger.warning("Model bundle %s unreadable: %s", path.name, error)
                continue
            if found and (best is None or found[0][0] < best):
                best, owner = found[0][0], str(path)
        return [owner, *(path for path in clips if path != owner)] if owner else None

    def asset_model_cores(self, candidates: Iterable[str]) -> set[str]:
        """Find candidates whose own dependency bundles hold story-only or UI-only models.

        Examples include Ayame's NP0206/NP0229 story forms and Arona. Each name is read
        once, on first request.

        Args:
            candidates: Dev names lacking character/field models, normalized to lower case for discovery.
        """
        families = self._asset_families()
        with self._lock("asset-models"):
            found = self._indexes.setdefault("asset-models", {})
            for core in sorted({str(c).lower() for c in candidates} - found.keys()):
                paths = families.get(core)
                found[core] = self._asset_model(core, paths) if paths else None
            return {core for core in (str(c).lower() for c in candidates) if found.get(core)}

    def _story_clips(self) -> dict[str, list[str]]:
        """The story cutscenes' clips by the character id they are named after.

        An id looks like NP0206_Cutin_A_01; the result is {id: [bundle]}, among the cutscenes' dependency bundles
        (assets-_mx-prodresources-sccutscene_*). A story model moves only there (Ayame's story forms).
        """

        def build() -> dict[str, list[str]]:
            found: dict[str, set[str]] = {}
            for path in self._paths():
                if not path.name.startswith("assets-_mx-prodresources-sccutscene_"):
                    continue
                with open_bundle(path) as env:
                    for obj in env.objects:
                        if obj.type.name == "AnimationClip" and (m := STORY_CLIP.match(obj.peek_name() or "")):
                            found.setdefault(m.group(1).lower(), set()).add(str(path))
            return {core: sorted(paths) for core, paths in sorted(found.items())}

        return self._index("story-clips", build)

    def _asset_bundles(self, core: str) -> list[str] | None:
        if not self.asset_model_cores({core}):
            return None
        return self._indexes.get("asset-models", {}).get(core)

    def _cab_discovery(self):
        from .cabs import CabIndex

        with self._lock("cab-discovery"):
            if "cab-discovery" not in self._indexes:
                self._indexes["cab-discovery"] = CabIndex(
                    self.resource_dirs, cache=self.preview_cache, namespace=self.preview_namespace, reader=cab_names
                )
            return self._indexes["cab-discovery"]

    def _resolve_cabs(self, wanted: set[str], hint_path: Path | None = None) -> dict[str, str]:
        return self._cab_discovery().resolve(wanted, hint_path)

    def _cab_index(self, everything: bool = False) -> dict[str, str]:
        """Explicit full-index compatibility path; preview requests resolve on demand."""
        return self._cab_discovery().all((lambda path: True) if everything else is_model_dependency)

    def model_glb(
        self, core: str, overrides: Iterable[Path] = (), *, reads: PreviewReads | None = None, clip: str | None = None
    ) -> bytes:
        """Export a chibi or one of its named clips to cached lossless GLB bytes.

        The export is one step under the model lock (about 1 s for a model, 0.5 s for a clip); a cached one needs no
        lock. Source choices cover every dependency, keeping chosen-source previews independent of installed mods.

        Args:
            core: Model dev name or NPC ID, matched without case sensitivity.
            overrides: Mod bundles replacing official prefab parts and dependencies holding the same CABs.
            reads: Optional source-selection rule for all other game files, with cache identity and trusted locations.
            clip: Exact name from the base GLB's extras.clips; None exports the base model with its default clips.
                Named clips use model.export_clip and the same source files, with a per-model/clip/file cache key.

        Raises:
            AssetNotFound: The model/clip is absent, the clip name is invalid, or sources change while reading.
        """
        from .model import BundleGraph, export_chibi, export_clip

        if clip is not None and not CLIP_NAME.fullmatch(clip):
            raise AssetNotFound(("clip", core, "invalid"))
        core = core.lower()
        slot = self._model_index().get(core) or self._field_index().get(core)
        listed = slot or self._asset_bundles(core)
        if not listed:
            raise AssetNotFound(("model", core))
        bundles = [Path(path) for path in listed]
        # a story model (no prefab of its own) moves with its own clips of the story cutscenes
        story = (
            []
            if slot
            else [
                Path(reads.resolve(Path(path))) if reads is not None else Path(path)
                for path in self._story_clips().get(core, [])
            ]
        )
        overrides = list(dict.fromkeys(Path(path).resolve() for path in overrides))
        # Prefabs are opened directly by the exporter; CAB overrides alone only replace their dependencies.
        own = prefab_overrides(bundles, overrides)
        bundles = [
            mine if mine is not None else Path(reads.resolve(path)) if reads is not None else path
            for path, mine in zip(bundles, own)
        ]
        resolve_cabs = self._resolve_cabs
        if reads is not None:

            def resolve_cabs(wanted: set[str], hint_path: Path | None = None) -> dict[str, str]:
                return {
                    cab: str(reads.resolve(Path(path))) for cab, path in self._resolve_cabs(wanted, hint_path).items()
                }

        stamps = []
        for path in [*bundles, *overrides, *story]:
            try:
                stat = path.stat()
            except FileNotFoundError:
                raise AssetNotFound(str(path)) from None
            stamps.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        # Cache identity includes source-selection rules and export schemas for halo effects, clip aliases,
        # and the complete clip list. Named clips also include the requested clip name.
        if clip is None:
            key = _SCHEMA + (
                "model/v2/fx1/vital2/names1/" + json.dumps([core, stamps])
                if reads is None
                else "model/v3/fx1/vital2/names1/" + json.dumps([core, stamps, reads.key])
            )
        else:
            key = _SCHEMA + (
                "model-clip/v1/" + json.dumps([core, clip, stamps])
                if reads is None
                else "model-clip/v1/reads/" + json.dumps([core, clip, stamps, reads.key])
            )
        generation, disk_generation = self.cache.generation, self._preview_generation()

        def valid_cached() -> bytes | None:
            cached = self.cache.get(key, version=self.cache_version, disk=False)
            # the meshes, textures and clips sit in other bundles than the prefab: the entry also keeps the stamps of
            # every bundle the export read, so one changed in place (a mod install) exports again
            if cached is not None and all(
                stamp is not None and _stamp(path) == stamp for path, stamp in cached[1].items()
            ):
                return cached[0]
            stored = self._preview_get(key)
            if stored is not None:
                try:
                    files = unpack_preview_files(stored)
                    read = json.loads(files["sources.json"])
                    if not isinstance(read, dict) or not read or len(read) > 8192:
                        raise ValueError("Invalid model source manifest")
                    permitted = {str(path) for path in overrides}
                    if any(
                        not isinstance(path, str)
                        or (
                            Path(path).parent not in self.resource_dirs
                            and path not in permitted
                            and not (reads is not None and reads.trusted(Path(path)))
                        )
                        for path in read
                    ):
                        raise ValueError("Invalid model source location")
                    if all(
                        isinstance(stamp, list) and len(stamp) == 3 and _stamp(path) == tuple(stamp)
                        for path, stamp in read.items()
                    ):
                        data = files["model.glb"]
                        self.cache.put(
                            key,
                            (data, {path: tuple(stamp) for path, stamp in read.items()}),
                            version=self.cache_version,
                            disk=False,
                            size=len(data),
                            generation=generation,
                        )
                        return data
                except (ValueError, UnicodeError, TypeError, KeyError):
                    pass
                self.preview_cache.discard(self._preview_key(key))
            return None

        # Cached models need no Unity bundle locks and must not queue behind unrelated model or lobby exports.
        with spans.span("export.model.cache-read"):
            cached = valid_cached()
        if cached is not None:
            return cached
        with self._model_flights_lock:
            flight = self._model_flights.get(key)
            leader = flight is None
            if flight is None:
                flight = Future()
                self._model_flights[key] = flight
        if not leader:
            with spans.span("export.model.join"):
                return flight.result()

        def export() -> bytes:
            with spans.held("model-lock.model", _MODEL_LOCK, hold=True):
                cached = valid_cached()
                if cached is not None:
                    return cached
                started: dict[str, tuple[int, int, int] | None] = {}
                phases = {"open": 0.0, "resolve": 0.0}
                find_cabs = resolve_cabs

                def resolve_timed(wanted: set[str], hint_path: Path | None = None) -> dict[str, str]:
                    begun = time.perf_counter()
                    try:
                        return find_cabs(wanted, hint_path)
                    finally:
                        phases["resolve"] += time.perf_counter() - begun

                @contextmanager
                def tracked_bundle(path: Path):
                    before = _stamp(path)
                    if before is None:
                        raise AssetNotFound("Model dependency is unavailable")
                    begun = time.perf_counter()
                    with open_bundle(path) as env:
                        phases["open"] += time.perf_counter() - begun
                        # The opener decodes before yielding. Never associate those decoded
                        # bytes with a replacement written while it was opening the bundle.
                        if _stamp(path) != before:
                            raise AssetNotFound("Model dependency changed while opening")
                        started[str(path)] = before
                        yield env
                        if _stamp(path) != before:
                            raise AssetNotFound("Model dependency changed during extraction")

                with (
                    self._cab_discovery().batch(),
                    BundleGraph({}, overrides, resolver=resolve_timed, opener=tracked_bundle) as graph,
                ):
                    try:
                        with spans.span("export.model.convert"):
                            # the halo's particles: their materials and meshes, read as a lobby's (_FxResolver)
                            def fx(objects: Mapping[int, Any]) -> Callable[[str, int, dict], Any]:
                                return _FxResolver(graph, dict(objects), self._shader_forms).resolve

                            if clip is not None:
                                data = export_clip(graph, bundles, core, clip)
                            else:
                                data = export_chibi(graph, bundles, core, fx=fx, **({"story": story} if story else {}))
                    finally:
                        spans.record("export.model.open", phases["open"])
                        spans.record("export.model.resolve", phases["resolve"])
                    read = {path: _stamp(path) for path in graph.loaded}
                    complete = not graph.missing_cabs
                    if any(stamp is None or _stamp(path) != stamp for path, stamp in started.items()):
                        raise AssetNotFound("Model dependency changed during extraction")
                if spine_stamps([*bundles, *overrides, *story]) != list(dict.fromkeys(stamps)):
                    raise AssetNotFound("Model changed during extraction")
                self.cache.put(
                    key, (data, read), version=self.cache_version, disk=False, size=len(data), generation=generation
                )
                if (
                    complete
                    and read
                    and all(stamp is not None for stamp in read.values())
                    and self.preview_cache is not None
                    and self.preview_cache.allowed
                ):
                    with spans.span("export.model.cache-write"):
                        manifest = json.dumps({str(path): stamp for path, stamp in read.items()}).encode()
                        self._preview_put(
                            key, pack_preview_files({"sources.json": manifest, "model.glb": data}), disk_generation
                        )
                return data

        try:
            data = export()
        except BaseException as error:
            flight.set_exception(error)
            raise
        else:
            flight.set_result(data)
            return data
        finally:
            with self._model_flights_lock:
                del self._model_flights[key]

    def _lobby_prefabs(self) -> dict[str, str]:
        """Memorial lobby prefab names (lower case) -> bundle names.

        Read from the bundle containers alone (about 5 s cold). It holds names only, so it is cached like the other
        indexes.
        """

        def build() -> dict[str, str]:
            names: dict[str, str] = {}
            for path in filter(is_lobby_prefab, self._paths()):
                with open_bundle(path) as env:
                    for name in env.container:
                        if name.lower().endswith(".prefab"):
                            names.setdefault(name.rsplit("/", 1)[-1][:-7].lower(), path.name)
            return names

        return self._index("lobby-prefabs", build)

    def lobby_info(self, core: str, prefab: Path | None = None) -> dict:
        """Read a memorial lobby's layout and interaction from its prefab and clip assets.

        The lobby.py data is
        read from the character's bundles, so it stays in memory; the file stamps keep an applied mod apart. With
        ``prefab``, that bundle file is read in place of the lobby's prefab bundle in the game folder (a lobby camera's
        own prefab).

        Args:
            core: Installed lobby bundle core, matched without case sensitivity.
            prefab: Optional replacement prefab bundle, such as a lobby camera's own copy.

        Raises:
            AssetNotFound: The lobby core or one of its required bundle files is absent.
        """
        try:
            bundles = self.spine_cores("lobby")[core.lower()]
        except KeyError:
            raise AssetNotFound(("lobby", core)) from None
        stamps = spine_stamps(bundles) + (spine_stamps([prefab]) if prefab is not None else [])

        def build() -> bytes:
            skeletons, behaviours, trees, keys = [], [], {}, {}
            for path in bundles:
                with open_bundle(path) as env:
                    for obj in env.objects:
                        if obj.type.name == "TextAsset":
                            name = obj.peek_name()
                            if name.lower().endswith(".skel"):
                                skeletons.append(name[:-5])
                        elif obj.type.name == "MonoBehaviour":
                            try:
                                tree = obj.read_typetree()
                            except (ValueError, TypeError):
                                logger.debug("Cannot read lobby behaviour %s", obj.path_id, exc_info=True)
                                continue
                            behaviours.append((obj.path_id, tree))
                            # the volume profiles and their components are behaviours of the core's bundles too
                            trees[(obj.assets_file.name.lower(), obj.path_id)] = tree
                            # a clip's sound keys point at events in another bundle: their CAB names, by key
                            for i, key in enumerate(tree.get("SoundKeys") or []):
                                event = key.get("Event") or {}
                                if event.get("m_PathID"):
                                    cab, pid = reference(obj, event)
                                    keys[(obj.path_id, event.get("m_FileID", 0), pid)] = f"{cab.lower()}_{pid}"
            skeletons = list(dict.fromkeys(skeletons))
            clips = lobby.Clips(
                behaviours,
                skeletons,
                sound=lambda owner, pointer: keys.get((owner, pointer.get("m_FileID", 0), pointer.get("m_PathID", 0))),
            )
            prefabs, found = self._lobby_prefabs(), None
            for name in lobby.prefab_candidates(core, skeletons):
                if name in prefabs:
                    found = self._lobby_prefab(prefabs[name], name, skeletons, clips, trees, path=prefab)
                    if found:
                        break
            sounds = self._lobby_sound_events(clips.sound_events())
            return json.dumps(
                lobby.lobby_info(skeletons, clips, found, sounds), ensure_ascii=True, separators=(",", ":")
            ).encode()

        return json.loads(self._cached("lobby/" + json.dumps([core.lower(), stamps]), build, disk=False))

    def lobby_prefab(self, core: str) -> tuple[str, str] | None:
        """Find a lobby's prefab bundle filename and root GameObject name, or None without a prefab.

        A lobby camera stores its framing in that prefab.

        Args:
            core: Installed lobby core passed to lobby_info without case sensitivity.
        """
        root = self.lobby_info(core).get("prefab")
        bundle = self._lobby_prefabs().get(str(root or "").lower())
        return (bundle, root) if root and bundle else None

    def _lobby_prefab(
        self,
        bundle: str,
        root: str,
        skeletons: list[str],
        clips: lobby.Clips,
        behaviours: dict[tuple[str, int], dict] | None = None,
        path: Path | None = None,
    ) -> dict | None:
        """Read a lobby prefab on its bundle (``path``: that file in its place), as ``read_prefab`` does.

        ``behaviours`` are the lobby core's MonoBehaviours by (CAB name, path id), where the prefab's volume profiles
        usually are.
        """
        from .model import BundleGraph

        if path is None:
            try:
                path = self._bundle_path(bundle)
            except AssetNotFound:
                return None
        known = dict(behaviours or {})
        # the particle renderers' materials, textures and meshes sit in other bundles (the lobbies' shared effect
        # bundles, the shader bundles, a character's own): one graph holds them open, so it runs alone like a model
        # export (see BundleGraph)
        with (
            spans.held("model-lock.particles", _MODEL_LOCK, hold=True),
            self._cab_discovery().batch(),
            BundleGraph({}, resolver=self._resolve_cabs) as graph,
        ):
            env = graph.add(path)
            objects = {obj.path_id: obj for obj in env.objects}
            kinds = {pid: obj.type.name for pid, obj in objects.items()}

            def tree(pid: int) -> dict:
                # read on demand: a lobby bundle may hold several prefabs, only one tree is walked
                return graph.tree(objects[pid])

            def profile(pid: int, pointer: dict) -> list[dict] | None:
                cab, ref = reference(objects[pid], pointer)
                key = (cab.lower(), ref)
                if key not in known:
                    known.update(self._lobby_behaviours(cab))
                found = known.get(key)
                if found is None:
                    logger.debug("Volume profile %s not found", key)
                    return None
                # the components are sub-assets of the profile, in its own file
                parts = [(key[0], c.get("m_PathID")) for c in found.get("components", []) if not c.get("m_FileID")]
                return [known[part] for part in parts if part in known]

            scripts = {pid: tree(pid).get("m_ClassName") for pid, kind in kinds.items() if kind == "MonoScript"}
            fx = _FxResolver(graph, objects, self._shader_forms)
            return lobby.read_prefab(kinds, tree, scripts, root, skeletons, clips, profile=profile, resolve=fx.resolve)

    def _lobby_behaviours(self, cab: str) -> dict[tuple[str, int], dict]:
        """The MonoBehaviours of another lobby core's bundle by (CAB name, path id).

        A few lobbies share a volume profile (Neru's is Serika's). Read on demand and kept in memory only, like the
        lobby data.
        """
        path = self._resolve_cabs({cab}).get(cab.lower())
        out: dict[tuple[str, int], dict] = {}
        if path is None:
            return out
        with open_bundle(Path(path)) as env:
            for obj in env.objects:
                if obj.type.name == "MonoBehaviour" and obj.assets_file.name.lower() == cab.lower():
                    try:
                        out[(cab.lower(), obj.path_id)] = obj.read_typetree()
                    except (ValueError, TypeError):
                        logger.debug("Cannot read lobby behaviour %s", obj.path_id, exc_info=True)
        return out

    def _lobby_sound_events(self, ids: Iterable[str]) -> dict[str, dict]:
        """The sound key events by id ("<cab>_<path id>"), as lobby.sound_event reads them.

        They are the AnimationAudioEvent assets of the lobbies' shared bundle; their clips are ids of the same form. An
        event that cannot be read is left out. Part of the lobby data: kept in memory only.
        """
        wanted: dict[str, set[int]] = {}
        for event in ids:
            cab, _, pid = event.rpartition("_")
            if cab and pid.lstrip("-").isdigit():
                wanted.setdefault(cab, set()).add(int(pid))
        if not wanted:
            return {}
        index, out = self._resolve_cabs(set(wanted)), {}
        for cab, pids in wanted.items():
            bundle = index.get(cab)
            if bundle is None:
                logger.debug("Sound events of %s not found", cab)
                continue
            try:
                with open_bundle(Path(bundle)) as env:
                    for obj in env.objects:
                        if (
                            obj.path_id not in pids
                            or obj.assets_file.name.lower() != cab
                            or obj.type.name != "MonoBehaviour"
                        ):
                            continue

                        def clip_id(pointer: dict, obj: Any = obj) -> str | None:
                            if not pointer.get("m_PathID"):
                                return None
                            c, p = reference(obj, pointer)
                            return f"{c.lower()}_{p}"

                        event = lobby.sound_event(obj.read_typetree(), clip_id)
                        if event:
                            out[f"{cab}_{obj.path_id}"] = event
            except (OSError, ValueError, TypeError, ServiceError):
                logger.debug("Cannot read the sound events of %s", cab, exc_info=True)
        return out

    def lobby_sound(self, core: str, clip_id: str) -> bytes:
        """Decode a listed lobby AudioClip to WAV using UnityPy/FMOD, caching game audio only in memory.

        Args:
            core: Lobby core whose exported sound events authorize the clip.
            clip_id: <lower-case CAB>_<path ID> locator listed by a sound event.

        Raises:
            AssetNotFound: The clip is unlisted, malformed, unavailable, or undecodable.
        """
        if not any(clip_id in e.get("clips", ()) for e in self.lobby_info(core).get("sounds", {}).values()):
            raise AssetNotFound(("lobby-sound", core, clip_id))
        cab, _, pid = clip_id.rpartition("_")
        bundle = self._resolve_cabs({cab}).get(cab.lower())
        if bundle is None or not pid.lstrip("-").isdigit():
            raise AssetNotFound(("lobby-sound", core, clip_id))

        def build() -> bytes:
            with open_bundle(Path(bundle)) as env:
                obj = next(
                    (
                        o
                        for o in env.objects
                        if o.path_id == int(pid) and o.assets_file.name.lower() == cab and o.type.name == "AudioClip"
                    ),
                    None,
                )
                if obj is None:
                    raise AssetNotFound(("lobby-sound", core, clip_id))
                try:
                    samples = obj.read().samples
                except Exception as exc:  # FMOD missing or a format it cannot decode
                    logger.debug("Cannot decode the lobby sound %s", clip_id, exc_info=True)
                    raise AssetNotFound(("lobby-sound", core, clip_id)) from exc
                data = next(iter(samples.values()), None)
                if not data:
                    raise AssetNotFound(("lobby-sound", core, clip_id))
                return data

        return self._cached("lobby-sound/" + json.dumps([clip_id, spine_stamps([Path(bundle)])]), build, disk=False)

    def lobby_fx_texture(self, core: str, tex_id: str) -> bytes:
        """Decode a listed lobby effect texture to PNG bytes cached only in memory.

        Args:
            core: Lobby core whose fx texture table authorizes the image.
            tex_id: <lower-case CAB>_<path ID> locator listed in lobby_info's fx.textures.

        Raises:
            AssetNotFound: The texture is unlisted, malformed, or unavailable in the install.
        """
        if tex_id not in self.lobby_info(core).get("fx", {}).get("textures", {}):
            raise AssetNotFound(("lobby-fx", core, tex_id))
        cab, _, pid = tex_id.rpartition("_")
        bundle = self._resolve_cabs({cab}).get(cab.lower())
        if bundle is None or not pid.lstrip("-").isdigit():
            raise AssetNotFound(("lobby-fx", core, tex_id))

        def build() -> bytes:
            with open_bundle(Path(bundle)) as env:
                obj = next(
                    (
                        o
                        for o in env.objects
                        if o.path_id == int(pid) and o.assets_file.name.lower() == cab and o.type.name == "Texture2D"
                    ),
                    None,
                )
                if obj is None:
                    raise AssetNotFound(("lobby-fx", core, tex_id))
                return png_bytes(texture_image(obj.read(), Path(bundle).name))

        return self._cached("lobby-fx/" + json.dumps([tex_id, spine_stamps([Path(bundle)])]), build, disk=False)
