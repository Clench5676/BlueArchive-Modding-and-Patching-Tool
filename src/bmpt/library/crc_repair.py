"""Repair managed built copies and game files whose bytes lack their target filename's CRC.

A whole-file mod replacement can target a name carrying another CRC, so its bytes need the target's CRC fix.
An Off can retain those bytes as a built copy for the next On. The reuse check (``_kept_crc_fits``) rejects a
mismatching copy; this module finds persisted mismatches once per library and repairs them at startup through
the web UI services, after the first screen:

- a built copy (``profiles.json`` built) with a part whose kept bytes lack its part's CRC: dropped, so the next On
  builds the entry again;
- a game file the library wrote that still holds such bytes (mods were on): written again from the source it carries,
  built anew.

``profiles.json`` ``checked.target_crc`` records when the library was found free of both; the start then asks no more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmpt.engine import LogFunc, ProgressCallback, no_log, t

from ..core.errors import ServiceError
from ..core.logging_utils import log_failure
from . import safety
from .judge import LIBRARY_SOURCES, core_of, crc_state, now, says_official, sorted_resource_types
from .catalog_switch import MODE_CATALOG, MODS_DIR
from .records import JsonResult

if TYPE_CHECKING:
    from .records import Pin
    from .service import ModLibrary

logger = logging.getLogger(__name__)

CHECK = "target_crc"


@dataclass
class CrcRepairResult(JsonResult):
    """Report dropped built copies, rewritten game entries, and failures with code and params.

    Entries identify install, core, alt, and kind; cores/kinds identify changed game displays.
    """

    dropped: list[dict] = field(default_factory=list)
    rewritten: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    cores: list[str] = field(default_factory=list)
    resource_types: list[str] = field(default_factory=list)


def checked(config: dict | None) -> bool:
    """Check a profile document's completed target-CRC marker, treating None as unchecked."""
    marks = (config or {}).get("checked")
    return isinstance(marks, dict) and isinstance(marks.get(CHECK), str)


def _mark(library: ModLibrary) -> None:
    def change(data: dict) -> bool:
        if checked(data):
            return False
        marks = data.get("checked")
        data["checked"] = {**(marks if isinstance(marks, dict) else {}), CHECK: now()}
        return True

    library._update_profiles(change)


def _kept(library: ModLibrary, source: str, part: dict) -> Path:
    """The file a built key's part keeps its bytes in (as ``_built_replacements`` reads it)."""
    if part.get("mod_file") and source.startswith("mod:"):
        return library.root / "mods" / source[4:] / part["mod_file"]
    return library.root / "built" / f"{part.get('sha1')}.bundle"


def _lacks_crc(library: ModLibrary, path: Path, name: str, sha1: str | None = None) -> bool:
    """Whether ``path`` holds bytes (``sha1``, when given) without the CRC of ``name`` that the part needs."""
    try:
        fingerprint = library._hashes.get(path)
    except OSError:
        return False
    if sha1 is not None and fingerprint.sha1 != sha1:
        return False  # not the recorded bytes: the reuse refuses the copy anyway
    if crc_state(fingerprint.crc, fingerprint.trailing, name) != "needs_fix":
        return False
    return bool(library.settings and library.settings.crc_needed(path))


def _item(library: ModLibrary, pin: Pin | None, install: str, core: str) -> dict[str, str]:
    if pin is None:
        return {"install": install, "core": core, "alt": core, "resource_type": "portrait"}
    return {
        "install": install,
        "core": core,
        "alt": library._core_map(pin).key(core) or core,
        "resource_type": library._resource_type(core, pin) or "portrait",
    }


def _built(library: ModLibrary, config: dict | None) -> list[tuple[str, str]]:
    """List built copies that need a target CRC in a class 4 step under the library lock.

    Catalog placements can reuse these bytes. The caller yields between repair entries.
    """
    out = []
    catalogs = library._catalog_document()["installs"]
    for install, folder in ((config or {}).get("folders") or {}).items():
        catalog = catalogs.get(install)
        pin = library._recorded_pin(install, folder) if catalog and catalog["mode"] == MODE_CATALOG else None
        for core, key in (folder.get("built") or {}).items():
            if pin is not None and library._catalog_targets(pin, library._spine_parts(core, pin), catalog):
                continue
            source = key.get("source") if isinstance(key, dict) else None
            if not isinstance(source, str):
                continue
            for name, part in (key.get("parts") or {}).items():
                if isinstance(part, dict) and _lacks_crc(library, _kept(library, source, part), name, part.get("sha1")):
                    out.append((install, core))
                    break
    return out


def _game(library: ModLibrary, pins: list[Pin]) -> list[tuple[Pin, str, str]]:
    """List the game files the library wrote that still hold the bytes it wrote, recorded without their name's CRC.

    Returns:
        One ``(install, core, the source it carries)`` tuple per such file.
    """
    out: dict[tuple[str, str], tuple[Pin, str, str]] = {}
    catalogs = library._catalog_document()["installs"]
    for target, record in list(library._applied.items()):
        source = record.get("source")
        if (
            record.get("crc") != "needs_fix"
            or says_official(record)
            or record.get("removed")
            or not isinstance(source, str)
            or not source.startswith(LIBRARY_SOURCES)
        ):
            continue
        path = Path(target)
        pin = next((pin for pin in pins if any(path.is_relative_to(folder) for folder in pin.dirs)), None)
        if pin is None:
            continue
        if catalogs.get(pin.key, {}).get("mode") == MODE_CATALOG and path.parent.name == MODS_DIR:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if (stat.st_size, stat.st_mtime_ns) != (record.get("size"), record.get("mtime")):
            continue  # Outside changes must not be rewritten as the recorded source.
        if not _lacks_crc(library, path, path.name, record.get("sha1")):
            continue
        core = library._core_map(pin).slot(core_of(path.name))
        out.setdefault((pin.key, core), (pin, core, source))
    return list(out.values())


def findings(library: ModLibrary) -> dict[str, Any]:
    """Find managed files needing a target-CRC repair, recording a clean library as checked.

    A completed marker makes later startup checks read only the configuration.

    Args:
        library: Library whose built-copy records and unchanged managed game writes are inspected.

    Returns:
        due and entries identifying install, core, alt, kind, and affected built/game copies.
    """
    config = library.profiles.read()
    if checked(config):
        return {"due": False, "entries": []}
    pins = library.installs()
    with library._lock:
        game = _game(library, pins)
        built = _built(library, config)
    if not game and not built:
        try:
            _mark(library)
        except ServiceError as error:
            log_failure(logger, logging.DEBUG, "Could not record the CRC check", error)
        return {"due": False, "entries": []}
    by_key = {pin.key: pin for pin in pins}
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for install, core in built:
        entries[(install, core)] = {**_item(library, by_key.get(install), install, core), "built": True, "game": False}
    for pin, core, _source in game:
        entry = entries.setdefault((pin.key, core), {**_item(library, pin, pin.key, core), "built": False})
        entry["game"] = True
    logger.info("Files without their part's CRC: %d built copies, %d game entries", len(built), len(game))
    return {"due": True, "entries": list(entries.values())}


def repair(library: ModLibrary, log: LogFunc = no_log, progress: ProgressCallback | None = None) -> CrcRepairResult:
    """Drop invalid built copies and rebuild managed game entries from their carried sources.

    Dropped copies are rebuilt on the next On. Entry refusals are reported in failed
    and retried at startup; the check is marked complete once none remain.
    Class 4 holds the library lock for one repair entry and yields between entries.

    Args:
        library: Library supplying source records, build storage, and the protected game writer.
        log: Receives user-facing operation messages.
        progress: Receives (completed entry, game-entry count, message); None disables reporting.
    """
    result = CrcRepairResult()
    pins = library.installs()
    config = library.profiles.read()
    with library._lock:
        game = _game(library, pins)
        built = _built(library, config)
    by_key = {pin.key: pin for pin in pins}
    step_aside = getattr(library._lock, "step_aside", lambda: 0.0)
    for install, core in built:
        step_aside()  # a preview or a page read waiting for the library goes first, between two entries
        library._drop_built(install, [core])
        result.dropped.append(_item(library, by_key.get(install), install, core))
    if game:
        try:
            safety.check_game_not_running()
            library._check_idle([pin for pin, _core, _source in game])
        except ServiceError as error:
            # nothing is asked: the game files are written at a later start
            logger.info("Game files without their part's CRC left for a later start: %s", error.code)
            result.failed.extend(
                {"entry": _item(library, pin, pin.key, core), "code": error.code, "params": error.to_json()["params"]}
                for pin, core, _source in game
            )
            game = []
    cores, resource_types = set(), set()
    for i, (pin, core, source) in enumerate(game, 1):
        item = _item(library, pin, pin.key, core)
        step_aside()
        with library._lock:
            try:
                library._drop_built(pin, [core])
                options = {"files_only": True} if library._catalog_record(pin)["mode"] == MODE_CATALOG else {}
                library._switch(pin, core, source, log, use_built=False, **options)
            except ServiceError as error:
                log_failure(logger, logging.WARNING, f"Could not write {core} again", error)
                result.failed.append({"entry": item, "code": error.code, "params": error.to_json()["params"]})
                continue
        result.rewritten.append(item)
        cores.add(core)
        resource_types.add(item["resource_type"])
        if progress:
            progress(i, len(game), t("webui.progress.apply", name=core))
    result.cores, result.resource_types = sorted(cores), sorted_resource_types(resource_types)
    if not result.failed:
        _mark(library)
    return result
