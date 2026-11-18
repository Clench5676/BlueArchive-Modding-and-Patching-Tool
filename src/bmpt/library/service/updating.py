"""Updating mods for a new game version, extracting and comparing assets."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from UnityPy.helpers.ResourceReader import get_resource_data

from bmpt.core import devmode
from bmpt.engine import (
    AssetType,
    Bundle,
    LogFunc,
    core,
    is_safe_file_name,
    no_log,
    parse_filename,
    t,
)
from ...host.store_formats import read_runtime
from ...core.errors import ServiceError
from ...core.filelock import file_lock
from ...core.logging_utils import log_failure
from ...core.openfiles import temporary_directory
from .. import fingerprint
from ..rebuild import RebuildResult, rebuild
from ..judge import (
    core_of,
    now,
)
from ..records import (
    AssetDifference,
    CompareResult,
    Pin,
    UpdateResult,
)
from ..storage import (
    atomic_copy,
    atomic_json,
    sync_directory,
    verify_file,
)
from .base import _item_fraction, _mod_writer, _serialized


logger = logging.getLogger(__package__)


class UpdatingMixin:
    """Updating mods for a new game version, extracting and comparing assets."""

    @_serialized
    def update(
        self,
        mod_id: str,
        log: LogFunc = no_log,
        progress: Callable[[dict], None] | None = None,
        pin: Pin | None = None,
        *,
        fraction: Callable[[float], None] | None = None,
    ) -> UpdateResult:
        """Rebuild a mod atomically against current official bundles and retain its previous files.

        Discard its cached builds and mark configured entries updated while game
        files retain the old build. An unchanged update adds no markers.

        Args:
            mod_id: Stored mod ID whose assets are rebuilt in place, including Optimize Physics copies.
            log: Receives user-facing operation messages.
            progress: Receives per-target phase dictionaries; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
            fraction: Optimize Physics build fraction callback overriding progress for that build;
                None derives one from progress.

        Returns:
            Updated mod, current files, previous files, unchanged status, and any remaining official rig gains.
        """
        self._check_recovery()  # refused before the rebuild when a recovery has failed or the state cannot be saved
        self._check_writable()
        pin = self._current(pin)
        record = self._record(mod_id)
        if self._optimized_from(record):
            return self._rebuild_optimized(record, log, pin, fraction or _item_fraction(progress, build=1.0))
        paths = self._paths(record)
        built_from = {path.name: self._hashes.get(path).sha1 for path in paths}
        painted = (self._read_built_against(record) or {}).get("painted")  # the textures move over as they are
        targets = self._plan_targets(self._plan(record, log, pin))
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "update-") as temporary:
            output = Path(temporary)
            generated = []
            all_unchanged = True
            no_match: RebuildResult | None = None  # a part's rebuild that took nothing, while no part took any
            matched = False
            for i, target in enumerate(targets, 1):

                def step(key: str) -> None:
                    if progress:
                        progress({"current": i, "total": len(targets), "step": f"import.step.{key}"})

                step("check")
                result = rebuild(
                    paths,
                    [self._official_for(target, pin)],
                    output,
                    self.settings.asset_types(),
                    self.settings.save_options(self.settings.crc_needed(target)),
                    self.settings.spine_options(),
                    skip_unchanged=True,
                    match_strategy="cont_name_type",
                    anim_check=self.settings.anim_check_options(),
                    log=log,
                    on_step=step,
                )
                if result.no_match:
                    # A part that takes nothing from the mod (one of materials only, or one the mod carries none
                    # of) keeps the official file: the copy below.
                    log(f"  ⏭️ {t('log.mod_update.target_unchanged', name=target.name, count=0)}")
                    no_match = no_match or result
                    continue
                if not result.success:
                    raise ServiceError("mod_update_failed", id=mod_id, reason=result.message)
                matched = True
                if not result.all_unchanged:
                    all_unchanged = False
                generated.extend(pair.output for pair in result.file_pairs)
            if no_match and not matched:  # no part took anything from the mod
                log(f"❌ {no_match.message}")
                raise ServiceError("mod_update_failed", id=mod_id, reason=no_match.message)
            if all_unchanged or (
                len(generated) == len(paths)
                and all(
                    new.name == old.name and self._hashes.get(new).sha1 == self._hashes.get(old).sha1
                    for new, old in zip(sorted(generated), sorted(paths))
                )
            ):
                # the mod is what a rebuild on these official parts gives: they are its baseline, also in place of
                # one of earlier parts whose change held it (an update ends the hold)
                if self._read_built_against(record) is None or self._impact(record, pin) != fingerprint.UNCHANGED:
                    self._write_built_against(
                        record, self._built_against({path.name: path for path in paths}, pin, "update", painted=painted)
                    )
                return UpdateResult(
                    self._entry(record),
                    paths,
                    [],
                    unchanged=True,
                    rig=self._rig_gains({path.name: path for path in paths}, pin),
                )
            # Include unchanged parts when a rebuild splits a mod across bundles.
            names = {path.name for path in generated}
            for target in targets:
                if target.name not in names:
                    atomic_copy(self._official_for(target, pin), output / target.name)
                    generated.append(output / target.name)
            built_against = self._built_against({path.name: path for path in generated}, pin, "update", painted=painted)
            rig = self._rig_gains({path.name: path for path in generated}, pin)
            record, previous = self._publish_update(mod_id, built_from, generated)
            # after the commit: a fingerprint of the files before stays theirs if the update is rolled back
            self._write_built_against(record, built_against)
        return UpdateResult(self._entry(record), self._paths(record), previous, rig=rig)

    def _rebuild_optimized(
        self, record: dict[str, Any], log: LogFunc, pin: Pin, fraction: Callable[[float], None] | None = None
    ) -> UpdateResult:
        """Update an Optimize Physics copy.

        Its costume is built again with the physics rules under the current costume rule, published in place of its
        files (``_publish_update``: its id, name and links stay), the rule recorded after. Refused like Optimize Physics
        when the pair's verdict refuses it; ``mod_update_failed`` when no physics rule improves the costume any more.
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        source = self._optimized_from(record)
        alt_id = self._target_of(record)
        signature, targets = self._costume_signature(alt_id, source[4:], pin)
        verdict = self._verdicts().get(signature)
        if not self._costume_allowed(verdict):
            raise ServiceError("source_unsupported", source=verdict.get("reason") or source)
        paths = self._paths(record)
        built_from = {path.name: self._hashes.get(path).sha1 for path in paths}
        report: dict[str, Any] = {}
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "update-") as temporary:
            replacements = self._stage_costume(
                source[4:], targets, Path(temporary) / "chibi", log, pin, report=report, physics=True, fraction=fraction
            )
            if not report.get("physics"):
                raise ServiceError("mod_update_failed", id=record["id"])
            generated = [path for path in replacements.values() if path.parent.name == "output"]
            built_against = self._built_against({path.name: path for path in generated}, pin, "update")
            if sorted((path.name, self._hashes.get(path).sha1) for path in generated) == sorted(built_from.items()):
                previous: list[Path] = []  # the same bytes: only the rule is new
            else:
                record, previous = self._publish_update(record["id"], built_from, generated)
            self._write_built_against(record, built_against)
        if record.get("costume_rule") != COSTUME_RULE:
            record["costume_rule"] = COSTUME_RULE  # after the commit: a rolled back update keeps the old rule
            self._save()
        return UpdateResult(self._entry(record), self._paths(record), previous, unchanged=not previous)

    @_mod_writer
    def _publish_update(
        self, mod_id: str, built_from: dict[str, str], generated: list[Path]
    ) -> tuple[dict[str, Any], list[Path]]:
        """Replace a mod's files with its rebuild, atomically per mod.

        A journal (``import-transactions/<id>``, ``operation`` ``update``) names the old and the new files with their
        hashes and the ``previous/<uuid>`` copy of the old ones before any file changes; the record's new file list is
        the commit point. A failure or an interruption before it leaves the old mod whole (``_rollback_update``, also at
        recovery): its files and file list come back, and its id, alias, assignment and repair links never change. Only
        the newest previous copy is kept. Refuses with ``entry_changed`` when the mod's files changed since the rebuild
        read them.
        """
        record = self._record(mod_id)
        paths = self._paths(record)
        if {path.name: self._hashes.get(path).sha1 for path in paths} != built_from:
            raise ServiceError("entry_changed", core=core_of(paths[0].name))
        directory = self.root / "mods" / mod_id
        transaction = self.root / "import-transactions" / mod_id
        journal = transaction / "journal.json"
        if transaction.parent.is_symlink() or transaction.exists() or (directory / "previous").is_symlink():
            raise ServiceError("library_io_error", file=transaction)
        new = {path.name: self._hashes.get(path).sha1 for path in generated}
        data = {
            "version": 2,
            "operation": "update",
            "id": mod_id,
            "phase": "copying",
            "old": built_from,
            "new": new,
            "previous": uuid.uuid4().hex,
        }
        previous_dir = directory / "previous" / data["previous"]
        atomic_json(journal, data)
        try:
            previous = []
            for path in paths:
                backup = previous_dir / path.name
                atomic_copy(path, backup)
                verify_file(backup, built_from[path.name])
                previous.append(backup)
            data["phase"] = "replacing"
            atomic_json(journal, data)
            for path in generated:
                atomic_copy(path, directory / path.name)
                verify_file(directory / path.name, new[path.name])
            record["files"] = sorted(new)
            self._save()
        except Exception as error:
            try:
                self._rollback_update(journal, data)
            except Exception as recovery_error:
                log_failure(logger, logging.ERROR, "Mod update rollback pending", recovery_error)
                raise ServiceError("library_recovery_pending", file=journal) from error
            raise
        self._finish_update(journal, data)
        return record, previous

    def _finish_update(self, journal: Path, data: dict) -> None:
        """Finish an update after its commit.

        The old files the rebuild does not name go, older previous copies go, the built copies and markers follow
        (``_after_update``), then the journal. Repeating it is safe.
        """
        directory = self.root / "mods" / data["id"]
        for name in data["old"]:
            if name not in data["new"]:
                (directory / name).unlink(missing_ok=True)
        kept = directory / "previous"
        if kept.is_dir() and not kept.is_symlink():
            for folder in kept.iterdir():
                if folder.name != data["previous"]:
                    if folder.is_dir() and not folder.is_symlink():
                        shutil.rmtree(folder)
                    else:
                        folder.unlink()
        self._after_update(data["id"])
        try:
            shutil.rmtree(journal.parent)
            sync_directory(journal.parent.parent)
        except OSError as error:
            log_failure(logger, logging.WARNING, "Committed update journal retained", error)

    def _rollback_update(self, journal: Path, data: dict) -> None:
        """Put the old mod back.

        Each old file whose bytes are not the journal's comes from the previous copy (verified), the new files the old
        mod does not name go, and the record's file list in state.json is the old one again. This attempt's previous
        copy and the journal go last. Repeating it is safe.
        """
        mod_id, old = data["id"], data["old"]
        directory = self.root / "mods" / mod_id
        previous_dir = directory / "previous" / data["previous"]
        for name, digest in old.items():
            path = directory / name
            if path.is_file() and not path.is_symlink() and self._hashes.get(path).sha1 == digest:
                continue
            verify_file(previous_dir / name, digest)
            atomic_copy(previous_dir / name, path)
            verify_file(path, digest)
        for name in data["new"]:
            if name not in old:
                (directory / name).unlink(missing_ok=True)
        names = sorted(old)
        record = self._records.get(mod_id)
        if record is not None:
            record["files"] = names
        path = self._state_path
        with file_lock(self.root / "state.json.lock"):
            try:
                state = read_runtime(path)
            except FileNotFoundError:
                state = None
            if state is not None:
                if (
                    isinstance(state, dict)
                    and isinstance(state.get("version"), (int, float))
                    and not isinstance(state["version"], bool)
                    and state["version"] > 2
                ):
                    raise ServiceError("library_state_unreadable", file=path)
                if not isinstance(state, dict) or state.get("version") != 2 or not isinstance(state.get("mods"), list):
                    raise ServiceError("library_io_error", file=path)
                stored = [
                    item
                    for item in state["mods"]
                    if isinstance(item, dict) and item.get("id") == mod_id and item.get("files") != names
                ]
                if stored:
                    for item in stored:
                        item["files"] = names
                    try:
                        atomic_copy(path, self._prev_path)
                    except OSError as error:
                        log_failure(logger, logging.WARNING, "Could not keep the previous library state", error)
                    atomic_json(path, state)
            sync_directory(self.root)
        self._seen = None
        self._refresh_state()
        if previous_dir.exists():
            shutil.rmtree(previous_dir)
        shutil.rmtree(journal.parent)
        sync_directory(journal.parent.parent)

    def _recover_update(self, journal: Path, data: dict) -> None:
        """Settle an interrupted update (``_publish_update``).

        Finish it when the record names the new files and each holds its new bytes, else roll it back.
        """
        root = self.root / "import-transactions"
        mod_id, old, new = data["id"], data["old"], data["new"]

        def names_valid(files: Any) -> bool:
            return (
                isinstance(files, dict)
                and bool(files)
                and all(
                    isinstance(name, str)
                    and Path(name).name == name
                    and name.endswith(".bundle")
                    and isinstance(value, str)
                    and re.fullmatch(r"[0-9a-f]{40}", value) is not None
                    for name, value in files.items()
                )
            )

        if (
            not isinstance(mod_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", mod_id) is None
            or journal != root / mod_id / "journal.json"
            or data["version"] != 2
            or data["phase"] not in ("copying", "replacing")
            or not names_valid(old)
            or not names_valid(new)
            or not isinstance(data["previous"], str)
            or re.fullmatch(r"[0-9a-f]{32}", data["previous"]) is None
        ):
            raise ValueError("Invalid update journal")
        directory = self.root / "mods" / mod_id
        if (
            directory.parent.is_symlink()
            or directory.is_symlink()
            or (directory / "previous").is_symlink()
            or not directory.resolve().is_relative_to(self.root)
        ):
            raise ValueError("Invalid update directory")
        record = self._records.get(mod_id)

        def holds(name: str, digest: str) -> bool:
            path = directory / name
            return path.is_file() and not path.is_symlink() and self._hashes.get(path).sha1 == digest

        if (
            data["phase"] == "replacing"
            and record is not None
            and record.get("files") == sorted(new)
            and all(holds(name, digest) for name, digest in new.items())
        ):
            self._finish_update(journal, data)
        else:
            self._rollback_update(journal, data)

    def _after_update(self, mod_id: str) -> None:
        """Handle a mod's changed build.

        The built copies of the old build go, and every entry configured with the mod gets the updated marker, so the
        game files still holding the old build are offered Apply Mod Configuration. An earlier Keep as Is of that prompt
        is forgotten: it answered the old build.
        """
        source, stamp = f"mod:{mod_id}", now()
        dropped: list[dict] = []

        def change(data: dict) -> bool:
            changed = False
            for folder in data["folders"].values():
                for core_name in [
                    core_name for core_name, key in folder["built"].items() if key.get("source") == source
                ]:
                    dropped.append(folder["built"].pop(core_name))
                    changed = True
                for core_name, entry in folder["configured"].items():
                    if entry.get("source") == source:
                        folder["updated"][core_name] = stamp
                        if isinstance(folder["dismissed"].get("drift"), dict):
                            folder["dismissed"]["drift"].pop(core_name, None)
                        changed = True
            return changed

        with contextlib.suppress(ServiceError):
            self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))

    @_serialized
    def extract(
        self,
        target: str,
        out_dir: Path,
        log: LogFunc = no_log,
        pin: Pin | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> Path:
        """Extract game or mod assets into a child folder named after their bundle core.

        Args:
            target: mod:<id> for stored bundles or alt:<key> for live game parts.
            out_dir: Parent directory of the core-named output folder, which the engine clears before extraction.
            log: Receives user-facing operation messages.
            pin: Game install and server the operation is fixed to; None uses the current install for alt targets.
            progress: Receives extraction phase dictionaries; None disables reporting.

        Returns:
            The absolute core-named extraction folder.

        Raises:
            ServiceError: The source is unsupported, files are missing, the core name is unsafe, or extraction fails.
        """
        if target.startswith("mod:"):
            paths = self._paths(self._record(target[4:]))
        elif target.startswith("alt:"):
            paths = self._game_files(target[4:], None, pin)
        else:
            raise ServiceError("source_unsupported", source=target)
        if not paths:
            raise ServiceError("game_file_not_found", target=target)
        name = parse_filename(paths[0].name).core
        # The engine clears the output folder before extracting, so the folder must be a child of out_dir.
        if not is_safe_file_name(name):
            raise ServiceError("mod_unsupported", file=paths[0])
        folder = Path(out_dir).resolve() / name
        folder.mkdir(parents=True, exist_ok=True)
        report = (
            (
                lambda current, total, _name: progress(
                    {"current": current, "total": total, "step": "import.step.extract"}
                )
            )
            if progress
            else None
        )
        success, _ = core.process_asset_extraction(
            paths, folder, spine_options=self.settings.spine_options(False), log=log, progress_callback=report
        )
        if not success:
            raise ServiceError("asset_extraction_failed", target=target)
        return folder

    def _asset_hashes(self, paths: list[Path]) -> dict[tuple[str, str, int], tuple[str, int]]:
        assets = {}
        counts: dict[tuple[str, str], int] = {}
        types = {AssetType.Texture2D, AssetType.TextAsset, AssetType.Mesh, AssetType.AnimationClip}
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path)
            try:
                for obj in bundle.env.objects:
                    if obj.type not in types:
                        continue
                    name = obj.peek_name() or ""
                    key = (name, obj.type.name)
                    occurrence = counts.get(key, 0)
                    counts[key] = occurrence + 1
                    raw = obj.get_raw_data()
                    digest = hashlib.sha1(raw)
                    size = len(raw)
                    # Streamed textures and meshes may change without any change to
                    # their serialized object; include the referenced payload too.
                    data = obj.read()
                    stream = getattr(data, "m_StreamData", None)
                    if stream and stream.path and stream.size:
                        payload = get_resource_data(stream.path, obj.assets_file, stream.offset, stream.size)
                        digest.update(payload)
                        size += len(payload)
                    assets[(*key, occurrence)] = (digest.hexdigest(), size)
            except (ValueError, EOFError) as error:
                raise ServiceError("bundle_invalid", file=path) from error
        return assets

    @_serialized
    def compare(self, mod_id: str, pin: Pin | None = None) -> CompareResult:
        """Compare raw assets and external stream payloads with official files even while a mod is installed.

        Args:
            mod_id: Stored mod ID whose texture, text, mesh, and animation assets are compared.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Per-asset same/changed/added/removed status and serialized-plus-stream byte sizes.
        """
        pin = self._current(pin)
        record = self._record(mod_id)
        targets = self._plan_targets(self._plan(record, no_log, pin, check_references=False))
        baseline = [self._official_for(path, pin) for path in targets]
        return self._compare_paths(mod_id, self._paths(record), baseline)

    def _compare_paths(self, mod_id: str, paths: list[Path], baseline: list[Path]) -> CompareResult:
        mod = self._asset_hashes(paths)
        official = self._asset_hashes(baseline)
        differences = []
        for key in sorted(mod.keys() | official.keys()):
            left, right = mod.get(key), official.get(key)
            status = (
                "removed"
                if left is None
                else "added"
                if right is None
                else ("same" if left[0] == right[0] else "changed")
            )
            differences.append(
                AssetDifference(key[0], key[1], status, left[1] if left else None, right[1] if right else None)
            )
        return CompareResult(mod_id, differences)
