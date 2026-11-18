"""Adding mods to the library: their files, their groups and the checks of a new group."""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import logging
from pathlib import Path

from bmpt.spine import atlas_remap
from bmpt.core import devmode
from bmpt.engine import (
    Bundle,
    LogFunc,
    ProgressCallback,
    get_skel_version,
    no_log,
    parse_filename,
    t,
)
from ...core.errors import ServiceError
from .. import fingerprint, integrity
from ..judge import (
    core_of,
    date_of,
    kind_of_file,
    other_target,
    part_of,
    supports_kind,
)
from ..models import (
    AddResult,
    Kind,
    Pin,
)
from .base import _mod_writer, _serialized


logger = logging.getLogger(__package__)


class AddMixin:
    """Adding mods to the library: their files, their groups and the checks of a new group."""

    def add_files(
        self,
        kind: Kind,
        paths: list[Path],
        progress: ProgressCallback | None = None,
        pin: Pin | None = None,
        *,
        apply: bool = False,
        log: LogFunc = no_log,
        direct: bool = False,
    ) -> AddResult:
        """Import a batch of one supported kind, optionally applying through the master switch.

        Args:
            kind: Required library kind: spr, lobby, model, or other.
            paths: Uploaded bundle files to group and deduplicate as complete mods.
            progress: Receives import and optional apply progress; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
            apply: Whether to select each newly added mod through the normal switch flow.
            log: Receives user-facing operation messages.
            direct: Whether storage may accept verified current-release whole files;
                applies only when apply is False.
        """
        if not supports_kind(kind):
            if not paths:
                raise ServiceError("source_unsupported", kind=kind)  # no file to name
            raise ServiceError("mod_unsupported", file=paths[0], kind=kind)
        if apply:
            return self.add_and_apply(paths, progress, pin, log, kind=kind)
        return self.add(paths, progress, pin, kind=kind, direct=direct)

    @_serialized
    @_mod_writer
    def add(
        self,
        paths: list[Path],
        progress: ProgressCallback | None = None,
        pin: Pin | None = None,
        *,
        kind: Kind | None = None,
        direct: bool = False,
    ) -> AddResult:
        """Store and deduplicate complete bundle sets grouped by slot, kind, and version.

        Parts may carry different dates. A repeated part under another date splits
        the set into versions by date, following judge.mod_versions. Development mode
        restricts reads to the configured development roots.

        Args:
            paths: Uploaded bundle files, including .bundle.backup files whose backup suffix is stripped.
            progress: Receives (processed files, total files, message); None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
            kind: Required library kind, or None to infer each group's kind.
            direct: Whether verified current-release whole files may bypass rebuild-oriented preflight.

        Returns:
            Newly stored entries with duplicate and recognized-installed entries reported separately.
        """
        for path in paths:
            devmode.check_read(path)
        self._note_packed(paths, pin)  # under the locks add holds: no library lock after its file locks
        return self._add(paths, progress, pin, kind, direct=direct)

    def _identity(self, files: dict[str, Path]) -> frozenset:
        return frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1) for name, path in files.items())

    @_serialized
    def import_source_identity(self, files: dict[str, Path], target: dict, originals: list[Path]) -> dict:
        """Bind an import's content identity to its target and exact official conversion baseline.

        Args:
            files: Canonical bundle filenames mapped to uploaded paths; paths may move without changing identity.
            target: Import target descriptor copied into the identity to scope server and platform.
            originals: Official baseline files whose names and SHA-1 digests scope the conversion release.
        """
        return {
            "files": sorted(
                ([list(part_of(name)), self._hashes.get(path).sha1] for name, path in files.items()),
                key=lambda item: json.dumps(item),
            ),
            "target": dict(target),
            "originals": sorted([path.name, self._hashes.get(path).sha1] for path in originals),
        }

    def _output_identity(self, paths: list[Path]) -> list:
        """Bind every stored output hash to its complete part filename."""
        return sorted([path.name, self._hashes.get(path).sha1] for path in paths)

    @_serialized
    def find_import_source(self, source: dict) -> str | None:
        """Find an import provenance match only while its recorded output bytes remain unchanged.

        Args:
            source: Content-and-target identity returned by import_source_identity.

        Returns:
            Matching stored mod ID, or None when source or current output differs.
        """
        for record in self._records.values():
            matches = [item for item in record.get("import_sources", []) if item.get("source") == source]
            if matches:
                output = self._output_identity(self._paths(record))
                if any(item.get("output") == output for item in matches):
                    return record["id"]
        return None

    @_serialized
    def find_legacy_import_source(self, source: dict, kind: str) -> str | None:
        """Recognize only recorded source hashes, scoped to their target and official release.

        Entries without these records cannot be identified without a rebuild, so skip them.
        Cache both hits and misses until the source, records or stored file revisions change.

        Args:
            source: Import content-and-target identity to match against legacy source_hashes.
            kind: Library kind restricting candidate records to the same asset class.

        Returns:
            Matching legacy mod ID, or None when no recorded provenance proves a match.
        """
        records, revisions = [], []
        for record in self._records.values():
            if record["kind"] != kind or record.get("import_sources") or not record.get("source_hashes"):
                continue
            records.append(record)
            stamps = []
            for path in self._paths(record):
                stat = path.stat()
                stamps.append((path.name, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            revisions.append((record["id"], record["source_hashes"], stamps))
        key = json.dumps([source, kind, revisions], sort_keys=True)
        if key not in self._legacy_import_matches:
            match = next((record["id"] for record in records if record["source_hashes"] == source), None)
            if len(self._legacy_import_matches) >= 128:
                self._legacy_import_matches.clear()
            self._legacy_import_matches[key] = match
        return self._legacy_import_matches[key]

    @_serialized
    def legacy_import_snapshot(self, kind: str, target: str) -> list:
        """Snapshot unlinked legacy records for an asset before an expensive output comparison.

        Args:
            kind: Library kind restricting legacy candidate records.
            target: Stored ownership target restricting candidates to the same asset.

        Returns:
            Sorted mod IDs paired with their current complete filenames and SHA-1 digests.
        """
        return sorted(
            [record["id"], self._output_identity(self._paths(record))]
            for record in self._records.values()
            if record["kind"] == kind
            and record["target"] == target
            and not record.get("import_sources")
            and not record.get("source_hashes")
        )

    @_serialized
    def find_import_output(self, paths: list[Path], candidates: set[str] | None = None) -> str | None:
        """Match stored output by exact filenames and bytes, optionally normalizing packing and CRC tails.

        Args:
            paths: Prepared imported bundles whose output is compared with library records.
            candidates: Mod IDs eligible for legacy normalized comparison; None searches all records
                by exact byte identity only.

        Returns:
            Matching mod ID, or None when no candidate has equivalent complete parts.
        """
        output = self._output_identity(paths)
        records = [record for record in self._records.values() if candidates is None or record["id"] in candidates]
        exact = next((record["id"] for record in records if self._output_identity(self._paths(record)) == output), None)
        if exact or candidates is None:
            return exact

        # Normalize only the container packing and CRC tail. Serialized objects,
        # external references, streams and complete part filenames must still agree.
        def contents(files):
            result = []
            for path in files:
                bundle = Bundle.load(path)
                if bundle is None:
                    raise ServiceError("bundle_invalid", file=path)
                result.append([path.name, hashlib.sha256(bundle.compress("none")).hexdigest()])
            return sorted(result)

        wanted = contents(paths)
        names = {path.name for path in paths}
        for record in records:
            files = self._paths(record)
            if {path.name for path in files} == names and contents(files) == wanted:
                return record["id"]
        return None

    @_serialized
    @_mod_writer
    def remember_import_source(self, mod_id: str, source: dict) -> None:
        """Link import provenance to a mod's current output in one state write.

        Args:
            mod_id: Stored mod ID whose current output hashes are bound to the provenance.
            source: Content-and-target identity to deep-copy into the mod's import_sources.
        """
        record = self._record(mod_id)
        item = {"source": copy.deepcopy(source), "output": self._output_identity(self._paths(record))}
        previous = copy.deepcopy(record)
        imports = record.setdefault("import_sources", [])
        if item in imports:
            return
        imports.append(item)
        try:
            self._save()
        except Exception:
            self._records[mod_id] = previous
            raise

    def _known(self) -> dict[frozenset, str]:
        known = {}
        for record in self._records.values():
            identity = self._identity({path.name: path for path in self._paths(record)})
            if identity in known:
                logger.warning(
                    "Duplicate library mods: %s (%s), %s (%s)",
                    known[identity],
                    self._records[known[identity]]["label"],
                    record["id"],
                    record["label"],
                )
            else:
                known[identity] = record["id"]
        return known

    def _add(
        self,
        paths: list[Path],
        progress: ProgressCallback | None = None,
        pin: Pin | None = None,
        expected_kind: Kind | None = None,
        *,
        direct: bool = False,
    ) -> AddResult:
        pin = pin or self.current_pin()
        known = self._known()
        groups: dict[tuple[str, ...], dict[str, Path]] = {}
        targets = {}
        taken: list[tuple[Path, str, str, str, str]] = []
        for i, source in enumerate(map(Path, paths)):
            if progress:
                progress(i, len(paths), t("webui.progress.add", name=source.name))
            name = source.name.removesuffix(".backup") if source.name.endswith(".bundle.backup") else source.name
            parsed = parse_filename(name)
            kind = kind_of_file(name)
            if not name.endswith(".bundle"):
                raise ServiceError("mod_unsupported", file=source)
            if not parsed.prefix or not parsed.crc or kind is None:
                fingerprint = self._hashes.get(source).sha1
                matches = [
                    path.name
                    for record in self._records.values()
                    for path in self._paths(record)
                    if self._hashes.get(path).sha1 == fingerprint
                ]
                if len({core_of(name) for name in matches}) != 1:
                    raise ServiceError("mod_unsupported", file=source)
                name = matches[0]
            name, target, kind = self._identify_import(source, name, pin)
            if expected_kind is not None and kind != expected_kind:
                raise ServiceError("mod_unsupported", file=source)
            core = self.import_core(parse_filename(name).core, kind, pin)
            taken.append((source, name, target, kind, core))
        # Model sets share their prefab slot key; other character kinds keep their physical core.
        versions = iter(
            self.import_versions([((core, kind), name) for _, name, _, kind, core in taken if kind != "other"], pin)
        )
        for source, name, target, kind, core in taken:
            key = (str(source.parent), "other") if kind == "other" else (core, kind, next(versions))
            targets[key] = (target, kind)
            group = groups.setdefault(key, {})
            if kind == "other":
                previous = next((old for old in group if part_of(old) == part_of(name)), None)
                if previous:
                    if self._hashes.get(group[previous]).sha1 != self._hashes.get(source).sha1:
                        raise ServiceError("mod_target_ambiguous", file=source)
                    if date_of(previous) >= date_of(name):
                        continue
                    del group[previous]
            if name in group and self._hashes.get(group[name]).sha1 != self._hashes.get(source).sha1:
                raise ServiceError("mod_unsupported", file=source)
            group[name] = source

        # Reject invalid uploads before publishing any group.
        whole = set()  # groups copied whole as files made for the current parts
        for key, group in groups.items():
            if direct and self.direct_import(group, pin):
                whole.add(key)
            else:
                self._check_group(group, pin, compatibility=False)
        added = AddResult()
        for key, group in groups.items():
            signature_ = self._identity(group)
            if signature_ in known:
                entry = self._entry(self._records[known[signature_]])
                if entry not in added and entry not in added.duplicates:
                    added.duplicates.append(entry)
                if self._recognize_installed(entry.id, signature_):
                    added.installed.append(entry)
                continue
            names = sorted(group)
            parsed = parse_filename(names[0])
            target, kind = targets[key]
            if kind == "other":
                target = other_target(group)
            record = self._store(
                group,
                parsed.core,
                target,
                kind,
                user_added=True,
                built_against=self._built_against(group, pin, "import", painted=key in whole),
            )
            known[signature_] = record["id"]
            entry = self._entry(record)
            added.append(entry)
            if self._recognize_installed(entry.id, signature_):
                added.installed.append(entry)
        if progress:
            progress(
                len(paths),
                len(paths),
                t("webui.progress.add", name=paths[-1].name if paths else t("webui.progress.mods")),
            )
        return added

    def _check_group(
        self,
        files: dict[str, Path],
        pin: Pin | None,
        *,
        compatibility: bool = True,
        dependencies: bool = True,
        conversion: bool = False,
        variant_key: str | None = None,
        inspection: integrity.Inspection | None = None,
        built: dict | None = None,
    ) -> dict:
        """Check a group of mod files and return the report on them.

        ``built`` is the stored fingerprint of the files when they are a library mod's (``_read_built_against``). The
        report's ``rig`` names what the official rig changed under the mod: atlases repacked since its textures were
        drawn (``layout``), animations its skeleton lacks and regions its atlas lacks (``atlas_remap.rig_gains``).
        ``remappable`` is any of them or a page size the texture does not fit (``misfit``).
        """
        inspect = inspection.inspect if inspection else integrity.inspect
        cab_inspection = inspection or integrity.Inspection()
        selected, official, effective = integrity.Contents(), integrity.Contents(), integrity.Contents()
        baseline = integrity.Contents()
        sources = {
            name: inspect(path, allow_unknown=True) if conversion else inspect(path) for name, path in files.items()
        }
        core = core_of(next(iter(files)))
        kind = kind_of_file(next(iter(files)))
        target_core = core
        targets = (self._other_targets(files, pin) if kind == "other" else self._spine_parts(core, pin)) if pin else []
        if pin and not targets and variant_key and kind != "other":
            target_core = self._core_map(pin).core(variant_key, kind) or core
            targets = self._spine_parts(target_core, pin)
        if targets and kind not in ("other", "model"):
            target_core = parse_filename(targets[0].name).core
        originals, original_paths, current_parts = {}, [], {}
        unavailable = not targets
        for target in targets:
            try:
                original = self._official_for(target, pin, game_first=True)
                originals[target] = inspect(original)
                original_paths.append(original)
            except ServiceError as error:
                if error.code not in ("official_unavailable", "official_invalid"):
                    raise
                unavailable = True
                current_parts[target] = inspect(target)
        for contents in originals.values():
            official.merge(contents)
        baseline_parts = {**current_parts, **originals}
        for contents in baseline_parts.values():
            baseline.merge(contents)
        expected = official.platforms or {"StandaloneWindows64"}
        for name, contents in sources.items():
            if not conversion:
                integrity.check_platform(contents, files[name], expected)
            selected.merge(contents)
        # Compatibility may address a variant whose installed core changed after this Mod was stored.
        target_names = (
            {
                name.replace(f"-{parse_filename(name).core}-", f"-{target_core}-", 1): path
                for name, path in files.items()
            }
            if target_core != core and kind != "other"
            else files
        )
        resolution = self._resolve(target_names, targets, pin, cab_inspection)
        replacements = resolution.current
        replaced = set(replacements)
        older = resolution.older
        check_dependencies = dependencies and (not compatibility or kind in ("model", "other"))
        if older and kind == "other":
            replaced.update(self._other_targets(older, pin) if pin else [])
        elif older:
            # The dependency check leaves out every part the files write; the Spine comparison, as the preview, only
            # the parts they stand in for.
            replaced.update(resolution.updated if check_dependencies else resolution.replaced)
        for target, contents in baseline_parts.items():
            if target not in replaced:
                effective.merge(contents)
        if not compatibility:
            # Import validates dependencies only; absent text parts use the current official assets.
            paired = atlas_remap.pair_atlases(selected.atlases, baseline.atlases)
            selected.atlases = {
                **{name: data for name, data in baseline.atlases.items() if name not in paired},
                **selected.atlases,
            }
        effective.merge(selected)
        missing = (
            integrity.missing_parts(effective) - integrity.missing_parts(baseline) if check_dependencies else set()
        )
        if missing and pin:
            references = missing & effective.references
            if references:
                missing.difference_update(
                    self._cab_index(pin).resolve(references, targets[0] if targets else None, exclude=replaced)
                )
        if missing:
            if inspection is not None:
                inspection.missing_cabs.update(missing & effective.references)
            names = dict(effective.reference_names)
            for target in replaced:
                # CABs removed by this upload identify the omitted replacement file, even without an original.
                with contextlib.suppress(ValueError):
                    names.update({name: target.name for name in cab_inspection.cabs(target)})
                if target in baseline_parts:
                    names.update({name: target.name for name in baseline_parts[target].files})
            for folder in {path.parent for path in files.values()}:
                if cab_inspection.header_only_missing:
                    continue
                for path in sorted([*folder.glob("*.bundle"), *folder.glob("*.bundle.backup")]):
                    if path not in files.values() and core_of(path.name) == core:
                        try:
                            contents = inspect(path)
                        except ServiceError:
                            continue
                        names.update({name: path.name for name in contents.files})
                        names.update({name + ".png": path.name for name in contents.textures})
            raise ServiceError(
                "mod_parts_missing", files=", ".join(sorted({names.get(name, name) for name in missing}))
            )
        if not compatibility:
            return {}
        current = self._replacements(files, targets, pin, cab_inspection) if target_names is not files else replacements
        report = {
            "kind": kind,
            "official": official.spine(),
            "mod": effective.spine(),
            "unavailable": unavailable,
            "outdated": bool(targets) and len(current) != len(files),
        }
        if conversion:
            # Internal preflight metadata; ordinary compatibility RPCs remain JSON-only.
            report["sourceContents"], report["officialContents"] = selected, official
            report["originalPaths"] = original_paths
        try:
            mixed = {
                name: data
                for name, data in effective.atlases.items()
                if name not in selected.atlases
                and any(
                    integrity.texture_key(page) in selected.textures
                    for page in atlas_remap.parse_atlas(data, name).pages
                )
            }
            misfits = {
                name for name, data in mixed.items() if atlas_remap.page_size_mismatch({name: data}, effective.textures)
            }
            misfit = bool(misfits)
            layout = fingerprint.changed_layouts(built, mixed)
            if kind == "spr" and not unavailable:
                # Where no record keeps the rig the mod's textures were drawn for, the earlier release the archive
                # keeps that they follow (rig_archive), against the installed pages of the verified official copies.
                unrecorded = {
                    name: data
                    for name, data in mixed.items()
                    if not isinstance((layout.get(name) or {}).get("skeleton"), str)
                }
                layout = {
                    **layout,
                    **self._archived_rigs(
                        list(files.values()), unrecorded, lambda: self._official_page_masks(original_paths)
                    ),
                }
            gains = atlas_remap.rig_gains(selected.atlases, selected.skeletons, official.atlases, official.skeletons)
            rerig = atlas_remap.rerigged(
                gains, selected.atlases, selected.skeletons, official.atlases, official.skeletons, selected.textures
            )
            report["rig"] = {
                "layout": sorted(layout),
                "animations": gains["animations"],
                "regions": {name: gains["regions"][name] for name in rerig if name in gains["regions"]},
            }
            # The rig a repacked atlas's textures were drawn for, where it was recorded: the source of the
            # world-space rebake (rig-geometry.js forRemap).
            recorded = {name: entry for name, entry in layout.items() if isinstance(entry.get("skeleton"), str)}
            dated = (
                self._dated_rig(list(files.values()), misfits - recorded.keys(), official.atlases, original_paths)
                if kind == "spr" and not unavailable
                else {}
            )
            # The UI needs to distinguish a repacked atlas without a usable repair rig.
            report["rig"]["unknown"] = sorted(
                name
                for name, entry in layout.items()
                if not isinstance(entry.get("skeleton"), str)
                and not isinstance((dated.get(name) or {}).get("skeleton"), str)
            )
            drawn_by = atlas_remap.pair_atlases(official.skeletons, dict.fromkeys({**dated, **recorded}))
            report["source"] = {
                "atlases": {name: entry["text"] for name, entry in {**dated, **recorded}.items()},
                "skeletons": {},
            }
            for name, entry in {**dated, **recorded}.items():
                if not isinstance(entry.get("skeleton"), str):
                    continue
                data = base64.b64decode(entry["skeleton"])
                skeleton = drawn_by.get(name) or f"{Path(name).stem}.skel"
                report["source"]["skeletons"][skeleton] = {"version": get_skel_version(data), "data": entry["skeleton"]}
            # The whole rig a texture-only portrait's textures were drawn for (``old_rig``): what Update offers for
            # a portrait the game drew anew, which the rebake cannot move (mod-compatibility.js).
            old_rig = (
                atlas_remap.old_rig_plan(
                    {**layout, **dated},
                    official.atlases,
                    official.skeletons,
                    selected.skeletons,
                    selected.textures,
                    official.textures,
                )
                if kind == "spr"
                else atlas_remap.OldRig()
            )
            report["oldRig"] = {
                "atlases": sorted(old_rig.atlases),
                "animations": old_rig.animations,
                "available": not unavailable and old_rig.available,
            }
            # The graft (bmpt.spine.graft): the mod's own binary skeleton keeps its rig and takes the animations the
            # official one gained; or the old rig above takes them, where they keep it from being offered.
            # Memorial lobbies and Spine backgrounds take the graft too (``graft.plain``): what each new animation
            # needs is known from the two skeletons alone, so the check tells which it carries and why not the others.
            own = (
                {
                    name: names
                    for name, names in gains["animations"].items()
                    if self._graftable(selected, official, name)
                }
                if kind in ("spr", "lobby")
                else {}
            )
            plans = {name: self._graft_plan(selected, official, name) for name in own}
            refused = {name: plan[1] for name, plan in plans.items() if plan[1]}
            own = {name: plan[0] for name, plan in plans.items() if plan[0]}
            gained = old_rig.animations if kind == "spr" and old_rig.atlases and old_rig.complete else {}
            report["graft"] = {
                "kind": "own" if own else "oldRig" if gained else None,
                "animations": own or gained,
                "refused": refused,
                "available": not unavailable and bool(own or gained),
            }
            report["misfit"] = not unavailable and misfit
            # Portraits are baked through world space; other kinds only move regions onto a page size that fits.
            wanted = misfit or bool(recorded) or bool(rerig) if kind == "spr" else misfit
            report["remappable"] = not unavailable and wanted
        except (ValueError, UnicodeError):
            report["rig"] = {"layout": [], "animations": {}, "regions": {}, "unknown": []}
            report["source"] = {"atlases": {}, "skeletons": {}}
            report["oldRig"] = {"atlases": [], "animations": {}, "available": False}
            report["graft"] = {"kind": None, "animations": {}, "refused": {}, "available": False}
            report["misfit"] = report["remappable"] = False
        return report
