"""Lazy game asset service for the web UI's asset routes."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import Future
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..core import devmode, preview_gate, spans
from ..core.errors import ServiceError
from bmpt.engine import parse_filename, spine_kind
from ..unity.texture_guard import texture_image
from .cache import AssetCache, PreviewCache, pack_preview_files, unpack_preview_files
from . import lobby
from .discovery import (
    bundle_paths,
    cab_names,
    group_bundles,
    is_atlas,
    is_layout_texture,
    is_lobby_prefab,
    is_model_dependency,
    is_pointer_texture,
    is_prefab,
    is_texture,
    may_hold_model_root,
    resource_version,
)
from .errors import AssetNotFound
from .layouts import export_layout, subtree
from .preview_reads import PreviewReads
from .spine import extract_spine, png_bytes, skipped, spine_stamps
from .unity import find_object, object_record, open_bundle, reference, ref_key

logger = logging.getLogger(__name__)
_SCHEMA = "gameassets-v1/"
# Bump the layout schema when export_layout changes so only incompatible layout entries are rebuilt.
_LAYOUT = "layout-v6/"
STORY_CLIP = re.compile(r"^((?:np|ch)\d{4})_", re.IGNORECASE)  # a story cutscene's clip named after its character
# a clip asked for by name (model_glb's clip): the characters the official clip names use ("CH0271_Formation Pickup");
# the name is only compared with the model's clip names, never used as a path
CLIP_NAME = re.compile(r"[A-Za-z0-9_ ]{1,128}")
_ATLASES = "atlases-v2/"


class _ModelLock:
    """The model lock: one reader at a time of those that hold several bundles open at once.

    Those readers are model and clip exports, the selection-frame layout, lobby particles and office models, each
    keeping the path lock of every bundle it read until it ends, so two of them could wait on each other's bundles. Held
    for one export (about 1 s for a model, 0.5 s for a clip, no step inside it); taken after the library's lock, never
    before it. A costume build needs no model lock: it reads bundles from memory after each individually locked load
    (``unity.load_bundle``). A foreground preview that waits for it while a job or background work holds it (the costume
    check's export, a lobby warm-up) waits outside its own time (``preview_gate.waiting``): the page's deadline leaves
    the wait out.
    """

    def __init__(self) -> None:
        """Create an unlocked export lock with no background holder."""
        self._lock = threading.Lock()
        self._job = False  # the holder answers no foreground preview

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire the export lock, excluding background-holder waits from foreground deadlines.

        Args:
            blocking: Whether to wait when another export holds the lock.
            timeout: Maximum wait in seconds for a blocking acquire; -1 waits indefinitely.

        Returns:
            Whether the lock was acquired.
        """
        if not self._lock.acquire(False):
            if not blocking:
                return False
            if self._job and preview_gate.in_foreground():
                with preview_gate.waiting():
                    if not self._lock.acquire(True, timeout):
                        return False
            elif not self._lock.acquire(True, timeout):
                return False
        self._job = not preview_gate.in_foreground()
        return True

    def release(self) -> None:
        """Release the held export lock."""
        self._lock.release()

    def locked(self) -> bool:
        """Report whether an export currently holds the lock."""
        return self._lock.locked()

    def __enter__(self) -> bool:
        """Acquire the export lock for the context."""
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        """Release the export lock when the context exits."""
        self.release()


_MODEL_LOCK = _ModelLock()


def model_lock(what: str) -> Any:
    """Wrap the model lock with wait/hold spans named model-lock.<what> for the supplied operation label."""
    return spans.held(f"model-lock.{what}", _MODEL_LOCK, hold=True)


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
    # the description tooltip: a greyed control's reason on hover (web/js/hover-hint.js)
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


class GameAssets:
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

    # --- 3D chibi models ---

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


# Unity's built-in meshes live in "Library/unity default resources", outside the bundles; the lobbies' effect quads
# use its Quad
_BUILTIN_QUAD = ("library/unity default resources", 10210)


def _builtin_quad(owner: Any, pointer: dict | None) -> tuple[str, None] | None:
    """("quad", None) for a pointer to Unity's built-in Quad mesh, else None."""
    fid, pid = (pointer or {}).get("m_FileID", 0), (pointer or {}).get("m_PathID", 0)
    try:
        path = owner.assets_file.externals[fid - 1].path if owner is not None and fid > 0 else ""
    except (AttributeError, IndexError):
        return None
    return ("quad", None) if (str(path).lower(), pid) == _BUILTIN_QUAD else None


class _FxResolver:
    """Reads what a lobby's renderers point at, for lobby.read_prefab.

    A particle material is read as (id, particles.material) with its textures (particles.texture), a mesh as (id,
    particles.mesh) (Unity's built-in Quad as ("quad", None)), the render queue of a skeleton's atlas pages ("pages": a
    SkeletonDataAsset, or one Material) as (id, {texture name: queue}), or their shaders' names ("shaders") as (id,
    {texture name: shader name}), the materials a skeleton is drawn with ("skeleton": a SkeletonDataAsset) as (id,
    spine_materials.skeleton) and an Animator's controller ("animator") as (id, {"decoded", "bindings", "loop"}) of its
    default state's clip. Ids are "<cab>_<path id>".
    """

    def __init__(self, graph: Any, objects: dict[int, Any], shader_forms: dict):
        """Prepare dependency reads for one prefab's effect components.

        Args:
            graph: BundleGraph resolving external pointers and caching typetrees.
            objects: Prefab-file readers indexed by path ID, identifying each pointer owner.
            shader_forms: Shared parsed-shader cache indexed by CAB and path ID.
        """
        self.graph, self.objects, self.forms = graph, objects, shader_forms
        self.done: dict[tuple[str, str], Any] = {}

    def resolve(self, kind: str, owner: int, pointer: dict) -> tuple[str, Any] | None:
        """Resolve an effect pointer to a stable ID and player-ready data, caching failures too.

        Args:
            kind: material, mesh, pages, shaders, skeleton, animator, timeline, or sprite description to read.
            owner: Prefab-file component path ID supplying the pointer's external-file table.
            pointer: Unity m_FileID/m_PathID reference to the requested asset.

        Returns:
            (<lower-case CAB>_<path ID>, description), ("quad", None) for the built-in Quad,
            or None for an absent, incompatible, or unreadable object.
        """
        from . import particles
        from .model import read_mesh

        g = self.graph
        own = self.objects.get(owner)
        obj = g.deref(own, pointer) if own is not None else None
        if obj is None:
            return _builtin_quad(own, pointer) if kind == "mesh" else None
        oid = f"{obj.assets_file.name.lower()}_{obj.path_id}"
        if (kind, oid) in self.done:
            return self.done[(kind, oid)]
        out = None
        try:
            if kind == "material" and obj.type.name == "Material":
                out = (oid, self._material(obj))
            elif kind == "mesh" and obj.type.name == "Mesh":
                data = read_mesh(obj.read())
                tris = [t for sub in data.triangles for t in sub]
                out = (oid, particles.mesh(data.vertices, data.uv0, data.normals, tris, data.colors))
            elif kind in ("pages", "shaders") and obj.type.name in ("MonoBehaviour", "Material"):
                out = (oid, self._pages(obj, kind))
            elif kind == "skeleton" and obj.type.name == "MonoBehaviour":
                out = (oid, self._skeleton(obj))
            elif kind == "animator" and obj.type.name == "AnimatorController":
                out = self._animator(obj, oid)
            elif kind == "timeline" and obj.type.name == "MonoBehaviour":
                from . import intro

                out = (oid, intro.read_timeline(g, obj, sound=self._sound))
            elif kind == "sprite" and obj.type.name == "Sprite":
                out = (oid, self._sprite(obj))
        except Exception:  # an unreadable object: the system is left out
            logger.debug("Cannot read lobby particle %s %s", kind, oid, exc_info=True)
            out = None
        self.done[(kind, oid)] = out
        return out

    def _sound(self, asset: Any, data: dict) -> dict | None:
        """A TimeLineAudioController's AudioData as a sound event (lobby.sound_event), with its id: the asset's."""

        def clip_id(pointer: dict) -> str | None:
            if not pointer.get("m_PathID"):
                return None
            cab, pid = reference(asset, pointer)
            return f"{cab.lower()}_{pid}"

        event = lobby.sound_event({"m_Name": "Intro", "AudioData": data}, clip_id)
        return {**event, "id": f"{asset.assets_file.name.lower()}_{asset.path_id}"} if event else None

    def _sprite(self, obj: Any) -> dict | None:
        """A Sprite as intro.sprite draws it.

        That is its texture (particles.texture), its rect in the texture's pixels, its size in units (rect / pixels per
        unit) and its pivot (0 to 1). The pixels are read when served.
        """
        from . import particles

        g = self.graph
        st = g.tree(obj)
        rd = st.get("m_RD") or {}
        tex = g.deref(obj, rd.get("texture"))
        if tex is None or tex.type.name != "Texture2D":
            return None
        tex_id = f"{tex.assets_file.name.lower()}_{tex.path_id}"
        info = particles.texture(g.tree(tex), tex_id)
        g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)
        r, tr = st.get("m_Rect") or {}, rd.get("textureRect") or {}
        ppu = float(st.get("m_PixelsToUnits") or 100.0) or 100.0
        pivot = st.get("m_Pivot") or {}
        return {
            "tex": info,
            "rect": [float(tr.get(k, 0.0)) for k in ("x", "y", "width", "height")],
            "size": [float(r.get("width", 0.0)) / ppu, float(r.get("height", 0.0)) / ppu],
            "pivot": [float(pivot.get("x", 0.5)), float(pivot.get("y", 0.5))],
        }

    def _pages(self, obj: Any, kind: str = "pages") -> dict[str, Any]:
        """{texture name: render queue} of a SkeletonDataAsset's atlas pages, as spine-unity draws them.

        Each page comes with the material its SpineAtlasAsset lists for it (matched by the material's main texture name,
        which is the page's file name without its extension). For a Material, its own page. With kind "shaders",
        {texture name: the name of the material's shader} instead (a page without a shader is left out). The textures'
        pixels are never read.
        """
        g = self.graph
        materials = [obj]
        if obj.type.name == "MonoBehaviour":
            materials = []
            for atlas in (g.deref(obj, p) for p in g.tree(obj).get("atlasAssets") or []):
                if atlas is not None:
                    materials += [g.deref(atlas, p) for p in g.tree(atlas).get("materials") or []]
        out: dict[str, Any] = {}
        for mat in materials:
            if mat is None or mat.type.name != "Material":
                continue
            tree = g.tree(mat)
            envs = dict((tree.get("m_SavedProperties") or {}).get("m_TexEnvs", []))
            tex = g.deref(mat, (envs.get("_MainTex") or {}).get("m_Texture"))
            name = tex.peek_name() if tex is not None else None
            if name:
                shader, _, state = self._state(mat, tree)
                value = shader if kind == "shaders" else state["queue"]
                if value is not None:
                    out.setdefault(name, value)
        return out

    def _state(self, obj: Any, tree: dict) -> tuple[str | None, dict | None, dict]:
        """A material's shader name, the shader's parsed form and the render state of its colour pass.

        The parsed form holds the parts read here; the render state is this material's (model.shader_state: blend and
        queue).
        """
        from .model import shader_state

        g = self.graph
        shader = g.deref(obj, tree.get("m_Shader"))
        name, form = None, None
        if shader is not None:
            key = (shader.assets_file.name.lower(), shader.path_id)
            if key not in self.forms:
                # a shader's typetree carries its compiled programs: read once, keep the parts used here
                full = g.tree(shader).get("m_ParsedForm") or {}
                self.forms[key] = {
                    "m_Name": full.get("m_Name"),
                    "m_PropInfo": full.get("m_PropInfo", {"m_Props": []}),
                    "m_SubShaders": [
                        {
                            "m_Tags": s.get("m_Tags"),
                            "m_Passes": [{"m_State": p.get("m_State")} for p in s.get("m_Passes", [])],
                        }
                        for s in full.get("m_SubShaders", [])
                    ],
                }
                g.trees.pop((key[0], key[1]), None)
            form = self.forms[key]
            name = form.get("m_Name")
        props = tree.get("m_SavedProperties") or {}
        floats = {k: v for k, v in props.get("m_Floats", []) if isinstance(v, (int, float))}
        try:
            state = shader_state(form, floats, tree.get("m_CustomRenderQueue", -1)) if form else {}
        except (KeyError, TypeError, IndexError, ValueError):
            state = {}
        if not state:
            queue = tree.get("m_CustomRenderQueue", -1)
            state = {"src": 1, "dst": 0, "queue": queue if queue and queue >= 0 else 3000}
        return name, form, state

    def _material(self, obj: Any) -> dict:
        from . import particles

        g = self.graph
        tree = g.tree(obj)
        name, form, state = self._state(obj, tree)
        props = tree.get("m_SavedProperties") or {}
        textures = {}
        for prop, env in props.get("m_TexEnvs", []):
            tex = g.deref(obj, env.get("m_Texture"))
            if tex is not None and tex.type.name == "Texture2D":
                textures[prop] = particles.texture(g.tree(tex), f"{tex.assets_file.name.lower()}_{tex.path_id}")
                g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)  # its pixels are read when served
        out = particles.material(name, tree, state, form["m_PropInfo"].get("m_Props", []) if form else (), textures)
        out["cull"] = state.get("cull", 2)  # back faces are culled (2), front faces (1) or none (0): meshfx.py's meshes
        return out

    def _skeleton(self, obj: Any) -> dict:
        """The materials of a SkeletonDataAsset's atlas pages and its blend mode replacement materials.

        See spine_materials.skeleton. Only the pages a ported shader draws read their other textures' settings; no
        pixels are read.
        """
        from . import spine_materials

        g = self.graph
        tree = g.tree(obj)
        mats = []
        for atlas in (g.deref(obj, p) for p in tree.get("atlasAssets") or []):
            if atlas is not None:
                for mat in (g.deref(atlas, p) for p in g.tree(atlas).get("materials") or []):
                    if mat is not None and mat.type.name == "Material":
                        mats.append(self._spine_material(mat))

        def replacement(pointer: dict) -> tuple[str | None, dict] | None:
            mat = g.deref(obj, pointer)
            return self._spine_material(mat) if mat is not None and mat.type.name == "Material" else None

        return spine_materials.skeleton(
            mats, tree.get("blendModeMaterials") or {}, replacement, tree.get("scale") or 0.01
        )

    def _spine_material(self, mat: Any) -> tuple[str | None, dict]:
        from . import particles, spine_materials

        g = self.graph
        tree = g.tree(mat)
        name, form, _ = self._state(mat, tree)
        envs = dict((tree.get("m_SavedProperties") or {}).get("m_TexEnvs", []))
        main = g.deref(mat, (envs.get("_MainTex") or {}).get("m_Texture"))
        textures = {}
        for prop, env in envs.items() if spine_materials.program(name) else ():
            tex = g.deref(mat, env.get("m_Texture")) if prop != "_MainTex" else None
            if tex is not None and tex.type.name == "Texture2D":
                textures[prop] = particles.texture(g.tree(tex), f"{tex.assets_file.name.lower()}_{tex.path_id}")
                g.trees.pop((tex.assets_file.name.lower(), tex.path_id), None)
        return (main.peek_name() if main is not None else None), spine_materials.material(name, form, tree, textures)

    def _animator(self, controller: Any, oid: str) -> tuple[str, dict] | None:
        """The clip an AnimatorController's first layer plays in its default state.

        The lobbies' controllers have one state, looping one clip. The clip is decoded (model.decode_clip) with its
        bindings.
        """
        from .model import decode_clip

        g = self.graph
        tree = g.tree(controller)
        clips = [g.deref(controller, p) for p in tree.get("m_AnimationClips") or []]
        machine = ((tree.get("m_Controller") or {}).get("m_StateMachineArray") or [{}])[0]
        machine = machine.get("data", machine)
        states = machine.get("m_StateConstantArray") or []
        clip = None
        if states:
            state = states[min(int(machine.get("m_DefaultState", 0)), len(states) - 1)]
            state = state.get("data", state)
            for blend in state.get("m_BlendTreeConstantArray") or []:
                for node in blend.get("data", blend).get("m_NodeArray") or []:
                    node = node.get("data", node)
                    index = node.get("m_ClipID", -1)
                    if 0 <= index < len(clips) and clips[index] is not None:
                        clip = clips[index]
                        break
                if clip is not None:
                    break
        if clip is None or clip.type.name != "AnimationClip":
            return None
        ct = g.tree(clip)
        bindings = (ct.get("m_ClipBindingConstant") or {}).get("genericBindings", [])
        loop = bool((ct.get("m_MuscleClip") or {}).get("m_LoopTime", True))
        return oid, {"decoded": decode_clip(ct), "bindings": bindings, "loop": loop}
