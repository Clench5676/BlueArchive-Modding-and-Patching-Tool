"""Costume switches of 3D models: staging, verdicts, Optimize Physics and costume previews."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from bmpt.core import devmode, preview_gate, spans
from bmpt.core.progress_meter import Meter
from bmpt.engine import (
    Bundle,
    LogFunc,
    ProgressCallback,
    SaveOptions,
    is_safe_file_name,
    no_log,
)
from ...core.errors import ServiceError
from ...gameassets.discovery import cab_names, may_hold_model_root, own_bundle
from ...core.logging_utils import log_failure
from ...core.openfiles import remove_tree, temporary_directory
from ..rebuild import rebuild
from ..bundle_saves import save_bundles
from ..judge import (
    core_of,
    now,
)
from ..models import Pin
from ..storage import (
    atomic_copy,
    atomic_json,
)
from ..variants import adapt_variant_sources
from .base import (
    COSTUME_BUILD_TRIES,
    COSTUME_PREVIEWS,
    COSTUME_VERDICTS,
    _CostumeInputs,
    _WornVerdicts,
    _errors,
    _item_fraction,
    _revision,
    _serialized,
)


logger = logging.getLogger(__package__)


class CostumesMixin:
    """Costume switches of 3D models: staging, verdicts, Optimize Physics and costume previews."""

    def _stage_adapted(
        self, sources: list[Path], targets: list[Path], directory: Path, log: LogFunc, pin: Pin
    ) -> dict[Path, Path]:
        staged = self._stage_official(targets, directory / "targets", pin, current_fallback=True)
        output = directory / "output"
        output.mkdir(parents=True)
        adapted = adapt_variant_sources(
            sources, list(staged.values()), directory / "sources", self.settings.asset_types(), log
        )
        result = rebuild(
            adapted,
            list(staged.values()),
            output,
            self.settings.asset_types(),
            self.settings.save_options(self.settings.crc_needed(targets[0])),
            self.settings.spine_options(),
            skip_unchanged=True,
            match_strategy="name_type",
            anim_check=self.settings.anim_check_options(),
            log=log,
        )
        if not result.success:
            raise ServiceError("mod_apply_failed", file=targets[0])
        reverse = {value: key for key, value in staged.items()}
        replacements = dict(staged)
        for pair in result.file_pairs:
            replacements[reverse[pair.source]] = pair.output
        return replacements

    def _costume_files(self, other: str, pin: Pin) -> tuple[str, list[Path], list[Path]]:
        """Find another variant's model as a costume source.

        That is its dev name, the game files that hold its prefab and every game file of its own (the model slot; for a
        slot of the variant's own dependency bundles, a story-only or UI-only model, the parts that can hold it; for a
        variant with a model but no slot, the field character bundles, which hold other students' field models as well,
        and those parts of its own bundles).
        """
        variant = self._variant(other)
        core = getattr(variant, "model", None)
        if core:
            files = self._game_files(other, "model", pin)
            prefabs = [path for path in files if path.name.startswith("character-")]
            if prefabs:
                return core.lower(), prefabs, files
            # no prefab bundle: a model of its own dependency bundles (Arona's)
            owned = sorted((path for path in files if may_hold_model_root(path.name)), key=lambda path: path.name)
            if owned:
                return core.lower(), owned, owned
        dev = getattr(variant, "field_model", None)
        if dev:
            names = self._names(pin)
            files = sorted(
                (
                    path
                    for name, path in names.items()
                    if name.startswith("field-character-_mxload")
                    or (own_bundle(name, dev) and may_hold_model_root(name))
                ),
                key=lambda path: path.name,
            )
            if files:
                return dev.lower(), files, files
        raise ServiceError("game_file_not_found", alt=other)

    def _costume_source(self, other: str, pin: Pin, *, preview: bool = False) -> tuple[str, list[Path]]:
        """The dev name and the official copies of another variant's model files (the build's inputs)."""
        dev, _, files = self._costume_files(other, pin)
        resolve = self._official_or_current if preview else self._official_for
        return dev, [resolve(path, pin) for path in files]

    def _costume_inputs(self, other: str, targets: list[Path], pin: Pin, *, preview: bool = False) -> _CostumeInputs:
        """Gather what ``_stage_costume`` reads of the library for a costume build.

        The build is of ``other``'s costume on the model slot ``targets``. This runs under the library lock (path
        lookups only, no bundle is opened).
        """
        originals = {path: self._official_or_current(path, pin) for path in targets}
        dev, prefab_files, files = self._costume_files(other, pin)
        resolve = self._official_or_current if preview else self._official_for
        sources = [resolve(path, pin) for path in files]
        target_dev = self._core_map(pin).slot(core_of(targets[0].name))
        try:  # what keys each model's worn items (model_props), cached in worn-items.json
            revisions = {dev: _revision(zip(files, sources)), target_dev: _revision(originals.items())}
        except OSError:
            revisions = {}
        return _CostumeInputs(
            originals=originals,
            dev=dev,
            chosen={path.name for path in prefab_files},
            sources=sources,
            cabs=self._cab_index(pin),
            target_dev=target_dev,
            official=lambda path: self._official_or_current(path, pin),
            revisions=revisions,
        )

    def _stage_copies(self, originals: dict[Path, Path], directory: Path) -> dict[Path, Path]:
        """Copy the bytes chosen for each game file (``originals``) into ``directory`` under the game file's name."""
        staged: dict[Path, Path] = {}
        directory.mkdir(parents=True, exist_ok=True)
        for path, source in originals.items():
            target = directory / path.name
            if target in staged.values():
                raise ServiceError("game_file_conflict", file=path)
            atomic_copy(source, target)
            staged[path] = target
        return staged

    def _stage_costume(
        self,
        other: str,
        targets: list[Path],
        directory: Path,
        log: LogFunc,
        pin: Pin,
        *,
        preview: bool = False,
        report: dict[str, Any] | None = None,
        physics: bool = False,
        inputs: _CostumeInputs | None = None,
        step: Callable[[], None] | None = None,
        fraction: Callable[[float], None] | None = None,
    ) -> dict[Path, Path]:
        """Build the model slot ``targets`` (official copies) carrying the costume of ``other``'s model.

        Changed parts are built into ``directory``, the others are staged official copies. Reads official bytes only:
        the slot's own parts, the source model's files, and any other bundle the CAB index finds. Every costume, the
        same student's too, carries its halo or hides the slot's. The build takes no model lock (``model_adapt.adapt``
        reads its bundles from memory), so exports run beside it. The changed parts are saved in threads
        (``bundle_saves.save_bundles``), at most half the cores; the same bytes as one after another.

        - ``physics``: Optimize Physics; the build may also choose Rule B, or a rule built with the followed leg
          groups made rigid; ``report["physics"]`` says whether it chose one of them.
        - ``inputs``: what it reads of the library, gathered before under the library lock
          (``_costume_inputs``); given, the build reads no library state and runs without that lock
          (``costume_preview``), else the caller holds it.
        - ``step``: called between its steps (``adapt``'s, then before each part's save starts); only for a
          build without the library lock (``costume_preview``), whose job yields there to the foreground
          previews.
        - ``fraction``: told how far the build is (0..1, its phases weighted by ``model_adapt.COSTUME_PHASES``),
          at most every 0.2 s (``progress_meter``); the job's progress for its item (``_item_fraction``).
        """
        from ...gameassets.model_adapt import adapt, costume_weights
        from ...gameassets.costume_cache import PoseStore

        meter = Meter(fraction, costume_weights(physics))
        meter.phase("stage")
        if inputs is None:
            inputs = self._costume_inputs(other, targets, pin, preview=preview)
        staged = self._stage_copies(inputs.originals, directory / "targets")
        index: dict[str, Path] = {}
        sources = inputs.sources
        for path in sources:
            for cab in cab_names(path):
                index.setdefault(cab, path)
        for path in staged.values():
            for cab in cab_names(path):
                index[cab] = path
        cabs = inputs.cabs

        def resolver(wanted: set[str], hint: Path | None) -> dict[str, str]:
            return {cab: str(inputs.official(Path(path))) for cab, path in cabs.resolve(wanted, hint).items()}

        step = step or (lambda: None)
        with spans.span("costume.build"):
            result = adapt(
                index=index,
                resolver=resolver,
                target_files=list(staged.values()),
                target_dev=inputs.target_dev,
                source_files=[p for p in sources if p.name in inputs.chosen],
                source_dev=inputs.dev,
                open_writable=Bundle.load,
                physics=physics,
                halo=True,
                step=step,
                worn=_WornVerdicts(self.root / "worn-items.json", inputs.revisions),
                meter=meter,
                poses=PoseStore(self.root / "costume-poses", inputs.revisions),
            )
        if report is not None and result.plan.unfit:
            report["unfit"] = {
                "source": "stretch",
                "adapted": f"{result.plan.stretch:.2f}",
                "own": f"{result.plan.own:.2f}",
            }
        if report is not None:
            # Optimize Physics gains something: hem or chain rules, or arms pushed out of a bulkier costume
            report["physics"] = bool(result.arms) or (
                result.hems is not None
                and (
                    result.hems.skirt is not None
                    or result.hems.regrouped
                    or result.hems.thin is not None
                    or bool(result.hems.springs)
                )
            )
            report["cautions"] = list(result.cautions)
        output = directory / "output"
        output.mkdir(parents=True, exist_ok=True)
        reverse = {value: key for key, value in staged.items()}
        replacements = dict(staged)
        # A preview's parts are only read by the App's own 3D export and never reach the game: written uncompressed and
        # without the CRC fix, which skips the LZMA pass (most of the save phase). Game-bound builds keep the settings.
        jobs = [
            (
                reverse[path],
                bundle,
                output / path.name,
                SaveOptions(perform_crc=False, compression="none")
                if preview
                else self.settings.save_options(self.settings.crc_needed(reverse[path])),
            )
            for path, bundle in result.changed.items()
        ]
        # in threads, at most half the cores: step() still runs before each save starts; a game-bound costume's LZMA
        # in the fast mode (``bundle_saves``: the same format, about three times faster, some 3 % larger)
        save_bundles(jobs, step=step, progress=lambda done, count: meter.phase("save", done, count), fast=not preview)
        for target, _, path, _ in jobs:
            replacements[target] = path
        return replacements

    def _costume_signature(self, variant_key: str, other: str, pin: Pin) -> tuple[str, list[Path]]:
        """Give the build signature of another variant's costume on a variant's model slot, and the slot's files.

        The signature is the preview's, from official bytes where there are any, else the current ones. It is keyed by
        the costume rule's version rather than the program's: a new program build keeps the verdicts and previews of the
        same rule.
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        rule = f"costume-rule:{COSTUME_RULE}"
        self._variant(variant_key)
        if other == variant_key:
            raise ServiceError("source_unsupported", source=f"alt:{other}")
        self._variant(other)
        targets = self._game_files(variant_key, "model", pin)
        if not targets:
            raise ServiceError("game_file_not_found", alt=variant_key)
        _, inputs = self._costume_source(other, pin, preview=True)
        try:
            return self._build_signature(inputs, targets, pin, rule), targets
        except ServiceError as error:
            if error.code != "original_unavailable":
                raise
            return hashlib.sha1(
                repr(
                    ("current", rule, [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in [*inputs, *targets]])
                ).encode()
            ).hexdigest(), targets

    def _costume_allowed(self, verdict: dict[str, Any] | None) -> bool:
        """Tell whether a pair's saved verdict lets it be built for the game.

        That is a verdict of unchecked, "ok" or "preview" (past the stretch limit); never a refusal.
        """
        return verdict is None or verdict["ok"] or verdict.get("preview")

    def _verdicts(self) -> dict[str, dict[str, Any]]:
        path = self.root / "costume-verdicts.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        verdicts = data.get("verdicts") if isinstance(data, dict) and data.get("version") == 1 else None
        return {
            key: value
            for key, value in (verdicts or {}).items()
            if isinstance(key, str) and isinstance(value, dict) and isinstance(value.get("ok"), bool)
        }

    def _save_verdict(
        self,
        signature: str,
        ok: bool,
        reason: str | None = None,
        detail: Mapping[str, Any] | None = None,
        *,
        preview: bool = False,
        cautions: Iterable[str] = (),
    ) -> dict[str, Any]:
        """``cautions``: what the built pair may draw differently in the game (``model_adapt.Result.cautions``)."""
        cautions = sorted({str(c) for c in cautions})
        verdict = {
            "ok": ok,
            "reason": reason,
            "detail": {str(k): str(v) for k, v in (detail or {}).items()},
            "at": now(),
            **({"preview": True} if preview and not ok else {}),
            **({"cautions": cautions} if cautions and (ok or preview) else {}),
        }
        verdicts = self._verdicts()
        verdicts[signature] = verdict
        kept = dict(
            sorted(verdicts.items(), key=lambda item: str(item[1].get("at", "")), reverse=True)[:COSTUME_VERDICTS]
        )
        try:
            atomic_json(self.root / "costume-verdicts.json", {"version": 1, "verdicts": kept})
        except OSError as error:  # the verdict is still returned; the pair is checked again next time
            log_failure(logger, logging.WARNING, "Could not save a costume verdict", error)
        return verdict

    @_serialized
    def costume_verdict(self, variant_key: str, other: str, pin: Pin | None = None) -> dict[str, Any] | None:
        """Read a costume pair's verdict for the current build signature.

        The verdict records whether the pair was built and exported for the current build
        signature (the program, the official bytes of both models, the save options): {"ok", "reason", "detail",
        "at"}, plus "preview" for a pair past the stretch limit (still offered), or None when not judged
        yet. A pair the Switch 3D Model card offers has one with ok or preview.

        Args:
            variant_key: Variant key whose model slot receives the costume.
            other: Variant key supplying the costume to fit onto that slot.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        signature, _ = self._costume_signature(variant_key, other, self._current(pin))
        return self._verdicts().get(signature)

    @_serialized
    def record_costume_verdict(
        self,
        variant_key: str,
        other: str,
        ok: bool,
        reason: str | None = None,
        detail: Mapping[str, Any] | None = None,
        pin: Pin | None = None,
    ) -> dict[str, Any]:
        """Save a completed costume check with any cautions recorded by its preview build.

        Args:
            variant_key: Variant key whose model slot was checked.
            other: Variant key supplying the costume that was built/exported or refused.
            ok: Whether the pair completed the compatibility check successfully.
            reason: Failure category, or None for a successful check without a refusal reason.
            detail: Structured failure evidence, or None when the verdict needs none.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        signature, _ = self._costume_signature(variant_key, other, self._current(pin))
        return self._save_verdict(signature, ok, reason, detail, cautions=self._preview_cautions(signature))

    def _preview_cautions(self, signature: str) -> list[str]:
        """The cautions the preview build of a pair recorded (``costume_preview``'s parts.json), none when unknown."""
        try:
            kept = json.loads((self.root / "built" / "previews" / signature / "parts.json").read_text())
        except (OSError, ValueError):
            return []
        cautions = kept.get("cautions") if isinstance(kept, dict) else None
        return [c for c in cautions if isinstance(c, str)] if isinstance(cautions, list) else []

    @_serialized
    def optimize_costume(
        self, variant_key: str, source: str, name: str, pin: Pin | None = None, progress: ProgressCallback | None = None
    ) -> dict[str, Any]:
        """Store a costume swap with skirt-chain physics as a reusable model mod.

        The variant's model slot carrying ``source``'s costume ("alt:<key>") is built with
        the physics rules (Rule B, the skirt chains as new bones), kept as a model mod of the variant named after
        ``name`` (the costume as the screen shows it): {"mod": id}, the copy made before when there is one. {"unneeded":
        True, "reason": ...} when there is nothing to build: "official" for the official model, whose own clips move its
        skirt, and "no_gain" for a costume that no physics rule improves within the stretch limit. The copy records the
        costume rule it was built under.

        Args:
            variant_key: Variant key whose model slot receives the optimized costume.
            source: alt:<key> costume source; official returns an unneeded result.
            name: Costume label from the UI, trimmed to 200 characters; blank falls back to the source key.
            pin: Game install and server the operation is fixed to; None uses the current install.
            progress: Receives build fraction and phase dictionaries; None disables reporting.
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        pin = self._current(pin)
        self._variant(variant_key)
        if source == "official":
            return {"unneeded": True, "reason": "official"}
        if not isinstance(source, str) or not source.startswith("alt:"):
            raise ServiceError("source_unsupported", source=str(source))
        other = source[4:]
        signature, targets = self._costume_signature(variant_key, other, pin)
        verdict = self._verdicts().get(signature)
        if not self._costume_allowed(verdict):  # refused: never built for the game
            raise ServiceError("source_unsupported", source=verdict.get("reason") or source)
        made = next(
            (
                record
                for record in self._records.values()
                if self._optimized_from(record) == source
                and record["kind"] == "model"
                and self._target_of(record) == variant_key
            ),
            None,
        )
        if made is not None:
            return {"mod": made["id"]}
        label = name.strip()[:200] if isinstance(name, str) and name.strip() else other
        report: dict[str, Any] = {}
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
            replacements = self._stage_costume(
                other,
                targets,
                Path(temporary) / "model",
                no_log,
                pin,
                report=report,
                physics=True,
                fraction=_item_fraction(progress, build=1.0),
            )
            if not report.get("physics"):
                return {"unneeded": True, "reason": "no_gain"}
            built = {path.name: path for path in replacements.values() if path.parent.name == "output"}
            record = self._store(
                built,
                label,
                variant_key,
                "model",
                metadata={"optimized_from": source, "costume_rule": COSTUME_RULE},
                built_against=self._built_against(built, pin, "import"),
            )
        return {"mod": record["id"]}

    def costume_preview(
        self, variant_key: str, other: str, pin: Pin | None = None, *, cancelled: Callable[[], bool] | None = None
    ) -> list[Path]:
        """Build and cache a variant's model slot carrying another variant's costume for preview.

        Build once per signature into built/previews/<signature> and retain the newest
        few, whose paths key the export cache. Read official bytes where available,
        falling back to current files as other previews do.

        Not serialized as a whole: its callers are the 3D tab's preview (class 1) and the costume check (class 2),
        and a build takes seconds. Only its short steps hold the library lock: the signature, the kept preview and
        the build's inputs (``_costume_preview_start``), then storing the result (``_costume_preview_store``); the
        staging and the build run without it, so the page's reads never queue behind a build. The build holds no
        model lock either: exports, the shown model's and its clips, run beside it. A build whose signature changed
        meanwhile (the game files or the kept originals changed) is discarded and built again.
        Class 2 for the costume check: between the build's steps (most under 0.5 s, ``model_adapt.adapt``) it yields
        to the foreground previews, at most 20 s a step (``preview_gate.step``); a preview's own build does not. A
        preview waiting for a build of the same pair waits outside its own time (``preview_gate.waiting``).
        ``cancelled`` (the costume check's ``jobs.cancel``): once set, the build stops at its next step (``busy``)
        and no verdict is saved; a build that ended is still kept.

        Args:
            variant_key: Variant key whose model slot receives the preview costume.
            other: Variant key supplying the costume to fit.
            pin: Game install and server the operation is fixed to; None uses the current install.
            cancelled: Returns True to stop at a build step without saving a verdict;
                None allows the build to finish.

        Returns:
            Cached built part paths whose stable names also key the export cache.

        Raises:
            ServiceError: The pair cannot be built, cancellation is requested (busy),
                or its inputs keep changing through all retries.
        """

        def stopped() -> bool:
            return cancelled is not None and cancelled()

        def step() -> None:
            preview_gate.step(cancelled)
            if stopped():
                raise ServiceError("busy")

        with _errors("costume_preview"):
            for _ in range(COSTUME_BUILD_TRIES):
                start = self._costume_preview_start(variant_key, other, pin, verdicts=not stopped())
                if isinstance(start, list):
                    return start
                signature, targets, built_pin, inputs = start
                building = self._costume_build_turn(signature)
                if building is not None:  # another caller builds the same pair: its result is kept, read it then
                    with preview_gate.waiting():
                        while not building.wait(0.1):
                            if stopped():
                                raise ServiceError("busy")
                    continue
                try:
                    if stopped():
                        raise ServiceError("busy")
                    report: dict[str, Any] = {}
                    with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX) as temporary:
                        try:
                            replacements = self._stage_costume(
                                other,
                                targets,
                                Path(temporary) / "model",
                                no_log,
                                built_pin,
                                preview=True,
                                report=report,
                                inputs=inputs,
                                step=step,
                            )
                        except ServiceError as error:
                            if error.code == "source_unsupported" and not stopped():
                                # the rule cannot carry this pair: never offered again
                                self._costume_preview_refused(variant_key, other, pin, signature, error)
                            raise
                        built = [path for path in replacements.values() if path.parent.name == "output"]
                        parts = self._costume_preview_store(
                            variant_key, other, pin, signature, built, report, verdicts=not stopped()
                        )
                    if parts is not None:
                        return parts
                    logger.info("Costume preview %s <- %s changed while it was built: built again", variant_key, other)
                finally:
                    self._costume_build_done(signature)
            raise ServiceError("busy")

    def _costume_build_turn(self, signature: str) -> threading.Event | None:
        """Claim the build of a costume preview signature.

        None when this caller builds it (``_costume_build_done`` ends the claim), else the event of the build under way.
        """
        with self._costume_builds_lock:
            building = self._costume_builds.get(signature)
            if building is None:
                self._costume_builds[signature] = threading.Event()
            return building

    def _costume_build_done(self, signature: str) -> None:
        with self._costume_builds_lock:
            building = self._costume_builds.pop(signature, None)
        if building is not None:
            building.set()

    @_serialized
    def _costume_preview_start(
        self, variant_key: str, other: str, pin: Pin | None, *, verdicts: bool = True
    ) -> list[Path] | tuple[str, list[Path], Pin, _CostumeInputs]:
        """Take ``costume_preview``'s first step, under the library lock.

        It gives the kept preview of the pair's build signature when there is one (with the stretch verdict it found,
        unless ``verdicts`` is False), else (signature, the slot's files, the install, the build's inputs).
        """
        pin = self._current(pin)
        signature, targets = self._costume_signature(variant_key, other, pin)
        folder = self.root / "built" / "previews" / signature
        done = folder / "parts.json"
        if done.is_file():
            try:
                kept = json.loads(done.read_text())
                kept = kept if isinstance(kept, dict) else {"parts": kept, "unfit": None}
                parts = [folder / name for name in kept["parts"] if is_safe_file_name(name)]
                if parts and all(path.is_file() for path in parts):
                    os.utime(folder)
                    if verdicts and kept.get("unfit") and signature not in self._verdicts():
                        self._save_verdict(
                            signature,
                            False,
                            "stretch",
                            kept["unfit"],
                            preview=True,
                            cautions=kept.get("cautions") or (),
                        )
                    return parts
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return signature, targets, pin, self._costume_inputs(other, targets, pin, preview=True)

    @_serialized
    def _costume_preview_refused(
        self, variant_key: str, other: str, pin: Pin | None, signature: str, error: ServiceError
    ) -> None:
        """Save the rule's refusal of a pair built without the lock, when its signature is still the current one."""
        if self._costume_signature(variant_key, other, self._current(pin))[0] == signature:
            self._save_verdict(signature, False, error.params.get("source"), error.params)

    @_serialized
    def _costume_preview_store(
        self,
        variant_key: str,
        other: str,
        pin: Pin | None,
        signature: str,
        built: list[Path],
        report: dict[str, Any],
        *,
        verdicts: bool = True,
    ) -> list[Path] | None:
        """Take ``costume_preview``'s last step, under the library lock.

        The parts built without the lock are copied into built/previews/<signature>, with the stretch verdict the build
        found (unless ``verdicts`` is False), and the older previews are pruned; None, keeping nothing, when the pair's
        current signature differs from ``signature``.
        """
        if self._costume_signature(variant_key, other, self._current(pin))[0] != signature:
            return None
        folder = self.root / "built" / "previews" / signature
        folder.mkdir(parents=True, exist_ok=True)
        for path in built:
            atomic_copy(path, folder / path.name)
        (folder / "parts.json").write_text(
            json.dumps(
                {
                    "parts": [path.name for path in built],
                    "unfit": report.get("unfit"),
                    "cautions": report.get("cautions") or [],
                }
            )
        )
        if verdicts and report.get("unfit"):  # past the stretch limit: still offered, with a caution
            self._save_verdict(
                signature, False, "stretch", report["unfit"], preview=True, cautions=report.get("cautions") or ()
            )
        previews = sorted(
            (p for p in folder.parent.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for stale in previews[COSTUME_PREVIEWS:]:
            remove_tree(stale)
        return [folder / path.name for path in built]
