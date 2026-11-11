"""The App's mod update: a mod's assets moved into the current release of its parts.

Built on the engine's primitives (``Bundle.load``, ``extract_patch``, ``apply_patch``, ``save``); upstream's
``process_mod_update`` stays as upstream ships it, for the classic UI and the CLI. On top of upstream's flow this
one keeps assets whose counterparts are unchanged (``UpdateScope``), validates every target before it saves any,
fails the whole mod on a load, match or save failure, and reports what moved over and what did not."""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from bmpt.engine import (
    REPLACEABLE_ASSET_TYPES, AnimCheckOptions, AnimDiffMap, AssetKey, Bundle, FilePair, LogFunc, MatchStrategy, Patch,
    ReplaceAssetType, SaveOptions, SkelConvertOptions, SkelVersionConflict, collect_stream_companions,
    format_skel_conflicts, no_log, t,
)
from ..errors import ServiceError


class UpdateScope:
    """Counterparts in the new release, shared by preflight and execution (the ``scope`` of
    ``Bundle.extract_patch``)."""

    def __init__(self, targets: list[Bundle], strategy: MatchStrategy, asset_types: set[str]):
        key_of = Bundle._get_key_func(strategy)
        self.counterparts: dict[AssetKey, list[tuple[Bundle, object]]] = {}
        self.not_transferred: set[AssetKey] = set()
        self.unchanged: set[AssetKey] = set()
        self.seen: set[AssetKey] = set()  # every source key asked about, transferred or not
        for target in targets:
            for obj in target.env.objects:
                if obj.type in REPLACEABLE_ASSET_TYPES and ("ALL" in asset_types or obj.type.name in asset_types):
                    key = key_of(obj)
                    if key is not None:
                        self.counterparts.setdefault(key, []).append((target, obj))

    def includes(self, key: AssetKey) -> bool:
        if key is not None:
            self.seen.add(key)
        if key in self.counterparts:
            return True
        self.not_transferred.add(key)
        return False

    @staticmethod
    def serialized(bundle: Bundle, obj) -> tuple[bytes, dict[str, bytes]]:
        raw = obj.get_raw_data()
        companions = collect_stream_companions(raw, bundle.env.file)
        if companions is None:
            raise ValueError(t("webui.error.required_asset_stream_missing"))
        # Compare the object's own stream range when metadata can be read without decoding it.
        try:
            data = obj.read()
            stream = getattr(data, "m_StreamData", None)
            resource = getattr(data, "m_Resource", None)
        except Exception:
            stream = resource = None
        ranges = []
        if stream and stream.path:
            ranges.append((stream.path, stream.offset, stream.size))
        if resource and resource.m_Source:
            ranges.append((resource.m_Source, resource.m_Offset, resource.m_Size))
        for path, offset, size in ranges:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            payload = companions.get(name)
            if payload is None or offset < 0 or size < 0 or offset + size > len(payload):
                raise ValueError(t("webui.error.required_asset_stream_missing"))
            companions[name] = payload[offset:offset + size]
        return raw, companions

    def skip_unchanged(self, source: Bundle, obj, key: AssetKey) -> bool:
        try:
            content = self.serialized(source, obj)
            if all(content == self.serialized(target, counterpart)
                   for target, counterpart in self.counterparts[key]):
                self.unchanged.add(key)
                return True
        except Exception:
            pass  # Unproven equality must retain the original extraction failure.
        return False

    @property
    def target_only(self) -> set[AssetKey]:
        """Target assets of the replaced types that no source asset matched."""
        return set(self.counterparts) - self.seen


@dataclass
class RebuildResult:
    """``rebuild``'s outcome. ``all_unchanged``: every matched asset already equals the target's, nothing was
    written. The asset lists hold the keys as text, sorted: ``matched`` found their counterpart in a target (written
    or equal), ``unchanged`` equal their counterpart, ``not_transferred`` have none, ``target_only`` are the
    targets' assets of the replaced types that the sources lack. A prepared patch reports matches only."""
    success: bool
    message: str
    file_pairs: list[FilePair] = field(default_factory=list)
    anim_diffs: AnimDiffMap = field(default_factory=dict)
    error: ServiceError | None = None
    all_unchanged: bool = False
    matched: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_transferred: list[str] = field(default_factory=list)
    target_only: list[str] = field(default_factory=list)


def _names(keys) -> list[str]:
    return sorted(map(str, keys))


def rebuild(
    source_paths: list[Path],
    target_paths: list[Path],
    output_dir: Path,
    asset_types_to_replace: set[ReplaceAssetType],
    save_options: SaveOptions,
    spine_options: SkelConvertOptions | None = None,
    *,
    skip_unchanged: bool = False,
    match_strategy: MatchStrategy = "path_id",
    anim_check: AnimCheckOptions | None = None,
    log: LogFunc = no_log,
    prepared_patch: Patch | None = None,
    on_step: Callable[[str], None] | None = None,
) -> RebuildResult:
    """Move the sources' assets into the targets and save the changed targets in ``output_dir`` under their own
    names (N to N). ``prepared_patch`` replaces the extraction: every target must then take all of it.
    ``on_step`` hears ``extract``, ``replace``, ``save`` and ``crc``. A failure returns ``success=False`` with a
    message (and the ``ServiceError`` when one names it); nothing is saved unless every target passed."""
    try:
        log(f'\n--- {t("log.section.extracting_patches")} ---')
        if on_step:
            on_step("extract")
        patches: Patch = dict(prepared_patch or {})
        skel_conflicts: list[SkelVersionConflict] = []
        targets = []
        for path in target_paths:
            target = Bundle.load(path, log)
            if target is None:
                return RebuildResult(False, t("message.load_failed"))
            targets.append(target)
        scope = UpdateScope(targets, match_strategy, asset_types_to_replace) if prepared_patch is None else None

        for src in source_paths:
            src_bundle = Bundle.load(src, log)
            if not src_bundle:
                return RebuildResult(False, t("message.load_failed"))
            patch, conflicts = src_bundle.extract_patch(asset_types_to_replace, match_strategy, spine_options,
                                                        **({"scope": scope} if scope is not None else {}))
            patches.update(patch)
            skel_conflicts.extend(conflicts)

        if skel_conflicts:
            # A skel version the target version cannot take stops the whole mod before any target.
            msg = format_skel_conflicts(skel_conflicts)
            log(f"❌ {msg}")
            return RebuildResult(False, msg)

        required_keys = set(patches) - {"__mode__"}
        unchanged_keys = scope.unchanged if scope is not None else set()
        not_transferred = _names(scope.not_transferred) if scope is not None else []
        target_only = _names(scope.target_only) if scope is not None else []
        if not required_keys and not unchanged_keys:
            return RebuildResult(False, t("message.mod_update.no_assets_extracted"))
        warning = t("webui.import.notTransferred", names=", ".join(not_transferred)) if not_transferred else ""
        if warning:
            log(warning)

        log(f"  > {t('log.mod_update.pool_built', count=len(patches))}")

        log(f'\n--- {t("log.section.applying_to_targets")} ---')
        file_pairs: list[FilePair] = []
        total_matched = len(unchanged_keys)
        anim_diffs: dict[str, set[str]] = {}
        failed_targets = False
        matched_keys: set[AssetKey] = set()
        applied_keys: set[AssetKey] = set()
        skipped_keys: set[AssetKey] = set()
        pending_saves = []

        for tgt, tgt_bundle in zip(target_paths, targets):
            retained = {key for key in unchanged_keys
                        if any(target is tgt_bundle for target, _ in scope.counterparts[key])}

            if on_step:
                on_step("replace")
            result = tgt_bundle.apply_patch(patches, match_strategy, anim_check)
            if prepared_patch is not None and result.matched_count != len(patches):
                return RebuildResult(False, t("webui.error.atlas_remap_target_mismatch", file=tgt.name,
                                              matched=result.matched_count, expected=len(patches)))
            total_matched += result.matched_count
            matched_keys.update(result.matched_keys)
            skipped = set(result.skipped_keys or ())
            skipped_keys |= skipped
            applied_keys |= set(result.matched_keys) - skipped

            for name, anims in (result.anim_diffs or {}).items():
                anim_diffs.setdefault(name, set()).update(anims)

            if skip_unchanged and result.applied_count == 0 and (result.skipped_count > 0 or retained):
                log(f"  ⏭️ {t('log.mod_update.target_unchanged', name=tgt.name, count=result.skipped_count)}")
                continue

            if result.is_success or retained:
                pending_saves.append((tgt, tgt_bundle, result))
            else:
                log(f"  > {t('log.file.no_changes_made')} ({tgt.name})")

        missing = required_keys - matched_keys
        if missing:
            raise ServiceError("required_assets_unmatched", names=", ".join(_names(missing)))
        # Validate every target before publishing any output for this Mod.
        for tgt, tgt_bundle, result in pending_saves:
            output_path = output_dir / tgt.name
            save_ok, save_message = (tgt_bundle.save(output_path, save_options, on_step)
                                     if on_step else tgt_bundle.save(output_path, save_options))
            if save_ok:
                file_pairs.append(FilePair(output_path, tgt))
                log(f"  ✅ {t('log.mod_update.target_processed', name=tgt.name, applied=result.applied_count)}")
            else:
                log(f"  ❌ {t('log.file.save_failed', path=output_path, error=save_message)}")
                failed_targets = True

        final_anim_diffs: AnimDiffMap = {k: sorted(v) for k, v in anim_diffs.items()}

        # A partial save is a failed mod, even when another part was unchanged or saved successfully.
        if failed_targets:
            return RebuildResult(False, t("message.mod_update.no_targets_processed"), anim_diffs=final_anim_diffs)

        report = {"matched": _names(matched_keys | unchanged_keys),
                  "unchanged": _names((unchanged_keys | skipped_keys) - applied_keys),
                  "not_transferred": not_transferred, "target_only": target_only}
        if not file_pairs:
            # Nothing matched at all, or every match was left as it is.
            if total_matched > 0 and skip_unchanged:
                return RebuildResult(True, "", anim_diffs=final_anim_diffs, all_unchanged=True, **report)
            return RebuildResult(False, t("message.mod_update.no_targets_processed"), anim_diffs=final_anim_diffs)

        message = "\n".join(filter(None, [t("message.mod_update.success"), warning]))
        return RebuildResult(True, message, file_pairs, final_anim_diffs, **report)

    except ServiceError as error:
        message = t("webui.error." + error.code, **error.params)
        log(message)
        return RebuildResult(False, message, error=error)

    except Exception as e:
        log(f"\n❌ {t('common.error')}: {t('log.error_processing', error=e)}")
        log(traceback.format_exc())
        return RebuildResult(False, t("message.error_during_process", error=e))
