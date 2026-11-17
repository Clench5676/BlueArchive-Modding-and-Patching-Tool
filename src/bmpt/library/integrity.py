"""Read-only bundle preflight and Spine inputs for the web runtime's compatibility check."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core import devmode
from bmpt.engine import REPLACEABLE_ASSET_TYPES, AssetKey, AssetType, Bundle, get_skel_version
from ..core.errors import ServiceError
from .rebuild import UpdateScope
from ..gameassets.spine import atlas_pages
from ..gameassets.discovery import cab_names


#: The own CABs a bundle summary names (a bundle holds one serialized file, two with a scene)
SUMMARY_CABS = 10


def basename(name: str) -> str:
    """Return the lower-case filename after either Windows or POSIX path separators."""
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def texture_key(name: str) -> str:
    """Normalize a texture path to a lower-case filename without its PNG extension."""
    return basename(name).removesuffix(".png")


@dataclass
class Contents:
    """Collect bundle format, dependency, and Spine metadata without decoded image payloads."""

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
        """Union another bundle's metadata, letting its named assets replace matching entries."""
        for key in ("platforms", "versions", "names", "paths", "files", "references", "reference_paths", "streams"):
            getattr(self, key).update(getattr(other, key))
        for key in ("skeletons", "atlases", "textures", "reference_names", "layout", "externals"):
            getattr(self, key).update(getattr(other, key))

    def spine(self) -> dict:
        """Serialize skeletons as base64, atlases as text, and texture dimensions for the web checker."""
        return {
            "skeletons": {
                name: {"version": get_skel_version(data), "data": base64.b64encode(data).decode("ascii")}
                for name, data in self.skeletons.items()
            },
            "atlases": {name: data.decode("utf-8-sig", errors="replace") for name, data in self.atlases.items()},
            "textures": self.textures,
        }


class Inspection:
    """A single analysis snapshot; never reused for execution or after input changes."""

    def __init__(self, asset_types: set[str] | None = None, *, header_only_missing: bool = False):
        """Start a cached analysis snapshot with file-revision checks.

        Args:
            asset_types: Unity asset class names, or ALL; None selects textures, text,
                meshes, and animation clips for key and readability checks.
            header_only_missing: Whether dependency discovery must avoid reading assets
                while resolving missing CABs.
        """
        self.header_only_missing = header_only_missing
        self.missing_cabs: set[str] = set()
        self.asset_types = frozenset(
            asset_types if asset_types is not None else {"Texture2D", "TextAsset", "Mesh", "AnimationClip"}
        )
        self._revisions: dict[Path, tuple[int, ...]] = {}
        self._bundles: dict[Path, Bundle] = {}
        self._contents: dict[Path, Contents] = {}
        self._metadata: dict[Path, Contents] = {}
        self._keys: dict[Path, set[AssetKey]] = {}
        self._readable: dict[tuple[Path, tuple[Path, ...]], set[AssetKey]] = {}
        self._cabs: dict[Path, set[str]] = {}

    def cabs(self, path: Path) -> set[str]:
        """Read and cache a bundle's CAB names, refusing changes since the snapshot began."""
        self.check(path)
        if path not in self._cabs:
            self._cabs[path] = set(cab_names(path))
        self.check(path)
        return self._cabs[path]

    def check(self, path: Path) -> None:
        """Record a file's revision or reject a change within the snapshot.

        Args:
            path: Input file whose device, inode, size, and timestamps must remain fixed.

        Raises:
            ServiceError: The input changed (entry_changed) or development-mode access is refused.
        """
        devmode.check_read(path)
        stat = path.stat()
        revision = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if self._revisions.setdefault(path, revision) != revision:
            raise ServiceError("entry_changed")

    def validate(self) -> None:
        """Recheck every observed input and raise entry_changed if any revision differs."""
        for path in self._revisions:
            self.check(path)

    def bundle(self, path: Path, *, cache: bool = True) -> Bundle:
        """Load a bundle while checking its revision before and after the read.

        Args:
            path: Bundle whose decoded environment is loaded or reused within this snapshot.
            cache: Whether to retain a newly loaded bundle; an existing cached bundle is reused either way.

        Raises:
            ServiceError: The bundle cannot be loaded (bundle_invalid) or the input changed.
        """
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
        """Cache bundle metadata and text assets for compatibility analysis.

        Args:
            path: Bundle supplying cached dependency metadata and Spine text assets.
            allow_unknown: Whether absent or unknown target-platform metadata is acceptable.

        Raises:
            ServiceError: Loading, revision checks, or required platform identification fails.
        """
        self.check(path)
        if path not in self._contents:
            self._contents[path] = inspect(path, allow_unknown=True, inspection=self)
        self.check(path)
        result = self._contents[path]
        if not allow_unknown and (not result.platforms or "UnknownPlatform" in result.platforms):
            raise ServiceError("mod_platform_unknown", file=path.name)
        return result

    def metadata(self, path: Path) -> Contents:
        """Cache validated UnityFS and serialized-file headers without reading individual assets."""
        self.check(path)
        if path not in self._metadata:
            self._metadata[path] = metadata(path, inspection=self)
        self.check(path)
        return self._metadata[path]

    def summary(self, path: Path) -> dict | None:
        """Summarize already observed bundle metadata for error reports and logs.

        The summary includes build targets, Unity versions, own
        files (its CABs and their stream files, at most SUMMARY_CABS) and how many other CABs or stream files it
        references. Reads the serialized-file headers of a bundle already loaded; loads nothing else.

        Args:
            path: Bundle to summarize from this snapshot's caches.

        Returns:
            Format and reference details, or None if nothing has been read for this path.
        """
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
        return {
            "platforms": sorted(set().union(*(part.platforms for part in parts))),
            "unity": sorted(set().union(*(part.versions for part in parts))),
            "cabs": sorted(
                name
                for name in (contents.files if contents else meta.files)
                if not name.endswith((".bundle", ".backup"))
            )[:SUMMARY_CABS],
            "references": len(contents.references - own) if contents else None,
        }

    def keys(self, path: Path) -> set[AssetKey]:
        """Cache name-and-type keys for the snapshot's selected asset classes in a bundle."""
        self.check(path)
        if path not in self._keys:
            types = (
                REPLACEABLE_ASSET_TYPES if "ALL" in self.asset_types else {AssetType[name] for name in self.asset_types}
            )
            self._keys[path] = self.bundle(path).get_asset_keys("name_type", types)
        self.check(path)
        return self._keys[path]

    def structure(self, path: Path) -> tuple:
        """Identify a bundle's release structure from headers alone.

        The structure contains its serialized files, each one's objects
        (path id and type) and the files its objects point into, in order. A mod built on a part keeps them; a game
        update that rebuilt the part (objects added or dropped, another dependency) changes them, though the part
        keeps its name but for the CRC and its CABs.

        Args:
            path: Bundle whose structure is compared with an official part.

        Returns:
            Serialized filenames, object layouts, and ordered external-file tables.
        """
        contents = self.metadata(path)
        return (
            frozenset(contents.files),
            tuple(sorted(contents.layout.items())),
            tuple(sorted(contents.externals.items())),
        )

    def readable_keys(self, path: Path, *, targets: tuple[Path, ...] = ()) -> set[AssetKey]:
        """Find transferable keys with the execution reader, including image and stream decoding.

        Args:
            path: Source bundle whose selected assets must be readable.
            targets: Counterpart bundles limiting extraction to relevant assets; empty checks all selected assets.

        Returns:
            Extracted keys plus keys proven identical to their counterparts.
        """
        self.check(path)
        for target in targets:
            self.check(target)
        cache_key = (path, targets)
        if cache_key not in self._readable:
            # Use the execution reader, including texture decoding and mesh stream resolution.
            # Source choices select conversion routes; they do not override serialized Unity metadata.
            scope = (
                UpdateScope([self.bundle(target) for target in targets], "name_type", self.asset_types)
                if targets
                else None
            )
            patch, _ = self.bundle(path).extract_patch(self.asset_types, "name_type", scope=scope)
            self._readable[cache_key] = (set(patch) - {"__mode__"}) | (scope.unchanged if scope else set())
        self.validate()
        return self._readable[cache_key]


def replacement_key(name: str) -> str:
    """Keep the complete release and part identity, ignoring only the final numeric CRC."""
    return re.sub(r"_[0-9]+\.bundle$", ".bundle", name.removesuffix(".backup"))


def metadata(path: Path, *, inspection: Inspection) -> Contents:
    """Validate UnityFS and serialized-file headers without reading individual assets.

    Args:
        path: Bundle whose platform, Unity version, layout, and external tables are read.
        inspection: Analysis snapshot providing the bundle and guarding its input revision.

    Raises:
        ServiceError: The bundle or Unity version is invalid, or the platform is unknown or NoTarget.
    """
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
    """Read metadata and text assets without decoding images or following external paths.

    Args:
        path: Bundle supplying platform, dependency, skeleton, atlas, and texture-size metadata.
        allow_unknown: Whether metadata may be returned without an identified Unity target platform.
        inspection: Snapshot supplying cached bundles; None loads directly without revision tracking.

    Raises:
        ServiceError: The bundle cannot be loaded or required platform metadata is unknown.
    """
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
            result.names.update(
                filter(
                    None,
                    [
                        getattr(data, "m_Name", ""),
                        getattr(data, "m_AssetBundleName", ""),
                        *getattr(data, "m_Dependencies", []),
                    ],
                )
            )
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
    """Require a bundle's platform set to match its intended targets exactly.

    Args:
        contents: Inspected platform metadata of the candidate bundle.
        path: Candidate filename to include in a mismatch report.
        expected: Complete platform set required by the official targets.

    Raises:
        ServiceError: The sets differ (mod_platform_mismatch).
    """
    if contents.platforms != expected:
        raise ServiceError(
            "mod_platform_mismatch",
            file=path.name,
            platform=", ".join(sorted(contents.platforms)),
            expected=", ".join(sorted(expected)),
        )


def check_format(contents: Contents, path: Path, expected: Contents) -> None:
    """Require a copied or rebuilt bundle to use its target's serialized Unity format.

    Args:
        contents: Candidate bundle's inspected platform and Unity-version sets.
        path: Copied or rebuilt bundle named in format-mismatch errors.
        expected: Official target metadata defining the required format.

    Raises:
        ServiceError: Platforms or Unity versions differ from the target.
    """
    check_platform(contents, path, expected.platforms)
    if contents.versions != expected.versions:
        raise ServiceError(
            "mod_unity_version_mismatch",
            file=path.name,
            version=", ".join(sorted(contents.versions)),
            expected=", ".join(sorted(expected.versions)),
        )


def references_changed_objects(
    path: Path, changed: dict[str, set[int]], removed_cabs: set[str], inspection: Inspection
) -> bool:
    """Inspect external PPtrs without resolving them or following paths outside the install.

    External tables identify candidate consumers cheaply. Only those consumers need their
    objects read; an unreadable consumer cannot prove an object safe to remove or retype.

    Args:
        path: Candidate consumer bundle to inspect without retaining its decompressed contents.
        changed: CAB names mapped to path IDs removed or retyped by a replacement.
        removed_cabs: Serialized files removed in their entirety by the replacement.
        inspection: Analysis snapshot guarding input revisions and supplying the bundle reader.

    Returns:
        Whether a changed object or removed CAB is referenced, including unreadable candidate objects.
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
    """Find unresolved dependencies and Spine pairs before install lookup and baseline comparison.

    Args:
        effective: Merged metadata of the parts that would remain after applying a mod.

    Returns:
        Missing serialized files, streams, atlas pages, and skeleton atlas names,
        excluding Unity's built-in resources and the preview's single unmatched Spine pair.
    """
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
