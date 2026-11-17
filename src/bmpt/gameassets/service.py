"""Lazy game asset service for the web UI's asset routes."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from collections.abc import Iterable, Iterator
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..core import devmode, spans
from ..core.errors import ServiceError
from bmpt.engine import spine_kind
from ..unity.texture_guard import texture_image
from .cache import AssetCache, PreviewCache, pack_preview_files, unpack_preview_files
from .discovery import (
    bundle_paths,
    cab_names,
    group_bundles,
    is_atlas,
    is_layout_texture,
    is_pointer_texture,
    is_prefab,
    is_texture,
    resource_version,
)
from .errors import AssetNotFound
from .layouts import export_layout, subtree
from .spine import extract_spine, png_bytes, skipped, spine_stamps
from .unity import find_object, object_record, open_bundle, reference, ref_key
from .model_service import ModelsMixin
from .service_base import _MODEL_LOCK, _SCHEMA, _FxResolver


logger = logging.getLogger(__name__)
# Bump the layout schema when export_layout changes so only incompatible layout entries are rebuilt.
_LAYOUT = "layout-v6/"
_ATLASES = "atlases-v2/"


_SPRITE_FIELDS = (
    "x",
    "y",
    "width",
    "height",
    "borderLeft",
    "borderRight",
    "borderTop",
    "borderBottom",
    "paddingLeft",
    "paddingRight",
    "paddingTop",
    "paddingBottom",
)


# Global-client bundle filenames used as fast prefab hints.
# Checked before scanning every prefab bundle. A game update rebuilds a changed bundle under a new CRC, the last part
# of its name: PREFAB_CABS then finds it among the bundles of the same name less the CRC.
PREFAB_HINTS = {
    "UICharacterCollection": "ui-_mxload-prefabs-2025-07-02_assets_all_3649883161.bundle",
    "UICharacterCollectionPopulatorUnit": "ui-uiassetobject-_mxload-2024-11-18_assets_all_4108373898.bundle",
    "UIPopup_GachaCharList": "ui-_mxload-prefabs-2025-07-02_assets_all_3329130623.bundle",
    "UIPopup_Sort": "ui-_mxload-prefabs-2025-07-02_assets_all_1596625361.bundle",
    "UIPopup_Mulligan": "ui-_mxload-prefabs-2025-07-02_assets_all_267995722.bundle",
    # the description tooltip: a greyed control's reason on hover (web/js/ui/hover-hint.js)
    "UIDescriptionTooltip": "ui-tooltip-_mxload-prefabs-2026-04-06_assets_all_413832495.bundle",
    # the student list's display settings popup (filter and sort tabs) and its filter group template
    "UIPopup_FilterSort": "ui-_mxload-prefabs-2025-07-02_assets_all_2229874793.bundle",
    "UIFilterObjectList": "ui-uiassetobject-_mxload-2024-11-18_assets_all_4108373898.bundle",
    "UICharacterDetail": "ui-_mxload-prefabs-2025-07-02_assets_all_4244715916.bundle",
    # the portrait's fullscreen view, opened by the detail screen's ZoomButton
    "UICharacterVisualMode": "ui-_mxload-prefabs-2025-07-02_assets_all_3863404692.bundle",
    "UIPopup_Option_PC": "ui-_mxload-prefabs-2025-11-28_assets_all_3994951711.bundle",
    # the loading screen (Resources/UI/UILoading.prefab) and its "Now Loading" indicator
    "UILoading": "prologgroup-assets-_mx-resources-ui-_mxprolog-2026-04-13_assets_all_1341870950.bundle",
    "UIRepeater": "ui-_mxload-prefabs-2025-07-02_assets_all_1187726256.bundle",
    # the loading ring a still being made shows in its place (UIRatioNoticePopup/Center/Popup/Base/LoadingIcon)
    "UIRatioNoticePopup": "ui-_mxload-prefabs-2025-07-02_assets_all_2108580176.bundle",
    # the lobby, whose bottom menu entries hold the game's disabled look (layouts.py STATE_FIELDS)
    "UIPopup_Formation_CharEdit": "ui-_mxload-prefabs-2025-07-02_assets_all_2190018216.bundle",
    "UIMailBox": "ui-_mxload-prefabs-2025-07-02_assets_all_3761459989.bundle",
    "UILobby": "ui-_mxload-prefabs-2025-07-02_assets_all_1122735527.bundle",
    # the student hall's first-screen slot template
    "UILobbyCharacterSelectPopup_Slot": "ui-_mxload-prefabs-2025-07-02_assets_all_3424282486.bundle",
}
# The internal (CAB) name of each hinted bundle: unlike the CRC, an update that rebuilds the bundle keeps it, and
# reading it takes only the bundle's header.
PREFAB_CABS = {
    "UICharacterCollection": "cab-e017641da51ecb7ddc61c727fe7d42c8",
    "UICharacterCollectionPopulatorUnit": "cab-d2c7f32eddfa3175eb350c1b4c586b33",
    "UIPopup_GachaCharList": "cab-15457654181428fbffe12298d7846f73",
    "UIPopup_Sort": "cab-7fb051b9251c7717ef5394a35da87c52",
    "UIPopup_Mulligan": "cab-0289266f037a5e4b22c9ef25622fbc99",
    "UIDescriptionTooltip": "cab-97c3b3fecdec100761a404aa17c35026",
    "UIPopup_FilterSort": "cab-11f9127e73f96f5d1e83a9522c8cc298",
    "UIFilterObjectList": "cab-d2c7f32eddfa3175eb350c1b4c586b33",
    "UICharacterDetail": "cab-e478474fb06d2fa735098c2370feb7ac",
    "UICharacterVisualMode": "cab-d14fefcfbe7f5f2903860f2d9e7f6776",
    "UIPopup_Option_PC": "cab-c43e5ff2f665426af43eb0d550563fe2",
    "UILoading": "cab-4c819d78e46be1ed1a32ad3e295e51e0",
    "UIRepeater": "cab-3731528684e0b1d0a35dd40a37462b05",
    "UIRatioNoticePopup": "cab-d19d2d22bd4d9320bc006c65e8d361a7",
    "UIPopup_Formation_CharEdit": "cab-dd05055f5344e2f7817dd319c6c56a44",
    "UIMailBox": "cab-df7afbdbff489d4ab5545b64b4904568",
    "UILobby": "cab-6d02540d52a62021acf772e8b866c60a",
    "UILobbyCharacterSelectPopup_Slot": "cab-a72e4fc32a67ea1e4fb3150a8b53b9ea",
}
_CRC = re.compile(r"_\d+(?=\.bundle$)")


def less_crc(name: str) -> str:
    """Remove a bundle filename's trailing CRC, retaining the identity preserved by a rebuild."""
    return _CRC.sub("", name)


def is_collection_art(name: str) -> bool:
    """Check whether a collection-bundle texture name is served by the art routes.

    These bundles contain UIs/01_Common/14_CharacterCollect, the folder referenced by
    CostumeExcel's CollectionBGTexturePath and CollectionTexturePath.

    Every background there is a costume's collection background: most are named BG_<place>_Collection, a few after
    the scenario background they reuse (BG_NewYearFestival2, BG_WaterFall), and two are region copies
    (BG_<place>_Collection_kr), so the suffix is not required. Portraits are Student_Portrait_<id>_Collection; the
    folder's NPC_Portrait_* are never asked for.

    Args:
        name: Collection Texture2D name, tested with case-sensitive BG_/portrait naming conventions.
    """
    return name.startswith("BG_") or (name.startswith("Student_Portrait_") and name.endswith("_Collection"))


class GameAssets(ModelsMixin):
    """Read light UI assets and heavy character art from the user's install.

    Construction only checks directory existence. Discovery, version hashing,
    Unity loading and index construction happen on the first relevant request.
    Index locks prevent duplicate scans, while per-path locks serialize all
    Unity operations even across different service instances.
    """

    def __init__(self, resource_dirs: list[Path], cache: AssetCache, *, preview_cache: PreviewCache | None = None):
        """Configure lazy reads for one install without scanning or loading Unity assets.

        Args:
            resource_dirs: Ordered resource folders; existing resolved directories are deduplicated in precedence order.
            cache: Consent-controlled light-asset storage and bounded decoded-asset memory cache.
            preview_cache: Optional separately consented store for unchanged heavy preview exports.

        Raises:
            ServiceError: No supplied resource directory exists (game_not_found).
        """
        for path in resource_dirs:
            devmode.check_read(path)  # dev mode: only game folders inside the dev roots
        self.resource_dirs = list(dict.fromkeys(Path(p).resolve() for p in resource_dirs if Path(p).is_dir()))
        if not self.resource_dirs:
            raise ServiceError("game_not_found")
        self.cache = cache
        self.preview_cache = preview_cache
        self._guard = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}
        self._model_flights_lock = threading.Lock()
        self._model_flights: dict[str, Future[bytes]] = {}
        self._indexes: dict[str, Any] = {}
        self._shader_forms: dict[tuple[str, int], dict] = {}  # shader (cab, path id) -> name, properties, pass states

    @property
    def preview_namespace(self) -> str:
        """Keep identical resource catalogs in different installations separate."""
        return json.dumps([[str(path) for path in self.resource_dirs], self.resource_version])

    @property
    def cache_version(self) -> str:
        """Scope light assets and indexes to the installation as well as its resource version."""
        return hashlib.sha1(self.preview_namespace.encode()).hexdigest()

    def clear_memory(self) -> None:
        """Drop derived indexes; old requests retain only their private references."""
        with self._guard:
            self._indexes = {}
            self._shader_forms = {}
            self.cache.clear_memory()

    def _preview_key(self, key: str) -> str:
        return self.preview_namespace + "/" + key

    def _preview_get(self, key: str) -> bytes | None:
        return self.preview_cache.get(self._preview_key(key)) if self.preview_cache else None

    def _preview_put(self, key: str, value: bytes, generation: str | None) -> None:
        if self.preview_cache is not None and generation is not None:
            self.preview_cache.put(self._preview_key(key), value, generation=generation)

    def _preview_generation(self) -> str | None:
        return self.preview_cache.generation if self.preview_cache else None

    def _lock(self, key: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    @property
    def resource_version(self) -> str:
        """Return the lazily cached catalog hash, or hash of the sorted resource file listing."""
        with self._lock("version"):
            if "version" not in self._indexes:
                self._indexes["version"] = resource_version(self.resource_dirs)
            return self._indexes["version"]

    def resource_changed(self) -> bool:
        """Check for an on-disk game update, returning False before this instance has indexed a version."""
        with self._lock("version"):
            known = self._indexes.get("version")
        return known is not None and resource_version(self.resource_dirs) != known

    def _paths(self) -> list[Path]:
        with self._lock("paths"):
            if "paths" not in self._indexes:
                self._indexes["paths"] = bundle_paths(self.resource_dirs)
            return self._indexes["paths"]

    def _bundle_path(self, name: str) -> Path:
        # Resolve in the current folders: a sandbox can acquire more bundles after the first discovery.
        if Path(name).name == name:
            for directory in self.resource_dirs:
                path = directory / name
                if path.is_file():
                    return path
        raise AssetNotFound(name)

    def _cached(self, key: str, build: Callable[[], bytes], *, disk: bool = True) -> bytes:
        with self._lock(key):
            generation = self.cache.generation
            value = self.cache.get(_SCHEMA + key, version=self.cache_version, disk=disk)
            if value is None:
                value = build()
                self.cache.put(_SCHEMA + key, value, version=self.cache_version, disk=disk, generation=generation)
            return value

    def _preview_bytes(self, key: str, build: Callable[[], bytes]) -> bytes:
        """Reuse the exact export bytes in memory and, with opt-in, across launches."""
        with self._lock(key):
            generation, disk_generation = self.cache.generation, self._preview_generation()
            value = self.cache.get(_SCHEMA + key, version=self.cache_version, disk=False)
            if value is None:
                value = self._preview_get(key)
                if value is None:
                    value = build()
                    self._preview_put(key, value, disk_generation)
                self.cache.put(_SCHEMA + key, value, version=self.cache_version, disk=False, generation=generation)
            return value

    def _index(self, key: str, build: Callable[[], Any], *, disk: bool = True) -> Any:
        with self._lock(key):
            generation = self.cache.generation
            if key not in self._indexes:

                def encode() -> bytes:
                    return json.dumps(build(), ensure_ascii=True, separators=(",", ":")).encode()

                data = self._cached(key, encode, disk=disk)
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeError):
                    logger.warning("Rebuilding invalid asset index %s", key)
                    value = build()
                    self.cache.put(
                        _SCHEMA + key,
                        json.dumps(value).encode(),
                        version=self.cache_version,
                        disk=disk,
                        generation=generation,
                    )
                self._indexes[key] = value
            return self._indexes[key]

    def _atlas_paths(self) -> list[Path]:
        """Reuse discovery while directory entries are unchanged; the caller holds the atlas lock.

        Resource folders are flat. Directory stamps detect added, removed and renamed bundles without enumerating
        every game resource for each sprite. Each known atlas is still stamped separately by ``_atlas_index`` so
        edits in place, including replacements that preserve mtime, never depend on the directory changing.
        """

        def directories() -> tuple:
            return tuple(
                (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns)
                for stat in (directory.stat() for directory in self.resource_dirs)
            )

        for _ in range(2):
            before = directories()
            if self._indexes.get("atlas-directories") == before:
                return self._indexes["atlas-paths"]
            paths = list(filter(is_atlas, bundle_paths(self.resource_dirs)))
            # Never publish a partial inventory when a download changes the directory during discovery.
            if directories() == before:
                self._indexes["atlas-directories"], self._indexes["atlas-paths"] = before, paths
                return paths
        raise AssetNotFound("UI atlas bundles changed during discovery")

    def _atlas_index(self) -> dict:
        # The catalog version alone does not describe the installed UI bundles (partial downloads and sandboxes).
        # Keep the inventory in the key, including replacements that preserve size and modification time.
        with self._lock("atlases"):
            generation = self.cache.generation
            try:
                paths = self._atlas_paths()
                if not paths:
                    raise AssetNotFound("No UI atlas bundles in the resource folders")
                stamps = spine_stamps(paths)
                key = _ATLASES + hashlib.sha256(json.dumps(stamps).encode()).hexdigest()
                if self._indexes.get("atlas-source") == key:
                    return self._indexes["atlases"]
                data = self.cache.get(_SCHEMA + key, version=self.cache_version)
                value = None
                if data is not None:
                    try:
                        value = json.loads(data)
                        self._validate_atlases(value)
                    except (ValueError, UnicodeError, TypeError, KeyError) as error:
                        logger.warning("Rebuilding invalid UI atlas index: %s", error)
                        value = None
                if value is None:
                    value = self._scan_atlases(paths)
                    self._validate_atlases(value)
                    if spine_stamps(self._atlas_paths()) != stamps:
                        raise AssetNotFound("UI atlas bundles changed during indexing")
                    self.cache.put(
                        _SCHEMA + key, json.dumps(value).encode(), version=self.cache_version, generation=generation
                    )
                self._indexes["atlases"], self._indexes["atlas-source"] = value, key
                return value
            except Exception as error:
                logger.exception("Cannot build UI atlas index from %s: %r", self.resource_dirs, error)
                raise

    @staticmethod
    def _validate_atlases(index: dict) -> None:
        if not isinstance(index, dict) or not all(
            isinstance(index.get(key), dict) for key in ("sprites", "images", "names")
        ):
            raise ValueError("Invalid UI atlas index structure")
        if not index["names"] or not any(index["sprites"].values()):
            raise ValueError("UI atlas index has no sprites")
        if set(index["names"].values()) != set(index["sprites"]):
            raise ValueError("UI atlas index is missing atlas metadata")
        for name, sprites in index["sprites"].items():
            if not isinstance(sprites, dict) or not all(isinstance(sprite, dict) for sprite in sprites.values()):
                raise ValueError(f"Invalid UI atlas metadata: {name}")
            for sprite, rect in sprites.items():
                if not all(
                    isinstance(rect.get(field), (int, float)) and math.isfinite(rect[field])
                    for field in ("x", "y", "width", "height")
                ):
                    raise ValueError(f"Invalid UI sprite rectangle: {name}/{sprite}")
            record = index["images"].get(name)
            if sprites and (
                not isinstance(record, dict) or not all(key in record for key in ("bundle", "cab", "pid", "name"))
            ):
                raise ValueError(f"UI atlas {name} has sprites but no texture")

    def _scan_atlases(self, paths: list[Path]) -> dict:
        atlases, by_key, objects, materials, names = {}, {}, {}, {}, {}
        for path in paths:
            with open_bundle(path) as env:
                for obj in env.objects:
                    key = ref_key(obj.assets_file.name, obj.path_id)
                    if obj.type.name == "MonoBehaviour":
                        tree = obj.read_typetree()
                        if "mSprites" in tree:
                            name = tree["m_Name"]
                            atlases[name] = (
                                key,
                                tree,
                                obj.assets_file.name,
                                [ext.path for ext in obj.assets_file.externals],
                            )
                            by_key[key] = atlases[name]
                            names[key] = name
                    elif obj.type.name == "Texture2D":
                        name = obj.peek_name()
                        objects[key] = object_record(path, obj, name)
                    elif obj.type.name == "Material":
                        tree = obj.read_typetree()
                        for name, value in tree["m_SavedProperties"]["m_TexEnvs"]:
                            if name == "_MainTex":
                                materials[key] = ref_key(*reference(obj, value["m_Texture"]))
        # All pointers are resolved after scanning, so external material and
        # texture CABs may appear before or after their atlas bundle.
        sprites, images = {}, {}
        for name, entry in atlases.items():
            seen = set()
            while True:
                key, tree, cab, externals = entry
                replacement = tree.get("mReplacement", {})
                if not replacement.get("m_PathID"):
                    break
                if key in seen:
                    raise AssetNotFound(f"UI atlas replacement cycle: {name}")
                seen.add(key)
                fid = replacement.get("m_FileID", 0)
                target = cab if not fid else externals[fid - 1].replace("\\", "/").rsplit("/", 1)[-1]
                try:
                    entry = by_key[ref_key(target, replacement["m_PathID"])]
                except KeyError:
                    raise AssetNotFound(f"Missing UI atlas replacement: {name}") from None
            sprites[name] = {
                sprite["name"]: {field: sprite.get(field, 0) for field in _SPRITE_FIELDS} for sprite in tree["mSprites"]
            }
            pointer = tree.get("material", {})
            fid = pointer.get("m_FileID", 0)
            target = cab if not fid else externals[fid - 1].replace("\\", "/").rsplit("/", 1)[-1]
            texture_key = materials.get(ref_key(target, pointer.get("m_PathID", 0)))
            if texture_key in objects:
                images[name] = objects[texture_key]
        return {"sprites": sprites, "images": images, "names": names}

    def atlases(self) -> dict[str, dict[str, dict]]:
        """Return NGUI sprite rectangles, borders and padding by atlas name."""
        return deepcopy(self._atlas_index()["sprites"])

    def _image(self, record: dict) -> Image.Image:
        path = self._bundle_path(record["bundle"])
        key = "image/" + ref_key(record["cab"], record["pid"]) + json.dumps(spine_stamps([path]))
        with self._lock(key):
            generation = self.cache.generation
            image = self.cache.get(_SCHEMA + key, version=self.cache_version, disk=False)
            if image is None:
                with open_bundle(path) as env:
                    image = texture_image(find_object(env, record).read(), path.name)
                    image.load()
                self.cache.put(_SCHEMA + key, image, version=self.cache_version, disk=False, generation=generation)
            return image

    def sprite_png(self, atlas: str, sprite: str) -> bytes:
        """Crop a named atlas sprite and return PNG bytes.

        Args:
            atlas: Exact NGUI atlas name from atlases.
            sprite: Exact sprite name within that atlas, using its top-left rectangle.

        Raises:
            AssetNotFound: The atlas/sprite is absent or atlas bundles change during extraction.
        """
        with self._lock("atlases"):
            index = self._atlas_index()
            source = self._indexes["atlas-source"]

        def build() -> bytes:
            try:
                rect = index["sprites"][atlas][sprite]
                record = index["images"][atlas]
            except KeyError:
                raise AssetNotFound((atlas, sprite)) from None
            image = self._image(record)
            # NGUI's mSprites uses top-left coordinates. UnityPy has already
            # flipped the decoded Unity texture into Pillow's top-left space.
            x, y = rect["x"], rect["y"]
            data = png_bytes(image.crop((x, y, x + rect["width"], y + rect["height"])))
            if self._atlas_index() is not index:
                raise AssetNotFound("UI atlas bundles changed while reading a sprite")
            return data

        return self._cached("sprite/" + source + json.dumps([atlas, sprite]), build)

    def _texture_index(self) -> dict:
        return self._index("textures", lambda: self._scan_textures(filter(is_texture, self._paths())))

    def _scan_textures(self, paths: Iterable[Path]) -> dict:
        textures, names = {}, {}
        for path in paths:
            with open_bundle(path) as env:
                for obj in env.objects:
                    if obj.type.name == "Texture2D":
                        name = obj.peek_name()
                        record = object_record(path, obj, name)
                        textures.setdefault(name, record)
                        names[ref_key(obj.assets_file.name, obj.path_id)] = name
        return {"textures": textures, "names": names}

    def textures(self) -> list[str]:
        """List the names available in the shared UI texture families."""
        return sorted(self._texture_index()["textures"])

    def _pointer_index(self) -> dict:
        """The PC client's cursor and the touch effect's textures (a small scan, about 0.1 s).

        An index of its own, so a UI texture index cached before these families were read stays valid.
        """
        return self._index(
            "pointer-textures", lambda: self._scan_textures(filter(is_pointer_texture, self._paths()))["textures"]
        )

    def texture_png(self, name: str) -> bytes:
        """Decode a shared UI, cursor, or touch-effect texture as PNG bytes.

        Args:
            name: Exact Texture2D name in the shared UI or pointer-effect indexes.

        Raises:
            AssetNotFound: Neither index contains the requested texture.
        """

        def build() -> bytes:
            record = self._texture_index()["textures"].get(name) or self._pointer_index().get(name)
            if record is None:
                raise AssetNotFound(name)
            return png_bytes(self._image(record))

        return self._cached("texture/" + name, build)

    def _prefabs(self) -> dict:
        return self._index("prefabs", self._scan_prefabs)

    def _prefab_bundle(self, root: str) -> Path:
        """The bundle holding a prefab root.

        The known bundle first (one small read), after a game update the bundle of the same name less the CRC that holds
        the known CAB (a header read each), else the full index (a scan of every UI prefab bundle, about 12 s cold).
        """
        for path in self._hinted_bundles(root):
            suffix = f"/{root.lower()}.prefab"
            with open_bundle(path) as env:
                if any(name.lower().endswith(suffix) for name in env.container):
                    return path
        try:
            return self._bundle_path(self._prefabs()[root])
        except KeyError:
            raise AssetNotFound(root) from None

    def _hinted_bundles(self, root: str) -> Iterator[Path]:
        """The bundles PREFAB_HINTS points at for a root, likeliest first: the named one, then the rebuilt ones."""
        hint = PREFAB_HINTS.get(root)
        if not hint:
            return
        try:
            yield self._bundle_path(hint)
        except AssetNotFound:
            pass
        cab, stem = PREFAB_CABS.get(root), less_crc(hint)
        if not cab:
            return
        for path in self._paths():
            if path.name == hint or less_crc(path.name) != stem or not is_prefab(path):
                continue
            try:
                names = cab_names(path)
            except (OSError, ValueError):
                continue
            if cab in names:
                yield path

    def _scan_prefabs(self) -> dict:
        roots = {}
        for path in filter(is_prefab, self._paths()):
            with open_bundle(path) as env:
                # The AssetBundle container points directly at prefab root
                # GameObjects, avoiding reads of every child and behaviour.
                found = False
                for name, pointer in env.container.items():
                    if not name.lower().endswith(".prefab") or pointer.m_FileID:
                        continue
                    obj = pointer.deref()
                    if obj.type.name == "GameObject":
                        roots.setdefault(obj.peek_name(), path.name)
                        found = True
                if not found:
                    for obj in env.objects:
                        if obj.type.name in ("Transform", "RectTransform"):
                            transform = obj.read()
                            if not transform.m_Father.path_id:
                                roots.setdefault(transform.m_GameObject.read().m_Name, path.name)
        return roots

    def layout(self, root: str, subpath: str = "") -> dict:
        """Return a prefab tree, optionally selecting an exact child subpath.

        Args:
            root: Exact top-level UI prefab GameObject name, resolved through hints and bundle containers.
            subpath: Slash-separated child names relative to the root; empty selects the whole tree.

        Raises:
            AssetNotFound: The prefab, its root, or a requested child is absent.
        """
        generation = self.cache.generation

        def build() -> dict:
            path = self._prefab_bundle(root)
            extra = self._index(
                "layout-textures", lambda: self._scan_textures(filter(is_layout_texture, self._paths()))["names"]
            )
            names = self._atlas_index()["names"] | self._texture_index()["names"] | extra
            from . import selection_frames

            if root == selection_frames.ROOT:
                from .model import BundleGraph

                with (
                    spans.held("model-lock.layout", _MODEL_LOCK, hold=True),
                    self._cab_discovery().batch(),
                    BundleGraph({}, resolver=self._resolve_cabs) as graph,
                ):
                    env = graph.add(path)
                    resolver = _FxResolver(graph, {obj.path_id: obj for obj in env.objects}, self._shader_forms)
                    effects = selection_frames.export_effects(env, resolver.resolve)
                    return export_layout(env, root, names, effects=effects)
            with open_bundle(path) as env:
                return export_layout(env, root, names)

        # Layouts can be large. Keep their serialized bytes in the bounded LRU,
        # rather than pinning every visited prefab tree alongside the indexes.
        def encode() -> bytes:
            return json.dumps(build(), ensure_ascii=True, separators=(",", ":")).encode()

        data = self._cached(_LAYOUT + root, encode)
        try:
            tree = json.loads(data)
        except (ValueError, UnicodeError):
            logger.warning("Rebuilding invalid prefab layout %s", root)
            tree = build()
            self.cache.put(
                _SCHEMA + _LAYOUT + root, json.dumps(tree).encode(), version=self.cache_version, generation=generation
            )
        return subtree(tree, subpath)

    def selection_frame_texture(self, tex_id: str) -> bytes:
        """Serve starting-skill selection-frame textures as PNG bytes cached only in memory.

        Args:
            tex_id: <lower-case CAB>_<path ID> locator listed by the selected-card material descriptions.

        Raises:
            AssetNotFound: The texture is unlisted, malformed, or unavailable in the install.
        """
        from .selection_frames import ROOT, SLOT

        slot = self.layout(ROOT, SLOT)
        textures = {}

        def collect(node):
            effect = node.get("frameEffect") or {}
            for tex in effect.get("material", {}).get("tex", {}).values():
                if "id" in tex:
                    textures[tex["id"]] = tex
            for child in node.get("children", []):
                collect(child)

        collect(slot)
        if tex_id not in textures:
            raise AssetNotFound(("selection-frame", tex_id))
        cab, _, pid = tex_id.rpartition("_")
        bundle = self._resolve_cabs({cab}).get(cab.lower())
        if bundle is None or not pid.lstrip("-").isdigit():
            raise AssetNotFound(("selection-frame", tex_id))

        def build():
            with open_bundle(Path(bundle)) as env:
                obj = next(
                    (
                        obj
                        for obj in env.objects
                        if obj.path_id == int(pid)
                        and obj.assets_file.name.lower() == cab
                        and obj.type.name == "Texture2D"
                    ),
                    None,
                )
                if obj is None:
                    raise AssetNotFound(("selection-frame", tex_id))
                return png_bytes(texture_image(obj.read(), Path(bundle).name))

        return self._cached("selection-frame/" + json.dumps([tex_id, spine_stamps([Path(bundle)])]), build, disk=False)

    def _collection(self) -> dict:
        """Collection backgrounds and portraits by texture name (names and bundle locations only, memory only)."""

        def build() -> dict:
            paths = [p for p in self._paths() if p.name.startswith("uis-01_common-14_charactercollect-")]
            textures = self._scan_textures(paths)["textures"]
            return {name: record for name, record in textures.items() if is_collection_art(name)}

        return self._index("collection", build, disk=False)

    def collection_names(self) -> dict[str, list[str]]:
        """List collection backgrounds and portraits by their full texture names."""
        names = self._collection()
        return {
            "bg": sorted(n for n in names if n.startswith("BG_")),
            "portrait": sorted(n for n in names if n.startswith("Student_Portrait_")),
        }

    def office_backgrounds(self) -> list[dict]:
        """List complete installed day/night office backgrounds with translated-label keys and layout URLs."""
        from . import office

        return office.choices(self)

    def office_layout(self, key: str) -> dict:
        """Return the installed day/night office layout and revisioned texture URLs.

        Args:
            key: Office background choice, "day" or "night".

        Raises:
            AssetNotFound: The background is unavailable in this install.
        """
        from . import office

        return office.layout(self, key)

    def office_texture(self, key: str, name: str) -> bytes:
        """Decode a texture used by an installed office prefab at its original resolution.

        Args:
            key: Office background choice, "day" or "night".
            name: Exact texture name referenced by that background's active widgets.

        Raises:
            AssetNotFound: The background/texture is absent or its bundle changes during extraction.
        """
        from . import office

        record = office.texture_record(self, key, name)
        path = self._bundle_path(record["bundle"])
        stamps = spine_stamps([path])

        def build() -> bytes:
            data = png_bytes(self._image(record))
            if spine_stamps([path]) != stamps:
                raise AssetNotFound("Office background changed during extraction")
            return data

        value = self._preview_bytes("office/texture/" + key + "/" + name + json.dumps(stamps), build)
        if spine_stamps([path]) != stamps:
            raise AssetNotFound("Office background changed during extraction")
        return value

    def _collection_png(self, name: str, prefix: str) -> bytes:
        if not name.startswith(prefix) or name not in self._collection():
            raise AssetNotFound(name)
        record = self._collection()[name]
        path = self._bundle_path(record["bundle"])
        stamps = spine_stamps([path])

        def build() -> bytes:
            value = png_bytes(self._image(record))
            if spine_stamps([path]) != stamps:
                raise AssetNotFound("Collection art changed during extraction")
            return value

        value = self._preview_bytes("art/" + prefix + name + json.dumps(stamps), build)
        if spine_stamps([path]) != stamps:
            raise AssetNotFound("Collection art changed during extraction")
        return value

    def collection_bg(self, name: str) -> tuple[bytes, str]:
        """Return a lossless collection background with its image MIME type.

        Args:
            name: Exact installed collection texture name beginning with BG_.

        Raises:
            AssetNotFound: The background is absent or its bundle changes during extraction.
        """
        return self._collection_png(name, "BG_"), "image/png"

    def collection_portrait(self, name: str) -> bytes:
        """Return a lossless collection portrait as PNG bytes.

        Args:
            name: Exact installed roster texture name beginning with Student_Portrait_.

        Raises:
            AssetNotFound: The portrait is absent or its bundle changes during extraction.
        """
        return self._collection_png(name, "Student_Portrait_")

    def collection_target(self, name: str) -> dict:
        """Return an installed student-roster texture locator, excluding selection-card portraits.

        Args:
            name: Exact Student_Portrait_ texture name present in the collection index.

        Raises:
            ServiceError: The texture is not an available roster portrait (photo_target_unavailable).
        """
        if not isinstance(name, str) or not name.startswith("Student_Portrait_") or name not in self._collection():
            raise ServiceError("photo_target_unavailable")
        return dict(self._collection()[name])

    def collection_portrait_from(self, name: str, paths: list[Path]) -> bytes:
        """Read a Collection portrait from explicitly verified original bundles.

        Args:
            name: Exact installed roster portrait name, validated by collection_target.
            paths: Verified original bundle copies expected to contain exactly one matching Texture2D.

        Returns:
            Lossless PNG bytes, optionally persisted in the preview cache.

        Raises:
            ServiceError: The portrait is unavailable, duplicated, or its source copies change while reading.
        """
        self.collection_target(name)
        stamps = spine_stamps(paths)

        def build() -> bytes:
            found = []
            for path in paths:
                with open_bundle(path) as env:
                    for obj in env.objects:
                        if obj.type.name == "Texture2D" and obj.peek_name() == name:
                            found.append(png_bytes(texture_image(obj.read(), path.name)))
            if len(found) != 1 or spine_stamps(paths) != stamps:
                raise ServiceError("photo_target_unavailable")
            return found[0]

        return self._preview_bytes("photography/default/" + name + json.dumps(stamps), build)

    def spine_cores(self, kind: str) -> dict[str, list[Path]]:
        """Group portrait or lobby bundle parts by parsed Spine core.

        Args:
            kind: "spr" for portraits or "lobby" for memorial/background/special lobby slots.

        Raises:
            AssetNotFound: The category is neither spr nor lobby.
        """
        categories = {"spr": ("spinecharacters",), "lobby": ("spinelobbies", "spinebackground", "spinecharacters")}
        if kind not in categories:
            raise AssetNotFound(kind)
        # shared bundles of the category (e.g. "_mxcommon-.asset") are not skeletons
        groups = self._index(
            "spine-cores/" + kind,
            lambda: {
                core: [str(path) for path in paths]
                for category in categories[kind]
                for core, paths in group_bundles(self._paths(), category).items()
                if spine_kind(core, category) == kind
            },
            disk=False,
        )
        return {core: [Path(path) for path in paths] for core, paths in groups.items()}

    def spine_files(self, kind: str, core: str) -> dict[str, bytes]:
        """Return all files required by a portrait or lobby Spine player.

        Args:
            kind: Spine category, "spr" or "lobby".
            core: Bundle core to find without case sensitivity.

        Raises:
            AssetNotFound: The category/core or a required bundle/atlas page is absent.
        """
        try:
            bundles = self.spine_cores(kind)[core.lower()]
        except KeyError:
            raise AssetNotFound((kind, core)) from None
        return self.spine_files_from(bundles)

    def spine_files_from(self, bundles: list[Path]) -> dict[str, bytes]:
        """Extract ordered parts, optionally persisting their unchanged bytes.

        Supply official parts first and mod parts last. Cache identity includes
        the order and file stamps, so precedence and changed mods invalidate it.

        Args:
            bundles: Merge-ordered Spine bundle paths, optionally a Composition carrying type exclusions.

        Raises:
            AssetNotFound: The list is empty, a bundle/page is absent, or sources change during extraction.
        """
        if not bundles:
            raise AssetNotFound("spine")
        skip = skipped(bundles)
        paths = list(dict.fromkeys(Path(path).resolve() for path in bundles))
        stamps = spine_stamps(paths)
        left = sorted([str(path), sorted(types)] for path, types in skip.items())
        key = "spine/" + json.dumps([stamps, left] if left else stamps)
        with self._lock(key):
            generation, disk_generation = self.cache.generation, self._preview_generation()
            value = self.cache.get(_SCHEMA + key, version=self.cache_version, disk=False)
            if value is None:
                stored = self._preview_get(key)
                if stored is not None:
                    try:
                        value = unpack_preview_files(stored)
                    except (ValueError, UnicodeError, TypeError):
                        self.preview_cache.discard(self._preview_key(key))
                if value is None:
                    value = extract_spine(paths, skip) if skip else extract_spine(paths)
                    if spine_stamps(paths) != stamps:
                        raise AssetNotFound("spine changed during extraction")
                    if self.preview_cache is not None and self.preview_cache.allowed:
                        self._preview_put(key, pack_preview_files(value), disk_generation)
                if spine_stamps(paths) != stamps:
                    raise AssetNotFound("spine changed during extraction")
                self.cache.put(
                    _SCHEMA + key,
                    value,
                    version=self.cache_version,
                    disk=False,
                    size=sum(len(data) for data in value.values()),
                    generation=generation,
                )
            return dict(value)
