"""Which official parts a set of mod files replaces or updates: the one rule every path asks.

Add and Import Mod's analysis, the import preview's complements, the dependency and compatibility checks, Enable and
Apply, and generic file slots delegate here, so a mod whose preview finds its official parts is not refused by
another path for its targets.

Order, per file: its exact name, its name without the CRC, a unique CAB (each makes the file the part's current
release: it replaces that part whole). A name without the CRC or a CAB only names the part: a game update rebuilds a
part under its name and CABs, so such a file is the current release only when the readers find it built on the part
as it is now (``Readers.release``); otherwise it is a file of an older release. The other files (an older release,
another build's names) are judged per name prefix by what a build guarantees: an asset path lives in exactly one
bundle, while a core keeps its parts under
several dates and a part number only counts its own dated group. So they update the parts holding their asset paths
over every date of the core, else the parts sharing an asset name and type; the part (prefix and resource type or
number) at its newest date only for files whose assets cannot be read. A file whose readable assets no part holds
resolves to nothing.

The readers are the caller's (``Readers``): they read a target from its official copy where there is one, and give
no evidence for a file they cannot read.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

from bmpt.engine import parse_filename

from ..errors import ServiceError
from .integrity import replacement_key
from .judge import core_of, date_of, part_of

#: How a file found its targets
NAME = "name"  # the exact name of a current part
NAME_CRC = "name_without_crc"  # the name of a current part, but for the CRC
CAB = "cab"  # the CABs of a current part
ASSET_PATH = "asset_path"  # the parts holding its asset paths, every date of the core
ASSET_KEY = "asset_key"  # the parts sharing an asset name and type, every date of the core
PART = "part"  # its part (prefix and resource type or number) at the newest date: assets unreadable
NOTHING = "none"  # readable assets that no part holds, or no part at all

#: Methods by which a file replaces a part whole (the part's current release)
WHOLE = frozenset({NAME, NAME_CRC, CAB})
#: Methods of the evidence that names a part as it is now, whatever the file's name
BY_ASSETS = frozenset({ASSET_PATH, ASSET_KEY})


def _nothing(path: Path) -> set:
    return set()


def _same_release(source: Path, target: Path) -> bool:
    return True


@dataclass(frozen=True)
class Readers:
    """Supply file evidence to the resolver: CABs, asset paths, and asset names and types.

    Each reader returns an empty set for a file it cannot read; a target is read from
    its official copy where there is one.
    ``release(source, target)`` says whether a file that names a part without its CRC, or by its CABs, was built on
    that part as it is now; the names alone (no reader) take it as such.
    """

    cabs: Callable[[Path], Iterable[str]] = _nothing
    paths: Callable[[Path], set[str]] = _nothing
    keys: Callable[[Path], set] = _nothing
    release: Callable[[Path, Path], bool] = _same_release


#: Names only: the cheap rule of a preview before its files are read
NAMES_ONLY = Readers()


@dataclass
class Match:
    """How one file found its targets."""

    method: str
    targets: list[Path]


@dataclass
class Group:
    """The older files of one name prefix and core, and the parts they update."""

    names: list[str]
    sources: list[Path]
    method: str
    targets: list[Path] = field(default_factory=list)


def current(
    files: Mapping[str, Path],
    targets: Sequence[Path],
    cabs: Callable[[Path], Iterable[str]] = _nothing,
    release: Callable[[Path, Path], bool] = _same_release,
    *,
    older: Collection[Path] = (),
) -> tuple[dict[Path, Path], dict[str, str]]:
    """Find files that replace current parts whole, with the evidence for each match.

    Sources in older are left to the asset stage regardless of their names.

    An exact name proves the release current; a name without the CRC or a CAB match does when ``release`` finds the
    file built on the part as it is now (a game update rebuilds a part under its name and CABs: such a file of the
    release before is left to the asset stage). Coexisting dates of a part stay apart. Two files on one part, or a
    name or CAB that several parts share without a part number to tell them apart, are ambiguous.

    Args:
        files: Source paths indexed by the bundle filenames used for matching.
        targets: Official parts available for replacement, including coexisting dates.
        cabs: Reads a file's CAB names; an empty result gives no CAB evidence.
        release: Confirms that a non-exact match was built on the target's current structure.
        older: Source paths known to require asset transfer regardless of their names.

    Returns:
        Target-to-source replacements and source-filename-to-matching-method evidence.

    Raises:
        ServiceError: Several sources or targets claim one replacement (mod_target_ambiguous).
    """
    result: dict[Path, Path] = {}
    methods: dict[str, str] = {}
    for name, source in files.items():
        if source in older:
            continue
        method = NAME
        matches = [path for path in targets if path.name == name]
        if len(matches) != 1:
            method = NAME_CRC
            matches = [path for path in targets if replacement_key(path.name) == replacement_key(name)]
        ambiguous = len(matches) > 1
        if len(matches) != 1:
            method = CAB
            held = set(cabs(source)) if targets else set()
            matches = [path for path in targets if held and set(cabs(path)) == held]
            ambiguous |= len(matches) > 1
        if not matches and not ambiguous:
            continue
        if len(matches) != 1:
            matches = [path for path in targets if part_of(path.name) == part_of(name)]
        if len(matches) != 1 or matches[0] in result:
            raise ServiceError("mod_target_ambiguous", file=name)
        if method != NAME and not release(source, matches[0]):
            continue
        result[matches[0]] = source
        methods[name] = method
    return result, methods


def by_assets(sources: Sequence[Path], targets: Sequence[Path], readers: Readers) -> tuple[str, list[Path]] | None:
    """Find target parts holding source asset paths, falling back to asset names and types.

    Args:
        sources: Older-release or other-build files whose assets will be transferred.
        targets: All candidate parts of the core, across every date.
        readers: Evidence readers using official copies for targets when available.

    Returns:
        Matching method and parts, an empty parts list when readable assets find no match,
        or None when neither source paths nor keys provide evidence.
    """
    wanted = set().union(*(readers.paths(source) for source in sources))
    if wanted:
        found = [target for target in targets if wanted & readers.paths(target)]
        if found:
            return ASSET_PATH, found
    wanted_keys = set().union(*(readers.keys(source) for source in sources))
    if not wanted and not wanted_keys:
        return None
    if wanted_keys:
        return ASSET_KEY, [target for target in targets if wanted_keys & readers.keys(target)]
    return ASSET_PATH, []


def by_part(names: Iterable[str], targets: Sequence[Path]) -> list[Path]:
    """Match unreadable files to each part's newest target date.

    Args:
        names: Source bundle filenames identifying parts by prefix and resource type or number.
        targets: Candidate parts whose dates determine the newest release.
    """
    found: list[Path] = []
    for name in names:
        same = [target for target in targets if part_of(target.name) == part_of(name)]
        if same:
            newest = max(date_of(target.name) for target in same)
            found.extend(target for target in same if date_of(target.name) == newest and target not in found)
    return found


def sharing_keys(sources: Sequence[Path], candidates: Sequence[Path], keys: Callable[[Path], set]) -> list[Path]:
    """Find candidates an asset transfer by name and type can write into.

    Args:
        sources: Bundles supplying the assets for conversion or rebuilding.
        candidates: Potential destination bundles, returned in their existing order.
        keys: Reads the asset name-and-type keys held by a bundle.
    """
    wanted = set().union(*(keys(source) for source in sources))
    return [candidate for candidate in candidates if wanted & keys(candidate)]


class Resolution:
    """Resolve a mod's whole replacements immediately and its asset transfers lazily.

    The asset stage reads only when requested through groups or a result built on it.
    """

    def __init__(
        self,
        files: Mapping[str, Path],
        targets: Sequence[Path],
        readers: Readers = NAMES_ONLY,
        *,
        whole: bool = True,
        older: Collection[Path] = (),
    ):
        """Match names and CABs, deferring asset reads until their results are requested.

        Args:
            files: Source paths indexed by bundle filename.
            targets: Candidate official parts of the mod's core.
            readers: Evidence readers; the default uses filenames alone.
            whole: Whether files may replace parts whole; False sends all files to asset transfer.
            older: Sources known to require asset transfer despite a matching name or CAB.
        """
        self.files = dict(files)
        self.targets = list(targets)
        self.readers = readers
        if whole:
            self.current, self._methods = current(self.files, self.targets, readers.cabs, readers.release, older=older)
        else:
            self.current, self._methods = {}, {}
        sources = set(self.current.values())
        self.older = {name: path for name, path in self.files.items() if path not in sources}
        self._groups: list[Group] | None = None

    @property
    def rest(self) -> list[Path]:
        """Return parts available for asset transfer because no file replaces them whole."""
        return [target for target in self.targets if target not in self.current]

    @property
    def groups(self) -> list[Group]:
        """Group older files by name prefix and core with the parts they update."""
        if self._groups is None:
            grouped: dict[tuple[str, str], list[str]] = {}
            for name in self.older:
                parsed = parse_filename(name)
                grouped.setdefault((parsed.prefix, parsed.core), []).append(name)
            rest, groups = self.rest, []
            for names in grouped.values():
                sources = [self.older[name] for name in names]
                found = by_assets(sources, rest, self.readers) if rest else None
                if found is None:
                    targets = by_part(names, rest)
                    groups.append(Group(names, sources, PART if targets else NOTHING, targets))
                else:
                    method, targets = found
                    groups.append(Group(names, sources, method if targets else NOTHING, targets))
            self._groups = groups
        return self._groups

    def matches(self) -> dict[str, Match]:
        """Return each file's matching method and targets in source-file order."""
        by_name: dict[str, Match] = {}
        for target, source in self.current.items():
            name = next(name for name, path in self.files.items() if path == source)
            by_name[name] = Match(self._methods[name], [target])
        for group in self.groups:
            for name in group.names:
                by_name[name] = Match(group.method, list(group.targets))
        return {name: by_name[name] for name in self.files if name in by_name}

    @property
    def updated(self) -> list[Path]:
        """Return whole replacements and asset-transfer destinations in target order."""
        touched = set(self.current).union(*(group.targets for group in self.groups))
        return [target for target in self.targets if target in touched]

    @property
    def replaced(self) -> set[Path]:
        """Find parts a preview can omit because the mod stands in for them.

        Omit whole replacements, parts whose asset paths are all carried by the mod,
        and matching parts for unreadable sources. Partially updated parts remain
        underneath the mod so their other assets stay available.
        """
        result = set(self.current)
        carried = set().union(*(self.readers.paths(path) for path in self.older.values())) if self.older else set()
        for group in self.groups:
            if group.method == PART:
                result.update(group.targets)
            elif carried:
                result.update(
                    target for target in group.targets if (held := self.readers.paths(target)) and held <= carried
                )
        return result

    @property
    def unresolved(self) -> list[str]:
        """Return source filenames that found no target part."""
        return [name for group in self.groups if group.method == NOTHING for name in group.names]

    @property
    def unplaced(self) -> list[str]:
        """Find files unsupported by names, CABs, asset paths, or a matching target part.

        Another server's files are accepted only when one of these forms of evidence places them.
        """
        placed = {name for name, match in self.matches().items() if match.method in WHOLE or match.method == ASSET_PATH}
        return [
            name
            for name in self.files
            if name not in placed and not any(part_of(target.name) == part_of(name) for target in self.targets)
        ]

    def sharing_keys(self, among: Sequence[Path] | None = None) -> list[Path]:
        """Find parts sharing asset name-and-type keys with the source files.

        Args:
            among: Parts to search; None searches every part the resolution writes.
        """
        return sharing_keys(
            list(self.files.values()), self.updated if among is None else list(among), self.readers.keys
        )

    def evidence(self) -> list[dict[str, Any]]:
        """Report each file's method, official target names, and dates without forcing asset reads."""
        rows = []
        stages = (
            self.matches()
            if self._groups is not None
            else {
                name: Match(self._methods[name], [target])
                for target, source in self.current.items()
                for name, path in self.files.items()
                if path == source
            }
        )
        for name in self.files:
            match = stages.get(name)
            if match is None:
                rows.append({"file": name, "method": None, "targets": [], "dates": []})
                continue
            rows.append(
                {
                    "file": name,
                    "method": match.method,
                    "targets": sorted(target.name for target in match.targets),
                    "dates": sorted({date_of(target.name) for target in match.targets}),
                }
            )
        return rows


def resolve(
    files: Mapping[str, Path],
    targets: Sequence[Path],
    readers: Readers = NAMES_ONLY,
    *,
    whole: bool = True,
    older: Collection[Path] = (),
) -> Resolution:
    """Resolve a set of mod files among the official parts of its core.

    Args:
        files: Mod bundle basenames mapped to their on-disk paths for all matching stages.
        targets: Candidate official parts, including coexisting dates.
        readers: Evidence callbacks for CAB, asset, and release matching; omitted readers leave only name evidence.
        whole: Whether to run the whole-file name and CAB stage; False treats all sources as older releases.
        older: Sources that must use asset transfer even when whole-file matching is enabled.
    """
    return Resolution(files, targets, readers, whole=whole, older=older)


# Generic file slots (kind "other"): every bundle of a prefix is a candidate, not only the parts of one core

COLLECTION = "uis-01_common-14_charactercollect-"


def generic(
    files: Mapping[str, Path],
    available: Sequence[Path],
    readers: Readers,
    *,
    container_keys: Callable[[Path, bool], set],
) -> dict[str, Match]:
    """Resolve generic mod files against official bundles across their filename prefixes.

    Matching considers a Collection batch's exact name; a current
    release (name, name without CRC, CAB); the files of its prefix holding its asset paths, over every date (an asset
    path lives in one bundle of a build: this tells apart the bundles of a group packed one asset per bundle, whose
    names differ only in the CRC); else the newest file of each slot of its prefix with its exact name, or sharing a
    container, name and type (``container_keys(path, source)`` reads a mod file, or a candidate from its official
    copy).

    Args:
        files: Generic source paths indexed by bundle filename.
        available: All candidate official bundles, including every date and slot.
        readers: CAB, asset-path, and release-structure readers for whole and asset matching.
        container_keys: Reads container/name/type keys; its second argument is True for
            a source and False for a candidate read from an official copy.

    Returns:
        Matching evidence and destination parts indexed by source filename.

    Raises:
        ServiceError: A target is missing, ambiguous, or conflicts with another file at the same date.
    """
    result: dict[str, Match] = {}
    assets = replace(readers, keys=_nothing)
    for name, source in files.items():
        if name.lower().startswith(COLLECTION):
            exact = [path for path in available if path.name == name]
            if exact:
                result[name] = Match(NAME, exact)
                continue
        found, methods = current({name: source}, available, readers.cabs, readers.release)
        if found:
            result[name] = Match(methods[name], list(found))
            continue
        prefix = parse_filename(name).prefix
        candidates = [path for path in available if parse_filename(path.name).prefix == prefix]
        held = by_assets([source], candidates, assets) if candidates else None
        if held and held[1]:
            result[name] = Match(held[0], held[1])
            continue
        latest: dict[str, Path] = {}
        for path in candidates:
            slot = core_of(path.name)
            if slot not in latest or date_of(path.name) > date_of(latest[slot].name):
                latest[slot] = path
            elif date_of(path.name) == date_of(latest[slot].name) and path != latest[slot]:
                raise ServiceError("game_file_conflict", file=name)
        candidates = list(latest.values())
        if not candidates:
            raise ServiceError("game_file_not_found", file=name)
        exact = [path for path in candidates if path.name == name]
        if exact:
            result[name] = Match(NAME, exact)
            continue
        wanted = container_keys(source, True)
        matched = [path for path in candidates if wanted & container_keys(path, False)]
        if not matched:
            raise ServiceError("game_file_not_found", file=name)
        result[name] = Match(ASSET_KEY, matched)
    return result


def slot_files(index: Mapping[str, Sequence[Path]], slots: Iterable[str]) -> list[Path]:
    """Select the single newest file of each generic slot.

    Args:
        index: Available bundle paths indexed by generic slot key, each with at least one file.
        slots: Slot keys to resolve, deduplicated before selection.

    Raises:
        ServiceError: Several files share a slot's newest date (game_file_conflict).
    """
    targets: list[Path] = []
    for slot in sorted(set(slots)):
        newest = max(date_of(path.name) for path in index[slot])
        latest = [path for path in index[slot] if date_of(path.name) == newest]
        if len(latest) != 1:
            raise ServiceError("game_file_conflict", file=latest[0])
        targets.extend(latest)
    return sorted(targets)


def same_prefix(sources: Iterable[Path], target: Path) -> list[Path]:
    """Choose generic sources sharing a target's filename prefix.

    The update transfers only assets held by the target, matched by container, name, and type.

    Args:
        sources: Generic mod bundles available to the update.
        target: Destination bundle whose parsed prefix limits the sources.
    """
    prefix = parse_filename(target.name).prefix
    return [path for path in sources if parse_filename(path.name).prefix == prefix]
