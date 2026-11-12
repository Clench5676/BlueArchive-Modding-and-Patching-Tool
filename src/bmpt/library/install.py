"""The App's mod install plan and its build into staged copies of the game's parts.

The library plans which mod file goes to which part (``ModLibrary._plan``), stages copies of those parts, builds the
mod into the copies here, and writes the game itself only through its journaled commit. Upstream's
``core.mod_install`` stays as upstream ships it, for the classic UI and the CLI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from bmpt.engine import (
    AnimCheckOptions, Bundle, CRCUtils, LogFunc, ParsedFilename, ProgressCallback, ReplaceAssetType, SaveOptions,
    SkelConvertOptions, no_log, parse_filename, t,
)
from .. import devmode
from ..openfiles import temporary_directory
from .rebuild import rebuild


class Action(StrEnum):
    """What the build does with one mod file (the values of upstream's ``InstallAction``)."""
    DIRECT_REPLACE = "direct_replace"  # the part's current release: written whole, re-saved for the CRC if needed
    UPDATE = "update"                  # an older release or another build: its assets move into the parts
    NOT_FOUND = "not_found"            # no part takes it


@dataclass
class PlannedFile:
    """One mod file of a group. ``existing_path``: the part a direct replacement writes."""
    mod_path: Path
    parsed: ParsedFilename
    action: Action
    existing_path: Path | None = None
    need_crc_fix: bool = False


@dataclass
class PlannedGroup:
    """Mod files that build together, per name prefix and core. ``target_paths``: the parts an update writes."""
    prefix: str
    category: str | None
    core: str
    files: list[PlannedFile]
    target_paths: list[Path] = field(default_factory=list)


def backup_file(dst: Path, create_backup: bool, log: LogFunc = no_log) -> None:
    """Keep the classic adjacent backup, also used before a library transaction writes."""
    if create_backup:
        backup_path = dst.with_suffix(dst.suffix + ".backup")
        devmode.check_write(backup_path)
        shutil.copy2(dst, backup_path)
        log(t("log.file.backed_up", path=backup_path))


def _replace(source: Path, target: Path, log: LogFunc) -> bool:
    try:
        # dev mode: game files are written only inside BAMT_DEV_WRITABLE (or the library's staging folders)
        devmode.check_write(target)
        log(t("log.file.overwritten", path=target))
        shutil.copy2(source, target)
        return True
    except Exception as error:
        log(f"❌ {t('log.mod_install.replace_failed', path=target, error=error)}")
        return False


def matches_target_crc(source: Path, target: Path) -> bool:
    """Whether a file's bytes already have the CRC in the target's name. The library also writes a mod file whole
    over a part whose name differs from the mod file's (its CRC changed in a game update, or it matched by CAB), so
    the mod file's own name proves nothing about the part's."""
    crc = parse_filename(target.name).crc
    return bool(crc) and crc.isdigit() and CRCUtils.compute_crc32(source) == int(crc)


def _direct(file: PlannedFile, save_options: SaveOptions, log: LogFunc) -> bool:
    """Copy the mod file over its part, or re-save it under the part's CRC first when the bytes do not match."""
    if not file.need_crc_fix or matches_target_crc(file.mod_path, file.existing_path):
        return _replace(file.mod_path, file.existing_path, log)
    with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
        resaved = Path(temporary) / file.existing_path.name
        bundle = Bundle.load(file.mod_path, log)
        if bundle is None:
            log(f"❌ {t('message.load_failed')}: {file.mod_path.name}")
            return False
        ok, message = bundle.save(resaved, replace(save_options, perform_crc=True))
        if not ok:
            log(f"❌ {t('log.file.save_failed', path=resaved, error=message)}")
            return False
        return _replace(resaved, file.existing_path, log)


def _build_group(group: PlannedGroup, output_dir: Path, asset_types: set[ReplaceAssetType],
                 save_options: SaveOptions, spine_options: SkelConvertOptions | None,
                 anim_check: AnimCheckOptions | None, log: LogFunc) -> list[tuple[str, str]]:
    direct = [file for file in group.files if file.action == Action.DIRECT_REPLACE]
    update = [file for file in group.files if file.action == Action.UPDATE]
    if not direct and not update:
        return []
    failures = []
    log("\n" + "-" * 50)
    log(t("log.mod_install.execute_group", core=group.core))
    for file in direct:
        if file.need_crc_fix:
            log(t("log.mod_install.crc_resave", filename=file.mod_path.name))
        if not _direct(file, save_options, log):
            failures.append((group.core, file.mod_path.name))
    if update:
        result = rebuild([file.mod_path for file in update], group.target_paths, output_dir, asset_types,
                         save_options, spine_options, skip_unchanged=True, match_strategy="cont_name_type",
                         anim_check=anim_check, log=log)
        if result.no_match:
            # The files hold nothing these parts take (e.g. a part of materials only that a game update rebuilt):
            # the parts stay the official copies they were staged as (BL-180).
            for target in group.target_paths:
                log(f"  ⏭️ {t('log.mod_update.target_unchanged', name=target.name, count=0)}")
        elif not result.success:
            failures.append((group.core, result.message))
        elif result.all_unchanged:
            log(t("log.mod_install.already_installed", core=group.core))
        else:
            for pair in result.file_pairs:  # output: the built file; source: the staged part
                if not _replace(pair.output, pair.source, log):
                    failures.append((group.core, pair.source.name))
    return failures


def install_staged(groups: list[PlannedGroup], output_dir: Path, asset_types: set[ReplaceAssetType],
                   save_options: SaveOptions, spine_options: SkelConvertOptions | None = None, *,
                   anim_check: AnimCheckOptions | None = None, log: LogFunc = no_log,
                   progress: ProgressCallback | None = None) -> list[tuple[str, str]]:
    """Build every group into the staged parts it names, in place, one group after another; the update builds go
    through ``output_dir``. ``progress`` hears (group number, groups, core) before each group. Returns the failures
    as (core, reason); a group that raises is one."""
    output_dir.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []
    for i, group in enumerate(groups, 1):
        if progress:
            progress(i, len(groups), group.core)
        try:
            failures.extend(_build_group(group, output_dir, asset_types, save_options, spine_options, anim_check,
                                         log))
        except Exception as error:
            log(f"❌ {t('log.process_failed', error=error)}")
            failures.append((group.core, str(error)))
    return failures
