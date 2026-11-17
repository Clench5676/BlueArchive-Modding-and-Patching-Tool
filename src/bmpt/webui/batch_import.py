"""Batch import preflight and execution, built on the library and classic updater."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from bmpt.unity import mod_origin
from bmpt.core import devmode
from bmpt.engine import (
    CRCUtils,
    parse_filename,
    t,
)
from ..core.errors import ServiceError
from ..gameassets.spine import PREVIEW_TYPES, Composition, spine_revision
from ..library import integrity, staging
from ..library.rebuild import rebuild
from ..library.judge import core_of
from ..library.storage import Fingerprints, atomic_clone, verify_file
from .batch_candidates import (
    IMPORTABLE,
    PREF,
    Candidate,
    ImportResult,
    ImportSettings,
    blocked_error,
    failure_detail,
    options_of,
    source_file,
    staging_name,
)
from .batch_judge import JudgeMixin


logger = logging.getLogger(__name__)


class BatchImport(JudgeMixin):
    """The batch import of mods: analyze what the user dropped into rows, name a row's source, then import.

    The rows of the latest analysis live in memory under a revision; every analysis and source change bumps it,
    and an import of rows from an older revision is refused (``entry_changed``).
    """

    def __init__(self, services: Any):
        """Create the import, with no rows.

        Args:
            services: The Services: settings, UI state, catalog, library and job submission.
        """
        self.services = services
        self._lock = threading.RLock()
        self._revision = 0
        self._latest: dict[str, Candidate] = {}
        self._previews: dict[str, tuple[Candidate, str]] = {}
        self._library: Any = None
        self._pin: Any = None
        self._hashes = Fingerprints()
        self._sources: dict[str, tuple[frozenset, dict]] = {}
        self._assignments: dict[str, tuple[frozenset, str]] = {}
        self._completed: dict[tuple, str] = {}
        self._legacy_checks: dict[str, str | None] = {}
        self._session_folders: set[Path] = set()

    def defaults(self) -> dict:
        """Return the import options the settings give.

        The keys are ``strategy``, ``types``, ``skipUnchanged``, ``crc``, ``compression``, ``extraBytes``,
        ``backup``, ``workers``, ``output``, ``enableAfterImport`` and ``previewAfterImport``.
        """
        settings = self.services.settings
        return {
            "strategy": "cont_name_type",
            "types": sorted(settings.asset_types()) or ["ALL"],
            "skipUnchanged": settings.get("skip_unchanged"),
            "crc": {"true": "on", "false": "off"}.get(settings.get("enable_crc_correction"), "auto"),
            "compression": settings.get("compression_method"),
            "extraBytes": settings.get("extra_bytes"),
            "backup": settings.get("create_backup"),
            "workers": settings.get("max_workers"),
            "output": settings.get("output_dir") or None,
            "enableAfterImport": True,
            "previewAfterImport": False,
        }

    def options(self) -> dict:
        """Return the saved import options over the defaults; the defaults alone when the saved ones are invalid."""
        defaults = self.defaults()
        try:
            return options_of(self.services.state.get(PREF, {}), defaults)
        except ServiceError:
            return options_of({}, defaults)

    def set_options(self, value: dict) -> dict:
        """Save the import options.

        Args:
            value: The options, as ``options_of`` validates them.

        Returns:
            The options as saved.

        Raises:
            ServiceError: ``bad_params`` naming the invalid option.
        """
        options = options_of(value, self.defaults())
        self.services.state.set(PREF, options)
        return options

    def pick(self, folder: bool = False, recursive: bool = False, directory: str | None = None) -> dict:
        """Show the native dialog that chooses what to import.

        Args:
            folder: Choose folders instead of mod files and archives.
            recursive: Only checked to be a boolean; the dialog does not depend on it.
            directory: The folder the file dialog opens in; None leaves it to the system.

        Returns:
            ``paths``, the chosen paths; empty when the dialog was cancelled.

        Raises:
            ServiceError: ``bad_params`` for a wrong type; ``native_unavailable`` without a window.
        """
        if not isinstance(folder, bool) or not isinstance(recursive, bool):
            raise ServiceError("bad_params", key="folder")
        if self.services.window is None:
            raise ServiceError("native_unavailable")
        if directory is not None:
            if not isinstance(directory, str) or not directory:
                raise ServiceError("bad_params", key="directory")
            devmode.check_read(Path(directory))
        from .native import pick_files, pick_folder

        paths = (
            pick_folder(self.services.window)
            if folder
            else pick_files(
                self.services.window,
                ("Mod (*.bundle;*.bundle.backup;*.zip)",),
                **({"directory": directory} if directory else {}),
            )
        )
        return {"paths": [str(path) for path in paths]}

    def analyze(self, paths: list[str], recursive: bool = False) -> str:
        """Start the analysis of dropped files, which replaces the rows of the last one.

        Rows of the previous analysis whose files are all part of this one keep their grouping, provided the library
        and the install are the same.

        Args:
            paths: Mod bundles, archives and folders.
            recursive: Also read the folders' subfolders.

        Returns:
            The id of the library writer job. Its result holds the ``entries`` (the rows), the number of files
            ``skipped`` and the ``revision`` the rows belong to.

        Raises:
            ServiceError: ``bad_params`` for a wrong type.
        """
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ServiceError("bad_params", key="paths")
        if not isinstance(recursive, bool):
            raise ServiceError("bad_params", key="recursive")
        for path in paths:
            devmode.check_read(Path(path))
        library = self.services.library
        pin = library.current_pin()
        with self._lock:
            previous = list(self._latest.values()) if self._library is library and self._pin == pin else []
            self._revision += 1
            revision = self._revision
            self._latest = {}
            self._previews = {}
            self._library, self._pin = library, pin

        def analyze(log: Any, progress: Any) -> dict:
            progress(0, 1, "")
            candidates, skipped = self._collect(paths, recursive, library, previous=previous)
            folders = {Path(path).expanduser().resolve() for path in paths if Path(path).is_dir()}
            folders.update(path.parent for candidate in candidates for path in candidate.files.values())
            self._session_folders.update(folders)
            headers = self._folder_headers(self._session_folders, progress)
            entries, previews = [], {}
            for i, candidate in enumerate(candidates):
                with self._lock:
                    override = self._sources.get(candidate.entry["id"])
                if override and override[0] == candidate.identity:
                    candidate.source_override = dict(override[1])
                assignment = self._assignments.get(candidate.entry["id"])
                if assignment and assignment[0] == candidate.identity:
                    candidate.entry["assignment"] = assignment[1]
                self._judge(candidate, library, pin, log)
                self._recognize_legacy(candidate, library)
                self._restore_completed(candidate, library, pin)
                entries.append(candidate)
                progress(i + 1, len(candidates), candidate.entry["core"] or "")
            entries = self._join_referenced(entries, headers, library, pin, log)
            selected_paths = {path for candidate in entries for path in candidate.files.values()}
            unique, known = [], set()
            for candidate in entries:
                self._missing(candidate, library, headers, selected_paths)
                # Identical selected subsets can still belong to different incomplete Mods in two folders.
                if candidate.entry["state"] != "incomplete" and candidate.identity:
                    if candidate.identity in known:
                        skipped += len(candidate.entry["paths"])
                        continue
                    known.add(candidate.identity)
                unique.append(candidate)
                self._log_analysis(candidate, "analyze")
                self._register_preview(candidate, revision, previews)
            entries = unique
            with self._lock:
                if revision == self._revision:
                    self._latest = {c.entry["id"]: c for c in entries}
                    self._previews = previews
            return {"entries": [c.entry for c in entries], "skipped": skipped, "revision": revision}

        return self.services.submit("import.analyze", analyze, writes="library", library=library)

    def _register_preview(self, candidate: Candidate, revision: int, previews: dict) -> None:
        if candidate.entry["core"] and candidate.entry["kind"] in ("spr", "lobby", "model"):
            try:
                stamp = spine_revision(list(candidate.files.values()))
                key = hashlib.sha256(f"{revision}:{candidate.entry['id']}:{stamp}".encode()).hexdigest()
                previews[key] = (candidate, stamp)
                candidate.entry["preview"] = {"kind": "import", "key": key}
            except (OSError, KeyError):
                pass  # An unavailable source keeps its static catalog artwork.

    def diagnostic_snapshot(self) -> dict:
        """Return the revision and the current rows, each with whether the import preview composed it, for reports."""
        with self._lock:
            return {
                "revision": self._revision,
                "rows": [{**deepcopy(c.entry), "previewed": c.previewed} for c in self._latest.values()],
            }

    def _log_analysis(self, candidate: Candidate, phase: str) -> None:
        from .diagnostics import import_row

        detail = import_row(candidate.entry, getattr(self.services, "log_secrets", set()))
        logger.info("Batch import analysis (%s): %s", phase, json.dumps(detail, ensure_ascii=False, sort_keys=True))

    def assignment_catalog(self) -> dict:
        """List the students of every server for assigning a mod, as the catalog's cross-server metadata has them.

        Assets the install lacks stay absent in the picker.

        Returns:
            ``families``, each with its ``key``, ``base`` names, ``aliases``, ``school`` and ``variants``.
        """
        families = []
        for family in self.services.catalog.assignment_families():
            variants = []
            for variant in family.variants:
                value = variant.to_json()
                value["fileId"] = value.pop("file_id")
                variants.append(value)
            families.append(
                {
                    "key": family.key,
                    "base": family.names,
                    "aliases": family.aliases,
                    "school": family.school,
                    "variants": variants,
                }
            )
        return {"families": families}

    def assign_character(self, id: str, variant: str) -> str:
        """Assign a row to a student of the user's choice, for a mod whose target the files do not give.

        Args:
            id: The row's id.
            variant: The student's variant key, which may be one the install lacks.

        Returns:
            The id of the job that analyzes the row again, as ``set_source`` returns it.

        Raises:
            ServiceError: ``bad_params`` for an unknown row or one that is done; the row's own error when it is
                blocked for another reason than its target.
        """
        if not isinstance(id, str) or not isinstance(variant, str):
            raise ServiceError("bad_params", key="variant")
        selected = self.services.catalog.assignment_variant(variant)
        with self._lock:
            candidate = self._latest.get(id)
            if candidate is None or candidate.entry["state"] in ("done", "library", "installed"):
                raise ServiceError("bad_params", key="id")
            if candidate.error not in (None, "mod_target_unknown", "mod_target_ambiguous", "unsupported"):
                raise blocked_error(candidate)
            candidate.entry["assignment"] = selected.key
            self._assignments[id] = (candidate.identity, selected.key)
        target = candidate.entry.get("source") or candidate.entry["target"]
        return self.set_source(
            id, target.get("server") or "global", target.get("platform") or "windows", assignment_only=True
        )

    def set_source(self, id: str, server: str, platform: str, *, assignment_only: bool = False) -> str:
        """Name the server and platform a row's files come from, and analyze the row again.

        Args:
            id: The row's id.
            server: The game server, one of ``mod_origin.SERVERS``.
            platform: The platform, one of ``mod_origin.PLATFORMS``.
            assignment_only: Keep the row's own source and only analyze it again, after an assignment.

        Returns:
            The id of the library writer job. Its result is the new row, with the ``revision``; the job refuses
            with ``entry_changed`` when the rows or their files changed meanwhile.

        Raises:
            ServiceError: ``bad_params`` for an unknown row, server or platform.
        """
        if not isinstance(id, str) or server not in mod_origin.SERVERS or platform not in mod_origin.PLATFORMS:
            raise ServiceError("bad_params", key="source")
        with self._lock:
            if id not in self._latest:
                raise ServiceError("bad_params", key="id")
            candidate = self._latest[id]
            self._revision += 1
            fresh = Candidate(
                dict(candidate.files),
                deepcopy(candidate.entry),
                candidate.error,
                identity=candidate.identity,
                failure=candidate.failure,
                source_override=candidate.source_override
                if assignment_only
                else {"server": server, "platform": platform, "detected": False},
            )
            library, pin, revision = self._library, self._pin, self._revision

        def analyze(log: Any, progress: Any) -> dict:
            if self.services.library is not library:
                raise ServiceError("folder_changed")
            if pin:
                self.services._check_pin(library, pin)
            identity = self._identity_of(fresh.files)
            if identity != fresh.identity:
                raise ServiceError("entry_changed")
            self._judge(fresh, library, pin, log)
            self._recognize_legacy(fresh, library)
            self._restore_completed(fresh, library, pin)
            selected_paths = {path for c in self._latest.values() for path in c.files.values()}
            self._missing(fresh, library, self._folder_headers(self._session_folders, progress), selected_paths)
            self._log_analysis(fresh, "source")
            previews = {}
            self._register_preview(fresh, revision, previews)
            with self._lock:
                if revision != self._revision or self._latest.get(id) is not candidate:
                    raise ServiceError("entry_changed")
                self._latest[id] = fresh
                if fresh.source_override:
                    self._sources[id] = (identity, dict(fresh.source_override))
                self._previews.update(previews)
            progress(1, 1, fresh.entry["core"] or "")
            return {**fresh.entry, "revision": revision}

        return self.services.submit("import.setSource", analyze, writes="library", library=library)

    def _completion_key(self, candidate: Candidate, library: Any, pin: Any) -> tuple:
        return (
            library,
            pin.key if pin else None,
            tuple(pin.dirs) if pin else (),
            candidate.entry["id"],
            candidate.identity,
        )

    def _recognize_legacy(self, candidate: Candidate, library: Any) -> None:
        """Verify old conversions in temporary copies without importing or applying them."""
        if candidate.entry["state"] != "convert" or not candidate.provenance:
            return
        try:
            mod_id = library.find_legacy_import_source(candidate.provenance, candidate.entry["kind"])
            if not mod_id:
                snapshot = library.legacy_import_snapshot(candidate.entry["kind"], candidate.entry["variant"])
                if not snapshot:
                    return
                options = {**self.options(), "output": None, "compression": "none", "crc": "off", "extraBytes": ""}
                key = json.dumps(
                    [
                        candidate.provenance,
                        snapshot,
                        options,
                        candidate.spine_version,
                        str(self.services.settings.spine_options()),
                    ],
                    sort_keys=True,
                )
                if key not in self._legacy_checks:
                    settings = ImportSettings(self.services.settings, options)
                    with staging.import_directory(library.root) as temporary:
                        checking = replace(candidate, entry=deepcopy(candidate.entry), not_transferred=[])
                        paths, _, _ = self._prepare(
                            checking, settings, Path(temporary), lambda *args: None, lambda *args: None
                        )
                        mod_id = library.find_import_output(paths, {item[0] for item in snapshot}) if paths else None
                    originals = (
                        candidate.originals
                        if candidate.entry["kind"] == "other"
                        else library.official_bundles(candidate.entry["core"], pin=self._pin)
                    )
                    if (
                        library.import_source_identity(candidate.files, candidate.entry["target"], originals)
                        != candidate.provenance
                    ):
                        return
                    if library.legacy_import_snapshot(candidate.entry["kind"], candidate.entry["variant"]) != snapshot:
                        return
                    if len(self._legacy_checks) >= 128:
                        self._legacy_checks.clear()
                    self._legacy_checks[key] = mod_id
                mod_id = self._legacy_checks[key]
            if mod_id:
                library.remember_import_source(mod_id, candidate.provenance)
                candidate.entry.update(state="library", reason=None, libraryId=mod_id)
        except (OSError, ServiceError, ValueError):
            logger.info("Batch import: optional source recognition unavailable")

    def _restore_completed(self, candidate: Candidate, library: Any, pin: Any) -> None:
        if candidate.entry["state"] in ("blocked", "incomplete") or candidate.provenance:
            return
        with self._lock:
            mod_id = self._completed.get(self._completion_key(candidate, library, pin))
        if mod_id and any(mod.id == mod_id for mod in library.mods()):
            candidate.entry.update(state="done", reason=None, libraryId=mod_id)

    def _stored(self, candidate: Candidate, mod_id: str) -> None:
        """A row whose mod was stored although a later step (its install) failed: in the library, not done."""
        with self._lock:
            candidate.entry.update(state="library", reason=None, libraryId=mod_id)

    def _complete(self, candidate: Candidate, library: Any, pin: Any, mod_id: str) -> None:
        with self._lock:
            self._completed[self._completion_key(candidate, library, pin)] = mod_id
            candidate.entry.update(state="done", reason=None, libraryId=mod_id)

    def preview(self, key: str) -> tuple[str, list[Path]]:
        """Compose a row's source with the official parts it omits, or the current parts when no official copy remains.

        Args:
            key: The key of the row's ``preview``.

        Returns:
            The row's core name and the composed bundles; the row is marked as previewed.

        Raises:
            KeyError: The key is unknown, the analysis was replaced or the files changed.
        """
        with self._lock:
            candidate, stamp = self._previews[key]
            library, pin = self._library, self._pin
        if self.services.library is not library:
            raise KeyError(key)
        if pin:
            self.services._check_pin(library, pin)
        paths = list(candidate.files.values())
        if spine_revision(paths) != stamp:
            raise KeyError(key)
        core_name = candidate.entry["core"]
        # A Spine import that transfers assets (update, convert) writes only the asset types its options name: the
        # preview keeps every official part and leaves the other types of the source files out, so it shows what the
        # game will. Whole-file imports (ready) and 3D previews compose the source files whole.
        types = set(self.options()["types"])
        transfers = candidate.entry["state"] in ("update", "convert") and candidate.entry["kind"] in ("spr", "lobby")
        left = set() if "ALL" in types or not transfers else set(PREVIEW_TYPES) - types
        try:
            originals = (
                library.preview_complements(
                    core_name, list(candidate.files), pin=pin, files={} if left else candidate.files
                )
                if pin
                else []
            )
        except ServiceError as error:
            if error.code not in ("game_file_not_found", "original_unavailable", "original_invalid"):
                raise
            originals = []  # A self-contained upload can preview without recoverable official parts.
        bundles = Composition(originals + paths, {path: left for path in paths})
        with self._lock:
            if key not in self._previews:
                raise KeyError(key)
            candidate.previewed = True
        return core_name, bundles

    def run(self, ids: list[str], options: dict, revision: int) -> str:
        """Start the import of analyzed rows.

        Each row is checked again against the files and the official data before its bundles are built, on parallel
        workers, and added to the library; the row's result says whether it was imported, updated, unchanged, failed
        or skipped.

        Args:
            ids: The ids of the rows to import.
            options: The import options, as ``options_of`` validates them.
            revision: The revision of the analysis the ids come from.

        Returns:
            The id of the library writer job. Its result is an ``ImportResult``.

        Raises:
            ServiceError: ``bad_params`` for ids that are no rows; ``entry_changed`` for an older revision;
                ``folder_changed`` when the library changed.
        """
        options = options_of(options, self.defaults())
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ServiceError("bad_params", key="ids")
        with self._lock:
            if type(revision) is not int or revision != self._revision:
                raise ServiceError("entry_changed")
            if any(i not in self._latest for i in ids):
                raise ServiceError("bad_params", key="ids")
            chosen = [self._latest[i] for i in dict.fromkeys(ids)]
            revision = self._revision
            library, pin = self._library, self._pin
        if library is None:
            raise ServiceError("bad_params", key="ids")
        if self.services.library is not library:
            raise ServiceError("folder_changed")
        settings = ImportSettings(self.services.settings, options)

        def check_current() -> None:
            if self.services.library is not library:
                raise ServiceError("folder_changed")
            if revision != self._revision or any(self._latest.get(c.entry["id"]) is not c for c in chosen):
                raise ServiceError("entry_changed")

        def run(log: Any, progress: Any) -> ImportResult:
            with self._lock:
                check_current()
            if pin:
                self.services._check_pin(library, pin)
            result = ImportResult([])
            total, completed = len(chosen), [0]
            progress_lock = threading.Lock()

            positions = {candidate.entry["id"]: index for index, candidate in enumerate(chosen, 1)}

            def report(candidate: Candidate, phase: str, step: str | None = None, *, finished: bool = False) -> None:
                with progress_lock:
                    if finished:
                        completed[0] += 1
                    progress(
                        {
                            "done": completed[0],
                            "completed": completed[0],
                            "remaining": total - completed[0],
                            "total": total,
                            "index": positions[candidate.entry["id"]],
                            "current": candidate.entry["id"],
                            "phase": phase,
                            "step": "import.step." + (step or phase),
                        }
                    )

            # Converted bundles are read back by add_files. Keep both target
            # copies and output in this library's allowed data, not system temp.
            with library.import_batch(), staging.import_directory(library.root) as temporary:
                with ThreadPoolExecutor(max_workers=options["workers"]) as workers:
                    futures, analyzed, originals = {}, {}, {}
                    for candidate in chosen:
                        if candidate.entry["state"] not in IMPORTABLE:
                            continue
                        # Recheck the actual inputs and current official files before launching parallel builds.
                        try:
                            with self._lock:
                                check_current()
                                self._require_importable(candidate)
                            current = self._identity_of(candidate.files)
                            if current != candidate.identity:
                                raise ServiceError("entry_changed")
                            fresh = Candidate(
                                dict(candidate.files),
                                deepcopy(candidate.entry),
                                candidate.error,
                                failure=deepcopy(candidate.failure),
                                source_override=deepcopy(candidate.source_override),
                            )
                            fresh.entry["files"] = [dict(f) for f in candidate.entry["files"]]
                            report(
                                candidate,
                                "convert"
                                if candidate.conversion
                                else "update"
                                if candidate.entry["state"] == "update"
                                else "add",
                                "check",
                            )
                            self._judge(
                                fresh,
                                library,
                                pin,
                                log,
                                asset_types=settings.asset_types(),
                                strategy=settings.options["strategy"],
                            )
                            analyzed[candidate.entry["id"]] = fresh
                            self._log_analysis(fresh, "execute")
                            if fresh.entry["state"] in ("library", "installed", "done"):
                                futures[candidate.entry["id"]] = fresh
                                continue
                            self._require_importable(fresh)
                            self._snapshot_originals(fresh, Path(temporary), originals)
                            futures[candidate.entry["id"]] = workers.submit(
                                self._prepare, fresh, settings, Path(temporary), log, report
                            )
                        except (OSError, ServiceError) as error:
                            futures[candidate.entry["id"]] = error
                    for candidate in chosen:
                        item = {
                            "id": candidate.entry["id"],
                            "outcome": "skipped",
                            "reason": None,
                            "libraryId": candidate.entry["libraryId"],
                            "configured": False,
                            "applied": False,
                        }
                        phase = "add"
                        try:
                            state = candidate.entry["state"]
                            if state in ("done", "library", "installed"):
                                item["outcome"] = "unchanged"
                            elif state not in IMPORTABLE:
                                item["reason"] = candidate.entry.get("reason")
                                if candidate.entry.get("error"):
                                    item["error"] = candidate.entry["error"]
                                if candidate.entry.get("params"):
                                    item["params"] = candidate.entry["params"]
                            else:
                                future = futures[candidate.entry["id"]]
                                if isinstance(future, Exception):
                                    raise future
                                if isinstance(future, Candidate):
                                    item.update(outcome="unchanged", libraryId=future.entry["libraryId"])
                                    continue
                                paths, updated, fresh = future.result()
                                with self._lock:
                                    check_current()
                                    self._require_importable(candidate)
                                    self._require_importable(fresh)
                                    current = self._identity_of(fresh.files)
                                    if current != fresh.identity:
                                        raise ServiceError("entry_changed")
                                if fresh.not_transferred:
                                    item["notTransferred"] = fresh.not_transferred
                                    logger.warning(
                                        "%s: %s",
                                        ", ".join(sorted(fresh.files)),
                                        t("webui.import.notTransferred", names=", ".join(fresh.not_transferred)),
                                    )
                                if paths is None:
                                    item.update(outcome="unchanged", libraryId=fresh.entry["libraryId"])
                                    mod = None
                                else:
                                    report(candidate, "add")
                                    with self._lock:
                                        check_current()
                                        added = (
                                            library.add_assigned_files(
                                                fresh.files, fresh.entry["assignment"], fresh.entry["kind"], pin=pin
                                            )
                                            if fresh.entry.get("assignment")
                                            else library.add_files(
                                                fresh.entry["kind"], paths, pin=pin, direct=bool(fresh.direct)
                                            )
                                        )
                                    entries = [*added, *added.duplicates]
                                    if len(entries) != 1:
                                        raise ServiceError("mod_unsupported", file=source_file(candidate))
                                    mod = entries[0]
                                    item.update(
                                        libraryId=mod.id,
                                        outcome=("updated" if updated else "imported") if added else "unchanged",
                                    )
                                    result.added.extend(added)
                                    if fresh.provenance:
                                        library.remember_import_source(mod.id, fresh.provenance)
                                if mod is not None:
                                    result.cores.update(core_of(name) for name in fresh.files)
                                    result.kinds.add(mod.kind)
                                    if options["previewAfterImport"] and pin and mod.kind in ("spr", "lobby", "model"):
                                        self.services.state.set_preview_source(
                                            pin.key, mod.target, mod.kind, f"mod:{mod.id}"
                                        )
                                        item["previewSelected"] = True
                                    if options["enableAfterImport"] and fresh.entry.get("libraryOnly"):
                                        item["applyUnavailable"] = "no_target"
                                    if (
                                        options["enableAfterImport"]
                                        and not fresh.entry.get("libraryOnly")
                                        and item["outcome"] != "unchanged"
                                    ):
                                        phase = "apply"
                                        report(candidate, phase)
                                        with self._lock:
                                            check_current()
                                            self._require_importable(candidate)
                                            self._require_importable(fresh)
                                            applied = library.apply_import(
                                                mod.target,
                                                f"mod:{mod.id}",
                                                kind=mod.kind,
                                                pin=pin,
                                                settings=settings,
                                                backup=options["backup"],
                                                log=log,
                                            )
                                        # A successful apply configures the source, including deferred applies.
                                        item["configured"] = True
                                        item["applied"] = not applied.deferred
                                        if applied.kept_current:
                                            item["kept_current"] = True
                                        if applied.incomplete:
                                            item["incomplete"] = True
                                    self._complete(candidate, library, pin, mod.id)
                        except ServiceError as error:
                            item.update(
                                outcome="failed",
                                reason=error.code,
                                error=failure_detail(
                                    error,
                                    candidate.files.values(),
                                    log,
                                    secrets=getattr(self.services, "log_secrets", set()),
                                ),
                            )
                            if error.params:
                                item["params"] = error.to_json()["params"]
                        except OSError as error:
                            detail = ServiceError(
                                "library_io_error", file=error.filename or source_file(candidate), error=str(error)
                            )
                            item.update(
                                outcome="failed",
                                reason=detail.code,
                                params=detail.params,
                                error=failure_detail(
                                    detail,
                                    candidate.files.values(),
                                    log,
                                    secrets=getattr(self.services, "log_secrets", set()),
                                ),
                            )
                        except Exception as error:
                            detail = ServiceError("internal", error=str(error))
                            item.update(
                                outcome="failed",
                                reason=detail.code,
                                params=detail.params,
                                error=failure_detail(
                                    detail,
                                    candidate.files.values(),
                                    log,
                                    secrets=getattr(self.services, "log_secrets", set()),
                                ),
                            )
                        finally:
                            if phase == "apply" and item.get("libraryId") and item.get("reason"):
                                # The mod reached the library before its install failed: the row says so.
                                self._stored(candidate, item["libraryId"])
                                # A failed install can still have saved the choice. Report both facts.
                                try:
                                    if mod.kind == "other":
                                        item["configured"] = any(
                                            row["id"] == mod.id and row["selected"] for row in library.other_mods(pin)
                                        )
                                    else:
                                        item["configured"] = (
                                            library.configured(mod.target, pin).get(mod.kind) == f"mod:{mod.id}"
                                        )
                                except (ServiceError, OSError):
                                    item["configured"] = False
                            verdict = analyzed.get(candidate.entry["id"])
                            if verdict and verdict.entry["state"] == "blocked":
                                with self._lock:
                                    candidate.entry = deepcopy(verdict.entry)
                                item["state"] = "blocked"
                            result.results.append(item)
                            report(candidate, phase, finished=True)
            return result

        writes = (
            ("game" if options["enableAfterImport"] else "library")
            if any(candidate.entry["state"] in IMPORTABLE for candidate in chosen)
            else None
        )
        return self.services.submit("import.run", run, writes=writes, library=library)

    def _snapshot_originals(self, candidate: Candidate, temporary: Path, snapshots: dict) -> None:
        """Freeze verified baselines before any earlier row can replace their live paths."""
        originals = []
        for original in candidate.originals:
            revision = spine_revision([original])
            key = (original.resolve(), revision)
            if key not in snapshots:
                fingerprint = self._hashes.get(original)
                copied = temporary / "originals" / staging_name(fingerprint.sha1) / original.name
                copied.parent.mkdir(parents=True, exist_ok=True)
                if not atomic_clone(original, copied):
                    shutil.copy2(original, copied)
                verify_file(copied, fingerprint.sha1)
                if spine_revision([original]) != revision:
                    raise ServiceError("entry_changed")
                snapshots[key] = copied
            originals.append(snapshots[key])
        candidate.originals = originals

    def _prepare(
        self, candidate: Candidate, settings: ImportSettings, temporary: Path, log: Any, progress: Any
    ) -> tuple[list[Path] | None, bool, Candidate]:
        entry = candidate.entry
        self._require_importable(candidate)
        if entry["state"] == "ready":
            if candidate.direct:
                output = temporary / staging_name(entry["id"]) / "direct"
                output.mkdir(parents=True)
                paths, extra = [], settings.save_options(False).extra_bytes or b""
                baselines = {path.name.removesuffix(".backup"): path for path in candidate.originals}
                for target, source in candidate.direct.items():
                    path = output / target.name
                    data = source.read_bytes()
                    expected = int(parse_filename(target.name).crc)
                    fix = (
                        settings.crc_needed(baselines.get(target.name, target))
                        and CRCUtils.compute_crc32(data) != expected
                    )
                    if not fix and source.name.removesuffix(".backup") == target.name:
                        paths.append(source)
                        continue
                    if fix:
                        data = CRCUtils.apply_crc_fix(data + extra, expected)
                        if data is None:
                            raise ServiceError("crc_fix_failed", file=source.name)
                    path.write_bytes(data)
                    paths.append(path)
                return paths, False, candidate
            return list(candidate.files.values()), False, candidate
        phase = "convert" if candidate.conversion else "update"
        progress(candidate, phase, "check")
        work = temporary / staging_name(entry["id"])
        output, targets_dir = work / "output", work / "targets"
        output.mkdir(parents=True)
        targets_dir.mkdir()
        targets = []
        for live, original in zip(candidate.targets, candidate.originals):
            target = targets_dir / live.name
            if not atomic_clone(original, target):
                shutil.copy2(original, target)
            targets.append(target)
        spine = settings.spine_options()
        if candidate.spine_version:
            spine = replace(spine, target_version=candidate.spine_version)
        groups = {}
        for target in targets:
            prefix = parse_filename(target.name).prefix if entry["kind"] == "other" else ""
            groups.setdefault(prefix, []).append(target)
        generated, unchanged = {}, True
        for prefix, group in groups.items():
            sources = [
                path for name, path in candidate.files.items() if not prefix or parse_filename(name).prefix == prefix
            ]
            updated = rebuild(
                source_paths=sources,
                target_paths=group,
                output_dir=output,
                asset_types_to_replace=settings.asset_types(),
                save_options=settings.save_options(settings.crc_needed(group[0])),
                spine_options=spine,
                skip_unchanged=settings.options["skipUnchanged"],
                match_strategy="name_type"
                if candidate.conversion or candidate.rebuild
                else settings.options["strategy"],
                anim_check=settings.anim_check_options(),
                log=log,
                on_step=lambda step: progress(candidate, phase, step),
            )
            if not updated.success:
                if updated.error:
                    raise updated.error
                raise ServiceError("conversion_failed" if candidate.conversion else "mod_update_failed")
            candidate.not_transferred = sorted(set(candidate.not_transferred) | set(updated.not_transferred))
            if not updated.all_unchanged:
                if not updated.file_pairs:
                    raise ServiceError("mod_update_failed")
                unchanged = False
            generated.update((pair.output.name, pair.output) for pair in updated.file_pairs)
        if unchanged:
            return None, False, candidate
        # Retain unchanged official parts when the current game splits one old mod into several bundles.
        for target in targets:
            if target.name not in generated:
                shutil.copy2(target, output / target.name)
                generated[target.name] = output / target.name
        if candidate.conversion or candidate.rebuild:
            # Reject a failed conversion before publishing exports or library records.
            validation = integrity.Inspection()
            baselines = {live.name: original for live, original in zip(candidate.targets, candidate.originals)}
            for name, path in generated.items():
                if name not in baselines:
                    raise ServiceError("mod_release_mismatch", file=path.name)
                integrity.check_format(validation.metadata(path), path, validation.metadata(baselines[name]))
            validation.validate()
        if settings.options["output"]:
            progress(candidate, phase, "export")
            destination = Path(settings.options["output"]) / "bundles" / entry["id"]
            for path in generated.values():
                exported = destination / path.name
                if exported.resolve() in candidate.files.values():
                    raise ServiceError("bad_params", key="output")
            destination.mkdir(parents=True, exist_ok=True)
            for path in generated.values():
                shutil.copy2(path, destination / path.name)
        return sorted(generated.values()), True, candidate
