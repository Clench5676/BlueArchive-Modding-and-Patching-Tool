"""Built copies and game files an older build left without the CRC of their part's name (BL-191).

Before 0.7.0, a mod file written whole over a part whose name carries another CRC (a game update rebuilt the part in
place, changing only the CRC in its name) was copied without the CRC fix. The game file then had a CRC that is not
its name's, and the Off that followed kept those very bytes as the entry's built copy, which the next On wrote back.
The program no longer writes such bytes and builds such a copy again rather than reuse it (``_kept_crc_fits``); this
module finds what an older build left, once per library, and the start repairs it without asking (the web UI's
services, after the first screen):

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

from ..errors import ServiceError
from ..logging_utils import log_failure
from . import safety
from .judge import LIBRARY_SOURCES, core_of, crc_state, now, says_official
from .models import JsonResult

if TYPE_CHECKING:
    from .models import Pin
    from .service import ModLibrary

logger = logging.getLogger(__name__)

CHECK = "target_crc"


@dataclass
class CrcRepairResult(JsonResult):
    """What the repair did, entry by entry ({install, core, variant, kind}): ``dropped`` built copies, game files
    ``rewritten``, ``failed`` ({entry, code, params}). ``cores``/``kinds``: what the game shows changed for."""

    dropped: list[dict] = field(default_factory=list)
    rewritten: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    cores: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)


def checked(config: dict | None) -> bool:
    """Whether the library was already found free of such files."""
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
        return {"install": install, "core": core, "variant": core, "kind": "spr"}
    return {"install": install, "core": core, "variant": library._core_map(pin).key(core) or core,
            "kind": library._kind(core, pin) or "spr"}


def _built(library: ModLibrary, config: dict | None) -> list[tuple[str, str]]:
    """(install key, core) of each built key with a part whose kept bytes lack the part's CRC."""
    out = []
    for install, folder in ((config or {}).get("folders") or {}).items():
        for core, key in (folder.get("built") or {}).items():
            source = key.get("source") if isinstance(key, dict) else None
            if not isinstance(source, str):
                continue
            for name, part in (key.get("parts") or {}).items():
                if isinstance(part, dict) and _lacks_crc(library, _kept(library, source, part), name,
                                                         part.get("sha1")):
                    out.append((install, core))
                    break
    return out


def _game(library: ModLibrary, pins: list[Pin]) -> list[tuple[Pin, str, str]]:
    """(install, core, the source it carries) of each game file the library wrote that still holds the bytes it
    wrote, recorded without its name's CRC."""
    out: dict[tuple[str, str], tuple[Pin, str, str]] = {}
    for target, record in list(library._applied.items()):
        source = record.get("source")
        if (record.get("crc") != "needs_fix" or says_official(record) or record.get("removed")
                or not isinstance(source, str) or not source.startswith(LIBRARY_SOURCES)):
            continue
        path = Path(target)
        pin = next((pin for pin in pins if any(path.is_relative_to(folder) for folder in pin.dirs)), None)
        if pin is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if (stat.st_size, stat.st_mtime_ns) != (record.get("size"), record.get("mtime")):
            continue  # changed since: no longer the library's bytes
        if not _lacks_crc(library, path, path.name, record.get("sha1")):
            continue
        core = library._core_map(pin).slot(core_of(path.name))
        out.setdefault((pin.key, core), (pin, core, source))
    return list(out.values())


def findings(library: ModLibrary) -> dict[str, Any]:
    """{due, entries: [{install, core, variant, kind, built, game}]}. A library found free of such files
    is recorded as checked at once, and later starts read nothing but the configuration."""
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
        entries[(install, core)] = {**_item(library, by_key.get(install), install, core), "built": True,
                                    "game": False}
    for pin, core, _source in game:
        entry = entries.setdefault((pin.key, core), {**_item(library, pin, pin.key, core), "built": False})
        entry["game"] = True
    logger.info("Files without their part's CRC: %d built copies, %d game entries", len(built), len(game))
    return {"due": True, "entries": list(entries.values())}


def repair(library: ModLibrary, log: LogFunc = no_log, progress: ProgressCallback | None = None) -> CrcRepairResult:
    """Drop every built copy found (the next On builds the entry again) and write each game entry found again from
    the source it carries, built anew. Never raises for an entry: one that cannot be written now (the game running,
    a write refused) is in ``failed`` and found again at the next start. Recorded as checked once nothing is left."""
    result = CrcRepairResult()
    pins = library.installs()
    config = library.profiles.read()
    with library._lock:
        game = _game(library, pins)
        built = _built(library, config)
    by_key = {pin.key: pin for pin in pins}
    for install, core in built:
        library._drop_built(install, [core])
        result.dropped.append(_item(library, by_key.get(install), install, core))
    if game:
        try:
            safety.check_game_not_running()
            library._check_idle([pin for pin, _core, _source in game])
        except ServiceError as error:
            # nothing is asked: the game files are written at a later start
            logger.info("Game files without their part's CRC left for a later start: %s", error.code)
            result.failed.extend({"entry": _item(library, pin, pin.key, core), "code": error.code,
                                  "params": error.to_json()["params"]} for pin, core, _source in game)
            game = []
    cores, kinds = set(), set()
    for i, (pin, core, source) in enumerate(game, 1):
        item = _item(library, pin, pin.key, core)
        with library._lock:
            try:
                library._drop_built(pin, [core])
                library._switch(pin, core, source, log, use_built=False)
            except ServiceError as error:
                log_failure(logger, logging.WARNING, f"Could not write {core} again", error)
                result.failed.append({"entry": item, "code": error.code, "params": error.to_json()["params"]})
                continue
        result.rewritten.append(item)
        cores.add(core)
        kinds.add(item["kind"])
        if progress:
            progress(i, len(game), t("webui.progress.apply", name=core))
    result.cores, result.kinds = sorted(cores), sorted(kinds)
    if not result.failed:
        _mark(library)
    return result
