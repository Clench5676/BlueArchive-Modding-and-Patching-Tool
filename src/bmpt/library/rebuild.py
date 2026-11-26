"""The App's mod update: a mod's assets moved into the current release of its parts.

Built on the engine's primitives (``Bundle.load``, ``extract_patch``, ``apply_patch``, ``save``); upstream's
``process_mod_update`` stays as upstream ships it, for the classic UI and the CLI. On top of upstream's flow this
one keeps assets whose counterparts are unchanged (``UpdateScope``), validates every target before it saves any,
fails the whole mod on a load, match or save failure, and reports what moved over and what did not. Raw transfers
across Unity releases keep equal official objects or use an explicit typetree upgrade.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from bmpt.engine import (
    REPLACEABLE_ASSET_TYPES,
    AnimCheckOptions,
    AnimDiffMap,
    AssetKey,
    Bundle,
    FilePair,
    LogFunc,
    MatchStrategy,
    Obj,
    Patch,
    PatchResult,
    ReplaceAssetType,
    SaveOptions,
    SkelConvertOptions,
    SkelVersionConflict,
    collect_stream_companions,
    format_skel_conflicts,
    no_log,
    t,
)
from ..core.errors import ServiceError
from ..unity.bundle_guard import refuse_unsupported
from . import pointers
from . import toggle_timing
from . import tree_upgrade
from .bundle_crc import name_hash, target_save_options

#: The types the engine moves through their type trees: their fields are written into the target's own object, so
#: they survive a change of the serialized layout between Unity releases. Every other replaceable type is copied as
#: whole serialized bytes, which are only valid in the layout they were written in.
TREE_COPIED_TYPES = frozenset({"Texture2D", "TextAsset", "Font"})


def unity_release(version: str) -> str | None:
    """Return the major.minor of a Unity version ("2021.3.56f2" gives "2021.3"), or None when it has none."""
    match = re.match(r"^(\d+\.\d+)(?:\.|$)", version or "")
    return match[1] if match else None


def unity_releases(bundle: Bundle) -> set[str]:
    """Return the Unity releases (major.minor) of the serialized files a loaded bundle holds."""
    found: set[str] = set()

    def walk(file) -> None:
        if hasattr(file, "target_platform"):  # a serialized file, as integrity.metadata finds them
            found.add(unity_release(file.unity_version) or file.unity_version)
            return
        for child in getattr(file, "files", {}).values():
            walk(child)

    walk(bundle.env)
    return found


def _shared_fields_equal(mine, theirs) -> bool:
    """Compare two type trees on the fields both have; a field only one release has is left out."""
    if isinstance(mine, dict) and isinstance(theirs, dict):
        return all(_shared_fields_equal(mine[key], theirs[key]) for key in mine.keys() & theirs.keys())
    if isinstance(mine, list) and isinstance(theirs, list):
        return len(mine) == len(theirs) and all(map(_shared_fields_equal, mine, theirs))
    return mine == theirs


def _object_release(obj: Obj, bundle: Bundle) -> str | None:
    version = getattr(getattr(obj, "assets_file", None), "unity_version", "")
    if isinstance(version, str) and version:
        return unity_release(version) or version
    releases = unity_releases(bundle)
    return next(iter(releases)) if len(releases) == 1 else None


def _can_upgrade(kind: str, source: str | None, target: str | None) -> bool:
    return (
        kind in tree_upgrade.UPGRADE_TYPES
        and source == "2021.3"
        and bool(target and re.fullmatch(r"6000\.\d+", target))
    )


def _release_mismatch(source: Bundle, obj: Obj, name: str, version: str | None, target: str | None) -> ServiceError:
    return ServiceError(
        "mod_unity_asset_mismatch",
        file=source.path.name,
        type=obj.type.name,
        name=name,
        version=version or "",
        expected=target or "",
    )


def _apply_target_patch(
    target: Bundle,
    patches: Patch,
    raw_sources: dict[AssetKey, tuple[Bundle, Obj]],
    strategy: MatchStrategy,
    anim_check: AnimCheckOptions | None,
    log: LogFunc,
) -> PatchResult:
    """Apply the engine's patch and handle cross-release raw matches through their typetrees."""
    if not raw_sources:
        return target.apply_patch(patches, strategy, anim_check)
    key_of = Bundle._get_key_func(strategy)
    remaining = set(raw_sources)
    patch = patches.copy()
    foreign = []
    for counterpart in target.env.objects:
        if not remaining:
            break
        if counterpart.type not in REPLACEABLE_ASSET_TYPES or (key := key_of(counterpart)) not in remaining:
            continue
        remaining.remove(key)
        source, obj = raw_sources[key]
        version = _object_release(obj, source)
        expected = _object_release(counterpart, target)
        if version != expected:
            foreign.append((key, counterpart, version, expected, patch.pop(key)))

    # The converted resources must have distinct names from companions installed by the raw patch too.
    result = target.apply_patch(patch, strategy, anim_check)
    if not foreign:
        return result
    upgraded, retained, applied_logs = [], [], []
    for key, counterpart, version, expected, content in foreign:
        source, obj = raw_sources[key]
        kind = obj.type.name
        name = obj.peek_name() or str(key)
        try:
            if obj.type != counterpart.type:
                raise tree_upgrade.TreeUpgradeError("The counterpart has a different asset type")
            if not isinstance(content, bytes):
                raise tree_upgrade.TreeUpgradeError("The source patch has no serialized object bytes")
            mine = obj.read_typetree()
            theirs = counterpart.read_typetree()
            if _shared_fields_equal(mine, theirs) and (
                kind != "Mesh" or tree_upgrade.same_mesh_stream(mine, content, theirs, target)
            ):
                retained.append(key)
                log(f"  ⏭️ {t('log.replace_skipped_same_content', type=kind, name=name)}")
            elif _can_upgrade(kind, version, expected):
                tree_upgrade.write_upgrade(mine, content, counterpart, target, log)
                upgraded.append(key)
                applied_logs.append(f"[{kind}] {name} (key: {key})")
                log(f"  ✅ {t('log.replace_applied', type=kind, name=name)}")
            else:
                raise tree_upgrade.TreeUpgradeError("The Unity release pair does not support typetree upgrades")
        except Exception as error:
            raise _release_mismatch(source, obj, name, version, expected) from error
    return result._replace(
        applied_count=result.applied_count + len(upgraded),
        skipped_count=result.skipped_count + len(retained),
        applied_logs=[*result.applied_logs, *applied_logs],
        matched_keys=[*result.matched_keys, *upgraded, *retained],
        skipped_keys=[*(result.skipped_keys or ()), *retained],
    )


def _read_back(path: Path, official: Bundle, asset_types: set[ReplaceAssetType]) -> None:
    """Load a saved part once more and read every object of the replaced types through its type tree.

    Raises:
        ServiceError: The part does not load (``bundle_invalid``), changed its Unity release
            (``mod_unity_version_mismatch``) or holds an object that does not read back
            (``required_asset_apply_failed``).
    """
    saved = _load(path, no_log)
    if saved is None:
        raise ServiceError("bundle_invalid", file=path.name)
    releases, expected = unity_releases(saved), unity_releases(official)
    if releases != expected:
        raise ServiceError(
            "mod_unity_version_mismatch",
            file=path.name,
            version=", ".join(sorted(releases)),
            expected=", ".join(sorted(expected)),
        )
    types = None if "ALL" in asset_types else set(asset_types)
    for obj in saved.env.objects:
        if obj.type in REPLACEABLE_ASSET_TYPES and (types is None or obj.type.name in types):
            try:
                obj.read_typetree()
            except Exception as error:
                name = obj.peek_name() or str(obj.path_id)
                raise ServiceError(
                    "required_asset_apply_failed", file=path.name, name=name, error=str(error)
                ) from error


def _load(path: Path, log: LogFunc) -> Bundle | None:
    """Load a bundle, refusing one of a newer archive format by name rather than as unreadable."""
    refuse_unsupported(path)
    return Bundle.load(path, log)


class _MissingAssetStream(ValueError):
    """A required stream is missing or its requested byte range is invalid."""


class UpdateScope:
    """Track new-release counterparts for preflight and execution through Bundle.extract_patch."""

    def __init__(self, targets: list[Bundle], strategy: MatchStrategy, asset_types: set[str]):
        """Index replaceable target assets by the selected matching strategy.

        Args:
            targets: Loaded official bundles receiving the transferred assets.
            strategy: Engine key strategy used for both source and target assets.
            asset_types: Eligible Unity class names, or ALL for every replaceable class.
        """
        self._key_of = Bundle._get_key_func(strategy)
        self.counterparts: dict[AssetKey, list[tuple[Bundle, object]]] = {}
        self._excluded_clips: set[AssetKey] = set()
        self.not_transferred: set[AssetKey] = set()
        self.unchanged: set[AssetKey] = set()
        self.seen: set[AssetKey] = set()  # every source key asked about, transferred or not
        for target in targets:
            for obj in target.env.objects:
                if obj.type in REPLACEABLE_ASSET_TYPES and ("ALL" in asset_types or obj.type.name in asset_types):
                    key = self._key_of(obj)
                    if key is not None:
                        self.counterparts.setdefault(key, []).append((target, obj))

    def includes(self, key: AssetKey) -> bool:
        """Record a source key as seen and report whether it can transfer to a target counterpart."""
        if key is not None:
            self.seen.add(key)
        if key in self.counterparts and key not in self._excluded_clips:
            return True
        self.not_transferred.add(key)
        return False

    def _keep_official_clips(self, source: Bundle, stale: set[pointers.Pointer]) -> None:
        """Keep official counterparts for clips that would transfer a known stale PPtr as raw bytes."""
        self._excluded_clips = (
            {
                self._key_of(obj)
                for obj in source.env.objects
                if obj.type.name == "AnimationClip" and not stale.isdisjoint(pointers.object_pointers(obj))
            }
            if stale
            else set()
        )

    @staticmethod
    def serialized(bundle: Bundle, obj) -> tuple[bytes, dict[str, bytes]]:
        """Read serialized object bytes and the external stream ranges used by that object.

        Args:
            bundle: Loaded bundle containing the object and its stream companions.
            obj: Unity object reader whose raw bytes and stream metadata are compared.

        Raises:
            ValueError: A required stream is missing or its requested byte range is invalid.
        """
        raw = obj.get_raw_data()
        companions = collect_stream_companions(raw, bundle.env.file)
        if companions is None:
            raise _MissingAssetStream(t("webui.error.required_asset_stream_missing"))
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
                raise _MissingAssetStream(t("webui.error.required_asset_stream_missing"))
            companions[name] = payload[offset : offset + size]
        return raw, companions

    def skip_unchanged(self, source: Bundle, obj, key: AssetKey) -> bool:
        """Mark an asset unchanged only when its bytes equal every target counterpart.

        Args:
            source: Loaded source bundle supplying the object's stream payloads.
            obj: Source object reader to compare without conversion.
            key: Existing counterpart key whose objects must all match.

        Returns:
            Whether equality was proven; unreadable data leaves extraction to report its failure.
        """
        try:
            content = self.serialized(source, obj)
        except _MissingAssetStream as error:
            if obj.type.name == "Mesh":
                version = _object_release(obj, source)
                for target, counterpart in self.counterparts[key]:
                    expected = _object_release(counterpart, target)
                    if counterpart.type == obj.type and _can_upgrade(obj.type.name, version, expected):
                        raise _release_mismatch(source, obj, obj.peek_name() or str(key), version, expected) from error
            return False
        except Exception:
            return False
        try:
            if all(content == self.serialized(target, counterpart) for target, counterpart in self.counterparts[key]):
                self.unchanged.add(key)
                return True
        except Exception:
            pass  # Unproven equality must retain the original extraction failure.
        return False

    @property
    def target_only(self) -> set[AssetKey]:
        """Return target keys of replaced types that no source asset matched."""
        return set(self.counterparts) - self.seen


@dataclass
class RebuildResult:
    """Describe rebuild success, output files, and asset-transfer results.

    ``all_unchanged``: every matched asset already equals the target's, nothing was
    written. ``no_match``: a failure because no source asset has a counterpart in the targets (a target that holds
    none of the replaced types, or none the sources carry): the targets take nothing from the sources. The asset
    lists hold the keys as text, sorted: ``matched`` found their counterpart in a target (written
    or equal), ``unchanged`` equal their counterpart, ``not_transferred`` have none, ``target_only`` are the
    targets' assets of the replaced types that the sources lack. A prepared patch reports matches only.
    """

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
    no_match: bool = False


def _names(keys) -> list[str]:
    return sorted(map(str, keys))


def _failed(message: str, log: LogFunc, **fields) -> RebuildResult:
    """A failure whose message the user's log has not shown yet."""
    log(f"❌ {message}")
    return RebuildResult(False, message, **fields)


@toggle_timing.stage("asset_rebuild")
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
    stale_pointers: Mapping[Path, set[pointers.Pointer]] | None = None,
    read_back: bool = False,
) -> RebuildResult:
    """Transfer source assets into current target parts and save changed parts under their names.

    Every target is validated before any output is saved.

    Args:
        source_paths: Mod bundles supplying assets for extraction.
        target_paths: Current-release bundles whose structure and untouched assets are retained.
            For CRC-corrected hash names, these must be verified official copies.
        output_dir: Existing directory receiving changed target bundles under their original names.
        asset_types_to_replace: Unity asset classes eligible for transfer.
        save_options: Compression and CRC settings for saved bundles.
        spine_options: Skeleton conversion settings, or None for engine defaults.
        skip_unchanged: Whether targets with only equal or skipped assets need no output file.
        match_strategy: Engine key strategy pairing source assets with target counterparts.
        anim_check: Animation comparisons passed to each target's patch application; None skips them.
        log: Receives user-facing operation messages.
        prepared_patch: Extracted patch entries to seed the transfer; use empty source_paths
            to bypass extraction. None builds a scoped patch from sources. With a supplied
            patch, each target must accept every resulting entry.
        on_step: Receives extract, replace, save, and crc phase names; None disables reporting.
        stale_pointers: Known dangling references by source file. Clips carrying them keep their official counterparts.
        read_back: Whether every saved part is loaded again and its replaced objects read (``_read_back``), so a
            write the App cannot read back fails the rebuild.

    Returns:
        Output pairs and transfer details, or success=False with a message and any named ServiceError.
    """
    try:
        log(f"\n--- {t('log.section.extracting_patches')} ---")
        if on_step:
            on_step("extract")
        patches: Patch = dict(prepared_patch or {})
        raw_sources: dict[AssetKey, tuple[Bundle, Obj]] = {}
        key_of = Bundle._get_key_func(match_strategy)
        skel_conflicts: list[SkelVersionConflict] = []
        targets = []
        for path in target_paths:
            target = _load(path, log)
            if target is None:
                return _failed(t("message.load_failed"), log)
            targets.append(target)
        scope = UpdateScope(targets, match_strategy, asset_types_to_replace) if prepared_patch is None else None
        transfer_clips = bool({"ALL", "AnimationClip"} & asset_types_to_replace)

        for src in source_paths:
            src_bundle = _load(src, log)
            if not src_bundle:
                return _failed(t("message.load_failed"), log)
            if scope is not None and stale_pointers and transfer_clips:
                scope._keep_official_clips(src_bundle, stale_pointers.get(src, set()))
            patch, conflicts = src_bundle.extract_patch(
                asset_types_to_replace, match_strategy, spine_options, **({"scope": scope} if scope is not None else {})
            )
            for key in patch:
                raw_sources.pop(key, None)
            for obj in src_bundle.env.objects:
                if (
                    obj.type in REPLACEABLE_ASSET_TYPES
                    and obj.type.name not in TREE_COPIED_TYPES
                    and ("ALL" in asset_types_to_replace or obj.type.name in asset_types_to_replace)
                    and (key := key_of(obj)) in patch
                ):
                    raw_sources[key] = (src_bundle, obj)
            patches.update(patch)
            skel_conflicts.extend(conflicts)

        if skel_conflicts:
            msg = format_skel_conflicts(skel_conflicts)
            log(f"❌ {msg}")
            return RebuildResult(False, msg)

        required_keys = set(patches) - {"__mode__"}
        unchanged_keys = scope.unchanged if scope is not None else set()
        not_transferred = _names(scope.not_transferred) if scope is not None else []
        target_only = _names(scope.target_only) if scope is not None else []
        if not required_keys and not unchanged_keys:
            # Not logged here: a caller may keep the targets as they are.
            return RebuildResult(False, t("message.mod_update.no_assets_extracted"), no_match=True)
        warning = t("webui.import.notTransferred", names=", ".join(not_transferred)) if not_transferred else ""
        if warning:
            log(warning)

        log(f"  > {t('log.mod_update.pool_built', count=len(patches))}")

        log(f"\n--- {t('log.section.applying_to_targets')} ---")
        file_pairs: list[FilePair] = []
        anim_diffs: dict[str, set[str]] = {}
        failed_targets = False
        matched_keys: set[AssetKey] = set()
        applied_keys: set[AssetKey] = set()
        skipped_keys: set[AssetKey] = set()
        pending_saves = []

        for tgt, tgt_bundle in zip(target_paths, targets):
            retained = (
                {key for key in unchanged_keys if any(target is tgt_bundle for target, _ in scope.counterparts[key])}
                if scope is not None
                else set()
            )

            if on_step:
                on_step("replace")
            result = _apply_target_patch(tgt_bundle, patches, raw_sources, match_strategy, anim_check, log)
            if prepared_patch is not None and result.matched_count != len(patches):
                return _failed(
                    t(
                        "webui.error.atlas_remap_target_mismatch",
                        file=tgt.name,
                        matched=result.matched_count,
                        expected=len(patches),
                    ),
                    log,
                )
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
        # Validate every target first to avoid publishing an incomplete mod.
        for tgt, tgt_bundle, result in pending_saves:
            output_name = tgt.name.removesuffix(".backup") if name_hash(tgt.name) else tgt.name
            output_path = output_dir / output_name
            options = target_save_options(save_options, output_path.name, tgt)
            save_ok, save_message = (
                tgt_bundle.save(output_path, options, on_step) if on_step else tgt_bundle.save(output_path, options)
            )
            if save_ok and read_back:
                _read_back(output_path, tgt_bundle, asset_types_to_replace)
            if save_ok:
                file_pairs.append(FilePair(output_path, tgt))
                log(f"  ✅ {t('log.mod_update.target_processed', name=tgt.name, applied=result.applied_count)}")
            else:
                log(f"  ❌ {t('log.file.save_failed', path=output_path, error=save_message)}")
                failed_targets = True

        final_anim_diffs: AnimDiffMap = {k: sorted(v) for k, v in anim_diffs.items()}

        # A partial save is a failed mod, even when another part was unchanged or saved successfully.
        if failed_targets:
            return _failed(t("message.mod_update.no_targets_processed"), log, anim_diffs=final_anim_diffs)

        report = {
            "matched": _names(matched_keys | unchanged_keys),
            "unchanged": _names((unchanged_keys | skipped_keys) - applied_keys),
            "not_transferred": not_transferred,
            "target_only": target_only,
        }
        if not file_pairs:
            if (matched_keys or unchanged_keys) and skip_unchanged:
                return RebuildResult(True, "", anim_diffs=final_anim_diffs, all_unchanged=True, **report)
            return _failed(t("message.mod_update.no_targets_processed"), log, anim_diffs=final_anim_diffs)

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
