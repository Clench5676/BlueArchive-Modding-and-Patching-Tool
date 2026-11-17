"""Judge game files and entries with pure rules that can be tested independently of reads.

Terms (docs/webui/library.md, library): an *entry* is one game core of a variant in one install (a portrait or one
memorial lobby core); what its files *carry* is the library source that wrote them while they are unchanged since, else
official, unknown or damaged; *configured* is the source the user chose for it; *enabled* is the Enable Mods switch.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Hashable, Iterable, Sequence

from bmpt.engine import SEARCH_DIR_SUFFIXES, parse_filename, spine_kind

LIBRARY_SOURCES = ("mod:", "variant:")
CRC_STATES = ("ok", "fixed", "needs_fix")
KINDS = ("spr", "lobby", "model", "other")
MODEL_CATEGORIES = {"character", "characters", "npcs"}


def supports_kind(kind: str) -> bool:
    """Return whether files of this kind can be stored and switched by the library."""
    return kind in KINDS


def now() -> str:
    """Return the current time as the library records it (ISO 8601, UTC)."""
    return datetime.now(timezone.utc).isoformat()


def seconds(value: Any) -> float | None:
    """Convert a recorded time to seconds since the epoch.

    Args:
        value: ISO timestamp or numeric file mtime in nanoseconds; None, booleans,
            and unparseable timestamps yield None.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1e9
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def kind_of(core: str, category: str | None = None) -> str | None:
    """Classify a character asset slot, using the bundle category for 3D models.

    Args:
        core: Bundle core to classify as portrait, lobby, or model.
        category: Filename category distinguishing model assets; None uses Spine naming alone.

    Returns:
        The supported character kind, or None if the core is not a character slot.
    """
    if (
        category
        and category.lower() in MODEL_CATEGORIES
        and spine_kind(core) is None
        and re.fullmatch(r"[a-z0-9][a-z0-9_]*", core.lower())
    ):
        if core.lower() != "mxcommon" and not core.lower().startswith("mxcommon_"):
            return "model"
    return spine_kind(core, category)


def kind_of_file(name: str) -> str | None:
    """Classify a bundle filename by category and core, using other for generic bundles."""
    parsed = parse_filename(name)
    return kind_of(parsed.core, parsed.category) or (
        "other" if name.endswith(".bundle") and parsed.prefix and parsed.crc else None
    )


# Groups packed one asset per bundle: their bundles' names differ only in the CRC, which a game update changes, so a
# name cannot tell them apart. The library learns each such bundle's CAB from its header (stable while the asset stays
# in its group) and its generic slot is keyed by it. A name with its CRC names one content, so what is learned holds.
_packed_lock = threading.Lock()
_packed: dict[str, str] = {}


def learn_packed(name: str, cab: str) -> bool:
    """Remember the CAB identifying a slot packed one asset per bundle.

    Args:
        name: Bundle filename, optionally ending in .backup; matched case-insensitively.
        cab: Serialized-file CAB used to distinguish bundles sharing a filename prefix.

    Returns:
        Whether the remembered CAB changed.
    """
    key, cab = name.removesuffix(".backup").lower(), cab.lower()
    with _packed_lock:
        if _packed.get(key) == cab:
            return False
        _packed[key] = cab
        return True


def packed_cab(name: str) -> str | None:
    """Return a bundle filename's learned CAB, ignoring case and .backup, or None if unknown."""
    with _packed_lock:
        return _packed.get(name.removesuffix(".backup").lower())


def core_of(name: str) -> str:
    """Find a filename's character core or stable generic slot key.

    Generic slots use prefix and resource type, plus a learned CAB for groups packed
    one asset per bundle. Dates and CRCs do not distinguish slots, except Collection
    batch dates, which identify coexisting portraits.

    Args:
        name: Bundle filename to resolve to its lower-case core or hashed other slot.
    """
    if kind_of_file(name) == "other":
        cab = packed_cab(name)
        part = part_of(name) if cab is None else (*part_of(name), cab)
        return "other:" + hashlib.sha256(repr(part).encode()).hexdigest()
    return parse_filename(name).core.lower()


def other_target(names: Iterable[str]) -> str:
    """Address a bundle set while leaving its individual slots independently configurable.

    Args:
        names: Bundle filenames whose unique slot keys form the set's stable address.
    """
    slots = sorted({core_of(name) for name in names})
    return "other-set:" + hashlib.sha256("\0".join(slots).encode()).hexdigest()


def part_of(name: str) -> tuple[str, str | None]:
    """Identify a filename's part by prefix and resource type, retaining Collection batch dates."""
    parsed = parse_filename(name)
    # Collection batches coexist across dates; a later batch does not replace an earlier batch's portraits.
    if name.lower().startswith("uis-01_common-14_charactercollect-"):
        return parsed.prefix + parsed.date + ":", parsed.res_type
    return parsed.prefix, parsed.res_type


def date_of(name: str) -> str:
    """Return the game-data date encoded in a bundle filename."""
    return parse_filename(name).date


def mod_versions(entries: Sequence[tuple[Hashable, str]], installed: Iterable[str] = ()) -> list[str]:
    """Group imported bundle sets into mod versions using the rule shared by Add and batch import.

    A set is what one import takes for one mod: the files of one core and kind, or of one model slot across its cores
    (the batch import also separates folders and variants), named by its key. Its files are the parts of one mod
    whatever their dates, because some cores keep their parts under different dates
    (a Spine core's textures and text, a model's prefab and
    dependencies). Only a set in which one part (``part_of``) occurs under more than one date holds several versions
    of the mod: the whole set then goes by date. Dates kept side by side for that part in the install are one version.

    Args:
        entries: Pairs of grouping key and bundle filename, in the import's order.
        installed: Live bundle names whose coexisting dates should count as one version.

    Returns:
        One version label per entry: empty for an unversioned set, otherwise its file's date.
    """
    live: dict[tuple[str, str | None], set[str]] = {}
    for name in installed:
        live.setdefault(part_of(name), set()).add(date_of(name))
    dates: dict[tuple[Hashable, tuple[str, str | None]], set[str]] = {}
    for key, name in entries:
        dates.setdefault((key, part_of(name)), set()).add(date_of(name))
    versioned = {key for (key, part), found in dates.items() if len(found) > 1 and not found <= live.get(part, set())}
    return [date_of(name) if key in versioned else "" for key, name in entries]


def install_root(folder: Path) -> Path:
    """Resolve a game-folder setting to its install root.

    Args:
        folder: Install or resource directory; a recognized SEARCH_DIR_SUFFIXES suffix
            is removed from the resolved path.
    """
    folder = Path(folder).resolve()
    for suffix in sorted(filter(None, SEARCH_DIR_SUFFIXES), key=len, reverse=True):
        parts = tuple(part.lower() for part in Path(suffix).parts)
        if len(folder.parts) > len(parts) and tuple(part.lower() for part in folder.parts[-len(parts) :]) == parts:
            return Path(*folder.parts[: -len(parts)])
    return folder


def install_key(root: Path) -> str:
    """Return the install root's storage key, case-folded on Windows."""
    return os.path.normcase(str(root))


def distinguishing_folders(roots: dict[str, Path]) -> dict[str, str]:
    """Choose the shortest distinct trailing path for each install of a server.

    Args:
        roots: Install roots indexed by install key, all belonging to the same server.

    Returns:
        Labels by install key, such as BlueArchive or Steam/BlueArchive when folder names coincide.
    """
    out = {}
    for key, root in roots.items():
        parts = root.parts
        for count in range(1, len(parts) + 1):
            tail = parts[-count:]
            if all(other.parts[-count:] != tail for k, other in roots.items() if k != key):
                out[key] = "/".join(tail)
                break
        else:
            out[key] = str(root)
    return out


def says_official(record: dict | None) -> bool:
    """Read a write record's official flag, falling back to its source when the flag is absent.

    Args:
        record: Recorded write, or None when no write is known.
    """
    if not record:
        return False
    flag = record.get("official")
    return flag if isinstance(flag, bool) else record.get("source") == "official"


def crc_state(crc: int, trailing: int | None, name: str) -> str:
    """Judge file bytes against the CRC encoded in a target filename.

    Args:
        crc: CRC32 of the entire file, including any fixing suffix.
        trailing: Bytes beyond the bundle's declared size, or None when the header is unreadable.
        name: Target filename containing the expected CRC.

    Returns:
        ok for a match without trailing bytes, fixed for a match with a suffix, else needs_fix.
    """
    parsed = parse_filename(name)
    matches = bool(parsed.crc) and crc == int(parsed.crc)
    if matches and trailing == 0:
        return "ok"
    return "fixed" if matches and trailing else "needs_fix"


def carried_of(parts: Iterable[tuple[str, bool]]) -> str:
    """Determine an entry's source from its non-official parts.

    A mod replaces its kind as a unit, recording even parts left official. Restoring
    the only modded part must make the entry official even though other parts still
    match their recorded mod-source writes.

    Args:
        parts: Pairs of carried source and official-byte flag for every part of the entry.

    Returns:
        official when all parts are official, the sole non-official source, or unknown
        for mixed or unknown sources.
    """
    sources = {carried for carried, official in parts if not official}
    if not sources:
        return "official"
    if len(sources) != 1 or "unknown" in sources:
        return "unknown"
    return next(iter(sources))


def classify(
    enabled: bool,
    configured: str | None,
    carried: str,
    *,
    files: bool = True,
    damaged: bool = False,
    configured_exists: bool = True,
    carried_exists: bool = True,
    drifted_present: bool = False,
    drifted_gone: bool = False,
    marker: bool = False,
    written: float | None = None,
    since: float | None = None,
    toggled: float | None = None,
) -> tuple[str, str | None]:
    """Classify an entry by the first matching state rule.

    A takeover is classified again by the caller with the returned source configured.

    Args:
        enabled: Enable Mods intent for the install.
        configured: Chosen library source, or None for official files.
        carried: Source of non-official parts, official if none, unknown if mixed,
            or removed for a deleted mod.
        files: Whether any target files exist for the configured slot.
        damaged: Whether the entry contains unreadable bundle data.
        configured_exists: Whether the chosen source is available.
        carried_exists: Whether the source identified in the game files is available.
        drifted_present: Whether a changed target part is still present in the install.
        drifted_gone: Whether a changed target part is absent from the install.
        marker: Whether a recorded update marks the entry as rebuilt.
        written: Latest source write time in epoch seconds, or None when unknown.
        since: Configuration choice time in epoch seconds, or None when unknown.
        toggled: Last switch change in epoch seconds, or None when unknown.

    Returns:
        State and source to configure for a takeover; the second value is None otherwise.
    """
    if damaged:
        return "damaged", None
    if configured and not files:
        return "missing", None
    if carried == "unknown":
        return ("changed" if configured else "untracked"), None
    if configured and not configured_exists:
        return "source_missing", None
    if carried.startswith(LIBRARY_SOURCES) and carried_exists and carried != configured:
        newest = max(value for value in (since, toggled, float("-inf")) if value is not None)
        if configured is None or (written is not None and written > newest):
            return "takeover", carried
    if enabled and configured and carried in ("official", configured):
        if marker or drifted_gone:
            return "updated", None
        if drifted_present:
            return "reset", None
    if enabled:
        if (configured and carried == configured) or (not configured and carried == "official"):
            return "ok", None
    elif carried == "official":
        return "ok", None
    return "pending", None


def leaves(state: str, enabled: bool) -> bool:
    """Decide whether an entry can leave the switch's unfinished list.

    Args:
        state: Classified entry state after a toggle or Retry.
        enabled: Current switch intent; enabling leaves outside changes untouched.
    """
    if state in ("ok", "missing", "damaged", "source_missing"):
        return True
    # an On never writes over an outside change; while mods are off the next Off or Retry adopts it
    return enabled and state in ("untracked", "changed")
