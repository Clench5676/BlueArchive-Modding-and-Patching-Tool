"""Applying and restoring a choice, and the switch of an entry."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from bmpt.core import devmode
from bmpt.engine import (
    LogFunc,
    ProgressCallback,
    no_log,
    parse_filename,
    t,
)
from ...core.errors import ServiceError
from ...core.openfiles import temporary_directory
from .. import profiles as profiles_mod
from .. import mod_progress
from ..install import Action, PlannedFile, PlannedGroup, install_staged
from ..rebuild import rebuild
from .. import targets as resolver
from ..judge import (
    core_of,
    kind_of_file,
)
from ..models import (
    ApplyResult,
    Kind,
    Pin,
)
from ..storage import atomic_copy
from .base import _ChoiceChange, _item_fraction, _reasons, _serialized


logger = logging.getLogger(__package__)


class SwitchingMixin:
    """Applying and restoring a choice, and the switch of an entry."""

    def _install_direct(
        self,
        record: dict[str, Any],
        targets: list[Path],
        work: Path,
        log: LogFunc,
        progress: ProgressCallback | None,
        pin: Pin,
    ) -> dict[Path, Path]:
        sources = {path.name: path for path in self._paths(record)}
        by_name = {path.name: path for path in targets}
        stale: set[Path] = set()
        if (
            len(sources) == len(record["files"])
            and len(by_name) == len(targets)
            and sources.keys() <= by_name.keys()
            and all(not self._needs_crc_fix(path, by_name[name]) for name, path in sources.items())
        ):
            stale = self._stale_sources({by_name[name]: path for name, path in sources.items()}, pin, targets=targets)
            if not stale:
                # These parts have current names, references and the required CRC. The
                # transaction checks and copies their exact bytes once; staging an
                # official copy and replacing it with the same Mod adds no protection.
                if progress:
                    progress(0, 1, t("webui.progress.process", name=core_of(record["files"][0])))
                return {
                    target: sources[target.name] if target.name in sources else self._official_or_current(target, pin)
                    for target in targets
                }
        if record["kind"] == "other":
            resolution = self._resolve(sources, targets, pin, older=stale)
            more_stale = self._stale_sources(resolution.current, pin, targets=targets)
            if more_stale:
                resolution = self._resolve(sources, targets, pin, older=stale | more_stale)
            current = resolution.current
            staged = self._stage_direct(targets, sources, work / "targets", pin, set(targets) - current.keys())
            output = work / "output"
            output.mkdir(parents=True, exist_ok=True)
            sources = self._paths(record)
            for target, original in staged.items():
                exact = current.get(target)
                if exact:
                    if not self._needs_crc_fix(exact, target):
                        atomic_copy(exact, original)
                        continue
                    # Same-name mods retain the whole uploaded bundle, including types outside the update filter.
                    parsed = parse_filename(exact.name)
                    group = PlannedGroup(
                        parsed.prefix,
                        parsed.category,
                        parsed.core,
                        [PlannedFile(exact, parsed, Action.DIRECT_REPLACE, existing_path=original, need_crc_fix=True)],
                    )
                    failures = install_staged(
                        [group],
                        output,
                        self.settings.asset_types(),
                        self.settings.save_options(self.settings.crc_needed(target)),
                        self.settings.spine_options(),
                        log=log,
                    )
                    if failures:
                        raise ServiceError("mod_apply_failed", id=record["id"], reason=_reasons(failures))
                    continue
                matching = resolver.same_prefix(sources, target)
                if not matching:
                    raise ServiceError("mod_target_mismatch", id=record["id"], variant=core_of(target.name))
                result = rebuild(
                    matching,
                    [original],
                    output,
                    self.settings.asset_types(),
                    self.settings.save_options(self.settings.crc_needed(target)),
                    self.settings.spine_options(),
                    match_strategy="cont_name_type",
                    skip_unchanged=True,
                    anim_check=self.settings.anim_check_options(),
                    log=log,
                )
                if result.no_match:  # the part takes nothing from the mod: it stays the official copy
                    log(f"  ⏭️ {t('log.mod_update.target_unchanged', name=target.name, count=0)}")
                    continue
                if not result.success:
                    raise ServiceError("mod_apply_failed", id=record["id"], reason=result.message)
                for pair in result.file_pairs:
                    atomic_copy(pair.output, original)
            return staged
        started = time.perf_counter()
        groups = self._plan(record, log, pin, older=stale)
        allowed = set(targets)
        planned = self._plan_targets(groups)
        if any(path not in allowed for path in planned):
            raise ServiceError("game_file_outside_resources", file=next(p for p in planned if p not in allowed))
        updating = {
            path
            for group in groups
            for path in group.target_paths
            if any(file.action == Action.UPDATE for file in group.files)
        }
        staged = self._stage_direct(targets, sources, work / "targets", pin, updating)
        logger.info("Mod apply plan and stage: %.3f s", time.perf_counter() - started)
        started = time.perf_counter()
        # Build only into temporary copies: a part is copied whole or rebuilt; all real game writes go through
        # _commit.
        staged_groups = [
            replace(
                group,
                files=[
                    replace(
                        file,
                        existing_path=staged.get(file.existing_path),
                        need_crc_fix=self._needs_crc_fix(file.mod_path, file.existing_path)
                        if file.existing_path
                        else False,
                    )
                    for file in group.files
                ],
                target_paths=[staged[path] for path in group.target_paths],
            )
            for group in groups
        ]
        failures = install_staged(
            staged_groups,
            work / "output",
            self.settings.asset_types(),
            self.settings.save_options(self.settings.crc_needed(targets[0])),
            self.settings.spine_options(),
            anim_check=self.settings.anim_check_options(),
            log=log,
            progress=(lambda current, total, name: progress(current, total, t("webui.progress.process", name=name)))
            if progress
            else None,
        )
        logger.info("Mod apply build and CRC: %.3f s", time.perf_counter() - started)
        if failures:
            raise ServiceError("mod_apply_failed", id=record["id"], reason=_reasons(failures))
        return staged

    def _needs_crc_fix(self, source: Path, target: Path) -> bool:
        if not self.settings.crc_needed(target):
            return False
        expected = parse_filename(target.name).crc
        fingerprint = self._hashes.get(source)
        # Already fixed mods can be copied without loading and recompressing their textures with UnityPy.
        return fingerprint.trailing is None or not expected or fingerprint.crc != int(expected)

    @_serialized
    def apply(
        self,
        variant_key: str,
        source: str,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        kind: Kind | None = "spr",
        pin: Pin | None = None,
        *,
        create_backup: bool = False,
        fraction: Callable[[float], None] | None = None,
    ) -> ApplyResult:
        """Install and configure a source choice, deferring writes while mods are off.

        A mod replaces its whole kind; a same-family variant's mod can be adapted to
        official targets. The validated choice is saved before installation. A memorial lobby camera
        (``lobby_camera``) is a lobby choice: its source's art and its own prefab; any other lobby choice puts the
        variant's prefab back to its base.

        Args:
            variant_key: Destination variant key, or generic other:/other-set: target for kind other.
            source: official, mod:<id>, or variant:<key>; a model variant source fits its costume.
            log: Receives user-facing operation messages.
            progress: Receives apply progress; None disables reporting.
            kind: Slot kind; None selects all kinds for official, a mod's own kind, or both Spine kinds
                for variant adaptation.
            pin: Game install and server the operation is fixed to; None uses the current install.
            create_backup: Whether game writes also create classic adjacent backups.
            fraction: Costume-build fraction callback overriding progress for the build;
                None derives one from progress.

        Returns:
            Applied files and unchanged, deferred, kept-current, and incomplete status.

        Raises:
            ServiceError: A source or target is unsupported, a required original is missing,
                confirmation is required, or protected game writes are refused.
        """
        from .. import lobby_camera

        camera = lobby_camera.camera_of(self, source) if kind == "lobby" else None
        if source != "official":
            self._untested_apply(variant_key, source, kind, self._current(pin), camera)
        if camera is not None:
            return lobby_camera.apply(self, variant_key, camera, log, progress, self._current(pin), create_backup)
        result = self._apply_choice(
            variant_key, source, log, progress, kind, pin, create_backup=create_backup, fraction=fraction
        )
        if kind in ("lobby", None) and lobby_camera.cameras(self, variant_key):
            lobby_camera.release(self, variant_key, self._current(pin), log, progress)
        return result

    def _apply_choice(
        self,
        variant_key: str,
        source: str,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        kind: Kind | None = "spr",
        pin: Pin | None = None,
        *,
        create_backup: bool = False,
        fraction: Callable[[float], None] | None = None,
    ) -> ApplyResult:
        """Install and configure one kind from a mod, variant, or originals.

        None selects all kinds for originals, and both Spine kinds for variant adaptation.

        A mod replaces all parts of its own kind as a unit. Another variant's mod must belong to the same family and
        is adapted to official targets. While mods are off nothing is written: the source is configured and applied
        when mods are on (``deferred``). Save the validated choice before attempting installation.
        """
        if source == "official":
            pin = self._current(pin)
            if not profiles_mod.enabled(self.profiles.read()):
                targets = self._game_files(variant_key, kind, pin)
                kinds = {
                    (core_of(path.name) if kind == "other" else kind_of_file(path.name)): core_of(path.name)
                    for path in targets
                }
                return self._defer(pin, variant_key, kinds, None)
            return self._restore(variant_key, log, kind, pin=pin)
        if kind == "other":
            return self._apply_other(variant_key, source, log, progress, self._current(pin), create_backup)
        variant = self._variant(variant_key)
        pin = self._current(pin)
        on = profiles_mod.enabled(self.profiles.read())
        started = time.perf_counter()
        kinds = {}
        kept_current: list[str] = []
        incomplete = False
        change = _ChoiceChange(self, pin, variant_key, source)
        try:
            if on:
                self._check_stopping(pin)
            with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                work = Path(temporary)
                build_keys: dict[Path, str] = {}
                if source.startswith("mod:"):
                    record = self._record(source[4:])
                    target_variant = self._target_of(record)
                    if (kind is not None and record["kind"] != kind) or (
                        self._variant(target_variant).family != variant.family
                    ):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant_key)
                    targets = self._game_files(variant_key, record["kind"], pin)
                    if not targets:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    cores = [
                        getattr(variant, "model").lower() if record["kind"] == "model" else core_of(targets[0].name)
                    ]
                    own_model = record["kind"] == "model" and all(
                        core_of(name) in self._core_map(pin).slot_cores(cores[0]) for name in record["files"]
                    )
                    if (record["kind"] == "model" and not own_model) or (
                        record["kind"] == "lobby" and core_of(record["files"][0]) != cores[0]
                    ):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=variant_key)
                    if not on:
                        return self._defer(pin, variant_key, {record["kind"]: cores[0]}, source)
                    kinds = {record["kind"]: cores[0]}
                    incomplete = self._incomplete(record, pin)
                    change.configure(kinds)
                    if own_model or core_of(record["files"][0]) == cores[0]:
                        view = self._view([pin], "full")
                        entry = view.entry(pin, cores[0])
                        identity = self._identity({path.name: path for path in self._paths(record)})
                        if entry.carried == source and self._matches_installed(identity, entry):
                            self._note_judged(view, entry)
                            change.configure({record["kind"]: cores[0]})
                            self._selection_error(pin, kinds.values(), None)
                            return ApplyResult(variant_key, source, [], unchanged=True, incomplete=incomplete)
                        replacements = self._cached_build(
                            self._paths(record),
                            targets,
                            pin,
                            lambda: self._install_direct(record, targets, work, log, progress, pin),
                            build_keys,
                        )
                    elif target_variant != variant_key:
                        sources = self._preview(record, pin)
                        replacements = self._cached_build(
                            sources,
                            targets,
                            pin,
                            lambda: self._stage_adapted(sources, targets, work / record["kind"], log, pin),
                            build_keys,
                        )
                    else:  # a lobby mod of another core of this variant: not offered, never written there
                        raise ServiceError("game_file_outside_resources", file=targets[0])
                    kinds = {record["kind"]: cores[0]}
                elif source.startswith("variant:") and kind == "model":
                    # another variant's costume put on this variant's model slot (gameassets.model_adapt)
                    other = source[8:]
                    if other == variant_key:
                        raise ServiceError("source_unsupported", source=source)
                    own = getattr(variant, "model", None)
                    targets = self._game_files(variant_key, "model", pin)
                    if not own or not targets:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    verdict = self._verdicts().get(self._costume_signature(variant_key, other, pin)[0])
                    if not self._costume_allowed(verdict):  # refused: never saved or written
                        raise ServiceError("source_unsupported", source=verdict.get("reason") or source)
                    kinds = {"model": own.lower()}
                    change.configure(kinds)
                    if not on:
                        return self._defer(pin, variant_key, kinds, source)
                    _, inputs = self._costume_source(other, pin)
                    replacements = self._cached_build(
                        inputs,
                        targets,
                        pin,
                        lambda: self._stage_costume(
                            other, targets, work / "model", log, pin, fraction=fraction or _item_fraction(progress)
                        ),
                        build_keys,
                    )
                elif source.startswith("variant:"):
                    other = source[8:]
                    self._variant(other)
                    if kind == "model":
                        raise ServiceError("source_unsupported", source=source)
                    replacements: dict[Path, Path] = {}
                    kinds = {}
                    for current in ("spr", "lobby") if kind is None else (kind,):
                        targets = self._game_files(variant_key, current, pin)
                        sources = self._game_files(other, current, pin)
                        if not targets or not sources:
                            continue
                        kinds[current] = core_of(targets[0].name)
                    if not kinds:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    change.configure(kinds)
                    if on:
                        for current in kinds:
                            targets = self._game_files(variant_key, current, pin)
                            sources = self._game_files(other, current, pin)
                            official = [self._official_for(path, pin) for path in sources]
                            replacements.update(
                                self._cached_build(
                                    official,
                                    targets,
                                    pin,
                                    lambda: self._stage_adapted(official, targets, work / current, log, pin),
                                    build_keys,
                                )
                            )
                    if not kinds:
                        raise ServiceError("game_file_not_found", variant=variant_key)
                    if not on:
                        return self._defer(pin, variant_key, kinds, source)
                else:
                    raise ServiceError("source_unsupported", source=source)
                prepared = time.perf_counter()
                mod_progress.entered(fraction, "write")
                files = self._commit(
                    replacements,
                    source,
                    log,
                    progress,
                    pin,
                    prune=False,
                    build_keys=build_keys,
                    create_backup=create_backup,
                    kept_current=kept_current,
                )
                change.configure(kinds)
                self._selection_error(pin, kinds.values(), None)
                for core in kinds.values():
                    self._clear_marker(pin, core)
                logger.info("Mod apply copy and record: %.3f s", time.perf_counter() - prepared)
                return ApplyResult(
                    variant_key,
                    source,
                    files,
                    unchanged=not files,
                    kept_current=bool(kept_current),
                    incomplete=incomplete,
                )
        except Exception as error:
            if not change.refused(error) and kinds:
                self._selection_error(
                    pin,
                    kinds.values(),
                    error.to_json()
                    if isinstance(error, ServiceError)
                    else {
                        "code": "library_io_error" if isinstance(error, OSError) else "library_operation_failed",
                        "params": {"operation": "apply"},
                    },
                )
            raise
        finally:
            change.close()
            pruning = time.perf_counter()
            self._prune_quietly()
            logger.info(
                "Mod apply prune: %.3f s; total: %.3f s", time.perf_counter() - pruning, time.perf_counter() - started
            )

    def _untested_apply(
        self, variant_key: str, source: str, kind: Kind | None, pin: Pin, camera: dict[str, Any] | None
    ) -> None:
        """Give an apply's confirmation on an untested build (``_check_untested``).

        It is made before anything is built or configured, on the targets of each kind the apply writes, a camera's art
        and prefab together. Nothing to confirm while mods are off, when an apply only configures.
        """
        if self.untested_version(pin) is None or not profiles_mod.enabled(self.profiles.read()):
            return
        if camera is not None:  # one entry, the lobby, for the art and the prefab it holds
            groups = [
                (
                    variant_key,
                    "lobby",
                    [*self._game_files(variant_key, "lobby", pin), *self._game_files(camera["target"], "other", pin)],
                )
            ]
        elif kind == "other":
            groups = [(variant_key, "other", self._game_files(variant_key, "other", pin))]
        else:
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            kinds = (kind,) if kind else ((record["kind"],) if record else ("spr", "lobby"))
            groups = [(variant_key, current, self._game_files(variant_key, current, pin)) for current in kinds]
        self._check_untested(
            [
                (pin, self._untested_entry(pin, core_of(targets[0].name), targets, variant, current), targets)
                for variant, current, targets in groups
                if targets
            ]
        )

    def _defer(self, pin: Pin, variant_key: str, kinds: dict[str, str], source: str | None) -> ApplyResult:
        """While mods are off: configure the source without writing, applied when mods are on."""
        self._configure(pin, variant_key, kinds, source)
        if source is None:
            self._prune_quietly()
        return ApplyResult(variant_key, source or "official", [], unchanged=True, deferred=True)

    def _apply_other(
        self,
        target: str,
        source: str,
        log: LogFunc,
        progress: ProgressCallback | None,
        pin: Pin,
        create_backup: bool,
        *,
        untested: bool = True,
    ) -> ApplyResult:
        if not source.startswith("mod:"):
            raise ServiceError("source_unsupported", source=source)
        record = self._record(source[4:])
        if record["kind"] != "other" or record["target"] != target:
            raise ServiceError("mod_target_mismatch", id=record["id"], variant=target)
        targets = self._game_files(target, "other", pin)
        kinds = {core_of(path.name): core_of(path.name) for path in targets}
        if not profiles_mod.enabled(self.profiles.read()):
            return self._defer(pin, target, kinds, source)
        kept_current: list[str] = []
        change = _ChoiceChange(self, pin, target, source)
        try:
            self._check_stopping(pin)
            change.configure(kinds)
            with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                build_keys = {}
                replacements = self._cached_build(
                    self._paths(record),
                    targets,
                    pin,
                    lambda: self._install_direct(record, targets, Path(temporary), log, progress, pin),
                    build_keys,
                )
                files = self._commit(
                    replacements,
                    source,
                    log,
                    progress,
                    pin,
                    prune=False,
                    build_keys=build_keys,
                    create_backup=create_backup,
                    kept_current=kept_current,
                    untested=untested,
                )
            self._configure(pin, target, kinds, source)
            self._selection_error(pin, kinds, None)
            for slot in kinds:
                self._clear_marker(pin, slot)
            return ApplyResult(target, source, files, unchanged=not files, kept_current=bool(kept_current))
        except Exception as error:
            if not change.refused(error):
                self._selection_error(
                    pin,
                    kinds,
                    error.to_json()
                    if isinstance(error, ServiceError)
                    else {
                        "code": "library_io_error" if isinstance(error, OSError) else "library_operation_failed",
                        "params": {"operation": "apply"},
                    },
                )
            raise
        finally:
            change.close()
            self._prune_quietly()

    @_serialized
    def restore(
        self,
        variant_key: str,
        log: LogFunc = no_log,
        kind: Kind | None = "spr",
        pin: Pin | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Restore official bytes and drop the selected slots' configuration.

        Already-official files are only recorded. A lobby's restore puts its prefab back too, when one of
        its cameras holds it (``lobby_camera.release``).

        Args:
            variant_key: Variant key, or generic other:/other-set: target whose files are restored.
            log: Receives user-facing operation messages.
            kind: Slot kind to restore; None restores all kinds.
            pin: Game install and server the operation is fixed to; None uses the current install.
            progress: Receives restore progress; None disables reporting.
        """
        from .. import lobby_camera

        result = self._restore(variant_key, log, kind, pin, progress)
        if (
            kind in ("lobby", None)
            and not variant_key.startswith(("other:", "other-set:"))
            and (lobby_camera.cameras(self, variant_key))
        ):
            lobby_camera.release(self, variant_key, self._current(pin), log, progress)
        return result

    def _restore(
        self,
        variant_key: str,
        log: LogFunc = no_log,
        kind: Kind | None = "spr",
        pin: Pin | None = None,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        pin = self._current(pin)
        self.profiles.read()
        targets = self._game_files(variant_key, kind, pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=variant_key)
        try:
            replacements = {path: self._official_for(path, pin) for path in targets}
            files = self._commit(replacements, "official", log, progress=progress, pin=pin, prune=False)
            kinds = {
                (core_of(path.name) if kind == "other" else kind_of_file(path.name)): core_of(path.name)
                for path in targets
            }
            self._configure(pin, variant_key, kinds, None)
        finally:
            self._prune_quietly()
        return ApplyResult(variant_key, "official", files, unchanged=not files)

    @_serialized
    def would_write(
        self, variant_key: str, source: str, kind: Kind | None = "spr", pin: Pin | None = None, *, setting: bool = False
    ) -> bool:
        """Estimate whether a source setting or explicit restore would write game files.

        Deferred settings and already-official restores only change configuration,
        allowing those calls while the classic UI is open.

        Args:
            variant_key: Variant key or generic target identifying the destination files.
            source: Requested source; official checks file headers, while other sources follow switch intent.
            kind: Slot kind to inspect; None considers every applicable kind.
            pin: Game install and server the operation is fixed to; None uses the current install.
            setting: Whether this is a source setting deferred while mods are off, rather than an explicit restore.
        """
        pin = self._current(pin)
        on = profiles_mod.enabled(self.profiles.read())
        if setting and not on:
            return False
        if source != "official":
            return on
        view = self._view([pin], "family")
        for path in self._game_files(variant_key, kind, pin):
            part = view.part(path, pin, kind_of_file(path.name) or "spr")
            if not part.official:
                return True
        return False

    def _switch(
        self,
        pin: Pin,
        core: str,
        source: str,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        use_built: bool = True,
        fraction: Callable[[float], None] | None = None,
    ) -> list[Path]:
        """Write an entry internally: its core's parts in its own install switched to a source, and nothing else.

        The configuration stays. The write is from a built copy when one still fits; a mod made for the core is
        installed directly, another variant's mod adapted, another variant's files (in that same install) adapted.
        ``fraction``: told how far a costume build is and when the write starts (``mod_progress.Part``).
        """
        core = self._core_map(pin).slot(core)
        targets = self._spine_parts(core, pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=core)
        kind = self._kind(core, pin) or "spr"
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            work = Path(temporary)
            build_keys: dict[Path, str] = {}
            replacements = None
            if source == "official":
                replacements = {path: self._official_for(path, pin) for path in targets}
            elif use_built:
                replacements = self._built_replacements(pin, core, source, targets, build_keys)
            if replacements is None:
                if source.startswith("mod:"):
                    record = self._record(source[4:])
                    if record["kind"] == "model" and any(
                        core_of(name) not in self._core_map(pin).slot_cores(core) for name in record["files"]
                    ):
                        raise ServiceError("mod_target_mismatch", id=record["id"], variant=core)
                    if record["kind"] in ("other", "model") or core_of(record["files"][0]) == core:
                        replacements = self._cached_build(
                            self._paths(record),
                            targets,
                            pin,
                            lambda: self._install_direct(record, targets, work, log, progress, pin),
                            build_keys,
                        )
                    else:
                        sources = self._preview(record, pin)
                        replacements = self._cached_build(
                            sources,
                            targets,
                            pin,
                            lambda: self._stage_adapted(sources, targets, work / kind, log, pin),
                            build_keys,
                        )
                elif source.startswith("variant:") and kind == "model":
                    _, inputs = self._costume_source(source[8:], pin)
                    replacements = self._cached_build(
                        inputs,
                        targets,
                        pin,
                        lambda: self._stage_costume(
                            source[8:], targets, work / kind, log, pin, fraction=fraction or _item_fraction(progress)
                        ),
                        build_keys,
                    )
                elif source.startswith("variant:"):
                    other = self._core_map(pin).core(source[8:], kind)
                    sources = self._index(pin).get(other.lower(), []) if other else []
                    if not sources:
                        raise ServiceError("game_file_not_found", variant=source[8:])
                    official = [self._official_for(path, pin) for path in sources]
                    replacements = self._cached_build(
                        official,
                        targets,
                        pin,
                        lambda: self._stage_adapted(official, targets, work / kind, log, pin),
                        build_keys,
                    )
                else:
                    raise ServiceError("source_unsupported", source=source)
            mod_progress.entered(fraction, "restore" if source == "official" else "write")
            files = self._commit(replacements, source, log, progress, pin, prune=False, build_keys=build_keys)
        self._clear_marker(pin, core)
        return files
