"""Batch import preflight and execution, built on the library and classic updater."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bmpt import atlas_remap, devmode, mod_origin
from ba_modding_toolkit import core
from ba_modding_toolkit.bundle import Bundle, UpdateScope
from ..errors import ServiceError
from ..gameassets.spine import spine_revision
from ..gameassets.discovery import cab_names
from ba_modding_toolkit.i18n import t
from ..library import integrity, staging, manual_identity
from ..library.judge import core_of, kind_of, part_of
from ..library.storage import Fingerprints, atomic_clone, verify_file
from ba_modding_toolkit.models import AssetType, SaveOptions
from ba_modding_toolkit.naming import parse_filename
from ba_modding_toolkit.searching import _asset_match
from ba_modding_toolkit.spine import get_skel_version
from ba_modding_toolkit.utils import CRCUtils, no_log, parse_hex_bytes

logger = logging.getLogger(__name__)
PREF = "batch_import"
TYPES = ("Texture2D", "TextAsset", "Mesh", "AnimationClip", "ALL")
CHOICES = {"strategy": ("path_id", "cont_name_type", "name_type"),
           "crc": ("auto", "on", "off"), "compression": ("lzma", "lz4", "original", "none")}
BOOLS = ("skipUnchanged", "backup", "enableAfterImport", "previewAfterImport")
IMPORTABLE = frozenset(("ready", "convert", "update"))
# Staging folders are named by the first hex digits of an id or digest. The bundle names inside keep their full form,
# so the folder names stay short enough for Windows paths (259 characters) with the longest official bundle names.
STAGING_DIGITS = 16


def staging_name(digest: str) -> str:
    """The short, stable name of the staging folder of an entry id or an original's digest."""
    return digest[:STAGING_DIGITS]


def options_of(value: Any, defaults: dict) -> dict:
    """Validate complete RPC options (and merge older saved preferences with new defaults)."""
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ServiceError("bad_params", key="options")
    out = {**defaults, **value}
    for key, choices in CHOICES.items():
        if not isinstance(out[key], str) or out[key] not in choices:
            raise ServiceError("bad_params", key=key)
    if any(not isinstance(out[key], bool) for key in BOOLS):
        raise ServiceError("bad_params", key="options")
    if not isinstance(out["types"], list) or not out["types"] or any(
            not isinstance(item, str) or item not in TYPES for item in out["types"]):
        raise ServiceError("bad_params", key="types")
    out["types"] = list(dict.fromkeys(out["types"]))
    if not isinstance(out["workers"], int) or isinstance(out["workers"], bool) or not 1 <= out["workers"] <= 64:
        raise ServiceError("bad_params", key="workers")
    if not isinstance(out["extraBytes"], str) or (out["extraBytes"] and parse_hex_bytes(out["extraBytes"]) is None):
        raise ServiceError("bad_params", key="extraBytes")
    if out["output"] is not None and (not isinstance(out["output"], str) or not out["output"].strip()):
        raise ServiceError("bad_params", key="output")
    if out["output"] is not None:
        out["output"] = str(Path(out["output"]).expanduser().resolve())
        devmode.check_read(Path(out["output"]))
    return out


class ImportSettings:
    """A read-only overlay; the classic config and the library's shared settings stay intact."""

    def __init__(self, base: Any, options: dict):
        self.base, self.options = base, options
        self._crc: dict[Path, tuple[tuple[int, ...], bool]] = {}
        self._crc_lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def asset_types(self) -> set[str]:
        return set(self.options["types"])

    def crc_needed(self, target: Path | None) -> bool:
        setting = self.options["crc"]
        if setting != "auto" or target is None:
            return setting == "on"
        target = target.resolve()
        stat = target.stat()
        stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self._crc_lock:
            cached = self._crc.get(target)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        value = Bundle.check_need_crc(target)
        with self._crc_lock:
            self._crc[target] = (stamp, value)
        return value

    def save_options(self, perform_crc: bool) -> SaveOptions:
        return SaveOptions(perform_crc=perform_crc, compression=self.options["compression"],
                           extra_bytes=parse_hex_bytes(self.options["extraBytes"]))


@dataclass
class Candidate:
    files: dict[str, Path]
    entry: dict
    error: str | None = None
    identity: frozenset = frozenset()
    targets: list[Path] = field(default_factory=list)
    originals: list[Path] = field(default_factory=list)
    spine_version: str | None = None
    source_override: dict | None = None
    conversion: bool = False
    rebuild: bool = False
    not_transferred: list[str] = field(default_factory=list)
    failure: dict | None = None
    direct: dict[Path, Path] = field(default_factory=dict)
    provenance: dict | None = None
    missing_cabs: set[str] = field(default_factory=set)


@dataclass
class ImportResult:
    results: list[dict]
    cores: set[str] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    added: list[Any] = field(default_factory=list)
    mods_changed: bool = True

    def to_json(self) -> dict:
        return {"results": self.results}


def source_file(candidate: Candidate) -> str:
    """The file an error about a whole entry names: its first source, which the list shows as the entry's file."""
    return str(min(candidate.files.values()))


def blocked_error(candidate: Candidate) -> ServiceError:
    """The error of a blocked candidate: its code with the details recorded when it was analyzed, and a file."""
    params = dict(candidate.failure["params"]) if candidate.failure else {}
    params.setdefault("file", source_file(candidate))
    return ServiceError(candidate.error, **params)


def blocked_reason(error: ServiceError) -> str:
    aliases = {"bundle_invalid": "unreadable", "game_file_not_found": "no_target",
               "mod_unsupported": "unsupported", "source_unsupported": "unsupported"}
    reasons = {"mod_platform_unknown", "library_io_error", "mod_target_ambiguous", "mod_target_unknown",
               "mod_parts_missing", "mod_release_mismatch", "game_file_conflict", "mod_platform_mismatch",
               "mod_unity_version_mismatch",
               "required_asset_extract_failed", "required_asset_apply_failed", "required_assets_unmatched",
               "spine_version", "source_unknown", "source_conflict", "platform_unsupported", "target_unknown",
               "conversion_names", "no_matching_assets", "conversion_failed", "entry_changed",
               "original_unavailable", "original_invalid", "conflict", "unreadable", "unsupported", "failed"}
    return aliases.get(error.code, error.code if error.code in reasons else "failed")


#: The unresolved CABs an incomplete entry names, and the bundles whose summary a refused entry carries
EVIDENCE_CABS = 40
EVIDENCE_BUNDLES = 20


def missing_detail(cabs: set[str], found: list[dict]) -> dict:
    """An incomplete entry's ``missing``: how many CABs it references that neither its files, the install nor the
    other dropped files hold, the first EVIDENCE_CABS of them, and the unselected files that complete it."""
    return {"unresolved": len(cabs), "cabs": sorted(cabs)[:EVIDENCE_CABS], "found": found}


def bundle_evidence(candidate: Candidate, inspection: integrity.Inspection) -> list[dict]:
    """What the check read of each of an entry's files (``Inspection.summary``), for the error report and the log: a
    refusal from the files' format can be told from one guessed from their names or targets."""
    rows = []
    for path in sorted(candidate.files.values())[:EVIDENCE_BUNDLES]:
        summary = inspection.summary(path)
        if summary is not None:
            rows.append({"file": path.name, **summary})
    return rows


def failure_detail(error: ServiceError, paths: Any, log: Any = no_log, *, secrets: set[str] | None = None) -> dict:
    from .diagnostics import clean

    detail = error.to_json()
    safe = clean({"files": list(map(str, paths)), **detail}, secrets or set())
    message = f"Batch import: {json.dumps(safe, ensure_ascii=False, sort_keys=True)}"
    logger.warning(message)
    log(message)
    return detail


def copy_compatibility(report: dict, uploaded: integrity.Contents) -> tuple[dict, dict, bool]:
    """A direct copy installs every uploaded asset alongside the retained official parts."""
    skeletons = report.get("official", {}).get("skeletons", {})
    incoming = {name: get_skel_version(data) for name, data in uploaded.skeletons.items()}
    versions = {data["version"] for data in skeletons.values()} or {None}
    official = {name: {skeletons[name]["version"]} if name in skeletons else versions for name in incoming}
    return official, incoming, report.get("remappable", False)


def transfer_compatibility(inspection: integrity.Inspection, sources: list[Path], targets: list[Path],
                           originals: list[Path], strategy: str) -> tuple[dict, dict, bool]:
    """Check transferred Spine inputs against their counterparts and retained official parts."""
    scope = UpdateScope([inspection.bundle(path) for path in targets], strategy, inspection.asset_types)
    key_of = Bundle._get_key_func(strategy)
    incoming, official = {}, {}
    atlases, textures, transferred_atlases, transferred_textures = {}, {}, set(), {}
    for path in originals:
        contents = inspection.inspect(path)
        atlases.update(contents.atlases)
        textures.update(contents.textures)
    for path in sources:
        contents = inspection.inspect(path, allow_unknown=True)
        for obj in inspection.bundle(path).env.objects:
            if obj.type not in (AssetType.TextAsset, AssetType.Texture2D):
                continue
            if "ALL" not in inspection.asset_types and obj.type.name not in inspection.asset_types:
                continue
            key = key_of(obj)
            if key not in scope.counterparts:
                continue
            name = obj.peek_name()
            for target, counterpart in scope.counterparts[key]:
                target_name = counterpart.peek_name()
                if obj.type == AssetType.TextAsset and name in contents.skeletons:
                    incoming[key] = get_skel_version(contents.skeletons[name])
                    data = inspection.inspect(target.path).skeletons.get(target_name)
                    official.setdefault(key, set()).add(get_skel_version(data) if data is not None else None)
                elif obj.type == AssetType.TextAsset and name in contents.atlases:
                    transferred_atlases.add(target_name)
                elif obj.type == AssetType.Texture2D:
                    size = contents.textures.get(integrity.texture_key(name))
                    if size is not None:
                        transferred_textures[integrity.texture_key(target_name)] = size
    try:
        mixed = {name: data for name, data in atlases.items() if name not in transferred_atlases
                 and any(integrity.texture_key(page) in transferred_textures
                         for page in atlas_remap.parse_atlas(data, name).pages)}
        remappable = bool(mixed) and atlas_remap.page_size_mismatch(mixed, {**textures, **transferred_textures})
    except (ValueError, UnicodeError):
        remappable = False
    inspection.validate()
    return official, incoming, remappable


class BatchImport:
    def __init__(self, services: Any):
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
        settings = self.services.settings
        return {"strategy": "cont_name_type", "types": sorted(settings.asset_types()) or ["ALL"],
                "skipUnchanged": settings.get("skip_unchanged"),
                "crc": {"true": "on", "false": "off"}.get(settings.get("enable_crc_correction"), "auto"),
                "compression": settings.get("compression_method"), "extraBytes": settings.get("extra_bytes"),
                "backup": settings.get("create_backup"), "workers": settings.get("max_workers"),
                "output": settings.get("output_dir") or None, "enableAfterImport": True,
                "previewAfterImport": False}

    def options(self) -> dict:
        defaults = self.defaults()
        try:
            return options_of(self.services.state.get(PREF, {}), defaults)
        except ServiceError:
            return options_of({}, defaults)

    def set_options(self, value: dict) -> dict:
        options = options_of(value, self.defaults())
        self.services.state.set(PREF, options)
        return options

    def pick(self, folder: bool = False, recursive: bool = False, directory: str | None = None) -> dict:
        if not isinstance(folder, bool) or not isinstance(recursive, bool):
            raise ServiceError("bad_params", key="folder")
        if self.services.window is None:
            raise ServiceError("native_unavailable")
        if directory is not None:
            if not isinstance(directory, str) or not directory:
                raise ServiceError("bad_params", key="directory")
            devmode.check_read(Path(directory))
        from .native import pick_files, pick_folder

        paths = pick_folder(self.services.window) if folder else pick_files(
            self.services.window, ("Bundle (*.bundle;*.bundle.backup)",), **({"directory": directory} if directory else {}))
        return {"paths": [str(path) for path in paths]}

    def analyze(self, paths: list[str], recursive: bool = False) -> str:
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
        with self._lock:
            return {"revision": self._revision, "rows": [deepcopy(c.entry) for c in self._latest.values()]}

    def _log_analysis(self, candidate: Candidate, phase: str) -> None:
        from .diagnostics import import_row
        detail = import_row(candidate.entry, getattr(self.services, "log_secrets", set()))
        logger.info("Batch import analysis (%s): %s", phase, json.dumps(detail, ensure_ascii=False, sort_keys=True))

    def assignment_catalog(self) -> dict:
        """Use cross-server metadata; absent assets remain absent in the picker."""
        families = []
        for family in self.services.catalog.assignment_families():
            variants = []
            for variant in family.variants:
                value = variant.to_json()
                value["fileId"] = value.pop("file_id")
                variants.append(value)
            families.append({"key": family.key, "base": family.names, "aliases": family.aliases,
                             "school": family.school, "variants": variants})
        return {"families": families}

    def assign_character(self, id: str, variant: str) -> str:
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
        return self.set_source(id, target.get("server") or "global", target.get("platform") or "windows",
                               assignment_only=True)

    def set_source(self, id: str, server: str, platform: str, *, assignment_only: bool = False) -> str:
        if not isinstance(id, str) or server not in mod_origin.SERVERS or platform not in mod_origin.PLATFORMS:
            raise ServiceError("bad_params", key="source")
        with self._lock:
            if id not in self._latest:
                raise ServiceError("bad_params", key="id")
            candidate = self._latest[id]
            self._revision += 1
            fresh = Candidate(dict(candidate.files), deepcopy(candidate.entry), candidate.error,
                              identity=candidate.identity, failure=candidate.failure,
                              source_override=candidate.source_override if assignment_only else
                              {"server": server, "platform": platform, "detected": False})
            library, pin, revision = self._library, self._pin, self._revision

        def analyze(log: Any, progress: Any) -> dict:
            if self.services.library is not library:
                raise ServiceError("folder_changed")
            if pin:
                self.services._check_pin(library, pin)
            identity = frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1)
                                 for name, path in fresh.files.items())
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
        return (library, pin.key if pin else None, tuple(pin.dirs) if pin else (),
                candidate.entry["id"], candidate.identity)

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
                key = json.dumps([candidate.provenance, snapshot, options, candidate.spine_version,
                                  str(self.services.settings.spine_options())], sort_keys=True)
                if key not in self._legacy_checks:
                    settings = ImportSettings(self.services.settings, options)
                    with staging.import_directory(library.root) as temporary:
                        checking = replace(candidate, entry=deepcopy(candidate.entry), not_transferred=[])
                        paths, _, _ = self._prepare(checking, settings, Path(temporary), lambda *args: None,
                                                    lambda *args: None)
                        mod_id = library.find_import_output(paths, {item[0] for item in snapshot}) if paths else None
                    originals = (candidate.originals if candidate.entry["kind"] == "other" else
                                 library.official_bundles(candidate.entry["core"], pin=self._pin))
                    if library.import_source_identity(candidate.files, candidate.entry["target"],
                                                      originals) != candidate.provenance:
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
        """Compose a source with official omitted parts, or current parts when no official copy remains."""
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
        try:
            originals = library.preview_complements(core_name, list(candidate.files), pin=pin,
                                                   files=candidate.files) if pin else []
        except ServiceError as error:
            if error.code not in ("game_file_not_found", "original_unavailable", "original_invalid"):
                raise
            originals = []  # A self-contained upload can preview without recoverable official parts.
        bundles = originals + paths
        with self._lock:
            if key not in self._previews:
                raise KeyError(key)
        return core_name, bundles

    def _collect(self, paths: list[str], recursive: bool, library: Any, *,
                 previous: list[Candidate] = ()) -> tuple[list[Candidate], int]:
        selected, seen, skipped = [], set(), 0

        def take(path: Path) -> None:
            nonlocal skipped
            path = path.expanduser().resolve()
            devmode.check_read(path)
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
            kind = kind_of(parsed.core, parsed.category) or "other"
            variant, error, failure = None, None, None
            try:
                if self._hashes.get(path).trailing is None:
                    raise ServiceError("bundle_invalid", file=path)
                if not library.supports_kind(kind):
                    error = "unsupported"
                    failure = ServiceError("mod_unsupported", file=path, kind=kind).to_json()
                    resolve = getattr(library.catalog, "variant_for_asset_core", library.catalog.variant_for_core)
                    found = resolve(parsed.core.lower())
                    variant = found.key if found else None
                else:
                    name, variant, kind = library.identify_import(path, name)
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
            core = library.import_core(parse_filename(name).core, kind, self._pin)
            inspected.append((path, name, kind, variant, core, error, failure))
        # Model sets share the prefab slot key, including version splits across their physical cores.
        # Folders still separate alternative mods of the same release.
        versioned = [item for item in inspected if item[2] != "other"]
        versions = library.import_versions([((path.parent, core, kind, variant), name)
                                      for path, name, kind, variant, core, _, _ in versioned], self._pin)
        version_by_path = {item[0]: version for item, version in zip(versioned, versions)}
        active = [c for c in previous if set(c.files.values()) <= seen]
        owners = {path: c for c in active for path in c.files.values()}
        for path, name, kind, variant, core, error, failure in inspected:
            version = version_by_path.get(path, "")
            owner = owners.get(path)
            if owner is None and not error:
                try:
                    held = set(cab_names(path))
                except (OSError, ValueError):
                    held = set()
                matches = [c for c in active if held & c.missing_cabs or (
                    (core, kind, variant) == (c.entry["core"], c.entry["kind"], c.entry["variant"]) and (
                        all(version_by_path.get(p, "") == version for p in c.files.values())
                        if path.parent in {p.parent for p in c.files.values()} else c.entry["state"] == "incomplete"))]
                local = [c for c in matches if path.parent in {p.parent for p in c.files.values()}]
                if local:
                    matches = local
                # A repeated part or several possible owners is an alternative Mod, never a guessed merge.
                collisions = [c for c in matches if path.parent not in {p.parent for p in c.files.values()} and
                              any(integrity.replacement_key(name) == integrity.replacement_key(n)
                                  for n in c.files)]
                if len(matches) > 1 or collisions:
                    error = "mod_target_ambiguous"
                    failure = ServiceError(error, file=path,
                        candidates=sorted(str(p) for c in matches for p in c.files.values())).to_json()
                elif matches:
                    owner = matches[0]
            if owner is not None:
                core, kind, variant = owner.entry["core"], owner.entry["kind"], owner.entry["variant"]
                key = (owner.entry["id"], core, "", kind, variant)
            else:
                key = (path.parent, "", "", kind, None) if kind == "other" else (
                    path.parent, core, version, kind, variant)
            groups.setdefault(key, []).append((path, name, error, failure))
        candidates = []
        for (_, core_name, _, kind, variant), items in groups.items():
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
            entry = {"id": hashlib.sha256("\0".join(map(str, sources)).encode()).hexdigest(),
                     "paths": list(map(str, sources)), "variant": variant, "core": core_name or None,
                     "kind": kind, "files": file_list, "size": sum(f["size"] for f in file_list),
                     "state": "blocked", "reason": error, "libraryId": None, "preview": None,
                     "source": None, "target": mod_origin.target_of(self.services.settings, library.current_pin())}
            candidate = Candidate(files, entry, error, failure=failure)
            try:
                candidate.identity = frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1)
                                               for name, path in files.items())
            except OSError:
                pass  # The unreadable row above still reports all of its source paths.
            candidates.append(candidate)
        return candidates, skipped

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

    def _join_referenced(self, candidates: list[Candidate], headers: dict[Path, set[str]],
                         library: Any, pin: Any, log: Any) -> list[Candidate]:
        """A selected supplier of a new CAB belongs to its dependent upload, regardless of its folder."""
        while True:
            links = {id(c): [other for other in candidates if other is not c and
                            any(headers.get(path, set()) & c.missing_cabs for path in other.files.values())]
                     for c in candidates if c.missing_cabs}
            changed = False
            for candidate in candidates:
                providers = links.get(id(candidate), [])
                if not providers:
                    continue
                group = [candidate, *providers]
                parts = [integrity.replacement_key(name) for c in group for name in c.files]
                shared = any(other is not candidate and any(provider in links.get(id(other), [])
                             for provider in providers) for other in candidates)
                if shared or len(parts) != len(set(parts)):
                    candidate.entry.update(state="blocked", reason="mod_target_ambiguous",
                        params={"candidates": sorted(str(p) for c in group for p in c.files.values())})
                    candidate.missing_cabs.clear()
                    continue
                files = {name: path for c in group for name, path in c.files.items()}
                paths = sorted(files.values())
                entry = {**candidate.entry, "id": hashlib.sha256("\0".join(map(str, paths)).encode()).hexdigest(),
                         "paths": list(map(str, paths)), "files": [
                             {"path": str(p), "size": p.stat().st_size, "target": None} for p in paths],
                         "size": sum(p.stat().st_size for p in paths), "preview": None}
                merged = Candidate(files, entry, source_override=candidate.source_override)
                self._judge(merged, library, pin, log)
                self._recognize_legacy(merged, library)
                self._restore_completed(merged, library, pin)
                candidates = [merged if c is candidate else c for c in candidates if c not in providers]
                changed = True
                break
            if not changed:
                return candidates

    def _missing(self, candidate: Candidate, library: Any, headers: dict[Path, set[str]],
                 selected_paths: set[Path] | None = None) -> None:
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
            kind = kind_of(parsed.core, parsed.category) or "other"
            resolve = getattr(library.catalog, "variant_for_asset_core", library.catalog.variant_for_core)
            variant = resolve(parsed.core.lower()) if kind in ("spr", "lobby") else None
            slot_matches = (library.import_core(parsed.core, kind, self._pin) == entry["core"] or
                            variant is not None and variant.key == entry["variant"])
            same_slot = (path.parent in folders and kind != "other" and kind == entry["kind"] and
                         slot_matches and path not in (selected_paths or selected) and
                         integrity.replacement_key(path.name) not in parts)
            if same_slot or held & candidate.missing_cabs:
                found.append({"path": str(path), "name": path.name, "size": path.stat().st_size})
        if found or candidate.missing_cabs:
            entry.update(state="incomplete", reason=None, missing=missing_detail(candidate.missing_cabs, found))
            entry.pop("error", None)
            entry.pop("params", None)

    def _judge(self, candidate: Candidate, library: Any, pin: Any, log: Any = no_log,
               *, asset_types: set[str] | None = None, strategy: str | None = None) -> None:
        entry = candidate.entry
        entry.pop("params", None)
        entry.pop("error", None)
        entry.pop("missing", None)
        entry.pop("bundles", None)
        candidate.missing_cabs = set()
        entry.update(state="blocked", reason=candidate.error, libraryId=None, source=None,
                     target=mod_origin.target_of(self.services.settings, pin))
        candidate.targets, candidate.originals, candidate.spine_version, candidate.conversion = [], [], None, False
        candidate.rebuild = False
        candidate.direct = {}
        candidate.provenance = None
        inspection = integrity.Inspection(asset_types if asset_types is not None else set(self.options()["types"]),
                                          header_only_missing=True)
        try:
            if candidate.error in ("mod_target_unknown", "mod_target_ambiguous") or entry.get("assignment"):
                existing = library.find_import_output(list(candidate.files.values()))
                saved = next((mod for mod in library.mods() if mod.id == existing), None) if existing else None
                if saved:
                    if entry.get("assignment") and entry["assignment"] != saved.target:
                        raise ServiceError("mod_target_ambiguous", file=source_file(candidate))
                    entry.update(state="library", reason=None, libraryId=saved.id, variant=saved.target,
                                 kind=saved.kind, core=entry["core"] or saved.core)
                    return
            if entry.get("assignment"):
                if candidate.error not in (None, "mod_target_unknown", "mod_target_ambiguous", "unsupported"):
                    raise blocked_error(candidate)
                variant = self.services.catalog.assignment_variant(entry["assignment"])
                kind = manual_identity.validate(candidate.files)
                candidate.identity = frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1)
                                               for name, path in candidate.files.items())
                entry.update(variant=variant.key, kind=kind, state="ready", reason=None, libraryOnly=True,
                             core=entry["core"] or variant.file_id)
                return
            if candidate.failure:
                raise ServiceError(candidate.failure["code"], **candidate.failure["params"])
            if candidate.error:
                raise ServiceError(candidate.error)
            for path in candidate.files.values():
                inspection.check(path)
            report = library.inspect_import(candidate.files, pin=pin, conversion=True, inspection=inspection)
            if report.get("recognized"):
                entry.update(state="library" if report["libraryId"] else "installed", reason=None,
                             libraryId=report["libraryId"])
                return
            if report.get("direct"):
                candidate.identity = report["identity"]
                candidate.direct = report["direct"]
                candidate.targets, candidate.originals = report["targets"], report["originals"]
                entry["target"] = mod_origin.target_of(self.services.settings, pin, report["officialContents"])
                source = report["source"]
                if candidate.source_override and any(candidate.source_override[key] != source[key]
                                                     for key in ("server", "platform")):
                    raise ServiceError("source_conflict")
                entry["source"] = candidate.source_override or source
                official, incoming, remappable = copy_compatibility(report["compatibility"], report["sourceContents"])
                if report["compatibility"]["unavailable"]:
                    official = {key: values if values != {None} else {incoming[key]}
                                for key, values in official.items()}
                mismatch = self._check_spine(candidate, official, incoming, remappable)
                for file in entry["files"]:
                    file["target"] = next(target.name for target, source in candidate.direct.items()
                                          if str(source) == file["path"])
                if mismatch:
                    if report["compatibility"]["unavailable"]:
                        library.official_bundles(entry["core"], pin=pin)
                    candidate.direct = {}
                entry.update(state="library" if report["libraryId"] else "installed" if report["installed"] else
                             "update" if mismatch else "ready", reason=None, libraryId=report["libraryId"])
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
            if candidate.source_override and any(value and candidate.source_override[key] != value
                                                  for key, value in evidence.items()):
                raise ServiceError("source_conflict")

            def conversion(source: dict) -> bool:
                return bool(any(source[key] != entry["target"][key] for key in ("server", "platform"))
                            or (official_contents.platforms and selected.platforms != official_contents.platforms)
                            or mod_origin.platform_of(selected) is None)

            if len({p.name for p in live}) != len(live):
                raise ServiceError("game_file_conflict", file=source_file(candidate))
            replacements = library._replacements(candidate.files, live, pin, inspection)
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
            originals = report["originals"] if entry["kind"] == "other" else library.official_bundles(entry["core"], pin=pin)
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
            targets = set(replacements)
            older = {name: path for name, path in candidate.files.items() if path not in replacements.values()}
            if older:
                if entry["kind"] == "other":
                    targets.update(library._other_targets(older, pin))
                else:
                    # Preserve the update path for pre-split bundles and older releases.
                    latest = {}
                    for path in live:
                        parsed = parse_filename(path.name)
                        latest[parsed.prefix] = max(latest.get(parsed.prefix, ""), parsed.date)
                    matched, _ = _asset_match(list(older.values()), [baseline[p] for p in live
                        if parse_filename(p.name).date == latest[parse_filename(p.name).prefix]],
                        log, keys=inspection.keys)
                    targets.update(p for p in live if baseline[p] in matched)
            targets = [path for path in live if path in targets]
            inferred = entry["source"] is None
            choices = (mod_origin.candidates(names, selected, official_contents, entry["target"]) if inferred
                       else [entry["source"]])
            matched_targets, last_failure = None, None
            for choice in choices:
                converting = conversion(choice)
                failure = None
                chosen = targets
                if choice["server"] != entry["target"]["server"] and not all(
                        any(part_of(name) == part_of(path.name) for path in live) for name in names):
                    failure = "conversion_names"
                if not failure and (converting or inferred or rebuild):
                    if matched_targets is None:
                        matches, _ = _asset_match(sources, [baseline[p] for p in targets], log, keys=inspection.keys)
                        matched_targets = [p for p in targets if baseline[p] in matches]
                    chosen = matched_targets
                if not chosen:
                    failure = failure or ("no_matching_assets" if converting or inferred or rebuild else "game_file_not_found")
                mismatch = False
                if not failure and (selected.skeletons or compatibility.get("remappable")):
                    if exact and not converting and not rebuild:
                        # The ready path copies whole bundles, ignoring update filters and matching keys.
                        official, incoming, remappable = copy_compatibility(compatibility, selected)
                    else:
                        official, incoming, remappable = transfer_compatibility(inspection, sources,
                            [baseline[path] for path in chosen], originals,
                            "name_type" if converting or rebuild else strategy or self.options()["strategy"])
                    try:
                        mismatch = self._check_spine(candidate, official, incoming, remappable)
                    except ServiceError as error:
                        failure = error.code
                if failure:
                    if inferred:
                        last_failure = failure
                        continue
                    raise ServiceError(failure)
                if rebuild or inferred or (candidate.source_override or {}).get("detected"):
                    # Validate selected inputs against current counterparts using the execution reader.
                    try:
                        counterparts = tuple(baseline[path] for path in chosen)
                        required = set().union(*(inspection.readable_keys(path, targets=counterparts) for path in sources))
                        available = set().union(*(inspection.keys(baseline[path]) for path in chosen))
                        if not required or not required <= available:
                            raise ServiceError("no_matching_assets")
                    except (ServiceError, OSError, ValueError):
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
                entry.update(state="convert" if converting else "ready" if exact and not mismatch and not rebuild else "update",
                             reason=None)
                return
            raise ServiceError(last_failure or "source_unknown")
        except ServiceError as error:
            candidate.missing_cabs = set(inspection.missing_cabs)
            if evidence := bundle_evidence(candidate, inspection):
                entry["bundles"] = evidence
            if error.code == "mod_parts_missing" and candidate.missing_cabs:
                entry.update(state="incomplete", reason=None, missing=missing_detail(candidate.missing_cabs, []))
                return
            entry.update(state="blocked", reason=blocked_reason(error),
                         error=failure_detail(error, candidate.files.values(), log,
                                              secrets=getattr(self.services, "log_secrets", set())))
            if error.params:
                entry["params"] = error.to_json()["params"]
        except (OSError, ValueError) as error:
            if evidence := bundle_evidence(candidate, inspection):
                entry["bundles"] = evidence
            detail = ServiceError("bundle_invalid", file=source_file(candidate), error=str(error))
            entry.update(state="blocked", reason="unreadable",
                         error=failure_detail(detail, candidate.files.values(), log,
                                              secrets=getattr(self.services, "log_secrets", set())))

    def _check_spine(self, candidate: Candidate, official: dict, incoming: dict, remappable: bool) -> bool:
        versions = {version for values in official.values() for version in values if version}
        series = {tuple(version.split(".")[:2]) for version in versions}
        candidate.spine_version = (max(versions, key=lambda version: tuple(map(int, version.split("."))))
                                   if len(series) == 1 else None)
        unknown = any(not version for version in incoming.values())
        mismatch = any(version and any(not target or version.split(".")[:2] != target.split(".")[:2]
                                       for target in official[key]) for key, version in incoming.items())
        if remappable:
            raise ServiceError("conflict")
        if unknown or (mismatch and (candidate.spine_version is None or
                                    not self.services.settings.spine_options().is_valid())):
            raise ServiceError("spine_version")
        return mismatch

    @staticmethod
    def _require_importable(candidate: Candidate) -> None:
        if candidate.entry["state"] not in IMPORTABLE:
            detail = candidate.entry.get("error") or {
                "code": candidate.entry.get("reason") or "entry_changed", "params": candidate.entry.get("params", {})}
            raise ServiceError(detail["code"], **detail["params"])

    def run(self, ids: list[str], options: dict, revision: int) -> str:
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
                    progress({"done": completed[0], "completed": completed[0], "remaining": total - completed[0],
                              "total": total, "index": positions[candidate.entry["id"]],
                              "current": candidate.entry["id"], "phase": phase,
                              "step": "import.step." + (step or phase)})

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
                            current = frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1)
                                                for name, path in candidate.files.items())
                            if current != candidate.identity:
                                raise ServiceError("entry_changed")
                            fresh = Candidate(dict(candidate.files), deepcopy(candidate.entry), candidate.error,
                                              failure=deepcopy(candidate.failure),
                                              source_override=deepcopy(candidate.source_override))
                            fresh.entry["files"] = [dict(f) for f in candidate.entry["files"]]
                            report(candidate, "convert" if candidate.conversion else "update"
                                   if candidate.entry["state"] == "update" else "add", "check")
                            self._judge(fresh, library, pin, log, asset_types=settings.asset_types(),
                                        strategy=settings.options["strategy"])
                            analyzed[candidate.entry["id"]] = fresh
                            self._log_analysis(fresh, "execute")
                            if fresh.entry["state"] in ("library", "installed", "done"):
                                futures[candidate.entry["id"]] = fresh
                                continue
                            self._require_importable(fresh)
                            self._snapshot_originals(fresh, Path(temporary), originals)
                            futures[candidate.entry["id"]] = workers.submit(
                                self._prepare, fresh, settings, Path(temporary), log, report)
                        except (OSError, ServiceError) as error:
                            futures[candidate.entry["id"]] = error
                    for candidate in chosen:
                        item = {"id": candidate.entry["id"], "outcome": "skipped", "reason": None,
                                "libraryId": candidate.entry["libraryId"], "configured": False, "applied": False}
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
                                    current = frozenset((core_of(name), part_of(name), self._hashes.get(path).sha1)
                                                        for name, path in fresh.files.items())
                                    if current != fresh.identity:
                                        raise ServiceError("entry_changed")
                                if fresh.not_transferred:
                                    item["notTransferred"] = fresh.not_transferred
                                    logger.warning("%s: %s", ", ".join(sorted(fresh.files)),
                                        t("webui.import.notTransferred", names=", ".join(fresh.not_transferred)))
                                if paths is None:
                                    item.update(outcome="unchanged", libraryId=fresh.entry["libraryId"])
                                    mod = None
                                else:
                                    report(candidate, "add")
                                    with self._lock:
                                        check_current()
                                        added = (library.add_assigned_files(fresh.files, fresh.entry["assignment"],
                                                 fresh.entry["kind"], pin=pin) if fresh.entry.get("assignment") else
                                                 library.add_files(fresh.entry["kind"], paths, pin=pin, direct=bool(fresh.direct)))
                                    entries = [*added, *added.duplicates]
                                    if len(entries) != 1:
                                        raise ServiceError("mod_unsupported", file=source_file(candidate))
                                    mod = entries[0]
                                    item.update(libraryId=mod.id, outcome=("updated" if updated else "imported")
                                                if added else "unchanged")
                                    result.added.extend(added)
                                    if fresh.provenance:
                                        library.remember_import_source(mod.id, fresh.provenance)
                                if mod is not None:
                                    result.cores.update(core_of(name) for name in fresh.files)
                                    result.kinds.add(mod.kind)
                                    if options["previewAfterImport"] and pin and mod.kind in ("spr", "lobby", "model"):
                                        self.services.state.set_preview_source(pin.key, mod.target, mod.kind, f"mod:{mod.id}")
                                        item["previewSelected"] = True
                                    if options["enableAfterImport"] and fresh.entry.get("libraryOnly"):
                                        item["applyUnavailable"] = "no_target"
                                    if options["enableAfterImport"] and not fresh.entry.get("libraryOnly") and item["outcome"] != "unchanged":
                                        phase = "apply"
                                        report(candidate, phase)
                                        with self._lock:
                                            check_current()
                                            self._require_importable(candidate)
                                            self._require_importable(fresh)
                                            applied = library.apply_import(mod.target, f"mod:{mod.id}", kind=mod.kind,
                                                pin=pin, settings=settings, backup=options["backup"], log=log)
                                        # A successful apply configures the source, including deferred applies.
                                        item["configured"] = True
                                        item["applied"] = not applied.deferred
                                        if applied.kept_current:
                                            item["kept_current"] = True
                                        if applied.incomplete:
                                            item["incomplete"] = True
                                    self._complete(candidate, library, pin, mod.id)
                        except ServiceError as error:
                            item.update(outcome="failed", reason=error.code,
                                        error=failure_detail(error, candidate.files.values(), log,
                                                             secrets=getattr(self.services, "log_secrets", set())))
                            if error.params:
                                item["params"] = error.to_json()["params"]
                        except OSError as error:
                            detail = ServiceError("library_io_error", file=error.filename or source_file(candidate),
                                                  error=str(error))
                            item.update(outcome="failed", reason=detail.code, params=detail.params,
                                        error=failure_detail(detail, candidate.files.values(), log,
                                                             secrets=getattr(self.services, "log_secrets", set())))
                        except Exception as error:
                            detail = ServiceError("internal", error=str(error))
                            item.update(outcome="failed", reason=detail.code, params=detail.params,
                                        error=failure_detail(detail, candidate.files.values(), log,
                                                             secrets=getattr(self.services, "log_secrets", set())))
                        finally:
                            if phase == "apply" and item.get("libraryId") and item.get("reason"):
                                # The mod reached the library before its install failed: the row says so.
                                self._stored(candidate, item["libraryId"])
                                # A failed install can still have saved the choice. Report both facts.
                                try:
                                    if mod.kind == "other":
                                        item["configured"] = any(row["id"] == mod.id and row["selected"]
                                                                 for row in library.other_mods(pin))
                                    else:
                                        item["configured"] = library.configured(mod.target, pin).get(mod.kind) == f"mod:{mod.id}"
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

        writes = ("game" if options["enableAfterImport"] else "library") if any(
            candidate.entry["state"] in IMPORTABLE for candidate in chosen) else None
        return self.services.submit("import.run", run, writes=writes,
                                    library=library)

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

    def _prepare(self, candidate: Candidate, settings: ImportSettings, temporary: Path,
                 log: Any, progress: Any) -> tuple[list[Path] | None, bool, Candidate]:
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
                    fix = settings.crc_needed(baselines.get(target.name, target)) and CRCUtils.compute_crc32(data) != expected
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
            sources = [path for name, path in candidate.files.items()
                       if not prefix or parse_filename(name).prefix == prefix]
            updated = core.process_mod_update(source_paths=sources, target_paths=group,
                output_dir=output, asset_types_to_replace=settings.asset_types(),
                save_options=settings.save_options(settings.crc_needed(group[0])), spine_options=spine,
                skip_unchanged=settings.options["skipUnchanged"],
                match_strategy="name_type" if candidate.conversion or candidate.rebuild else settings.options["strategy"],
                anim_check=settings.anim_check_options(), log=log,
                on_step=lambda step: progress(candidate, phase, step))
            if not updated.success:
                if updated.error:
                    raise updated.error
                raise ServiceError("conversion_failed" if candidate.conversion else "mod_update_failed")
            candidate.not_transferred = sorted(set(candidate.not_transferred) | set(updated.not_transferred))
            if updated.message != "all_targets_unchanged":
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
