"""Target-validated Mod ZIP exports, published only after every member is complete."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .. import atlas_remap, devmode, mod_origin
from ..errors import ServiceError
from ..openfiles import temporary_directory
from ..library import ba_mod, integrity, mod_progress
from ..library.judge import core_of, kind_of_file
from ..library.storage import sync_directory
from .batch_import import BatchImport, Candidate, ImportSettings


class _ExportLibrary:
    """Use import validation without treating an existing library entry as completed work."""

    def __init__(self, library: Any):
        self.library = library

    def __getattr__(self, name: str) -> Any:
        return getattr(self.library, name)

    def inspect_import(self, *args: Any, **kwargs: Any) -> dict:
        report = dict(self.library.inspect_import(*args, **kwargs))
        if report.get("recognized"):
            raise ServiceError("original_unavailable")
        report.update(libraryId=None, installed=False)
        return report

    def find_import_source(self, provenance: dict) -> None:
        return None


class _Preparation(BatchImport):
    def options(self) -> dict:
        # Export all transferable Mod content; import filters and output preferences do not apply.
        return {
            **self.defaults(),
            "types": ["ALL"],
            "output": None,
            "skipUnchanged": False,
            "crc": "auto",
            "strategy": "name_type",
        }


def _safe_name(value: str) -> str:
    """A portable component with stable identity even for colliding labels or reserved names."""
    label = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_")[:48] or "mod"
    return f"mod-{label}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _member_name(path: Path) -> str:
    name = path.name
    if (
        not name
        or name in (".", "..")
        or any(c in name for c in '\\/:<>"|?*')
        or any(ord(c) < 32 for c in name)
        or name.endswith((".", " "))
    ):
        raise ServiceError("mod_unsupported", file=name)
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ServiceError("mod_unsupported", file=name)
    return name


def _pins(library: Any) -> list:
    pins = [library.current_pin(), *library._named_pins()]
    return list({pin.key: pin for pin in reversed(pins) if pin}.values())


def _pin_target(library: Any, settings: Any, pin: Any) -> dict:
    """Read serialized platform metadata when directory names do not identify the install."""
    if pin:
        inspection = integrity.Inspection()
        paths = (path for group in library._index(pin).values() for path in group)
        for _, path in zip(range(8), paths):
            try:
                official = inspection.metadata(library._official_for(path, pin))
                inspection.validate()
            except (OSError, ServiceError):
                continue
            target = mod_origin.target_of(settings, pin, official)
            if target["platform"]:
                return target
    return mod_origin.target_of(settings, pin)


def _target_pin(library: Any, settings: Any, server: str, platform: str) -> Any:
    for pin in _pins(library):
        if _pin_target(library, settings, pin) == {"server": server, "platform": platform}:
            return pin
    raise ServiceError("export_target_unavailable")


def _sources(library: Any, scope: str, target: str | None) -> tuple[list[str], list[dict]]:
    assignments = []
    if scope == "single":
        if not isinstance(target, str) or not target.startswith(("mod:", "variant:")):
            raise ServiceError("bad_params", key="target")
        return [target], assignments
    if scope == "library":
        return [f"mod:{key}" for key in sorted(library._records)], assignments
    if scope != "applied":
        raise ServiceError("bad_params", key="scope")
    profiles = library.profiles.read() or {}
    for folder in profiles.get("folders", {}).values():
        for core, entry in sorted(folder.get("configured", {}).items()):
            source = entry.get("source", "")
            if source.startswith("mod:"):
                assignments.append(
                    {
                        "server": folder.get("server", "global"),
                        "core": core,
                        "variant": entry.get("variant"),
                        "kind": entry.get("kind"),
                        "source": source,
                    }
                )
    return sorted({entry["source"] for entry in assignments}), assignments


def _prepare(
    services: Any,
    library: Any,
    source: str,
    pin: Any,
    target: dict,
    work: Path,
    log: Any,
    progress: Any,
    kind: str | None = None,
) -> tuple[list[Path], dict]:
    if source.startswith("variant:"):
        variant = source[8:]
        targets = library._game_files(variant, kind, pin)
        paths = [library._official_for(path, pin) for path in targets]
        if not paths:
            raise ServiceError("game_file_not_found", target=variant)
        contents = integrity.Contents()
        for path in paths:
            contents.merge(integrity.inspect(path))
        if mod_origin.target_of(services.settings, pin, contents) != target:
            raise ServiceError("export_target_unavailable")
        directory = work / _safe_name(source) / "official"
        directory.mkdir(parents=True)
        prepared = []
        for live, original in zip(targets, paths):
            output = directory / _member_name(live)
            shutil.copy2(original, output)
            prepared.append(output)
        return prepared, {"source": source, "variant": variant, "kind": kind}
    record = library._record(source[4:])
    paths = library._paths(record)
    entry = {
        "id": _safe_name(source),
        "core": core_of(paths[0].name),
        "kind": record["kind"],
        "variant": library._target_of(record),
        "files": [{"path": str(path)} for path in paths],
    }
    candidate = Candidate({path.name: path for path in paths}, entry)
    preparation = _Preparation(services)
    preparation._judge(candidate, _ExportLibrary(library), pin, log, asset_types={"ALL"}, strategy="name_type")
    preparation._require_importable(candidate)
    if candidate.entry["target"] != target:
        raise ServiceError("export_target_unavailable")
    prepared, _, _ = preparation._prepare(
        candidate,
        ImportSettings(services.settings, preparation.options()),
        work,
        log,
        lambda _c, _phase, step: progress({"step": f"import.step.{step}"}),
    )
    if not prepared:
        raise ServiceError("mod_update_failed")
    # Recheck complete prepared bundles against the requested install, including dependencies.
    library._check_group(
        {path.name: path for path in prepared}, pin, compatibility=False, variant_key=library._target_of(record)
    )
    return prepared, {
        "source": source,
        "variant": library._target_of(record),
        "kind": record["kind"],
        "name": library._label(record),
    }


def _member_path(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _contents(path: Path) -> integrity.Contents:
    """The text assets and texture names of a bundle, as the package manifest needs them."""
    return integrity.inspect(path, allow_unknown=True)


def _generated(library: Any, record: dict | None) -> bool:
    """Whether a library mod is made by the App (an Optimize Physics copy, a lobby camera, a roster photograph): its
    changed files are ``generated`` in the package manifest."""
    from ..library import lobby_camera, portrait_edit

    return bool(record) and bool(
        library._optimized_from(record) or lobby_camera.meta_of(record) or record.get(portrait_edit.TAG)
    )


class _Package:
    """What one exported mod's ``ba-mod.json`` says, worked out from the prepared files and the target install's
    verified official copies: each file's role, the official bundles holding the .skel and .atlas files the mod lacks
    (a Spine package carries them), and per skeleton which parts are the mod's. Best effort: a part that cannot be
    read is left out of ``skeletons`` and a file without a verified official copy counts as the mod's."""

    def __init__(
        self, library: Any, pin: Any, live: dict[str, Path], record: dict | None, paths: list[Path], kind: str | None
    ):
        from ..library import lobby_camera

        self.library, self.pin, self.live = library, pin, live
        camera = lobby_camera.meta_of(record)
        names = [path.name for path in paths]
        library_kind = record["kind"] if record else (kind or kind_of_file(names[0]) or "other")
        self.kind = "lobby" if camera else ba_mod.kind_of(library_kind, names)
        self.core = camera["core"] if camera else core_of(names[0])
        self.generated = _generated(library, record)
        self.camera = camera is not None
        self._official: dict[str, Path | None] = {}
        self._read: dict[Path, integrity.Contents] = {}

    def contents(self, path: Path) -> integrity.Contents:
        """A bundle's text assets and texture names, read once per export of this mod."""
        if path not in self._read:
            self._read[path] = _contents(path)
        return self._read[path]

    def official(self, name: str) -> Path | None:
        """The verified official copy of the target install's file of this name, None when there is none."""
        if name not in self._official:
            live = self.live.get(name)
            try:
                self._official[name] = self.library._official_for(live, self.pin) if live else None
            except (OSError, ServiceError):
                self._official[name] = None
        return self._official[name]

    def role(self, path: Path, digest: str) -> str:
        official = self.official(path.name)
        try:
            if official is not None and ba_mod.sha256(official) == digest:
                return ba_mod.OFFICIAL
        except OSError:
            pass
        return ba_mod.GENERATED if self.generated else ba_mod.MOD

    def companions(self, paths: list[Path]) -> list[tuple[Path, str]]:
        """The official bundles of a Spine core that hold the .skel and .atlas files of the pages the mod paints and
        that the mod lacks, as (copy, member name): a Spine package carries them. A core with several scenes (Spine
        backgrounds) carries only the rigs of the scenes the mod paints; a lobby camera carries only its prefab."""
        if self.kind not in ba_mod.SPINE_KINDS or self.camera:
            return []
        names = {path.name for path in paths}
        try:
            parts = self.library._spine_parts(self.core, self.pin)
        except (OSError, ServiceError):
            return []
        try:
            painted: set[str] | None = {key for path in paths for key in self.contents(path).textures}
        except Exception:  # unreadable here: every rig of the core is carried
            painted = None
        replaced = {integrity.replacement_key(name) for name in names}
        result = []
        for part in parts:
            if part.name in names or integrity.replacement_key(part.name) in replaced:
                continue
            official = self.official(part.name)
            if official is None:
                continue
            try:
                contents = self.contents(official)
                pages = {
                    atlas_remap.texture_key(page)
                    for name, text in contents.atlases.items()
                    for page in atlas_remap.parse_atlas(text, name).pages
                }
            except Exception:  # an unreadable part is not carried; the package stays a plain mod for it
                continue
            if (contents.atlases or contents.skeletons) and (painted is None or pages & painted):
                result.append((official, part.name))
        return result

    def skeletons(self, files: list[tuple[Path, str, str]]) -> list[dict]:
        """Per skeleton of the package, whether its .atlas, .skel and textures are the mod's or official ones."""
        if self.kind not in ba_mod.SPINE_KINDS:
            return []
        try:
            mine = integrity.Contents()
            holders: dict[str, str] = {}
            for path, _name, role in files:
                contents = self.contents(path)
                mine.merge(contents)
                for key in contents.textures:
                    if holders.get(key) != ba_mod.MOD:
                        holders[key] = ba_mod.OFFICIAL if role == ba_mod.OFFICIAL else ba_mod.MOD
            official = integrity.Contents()
            for part in self.library._spine_parts(self.core, self.pin):
                copy = self.official(part.name)
                if copy is not None:
                    official.merge(self.contents(copy))
        except Exception:  # best effort: the manifest is still written, without skeletons
            return []
        atlas_of = atlas_remap.pair_atlases(mine.atlases, mine.skeletons)
        result = []
        for name in sorted(mine.skeletons):
            entry = {
                "name": ba_mod.skeleton_name(name),
                "skel": ba_mod.OFFICIAL if official.skeletons.get(name) == mine.skeletons[name] else ba_mod.MOD,
            }
            atlas = atlas_of.get(name)
            if atlas is not None:
                entry["atlas"] = ba_mod.OFFICIAL if official.atlases.get(atlas) == mine.atlases[atlas] else ba_mod.MOD
                try:
                    pages = {
                        atlas_remap.texture_key(page)
                        for page in atlas_remap.parse_atlas(mine.atlases[atlas], atlas).pages
                    }
                except (atlas_remap.RemapError, ValueError, UnicodeError):
                    pages = set()
                roles = {holders[key] for key in pages if key in holders}
                if roles:
                    entry["textures"] = ba_mod.MOD if ba_mod.MOD in roles else ba_mod.OFFICIAL
            result.append({key: entry[key] for key in ("name", "atlas", "skel", "textures") if key in entry})
        return result


def export_zip(
    services: Any,
    library: Any,
    pin: Any,
    *,
    scope: str,
    target: str | None,
    folder: Path,
    server: str,
    platform: str,
    kind: str | None,
    log: Any,
    progress: Any,
) -> dict:
    """Keep a snapshot locked until ZIP verification and atomic publication finish."""
    devmode.check_write(folder)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"mods-{scope}-{server}-{platform}-{uuid.uuid4().hex}.zip"
    temporary_zip = None
    with library._lock:
        library._refresh_state()
        sources, assignments = _sources(library, scope, target)
        if not sources:
            raise ServiceError("export_empty")
        manifest = {
            "version": 1,
            "scope": scope,
            "target": {"server": server, "platform": platform},
            "mods": [],
            "assignments": assignments,
        }
        live = {path.name: path for group in library._index(pin).values() for path in group} if pin else {}
        release = ba_mod.release_of(list(pin.dirs), server, platform) if pin else {}
        from ..updater import current_version

        created_by = {"tool": "BMPT", "version": current_version()}
        try:
            with tempfile.NamedTemporaryFile(prefix=".mod-export-", suffix=".tmp", dir=folder, delete=False) as stream:
                temporary_zip = Path(stream.name)
            with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "export-") as staging:
                with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    # the progress line: each mod as it starts, who it is and its step (mod_progress)
                    line = mod_progress.ItemProgress(progress, len(sources), "export")
                    for i, source in enumerate(sources, 1):
                        record = library._records.get(source[4:]) if source.startswith("mod:") else None
                        line.item(
                            i,
                            variant=library._target_of(record) if record else "",
                            kind=record["kind"] if record else "",
                            name=library._progress_name(record) if record else "",
                            core=core_of(record["files"][0]) if record and record.get("files") else "",
                            step=mod_progress.STEP_EXPORT,
                        )
                        paths, metadata = _prepare(
                            services,
                            library,
                            source,
                            pin,
                            manifest["target"],
                            Path(staging),
                            log,
                            lambda p: line.step(p["step"]),
                            kind,
                        )
                        line.done()
                        # one mod per archive sits at its root, beside its ba-mod.json; several get a folder each
                        directory = "" if scope == "single" else _safe_name(source)
                        names = [_member_name(path) for path in paths]
                        package = _Package(library, pin, live, record, paths, kind)
                        companions = package.companions(paths)
                        names += [name for _, name in companions]
                        sources_ = [*paths, *(path for path, _ in companions)]
                        keys = [unicodedata.normalize("NFC", name).casefold() for name in names]
                        if len(set(keys)) != len(keys):
                            raise ServiceError(
                                "game_file_conflict",
                                file=next(name for name, key in zip(names, keys) if keys.count(key) > 1),
                            )
                        members, described = [], []
                        for index, (path, name) in enumerate(zip(sources_, names)):
                            before = path.stat()
                            digest = _sha256(path)
                            archive.write(path, _member_path(directory, name))
                            role = ba_mod.OFFICIAL if index >= len(paths) else package.role(path, digest)
                            members.append({"name": name, "size": path.stat().st_size, "sha256": digest, "role": role})
                            described.append((path, name, role))
                            after = path.stat()
                            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                after.st_ino,
                                after.st_size,
                                after.st_mtime_ns,
                                after.st_ctime_ns,
                            ):
                                raise ServiceError("entry_changed")
                        document = ba_mod.document(
                            created_by=created_by,
                            kind=package.kind,
                            core=package.core,
                            server=server,
                            platform=platform,
                            release=release,
                            files=[
                                {"path": member["name"], "sha256": member["sha256"], "role": member["role"]}
                                for member in members
                            ],
                            skeletons=package.skeletons(described),
                        )
                        archive.writestr(_member_path(directory, ba_mod.FILE_NAME), ba_mod.dumps(document))
                        manifest["mods"].append({**metadata, "directory": directory, "files": members})
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(temporary_zip) as archive:
                if archive.testzip() is not None:
                    raise ServiceError("library_io_error", file=str(destination))
                for mod in manifest["mods"]:
                    for member in mod["files"]:
                        with archive.open(_member_path(mod["directory"], member["name"])) as stream:
                            if hashlib.file_digest(stream, "sha256").hexdigest() != member["sha256"]:
                                raise ServiceError("entry_changed")
            with temporary_zip.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary_zip, destination)
            sync_directory(folder)
        finally:
            if temporary_zip is not None:
                temporary_zip.unlink(missing_ok=True)
    return {"folder": str(folder), "archive": str(destination), "count": len(sources)}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def register_api(rpc: Any, services: Any) -> None:
    @rpc.method("export.options")
    def options() -> dict:
        library = services.library
        with library._lock:
            current = _pin_target(library, services.settings, library.current_pin())
            targets = [_pin_target(library, services.settings, pin) for pin in _pins(library)]
        return {
            "folder": str(Path(services.settings.get("output_dir")).expanduser().resolve()),
            **current,
            "targets": targets,
        }

    @rpc.method("export.start")
    def start(
        scope: str, folder: str, server: str, platform: str, target: str | None = None, kind: str | None = None
    ) -> str:
        if (
            scope not in ("single", "library", "applied")
            or server not in mod_origin.SERVERS
            or platform not in mod_origin.PLATFORMS
        ):
            raise ServiceError("bad_params", key="export")
        if not isinstance(folder, str) or not folder.strip() or kind not in (None, "spr", "lobby", "model", "other"):
            raise ServiceError("bad_params", key="folder")
        destination = Path(folder).expanduser().resolve()
        devmode.check_write(destination)
        library = services.library
        with library._lock:
            pin = _target_pin(library, services.settings, server, platform)
        return services.submit(
            "export",
            lambda log, progress: export_zip(
                services,
                library,
                pin,
                scope=scope,
                target=target,
                folder=destination,
                server=server,
                platform=platform,
                kind=kind,
                log=log,
                progress=progress,
            ),
        )
