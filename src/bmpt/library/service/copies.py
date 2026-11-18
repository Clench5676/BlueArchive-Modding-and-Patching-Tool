"""Built copies, the configuration, entries and the scan, and adopting mods already in use."""

from __future__ import annotations

from ..records import stored_target

import contextlib
import copy
import hashlib
import logging
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from bmpt.engine import (
    LogFunc,
    ProgressCallback,
    no_log,
    parse_filename,
)
from ...core.errors import ServiceError
from ...core.openfiles import remove_tree
from .. import profiles as profiles_mod
from .. import mod_progress, safety, toggle_timing
from ..judge import (
    sorted_resource_types,
    LIBRARY_SOURCES,
    core_of,
    crc_state,
    resource_type_of_file,
    now,
)
from ..records import (
    AdoptResult,
    EntryState,
    Pin,
    ScanResult,
)
from ..storage import (
    Fingerprint,
    atomic_copy,
    read_tail,
)
from .base import (
    BUILT_TEMP_MAX_AGE,
    SPACE_MARGIN,
    _Entry,
    _Unread,
    _View,
    _build_version,
    _errors,
    _folder_size,
    _mod_writer,
    _serialized,
)


logger = logging.getLogger(__package__)


class CopiesMixin:
    """Built copies, the configuration, entries and the scan, and adopting mods already in use."""

    @toggle_timing.stage("build_signature")
    def _build_signature(self, sources: list[Path], targets: list[Path], pin: Pin, version: str | None = None) -> str:
        """Give what a build is made from.

        That is the program (or ``version``, a rule's own), the bytes of its inputs, the official bytes of its targets
        and the options. A game file written from it records this as its ``build``.
        """
        inputs = [(path.name, self._hashes.get(path).sha1) for path in sources]
        originals = [(path.name, self._hashes.get(self._official_for(path, pin)).sha1) for path in targets]
        options = (
            sorted(self.settings.asset_types()),
            self.settings.save_options(self.settings.crc_needed(targets[0])),
            self.settings.spine_options(),
            self.settings.anim_check_key(),
        )
        return hashlib.sha1(repr((1, version or _build_version(), inputs, originals, options)).encode()).hexdigest()

    def _cached_build(
        self,
        sources: list[Path],
        targets: list[Path],
        pin: Pin,
        build: Callable[[], dict[Path, Path]],
        build_keys: dict[Path, str],
    ) -> dict[Path, Path]:
        """Reuse only a referenced instant-On copy with the same program, inputs, official targets and options.

        Prepared files otherwise live solely in the caller's temporary staging directory.
        """

        def prepare() -> dict[Path, Path]:
            toggle_timing.prepared("rebuilt")
            with toggle_timing.stage("build_prepare"):
                return build()

        target_names = {path.name for path in targets}
        if len(target_names) != len(targets):
            return prepare()  # built keys identify parts by file name
        try:
            signature = self._build_signature(sources, targets, pin)
        except ServiceError as error:
            if error.code != "official_unavailable":
                raise
            return prepare()  # No official baseline to sign; never cache a live fallback as official.
        build_keys.update((target, signature) for target in targets)
        for folder in ((self.profiles.read() or {}).get("folders") or {}).values():
            for core_name, key in (folder.get("built") or {}).items():
                configured = (folder.get("configured") or {}).get(core_name) or {}
                if key.get("build") != signature or key.get("source") != configured.get("source"):
                    continue
                parts = key.get("parts") or {}
                if not parts or not set(parts) <= target_names:
                    continue
                try:
                    replacements = {}
                    for target in targets:
                        part = parts.get(target.name)
                        if part is None:
                            replacements[target] = self._official_or_current(target, pin)
                            continue
                        kept = self.root / "built" / f"{part['sha1']}.bundle"
                        if part.get("mod_file") and key["source"].startswith("mod:"):
                            kept = self.root / "mods" / key["source"][4:] / part["mod_file"]
                        fingerprint = self._hashes.get(kept)
                        if fingerprint.sha1 != part["sha1"]:
                            raise ValueError("Changed built copy")
                        if not self._kept_crc_fits(fingerprint, target):
                            raise ValueError("Built copy without the part's CRC")
                        replacements[target] = kept
                    if self._stale_build(replacements, pin):
                        continue
                    logger.debug("Reused configured build %s", signature)
                    toggle_timing.prepared("reused")
                    return replacements
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        start = time.perf_counter()
        replacements = prepare()
        logger.debug("Built %s in %.3f s", signature, time.perf_counter() - start)
        return replacements

    @toggle_timing.stage("built_lookup")
    def _built_replacements(
        self, pin: Pin, core: str, source: str, targets: list[Path], build_keys: dict[Path, str] | None = None
    ) -> dict[Path, Path] | None:
        """Give the entry's built copy written back, when it still fits.

        It fits when it was built for this very source, with every part's name and every input's name still in the
        install. The other parts take their official copies. A kept file whose bytes are not the recorded ones drops the
        key (the entry is rebuilt).
        """
        folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
        key = (folder.get("built") or {}).get(core)
        if not key:
            return None
        names = self._names(pin)
        parts = key.get("parts") or {}
        fits = (
            key.get("source") == source
            and parts
            and all(name in names for name in parts)
            and all(name in names for name in key.get("inputs") or [])
        )
        replacements: dict[Path, Path] = {}
        if fits:
            for target in targets:
                part = parts.get(target.name)
                if part is None:
                    replacements[target] = self._official_or_current(target, pin)
                    continue
                kept = (
                    (self.root / "mods" / source[4:] / part["mod_file"])
                    if part.get("mod_file") and source.startswith("mod:")
                    else self.root / "built" / f"{part.get('sha1')}.bundle"
                )
                try:
                    fingerprint = self._hashes.get(kept)
                    if fingerprint.sha1 != part.get("sha1") or not self._kept_crc_fits(fingerprint, target):
                        raise ValueError(kept)
                except (OSError, ValueError):
                    logger.info("The built copy of %s no longer fits, rebuilding", target.name)
                    fits = False
                    break
                replacements[target] = kept
        if fits and self._stale_build(replacements, pin):
            fits = False
        if fits:
            if build_keys is not None and key.get("build"):
                build_keys.update((target, key["build"]) for target in targets)
            toggle_timing.prepared("reused")
            return replacements
        self._drop_built(pin, [core])
        return None

    def _kept_crc_fits(self, fingerprint: Fingerprint, target: Path) -> bool:
        """Tell whether a built copy's part may go over ``target`` as it is.

        It may when its bytes carry the CRC of the target's name, or the target needs no CRC. An older build kept the
        bytes of a mod file written whole over a part whose name carries another CRC; such a copy is built again.
        """
        if crc_state(fingerprint.crc, fingerprint.trailing, target.name) != "needs_fix":
            return True
        return not self.settings.crc_needed(target)

    @_serialized
    def crc_repair_due(self) -> dict[str, Any]:
        """Report due and entries for managed files needing their target filename's CRC."""
        from .. import crc_repair

        return crc_repair.findings(self)

    @_serialized
    def repair_crc(self, log: LogFunc = no_log, progress: ProgressCallback | None = None) -> Any:
        """Repair managed files lacking their target CRC through the protected game writer.

        Args:
            log: Receives user-facing operation messages.
            progress: Receives (completed entry, total entries, message); None disables reporting.
        """
        from .. import crc_repair

        return crc_repair.repair(self, log, progress)

    def _current_builds(self, pin: Pin, core: str, record: dict[str, Any], targets: list[Path]) -> set[str]:
        """Give the builds a write of a mod over an entry's game files signs now.

        They are signed as apply and switch sign them (``_cached_build``); those that cannot be told are left out.
        """

        def sign(sources: Callable[[], list[Path]], group: Callable[[], list[Path]]) -> str | None:
            try:
                files = group()
                if not files or len({path.name for path in files}) != len(files):
                    return None  # built keys identify parts by file name: such a write signs nothing
                return self._build_signature(sources(), files, pin)
            except (OSError, ServiceError):
                return None

        if record["resource_type"] == "other":
            # a switch signs one slot's files, an apply the files of the whole mod: either may have written a part
            signatures = {
                sign(lambda: self._paths(record), lambda: targets),
                sign(lambda: self._paths(record), lambda: self._game_files(stored_target(record), "other", pin)),
            }
        elif record["resource_type"] == "chibi" or core_of(record["files"][0]) == core:
            signatures = {sign(lambda: self._paths(record), lambda: targets)}
        else:
            signatures = {sign(lambda: self._preview(record, pin), lambda: targets)}
        signatures.discard(None)
        return signatures

    @toggle_timing.stage("keep_built")
    def _make_built(self, pin: Pin, entry: _Entry) -> None:
        """Keep the exact bytes of an entry's modded parts before an Off replaces them, whole.

        That is every non-official part of one write of its configured source, as a file of the mod in mods/<id>/ when
        one holds those bytes, else a copy in built/<sha1>.bundle. The key replaces any earlier one. Nothing is kept
        when a part of a mod was not written from the mod's current files (the mod was updated since): a key naming only
        some parts would put official bytes over the others, and the old build must not come back. The entry is built
        again by the On.
        """
        source = entry.configured
        parts: dict[str, dict[str, Any]] = {}
        mod_files: dict[str, str] = {}
        record = self._records.get(source[4:]) if source.startswith("mod:") else None
        if record is not None:
            for path in self._paths(record):
                with contextlib.suppress(OSError):
                    mod_files[self._hashes.get(path).sha1] = path.name
        modded = [part for part in entry.parts if not part.official and part.record]
        current: set[str] | None = None
        for part in modded:
            if record is None or part.record["sha1"] in mod_files:
                continue
            if current is None:
                current = self._current_builds(pin, entry.core, record, [part.path for part in entry.parts])
            if part.record.get("build") not in current:
                logger.info(
                    "Not keeping the built copy of %s: %s was not written from the current %s",
                    entry.core,
                    part.path.name,
                    source,
                )
                return
        for part in modded:
            sha1 = part.record["sha1"]
            if sha1 in mod_files:
                parts[part.path.name] = {"sha1": sha1, "mod_file": mod_files[sha1]}
                continue
            built = self.root / "built" / f"{sha1}.bundle"
            if not built.exists():
                atomic_copy(part.path, built)
            parts[part.path.name] = {"sha1": sha1, "mod_file": None}
        if not parts:
            return
        inputs = self._inputs(pin, entry.core, source)
        key = {"source": source, "inputs": inputs, "parts": parts}
        signatures = {(part.record or {}).get("build") for part in entry.parts}
        if len(signatures) == 1 and None not in signatures:
            key["build"] = signatures.pop()
        replaced: list[dict] = []

        def change(data: dict) -> None:
            built = self._folder(data, pin)["built"]
            if entry.core in built:
                replaced.append(built[entry.core])
            built[entry.core] = key

        self._update_profiles(change, after=lambda data: self._delete_built_files(replaced, data))
        # another instance may have dropped the last reference before the new key was saved
        recovered = False
        for part in entry.parts:
            kept = parts.get(part.path.name)
            if kept is not None and kept["mod_file"] is None:
                built = self.root / "built" / f"{kept['sha1']}.bundle"
                if not built.exists():
                    atomic_copy(part.path, built)
                    recovered = True
        if recovered:
            self._update_profiles(lambda data: False, after=lambda data: self._delete_built_files([key], data))

    def _inputs(self, pin: Pin, core: str, source: str) -> list[str]:
        """List the other game files a build of a source reads.

        They are an adapted mod's official parts of its own core, another alt's files of the kind; none for a mod
        made for the core.
        """
        index = self._index(pin)
        if source.startswith("mod:") and source[4:] in self._records:
            record = self._records[source[4:]]
            own = core_of(record["files"][0])
            if record["resource_type"] == "chibi" or own == core:
                return []
            targets = index.get(own, [])
            replaced = self._replaced_parts({path.name: path for path in self._paths(record)}, targets, pin)
            return sorted(path.name for path in targets if path not in replaced)
        if source.startswith("alt:") and self._resource_type(core, pin) == "chibi":
            try:
                return sorted(path.name for path in self._costume_files(source[4:], pin)[2])
            except ServiceError:
                return []
        if source.startswith("alt:"):
            other = self._core_map(pin).core(source[4:], self._resource_type(core, pin) or "portrait")
            return sorted(path.name for path in index.get(other.lower(), [])) if other else []
        return []

    def _drop_built(
        self, pin_or_key: Pin | str, cores: list[str] | None = None, when: Callable[[dict], bool] | None = None
    ) -> None:
        """Drop built keys of an install, deleting the built/ files no other key references.

        The keys dropped are the given cores', or those ``when`` picks; with neither, all.
        """
        target = pin_or_key.key if isinstance(pin_or_key, Pin) else pin_or_key
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            folder = data["folders"].get(target)
            if not folder:
                return False
            for core_name in list(folder["built"]):
                if (cores is not None and core_name not in cores) or (
                    when is not None and not when(folder["built"][core_name])
                ):
                    continue
                dropped.append(folder["built"].pop(core_name))
            return bool(dropped)

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    def _delete_built_files(self, keys: list[dict], data: dict | None) -> None:
        """Delete dropped keys' files no key references, under the profiles lock, against the saved configuration."""
        if not keys:
            return
        # a part kept as a file of its mod reads mods/<id>/, so only the others hold their built/ file
        referenced = {
            part.get("sha1")
            for folder in ((data or {}).get("folders") or {}).values()
            for key in (folder.get("built") or {}).values()
            for part in (key.get("parts") or {}).values()
            if not part.get("mod_file")
        }
        for key in keys:
            for part in (key.get("parts") or {}).values():
                sha1 = part.get("sha1")
                if part.get("mod_file") or not sha1 or sha1 in referenced:
                    continue
                path = self.root / "built" / f"{sha1}.bundle"
                if path.resolve().is_relative_to(self.root) and path.parent == self.root / "built":
                    deletion = getattr(self, "_asset_deletion", None)
                    if deletion:
                        deletion.remember_built(path)
                        path.unlink(missing_ok=True)
                    else:
                        with contextlib.suppress(OSError):
                            path.unlink()

    @_serialized
    def clear_built(self) -> int:
        """Clear stored built bundles, costume previews, stale temporaries, and their build keys.

        Delete built/*.bundle and every built key with a part kept there.
        Keys whose parts are all files of their mod stay: they use no disk. The kept originals stay (Off needs
        them).

        Returns:
            Byte count of the built files selected for cleanup.
        """
        directory = self.root / "built"
        freed = _folder_size(directory)

        def change(data: dict) -> bool:
            changed = False
            for folder in data["folders"].values():
                for core_name in list(folder["built"]):
                    if any(
                        not part.get("mod_file") for part in (folder["built"][core_name].get("parts") or {}).values()
                    ):
                        del folder["built"][core_name]
                        changed = True
            return changed

        def after(data: dict) -> None:
            nonlocal freed
            for path in directory.glob("*.bundle"):
                with contextlib.suppress(OSError):
                    path.unlink()
            previews = self.cache_root / "built" / "previews"
            if previews.is_dir():
                for part in previews.rglob("*.bundle"):
                    with contextlib.suppress(OSError):
                        freed += part.stat().st_size
                with contextlib.suppress(OSError):
                    remove_tree(previews)
            cutoff = time.time() - BUILT_TEMP_MAX_AGE
            for path in directory.glob(".library-*"):
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    if stat.st_mtime <= cutoff:
                        path.unlink()
                        freed += stat.st_size

        self._update_profiles(change, after=after)
        return freed

    def _update_profiles(self, change: Callable[[dict], Any], after: Callable[[dict], Any] | None = None) -> Any:
        """Make one change of profiles.json, then ``after`` under its lock.

        Neither callback calls into Profiles or the library's public methods. Returns what the change returned when it
        is not a bool, else the configuration as written.
        """
        result: list[Any] = []

        def wrapped(data: dict) -> Any:
            value = change(data)
            result.append(value)
            return value

        data = self.profiles.update(wrapped, after=after)
        value = result[0] if result else None
        return data if value is None or isinstance(value, bool) else value

    def _folder(self, data: dict, pin: Pin) -> dict:
        """Give the install's record in a configuration, created when missing.

        A new record gets this data directory's id in named_by when its settings name the install.
        """
        folders = data["folders"]
        folder = folders.get(pin.key)
        if folder is None:
            folder = folders[pin.key] = {
                "server": pin.server,
                "named_by": {},
                "hidden": [],
                "configured": {},
                "updated": {},
                "built": {},
                "dismissed": {},
            }
            if any(named.key == pin.key for named in self._named_pins()):
                folder["named_by"][self.data_id] = now()
        return folder

    def _configure(
        self,
        pin: Pin,
        alt_id: str,
        resource_types: dict[str, str],
        source: str | None,
        *,
        dropped: list[dict] | None = None,
    ) -> None:
        """Set (or, with None, drop) the configured source of an entry's cores.

        A changed or dropped configuration drops the entry's built copy and its updated marker. With ``dropped``, the
        dropped built keys go there and their files stay until the caller deletes them (``_ChoiceChange.close``), else
        they are deleted at once.
        """
        resource_types = {
            resource_type: self._core_map(pin).slot(core) if resource_type == "chibi" else core
            for resource_type, core in resource_types.items()
        }
        stamp = now()
        dropped_keys: list[dict] = []
        named = any(other.key == pin.key for other in self._named_pins())

        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            if folder is None:
                if source is None:
                    return False
                folder = self._new_folder(data, pin, named)
            changed = False
            for resource_type, core_name in resource_types.items():
                if core_name.startswith("other:"):
                    resource_type = "other"
                current = folder["configured"].get(core_name)
                if source is None:
                    if current is None:
                        continue
                    del folder["configured"][core_name]
                elif current and current.get("source") == source:
                    continue
                else:
                    folder["configured"][core_name] = {
                        "alt": alt_id,
                        "resource_type": resource_type,
                        "source": source,
                        "since": stamp,
                    }
                changed = True
                folder["updated"].pop(core_name, None)
                if core_name in folder["built"]:
                    dropped_keys.append(folder["built"].pop(core_name))
            return changed

        if dropped is not None:
            self._update_profiles(change)
            dropped.extend(dropped_keys)
            return
        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped_keys, data))

    def _check_stopping(self, pin: Pin) -> None:
        """Check the refusals of the moment that the write-time checks (``_writable``) would give whatever the files.

        They are the classic UI open, Steam changing the install, the game running. They are checked before a choice is
        saved, so a write refused for one of them (STOPPING) leaves the choice as it was and costs no build.
        """
        if self._write_check is not None:
            self._write_check(pin)
        self._check_idle([pin])
        safety.check_game_for(list(pin.dirs))

    def _saved_choices(self, pin: Pin, resource_types: dict[str, str]) -> dict[str, dict[str, Any]]:
        """Give the saved entries of an entry's cores (configured, updated marker, built key).

        ``_put_back_choices`` restores them.
        """
        folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
        cores = [
            self._core_map(pin).slot(core) if resource_type == "chibi" else core
            for resource_type, core in resource_types.items()
        ]
        return {
            core: {
                part: copy.deepcopy((folder.get(part) or {}).get(core)) for part in ("configured", "updated", "built")
            }
            for core in cores
        }

    def _put_back_choices(self, pin: Pin, saved: dict[str, dict[str, Any]]) -> None:
        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            if folder is None:
                return False
            for core_name, parts in saved.items():
                for part, value in parts.items():
                    if value is None:
                        folder.setdefault(part, {}).pop(core_name, None)
                    else:
                        folder.setdefault(part, {})[core_name] = copy.deepcopy(value)
            return True

        self._update_profiles(change)

    def _new_folder(self, data: dict, pin: Pin, named: bool) -> dict:
        folder = data["folders"][pin.key] = {
            "server": pin.server,
            "named_by": {},
            "hidden": [],
            "configured": {},
            "updated": {},
            "built": {},
            "dismissed": {},
        }
        if named:
            folder["named_by"][self.data_id] = now()
        return folder

    def _clear_marker(self, pin: Pin, core: str) -> None:
        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            return bool(folder) and folder["updated"].pop(core, None) is not None

        with contextlib.suppress(ServiceError):
            if core in ((((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}).get("updated") or {}):
                self._update_profiles(change)

    def _lookup_records(self) -> None:
        """Find each named install's record at each build.

        That is its own; a moved install's, re-keyed; else one migrated from what the library already wrote there. Then
        refresh named_by and hidden of the records that exist. The scan consent is asked before profiles.json is locked.
        """
        config = self.profiles.read()
        allowed = self._scan_allowed()
        named = self._named_pins()
        folders = (config or {}).get("folders") or {}
        moves: dict[str, str] = {}
        migrations: dict[str, dict[str, dict]] = {}
        for pin in named:
            if pin.key in folders:
                continue
            gone = [
                key
                for key, folder in folders.items()
                if key not in moves.values()
                and (folder.get("server") or "global") == pin.server
                and not Path(key).exists()
                and Path(key).parent.is_dir()
            ]
            if len(gone) == 1:
                moves[pin.key] = gone[0]
                continue
            migration = self._migration(pin, config)
            if migration:
                migrations[pin.key] = migration
        for new, old in moves.items():
            self._rekey(Path(old), next(pin for pin in named if pin.key == new), allowed)
        if not folders and not migrations:
            return
        named_keys = {pin.key for pin in named}
        pins = {pin.key: pin for pin in named}
        stamp = now()

        def change(data: dict) -> None:
            for new, old in moves.items():
                if new in data["folders"] or old not in data["folders"]:
                    continue
                data["folders"][new] = data["folders"].pop(old)
                for item in data["unfinished"]:
                    if item["install"] == old:
                        item["install"] = new
                logger.info("Moved the mod configuration of %s to %s", old, new)
            for key, configured in migrations.items():
                if key in data["folders"]:
                    continue
                folder = self._new_folder(data, pins[key], True)
                folder["configured"] = configured
                logger.info("Configured %d entries of %s from what the library wrote there", len(configured), key)
            for key, folder in data["folders"].items():
                if key in named_keys:
                    folder["named_by"][self.data_id] = stamp
                    if self.data_id in folder["hidden"]:
                        folder["hidden"].remove(self.data_id)
                else:
                    folder["named_by"].pop(self.data_id, None)

        self.profiles.update(change)

    def _rekey(self, old_root: Path, pin: Pin, allowed: bool) -> None:
        """Follow a moved install: its records go to the same relative paths where the file matches.

        A file matches by size and date, or (with scan consent: the new paths are not the library's yet) by size and
        last bytes. Nothing is hashed.
        """
        moved = 0
        for name, record in list(self._applied.items()):
            path = Path(name)
            if not path.is_relative_to(old_root):
                continue
            new = pin.root / path.relative_to(old_root)
            try:
                stat = new.stat()
            except OSError:
                continue
            match = stat.st_size == record.get("size") and stat.st_mtime_ns == record.get("mtime")
            if not match and allowed and record.get("tail") and stat.st_size == record.get("size"):
                match = read_tail(new) == record["tail"]
            if match:
                del self._applied[name]
                self._applied[str(new.resolve())] = {**record, "mtime": stat.st_mtime_ns}
                moved += 1
        if moved:
            self._save()
            logger.info("Moved %d records of %s to %s", moved, old_root, pin.root)

    def _migration(self, pin: Pin, config: dict | None) -> dict[str, dict]:
        """Derive the configuration of an install without a record, from what the library already wrote there.

        It uses recorded paths and names only. It holds every entry whose non-official parts all carry one library
        source, and every entry whose present parts carry none while its gone records (renamed by a game update) all say
        non-official bytes of one source.
        """
        view = _View(self, [pin], config, "recorded")
        stamp = now()
        configured: dict[str, dict] = {}
        by_core: dict[str, list[tuple[Path, dict]]] = {}
        for folder in pin.dirs:
            for path, record in view.by_folder.get(folder, []):
                core = view.maps[pin.key].slot(core_of(path.name))
                if resource_type_of_file(path.name):
                    by_core.setdefault(core, []).append((path, record))
        for core, records in sorted(by_core.items()):
            resource_type = self._resource_type(core, pin)
            parts = []
            for path in self._spine_parts(core, pin):
                if str(path.resolve()) in view.applied:
                    with contextlib.suppress(OSError, _Unread):
                        parts.append(view.part(path, pin, resource_type))
            nonofficial = [part for part in parts if not part.official]
            sources = {part.carried for part in nonofficial}
            source = None
            if nonofficial:
                if len(sources) == 1 and next(iter(sources)).startswith(LIBRARY_SOURCES):
                    source = next(iter(sources))
            else:
                gone = [
                    (path, record)
                    for path, record in records
                    if not path.exists() and path.parent.is_dir() and view.successor(pin, path)
                ]
                gone_sources = {
                    record.get("source") for path, record in gone if view.says_nonofficial(path, record, present=False)
                }
                if (
                    gone
                    and len(gone_sources) == 1
                    and all(view.says_nonofficial(path, record, present=False) for path, record in gone)
                ):
                    candidate = next(iter(gone_sources))
                    if (
                        candidate
                        and candidate.startswith(LIBRARY_SOURCES)
                        and view.source_exists(candidate, pin.key, resource_type)
                    ):
                        source = candidate
            if source is None:
                # Applied records preserve a choice without explicit configuration, even after
                # an external reset; selection() separately verifies installation.
                remembered = {
                    record.get("source")
                    for path, record in records
                    if view.says_nonofficial(path, record, present=path.exists())
                }
                if len(remembered) == 1:
                    candidate = next(iter(remembered))
                    if (
                        candidate
                        and candidate.startswith(LIBRARY_SOURCES)
                        and view.source_exists(candidate, pin.key, resource_type)
                    ):
                        source = candidate
            if source:
                configured[core] = {
                    "alt": view.maps[pin.key].key(core) or core,
                    "resource_type": resource_type,
                    "source": source,
                    "since": stamp,
                }
        return configured

    def _entry_state(self, view: _View, entry: _Entry) -> EntryState:
        names = None
        if self.catalog.alt_for_core(entry.core) is None:
            names = view.maps[entry.pin.key].names.get(entry.alt)
        unknown = [part for part in entry.parts if part.carried == "unknown"]
        return EntryState(
            entry.pin.key,
            entry.core,
            entry.alt,
            names,
            entry.resource_type,
            entry.carried,
            entry.configured,
            entry.state,
            entry.overwritten,
            any(part.trailing for part in unknown),
            sum(part.size for part in unknown),
            [self._file_state(view, entry, part) for part in entry.parts],
            entry.dismissed,
        )

    def entries(self, pins: list[Pin] | None = None, include_dismissed: bool = False) -> list[EntryState]:
        """Scan configured or exceptional entries in the selected installs.

        Include entries that are configured, untracked, damaged,
        overwritten or not in their intended state. It reads headers of files the library did not write: it needs
        the scan consent (``scan_off``).

        Args:
            pins: Game installs and servers the operation is fixed to; None scans every known install.
            include_dismissed: Whether to clear dismissed flags so callers can display their prompts again.
        """
        return self.scan(pins, include_dismissed=include_dismissed).entries

    def scan(
        self,
        pins: list[Pin] | None = None,
        cancel: Callable[[], bool] | None = None,
        include_dismissed: bool = False,
        cores: set[str] | None = None,
    ) -> ScanResult:
        """Scan entries cheaply outside the library lock and report counts per install.

        With scan consent, copy records under the library lock and inspect headers
        and recorded-file tails outside it. A concurrent library write returns an
        empty stale result. Save source takeovers and drop configuration referencing
        sources absent from the library.

        Args:
            pins: Game installs and servers the operation is fixed to; None scans every known install.
            cancel: Returns True to stop before the next entry; None completes the scan.
            include_dismissed: Whether to clear dismissed flags in reported entries.
            cores: Core names restricting each install's scan; None includes every relevant slot.

        Raises:
            ServiceError: Scan consent is absent (scan_off), or a required library read fails.
        """
        if not self._scan_allowed():
            raise ServiceError("scan_off")
        # the kept originals Steam's list names and no verdict covers yet, hashed once each
        with contextlib.suppress(OSError, ServiceError):
            self.check_originals(cancel)
        with self._lock, _errors("scan"):
            pins = list(pins) if pins is not None else self.installs()
            view = self._view(pins, "scan", snapshot=True)
            version = self._version
        found = view.entries(pins, cancel, cores, background=True)
        with self._lock, _errors("scan"):
            if self._version != version:
                return ScanResult([], {}, stale=True)
            self._settle(view, found)
            result = [self._entry_state(view, entry) for entry in found]
        if include_dismissed:
            result = [replace(entry, dismissed=False) for entry in result]
        counts: dict[str, dict[str, int]] = {}
        for entry in result:
            count = counts.setdefault(entry.install, {})
            name = "stuck" if entry.state == "pending" and entry.overwritten else entry.state
            count[name] = count.get(name, 0) + 1
            if entry.configured:
                count["configured"] = count.get("configured", 0) + 1
        return ScanResult(result, counts)

    def _settle(self, view: _View, found: list[_Entry]) -> None:
        """Settle in the configuration what the judgement finds.

        That is take-overs (a newer library write than the configured source, or a library source with none) and sources
        gone from the library (dropped once listed); and the new dates of files the library knows by their last bytes.
        """
        if self._writes_refused():
            return  # a recovery that has failed refuses changes: the next scan settles
        restamps = {
            str(part.path.resolve()): part.restamp
            for entry in found
            for part in entry.parts
            if part.restamp is not None and part.record is not None
        }
        if restamps:
            for path, mtime in restamps.items():
                if path in self._applied:
                    logger.info("Same bytes, new date: %s", Path(path).name)
                    self._applied[path]["mtime"] = mtime
            with contextlib.suppress(OSError, ServiceError):
                self._save()
        takeovers = [entry for entry in found if entry.takeover]
        missing = [entry for entry in found if entry.state == "source_missing"]
        if not takeovers and not missing:
            return
        stamp = now()
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            changed = False
            for entry in takeovers:
                folder = self._folder(data, entry.pin)
                current = (folder["configured"].get(entry.core) or {}).get("source")
                expected = (view.configured(entry.pin.key, entry.core) or {}).get("source")
                if current != expected:
                    continue
                logger.info("Configured %s in %s with %s (taken over)", entry.core, entry.pin.key, entry.takeover)
                folder["configured"][entry.core] = {
                    "alt": entry.alt,
                    "resource_type": entry.resource_type,
                    "source": entry.takeover,
                    "since": stamp,
                }
                if entry.core in folder["built"]:
                    dropped.append(folder["built"].pop(entry.core))
                changed = True
            for entry in missing:
                folder = data["folders"].get(entry.pin.key)
                if folder and entry.core in folder["configured"]:
                    logger.info(
                        "Dropped the configuration of %s in %s: %s is gone from the library",
                        entry.core,
                        entry.pin.key,
                        entry.configured,
                    )
                    del folder["configured"][entry.core]
                    folder["updated"].pop(entry.core, None)
                    if entry.core in folder["built"]:
                        dropped.append(folder["built"].pop(entry.core))
                    changed = True
            return changed

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    def adopt(
        self,
        entries: list[dict],
        pins: list[Pin] | None = None,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> AdoptResult:
        """Adopt non-official game parts into managed mods without changing game bytes.

        Each entry still untracked or changed is stored as one mod of its non-official parts, every part is
        recorded, the entry configured with it and its built copy kept as the mod's own files. Official copies are
        kept in originals/ while they exist.

        Args:
            entries: Install/core descriptors to adopt if they remain untracked or changed.
            pins: Game installs and servers the operation is fixed to; None uses every known install.
            log: Reserved operation logger; this adoption path reports entry results instead of log lines.
            progress: Receives per-entry import dictionaries; None disables reporting.
            cancel: Returns True to stop before the next entry; None completes the job.

        Returns:
            Added or matched mods, failed/skipped entries, and affected cores/kinds.

        Raises:
            ServiceError: The install list changed or storage cannot retain a 512 MiB free-space margin.
        """
        with self._lock, _errors("adopt"):
            pins = list(pins) if pins is not None else self.installs()
            self._check_pins(pins)
            by_key = {pin.key: pin for pin in pins}
            wanted = [
                (by_key[item["install"]], item["core"].lower())
                for item in entries
                if isinstance(item, dict) and item.get("install") in by_key and isinstance(item.get("core"), str)
            ]
            view = self._view(pins, "student")
            need = 0
            for pin, core in wanted:
                with contextlib.suppress(OSError, _Unread):
                    entry = view.entry(pin, core)
                    need += sum(part.size for part in entry.parts if part.carried == "unknown")
                    need += sum(
                        part.size
                        for part in entry.parts
                        if not part.official and entry.copies.get(str(part.path)) in ("backup", "elsewhere")
                    )
            self._check_space(need)
        added, failed, skipped, cores, resource_types = [], [], [], set(), set()
        line = mod_progress.ItemProgress(progress, len(wanted), "add")
        config = self.profiles.read() if progress else None
        for i, (pin, core) in enumerate(wanted, 1):
            if cancel and cancel():
                break
            with self._lock:
                if progress:
                    line.item(i, **self._entry_identity(pin, core, config), step=mod_progress.STEP_ADD)
                try:
                    with _errors("adopt"):
                        mod_id = self._adopt_one(pin, core)
                except ServiceError as error:
                    if error.code == "entry_changed":
                        skipped.append({"install": pin.key, "core": core, "why": "entry_changed"})
                    else:
                        failed.append(
                            {
                                "entry": {"install": pin.key, "core": core},
                                "code": error.code,
                                "params": error.to_json()["params"],
                            }
                        )
                    continue
                added.append(self._entry(self._records[mod_id]))
                cores.add(core)
                resource_types.add(self._resource_type(core, pin) or "portrait")
            line.done()
        return AdoptResult(added, failed, skipped, sorted(cores), sorted_resource_types(resource_types))

    def _check_space(self, need: int) -> None:
        if need <= 0:
            return
        folder = self.root
        while not folder.exists() and folder.parent != folder:
            folder = folder.parent
        free = shutil.disk_usage(folder).free
        if need + SPACE_MARGIN > free:
            raise ServiceError("not_enough_space", need=need, free=free)

    @_mod_writer
    def _adopt_one(self, pin: Pin, core: str, mod_id: str | None = None) -> str:
        """Adopt one entry, under the library lock.

        Its files are read in full (they are copied anyway). Rolled back as a whole on failure; a mod already in the
        library stays as it was.
        """
        view = self._view([pin, *[p for p in self.installs() if p.key != pin.key]], "full")
        entry = view.entry(pin, core)
        core = entry.core
        if entry.state not in ("untracked", "changed"):
            raise ServiceError("entry_changed", core=core)
        nonofficial = [part for part in entry.parts if not part.official]
        if not nonofficial:
            raise ServiceError("entry_changed", core=core)
        old_applied = copy.deepcopy(self._applied)
        if mod_id is None:
            mod_id = next(
                (
                    record["id"]
                    for record in self._records.values()
                    if self._matches_installed(self._identity({path.name: path for path in self._paths(record)}), entry)
                ),
                None,
            )
        if mod_id is None:
            mod_id, created = self._add_group(
                [part.path for part in nonofficial], parse_filename(nonofficial[0].path.name).core, pin
            )
        else:
            record = self._record(mod_id)
            if not self._matches_installed(self._identity({path.name: path for path in self._paths(record)}), entry):
                raise ServiceError("entry_changed", core=core)
            created = False
        source = f"mod:{mod_id}"
        try:
            for part in nonofficial:
                with contextlib.suppress(ServiceError):  # overwritten: no official copy to keep
                    self._keep_original(part.path, pin)
            stamp = now()
            for part in entry.parts:
                fingerprint = self._hashes.get(part.path)
                stat = part.path.stat()
                self._applied[str(part.path.resolve())] = {
                    "source": source,
                    "sha1": fingerprint.sha1,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime_ns,
                    "official": part.official,
                    "at": stamp,
                    "tail": read_tail(part.path) if fingerprint.trailing else None,
                }
            self._save()
            files = {self._hashes.get(path).sha1: path.name for path in self._paths(self._records[mod_id])}
            built = {
                "source": source,
                "inputs": [],
                "parts": {
                    part.path.name: {
                        "sha1": self._hashes.get(part.path).sha1,
                        "mod_file": files.get(self._hashes.get(part.path).sha1),
                    }
                    for part in nonofficial
                },
            }
            alt = entry.alt
            named = any(other.key == pin.key for other in self._named_pins())
            replaced: list[dict] = []

            def change(data: dict) -> None:
                folder = data["folders"].get(pin.key) or self._new_folder(data, pin, named)
                folder["configured"][core] = {
                    "alt": alt,
                    "resource_type": entry.resource_type,
                    "source": source,
                    "since": stamp,
                }
                folder["updated"].pop(core, None)
                if core in folder["built"]:
                    replaced.append(folder["built"][core])
                folder["built"][core] = built

            self._update_profiles(change, after=lambda data: self._delete_built_files(replaced, data))
        except Exception:
            self._applied = old_applied
            with contextlib.suppress(OSError, ServiceError):
                self._save()
            if created:
                self._drop_mods([mod_id])
            raise
        logger.info("Added %s of %s to the library as mod %s", core, pin.key, mod_id)
        return mod_id

    def intent(self) -> dict[str, Any]:
        """Read the shared switch intent and unfinished counts for this directory's known installs.

        Returns:
            enabled, toggled, unfinished counts for pending/left/stuck/held, and held
            entries with install, core, alt, resource type, source, and any stored mod name.

        Raises:
            ServiceError: The configuration cannot be read (mod_configuration_unreadable).
        """
        config = self.profiles.read()
        keys = {pin.key for pin in self.installs()}
        counts = {why: 0 for why in profiles_mod.WHY}
        held = []
        for item in (config or {}).get("unfinished", []):
            if item["install"] in keys:
                counts[item["why"]] += 1
                if item["why"] == "held":
                    entry = (
                        (((config or {}).get("folders") or {}).get(item["install"]) or {}).get("configured") or {}
                    ).get(item["core"]) or {}
                    source = entry.get("source")
                    record = (
                        self._records.get(source[4:]) if isinstance(source, str) and source.startswith("mod:") else None
                    )
                    held.append(
                        {
                            "install": item["install"],
                            "core": item["core"],
                            "alt": entry.get("alt"),
                            "resource_type": entry.get("resource_type"),
                            "source": source,
                            **({"name": self._label(record)} if record else {}),
                        }
                    )
        return {
            "enabled": profiles_mod.enabled(config),
            "toggled": (config or {}).get("toggled"),
            "unfinished": counts,
            "held": held,
        }
