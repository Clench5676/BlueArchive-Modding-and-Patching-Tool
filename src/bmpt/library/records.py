"""JSON-ready results returned by the mod library."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ResourceType = Literal["portrait", "memorialLobby", "chibi", "other"]
CRCStatus = Literal["ok", "fixed", "needs_fix"]


def stored_target(record: dict[str, Any]) -> Any:
    """Read the identity of a stored Mod from its target field."""
    return record.get("generic_target", record.get("target_alt"))


class JsonResult:
    """Convert result dataclasses to plain JSON values."""

    def to_json(self) -> dict[str, Any]:
        """Return a dictionary with absolute paths represented as strings."""

        def convert(value: Any) -> Any:
            if isinstance(value, Path):
                return str(value.absolute())
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(item) for item in value]
            return value

        return convert(asdict(self))


@dataclass(frozen=True)
class ModEntry(JsonResult):
    """A user's mod and the game alt it targets."""

    id: str
    file: Path
    name: str
    target: str
    resource_type: ResourceType
    added: str
    fresh: bool
    size: int
    date: str
    crc: str
    core: str = ""  # the lower-case core of its files (a memorial lobby mod belongs to one memorial lobby core)
    repair_source_id: str = ""
    repaired_copy_id: str = ""
    user_added: bool = False  # explicit add, rather than adoption, recovery or re-filing
    alias: str = ""
    base_name: str = ""  # stored name before aliases and localized repair suffixes
    optimized_from: str = ""  # an Optimize Physics copy: the costume it was built from, "alt:<key>"
    # a memorial lobby camera: {source, number, scale, offset}; listed as a memorial lobby choice of its alt
    camera: dict | None = None


class AddResult(list[ModEntry]):
    """New entries, with matched library entries and sources recognized in game files."""

    def __init__(self) -> None:
        """Start an empty import result with separate lists for matched and failed entries."""
        super().__init__()
        self.duplicates: list[ModEntry] = []
        self.installed: list[ModEntry] = []
        self.applied: list[ModEntry] = []
        self.configured: list[ModEntry] = []
        self.failed: list[dict] = []
        self.compatibility: dict[str, dict] = {}
        self.kept_current = False
        self.incomplete: list[str] = []

    @property
    def added(self) -> list[ModEntry]:
        """Return the newly added entries as a plain list."""
        return list(self)

    def to_json(self) -> dict[str, Any]:
        """Serialize added, matched, applied, and failed entries with their import metadata."""
        return {
            "added": [entry.to_json() for entry in self],
            "duplicates": [entry.to_json() for entry in self.duplicates],
            "installed": [entry.to_json() for entry in self.installed],
            "applied": [entry.to_json() for entry in self.applied],
            "configured": [entry.to_json() for entry in self.configured],
            "failed": self.failed,
            "compatibility": self.compatibility,
            "kept_current": self.kept_current,
            "incomplete": self.incomplete,
        }


@dataclass(frozen=True)
class DeleteResult(JsonResult):
    """Deleted sources and any game entries still awaiting their configured source."""

    done: list[dict]
    failed: list[dict]
    dropped: list[dict]
    cores: list[str]
    resource_types: list[str]


CopyKind = Literal["game", "kept", "backup", "elsewhere"]


@dataclass(frozen=True)
class GameFileState(JsonResult):
    """Describe one bundle part's installed source and restore availability.

    The cheap judgement uses size, date, and header without reading the whole file.
    """

    file: Path
    core: str
    resource_type: ResourceType
    applied: str
    crc: CRCStatus
    has_kept_official: bool
    date: str
    size: int
    # where an official copy of this file is: the file itself, the library's kept original, the classic GUI's
    # .backup beside it, a file of its name in another game folder; None: nowhere (overwritten)
    copy: CopyKind | None = None
    restorable: bool = False  # copy is not None
    official: bool = False  # the file holds official bytes (the front end's source rule leaves it out)
    # what verifies that official copy: "steam" (Steam's file list), "crc" (the CRC rule alone, no Steam list
    # names it); None for an official file or without a copy. A kept original a Steam list rejected is no copy at all.
    checked: Literal["steam", "crc"] | None = None


@dataclass(frozen=True)
class AltState(JsonResult):
    """Bundle states for both the portrait and memorial lobby of an alt.

    ``applied`` is ``unknown`` when the parts have different sources. Consumers
    displaying separate tabs should use the individual file states.
    """

    alt: str
    files: list[GameFileState]
    applied: str
    crc: CRCStatus
    backup: bool


@dataclass(frozen=True)
class ApplyResult(JsonResult):
    """Files installed or restored by one action."""

    alt: str
    source: str
    files: list[Path]
    unchanged: bool = False
    deferred: bool = False  # mods are off: the source is configured and applied when mods are on
    kept_current: bool = False
    incomplete: bool = False


@dataclass(frozen=True)
class ThumbnailApplyResult(ApplyResult):
    """A thumbnail changes a shared Collection slot, rather than the student's Spine core."""

    cores: list[str] = field(default_factory=list)
    resource_types: list[str] = field(default_factory=lambda: ["other"])


@dataclass(frozen=True)
class DefaultThumbnailsResult(JsonResult):
    """Generated default thumbnails applied to shared Collection slots, one write per slot."""

    alts: list[str] = field(default_factory=list)  # App thumbnail now the alt's generated default
    cores: list[str] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)  # {"alts", "code"} per write that failed
    left: list[str] = field(default_factory=list)  # alts handed to the job queued after a higher class's
    resource_types: list[str] = field(default_factory=lambda: ["other"])


@dataclass(frozen=True)
class UpdateResult(JsonResult):
    """A rebuilt library mod and its retained previous version."""

    mod: ModEntry
    files: list[Path]
    previous: list[Path]
    unchanged: bool = False
    # what the official rig has that the mod as it stands lacks (atlas_remap.rig_gains); empty when nothing
    rig: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AssetDifference(JsonResult):
    """Raw serialized asset comparison, including external stream payloads."""

    name: str
    type: str
    status: Literal["changed", "same", "added", "removed"]
    mod_size: int | None
    official_size: int | None


@dataclass(frozen=True)
class CompareResult(JsonResult):
    """Asset differences between a mod and the current official bundles."""

    mod_id: str
    assets: list[AssetDifference]


@dataclass(frozen=True)
class RemapResult(JsonResult):
    """A separately stored repaint, its region report and comparison with the source mod."""

    mod: ModEntry
    report: dict
    compare: CompareResult

    @property
    def added(self) -> list[ModEntry]:
        """Return the separately stored repaint as a single added entry."""
        return [self.mod]


@dataclass(frozen=True)
class Pin(JsonResult):
    """Fix a game install and server for one call or job.

    The install key is the resolved install root; dirs holds its resource folders.
    """

    key: str
    root: Path
    dirs: tuple[Path, ...]
    server: str

    @property
    def id(self) -> str:
        """Return the install's URL name: the first 12 hex digits of its key's SHA-1."""
        import hashlib

        return hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:12]


EntryStateName = Literal[
    "damaged", "missing", "untracked", "changed", "source_missing", "updated", "reset", "ok", "pending"
]


@dataclass(frozen=True)
class EntryState(JsonResult):
    """Describe an asset slot's carried source, configuration, and switch outcome.

    Each slot belongs to one alt in one install. A chibi slot combines its prefab
    and its own dependency cores.
    """

    install: str  # the install key
    core: str  # lower-case core
    alt: str  # the alt key of the core in its own install
    names: dict | None  # the alt's shipped names when the current catalog does not map the core, else None
    resource_type: ResourceType
    source: str  # what the files carry: "official", "mod:<id>", "alt:<key>", "removed", "unknown", "damaged"
    configured: str | None  # the configured source, None for official
    state: EntryStateName
    overwritten: bool  # a non-official file with no official copy anywhere
    fixed: bool  # the parts the library does not know have trailing bytes (CRC-fixed)
    size: int  # bytes of the non-official parts not in the library yet
    files: list[GameFileState]
    dismissed: bool = False  # its prompt was dismissed for these very files

    @property
    def key(self) -> tuple[str, str]:
        """Return the install and core identifying this slot."""
        return self.install, self.core


@dataclass(frozen=True)
class ScanResult(JsonResult):
    """The entries of every known install, with counts per install. ``stale``: the library wrote meanwhile."""

    entries: list[EntryState]
    counts: dict[str, dict[str, int]]
    stale: bool = False


@dataclass(frozen=True)
class AdoptResult(JsonResult):
    """Mods added from the game files: the mods (new or matched) and the entries that failed or changed."""

    added: list[ModEntry]
    failed: list[dict]
    skipped: list[dict]
    cores: list[str]
    resource_types: list[str]


@dataclass(frozen=True)
class ToggleResult(JsonResult):
    """What a toggle, Retry or Apply Mod Configuration did, entry by entry ({install, core, alt, kind})."""

    enabled: bool
    done: list[dict]
    adopted: list[dict]
    failed: list[dict]
    stuck: list[dict]
    left: list[dict]
    skipped: list[dict]
    dropped: list[dict]
    cores: list[str]
    resource_types: list[str]
    held: list[dict] = field(default_factory=list)  # entries an update's rig change holds ({..., mod})
