"""Collecting and judging a batch import's candidates, a mixin of ``BatchImport``."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from bmpt.unity import mod_origin
from bmpt.core import devmode
from bmpt.engine import (
    no_log,
    parse_filename,
)
from ..core.errors import ServiceError
from ..gameassets.discovery import cab_names
from ..library import ba_mod, import_archive, integrity, manual_identity
from ..library.judge import core_of, resource_type_of, part_of
from .batch_candidates import (
    IMPORTABLE,
    Candidate,
    blocked_error,
    blocked_reason,
    bundle_evidence,
    claimed_first,
    copy_compatibility,
    engine_match_strategy,
    failure_detail,
    missing_detail,
    source_file,
    target_evidence,
    transfer_compatibility,
)


logger = logging.getLogger("bmpt.webui.batch_import")  # batch import logs under one name


class JudgeMixin:
    """Collecting the dropped files into candidates and judging them, for ``BatchImport``."""

    def _collect(
        self, paths: list[str], recursive: bool, library: Any, *, previous: list[Candidate] = ()
    ) -> tuple[list[Candidate], int]:
        selected, seen, skipped = [], set(), 0

        def take(path: Path) -> None:
            nonlocal skipped
            path = path.expanduser().resolve()
            devmode.check_read(path)
            if path.name == ba_mod.FILE_NAME:
                return  # a package manifest is read with the bundles beside it, never a file left out
            if path in seen or not path.name.endswith((".bundle", ".bundle.backup")):
                skipped += 1
            else:
                seen.add(path)
                selected.append(path)

        for value in paths:
            path = Path(value).expanduser().resolve()
            if path.is_dir():
                if recursive:

                    def failed(error: OSError) -> None:
                        raise ServiceError("library_io_error", file=error.filename)

                    for folder, directories, files in os.walk(path, followlinks=False, onerror=failed):
                        devmode.check_read(Path(folder))
                        directories.sort()
                        for name in sorted(files):
                            take(Path(folder) / name)
                else:
                    for child in sorted(path.iterdir()):
                        if not child.is_dir():
                            take(child)
            elif import_archive.is_archive(path):
                # A chosen archive opens like a folder, one level only: archives inside it stay packed.
                devmode.check_read(path)
                unpacked, left = import_archive.unpack(path, library.root)
                skipped += left
                for member in unpacked:
                    take(member)
            else:
                take(path)
        # a bundle of a group packed one asset per bundle gets its own slot (its CAB) before anything is identified
        note = getattr(library, "note_packed", None)
        if note is not None:
            note(selected, self._pin)
        groups: dict[tuple, list[tuple]] = {}
        inspected = []
        for path in selected:
            name = path.name.removesuffix(".backup")
            parsed = parse_filename(name)
            resource_type = resource_type_of(parsed.core, parsed.category) or "other"
            alt, error, failure = None, None, None
            try:
                if self._hashes.get(path).trailing is None:
                    raise ServiceError("bundle_invalid", file=path)
                if not library.supports_resource_type(resource_type):
                    error = "unsupported"
                    failure = ServiceError("mod_unsupported", file=path, resource_type=resource_type).to_json()
                    resolve = getattr(library.catalog, "alt_for_asset_core", library.catalog.alt_for_core)
                    found = resolve(parsed.core.lower())
                    alt = found.key if found else None
                else:
                    name, alt, resource_type = library.identify_import(path, name)
            except OSError as caught:
                error = "library_io_error"
                failure = ServiceError("library_io_error", file=path, error=str(caught)).to_json()
            except ServiceError as caught:
                error = blocked_reason(caught)
                failure = caught.to_json()
            except Exception as caught:
                logger.debug("Cannot inspect uploaded bundle")
                error = "unreadable"
                failure = ServiceError("bundle_invalid", file=path, error=str(caught)).to_json()
            core = library.import_core(parse_filename(name).core, resource_type, self._pin)
            inspected.append((path, name, resource_type, alt, core, error, failure))
        # Chibi sets share the prefab slot key, including version splits across their physical cores.
        # Folders still separate alternative mods of the same release.
        versioned = [item for item in inspected if item[2] != "other"]
        versions = library.import_versions(
            [
                ((path.parent, core, resource_type, alt), name)
                for path, name, resource_type, alt, core, _, _ in versioned
            ],
            self._pin,
        )
        version_by_path = {item[0]: version for item, version in zip(versioned, versions)}
        self._join_passengers(versioned, version_by_path, library)
        active = [c for c in previous if set(c.files.values()) <= seen]
        owners = {path: c for c in active for path in c.files.values()}
        for path, name, resource_type, alt, core, error, failure in inspected:
            version = version_by_path.get(path, "")
            owner = owners.get(path)
            if owner is None and not error:
                try:
                    held = set(cab_names(path))
                except (OSError, ValueError):
                    held = set()
                matches = [
                    c
                    for c in active
                    if held & c.missing_cabs
                    or (
                        (core, resource_type, alt) == (c.entry["core"], c.entry["resource_type"], c.entry["alt"])
                        and (
                            all(version_by_path.get(p, "") == version for p in c.files.values())
                            if path.parent in {p.parent for p in c.files.values()}
                            else c.entry["state"] == "incomplete"
                        )
                    )
                ]
                local = [c for c in matches if path.parent in {p.parent for p in c.files.values()}]
                if local:
                    matches = local
                # A repeated part or several possible owners is an alternative Mod, never a guessed merge.
                collisions = [
                    c
                    for c in matches
                    if path.parent not in {p.parent for p in c.files.values()}
                    and any(integrity.replacement_key(name) == integrity.replacement_key(n) for n in c.files)
                ]
                if len(matches) > 1 or collisions:
                    error = "mod_target_ambiguous"
                    failure = ServiceError(
                        error, file=path, candidates=sorted(str(p) for c in matches for p in c.files.values())
                    ).to_json()
                elif matches:
                    owner = matches[0]
            if owner is not None:
                core, resource_type, alt = owner.entry["core"], owner.entry["resource_type"], owner.entry["alt"]
                key = (owner.entry["id"], core, "", resource_type, alt)
            else:
                key = (
                    (path.parent, "", "", resource_type, None)
                    if resource_type == "other"
                    else (path.parent, core, version, resource_type, alt)
                )
            groups.setdefault(key, []).append((path, name, error, failure))
        candidates = []
        packages: dict[Path, dict | None] = {}
        for (_, core_name, _, resource_type, alt), items in groups.items():
            files, parts, sources, error, failure = {}, {}, [], None, None
            for path, name, problem, detail in items:
                error = error or problem
                failure = failure or detail
                try:
                    if name in files:
                        if self._hashes.get(files[name]).sha1 == self._hashes.get(path).sha1:
                            skipped += 1
                            continue
                        error = "conflict"
                    part = integrity.replacement_key(name)
                    if part in parts and self._hashes.get(parts[part]).sha1 != self._hashes.get(path).sha1:
                        error = "conflict"
                    parts[part] = path
                except OSError:
                    error = "unreadable"
                files[name] = path
                sources.append(path)
            sources.sort()
            file_list = []
            for path in sources:
                try:
                    size = path.stat().st_size
                except OSError:
                    size, error = 0, "unreadable"
                file_list.append({"path": str(path), "size": size, "target": None})
            entry = {
                "id": hashlib.sha256("\0".join(map(str, sources)).encode()).hexdigest(),
                "paths": list(map(str, sources)),
                "alt": alt,
                "core": core_name or None,
                "resource_type": resource_type,
                "files": file_list,
                "size": sum(f["size"] for f in file_list),
                "state": "blocked",
                "reason": error,
                "libraryId": None,
                "preview": None,
                "source": None,
                "target": mod_origin.target_of(self.services.settings, library.current_pin()),
            }
            candidate = Candidate(files, entry, error, failure=failure)
            folders = {path.parent for path in sources}
            if len(folders) == 1:
                folder = folders.pop()
                if folder not in packages:
                    packages[folder] = ba_mod.read(folder)
                if ba_mod.covers(packages[folder], folder, sources):
                    candidate.package = packages[folder]
            try:
                candidate.identity = self._identity_of(files)
            except OSError:
                pass  # The unreadable row above still reports all of its source paths.
            candidates.append(candidate)
        return candidates, skipped

    def _join_passengers(self, versioned: list[tuple], version_by_path: dict[Path, str], library: Any) -> None:
        """Keep a part that holds no asset of the import's types with the one mod of its set.

        Such a part (a split bundle of animation data only) is no version of its own, and the import leaves its
        target official. Only sets the date rule split are read; a set with several versions besides, or with
        nothing else, keeps the date rule.
        """
        sets: dict[tuple, list[tuple]] = {}
        for path, name, resource_type, alt, core, error, _ in versioned:
            sets.setdefault((path.parent, core, resource_type, alt), []).append((path, name, error))
        inspection = integrity.Inspection(set(self.options()["assetTypes"]))
        for key, items in sets.items():
            if not any(version_by_path.get(path) for path, _, _ in items):
                continue
            passengers = []
            for path, _, error in items:
                try:
                    if not error and not inspection.keys(path):
                        passengers.append(path)
                except Exception:  # noqa: BLE001 - an unreadable file stays under the date rule
                    logger.debug("Batch import: contents not read for grouping", exc_info=True)
            carriers = [(path, name) for path, name, _ in items if path not in passengers]
            if not passengers or not carriers:
                continue
            versions = library.import_versions([(key, name) for _, name in carriers], self._pin)
            if len(set(versions)) != 1:
                continue
            version_by_path.update({path: version for (path, _), version in zip(carriers, versions)})
            version_by_path.update({path: versions[0] for path in passengers})

    @staticmethod
    def _folder_headers(folders: set[Path], progress: Any = None) -> dict[Path, set[str]]:
        """Discover suppliers from actual directory entries, never from a bundle's embedded names."""
        headers = {}
        for index, folder in enumerate(sorted(folders)):
            if progress:
                progress(index, len(folders), "")
            devmode.check_read(folder)
            try:
                paths = sorted(folder.iterdir())
            except OSError:
                continue
            for path in paths:
                if not path.name.endswith((".bundle", ".bundle.backup")) or not path.is_file():
                    continue
                devmode.check_read(path)
                try:
                    headers[path.resolve()] = set(cab_names(path))
                except (OSError, ValueError):
                    continue
        return headers

    def _join_referenced(
        self, candidates: list[Candidate], headers: dict[Path, set[str]], library: Any, pin: Any, log: Any
    ) -> list[Candidate]:
        """A selected supplier of a new CAB belongs to its dependent upload, regardless of its folder."""
        while True:
            links = {
                id(c): [
                    other
                    for other in candidates
                    if other is not c
                    and any(headers.get(path, set()) & c.missing_cabs for path in other.files.values())
                ]
                for c in candidates
                if c.missing_cabs
            }
            changed = False
            for candidate in candidates:
                providers = links.get(id(candidate), [])
                if not providers:
                    continue
                group = [candidate, *providers]
                parts = [integrity.replacement_key(name) for c in group for name in c.files]
                shared = any(
                    other is not candidate and any(provider in links.get(id(other), []) for provider in providers)
                    for other in candidates
                )
                if shared or len(parts) != len(set(parts)):
                    candidate.entry.update(
                        state="blocked",
                        reason="mod_target_ambiguous",
                        params={"candidates": sorted(str(p) for c in group for p in c.files.values())},
                    )
                    candidate.missing_cabs.clear()
                    continue
                files = {name: path for c in group for name, path in c.files.items()}
                paths = sorted(files.values())
                entry = {
                    **candidate.entry,
                    "id": hashlib.sha256("\0".join(map(str, paths)).encode()).hexdigest(),
                    "paths": list(map(str, paths)),
                    "files": [{"path": str(p), "size": p.stat().st_size, "target": None} for p in paths],
                    "size": sum(p.stat().st_size for p in paths),
                    "preview": None,
                }
                merged = Candidate(files, entry, source_override=candidate.source_override)
                self._judge(merged, library, pin, log)
                self._recognize_legacy(merged, library)
                self._restore_completed(merged, library, pin)
                candidates = [merged if c is candidate else c for c in candidates if c not in providers]
                changed = True
                break
            if not changed:
                return candidates

    def _missing(
        self, candidate: Candidate, library: Any, headers: dict[Path, set[str]], selected_paths: set[Path] | None = None
    ) -> None:
        entry = candidate.entry
        if candidate.error or entry.get("reason") in {"conflict", "mod_target_ambiguous", "unreadable"}:
            return
        selected = set(candidate.files.values())
        folders = {path.parent for path in selected}
        # Another release or a backup of a selected part is an alternative to it, not a missing file.
        parts = {integrity.replacement_key(path.name) for path in selected}
        found = []
        for path, held in sorted(headers.items(), key=lambda item: (item[0].name, str(item[0]))):
            if path in selected:
                continue
            parsed = parse_filename(path.name.removesuffix(".backup"))
            resource_type = resource_type_of(parsed.core, parsed.category) or "other"
            resolve = getattr(library.catalog, "alt_for_asset_core", library.catalog.alt_for_core)
            alt = resolve(parsed.core.lower()) if resource_type in ("portrait", "memorialLobby") else None
            slot_matches = (
                library.import_core(parsed.core, resource_type, self._pin) == entry["core"]
                or alt is not None
                and alt.key == entry["alt"]
            )
            same_slot = (
                path.parent in folders
                and resource_type != "other"
                and resource_type == entry["resource_type"]
                and slot_matches
                and path not in (selected_paths or selected)
                and integrity.replacement_key(path.name) not in parts
            )
            if same_slot or held & candidate.missing_cabs:
                found.append({"path": str(path), "name": path.name, "size": path.stat().st_size})
        if found or candidate.missing_cabs:
            entry.update(state="incomplete", reason=None, missing=missing_detail(candidate.missing_cabs, found))
            entry.pop("error", None)
            entry.pop("params", None)

    def _identity_of(self, files: dict[str, Path]) -> frozenset:
        """What an import row's files are: per file its core, part and content."""
        return frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1) for name, path in files.items())

    def _judge(
        self,
        candidate: Candidate,
        library: Any,
        pin: Any,
        log: Any = no_log,
        *,
        asset_types: set[str] | None = None,
        strategy: str | None = None,
    ) -> None:
        entry = candidate.entry
        entry.pop("params", None)
        entry.pop("error", None)
        entry.pop("missing", None)
        entry.pop("bundles", None)
        candidate.missing_cabs = set()
        entry.update(
            state="blocked",
            reason=candidate.error,
            libraryId=None,
            source=None,
            target=mod_origin.target_of(self.services.settings, pin),
        )
        candidate.targets, candidate.originals, candidate.spine_version, candidate.conversion = [], [], None, False
        candidate.rebuild = False
        candidate.direct = {}
        candidate.provenance = None
        inspection = integrity.Inspection(
            asset_types if asset_types is not None else set(self.options()["assetTypes"]), header_only_missing=True
        )
        entry.pop("resolution", None)
        entry.pop("attempts", None)
        resolution, attempts = None, []
        try:
            if candidate.error in ("mod_target_unknown", "mod_target_ambiguous") or entry.get("assigned_alt"):
                existing = library.find_import_output(list(candidate.files.values()))
                saved = next((mod for mod in library.mods() if mod.id == existing), None) if existing else None
                if saved:
                    if entry.get("assigned_alt") and entry["assigned_alt"] != saved.target:
                        raise ServiceError("mod_target_ambiguous", file=source_file(candidate))
                    entry.update(
                        state="library",
                        reason=None,
                        libraryId=saved.id,
                        alt=saved.target,
                        resource_type=saved.resource_type,
                        core=entry["core"] or saved.core,
                    )
                    return
            if entry.get("assigned_alt"):
                if candidate.error not in (None, "mod_target_unknown", "mod_target_ambiguous", "unsupported"):
                    raise blocked_error(candidate)
                alt = self.services.catalog.assignment_alt(entry["assigned_alt"])
                resource_type = manual_identity.validate(candidate.files)
                candidate.identity = self._identity_of(candidate.files)
                entry.update(
                    alt=alt.key,
                    resource_type=resource_type,
                    state="ready",
                    reason=None,
                    libraryOnly=True,
                    core=entry["core"] or alt.file_id,
                )
                return
            if candidate.failure:
                raise ServiceError(candidate.failure["code"], **candidate.failure["params"])
            if candidate.error:
                raise ServiceError(candidate.error)
            for path in candidate.files.values():
                inspection.check(path)
            report = library.inspect_import(candidate.files, pin=pin, conversion=True, inspection=inspection)
            if report.get("recognized"):
                entry.update(
                    state="library" if report["libraryId"] else "installed", reason=None, libraryId=report["libraryId"]
                )
                return
            if report.get("direct"):
                candidate.identity = report["identity"]
                candidate.direct = report["direct"]
                candidate.targets, candidate.originals = report["targets"], report["officials"]
                entry["target"] = mod_origin.target_of(self.services.settings, pin, report["officialContents"])
                source = report["source"]
                if candidate.source_override and any(
                    candidate.source_override[key] != source[key] for key in ("server", "platform")
                ):
                    raise ServiceError("source_conflict")
                entry["source"] = candidate.source_override or source
                official, incoming, remappable = copy_compatibility(report["compatibility"], report["sourceContents"])
                if report["compatibility"]["unavailable"]:
                    official = {
                        key: values if values != {None} else {incoming[key]} for key, values in official.items()
                    }
                mismatch = self._check_spine(candidate, official, incoming, remappable)
                for file in entry["files"]:
                    file["target"] = next(
                        target.name for target, source in candidate.direct.items() if str(source) == file["path"]
                    )
                if mismatch:
                    if report["compatibility"]["unavailable"]:
                        library.official_bundles(entry["core"], pin=pin)
                    candidate.direct = {}
                entry.update(
                    state="library"
                    if report["libraryId"]
                    else "installed"
                    if report["installed"]
                    else "update"
                    if mismatch
                    else "ready",
                    reason=None,
                    libraryId=report["libraryId"],
                )
                return
            selected = integrity.Contents()
            rebuild = report.get("rebuild", False)
            for path in candidate.files.values():
                selected.merge(inspection.inspect(path, allow_unknown=True))
            names = {path.name.removesuffix(".backup") for path in candidate.files.values()}
            source, source_error = mod_origin.source_of(names, selected, integrity.Contents(), entry["target"])
            entry["source"] = candidate.source_override or source
            inspection.validate()
            candidate.identity = report["identity"]
            live = report["targets"]
            compatibility = report.get("compatibility") or {}
            official_contents = compatibility.get("officialContents", integrity.Contents())
            if not compatibility and pin and live:
                try:
                    for path in library.official_bundles(entry["core"], pin=pin):
                        official_contents.merge(inspection.inspect(path))
                except ServiceError:
                    pass  # Installed uploads can be recognized without a recoverable original.
            entry["target"] = mod_origin.target_of(self.services.settings, pin, official_contents)
            source, source_error = mod_origin.source_of(names, selected, official_contents, entry["target"])
            entry["source"] = candidate.source_override or source
            if source_error in ("source_conflict", "platform_unsupported"):
                raise ServiceError(source_error)
            evidence, _ = mod_origin.constraints(names, selected, official_contents, entry["target"])
            if candidate.source_override and any(
                value and candidate.source_override[key] != value for key, value in evidence.items()
            ):
                raise ServiceError("source_conflict")

            def conversion(source: dict) -> bool:
                return bool(
                    any(source[key] != entry["target"][key] for key in ("server", "platform"))
                    or (official_contents.platforms and selected.platforms != official_contents.platforms)
                    or mod_origin.platform_of(selected) is None
                )

            if len({p.name for p in live}) != len(live):
                raise ServiceError("game_file_conflict", file=source_file(candidate))
            resolution = library._resolve(candidate.files, live, pin, inspection)
            replacements = resolution.current
            for file in entry["files"]:
                matches = [target for target, source in replacements.items() if str(source) == file["path"]]
                file["target"] = matches[0].name if len(matches) == 1 else None
            if entry["source"] is None or not conversion(entry["source"]):
                if report["libraryId"]:
                    entry.update(state="library", reason=None, libraryId=report["libraryId"])
                    return
                if report["installed"]:
                    entry.update(state="installed", reason=None)
                    return
            if not pin or not live:
                raise ServiceError("game_file_not_found")
            originals = (
                report["officials"]
                if entry["resource_type"] == "other"
                else library.official_bundles(entry["core"], pin=pin)
            )
            candidate.provenance = library.import_source_identity(candidate.files, entry["target"], originals)
            try:
                imported = library.find_import_source(candidate.provenance)
            except (OSError, ServiceError, ValueError):
                logger.info("Batch import: optional source recognition unavailable")
                imported = None
            if imported:
                entry.update(state="library", reason=None, libraryId=imported)
                return
            if not entry["target"]["platform"]:
                raise ServiceError("target_unknown")
            if len(live) != len(originals):
                raise ServiceError("game_file_conflict", file=source_file(candidate))
            baseline = dict(zip(live, originals))
            exact = set(candidate.files) <= {p.name for p in live}
            sources = list(candidate.files.values())
            generic = entry["resource_type"] == "other"
            # The parts the files write, by the one rule Enable, Apply and the preview use (``targets.resolve``).
            targets = set(replacements)
            if resolution.older:
                targets.update(library._other_targets(resolution.older, pin) if generic else resolution.updated)
            targets = [path for path in live if path in targets]
            inferred = entry["source"] is None
            choices = (
                claimed_first(
                    mod_origin.candidates(names, selected, official_contents, entry["target"]), candidate.package
                )
                if inferred
                else [entry["source"]]
            )
            matched_targets, last_failure = None, None
            for choice in choices:
                converting = conversion(choice)
                failure = None
                chosen = targets
                # Another server's files are accepted where the resolver places them by name, CAB or asset path;
                # the file names decide only for the others.
                if choice["server"] != entry["target"]["server"] and not generic and resolution.unplaced:
                    failure = "conversion_names"
                if not failure and (converting or inferred or rebuild):
                    if matched_targets is None:
                        matched_targets = resolution.sharing_keys(targets)
                    chosen = matched_targets
                if not chosen:
                    failure = failure or (
                        "no_matching_assets" if converting or inferred or rebuild else "game_file_not_found"
                    )
                mismatch = False
                if not failure and (selected.skeletons or compatibility.get("remappable")):
                    if exact and not converting and not rebuild:
                        # The ready path copies whole bundles, ignoring update filters and matching keys.
                        official, incoming, remappable = copy_compatibility(compatibility, selected)
                    else:
                        official, incoming, remappable = transfer_compatibility(
                            inspection,
                            sources,
                            [baseline[path] for path in chosen],
                            originals,
                            "name_type"
                            if converting or rebuild
                            else strategy or engine_match_strategy(self.options()["matchStrategy"]),
                        )
                    try:
                        mismatch = self._check_spine(candidate, official, incoming, remappable)
                    except ServiceError as error:
                        failure = error.code
                if failure:
                    if inferred:
                        attempts.append({"server": choice["server"], "platform": choice["platform"], "code": failure})
                        # A guessed server whose file names the install lacks says less than why a guess with
                        # matching names failed, so that reason is kept.
                        if last_failure in (None, "conversion_names"):
                            last_failure = failure
                        continue
                    raise ServiceError(failure)
                if rebuild or inferred or (candidate.source_override or {}).get("detected"):
                    # Validate selected inputs against current counterparts using the execution reader.
                    try:
                        counterparts = tuple(baseline[path] for path in chosen)
                        required = set().union(
                            *(inspection.readable_keys(path, targets=counterparts) for path in sources)
                        )
                        available = set().union(*(inspection.keys(baseline[path]) for path in chosen))
                        if not required or not required <= available:
                            raise ServiceError("no_matching_assets")
                    except (ServiceError, OSError, ValueError) as error:
                        if inferred:
                            attempts.append(
                                {
                                    "server": choice["server"],
                                    "platform": choice["platform"],
                                    "code": getattr(error, "code", "unreadable"),
                                }
                            )
                        inspection.validate()
                        raise
                if not converting and not rebuild:
                    library.inspect_import(candidate.files, pin=pin, inspection=inspection)
                inspection.validate()
                candidate.conversion = converting
                candidate.rebuild = rebuild
                candidate.targets, candidate.originals = chosen, [baseline[p] for p in chosen]
                if inferred:
                    candidate.source_override = dict(choice)
                entry["source"] = choice
                for file in entry["files"]:
                    if file["target"] is None and len(chosen) == 1:
                        file["target"] = chosen[0].name
                entry.update(
                    state="convert" if converting else "ready" if exact and not mismatch and not rebuild else "update",
                    reason=None,
                )
                return
            raise ServiceError(last_failure or "source_unknown")
        except ServiceError as error:
            candidate.missing_cabs = set(inspection.missing_cabs)
            if evidence := bundle_evidence(candidate, inspection):
                entry["bundles"] = evidence
            target_evidence(entry, resolution, attempts)
            if error.code == "mod_parts_missing" and candidate.missing_cabs:
                entry.update(state="incomplete", reason=None, missing=missing_detail(candidate.missing_cabs, []))
                return
            entry.update(
                state="blocked",
                reason=blocked_reason(error),
                error=failure_detail(
                    error, candidate.files.values(), log, secrets=getattr(self.services, "log_secrets", set())
                ),
            )
            if error.params:
                entry["params"] = error.to_json()["params"]
        except (OSError, ValueError) as error:
            if evidence := bundle_evidence(candidate, inspection):
                entry["bundles"] = evidence
            target_evidence(entry, resolution, attempts)
            detail = ServiceError("bundle_invalid", file=source_file(candidate), error=str(error))
            entry.update(
                state="blocked",
                reason="unreadable",
                error=failure_detail(
                    detail, candidate.files.values(), log, secrets=getattr(self.services, "log_secrets", set())
                ),
            )

    def _check_spine(self, candidate: Candidate, official: dict, incoming: dict, remappable: bool) -> bool:
        versions = {version for values in official.values() for version in values if version}
        series = {tuple(version.split(".")[:2]) for version in versions}
        candidate.spine_version = (
            max(versions, key=lambda version: tuple(map(int, version.split(".")))) if len(series) == 1 else None
        )
        unknown = any(not version for version in incoming.values())
        mismatch = any(
            version and any(not target or version.split(".")[:2] != target.split(".")[:2] for target in official[key])
            for key, version in incoming.items()
        )
        if remappable:
            raise ServiceError("conflict")
        if unknown or (
            mismatch and (candidate.spine_version is None or not self.services.settings.spine_options().is_valid())
        ):
            raise ServiceError("spine_version")
        return mismatch

    @staticmethod
    def _require_importable(candidate: Candidate) -> None:
        if candidate.entry["state"] not in IMPORTABLE:
            detail = candidate.entry.get("error") or {
                "code": candidate.entry.get("reason") or "entry_changed",
                "params": candidate.entry.get("params", {}),
            }
            raise ServiceError(detail["code"], **detail["params"])
