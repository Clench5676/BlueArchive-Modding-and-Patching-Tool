"""Working on a mod's Spine files: atlas remaps, redrawn pages, old rigs and the remap review."""

from __future__ import annotations

import base64
import io
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from bmpt.spine import atlas_remap, rebake
from bmpt.core import devmode
from bmpt.engine import (
    Bundle,
    LogFunc,
    no_log,
)
from ...core.errors import ServiceError
from ...core.openfiles import temporary_directory
from .. import fingerprint, integrity, rig_archive
from ..rebuild import rebuild
from ..judge import (
    core_of,
    date_of,
    kind_of_file,
)
from ..models import (
    Pin,
    RemapResult,
)
from ..storage import atomic_copy
from .base import _mod_writer, _serialized


class EditingMixin:
    """Working on a mod's Spine files: atlas remaps, redrawn pages, old rigs and the remap review."""

    def _remap_source_atlases(
        self,
        paths: list[Path],
        selected: dict[str, bytes],
        official: dict[str, bytes],
        available: list[Path] | None = None,
    ) -> dict[str, bytes]:
        """Fill absent source atlases only from verified official copies of the mod's date."""
        missing = official.keys() - atlas_remap.pair_atlases(selected, official).keys()
        if not missing:
            return selected
        core = core_of(paths[0].name)
        dates = {date_of(path.name) for path in paths} - {""}
        fallback = {}
        candidates = {name: self.root / "originals" / name for name in self._originals()}
        candidates.update({path.name: path for path in available or ()})
        for name, path in sorted(candidates.items()):
            if core_of(name) != core or date_of(name) not in dates:
                continue
            if not self._is_official(path):
                raise ServiceError("original_invalid", file=name)
            for asset, text in integrity.inspect(path).atlases.items():
                if asset in fallback and fallback[asset] != text:
                    raise atlas_remap.RemapError("atlas_remap_asset_ambiguous", file=name, name=asset)
                fallback[asset] = text
        pairs = atlas_remap.pair_atlases(fallback, {name: official[name] for name in missing})
        result = dict(selected)
        result.update({name: fallback[source] for name, source in pairs.items()})
        if not result:
            raise atlas_remap.RemapError(
                "atlas_remap_source_missing", file=", ".join(sorted(missing)), date=", ".join(sorted(dates)) or "—"
            )
        return result

    def _remap_view(
        self, record: dict, paths: list[Path], targets: list[Path], originals: dict[Path, Path], pin: Pin
    ) -> dict:
        """Gather what a remap starts from.

        That is the mod's own inputs (``mod``), the official ones (``official``, from ``originals``: per target its
        verified official copy), the official files with the mod's over them (``view``), the official atlases the mod
        paints without carrying them (``mixed``), and why a remap is wanted: those whose page the texture does not fit
        (``misfit``), those repacked since its textures were drawn on them (``layout``, ``fingerprint.changed_layouts``)
        and the mod's own atlases that give way to the official rig, with its skeleton (``rerig``,
        ``atlas_remap.rerigged``). ``dated``: the rig of the mod's date of the misfit atlases the fingerprint does not
        record (``_dated_rig``).
        """
        mod = atlas_remap.read_inputs(paths)
        official = atlas_remap.read_inputs(list(originals.values()))
        replaced = self._replaced_parts({path.name: path for path in paths}, targets, pin)
        others = [original for target, original in originals.items() if target not in replaced]
        view = atlas_remap.read_inputs(others + paths)
        mixed = {
            name: data
            for name, data in view.atlases.items()
            if name not in mod.atlases
            and any(atlas_remap.texture_key(page) in mod.textures for page in atlas_remap.parse_atlas(data, name).pages)
        }
        sizes = {name: image.size for name, image in view.textures.items()}
        misfit = {name for name, data in mixed.items() if atlas_remap.page_size_mismatch({name: data}, sizes)}
        layout = fingerprint.changed_layouts(self._read_built_against(record), mixed)
        if kind_of_file(paths[0].name) == "spr":
            # the rig the archive keeps for textures no record covers, as compatibility finds it
            unrecorded = {
                name: data
                for name, data in mixed.items()
                if not isinstance((layout.get(name) or {}).get("skeleton"), str)
            }
            layout = {
                **layout,
                **self._archived_rigs(
                    paths,
                    unrecorded,
                    lambda: {key: rig_archive.alpha_mask(image) for key, image in official.textures.items()},
                ),
            }
        gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
        rerig = atlas_remap.rerigged(
            gains, mod.atlases, mod.skeletons, official.atlases, official.skeletons, mod.textures
        )
        unrecorded = {name for name in misfit if not isinstance((layout.get(name) or {}).get("skeleton"), str)}
        dated = self._dated_rig(paths, unrecorded, official.atlases, originals.values())
        return {
            "mod": mod,
            "official": official,
            "view": view,
            "mixed": mixed,
            "misfit": misfit,
            "layout": layout,
            "rerig": rerig,
            "dated": dated,
        }

    @staticmethod
    def _rebake_plan(inputs: dict) -> dict[str, bytes]:
        """Plan the portrait atlases the world-space rebake bakes (``bmpt.spine.rebake``).

        Each comes with the atlas the mod's textures were drawn on, chosen as ``rig-geometry.js`` ``forRemap`` chooses
        it (the mod's own, else the recorded one: the fingerprint's, or the official copy of the mod's date, else the
        official one). The atlases are the mod's own atlases that give way to the official rig, and the official ones it
        paints without carrying them where they were repacked under a recorded rig or the texture does not fit. An atlas
        the mod carries otherwise keeps its own rig and textures.
        """
        mod, official = inputs["mod"], inputs["official"]
        carried = atlas_remap.pair_atlases(mod.atlases, official.atlases)
        plan = {}
        for name, text in official.atlases.items():
            recorded = inputs["layout"].get(name) or {}
            if name in inputs["rerig"]:
                plan[name] = mod.atlases[carried[name]]
            elif name in carried:
                continue
            elif isinstance(recorded.get("skeleton"), str):
                plan[name] = recorded["text"].encode("utf-8")
            elif name in inputs["misfit"]:
                dated = inputs["dated"].get(name)
                plan[name] = dated["text"].encode("utf-8") if dated else text
        return plan

    @staticmethod
    def _posted_rigs(geometry: Any, name: str, source: bytes, target: bytes) -> tuple:
        """Check the posted geometry of an atlas's two rigs (``rig-geometry.js`` ``forRemap``).

        It is checked against the atlases read here (``rebake.rig``), and returned with the two atlases.
        """
        posted = geometry.get(name) if isinstance(geometry, dict) else None
        if not isinstance(posted, dict):
            raise atlas_remap.RemapError("bad_params", key="geometry")
        source_atlas, target_atlas = atlas_remap.parse_atlas(source, name), atlas_remap.parse_atlas(target, name)
        return (
            rebake.rig(posted.get("source"), source_atlas),
            rebake.rig(posted.get("target"), target_atlas),
            source_atlas,
            target_atlas,
        )

    @staticmethod
    def _rebake_pages(
        inputs: dict, source: atlas_remap.Atlas, target: atlas_remap.Atlas, done: dict | None = None
    ) -> tuple[dict, dict]:
        """Give the mod's pages of the source atlas and the official pages of the target one, keyed by texture key.

        A page an earlier atlas baked (``done``) is taken as it came out.
        """
        mod, official = inputs["mod"], inputs["official"]
        source_pages = {
            key: mod.textures[key] for key in map(atlas_remap.texture_key, source.pages) if key in mod.textures
        }
        official_pages = {}
        for page in target.pages:
            key = atlas_remap.texture_key(page)
            if key not in official.textures:
                raise atlas_remap.RemapError("atlas_remap_page_missing", file=target.file, page=page)
            official_pages[key] = (done or {}).get(key, official.textures[key])
        return source_pages, official_pages

    def _rebake(self, inputs: dict, plan: dict[str, bytes], geometry: Any) -> tuple[dict, dict]:
        """Build the texture patch of a portrait remap.

        Every planned atlas is baked (``rebake.bake``), every other page is as the official files with the mod's over
        them have it.
        """
        official, view = inputs["official"], inputs["view"]
        report = {"moved": [], "added": [], "official": [], "dropped": [], "upscaled": [], "warnings": []}
        baked: dict[str, Any] = {}
        for name, source_text in plan.items():
            source, target, source_atlas, target_atlas = self._posted_rigs(
                geometry, name, source_text, official.atlases[name]
            )
            source_pages, official_pages = self._rebake_pages(inputs, source_atlas, target_atlas, baked)
            pages, summary = rebake.bake(source, target, source_pages, official_pages)
            baked.update(pages)
            for key in ("moved", "official"):
                report[key].extend(f"{name} - {region}" for region in summary[key])
        patch = {
            official.keys[key]: baked.get(key, view.textures.get(key, image))
            for key, image in official.textures.items()
            if key in official.keys
        }
        report["partial"], report["unchanged"] = bool(report["official"]), False
        return patch, report

    @_serialized
    def redrawn(self, mod_id: str, geometry: Any, pin: Pin | None = None) -> dict:
        """Compare rest-picture overlap to detect a redrawn official portrait without changing files.

        Args:
            mod_id: Stored portrait mod ID whose painted rig is compared with current official art.
            geometry: Per-atlas source/target posed rigs from rig-geometry.js forRemap;
                required for portrait comparisons.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            redrawn and the smallest rest-picture overlap, rounded to four decimals;
            nonportrait mods return False and 1.0.
        """
        pin = self._current(pin)
        record = self._record(mod_id)
        paths = self._paths(record)
        if kind_of_file(paths[0].name) != "spr":
            return {"redrawn": False, "overlap": 1.0}
        if not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])
        originals = {target: self._official_for(target, pin) for target in targets}
        lowest = 1.0
        try:
            inputs = self._remap_view(record, paths, targets, originals, pin)
            for name, source_text in self._rebake_plan(inputs).items():
                source, target, source_atlas, target_atlas = self._posted_rigs(
                    geometry, name, source_text, inputs["official"].atlases[name]
                )
                lowest = min(
                    lowest, rebake.overlap(source, target, *self._rebake_pages(inputs, source_atlas, target_atlas))
                )
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        except (ValueError, KeyError, EOFError, OSError) as error:
            raise ServiceError(
                "atlas_remap_invalid", file=", ".join(path.name for path in paths), reason=str(error)
            ) from error
        return {"redrawn": lowest < rebake.REDRAWN_BELOW, "overlap": round(lowest, 4)}

    def _dated_rig(
        self, paths: list[Path], names: Iterable[str], official: dict[str, bytes], available: Iterable[Path] = ()
    ) -> dict[str, dict]:
        """Find the rig a texture-only mod's textures were drawn for when its fingerprint does not record it.

        Of the official atlases ``names``, it takes those a verified official copy of the mod's date holds (one the
        library kept, or one of ``available`` of that date), each as a ``fingerprint.painted`` entry: that copy's atlas
        ``text`` and, where it has one, the base64 ``skeleton`` of the same stem.
        """
        names = set(names)
        if not names or not paths:
            return {}
        core = core_of(paths[0].name)
        dates = {date_of(path.name) for path in paths} - {""}
        candidates = {name: self.root / "originals" / name for name in self._originals()}
        candidates.update({path.name: path for path in available})
        atlases: dict[str, bytes] = {}
        skeletons: dict[str, bytes] = {}
        for name, path in sorted(candidates.items()):
            if core_of(name) != core or date_of(name) not in dates or not self._is_official(path):
                continue
            contents = integrity.inspect(path)
            atlases.update(contents.atlases)
            skeletons.update(contents.skeletons)
        pairs = atlas_remap.pair_atlases(atlases, {name: official[name] for name in names if name in official})
        drawn_by = atlas_remap.pair_atlases(skeletons, dict.fromkeys(pairs))
        return {
            name: {
                "text": atlases[source].decode("utf-8-sig"),
                **(
                    {"skeleton": base64.b64encode(skeletons[drawn_by[name]]).decode("ascii")}
                    if name in drawn_by
                    else {}
                ),
            }
            for name, source in pairs.items()
        }

    @_serialized
    def remap(
        self,
        mod_id: str,
        log: LogFunc = no_log,
        progress: Callable[[dict], None] | None = None,
        pin: Pin | None = None,
        geometry: Any = None,
    ) -> dict:
        """Prepare an atlas-repair preview in temporary storage for acceptance with accept_remap.

        A portrait's pages are
        baked through world space from ``geometry`` (``rig-geometry.js`` ``forRemap``, required); other kinds move
        regions by name onto a page size the texture does not fit.

        Args:
            mod_id: Unrepaired stored mod ID whose textures need the current official layout.
            log: Receives user-facing operation messages.
            progress: Receives per-target phase dictionaries; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
            geometry: Per-atlas posed source/target rigs from rig-geometry.js forRemap;
                required for portraits, and None is acceptable for other kinds.

        Returns:
            Temporary preview token, repair report, and comparison, or unchanged when no repair is needed.
        """
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repaired_from") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        self._remap_previews = {
            token: preview
            for token, preview in self._remap_previews.items()
            if preview["expires"] > time.monotonic() and preview["source"] != mod_id
        }
        paths = self._paths(record)
        portrait = kind_of_file(paths[0].name) == "spr"
        if portrait and not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                step("extract")
                inputs = self._remap_view(record, paths, targets, originals, pin)
                mod, official, original_view = inputs["mod"], inputs["official"], inputs["view"]
                rerig = inputs["rerig"] if portrait else {}
                given = {
                    source: name
                    for name, source in atlas_remap.pair_atlases(mod.atlases, official.atlases).items()
                    if name in rerig
                }
                given_skeletons = {
                    source: name
                    for name, source in atlas_remap.pair_atlases(mod.skeletons, official.skeletons).items()
                    if name in rerig.values()
                }
                if portrait:
                    plan = self._rebake_plan(inputs)
                    if not plan:
                        return {"unchanged": True}
                    patch, report = self._rebake(inputs, plan, geometry)
                    for source, name in sorted(given_skeletons.items()):
                        if not atlas_remap.same_rig(mod.skeletons[source], official.skeletons[name]):
                            report["warnings"].append({"key": "rig", "name": name})
                    report["partial"] = bool(report["warnings"] or report["official"])
                else:
                    mixed = inputs["mixed"]
                    if not inputs["misfit"]:
                        return {"unchanged": True}
                    mod.atlases = {
                        **mod.atlases,
                        **self._remap_source_atlases(paths, {}, mixed, list(originals.values())),
                    }
                    target_inputs = replace(official, atlases=original_view.atlases, skeletons=original_view.skeletons)
                    patch, report = atlas_remap.prepare_patch(mod, target_inputs)
                # Preserve the winning mod skeleton and atlas when porting the texture patch, but for the ones
                # that give way to the official rig.
                text_patch = {}
                for path in paths:
                    bundle = Bundle.load(path)
                    extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
                    text_patch.update(
                        {
                            key: value
                            for key, value in extracted.items()
                            if key.name not in given and key.name not in given_skeletons
                        }
                    )
                target_keys = set().union(
                    *(Bundle.load(path).get_asset_keys("cont_name_type") for path in originals.values())
                )
                if text_patch.keys() - target_keys:
                    raise atlas_remap.RemapError(
                        "atlas_remap_target_mismatch",
                        file=paths[0].name,
                        matched=len(text_patch.keys() & target_keys),
                        expected=len(text_patch),
                    )
                patch.update(text_patch)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError(
                    "atlas_remap_invalid", file=", ".join(path.name for path in paths), reason=str(error)
                ) from error
            if report["unchanged"]:
                return {"unchanged": True, "report": report}
            return self._remap_review(
                mod_id, identity, paths, originals, official, original_view, output, patch, report, pin, log, step
            )

    @_serialized
    def old_rig(
        self, mod_id: str, log: LogFunc = no_log, progress: Callable[[dict], None] | None = None, pin: Pin | None = None
    ) -> dict:
        """Prepare a texture-only portrait repair using the complete rig its textures were drawn for.

        Use the rig as a
        whole (``atlas_remap.old_rig_plan`` over the recorded rig, ``_remap_view`` ``layout``, or the official copy
        of the mod's date, ``dated``): that atlas and skeleton with the mod's textures, ported into the current
        official parts. Only accept_remap can add it to the library. For a portrait the game drew anew, whose pixels
        the rebake cannot move (``rebake.REDRAWN_BELOW``). Refused with ``old_rig_animations`` when an official
        skeleton has animations the old one lacks (the game calls them by name), and with ``old_rig_unavailable``
        when a recorded rig cannot be set up.

        Args:
            mod_id: Unrepaired stored portrait mod ID to combine with its recorded or dated official rig.
            log: Receives user-facing operation messages.
            progress: Receives per-target phase dictionaries; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Temporary preview accepted only through accept_remap.

        Raises:
            ServiceError: The rig is unavailable (old_rig_unavailable), lacks required official
                animations (old_rig_animations), or the source or target changed.
        """
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repaired_from") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        self._remap_previews = {
            token: preview
            for token, preview in self._remap_previews.items()
            if preview["expires"] > time.monotonic() and preview["source"] != mod_id
        }
        paths = self._paths(record)
        if kind_of_file(paths[0].name) != "spr":
            raise ServiceError("old_rig_unavailable")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", variant=record["target"])

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                step("extract")
                inputs = self._remap_view(record, paths, targets, originals, pin)
                mod, official = inputs["mod"], inputs["official"]
                plan = atlas_remap.old_rig_plan(
                    {**inputs["layout"], **inputs["dated"]},
                    official.atlases,
                    official.skeletons,
                    mod.skeletons,
                    mod.textures,
                    official.keys,
                )
                patch = self._old_rig_patch(paths, originals, official, mod, plan) if plan.available else {}
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError(
                    "atlas_remap_invalid", file=", ".join(path.name for path in paths), reason=str(error)
                ) from error
            if plan.animations:
                names = dict.fromkeys(name for missing in plan.animations.values() for name in missing)
                raise ServiceError("old_rig_animations", names=", ".join(names))
            if not plan.available:
                raise ServiceError("old_rig_unavailable")
            report = {
                "moved": [],
                "added": [],
                "official": [],
                "dropped": [],
                "upscaled": [],
                "warnings": [],
                "rigs": sorted(Path(name).stem for name in plan.atlases),
                "partial": False,
                "unchanged": False,
            }
            return self._remap_review(
                mod_id, identity, paths, originals, official, inputs["view"], output, patch, report, pin, log, step
            )

    @staticmethod
    def _old_rig_patch(
        paths: list[Path],
        originals: dict[Path, Path],
        official: atlas_remap.Inputs,
        mod: atlas_remap.Inputs,
        plan: atlas_remap.OldRig,
    ) -> dict:
        """Build the patch of an old-rig set (``old_rig``).

        It holds the mod's own text assets, the plan's atlases and skeletons over the official ones of their names, and
        the mod's own textures, all by ``cont_name_type``.
        """
        patch: dict = {}
        texts: dict[str, Any] = {}
        for original in originals.values():
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            for key in bundle.get_asset_keys("cont_name_type"):
                if key.type == "TextAsset":
                    texts.setdefault(key.name, key)
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path.name)
            extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
            patch.update(extracted)
        if patch.keys() - set(texts.values()):
            raise atlas_remap.RemapError(
                "atlas_remap_target_mismatch",
                file=paths[0].name,
                matched=len(patch.keys() & set(texts.values())),
                expected=len(patch),
            )
        for name, (atlas, skeleton, data) in plan.atlases.items():
            patch[texts[name]] = atlas
            patch[texts[skeleton]] = data
        patch.update({official.keys[key]: image for key, image in mod.textures.items() if key in official.keys})
        return patch

    def _remap_review(
        self,
        mod_id: str,
        identity: Any,
        paths: list[Path],
        originals: dict[Path, Path],
        official: atlas_remap.Inputs,
        original_view: atlas_remap.Inputs,
        output: Path,
        patch: dict,
        report: dict,
        pin: Pin,
        log: LogFunc,
        step: Callable[..., None],
    ) -> dict:
        """Build the review of a prepared repair.

        It is the official parts (``originals``) with ``patch`` ported in by ``cont_name_type`` and CRC fixed as the
        settings ask, in ``output``; kept in temporary storage under a token that only accept_remap can add to the
        library. Every Texture2D of a part takes the patch's image of its texture key where the patch has one.
        ``original_view``: the official files with the mod's over them, the review's "before".
        """
        generated = {}
        for i, (target, original) in enumerate(originals.items(), 1):
            step("replace", i)
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            keys = bundle.get_asset_keys("cont_name_type")
            local_patch = {key: value for key, value in patch.items() if key in keys}
            local_patch.update(
                {
                    key: patch[official.keys[atlas_remap.texture_key(key.name)]]
                    for key in keys
                    if key.type == "Texture2D" and official.keys.get(atlas_remap.texture_key(key.name)) in patch
                }
            )
            if local_patch:
                result = rebuild(
                    [],
                    [original],
                    output,
                    {"Texture2D"},
                    self.settings.save_options(self.settings.crc_needed(target)),
                    match_strategy="cont_name_type",
                    log=log,
                    prepared_patch=local_patch,
                    on_step=lambda key: step(key, i),
                )
                if not result.success or len(result.file_pairs) != 1:
                    raise ServiceError("mod_update_failed", id=mod_id)
            else:
                atomic_copy(original, output / target.name)
            generated[target.name] = output / target.name
        if identity != self._identity({path.name: path for path in paths}):
            raise ServiceError("entry_changed")
        comparison = self._compare_paths(mod_id, list(generated.values()), paths)
        built_against = self._built_against(generated, pin, "remap")
        repaired = atlas_remap.read_inputs(list(generated.values()))

        def png(image) -> str | None:
            if image is None:
                return None
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")

        pages = [
            {
                "name": name,
                "original": png(original_view.textures.get(name)),
                "repaired": png(repaired.textures.get(name)),
            }
            for name in sorted(original_view.textures.keys() | repaired.textures.keys())
        ]

        def scenes(inputs) -> list[dict]:
            atlas_by_stem = {Path(name).stem.lower(): data for name, data in inputs.atlases.items()}
            result = []
            for name, data in inputs.skeletons.items():
                atlas = atlas_by_stem.get(Path(name).stem.lower())
                if atlas is None and len(inputs.atlases) == 1:
                    atlas = next(iter(inputs.atlases.values()))
                if atlas is not None:
                    result.append(
                        {
                            "name": name,
                            "json": data.decode("utf-8-sig", errors="replace").lstrip().startswith("{"),
                            "skeleton": base64.b64encode(data).decode("ascii"),
                            "atlas": atlas.decode("utf-8-sig"),
                        }
                    )
            return result

        drawings = {"original": scenes(original_view), "repaired": scenes(repaired)}
        token = uuid.uuid4().hex
        self._remap_previews[token] = {
            "source": mod_id,
            "identity": identity,
            "files": {name: path.read_bytes() for name, path in generated.items()},
            "report": report,
            "compare": comparison,
            "expires": time.monotonic() + 3600,
            "built_against": built_against,
        }
        return {
            "preview": token,
            "report": report,
            "compare": comparison.to_json(),
            "pages": pages,
            "drawings": drawings,
        }

    @_serialized
    @_mod_writer
    def accept_remap(self, preview: str) -> RemapResult:
        """Store the exact prepared repair bytes once, linking source and copy in one state write.

        Args:
            preview: Unexpired token returned by remap, old_rig, or a completed graft check.

        Raises:
            ServiceError: The token expired or the source bytes or repair links changed (entry_changed).
        """
        prepared = self._remap_previews.get(preview)
        if prepared is None or prepared["expires"] <= time.monotonic():
            self._remap_previews.pop(preview, None)
            raise ServiceError("entry_changed")
        record = self._record(prepared["source"])
        if (
            record.get("repaired_from")
            or self._repaired_id(record)
            or prepared["identity"] != self._identity({path.name: path for path in self._paths(record)})
        ):
            raise ServiceError("entry_changed")
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "accept-") as temporary:
            files = {}
            for name, data in prepared["files"].items():
                path = Path(temporary) / name
                path.write_bytes(data)
                files[name] = path
            stored = self._store(
                files,
                self._label(record),
                record["target"],
                record["kind"],
                repaired_from=record["id"],
                built_against=prepared.get("built_against"),
            )
        self._remap_previews.pop(preview, None)
        return RemapResult(self._entry(stored), prepared["report"], replace(prepared["compare"], mod_id=stored["id"]))

    @_serialized
    def discard_remap(self, preview: str) -> None:
        """Discard a temporary repair-preview token without changing mod or game files."""
        self._remap_previews.pop(preview, None)
