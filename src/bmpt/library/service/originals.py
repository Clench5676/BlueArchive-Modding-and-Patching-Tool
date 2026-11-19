"""Official copies: kept originals, their checks and pruning, and the rig archive."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import AbstractSet, Any, Callable, Iterator, Mapping, Sequence

from bmpt.spine import atlas_remap
from bmpt.host import game_paths, steam_records
from bmpt.core import devmode, preview_gate, spans
from bmpt.engine import parse_filename
from ...core.errors import ServiceError
from ...core.logging_utils import log_failure
from ...core.openfiles import remove_tree
from .. import fingerprint, integrity, rig_archive, toggle_timing
from ..catalog_switch import MODE_CATALOG
from ..original_checks import Verdict
from ..judge import (
    core_of,
    resource_type_of_file,
    now,
    says_official,
)
from ..records import Pin
from ..safety import check_resource_path
from ..storage import (
    Fingerprint,
    atomic_clone,
    atomic_copy,
    trailing_bytes,
    verified_move,
    verify_file,
)
from .base import RIG_ARCHIVE_QUIET, TOGGLE_YIELD, _View, _background, _serialized


logger = logging.getLogger(__package__)


class OriginalsMixin:
    """Official copies: kept originals, their checks and pruning, and the rig archive."""

    def _is_official(self, path: Path) -> bool:
        return self._is_official_as(path, path.name)

    def _steam_official(
        self, name: str, path: Path | None = None, roots: Sequence[Path] | None = None
    ) -> steam_records.OfficialFile | None:
        """Read what Steam's file list says the official file of a bundle name is (its size and SHA-1).

        It reads only with the consent to read Steam's records: the list of the Steam install holding ``path``, else of
        ``roots``, else of every known install. None when no list names it, or two lists disagree.
        """
        if not self._scan_allowed():
            return None
        name = name.removesuffix(".backup")
        folders: list[Path] = []
        if path is not None and game_paths.steam_app_manifest(path.parent) is not None:
            folders = [path.parent]
        elif roots is not None:
            folders = list(roots)
        else:
            folders = self._steam_roots()
        found = set()
        for folder in folders:
            try:
                entry = self._steam_files.bundles(folder).get(name)
            except OSError:
                entry = None
            if entry is not None:
                found.add(entry)
        return next(iter(found)) if len(found) == 1 else None

    def _steam_roots(self) -> list[Path]:
        """List the known installs' roots for Steam's file lists.

        The list is made again after a few seconds (a judgement asks once per file).
        """
        now = time.monotonic()
        cached = self._steam_roots_cache
        if cached is None or now - cached[0] > 5:
            cached = self._steam_roots_cache = (now, [pin.root for pin in self.installs()])
        return cached[1]

    def _is_official_as(self, path: Path, name: str) -> bool:
        """Tell whether the file at ``path`` holds the official bytes of ``name``.

        It does with the size and SHA-1 that Steam's file list gives ``name`` (``_steam_official``), else with the CRC32
        of the whole content matching the CRC in ``name``, and no trailing bytes. A mod can match a CRC, not a SHA-1. A
        kept original also records which rule it passed, and one a Steam list rejected stays rejected without the list
        (``_judge_original``).
        """
        kept = self._is_kept(path) and path.name == name
        if kept and not self._original_checks.matches_digest(path):
            return False
        fingerprint = self._hashes.get(path)
        steam = self._steam_official(name, path)
        if kept:
            return self._judge_original(path, fingerprint, steam) in ("steam", "crc")
        if steam is not None:
            return fingerprint.size == steam.size and fingerprint.sha1 == steam.sha1
        # the very bytes a Steam list rejected as the kept original of this name stay a mod's without the list
        return self._original_checks.of_bytes(name, fingerprint) != "failed" and self._crc_official(fingerprint, name)

    @staticmethod
    def _crc_official(fingerprint: Fingerprint, name: str) -> bool:
        parsed = parse_filename(name)
        return fingerprint.trailing == 0 and bool(parsed.crc) and fingerprint.crc == int(parsed.crc)

    def _is_kept(self, path: Path) -> bool:
        """Whether a path is a kept original (a file of originals/)."""
        try:
            return path.parent.resolve() == (self.root / "originals").resolve()
        except (OSError, RuntimeError):
            return False

    def _judge_original(
        self, original: Path, fingerprint: Fingerprint, steam: steam_records.OfficialFile | None
    ) -> Verdict | None:
        """Judge how a kept original's bytes pass, as it is recorded.

        The result is ``steam`` (Steam's list gives its size and SHA-1, now or when it was recorded), ``crc`` (no list
        names it, the CRC rule alone) or ``failed`` (a Steam list gave other bytes: never official again, list or not).
        None when it fails the CRC rule (damaged, ``official_invalid``).
        """
        if steam is not None:
            verdict: Verdict = "steam" if (fingerprint.size, fingerprint.sha1) == (steam.size, steam.sha1) else "failed"
            self._original_checks.save(original, verdict, fingerprint, self._originals())
            return verdict
        recorded = self._original_checks.of_bytes(original.name, fingerprint)
        if recorded in ("failed", "steam"):
            return recorded
        if not self._crc_official(fingerprint, original.name):
            return None
        if recorded is None:
            self._original_checks.save(original, "crc", fingerprint, self._originals())
        return "crc"

    def original_check(self, name: str, copy: str | None, roots: Sequence[Path] | None = None) -> str | None:
        """Describe an official copy's verification strength without reading its content.

        _View.copy_of excludes kept originals rejected by Steam's list.

        Args:
            name: Bundle filename identifying the official bytes and verification record.
            copy: Copy location from _View.copy_of: kept, backup, elsewhere, game, or None.
            roots: Install roots whose Steam lists may verify the name; None uses known installs.

        Returns:
            steam if a Steam list names the file or the kept original passed it, crc
            for the CRC rule alone, or None without a copy or for the game file itself.
        """
        if copy not in ("kept", "backup", "elsewhere"):
            return None
        if copy == "kept" and self._original_checks.of_file(self.root / "originals" / name) == "steam":
            return "steam"
        return "steam" if self._steam_official(name, roots=roots) is not None else "crc"

    def check_originals(self, cancel: Callable[[], bool] | None = None) -> dict[str, int]:
        """Verify kept originals lacking a current Steam verdict when scan consent permits it.

        Originals with only a CRC verdict, no verdict, or changed file metadata are
        hashed once and recorded as steam or failed. The scan calls this outside the
        library lock.

        Args:
            cancel: Returns True to stop before the next original; None checks every eligible original.

        Returns:
            Counts by verdict for the originals judged in this pass.
        """
        counts: dict[str, int] = {}
        if not self._scan_allowed():
            return counts
        roots = self._steam_roots()
        for name in sorted(self._originals()):
            if cancel and cancel():
                break
            original = self.root / "originals" / name
            if self._original_checks.of_file(original) in ("steam", "failed"):
                continue
            steam = self._steam_official(name, roots=roots)
            if steam is None:
                continue
            preview_gate.yield_to_previews(cancel)
            try:
                fingerprint = self._hashes.get(original, include_sha256=self._managed)
                verdict = self._judge_original(original, fingerprint, steam)
            except OSError:
                continue  # released meanwhile
            if verdict == "failed":
                logger.warning("The kept original %s is not the file Steam's list names", name)
            counts[verdict or "invalid"] = counts.get(verdict or "invalid", 0) + 1
        return counts

    def _original_path(self, game_file: Path) -> Path:
        return self.root / "originals" / game_file.name

    def _in_library(self, path: str | os.PathLike[str] | None) -> bool:
        """Whether a path an OS error names lies in the library's own folder rather than in the game."""
        if path is None:
            return False
        try:
            return Path(os.fsdecode(path)).resolve().is_relative_to(self.root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def _elsewhere(self, game_file: Path) -> list[Path]:
        """Files of a game file's name in the other known installs and the folders of the library's records."""
        name, exclude = game_file.name, game_file.resolve()
        out: list[Path] = []
        for pin in self.installs():
            for path in self._index(pin).get(core_of(name), []):
                if path.name == name and path.resolve() != exclude:
                    out.append(path)
        for recorded in self._applied:
            path = Path(recorded)
            if (
                path.name == name
                and path != exclude
                and path not in out
                and devmode.allows_read(path)
                and path.is_file()
            ):
                out.append(path)
        return out

    @toggle_timing.stage("official_lookup")
    def _official_for(self, game_file: Path, pin: Pin, full: bool = True, *, game_first: bool = False) -> Path:
        """Find an official copy of a game file.

        The candidates come in this order: the library's kept original (it was verified when it was kept;
        ``official_invalid`` when it fails, never overwritten), the game file itself, its .backup beside it (the classic
        GUI's; judged by the name without .backup), a file of its name in another game folder. At write strength each
        candidate is verified by the CRC32 of its whole content; else (previews) by the cheap rule.
        ``official_unavailable`` when there is none. Read-only checks may prefer the current official game file with
        ``game_first``; an invalid kept original must still block writes.
        """
        check_resource_path(game_file, list(pin.dirs))

        def official(path: Path) -> bool:
            try:
                if full:
                    return self._is_official_as(path, game_file.name)
                stat_size = path.stat().st_size
                expected = self._official_size(game_file.name)
                return trailing_bytes(path) == 0 and (expected is None or expected == stat_size)
            except OSError:
                return False

        if game_first and official(game_file):
            return game_file
        original = self._original_path(game_file)
        if original.exists():
            if not self._original_checks.matches_digest(original):
                raise ServiceError("official_invalid", file=original)
            if full:
                verdict = self._judge_original(
                    original, self._hashes.get(original), self._steam_official(original.name, original)
                )
                if verdict is None:
                    raise ServiceError("official_invalid", file=original)
            else:
                verdict = self._original_checks.of_file(original, verified=True)
            # A kept file rejected by Steam can be bypassed in favor of an independently verified copy.
            if verdict != "failed":
                return original
        if official(game_file):
            return game_file
        backup = game_file.with_name(game_file.name + ".backup")
        if backup.is_file() and official(backup):
            return backup
        for other in self._elsewhere(game_file):
            if official(other):
                return other
        raise ServiceError("official_unavailable", file=game_file)

    def _keep_original(self, game_file: Path, pin: Pin, *, moving: bool = False) -> Path:
        """Keep verified official bytes, cloning where possible.

        A journaled write may move its own target; backups and files of another install must remain available in their
        current locations.
        """
        official = self._official_for(game_file, pin)
        original = self._original_path(game_file)
        if official != original:
            sha1 = self._hashes.get(official).sha1
            if not atomic_clone(official, original):
                if moving and official == game_file and not official.is_symlink():
                    verified_move(official, original, sha1)
                else:
                    atomic_copy(official, original)
            if self._original_checks.managed:
                self._original_checks.remember_digest(original, sha1)
            else:
                verify_file(original, sha1)
            if not self._is_official(original):
                if official.exists():
                    original.unlink()
                raise ServiceError("official_invalid", file=game_file)
        return official

    @toggle_timing.stage("official_stage")
    def _copy_verified_official(self, source: Path, output: Path) -> None:
        """Detach a kept official input and verify the staged bytes before they can be used."""
        if not self._original_checks.matches_digest(source):
            raise ServiceError("official_invalid", file=source)
        if not atomic_clone(source, output):
            atomic_copy(source, output)
        # Use the source's name to find the baseline even when the caller stages it elsewhere.
        if not self._original_checks.matches_digest(output, name=source.name):
            raise ServiceError("official_invalid", file=source)

    def _official_or_current(self, game_file: Path, pin: Pin) -> Path:
        """An omitted mod part uses official bytes when available, otherwise the current game bytes.

        This fallback is never an official baseline for rebuilding, keeping originals or restoring.
        """
        try:
            return self._official_for(game_file, pin)
        except ServiceError as error:
            if error.code != "official_unavailable":
                raise
            return game_file

    def _official_sha1(self, target: Path, pin: Pin) -> str | None:
        try:
            return self._hashes.get(self._official_for(target, pin)).sha1
        except (ServiceError, OSError):
            return None

    def _keep_replaced(self, targets: list[Path], pin: Pin) -> list[str]:
        """Store the game files about to be replaced that are kept nowhere else as library mods, one per core.

        It returns the ids of the mods it created. Each target is judged in full: official files go to originals/
        (_keep_original), files carrying a library source stay where they are, and a file changed outside the library
        (another tool, a manual copy), or the bytes of a source absent from the library, is kept. A part of a mod the
        user removed is never kept. At write strength a target's sha1 is compared with its record, never its size and
        date: _write has hashed every target already, so this reads nothing more.
        """
        view = self._view([pin], "full")
        keep: dict[str, list[Path]] = {}
        for target in targets:
            if self._replaced_unkept(target, pin, view):
                keep.setdefault(core_of(target.name), []).append(target)
        created = []
        for paths in keep.values():
            mod_id, new = self._add_group(paths, parse_filename(paths[0].name).core, pin)
            logger.info("Kept the replaced game file %s as mod %s", paths[0].name, mod_id)
            if new:
                created.append(mod_id)
        return created

    def _replaced_unkept(self, target: Path, pin: Pin, view: _View) -> bool:
        """Tell whether a game file about to be replaced holds bytes kept nowhere else, by the full judgement.

        A target whose sha1 equals its record carries the record's source, and is kept only when that source is gone
        from the library (never a removed mod's part) and the target is not official; any other target is kept when it
        is not official. A file whose header cannot be read is never kept.
        """
        fingerprint = self._hashes.get(target)
        record = self._applied.get(str(target.resolve()))
        if record and fingerprint.sha1 == record.get("sha1"):
            if record.get("removed"):
                return False
            # a held record has no bytes to show: the target's bytes are kept as a mod (held=False)
            if view.source_exists(
                record.get("source", ""), pin.key, resource_type_of_file(target.name) or "portrait", held=False
            ):
                return False
        elif fingerprint.trailing is None:
            return False
        return not self._is_official(target)

    def _drop_mods(self, mod_ids: list[str]) -> None:
        """Forget mods _keep_replaced added for a replacement that was rolled back.

        Their files are in the game again. The state is saved before a folder is deleted. A state that cannot be saved
        still lists them, so their folders stay: a record whose files are gone is held, never dropped.
        """
        if not mod_ids:
            return
        deletion = getattr(self, "_asset_deletion", None)
        if deletion:
            deletion.remember_mods(mod_ids)
            for mod_id in mod_ids:
                self._records.pop(mod_id, None)
            self._save()
            for mod_id in mod_ids:
                remove_tree(self.root / "mods" / mod_id)
            return
        removed = {mod_id: self._records.pop(mod_id) for mod_id in mod_ids if mod_id in self._records}
        try:
            self._save()
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not save the library state after a rollback", error)
            self._records.update(removed)  # still listed in state.json: they keep their folders
            return
        for mod_id in mod_ids:
            shutil.rmtree(self.root / "mods" / mod_id, ignore_errors=True)

    def _core_targets(self, files: Mapping[str, Path], pin: Pin) -> list[Path]:
        """The official parts of the core a mod's files belong to."""
        name = next(iter(files))
        if resource_type_of_file(name) == "other":
            return self._other_targets(dict(files), pin)
        return self._spine_parts(core_of(name), pin)

    def _needs_original(self, path: Path, record: dict[str, Any]) -> bool:
        """Tell whether a game file the library wrote to may still need the kept original of its name, by stat alone.

        It does not once its folder exists and the file is gone, nor while it is unchanged since the library wrote
        official bytes there. A folder that cannot be seen (an unmounted drive) is judged by its record. When unsure,
        the original stays: the cost is disk space, never a lost official copy.
        """
        try:
            if not path.parent.is_dir():
                return not says_official(record)
            if not path.is_file():
                return False
            stat = path.stat()
            return not (
                says_official(record) and record.get("size") == stat.st_size and record.get("mtime") == stat.st_mtime_ns
            )
        except OSError:
            return True

    def _needed_elsewhere(self, name: str, mine: set[str], pins: list[Pin]) -> bool:
        """Tell whether another file needs the kept original of a name.

        Another file is one of that name the library wrote to outside ``mine`` (resolved paths), judged by
        _needs_original, or one of that name in one of ``pins`` outside ``mine`` the library never wrote (it may be
        another tool's mod, with this original its only official copy). ``pins`` holds the installs this data directory
        removed from its list too (_all_pins).
        """
        for other, record in self._applied.items():
            if other not in mine and Path(other).name == name and self._needs_original(Path(other), record):
                return True
        for pin in pins:
            for path in self._index(pin).get(core_of(name), []):
                resolved = str(path.resolve())
                if path.name == name and resolved not in mine and resolved not in self._applied:
                    return True
        return False

    def _kept_for_configuration(
        self,
        name: str,
        config: dict | None,
        pins: list[Pin],
        *,
        catalog_official: AbstractSet[Path] = frozenset(),
    ) -> bool:
        """Tell whether a configured entry keeps the original of a name.

        It does when its game file of that name exists in that install, and the configured source may write non-official
        bytes there (no record there shows the configured source leaving that part official). When unsure, the original
        stays. While troubleshooting, the configuration saved to restore keeps its originals too: releasing them all
        would be a long pass for nothing.
        """
        core = core_of(name)
        saved = (config or {}).get("troubleshoot")
        saved = saved.get("folders") if isinstance(saved, dict) else None
        saved = saved if isinstance(saved, dict) else {}
        for pin in pins:
            slot = self._core_map(pin).slot(core)
            entry = ((((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}).get(slot)
            if not entry:
                kept = saved.get(pin.key)
                entry = kept.get(slot) if isinstance(kept, dict) else None
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                continue
            path = next((path for path in self._index(pin).get(core, []) if path.name == name), None)
            if path is None or path in catalog_official:
                continue
            record = self._applied.get(str(path.resolve()))
            if not (record and record.get("source") == entry["source"] and record.get("official") is True):
                return True
        return False

    def _release_failed(self, targets: list[Path], pin: Pin) -> None:
        """Release the originals a failed write kept, from the game files themselves.

        This is for a write that failed with its game files back as they were: the originals that same call kept are
        released from the game files, which hold the bytes just verified.
        """
        if not targets:
            return
        try:
            config = self.profiles.read()
        except ServiceError:
            return
        pins = self._all_pins()
        for target in targets:
            original = self._original_path(target)
            if self._kept_for_configuration(target.name, config, pins) or self._needed_elsewhere(
                target.name, {str(target.resolve())}, pins
            ):
                continue
            with contextlib.suppress(OSError):
                if not original.resolve().is_relative_to(self.root):
                    continue
                original.unlink()
                logger.info("Released the original of %s kept by a failed write", target.name)

    def _prune(self) -> list[Path]:
        """Serialize class 4 deletion with writes that may still need files for rollback.

        The library lock is held per original or catalog side file, yielding between items.
        A pending file journal or unreadable state prevents deletion.
        """
        if self._state_error is not None or any((self.root / "transactions").glob("*/journal.json")):
            return []
        with self._transaction_lock():
            records = self._catalog_document()["installs"]
            released = self._prune_originals(records)
            if records:
                self._prune_catalogs(self._all_pins(), records)
            return released

    def _prune_originals(self, catalogs: Mapping[str, dict] | None = None) -> list[Path]:
        """Delete unneeded kept originals in class 4, yielding the library lock between originals.

        An original about to go is read once for the mods of its core that have no fingerprint of their official
        parts yet (``_backfill_before_release``). A catalog core can release an original if the official-named file
        verifies and every other install still has what it needs. A files-mode original whose name is present is
        released once every file of that name is unchanged since the library wrote official bytes there. An original
        whose name is gone from every known install is deleted when a game update superseded it (a later-dated file of
        the same part in the folder of each of its gone records). Then the records of superseded gone files are dropped
        (with the entry's ``updated`` marker when the library wrote non-official bytes there), and the built copies
        whose names are gone.

        What it keeps is decided over the installs this data directory removed from its list too (``guarded``): a
        library shared with a data directory that names such an install keeps the same originals here as there, so
        removing an install from the list never changes what the prune keeps.
        """
        if catalogs is None:
            catalogs = self._catalog_document()["installs"]
        directory = self.root / "originals"
        if not directory.resolve().is_relative_to(self.root):
            return []
        try:
            config = self.profiles.read()
        except ServiceError:
            return []
        pins = self.installs()
        guarded = self._all_pins()
        listed = {path.name for path in directory.glob("*.bundle")} if directory.is_dir() else set()
        view = _View(self, guarded, config, "recorded", official_only=True)
        view.originals = {name: size for name, size in view.originals.items()}  # as listed at the start
        present: dict[str, list[Path]] = {}
        for names in view.names.values():
            for name, path in names.items():
                present.setdefault(name, []).append(path)
        known_dirs = {folder: pin for pin in guarded for folder in pin.dirs}
        released: list[Path] = []
        waited = 0.0
        for name in sorted(listed):
            # a preview or a page read waiting for the library goes first; nothing is decided between two originals
            waited += self._lock.step_aside() if hasattr(self._lock, "step_aside") else 0.0
            original = directory / name
            files = present.get(name, [])
            if files:
                catalog_official = set()
                for path in files:
                    pin = known_dirs.get(path.parent)
                    record = catalogs.get(pin.key) if pin else None
                    if record and record["mode"] == MODE_CATALOG:
                        core = view.maps[pin.key].slot(core_of(name))
                        with contextlib.suppress(OSError, ServiceError):
                            if self._catalog_targets(pin, self._spine_parts(core, pin), record) and self._is_official(
                                path
                            ):
                                catalog_official.add(path)
                if self._kept_for_configuration(name, config, guarded, catalog_official=catalog_official):
                    continue
                if not all(path in catalog_official or self._unchanged_official(path) for path in files):
                    continue
                if self._needed_elsewhere(name, {str(path.resolve()) for path in files}, guarded):
                    continue
            else:
                gone = [
                    Path(path)
                    for path in self._applied
                    if Path(path).name == name and Path(path).parent in known_dirs and not Path(path).exists()
                ]
                if not gone or not all(view.successor(known_dirs[path.parent], path) for path in gone):
                    continue
                if self._needed_elsewhere(name, {str(path) for path in gone}, guarded):
                    continue
            self._backfill_before_release(name)
            try:
                original.unlink()
            except OSError as error:
                log_failure(logger, logging.WARNING, "Could not delete the kept original", error)
                continue
            logger.info("Released the kept original %s", name)
            released.append(original)
        if waited:
            spans.record("library.prune.yield", waited)
        # superseded gone records: kept while an original of their name is, else dropped (with the marker)
        kept = {name for name in listed if (directory / name).exists()}
        stale: list[str] = []
        markers: dict[str, set[str]] = {}
        for pin in pins:
            for folder in pin.dirs:
                for path, record in view.by_folder.get(folder, []):
                    if path.exists() or not view.successor(pin, path) or path.name in kept:
                        continue
                    stale.append(str(path))
                    core = view.maps[pin.key].slot(core_of(path.name))
                    if (
                        view.says_nonofficial(path, record, present=False)
                        and view.configured(pin.key, core)
                        and not view.later_record(path, pin)
                    ):
                        markers.setdefault(pin.key, set()).add(core)
        built_drops: dict[str, set[str]] = {}
        for pin in pins:
            names = view.names[pin.key]
            for core, key in (view.folder(pin.key).get("built") or {}).items():
                wanted = [*(key.get("parts") or {}), *(key.get("inputs") or [])]
                if any(name not in names for name in wanted):
                    built_drops.setdefault(pin.key, set()).add(core)
        if stale:
            old_applied = dict(self._applied)
            for name in stale:
                self._applied.pop(name, None)
            try:
                self._save()
            except (OSError, ServiceError) as error:
                self._applied = old_applied
                log_failure(logger, logging.WARNING, "Could not save the library state after pruning", error)
                markers = {}
        if markers or built_drops:
            stamp = now()
            dropped: list[dict] = []

            def change(data: dict) -> bool:
                changed = False
                for key, cores in markers.items():
                    folder = data["folders"].get(key)
                    for core in cores:
                        if folder and core in folder["configured"]:
                            folder["updated"][core] = stamp
                            changed = True
                for key, cores in built_drops.items():
                    folder = data["folders"].get(key)
                    for core in cores:
                        if folder and core in folder["built"]:
                            dropped.append(folder["built"].pop(core))
                            changed = True
                return changed

            self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))
        self._pruned_cores.update(core_of(Path(name).name) for name in stale)
        self._pruned_cores.update(core for cores in built_drops.values() for core in cores)
        return released

    @_serialized
    def take_pruned_cores(self) -> set[str]:
        """Consume the cores changed by global pruning since the last replacement scan was queued."""
        cores, self._pruned_cores = self._pruned_cores, set()
        return cores

    def _unchanged_official(self, path: Path) -> bool:
        record = self._applied.get(str(path.resolve()))
        if not says_official(record):
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        return record.get("size") == stat.st_size and record.get("mtime") == stat.st_mtime_ns

    def _prune_quietly(self) -> None:
        # Writers already hold the library lock, possibly followed by transaction
        # and Mod locks. Do not reacquire it after those higher-order locks.
        if getattr(self, "_asset_deletion", None):
            return
        if self._deferred_prunes:
            self._prune_pending = True
            return
        try:
            self._prune()
        except Exception as error:  # never in the way of the switch itself; the next prune tries again
            log_failure(logger, logging.WARNING, "Could not prune the library files", error)

    @contextlib.contextmanager
    def import_batch(self) -> Iterator[None]:
        """Defer global pruning until the outermost import batch exits, keeping mod transactions independent."""
        with self._lock:
            self._deferred_prunes += 1
        try:
            yield
        finally:
            with self._lock:
                self._deferred_prunes -= 1
                if not self._deferred_prunes and self._prune_pending:
                    self._prune_pending = False
                    self._prune_quietly()

    @_serialized
    def prune(self) -> list[Path]:
        """Delete unneeded originals after official restoration or replacement by a game update.

        Originals another install may need, including configured entries restored by the
        next Off, are retained.

        Returns:
            Paths of the deleted kept originals.
        """
        return self._prune()

    def archive_rigs(self, pin: Pin | None = None, cancel: Callable[[], bool] | None = None) -> int:
        """Archive each distinct official portrait rig and its page alpha masks for an install.

        Read verified official copies with scan consent, storing each distinct
        atlas/skeleton pair once. Repeat a pass only when portrait filenames change;
        their CRCs identify the release. Each core holds the library lock only after
        RIG_ARCHIVE_QUIET without foreground use, letting pages, previews, and switches
        proceed first. Unreadable cores are skipped and failures are logged.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.
            cancel: Returns True to stop while yielding to foreground work; None completes the pass.

        Returns:
            Number of newly archived rigs, or zero if the pass cannot run or fails.
        """
        _background.on = True
        try:
            pin = pin or self.current_pin()
            if pin is None or not self._scan_allowed():
                return 0
            with self._lock:
                index = self._index(pin)
                cores = sorted(
                    core
                    for core, paths in index.items()
                    if paths and all(resource_type_of_file(path.name) == "portrait" for path in paths)
                )
                stamp = hashlib.sha1(
                    json.dumps([[core, sorted(p.name for p in index[core])] for core in cores]).encode()
                ).hexdigest()
            if self.rigs.stamp(pin.key) == stamp:
                return 0
            kept, waited = 0, 0.0
            with self.rigs.deferred():
                try:
                    for core in cores:
                        preview_gate.yield_to_previews(cancel)
                        wait = self._yield_to_foreground(cancel)
                        if wait is None:
                            return kept
                        waited += wait
                        kept += self._archive_core(core, pin)
                finally:
                    spans.record("library.rigs.yield", waited)
                self.rigs.set_stamp(pin.key, stamp)
            logger.info("Rig archive: %d portrait rigs kept of %d cores", kept, len(cores))
            return kept
        except Exception as error:  # never in the way of the program; the next start tries again
            log_failure(logger, logging.WARNING, "Could not keep the official portrait rigs", error)
            return 0
        finally:
            _background.on = False

    def _yield_to_foreground(self, cancel: Callable[[], bool] | None) -> float | None:
        """Wait until no other caller used the library's lock for ``RIG_ARCHIVE_QUIET`` seconds.

        Returns:
            How long it waited; None once cancelled.
        """
        started = time.monotonic()
        while True:
            if cancel is not None and cancel():
                return None
            quiet = time.monotonic() - self._lock.used
            if quiet >= RIG_ARCHIVE_QUIET:
                return time.monotonic() - started
            time.sleep(min(0.1, RIG_ARCHIVE_QUIET - quiet))

    def _let_others_in(self) -> float:
        """Wait between two steps of a paced job (a toggle) until no other caller used the library's lock for a while.

        The wait lasts until no other caller used the lock for ``TOGGLE_YIELD[0]`` seconds, at most ``TOGGLE_YIELD[1]``.
        A caller that waits for the lock notes it before it blocks, so the job steps aside for it at once.

        Returns:
            How long it waited.
        """
        quiet_for, longest = TOGGLE_YIELD
        started = time.monotonic()
        while True:
            now = time.monotonic()
            # a lock without the stamps: nothing to wait for
            quiet = 0.0 if getattr(self._lock, "waiting", 0) else now - getattr(self._lock, "asked", 0.0)
            if quiet >= quiet_for or now - started >= longest:
                return now - started
            time.sleep(max(0.0, min(quiet_for - quiet, longest - (now - started), 0.01)))

    @_serialized
    def _archive_core(self, core: str, pin: Pin) -> int:
        """Keep the official rigs of one portrait core (``archive_rigs``)."""
        try:
            parts = sorted(self._spine_parts(core, pin), key=lambda path: path.stat().st_size)
        except (OSError, ServiceError):
            return 0
        atlases: dict[str, bytes] = {}
        skeletons: dict[str, bytes] = {}
        holders: dict[str, Path] = {}  # texture key -> the verified official part that holds it
        for part in parts:
            try:
                official = self._official_for(part, pin, game_first=True)
                contents = integrity.inspect(official, allow_unknown=True)
            except (ServiceError, OSError, ValueError):
                continue
            atlases.update(contents.atlases)
            skeletons.update(contents.skeletons)
            holders.update({key: official for key in contents.textures})
        drawn_by = atlas_remap.pair_atlases(skeletons, atlases)
        kept = 0
        for name, text in atlases.items():
            skeleton = drawn_by.get(name)
            if skeleton is None or self.rigs.has(
                core, hashlib.sha1(text).hexdigest(), hashlib.sha1(skeletons[skeleton]).hexdigest()
            ):
                continue
            try:
                pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(text, name).pages}
                images = atlas_remap.read_inputs(sorted({holders[key] for key in pages if key in holders})).textures
            except (atlas_remap.RemapError, ValueError, UnicodeError, OSError, EOFError):
                continue
            masks = {key: rig_archive.alpha_mask(images[key]) for key in pages if key in images}
            if masks and self.rigs.add(
                core, name, text, skeleton, skeletons[skeleton], fingerprint.atlas(text)["layout"], masks
            ):
                kept += 1
        return kept

    def _mod_page_masks(self, paths: list[Path]) -> dict:
        """The alpha masks of a mod's own pages (texture key to mask), kept per file content."""
        key = tuple(sorted((path.name, self._hashes.get(path).sha1) for path in paths))
        masks = self._mod_masks.get(key)
        if masks is None:
            textures = atlas_remap.read_inputs(paths).textures
            masks = {name: rig_archive.alpha_mask(image) for name, image in textures.items()}
            if len(self._mod_masks) >= 256:
                self._mod_masks.clear()
            self._mod_masks[key] = masks
        return masks

    def _official_page_masks(self, paths: list[Path]) -> dict:
        """The alpha masks of the pages of verified official copies (texture key to mask), kept per file content."""
        return self._mod_page_masks(paths)

    def _archived_rigs(
        self, paths: list[Path], atlases: Mapping[str, bytes], current: Callable[[], Mapping[str, Any]] | None = None
    ) -> dict[str, dict]:
        """Find the rigs of an earlier release that a mod's textures were drawn for.

        The archive keeps them (``rig_archive.best``). It looks at the official ``atlases`` as they are now, where a
        mod's textures paint without the mod carrying them, and gives the ones its pages were drawn for in an earlier
        release the archive keeps: per atlas name a ``fingerprint.painted``-style entry (``text``, base64 ``skeleton``,
        ``layout``, ``archived``). ``current`` gives the installed release's page masks; without it, the archive's rig
        of the installed layout stands for them, and an atlas whose installed rig the archive does not keep is left out.
        Nothing is decoded unless the archive keeps a rig of another layout for the core. Never raises.
        """
        if not paths or not atlases:
            return {}
        core = core_of(paths[0].name)
        try:
            kept = self.rigs.rigs(core)
            now = {name: fingerprint.atlas(text)["layout"] for name, text in atlases.items()}
            stem = lambda name: Path(atlas_remap.texture_key(name)).stem
            candidates = {
                name: [rig for rig in kept if stem(rig.atlas) == stem(name) and rig.layout != layout]
                for name, layout in now.items()
            }
            if not any(candidates.values()):
                return {}
            key = (
                tuple(sorted((path.name, self._hashes.get(path).sha1) for path in paths)),
                tuple(sorted(now.items())),
                self.rigs.generation(),
                current is not None,
            )
            if key in self._rig_matches:
                return self._rig_matches[key]
            mod = self._mod_page_masks(paths)
            masks_now = current() if current is not None else None
            result = {}
            for name, rigs in candidates.items():
                installed = (
                    masks_now
                    if masks_now is not None
                    else next(
                        (rig.unpacked() for rig in kept if stem(rig.atlas) == stem(name) and rig.layout == now[name]),
                        None,
                    )
                )
                if not rigs or installed is None:
                    continue
                found = rig_archive.best(rigs, mod, installed)
                if found is None:
                    continue
                rig = found[0]
                text, skeleton = self.rigs.read(rig.atlas_sha1), self.rigs.read(rig.skeleton_sha1)
                if text is None or skeleton is None:
                    continue
                result[name] = {
                    "text": text.decode("utf-8-sig"),
                    "layout": rig.layout,
                    "archived": True,
                    "skeleton": base64.b64encode(skeleton).decode("ascii"),
                }
            if len(self._rig_matches) >= 256:
                self._rig_matches.clear()
            self._rig_matches[key] = result
            return result
        except (atlas_remap.RemapError, ValueError, UnicodeError, OSError, EOFError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not match a mod against the kept rigs", error)
            return {}

    @_serialized
    def recorded_rig(self, mod_id: str, atlases: Mapping[str, bytes] | None = None) -> dict[str, dict]:
        """Find the recorded or archived rigs a portrait mod's textures were drawn for.

        Return, per atlas basename, what its fingerprint recorded
        (``fingerprint.painted``, the entries that keep their skeleton) and, for the ``atlases`` its preview draws with
        (name to bytes) that no record covers, the earlier release the archive keeps that its pages follow
        (``_archived_rigs``). Empty for another mod. Its previews draw it on them where the atlas was repacked since
        (``fingerprint.drawn_on``).

        Args:
            mod_id: Stored mod ID; nonportrait mods return no rigs.
            atlases: Preview atlas bytes by name to match against the archive when unrecorded;
                None uses only the saved fingerprint.
        """
        record = self._record(mod_id)
        if record.get("resource_type") != "portrait":
            return {}
        painted = (self._read_built_against(record) or {}).get("painted") or {}
        known = {
            name: entry
            for name, entry in painted.items()
            if isinstance(entry.get("text"), str) and isinstance(entry.get("skeleton"), str)
        }
        unrecorded = {name: data for name, data in (atlases or {}).items() if integrity.basename(name) not in known}
        if unrecorded:
            archived = self._archived_rigs(self._paths(record), unrecorded)
            known.update({integrity.basename(name): entry for name, entry in archived.items()})
        return known

    @_serialized
    def rig_stamp(self, mod_id: str) -> str:
        """Stamp a stored portrait mod's archive state for preview-cache invalidation.

        Return empty for nonportrait mods or cores with at most one archived layout,
        so archiving only the installed rig does not redraw existing previews.
        """
        record = self._record(mod_id)
        if record.get("resource_type") != "portrait":
            return ""
        paths = self._paths(record)
        rigs = self.rigs.rigs(core_of(paths[0].name)) if paths else []
        if len({rig.layout for rig in rigs}) < 2:
            return ""
        return hashlib.sha1(json.dumps([[rig.atlas_sha1, rig.skeleton_sha1] for rig in rigs]).encode()).hexdigest()[:16]
