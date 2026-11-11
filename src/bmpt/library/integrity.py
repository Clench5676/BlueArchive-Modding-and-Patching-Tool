"""Read-only bundle preflight and Spine inputs for the web runtime's compatibility check."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import devmode
from bmpt.engine import REPLACEABLE_ASSET_TYPES, AssetKey, AssetType, Bundle, UpdateScope, get_skel_version
from ..errors import ServiceError
from ..gameassets.spine import atlas_pages
from ..gameassets.discovery import cab_names


#: The own CABs a bundle summary names (a bundle holds one serialized file, two with a scene)
SUMMARY_CABS = 10


def basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def texture_key(name: str) -> str:
    return basename(name).removesuffix(".png")


@dataclass
class Contents:
    platforms: set[str] = field(default_factory=set)
    versions: set[str] = field(default_factory=set)
    layout: dict[str, tuple[tuple[int, int], ...]] = field(default_factory=dict)
    # per serialized file, the files its objects point into, in file id order
    externals: dict[str, tuple[str, ...]] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)
    # the bundle's asset paths (its AssetBundle container keys), lower case: each is held by one bundle of a build
    paths: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    references: set[str] = field(default_factory=set)
    reference_paths: set[str] = field(default_factory=set)
    streams: set[str] = field(default_factory=set)
    reference_names: dict[str, str] = field(default_factory=dict)
    skeletons: dict[str, bytes] = field(default_factory=dict)
    atlases: dict[str, bytes] = field(default_factory=dict)
    textures: dict[str, tuple[int, int]] = field(default_factory=dict)

    def merge(self, other: Contents) -> None:
        for key in ("platforms", "versions", "names", "paths", "files", "references", "reference_paths", "streams"):
            getattr(self, key).update(getattr(other, key))
        for key in ("skeletons", "atlases", "textures", "reference_names", "layout", "externals"):
            getattr(self, key).update(getattr(other, key))

    def spine(self) -> dict:
        return {"skeletons": {name: {"version": get_skel_version(data),
                                    "data": base64.b64encode(data).decode("ascii")}
                              for name, data in self.skeletons.items()},
                "atlases": {name: data.decode("utf-8-sig", errors="replace") for name, data in self.atlases.items()},
                "textures": self.textures}


class Inspection:
    """A single analysis snapshot; never reused for execution or after input changes."""

    def __init__(self, asset_types: set[str] | None = None, *, header_only_missing: bool = False):
        self.header_only_missing = header_only_missing
        self.missing_cabs: set[str] = set()
        self.asset_types = frozenset(asset_types if asset_types is not None else
                                    {"Texture2D", "TextAsset", "Mesh", "AnimationClip"})
        self._revisions: dict[Path, tuple[int, ...]] = {}
        self._bundles: dict[Path, Bundle] = {}
        self._contents: dict[Path, Contents] = {}
        self._metadata: dict[Path, Contents] = {}
        self._keys: dict[Path, set[AssetKey]] = {}
        self._readable: dict[tuple[Path, tuple[Path, ...]], set[AssetKey]] = {}
        self._cabs: dict[Path, set[str]] = {}

    def cabs(self, path: Path) -> set[str]:
        self.check(path)
        if path not in self._cabs:
            self._cabs[path] = set(cab_names(path))
        self.check(path)
        return self._cabs[path]

    def check(self, path: Path) -> None:
        devmode.check_read(path)
        stat = path.stat()
        revision = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if self._revisions.setdefault(path, revision) != revision:
            raise ServiceError("entry_changed")

    def validate(self) -> None:
        for path in self._revisions:
            self.check(path)

    def bundle(self, path: Path, *, cache: bool = True) -> Bundle:
        self.check(path)
        bundle = self._bundles.get(path)
        if bundle is None:
            devmode.check_read(path)
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path.name)
            if cache:
                self._bundles[path] = bundle
        self.check(path)
        return bundle

    def inspect(self, path: Path, *, allow_unknown: bool = False) -> Contents:
        self.check(path)
        if path not in self._contents:
            self._contents[path] = inspect(path, allow_unknown=True, inspection=self)
        self.check(path)
        result = self._contents[path]
        if not allow_unknown and (not result.platforms or "UnknownPlatform" in result.platforms):
            raise ServiceError("mod_platform_unknown", file=path.name)
        return result

    def metadata(self, path: Path) -> Contents:
        self.check(path)
        if path not in self._metadata:
            self._metadata[path] = metadata(path, inspection=self)
        self.check(path)
        return self._metadata[path]

    def summary(self, path: Path) -> dict | None:
        """What this snapshot has read of a bundle, for error reports and logs: its build targets, Unity versions, own
        files (its CABs and their stream files, at most SUMMARY_CABS) and how many other CABs or stream files it
        references. Reads the serialized-file headers of a bundle already loaded; loads nothing else."""
        contents = self._contents.get(path)
        meta = self._metadata.get(path)
        if meta is None and path in self._bundles:
            try:
                meta = self.metadata(path)
            except (ServiceError, OSError, ValueError):
                meta = None
        parts = [part for part in (meta, contents) if part is not None]
        if not parts:
            return None
        own = set().union(*(part.files for part in parts))
        return {"platforms": sorted(set().union(*(part.platforms for part in parts))),
                "unity": sorted(set().union(*(part.versions for part in parts))),
                "cabs": sorted(name for name in (contents.files if contents else meta.files)
                               if not name.endswith((".bundle", ".backup")))[:SUMMARY_CABS],
                "references": len(contents.references - own) if contents else None}

    def keys(self, path: Path) -> set[AssetKey]:
        self.check(path)
        if path not in self._keys:
            types = REPLACEABLE_ASSET_TYPES if "ALL" in self.asset_types else {
                AssetType[name] for name in self.asset_types}
            self._keys[path] = self.bundle(path).get_asset_keys("name_type", types)
        self.check(path)
        return self._keys[path]

    def structure(self, path: Path) -> tuple:
        """What a bundle is as a release of a part, whatever its content: its serialized files, each one's objects
        (path id and type) and the files its objects point into, in order. A mod built on a part keeps them; a game
        update that rebuilt the part (objects added or dropped, another dependency) changes them, though the part
        keeps its name but for the CRC and its CABs. Read from the headers alone."""
        contents = self.metadata(path)
        return (frozenset(contents.files), tuple(sorted(contents.layout.items())),
                tuple(sorted(contents.externals.items())))

    def readable_keys(self, path: Path, *, targets: tuple[Path, ...] = ()) -> set[AssetKey]:
        self.check(path)
        for target in targets:
            self.check(target)
        cache_key = (path, targets)
        if cache_key not in self._readable:
            # Use the execution reader, including texture decoding and mesh stream resolution.
            # Source choices select conversion routes; they do not override serialized Unity metadata.
            scope = UpdateScope([self.bundle(target) for target in targets], "name_type", self.asset_types) if targets else None
            patch, _ = self.bundle(path).extract_patch(self.asset_types, "name_type", scope=scope)
            self._readable[cache_key] = (set(patch) - {"__mode__"}) | (scope.unchanged if scope else set())
        self.validate()
        return self._readable[cache_key]


def replacement_key(name: str) -> str:
    """Keep the complete release and part identity, ignoring only the final numeric CRC."""
    return re.sub(r"_[0-9]+\.bundle$", ".bundle", name.removesuffix(".backup"))


def metadata(path: Path, *, inspection: Inspection) -> Contents:
    """Validate UnityFS and serialized-file headers without reading individual assets."""
    bundle = inspection.bundle(path)
    if getattr(bundle.env.file, "signature", None) != "UnityFS":
        raise ServiceError("bundle_invalid", file=path.name)
    result = Contents(names={path.name.removesuffix(".backup")})

    def walk(file, name: str = "") -> None:
        if hasattr(file, "target_platform"):
            identity = basename(name)
            result.files.add(identity)
            result.layout[identity] = tuple(sorted((obj.path_id, obj.type.value) for obj in file.objects.values()))
            result.externals[identity] = tuple(basename(ref.path) for ref in file.externals)
            platform = file.target_platform
            result.platforms.add(getattr(platform, "name", str(platform)))
            version = re.match(r"^(\d+\.\d+)(?:\.|$)", file.unity_version)
            if not version:
                raise ServiceError("bundle_invalid", file=path.name)
            result.versions.add(version[1])
            return
        for child_name, child in getattr(file, "files", {}).items():
            walk(child, child_name)

    walk(bundle.env)
    if not result.platforms or result.platforms & {"UnknownPlatform", "NoTarget"}:
        raise ServiceError("mod_platform_unknown", file=path.name)
    return result


def inspect(path: Path, *, allow_unknown: bool = False, inspection: Inspection | None = None) -> Contents:
    """Read metadata and text assets, without decoding images or following external paths."""
    devmode.check_read(path)
    bundle = inspection.bundle(path) if inspection else Bundle.load(path)
    if bundle is None:
        raise ServiceError("bundle_invalid", file=path.name)
    result = Contents(names={path.name.removesuffix(".backup")})

    def reference(path: str) -> str:
        name = basename(path)
        result.references.add(name)
        result.reference_paths.add(path.replace("\\", "/"))
        result.reference_names[name] = path.replace("\\", "/").rsplit("/", 1)[-1]
        return name

    def walk(file, name: str = "") -> None:
        if name:
            result.files.add(basename(name))
        if hasattr(file, "target_platform"):
            platform = file.target_platform
            result.platforms.add(getattr(platform, "name", str(platform)))
            for ref in file.externals:
                if ref.path:
                    reference(ref.path)
            # SerializedFile.files contains ObjectReaders keyed by numeric path IDs.
            return
        for child_name, child in getattr(file, "files", {}).items():
            walk(child, child_name)

    walk(bundle.env)
    if not allow_unknown and (not result.platforms or "UnknownPlatform" in result.platforms):
        raise ServiceError("mod_platform_unknown", file=path.name)
    container = getattr(bundle.env, "container", {})
    result.names.update(container)
    result.paths.update(path.lower() for path in container)
    for obj in bundle.env.objects:
        if obj.type.name == "AssetBundle":
            data = obj.read()
            result.names.update(filter(None, [getattr(data, "m_Name", ""),
                getattr(data, "m_AssetBundleName", ""), *getattr(data, "m_Dependencies", [])]))
        if obj.type.name == "Texture2D":
            data = obj.read()
            result.textures[texture_key(data.m_Name)] = (data.m_Width, data.m_Height)
            stream = getattr(data, "m_StreamData", None)
            if stream and stream.path:
                result.streams.add(reference(stream.path))
        elif obj.type.name == "Mesh":
            stream = getattr(obj.read(), "m_StreamData", None)
            if stream and stream.path:
                result.streams.add(reference(stream.path))
        elif obj.type.name == "AudioClip":
            stream = getattr(obj.read(), "m_Resource", None)
            if stream and stream.m_Source and stream.m_Size:
                result.streams.add(reference(stream.m_Source))
        elif obj.type.name == "TextAsset":
            name = obj.peek_name() or ""
            if name.lower().endswith((".skel", ".atlas")):
                script = obj.read().m_Script
                raw = script if isinstance(script, bytes) else script.encode("utf-8", "surrogateescape")
                (result.skeletons if name.lower().endswith(".skel") else result.atlases)[name] = raw
    return result


def check_platform(contents: Contents, path: Path, expected: set[str]) -> None:
    if contents.platforms != expected:
        raise ServiceError("mod_platform_mismatch", file=path.name,
                           platform=", ".join(sorted(contents.platforms)), expected=", ".join(sorted(expected)))


def check_format(contents: Contents, path: Path, expected: Contents) -> None:
    """A copied or rebuilt bundle must use the target's serialized Unity format."""
    check_platform(contents, path, expected.platforms)
    if contents.versions != expected.versions:
        raise ServiceError("mod_unity_version_mismatch", file=path.name,
                           version=", ".join(sorted(contents.versions)),
                           expected=", ".join(sorted(expected.versions)))


def references_changed_objects(path: Path, changed: dict[str, set[int]], removed_cabs: set[str],
                               inspection: Inspection) -> bool:
    """Inspect external PPtrs without resolving them or following paths outside the install.

    External tables identify candidate consumers cheaply. Only those consumers need their
    objects read; an unreadable consumer cannot prove an object safe to remove or retype.
    """
    def pointers(value, external: dict[int, str]) -> bool:
        if isinstance(value, dict):
            file_id, path_id = value.get("m_FileID"), value.get("m_PathID")
            if path_id and path_id in changed.get(external.get(file_id), set()):
                return True
            return any(pointers(child, external) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(pointers(child, external) for child in value)
        return False

    def walk(file) -> bool:
        if hasattr(file, "target_platform"):
            external = {index: basename(ref.path) for index, ref in enumerate(file.externals, 1)}
            if removed_cabs.intersection(external.values()):
                return True
            if not changed.keys() & external.values():
                return False
            for obj in file.objects.values():
                try:
                    tree = obj.read_typetree()
                except Exception:
                    return True
                if pointers(tree, external):
                    return True
            return False
        return any(walk(child) for child in getattr(file, "files", {}).values())

    # A removal may need to inspect the whole install; do not retain every decompressed bundle.
    return walk(inspection.bundle(path, cache=False).env)


#: Unity's built-in resource files: externals of many official bundles, shipped with the player, never in a bundle
BUILTIN_RESOURCES = frozenset({"unity default resources", "unity_builtin_extra"})


def missing_parts(effective: Contents) -> set[str]:
    """Unresolved references and Spine pairs, before install lookup and baseline comparison."""
    missing = effective.references - effective.files - BUILTIN_RESOURCES
    missing.update(effective.streams - effective.files)
    for data in effective.atlases.values():
        try:
            missing.update(basename(page) for page in atlas_pages(data) if texture_key(page) not in effective.textures)
        except UnicodeDecodeError:
            pass  # The compatibility report identifies unreadable atlas data.
    atlas_keys = {basename(name).removesuffix(".atlas") for name in effective.atlases}
    skeleton_keys = {basename(name).removesuffix(".skel") for name in effective.skeletons}
    unpaired = skeleton_keys - atlas_keys
    # Match the preview's single remaining pair, whose names may differ in the game too.
    if not (len(unpaired) == 1 and len(atlas_keys - skeleton_keys) == 1):
        missing.update(name + ".atlas" for name in unpaired)
    return missing
