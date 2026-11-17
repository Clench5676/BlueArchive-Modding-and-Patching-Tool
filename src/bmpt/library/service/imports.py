"""Identifying, inspecting, storing and applying imports, and recovering unfinished ones."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Collection, Hashable, Sequence

from bmpt.spine import atlas_remap, graft as grafting
from bmpt.unity import mod_origin
from bmpt.core import devmode
from bmpt.engine import (
    LogFunc,
    ProgressCallback,
    no_log,
    parse_filename,
)
from ...core.errors import ServiceError
from ...core.filelock import file_lock
from ...gameassets.cabs import CabIndex
from ...core.logging_utils import log_failure
from .. import fingerprint, integrity
from ..install import Action, PlannedFile, PlannedGroup
from .. import targets as resolver
from ..identity import identify
from ..judge import (
    core_of,
    kind_of_file,
    mod_versions,
    other_target,
)
from ..migrations import migrate_journal_names
from ..models import (
    AddResult,
    ApplyResult,
    Kind,
    Pin,
)
from ..storage import (
    atomic_copy,
    atomic_json,
    sync_directory,
    verify_file,
)
from .base import _held_id, _mod_writer, _serialized

if TYPE_CHECKING:
    from ...host.config import Settings


logger = logging.getLogger(__package__)


class ImportsMixin:
    """Identifying, inspecting, storing and applying imports, and recovering unfinished ones."""

    def _graftable(self, selected: integrity.Contents, official: integrity.Contents, skeleton: str) -> bool:
        """Tell whether the mod carries what a graft of the official ``skeleton`` needs.

        That is a binary 4.2 skeleton for it and the atlas of its stem with the pages of that atlas.
        """
        own = atlas_remap.pair_atlases(selected.skeletons, official.skeletons).get(skeleton)
        atlas = next(
            (
                name
                for name, other in atlas_remap.pair_atlases(official.skeletons, official.atlases).items()
                if other == skeleton
            ),
            None,
        )
        source = atlas_remap.pair_atlases(selected.atlases, official.atlases).get(atlas) if atlas else None
        if own is None or source is None or not self._binary_42(selected.skeletons[own]):
            return False
        try:
            pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(selected.atlases[source]).pages}
        except (ValueError, UnicodeError):
            return False
        return bool(pages) and pages <= set(selected.textures)

    @staticmethod
    def _graft_plan(
        selected: integrity.Contents, official: integrity.Contents, skeleton: str
    ) -> tuple[list[str], dict[str, list[str]]]:
        """Plan a graft of the official ``skeleton``'s new animations into the mod's own (``graft.SkeletonGraft``).

        Returns:
            The tuple (carried, refused): the names it carries and, per animation it cannot carry, the reasons
            (``kind:name``). Nothing carried when a skeleton cannot be read.
        """
        own = atlas_remap.pair_atlases(selected.skeletons, official.skeletons).get(skeleton)
        try:
            plan = grafting.SkeletonGraft(selected.skeletons[own], official.skeletons[skeleton])
        except (ValueError, KeyError, IndexError, struct.error):
            return [], {}
        return [an["name"] for an in plan.animations], dict(plan.refused)

    @_serialized
    def compatibility_stamp(self, mod_id: str, pin: Pin | None = None) -> str:
        """Hash the mod and candidate official-source revisions for compatibility-cache invalidation.

        Args:
            mod_id: Stored mod ID whose files, ownership, and repair links enter the stamp.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        record = self._record(mod_id)
        pin = pin or self.current_pin()
        files = {path.name: path for path in self._paths(record)}
        core = core_of(next(iter(files)))
        kind = kind_of_file(next(iter(files)))
        targets = (self._other_targets(files, pin) if kind == "other" else self._spine_parts(core, pin)) if pin else []
        if pin and not targets and kind != "other":
            target_core = self._core_map(pin).core(self._target_of(record), kind) or core
            targets = self._spine_parts(target_core, pin)
        paths = set(files.values()) | set(targets)
        for target in targets:
            paths.update(
                [self._original_path(target), target.with_name(target.name + ".backup"), *self._elsewhere(target)]
            )
        stamps = []
        for path in sorted(paths):
            try:
                stat = path.stat()
                stamp = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
            except FileNotFoundError:
                stamp = None
            stamps.append([str(path.resolve()), stamp])
        identity = [
            pin.key if pin else None,
            self._target_of(record),
            record.get("repaired_from"),
            self._repaired_id(record) or None,
            stamps,
        ]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @_serialized
    def compatibility(
        self, mod_id: str, pin: Pin | None = None, progress: Callable[[dict], None] | None = None
    ) -> dict:
        """Read fresh official and effective Spine data without building or rewriting a mod.

        Args:
            mod_id: Stored mod ID whose format, dependencies, and repair options are checked.
            pin: Game install and server the operation is fixed to; None uses the current install.
            progress: Receives the initial check-step dictionary; None disables reporting.

        Returns:
            Compatibility data, official/mod Spine inputs, and available remap, old-rig, and graft options.
        """
        record = self._record(mod_id)
        if progress:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
        report = self._check_group(
            {path.name: path for path in self._paths(record)},
            pin or self.current_pin(),
            variant_key=self._target_of(record),
            built=self._read_built_against(record),
        )
        report["repaired_from"], report["repaired_id"] = record.get("repaired_from", ""), self._repaired_id(record)
        report["remappable"] = report["remappable"] and not (report["repaired_from"] or report["repaired_id"])
        old_rig = report.setdefault("oldRig", {"atlases": [], "animations": {}, "available": False})
        old_rig["available"] = old_rig["available"] and report["remappable"]
        graft = report.setdefault("graft", {"kind": None, "animations": {}, "refused": {}, "available": False})
        graft["available"] = graft["available"] and not (report["repaired_from"] or report["repaired_id"])
        return report

    @_serialized
    def identify_import(self, path: Path, name: str, pin: Pin | None = None) -> tuple[str, str, str]:
        """Resolve upload identity, trusting verified current slots despite renamed assets.

        Args:
            path: Uploaded bundle inspected when a verified filename cannot establish identity.
            name: Canonical bundle filename, without an adjacent-backup suffix.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Stored filename, ownership target, and library kind.
        """
        return self._identify_import(path, name, pin)

    def _identify_import(self, path: Path, name: str, pin: Pin | None = None) -> tuple[str, str, str]:
        if kind_of_file(name) == "other":
            return identify(path, name, self.catalog, filename_only=True)
        pin = pin or self.current_pin()
        targets = self._spine_parts(core_of(name), pin) if pin else []
        named = any(integrity.replacement_key(target.name) == integrity.replacement_key(name) for target in targets)
        if not named:
            named = any(
                integrity.replacement_key(kept) == integrity.replacement_key(name)
                and self._is_official_as(self.root / "originals" / kept, kept)
                for kept in self._originals()
            )
        return identify(path, name, self.catalog, filename_only=named)

    def import_core(self, core: str, kind: str, pin: Pin | None = None) -> str:
        """Choose an import grouping key, mapping model dependency cores to their prefab slot.

        Args:
            core: Physical bundle core, normalized to lower case.
            kind: Library kind; only model joins several physical cores into one slot.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
        """
        core = core.lower()
        if kind != "model":
            return core
        pin = pin or self.current_pin()
        return self._core_map(pin).slot(core) if pin else core

    def import_versions(self, entries: Sequence[tuple[Hashable, str]], pin: Pin | None = None) -> list[str]:
        """Apply the shared Add/batch version rule, respecting dates that coexist in the install.

        Args:
            entries: Grouping-key/filename pairs in import order; model keys must use import_core
                so prefab and dependency cores belong to one set.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            One date label per entry, empty for sets that form a single mod version.
        """
        pin = pin or self.current_pin()
        installed = (path.name for paths in self._index(pin).values() for path in paths) if pin else ()
        return mod_versions(entries, installed)

    def _cab_index(self, pin: Pin) -> CabIndex:
        key = (pin.key, tuple(pin.dirs))
        if key not in self._cab_indexes:
            self._cab_indexes[key] = CabIndex(pin.dirs)
        return self._cab_indexes[key]

    def _readers(
        self,
        targets: Sequence[Path],
        pin: Pin | None,
        inspection: integrity.Inspection | None = None,
        *,
        keys: bool = True,
        release: bool = True,
    ) -> resolver.Readers:
        """Read what the resolver needs of a file: its CABs, asset paths and (with ``keys``) asset names and types.

        A file of ``targets`` is read from its official copy where there is one, else as it is. Asset evidence is on top
        of the name rules: a file the readers cannot read gives none. With ``release``, a file naming a part without its
        CRC or by its CABs is that part's current release only with the part's structure (``Inspection.structure``).
        """
        inspection = inspection or integrity.Inspection()
        members, baselines = set(targets), {}

        def baseline(path: Path) -> Path:
            if pin is None or path not in members:
                return path
            if path not in baselines:
                try:
                    baselines[path] = self._official_or_current(path, pin)
                except ServiceError:
                    baselines[path] = path
            return baselines[path]

        def cabs(path: Path):
            return inspection.cabs(self._official_or_current(path, pin) if pin and path in members else path)

        def paths(path: Path) -> set[str]:
            try:
                return set(inspection.inspect(baseline(path), allow_unknown=True).paths)
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No asset paths read from %s", path.name, exc_info=True)
                return set()

        def asset_keys(path: Path) -> set:
            if not keys:
                return set()
            try:
                return set(inspection.keys(baseline(path)))
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No asset keys read from %s", path.name, exc_info=True)
                return set()

        def same_release(source: Path, target: Path) -> bool:
            # A part rebuilt by a game update keeps its name (less the CRC) and CABs. Only a file of the part's
            # structure as it is now replaces it whole; unread files give no evidence and keep the name rule.
            try:
                return inspection.structure(source) == inspection.structure(baseline(target))
            except Exception:  # noqa: BLE001 - any reader failure means no evidence
                logger.debug("No structure read for %s", source.name, exc_info=True)
                return True

        return resolver.Readers(cabs, paths, asset_keys, same_release if release else resolver.NAMES_ONLY.release)

    def _resolve(
        self,
        files: dict[str, Path],
        targets: Sequence[Path],
        pin: Pin | None,
        inspection: integrity.Inspection | None = None,
        *,
        whole: bool = True,
        keys: bool = True,
        release: bool = True,
        older: Collection[Path] = (),
    ) -> resolver.Resolution:
        """The one rule for the official parts these files replace or update (``targets.resolve``)."""
        return resolver.resolve(
            files,
            targets,
            self._readers(targets, pin, inspection, keys=keys, release=release),
            whole=whole,
            older=older,
        )

    def _replacements(
        self,
        files: dict[str, Path],
        targets: list[Path],
        pin: Pin | None,
        inspection: integrity.Inspection | None = None,
        *,
        release: bool = True,
    ) -> dict[Path, Path]:
        """Find the files that are a part's current release, as {target: source}.

        ``release=False`` takes the names and CABs alone, for a caller that judges the release itself.
        """
        return self._resolve(files, targets, pin, inspection, release=release).current

    def _update_groups(
        self, older: dict[str, Path], targets: Sequence[Path], pin: Pin, inspection: integrity.Inspection | None = None
    ) -> list[PlannedGroup]:
        """Install groups for files of older releases or other builds (every file treated as one)."""
        return self._groups_of(self._resolve(older, targets, pin, inspection, whole=False))

    @staticmethod
    def _groups_of(resolution: resolver.Resolution) -> list[PlannedGroup]:
        """Plan install groups for the files the resolution does not replace whole.

        They are per name prefix and core, the parts they update (groups reaching a common part update it together);
        files that found no part are NOT_FOUND.
        """
        planned: list[PlannedGroup] = []
        for group in resolution.groups:
            parsed = parse_filename(group.names[0])
            if not group.targets:
                planned.append(
                    PlannedGroup(
                        parsed.prefix,
                        parsed.category,
                        parsed.core,
                        [PlannedFile(path, parse_filename(path.name), Action.NOT_FOUND) for path in group.sources],
                    )
                )
                continue
            merged = PlannedGroup(
                parsed.prefix,
                parsed.category,
                parsed.core,
                [PlannedFile(path, parse_filename(path.name), Action.UPDATE) for path in group.sources],
                target_paths=sorted(group.targets),
            )
            for other in [other for other in planned if set(other.target_paths) & set(merged.target_paths)]:
                planned.remove(other)
                merged.files[:0] = other.files
                merged.target_paths = sorted(set(other.target_paths) | set(merged.target_paths))
            planned.append(merged)
        return planned

    def _replaced_parts(self, files: dict[str, Path], targets: list[Path], pin: Pin | None) -> set[Path]:
        """The parts a preview drops for these files (``Resolution.replaced``)."""
        return self._resolve(files, targets, pin).replaced

    def direct_import(
        self, files: dict[str, Path], pin: Pin | None = None, *, inspection: integrity.Inspection | None = None
    ) -> dict | None:
        """Validate current parts and the resulting set before admitting a whole-file copy.

        Args:
            files: Uploaded whole-copy candidates indexed by their proposed current-part filenames.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
            inspection: Shared analysis snapshot; None creates one for this validation.

        Returns:
            Whole-file replacements, baselines, source evidence, and compatibility data;
            None without an install or when not every source names a current part.

        Raises:
            ServiceError: Formats, release structure, source constraints, dependencies, or pointers are incompatible.
        """
        pin = pin or self.current_pin()
        if not pin:
            return None
        name = next(iter(files))
        targets = (
            self._other_targets(files, pin) if kind_of_file(name) == "other" else self._spine_parts(core_of(name), pin)
        )
        inspection = inspection or integrity.Inspection()
        # Named for current parts: this check judges their release itself and refuses another one by name.
        replacements = self._replacements(files, targets, pin, inspection, release=False)
        if len(replacements) != len(files):
            return None
        selected, official, originals = integrity.Contents(), integrity.Contents(), []
        identities = []
        for target, source in replacements.items():
            try:
                original = self._official_for(target, pin, game_first=True)
            except ServiceError as error:
                if error.code != "original_unavailable":
                    raise
                # Current slots can accept whole files without an official baseline. The live file is
                # format evidence only; it must never enter originals or a conversion's baselines.
                inspection.check(source)
                selected.merge(inspection.inspect(source))
                integrity.check_format(inspection.metadata(source), source, inspection.metadata(target))
                continue
            incoming, baseline = inspection.metadata(source), inspection.metadata(original)
            integrity.check_format(incoming, source, baseline)
            identities.append((source, incoming, baseline))
            selected.merge(inspection.inspect(source))
            official.merge(inspection.inspect(original))
            originals.append(original)
        target_origin = mod_origin.target_of(self.settings, pin, official)
        evidence, error = mod_origin.constraints(set(files), selected, official, target_origin)
        if error or (evidence.get("server") and evidence["server"] != target_origin["server"]):
            raise ServiceError(error or "source_conflict")
        # Retained official prefabs must still resolve into the replacement CABs.
        compatibility = self._check_group(files, pin, conversion=True, inspection=inspection)
        self._check_group(files, pin, compatibility=False, inspection=inspection)
        for source, incoming, baseline in identities:
            if not incoming.files:
                raise ServiceError("mod_release_mismatch", file=source.name)
            if kind_of_file(name) == "model":
                self._check_model_references(source, incoming, baseline, replacements, targets, pin, inspection)
            elif incoming.files != baseline.files or incoming.layout != baseline.layout:
                raise ServiceError("mod_release_mismatch", file=source.name)
        # A whole file must also point at objects the game still holds where it points into other bundles.
        self._check_references(replacements, pin)
        inspection.validate()
        source_origin, error = mod_origin.source_of(set(files), selected, official, target_origin)
        if error == "source_unknown" and compatibility["unavailable"]:
            # The current slot already identifies a direct replacement; absent official CAB evidence
            # must not turn the missing baseline into an import refusal.
            evidence, error = mod_origin.constraints(set(files), selected, official, target_origin)
            source_origin = {
                **target_origin,
                **{key: value for key, value in evidence.items() if value},
                "detected": False,
            }
        if error:
            raise ServiceError(error)
        return {
            "direct": replacements,
            "targets": list(replacements),
            "originals": originals,
            "sourceContents": selected,
            "officialContents": official,
            "source": source_origin,
            "compatibility": compatibility,
        }

    def _check_model_references(
        self,
        source: Path,
        incoming: integrity.Contents,
        baseline: integrity.Contents,
        replacements: dict[Path, Path],
        targets: list[Path],
        pin: Pin,
        inspection: integrity.Inspection,
    ) -> None:
        """Only changes to objects consumed outside a replaced part can break its public interface."""
        changed = {}
        for cab, layout in baseline.layout.items():
            replacement = dict(incoming.layout.get(cab, ()))
            ids = {path_id for path_id, kind in layout if replacement.get(path_id) != kind}
            if ids:
                changed[cab] = ids
        removed_cabs = baseline.files - incoming.files
        if not changed and not removed_cabs:
            return  # Preserving every original object is safe without scanning other bundles.
        consumers = {path: path for paths in self._index(pin).values() for path in paths}
        for target in targets:
            consumers[target] = (
                replacements[target] if target in replacements else self._official_or_current(target, pin)
            )
        for path in consumers.values():
            if path != source and integrity.references_changed_objects(path, changed, removed_cabs, inspection):
                raise ServiceError("mod_release_mismatch", file=source.name)

    @_serialized
    def inspect_import(
        self,
        files: dict[str, Path],
        pin: Pin | None = None,
        *,
        conversion: bool = False,
        inspection: integrity.Inspection | None = None,
    ) -> dict:
        """Preflight canonical upload names without storing or adopting anything.

        Use the same identities, dependency checks and installed-content judgement as add().
        Original recovery is deliberately separate: an already installed upload can be recognized
        even when an outside installer left no official copy behind.

        Args:
            files: Canonical bundle filenames mapped to uploaded source paths.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
            conversion: Whether to include source-format evidence and evaluate whole-copy or rebuild routes.
            inspection: Shared analysis snapshot guarding uploaded file revisions; None uses fresh readers.

        Returns:
            Import identity, recognized library/install matches, target parts, and any compatibility
            and conversion evidence needed before storage.
        """
        for path in files.values():
            devmode.check_read(path)
            if inspection:
                inspection.check(path)
        identity = self._identity(files)
        if inspection:
            inspection.validate()
        duplicate = self._known().get(identity)
        core_name = core_of(next(iter(files)))
        pin = pin or self.current_pin()
        generic = kind_of_file(next(iter(files))) == "other"
        targets = []
        if pin:
            try:
                targets = self._other_targets(files, pin) if generic else self._spine_parts(core_name, pin)
            except ServiceError:
                if not duplicate:
                    raise
        installed = False
        if pin:
            view = self._view([pin], "full")
            entry = view.entry(pin, core_name)
            if generic:
                entry.parts = [view.part(path, pin, "other") for path in targets]
            installed = self._matches_installed(identity, entry)
        if (duplicate or installed) and not conversion:
            return {"identity": identity, "libraryId": duplicate, "installed": installed, "targets": targets}
        rebuild = False
        try:
            direct = self.direct_import(files, pin, inspection=inspection) if conversion else None
        except ServiceError as error:
            if (duplicate or installed) and error.code in ("original_unavailable", "original_invalid"):
                return {
                    "identity": identity,
                    "libraryId": duplicate,
                    "installed": installed,
                    "targets": targets,
                    "recognized": True,
                }
            if kind_of_file(next(iter(files))) not in ("spr", "lobby") or error.code not in (
                "mod_platform_mismatch",
                "mod_platform_unknown",
                "mod_unity_version_mismatch",
                "mod_release_mismatch",
                "source_conflict",
            ):
                raise
            # Current Spine filenames identify slots, not a compatible whole-file format.
            # Preserve this verdict even on the same platform: extraction and target
            # rebuilding must succeed before add() validates and stores the output.
            direct, rebuild = None, True
        if direct:
            return {"identity": identity, "libraryId": duplicate, "installed": installed, **direct}
        report = self._check_group(files, pin, dependencies=False, conversion=conversion, inspection=inspection)
        # Old CAB references are replaced by the rebuild. Its output goes through add()'s full preflight.
        if not report["outdated"] and not conversion:
            self._check_group(files, pin, compatibility=False, inspection=inspection)
        originals = (
            report.pop("originalPaths", [])
            if conversion
            else ([self._official_for(path, pin) for path in targets] if generic else [])
        )
        return {
            "identity": identity,
            "libraryId": duplicate,
            "installed": installed,
            "compatibility": report,
            "targets": targets,
            "originals": originals if generic else [],
            "rebuild": rebuild,
        }

    @_serialized
    def apply_import(
        self,
        variant_key: str,
        source: str,
        *,
        kind: Kind,
        pin: Pin,
        settings: Settings,
        backup: bool,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Apply with one import's save settings, retaining the normal switch and transaction checks.

        The settings adapter is local to this serialized operation; neither config.toml nor
        the shared Settings object changes. Deferred configurations use the usual switch flow.

        Args:
            variant_key: Variant key or generic target selecting the destination slot.
            source: official, mod:<id>, or alt:<key> source selected by the import.
            kind: Library kind restricting which slot receives the source.
            pin: Game install and server the operation is fixed to.
            settings: Import-specific adapter supplying save and conversion settings during this call.
            backup: Whether this import's game writes also preserve adjacent .backup files.
            log: Receives user-facing operation messages.
            progress: Receives apply progress; None disables reporting.
        """
        previous = self.settings
        self.settings = settings
        try:
            return self.apply(variant_key, source, kind=kind, pin=pin, log=log, progress=progress, create_backup=backup)
        finally:
            self.settings = previous

    def add_and_apply(
        self,
        paths: list[Path],
        progress: ProgressCallback | None = None,
        pin: Pin | None = None,
        log: LogFunc = no_log,
        *,
        kind: Kind | None = None,
    ) -> AddResult:
        """Store an upload, then select each newly added mod through the normal switch flow.

        Args:
            paths: Uploaded bundle files grouped and validated by add.
            progress: Receives import and apply progress; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
            log: Receives user-facing operation messages.
            kind: Required library kind, or None to infer it from each group.

        Returns:
            Import results with applied, deferred, incomplete, and failed additions recorded separately.
        """
        pin = pin or self.current_pin()
        added = self.add(paths, progress, pin=pin, kind=kind)
        for entry in added:
            try:
                result = self.apply(
                    entry.target, f"mod:{entry.id}", kind=entry.kind, pin=pin, log=log, progress=progress
                )
            except ServiceError as error:
                added.failed.append({"id": entry.id, **error.to_json()})
                if (
                    pin is not None
                    and entry.kind != "other"
                    and self.configured(entry.target, pin).get(entry.kind) == f"mod:{entry.id}"
                ):
                    added.configured.append(entry)
            else:
                (added.configured if result.deferred else added.applied).append(entry)
                added.kept_current |= result.kept_current
                if result.incomplete:
                    added.incomplete.append(entry.id)
        return added

    @_serialized
    @_mod_writer
    def add_assigned_files(self, files: dict[str, Path], variant: str, kind: str, pin: Pin | None = None) -> AddResult:
        """Store an explicitly attributed, self-contained mod without claiming an install target.

        Args:
            files: Safe bundle basenames mapped to source paths forming one complete mod.
            variant: Assignment-capable variant key explicitly chosen to own the mod.
            kind: Required inferred library kind for the self-contained upload.
            pin: Reserved install context; this storage-only operation does not inspect it.

        Raises:
            ServiceError: The assignment or upload is invalid, or identical stored bytes have another owner or kind.
        """
        from ..manual_identity import validate

        self.catalog.assignment_variant(variant)
        validate(files, kind)
        signature = self._identity(files)
        known = self._known().get(signature)
        result = AddResult()
        if known:
            record = self._record(known)
            if self._target_of(record) != variant or record["kind"] != kind:
                raise ServiceError("mod_target_mismatch", id=known, alt=variant)
            result.duplicates.append(self._entry(record))
            return result
        record = self._store(files, variant, variant, kind, user_added=True, assignment=variant)
        result.append(self._entry(record))
        return result

    def _store(
        self,
        files: dict[str, Path],
        label: str,
        target: str,
        kind: str,
        *,
        repaired_from: str = "",
        user_added: bool = False,
        assignment: str = "",
        metadata: dict[str, Any] | None = None,
        built_against: dict | None = None,
    ) -> dict[str, Any]:
        """Publish a complete directory before its metadata, with durable recovery of either outcome.

        ``built_against`` (``_built_against``) is stored with the files, as part of the same directory.
        """
        names = sorted(files)
        if not names:
            raise ServiceError("source_unsupported")  # no file to name
        refused = next((name for name in names if Path(name).name != name or not name.endswith(".bundle")), None)
        if refused is not None:
            raise ServiceError("mod_unsupported", file=refused)
        mod_id = uuid.uuid4().hex
        directory = self.root / "mods" / mod_id
        transaction = self.root / "import-transactions" / mod_id
        journal = transaction / "journal.json"
        staged = transaction / "files"
        if (
            directory.parent.is_symlink()
            or transaction.parent.is_symlink()
            or directory.exists()
            or transaction.exists()
        ):
            raise ServiceError("library_io_error", file=transaction)
        record = {
            "id": mod_id,
            "files": names,
            "label": label,
            "target": target,
            "kind": kind,
            "added": datetime.now(timezone.utc).isoformat(),
            "seen": False,
            "user_added": user_added,
        }
        if metadata:
            if set(metadata) & set(record):
                raise ValueError("Generated Mod metadata overrides a reserved field")
            record.update(copy.deepcopy(metadata))
        if repaired_from:
            record["repaired_from"] = repaired_from
        if assignment:
            record["assignment"] = assignment
        hashes = {name: self._hashes.get(files[name]).sha1 for name in names}
        data = {
            "version": 1,
            "id": mod_id,
            "phase": "preparing",
            "record": record,
            "hashes": hashes,
            "repaired_from": repaired_from,
            "old_repaired_id": self._record(repaired_from).get("repaired_id") if repaired_from else None,
        }
        atomic_json(journal, data)
        try:
            for name in names:
                atomic_copy(files[name], staged / name)
                verify_file(staged / name, hashes[name])
                if self._hashes.get(files[name]).sha1 != hashes[name]:
                    raise ServiceError("entry_changed", core=core_of(name))
            if built_against is not None:
                try:
                    fingerprint.write(staged, built_against, hashes)
                except OSError as error:  # the mod is whole without it
                    log_failure(logger, logging.WARNING, "Could not store the official parts' fingerprint", error)
            data["phase"] = "publishing"
            atomic_json(journal, data)
            directory.parent.mkdir(parents=True, exist_ok=True)
            sync_directory(self.root)
            os.replace(staged, directory)
            sync_directory(directory.parent)
            sync_directory(transaction)
            self._records[mod_id] = record
            if repaired_from:
                self._record(repaired_from)["repaired_id"] = mod_id
            self._save()
        except Exception as error:
            try:
                data["phase"] = "rollback"
                atomic_json(journal, data)
                self._rollback_import(journal, data)
            except Exception as recovery_error:
                log_failure(logger, logging.ERROR, "Mod import rollback pending", recovery_error)
                raise ServiceError("library_recovery_pending", file=journal) from error
            raise
        # Metadata publication is the commit point. A leftover journal is safe:
        # recovery verifies the complete committed group before removing it.
        try:
            shutil.rmtree(transaction)
            sync_directory(transaction.parent)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Committed import journal retained", error)
        return record

    def _rollback_import(self, journal: Path, data: dict) -> None:
        mod_id = data["id"]
        directory = self.root / "mods" / mod_id
        if (
            re.fullmatch(r"[0-9a-f]{32}", mod_id) is None
            or directory.parent.is_symlink()
            or directory.is_symlink()
            or not directory.resolve().is_relative_to(self.root)
            or journal.is_symlink()
            or journal.parent.is_symlink()
            or not journal.resolve().is_relative_to(self.root / "import-transactions")
        ):
            raise ServiceError("library_io_error", file=journal)
        self._records.pop(mod_id, None)
        repaired = self._records.get(data["repaired_from"])
        if repaired and repaired.get("repaired_id") == mod_id:
            if data["old_repaired_id"] is None:
                repaired.pop("repaired_id", None)
            else:
                repaired["repaired_id"] = data["old_repaired_id"]
        path = self._state_path
        with file_lock(self.root / "state.json.lock"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = None
            if state is not None:
                if (
                    isinstance(state, dict)
                    and isinstance(state.get("version"), (int, float))
                    and not isinstance(state["version"], bool)
                    and state["version"] > 1
                ):
                    raise ServiceError("library_state_unreadable", file=path)
                if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("mods"), list):
                    raise ServiceError("library_io_error", file=path)
                before = copy.deepcopy(state)
                # held records are raw JSON, whatever their shape: only an object with this id goes
                state["mods"] = [
                    record for record in state["mods"] if not (isinstance(record, dict) and record.get("id") == mod_id)
                ]
                for record in state["mods"]:
                    if (
                        isinstance(record, dict)
                        and record.get("id") == data["repaired_from"]
                        and record.get("repaired_id") == mod_id
                    ):
                        if data["old_repaired_id"] is None:
                            record.pop("repaired_id", None)
                        else:
                            record["repaired_id"] = data["old_repaired_id"]
                if state != before:
                    # the file just read is the last good one; a rollback does not wait for its copy
                    try:
                        atomic_copy(path, self._prev_path)
                    except OSError as error:
                        log_failure(logger, logging.WARNING, "Could not keep the previous library state", error)
                    atomic_json(path, state)
                    self._held = [raw for raw in self._held if _held_id(raw) != mod_id]
            # A prior replace may have succeeded before its directory flush failed.
            # Prove the metadata removal durable even when this retry changes no JSON.
            sync_directory(self.root)
        self._seen = None
        self._refresh_state()
        if directory.exists():
            shutil.rmtree(directory)
            sync_directory(directory.parent)
        shutil.rmtree(journal.parent)
        sync_directory(journal.parent.parent)

    def _recover_imports(self) -> None:
        root = self.root / "import-transactions"
        if root.is_symlink():
            raise ServiceError("library_io_error", file=root)
        journals = sorted(root.glob("*/journal.json"))
        if not journals:
            return
        with file_lock(self.root / "mods.lock"):
            for journal in journals:
                try:
                    if (
                        journal.is_symlink()
                        or journal.parent.is_symlink()
                        or not journal.resolve().is_relative_to(root.resolve())
                    ):
                        raise ValueError("Invalid import journal path")
                    data = json.loads(journal.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("operation") == "update":
                        self._recover_update(journal, data)
                        continue
                    mod_id, record, hashes = data["id"], data["record"], data["hashes"]
                    if (
                        not isinstance(mod_id, str)
                        or re.fullmatch(r"[0-9a-f]{32}", mod_id) is None
                        or journal != root / mod_id / "journal.json"
                        or journal.is_symlink()
                        or journal.parent.is_symlink()
                        or not isinstance(record, dict)
                        or record.get("id") != mod_id
                        or not isinstance(hashes, dict)
                        or data["version"] != 1
                        or data["phase"] not in ("preparing", "publishing", "rollback")
                        or not record.get("files")
                        or set(record["files"]) != set(hashes)
                        or any(
                            not isinstance(name, str)
                            or Path(name).name != name
                            or not name.endswith(".bundle")
                            or not isinstance(value, str)
                            or re.fullmatch(r"[0-9a-f]{40}", value) is None
                            for name, value in hashes.items()
                        )
                    ):
                        raise ValueError("Invalid import journal")
                    migrate_journal_names(data, journal)
                    directory = self.root / "mods" / mod_id
                    if (
                        directory.parent.is_symlink()
                        or directory.is_symlink()
                        or not directory.resolve().is_relative_to(self.root)
                    ):
                        raise ValueError("Invalid import directory")
                    committed = self._records.get(mod_id)
                    if data["phase"] == "publishing" and committed and committed.get("files") == record["files"]:
                        for name, digest in hashes.items():
                            if (directory / name).is_symlink():
                                raise ValueError("Invalid imported file")
                            verify_file(directory / name, digest)
                        sync_directory(directory)
                        sync_directory(directory.parent)
                        sync_directory(self.root)
                        shutil.rmtree(journal.parent)
                        sync_directory(root)
                    else:
                        self._rollback_import(journal, data)
                except Exception as error:
                    raise ServiceError("library_recovery_pending", file=journal) from error

    @_mod_writer
    def _add_group(self, paths: list[Path], label: str, pin: Pin) -> tuple[str, bool]:
        """Store the parts of one core as one mod, whatever their dates.

        The mod is keyed by the variant key the install's own core map gives the core (a core the current catalog does
        not map is stored too). A part set already in the library is not stored twice.

        Returns:
            The id of the mod and whether it was created.
        """
        files = {path.name: path for path in paths}
        signature_ = self._identity(files)
        existing = self._known().get(signature_)
        if existing is not None:
            return existing, False
        core_name = core_of(paths[0].name)
        kind = kind_of_file(paths[0].name)
        if kind is None:
            raise ServiceError("mod_unsupported", file=paths[0])
        key = other_target(files) if kind == "other" else self._core_map(pin).key(core_name) or core_name.lower()
        return self._store(
            files,
            label if kind == "other" else core_name,
            key,
            kind,
            built_against=self._built_against(files, pin, "import", painted=True),
        )["id"], True
