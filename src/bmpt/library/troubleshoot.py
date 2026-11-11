"""Troubleshoot Mods (BL-185), a pre-release Settings entry: every choice back to official, one at a time again.

The Enable Mods switch's Off writes official files and keeps the configuration, and while mods are off a choice is
only configured, so mods cannot be tried one by one with it. Apply Official to All runs the same Off (it adopts the
changed files it meets and keeps the built copies), keeps the configuration of the listed installs as a snapshot in
``profiles.json`` (``troubleshoot``), clears it and turns mods on again: every later choice writes at once. Restore
Configuration puts the snapshot back and runs the toggle to the intent it was taken with; a core chosen meanwhile and
absent from the snapshot goes back to official on the way. Then each listed install's bundles are checked against
Steam's file list (size and SHA-1).

    "troubleshoot": {"since": "<ISO>", "enabled": true, "folders": {"<install key>": {"<core>": {...}}}}
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmpt.engine import t

from ..errors import ServiceError
from . import profiles as profiles_mod
from .judge import now
from .models import ToggleResult

if TYPE_CHECKING:
    from .models import Pin
    from .service import ModLibrary

logger = logging.getLogger(__name__)

TAG = "troubleshoot"
#: the most file names a check lists (the count stays exact)
MAX_LISTED = 200


@dataclasses.dataclass(frozen=True)
class TroubleshootResult(ToggleResult):
    """The toggle's result, entry by entry (the switch's result popup reads it as it is), with the check of the
    files against Steam's list (None when none ran) and whether the job was cancelled before its end."""

    check: dict | None = None
    cancelled: bool = False


def _result(toggle: ToggleResult, check_: dict | None, cancelled: bool = False) -> TroubleshootResult:
    return TroubleshootResult(**{f.name: getattr(toggle, f.name) for f in dataclasses.fields(ToggleResult)},
                              check=check_, cancelled=cancelled)


def snapshot(config: dict | None) -> dict | None:
    """The saved configuration, None when not troubleshooting."""
    value = (config or {}).get(TAG)
    if not isinstance(value, dict) or not isinstance(value.get("folders"), dict):
        return None
    return value


def status(library: ModLibrary) -> dict[str, Any]:
    """{active, count (saved choices), since} for Settings."""
    saved = snapshot(library.profiles.read())
    if saved is None:
        return {"active": False, "count": 0, "since": None}
    count = sum(len(entries) for entries in saved["folders"].values() if isinstance(entries, dict))
    return {"active": True, "count": count, "since": saved.get("since")}


def apply_official(library: ModLibrary, pins: list[Pin], log: Callable = lambda *_: None,
                   progress: Callable | None = None, cancel: Callable[[], bool] | None = None) -> TroubleshootResult:
    """Apply Official to All: the Off over ``pins``, the snapshot (an earlier one is kept: pressed again while
    troubleshooting, the configuration from before stays the one to restore), the configuration of ``pins`` cleared
    and mods on. A cancelled or stopped Off leaves the intent Off and takes no snapshot."""
    current = profiles_mod.enabled(library.profiles.read())
    result = library.toggle(False, current, pins, log=log, progress=progress, cancel=cancel)
    if cancel and cancel():
        return _result(result, None, True)
    keys = {pin.key for pin in pins}
    stamp = now()

    def change(data: dict) -> bool:
        folders = data.get("folders") or {}
        if snapshot(data) is None:
            data[TAG] = {"since": stamp, "enabled": current,
                         "folders": {key: copy.deepcopy(folder.get("configured") or {})
                                     for key, folder in folders.items() if key in keys and folder.get("configured")}}
        for key, folder in folders.items():
            if key in keys:
                folder["configured"] = {}
        data["enabled"], data["toggled"] = True, stamp
        return True

    library.profiles.update(change)
    logger.info("Troubleshoot: every choice set to official in %d installs", len(pins))
    return _result(result, check(library, pins, progress, cancel))


def restore(library: ModLibrary, pins: list[Pin], log: Callable = lambda *_: None,
            progress: Callable | None = None, cancel: Callable[[], bool] | None = None) -> TroubleshootResult:
    """Restore Configuration: the snapshot's configuration back in the installs it names (a core configured meanwhile
    and absent from it goes back to official), then the toggle to the intent it was taken with. The snapshot is
    dropped once the toggle ran; a refusal (``untested_build``, the game running) keeps it for the same call again.
    ``state_changed`` when there is none."""
    config = library.profiles.read()
    saved = snapshot(config)
    if saved is None:
        raise ServiceError("state_changed")
    current = profiles_mod.enabled(config)
    target = saved.get("enabled") is not False
    by_key = {pin.key: pin for pin in pins}
    if current:
        # a choice made meanwhile and absent from the snapshot: back to official first (no confirmation needed)
        for key, folder in ((config or {}).get("folders") or {}).items():
            before = saved["folders"].get(key) if isinstance(saved["folders"].get(key), dict) else {}
            if key not in by_key:
                continue
            for core, entry in sorted((folder.get("configured") or {}).items()):
                if core not in before and isinstance(entry, dict) and isinstance(entry.get("variant"), str):
                    library.apply(entry["variant"], "official", log=log, kind=entry.get("kind"), pin=by_key[key])

    def put(data: dict) -> bool:
        for key, folder in (data.get("folders") or {}).items():
            if key in by_key:
                before = saved["folders"].get(key)
                folder["configured"] = copy.deepcopy(before) if isinstance(before, dict) else {}
        return True

    library.profiles.update(put)
    result = library.toggle(target, current, pins, log=log, progress=progress, cancel=cancel)
    if cancel and cancel():
        return _result(result, None, True)

    def drop(data: dict) -> bool:
        return data.pop(TAG, None) is not None

    library.profiles.update(drop)
    logger.info("Troubleshoot: configuration restored (mods %s)", "on" if target else "off")
    return _result(result, None if target else check(library, pins, progress, cancel))


def check(library: ModLibrary, pins: list[Pin], progress: Callable | None = None,
          cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Every bundle Steam's file list names in each of ``pins`` against its size and SHA-1: {verified, checked,
    changed: [names], missing: [names], unverified: [install keys]} (each list cut at ``MAX_LISTED``; ``changedCount``
    and ``missingCount`` exact). An install without a readable list, or without the consent to read Steam's records,
    is unverified."""
    changed: list[str] = []
    missing: list[str] = []
    unverified: list[str] = []
    work: list[tuple[str, Path | None, Any]] = []
    allowed = library._scan_allowed()
    for pin in pins:
        listed = library._steam_files.bundles(pin.root) if allowed else {}
        if not listed:
            unverified.append(pin.key)
            continue
        present = {name: Path(path) for name, path in library._listing(pin)}
        work.extend((name, present.get(name), official) for name, official in sorted(listed.items()))
    total = len(work)
    for i, (name, path, official) in enumerate(work, 1):
        if cancel and cancel():
            break
        if path is None:
            missing.append(name)
        else:
            try:
                same = path.stat().st_size == official.size and library._hashes.get(path).sha1 == official.sha1
            except OSError:
                same = False
            if not same:
                changed.append(name)
        if progress and (i % 64 == 0 or i == total):
            progress(i, total, t("webui.progress.check"))
    if changed or missing:
        logger.warning("Troubleshoot check: %d changed, %d missing of %d", len(changed), len(missing), total)
    return {"verified": not unverified and total > 0, "checked": total,
            "changed": changed[:MAX_LISTED], "changedCount": len(changed),
            "missing": missing[:MAX_LISTED], "missingCount": len(missing), "unverified": unverified}
