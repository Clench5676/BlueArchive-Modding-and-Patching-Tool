"""Which official parts a set of mod files replaces or updates: the one rule every path asks.

Add and Import Mod's analysis, the import preview's complements, the dependency and compatibility checks, Enable and
Apply, and generic file slots delegate here, so a mod whose preview finds its official parts is not refused by
another path for its targets.

Order, per file: its exact name, its name without the CRC, a unique CAB (each makes the file the part's current
release: it replaces that part whole). The other files (an older release, another build's names) are judged per name
prefix by what a build guarantees: an asset path lives in exactly one bundle, while a core keeps its parts under
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
from typing import Any, Callable, Iterable, Mapping, Sequence

from ba_modding_toolkit.naming import parse_filename

from ..errors import ServiceError
from .integrity import replacement_key
from .judge import core_of, date_of, part_of

#: How a file found its targets
NAME = "name"                    # the exact name of a current part
NAME_CRC = "name_without_crc"    # the name of a current part, but for the CRC
CAB = "cab"                      # the CABs of a current part
ASSET_PATH = "asset_path"        # the parts holding its asset paths, every date of the core
ASSET_KEY = "asset_key"          # the parts sharing an asset name and type, every date of the core
PART = "part"                    # its part (prefix and resource type or number) at the newest date: assets unreadable
NOTHING = "none"                 # readable assets that no part holds, or no part at all

#: Methods by which a file replaces a part whole (the part's current release)
WHOLE = frozenset({NAME, NAME_CRC, CAB})
#: Methods of the evidence that names a part as it is now, whatever the file's name
BY_ASSETS = frozenset({ASSET_PATH, ASSET_KEY})


def _nothing(path: Path) -> set:
    return set()


@dataclass(frozen=True)
class Readers:
    """What the resolver may read of a file: its CABs, asset paths and asset names and types. Each returns an empty
    set for a file it cannot read; a target is read from its official copy where there is one."""
    cabs: Callable[[Path], Iterable[str]] = _nothing
    paths: Callable[[Path], set[str]] = _nothing
    keys: Callable[[Path], set] = _nothing


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


def current(files: Mapping[str, Path], targets: Sequence[Path],
            cabs: Callable[[Path], Iterable[str]] = _nothing) -> tuple[dict[Path, Path], dict[str, str]]:
    """The files that are a part's current release, {target: source}, and the method each was found by.

    A name or CAB match proves the release current; coexisting dates of a part stay apart. Two files on one part, or
    a name or CAB that several parts share without a part number to tell them apart, are ambiguous."""
    result: dict[Path, Path] = {}
    methods: dict[str, str] = {}
    for name, source in files.items():
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
        result[matches[0]] = source
        methods[name] = method
    return result, methods


def by_assets(sources: Sequence[Path], targets: Sequence[Path], readers: Readers) -> tuple[str, list[Path]] | None:
    """The parts among ``targets`` (every part of the core, whatever its date) that files of an older release or
    another build update: the parts holding any of their asset paths, else the parts sharing an asset name and type;
    None when the sources show neither (nothing to judge by), an empty list when no part holds what they show."""
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
    """The cheap rule, for files whose assets cannot be read: each file's part (prefix and resource type or number)
    at the newest date it has among ``targets``."""
    found: list[Path] = []
    for name in names:
        same = [target for target in targets if part_of(target.name) == part_of(name)]
        if same:
            newest = max(date_of(target.name) for target in same)
            found.extend(target for target in same if date_of(target.name) == newest and target not in found)
    return found


def sharing_keys(sources: Sequence[Path], candidates: Sequence[Path], keys: Callable[[Path], set]) -> list[Path]:
    """The candidates sharing an asset name and type with the sources: those an asset transfer by name and type
    (conversions and rebuilds) writes into."""
    wanted = set().union(*(keys(source) for source in sources))
    return [candidate for candidate in candidates if wanted & keys(candidate)]


class Resolution:
    """What a set of mod files replaces and updates among the parts of its core. The name and CAB stage runs at
    once; the asset stage reads only when asked for (``groups`` and everything built on it)."""

    def __init__(self, files: Mapping[str, Path], targets: Sequence[Path], readers: Readers = NAMES_ONLY, *,
                 whole: bool = True):
        self.files = dict(files)
        self.targets = list(targets)
        self.readers = readers
        if whole:
            self.current, self._methods = current(self.files, self.targets, readers.cabs)
        else:
            self.current, self._methods = {}, {}
        sources = set(self.current.values())
        self.older = {name: path for name, path in self.files.items() if path not in sources}
        self._groups: list[Group] | None = None

    @property
    def rest(self) -> list[Path]:
        """The parts no file replaces whole: what the other files may update."""
        return [target for target in self.targets if target not in self.current]

    @property
    def groups(self) -> list[Group]:
        """The older files per name prefix and core, with the parts they update."""
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
        """How each file found its targets, in the files' order."""
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
        """Every part the files write: those they replace whole and those they update, in the targets' order."""
        touched = set(self.current).union(*(group.targets for group in self.groups))
        return [target for target in self.targets if target in touched]

    @property
    def replaced(self) -> set[Path]:
        """The parts the files stand in for, which a preview drops: those they replace whole, those whose every asset
        path they carry, and, for files whose assets cannot be read, their parts. A part they update in part keeps
        its other assets, so a preview keeps it under the mod's files."""
        result = set(self.current)
        carried = set().union(*(self.readers.paths(path) for path in self.older.values())) if self.older else set()
        for group in self.groups:
            if group.method == PART:
                result.update(group.targets)
            elif carried:
                result.update(target for target in group.targets
                              if (held := self.readers.paths(target)) and held <= carried)
        return result

    @property
    def unresolved(self) -> list[str]:
        """The files that found no part."""
        return [name for group in self.groups if group.method == NOTHING for name in group.names]

    @property
    def unplaced(self) -> list[str]:
        """The files that neither a name, a CAB nor their asset paths place, and whose part (prefix and resource type
        or number) no target has: another server's files are accepted only where something places them."""
        placed = {name for name, match in self.matches().items() if match.method in WHOLE or match.method == ASSET_PATH}
        return [name for name in self.files if name not in placed
                and not any(part_of(target.name) == part_of(name) for target in self.targets)]

    def sharing_keys(self, among: Sequence[Path] | None = None) -> list[Path]:
        """The parts (``among``, else every part the files write) sharing an asset name and type with the files."""
        return sharing_keys(list(self.files.values()), self.updated if among is None else list(among),
                            self.readers.keys)

    def evidence(self) -> list[dict[str, Any]]:
        """Per file: how it matched, the official parts it found and their dates, for error reports. The asset stage
        is reported only when it has run."""
        rows = []
        stages = self.matches() if self._groups is not None else {
            name: Match(self._methods[name], [target]) for target, source in self.current.items()
            for name, path in self.files.items() if path == source}
        for name in self.files:
            match = stages.get(name)
            if match is None:
                rows.append({"file": name, "method": None, "targets": [], "dates": []})
                continue
            rows.append({"file": name, "method": match.method, "targets": sorted(target.name for target in match.targets),
                         "dates": sorted({date_of(target.name) for target in match.targets})})
        return rows


def resolve(files: Mapping[str, Path], targets: Sequence[Path], readers: Readers = NAMES_ONLY, *,
            whole: bool = True) -> Resolution:
    """The resolution of a set of mod files among the parts of its core; ``whole=False`` treats every file as an
    older release (no name or CAB stage)."""
    return Resolution(files, targets, readers, whole=whole)


# Generic file slots (kind "other"): every bundle of a prefix is a candidate, not only the parts of one core

COLLECTION = "uis-01_common-14_charactercollect-"


def generic(files: Mapping[str, Path], available: Sequence[Path], readers: Readers, *,
            container_keys: Callable[[Path, bool], set]) -> dict[str, Match]:
    """The official files each generic mod file replaces or updates: a collection batch's exact name; a current
    release (name, name without CRC, CAB); the files of its prefix holding its asset paths, over every date (an asset
    path lives in one bundle of a build: this tells apart the bundles of a group packed one asset per bundle, whose
    names differ only in the CRC); else the newest file of each slot of its prefix with its exact name, or sharing a
    container, name and type (``container_keys(path, source)`` reads a mod file, or a candidate from its official
    copy)."""
    result: dict[str, Match] = {}
    assets = replace(readers, keys=_nothing)
    for name, source in files.items():
        if name.lower().startswith(COLLECTION):
            exact = [path for path in available if path.name == name]
            if exact:
                result[name] = Match(NAME, exact)
                continue
        found, methods = current({name: source}, available, readers.cabs)
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
    """The file each generic slot holds now: its newest date. Two files of a slot under that date conflict."""
    targets: list[Path] = []
    for slot in sorted(set(slots)):
        newest = max(date_of(path.name) for path in index[slot])
        latest = [path for path in index[slot] if date_of(path.name) == newest]
        if len(latest) != 1:
            raise ServiceError("game_file_conflict", file=latest[0])
        targets.extend(latest)
    return sorted(targets)


def same_prefix(sources: Iterable[Path], target: Path) -> list[Path]:
    """The generic mod files an update of ``target`` reads: those of its name prefix (the update itself transfers
    only the assets the target holds, by container, name and type)."""
    prefix = parse_filename(target.name).prefix
    return [path for path in sources if parse_filename(path.name).prefix == prefix]
