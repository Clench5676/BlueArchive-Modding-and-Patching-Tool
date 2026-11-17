"""What a mod was built against, what a game update did to it, and the untested game build."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from bmpt.spine import atlas_remap
from bmpt.engine import (
    LogFunc,
    ProgressCallback,
    no_log,
)
from ...core.errors import ServiceError
from ...core.logging_utils import log_failure
from .. import profiles as profiles_mod
from .. import fingerprint, integrity, mod_progress, tested_build
from ..judge import (
    core_of,
    now,
)
from ..models import (
    Pin,
    ToggleResult,
)
from .base import LATER, _app_version, _errors, _serialized


logger = logging.getLogger(__package__)


class ImpactMixin:
    """What a mod was built against, what a game update did to it, and the untested game build."""

    def _built_against(
        self,
        files: Mapping[str, Path],
        pin: Pin | None,
        event: str,
        *,
        painted: bool | Mapping[str, dict] | None = None,
    ) -> dict | None:
        """Build the fingerprint (``fingerprint.document``) of the official parts a mod's files are built against.

        It covers every part its core has now, from a verified official copy, each with how the mod's own files stand to
        it (``fingerprint.standing``). A part with no official copy is left out (``complete`` False). Headers and text
        assets only. None when nothing can be told (no install, no part, unreadable files); it never fails the import,
        update or remap that asks.

        ``painted`` tells which atlas layout the mod's textures follow where the mod carries no atlas
        (``fingerprint.painted``): True when the files are known to be made for these parts (a whole-file copy of files
        named for them), the record of the files before when a rebuild only moved their textures over, and nothing when
        no one knows.
        """
        if pin is None or not files:
            return None
        try:
            inspection = integrity.Inspection()
            mod = fingerprint.carried(inspection, list(files.values()))
            parts, complete = [], True
            skeletons: dict[str, bytes] = {}
            for target in self._core_targets(files, pin):
                try:
                    official = self._official_for(target, pin, game_first=True)
                except ServiceError as error:
                    if error.code not in ("original_unavailable", "original_invalid"):
                        raise
                    complete = False
                    continue
                item = fingerprint.part(inspection, official, name=target.name, sha1=self._hashes.get(official).sha1)
                item["mod"] = fingerprint.standing(item, mod)
                parts.append(item)
                if painted is True:
                    skeletons.update(inspection.inspect(official, allow_unknown=True).skeletons)
            if not parts:
                return None
            if painted is True:
                known = fingerprint.painted(parts, mod, skeletons)
            elif painted:
                known = {name: entry for name, entry in painted.items() if name not in mod["atlases"]}
            else:
                known = None
            return fingerprint.document(event, parts, complete=complete, painted=known)
        except Exception as error:  # noqa: BLE001 - a fingerprint is never worth the operation
            log_failure(logger, logging.DEBUG, "No fingerprint of the official parts", error)
            return None

    def _rig_gains(self, files: Mapping[str, Path], pin: Pin | None) -> dict:
        """Find what the official rig of a mod's core has that the files lack (``atlas_remap.rig_gains``).

        Only the non-empty kinds are given; empty when nothing, or nothing can be told. Text assets only; never raises.
        """
        if pin is None or not files:
            return {}
        try:
            inspection = integrity.Inspection()
            mod, official = integrity.Contents(), integrity.Contents()
            for path in files.values():
                mod.merge(inspection.inspect(path, allow_unknown=True))
            for target in self._core_targets(files, pin):
                try:
                    official.merge(inspection.inspect(self._official_for(target, pin, game_first=True)))
                except ServiceError as error:
                    if error.code not in ("original_unavailable", "original_invalid"):
                        raise
            gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
            return {kind: names for kind, names in gains.items() if names}
        except Exception as error:  # noqa: BLE001 - a notice is never worth the operation
            log_failure(logger, logging.DEBUG, "No comparison with the official rig", error)
            return {}

    def _mod_hashes(self, record: dict[str, Any]) -> dict[str, str]:
        return {path.name: self._hashes.get(path).sha1 for path in self._paths(record)}

    def _read_built_against(self, record: dict[str, Any]) -> dict | None:
        """The stored fingerprint of a mod's files as they are now, or None."""
        try:
            return fingerprint.read(self.root / "mods" / record["id"], self._mod_hashes(record))
        except (OSError, ServiceError):
            return None

    def _write_built_against(self, record: dict[str, Any], data: dict | None) -> bool:
        if data is None or self._state_error is not None:  # a library that refuses writes gets none
            return False
        try:
            fingerprint.write(self.root / "mods" / record["id"], data, self._mod_hashes(record))
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.WARNING, "Could not store the official parts' fingerprint", error)
            return False
        # a new baseline: the impacts judged from the one before go (the same files can now tell otherwise)
        self._impacts = {key: found for key, found in self._impacts.items() if key[0] != record["id"]}
        return True

    @_serialized
    def built_against(self, mod_id: str, pin: Pin | None = None) -> dict | None:
        """Read a mod's recorded official baseline or backfill it from matching surviving parts.

        Prefer the fingerprint from import, update, or remapping. Backfill only from
        surviving official files whose matching assets establish the baseline.

        Args:
            mod_id: Stored mod ID whose official baseline is needed.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            The recorded or backfilled fingerprint, or None when evidence is insufficient.
        """
        record = self._record(mod_id)
        return self._read_built_against(record) or self._backfill_built_against(record, pin or self.current_pin())

    @_serialized
    def official_parts_now(self, mod_id: str, pin: Pin | None = None) -> dict | None:
        """Fingerprint current official parts for comparison with a mod's baseline without storing it.

        Args:
            mod_id: Stored mod ID whose core determines the official parts to fingerprint.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            Current official fingerprint, or None when no usable baseline can be read.
        """
        record = self._record(mod_id)
        return self._built_against({path.name: path for path in self._paths(record)}, pin or self.current_pin(), "now")

    def _impact(self, record: dict[str, Any], pin: Pin | None) -> str:
        """Report what a game update did to the official parts a mod was built against (``fingerprint.impact``).

        It is held against its core's parts in ``pin``. Never raises.
        """
        return self._impact_of(record, pin)[0]

    def _impact_of(self, record: dict[str, Any], pin: Pin | None) -> tuple[str, dict[str, Any]]:
        """Give ``_impact`` with what changed in those parts (``fingerprint.changes``).

        It is kept per mod files and official part names (a rebuilt part has another CRC in its name), so a toggle
        reaching many entries reads each core once. Never raises.
        """
        if pin is None:
            return fingerprint.UNKNOWN, {}
        try:
            files = {path.name: path for path in self._paths(record)}
            if not files:
                return fingerprint.UNKNOWN, {}
            targets = self._core_targets(files, pin)
            key = (
                record["id"],
                tuple(sorted(self._mod_hashes(record).items())),
                pin.key,
                tuple(sorted(str(path) for path in targets)),
            )
            known = self._impacts.get(key)
            if known is not None:
                return known
            if not targets:
                # a variant whose core moved since (the catalog maps it elsewhere) is no target gone
                mapped = (
                    self._core_map(pin).core(self._target_of(record), record["kind"])
                    if record["kind"] != "other"
                    else None
                )
                moved = bool(mapped) and mapped.lower() != core_of(next(iter(files)))
                gone = bool(self._read_built_against(record)) and not moved
                found = (fingerprint.GONE, {"gone": True}) if gone else (fingerprint.UNKNOWN, {})
            else:
                built = self._read_built_against(record) or self._backfill_built_against(record, pin)
                now = self._built_against(files, pin, "now") if built else None
                found = (
                    (fingerprint.impact(built, now), fingerprint.changes(built, now))
                    if built
                    else (fingerprint.UNKNOWN, {})
                )
            if len(self._impacts) >= 4096:
                self._impacts.clear()
            self._impacts[key] = found
            return found
        except Exception as error:  # noqa: BLE001 - a judgement that cannot be made tells nothing
            log_failure(logger, logging.DEBUG, "No impact judged for the mod", error)
            return fingerprint.UNKNOWN, {}

    def _hold(self, pin: Pin, target: str) -> bool:
        """Tell whether an On or its Retry holds an entry configured with ``target`` instead of writing it.

        It does for a mod whose official rig a game update changed (``fingerprint.RIG_CHANGED``), so the write would
        carry its old rig or layout onto the new parts. Remapping or updating the mod records its new baseline and ends
        the hold; Apply Mod Configuration and a switch on the detail screen write it as asked.
        """
        if not target.startswith("mod:") or target[4:] not in self._records:
            return False
        return self._impact(self._records[target[4:]], pin) == fingerprint.RIG_CHANGED

    def resource_version(self, pin: Pin) -> str | None:
        """Return a pinned install's resource version, or None when it cannot be read."""
        from ...gameassets.discovery import resource_version

        try:
            return resource_version(list(pin.dirs))
        except OSError:
            return None

    def update_impact(
        self, pin: Pin | None = None, *, force: bool = False, progress: Callable[[dict], None] | None = None
    ) -> dict[str, Any]:
        """Classify a game update's impact on library mods for one install.

        A new or unrecorded resource version triggers baseline comparisons through
        fingerprint.impact and fingerprint.changes. due is True for rebuilt parts,
        new parts, changed rigs, or vanished cores. An empty check acknowledges the
        version immediately; a nonempty result waits for impact_seen. Per-mod
        failures provide no impact evidence and do not fail the pass. Apart from
        caching outcomes and acknowledging empty checks, the operation only reads.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.
            force: Whether to classify even a resource version already recorded as seen.
            progress: Receives per-mod check dictionaries; None disables reporting.
        """
        pin = pin or self.current_pin()
        if pin is None:
            return {"due": False, "install": None, "version": None, "previous": None, "mods": [], "counts": {}}
        version = self.resource_version(pin)
        config = self.profiles.read() or {}
        seen = ((config.get("folders") or {}).get(pin.key) or {}).get("resource") or {}
        previous = seen.get("version") if isinstance(seen, dict) else None
        out: dict[str, Any] = {
            "due": False,
            "install": pin.key,
            "version": version,
            "previous": previous,
            "mods": [],
            "counts": {name: 0 for name in fingerprint.IMPACTS},
        }
        if version is None or (version == previous and not force):
            return out
        configured = {
            entry.get("source")
            for entry in (((config.get("folders") or {}).get(pin.key) or {}).get("configured") or {}).values()
        }
        with self._lock:
            entries = {entry.id: entry for entry in self.mods()}
            records = [self._records[mod_id] for mod_id in entries if mod_id in self._records]
            # the same resource version, the same mods and baselines and configuration as the last check: its answer
            # (Settings' View shows the summary at once when nothing changed)
            stamp = self._impact_stamp(pin, version, records, configured)
        cached = self._impact_cache.get(pin.key)
        if cached and cached[0] == stamp:
            out.update(mods=[dict(mod) for mod in cached[1]["mods"]], counts=dict(cached[1]["counts"]))
            out["due"] = bool(out["mods"])
            return out
        line = mod_progress.ItemProgress(progress, len(records), "check")
        for i, record in enumerate(records, 1):
            if progress:
                entry = entries[record["id"]]
                line.item(
                    i,
                    variant=entry.target,
                    kind=entry.kind,
                    name=self._progress_name(record),
                    core=entry.core,
                    step=mod_progress.STEP_CHECK,
                )
            with self._lock:
                if record["id"] not in self._records:
                    continue
                found, changes = self._impact_of(record, pin)
            out["counts"][found] += 1
            if found in (fingerprint.UNCHANGED, fingerprint.UNKNOWN):
                continue
            out["mods"].append(self._impact_item(entries[record["id"]], found, changes, configured))
        out["due"] = bool(out["mods"])
        self._impact_cache[pin.key] = (
            stamp,
            {"mods": [dict(mod) for mod in out["mods"]], "counts": dict(out["counts"])},
        )
        self._keep_impact(pin.key, version, out["mods"])
        if not out["due"] and not force:
            self.impact_seen(pin.key, version)
        return out

    @staticmethod
    def _impact_item(entry: Any, found: str, changes: dict, configured: set) -> dict[str, Any]:
        """A mod as the impact summary names it."""
        return {
            "id": entry.id,
            "label": entry.label,
            "target": entry.target,
            "kind": entry.kind,
            "core": entry.core,
            "impact": found,
            "changes": changes,
            "configured": f"mod:{entry.id}" in configured,
            "repaired_id": entry.repaired_id,
        }

    def _impact_mark(self, record: dict[str, Any]) -> str:
        """Give what a kept impact of a mod depends on.

        That is its files and its stored baseline (by its modification time). A repair or an update writes a new
        baseline, which ends the kept impact. Names and times only.
        """
        baseline = self.root / "mods" / record["id"] / fingerprint.FILE_NAME
        try:
            mark = baseline.stat().st_mtime_ns
        except OSError:
            mark = None
        return hashlib.sha1(json.dumps([sorted(record.get("files") or []), mark]).encode()).hexdigest()

    def _keep_impact(self, install: str, version: str, mods: list[dict[str, Any]]) -> None:
        """Keep the outcome of an impact check of an install (``last_impact``).

        It holds the resource version and the mods whose rig changed, each with what changed and its mark
        (``_impact_mark``). Never fails.
        """
        with self._lock:
            kept = [
                {
                    "id": mod["id"],
                    "changes": mod.get("changes") or {},
                    "mark": self._impact_mark(self._records[mod["id"]]),
                }
                for mod in mods
                if mod.get("impact") == fingerprint.RIG_CHANGED and mod["id"] in self._records
            ]
        if self._writes_refused():
            return

        def change(data: dict) -> bool:
            folder = data["folders"].setdefault(install, {})
            before = folder.get("impact") or {}
            if before.get("version") == version and before.get("mods") == kept:
                return False
            folder["impact"] = {"version": version, "checked": now(), "mods": kept}
            return True

        try:
            with _errors("impact_kept"):
                self.profiles.update(change)
        except ServiceError as error:
            log_failure(logger, logging.WARNING, "Could not keep the outcome of the impact check", error)

    def last_impact(self, pin: Pin | None = None) -> dict[str, Any]:
        """Read the last impact outcome using only the version file and configuration.

        Include mods whose recorded rig change still has the same baseline mark,
        using update_impact's row format. update is True when the resource version
        differs from the last checked version (or last seen version), or is unknown.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.
        """
        pin = pin or self.current_pin()
        out: dict[str, Any] = {"install": None, "version": None, "checked": None, "update": False, "mods": []}
        if pin is None:
            return out
        version = self.resource_version(pin)
        config = self.profiles.read() or {}
        folder = (config.get("folders") or {}).get(pin.key) or {}
        kept = folder.get("impact") if isinstance(folder.get("impact"), dict) else {}
        seen = folder.get("resource") if isinstance(folder.get("resource"), dict) else {}
        checked = kept.get("version") if isinstance(kept.get("version"), str) else None
        reference = checked or (seen.get("version") if isinstance(seen.get("version"), str) else None)
        out.update(
            install=pin.key,
            version=version,
            checked=checked,
            update=version is None or reference is None or version != reference,
        )
        configured = {
            entry.get("source") for entry in (folder.get("configured") or {}).values() if isinstance(entry, dict)
        }
        with self._lock:
            entries = {entry.id: entry for entry in self.mods()}
            for item in kept.get("mods") or []:
                if not isinstance(item, dict):
                    continue
                record = self._records.get(item.get("id"))
                if record is None or item["id"] not in entries or item.get("mark") != self._impact_mark(record):
                    continue
                changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
                out["mods"].append(self._impact_item(entries[item["id"]], fingerprint.RIG_CHANGED, changes, configured))
        return out

    def _outdated_costumes(self, pin: Pin, config: dict) -> list[dict[str, Any]]:
        """List what the App built from a costume under an older costume rule than the program's.

        ``refresh_costumes`` builds them again. They are Optimize Physics copies (``_costume_outdated``; ``installed``
        when the game files hold it), then the costume swaps the install's game files hold (``_swap_outdated``, verified
        installed; ``source`` the costume, no mod id). Reads records, the configuration and the game files' cached
        fingerprints only.
        """
        folder = (config.get("folders") or {}).get(pin.key) or {}
        choices = folder.get("configured") or {}
        out: list[dict[str, Any]] = []
        with self._lock:
            installed: dict[str, bool] = {}  # a configured source: whether the game files hold it

            def holds(core: str, choice: dict) -> bool:
                key = f"{core}|{choice.get('source')}"
                if key not in installed:
                    try:
                        installed[key] = self._selection_of(pin, config, core, choice)["installed"]
                    except (OSError, ServiceError) as error:
                        log_failure(logger, logging.DEBUG, "Could not judge an installed costume build", error)
                        installed[key] = False
                return installed[key]

            for record in self._records.values():
                if not self._costume_outdated(record):
                    continue
                entry = self._entry(record)
                held = any(
                    choice.get("source") == f"mod:{entry.id}" and holds(core, choice)
                    for core, choice in choices.items()
                )
                out.append(
                    {
                        "id": entry.id,
                        "target": entry.target,
                        "kind": entry.kind,
                        "core": entry.core,
                        "source": f"mod:{entry.id}",
                        "installed": held,
                    }
                )
            for core, choice in sorted(choices.items()):
                if choice.get("kind") != "model" or not str(choice.get("source") or "").startswith("variant:"):
                    continue
                if not holds(core, choice):
                    continue
                try:
                    if not self._swap_outdated(self._spine_parts(core, pin), choice):
                        continue
                except (OSError, ServiceError) as error:
                    log_failure(logger, logging.DEBUG, "Could not judge a costume swap", error)
                    continue
                out.append(
                    {
                        "id": "",
                        "target": choice.get("variant"),
                        "kind": "model",
                        "core": core,
                        "source": choice["source"],
                        "installed": True,
                    }
                )
        return out

    def refresh_costumes(
        self,
        pin: Pin | None = None,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        *,
        write_game: bool = True,
    ) -> dict[str, list]:
        """Refresh costume-derived builds against the current costume rule at startup.

        Rebuild Optimize Physics copies in place and reapply installed copies and
        costume swaps. A copy with no physics gain, a refused pair, or a failed build
        is marked costume_kept for this rule. Installed builds whose writes are
        blocked by game activity, an untested build, switch intent, or write_game
        are deferred before building. Individual failures stay in the result.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install.
            log: Receives user-facing operation messages.
            progress: Receives per-item build/write dictionaries; None disables reporting.
            write_game: Whether installed builds may be refreshed; False leaves them for the next startup.

        Returns:
            done, kept, and left lists, each identifying mod ID, core, and source.
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        pin = self._current(pin)
        items = self._outdated_costumes(pin, self.profiles.read() or {})
        result: dict[str, list] = {"done": [], "kept": [], "left": []}
        if items and self._writes_refused():
            result["left"] = [{key: item[key] for key in ("id", "core", "source")} for item in items]
            return result
        line = mod_progress.ItemProgress(progress, len(items), "process")
        for i, item in enumerate(items, 1):
            line.item(
                i, **self._progress_identity(item["target"], "model", item["source"]), step=mod_progress.STEP_CHECK
            )
            if item["id"]:
                rebuilt = line.part(0.0, 0.9 if item["installed"] else 1.0)
                written = line.part(0.9, 1.0, build=0.0)
            else:
                rebuilt, written = None, line.part(0.0, 1.0, build=0.9)
            named = {key: item[key] for key in ("id", "core", "source")}
            if item["installed"]:
                # a build the game files hold is built only when it can be written now: checked before the rebuild
                try:
                    if not write_game or not profiles_mod.enabled(self.profiles.read()):
                        raise ServiceError("state_changed")
                    with self._lock:
                        self._check_stopping(pin)
                        self._untested_apply(item["target"], item["source"], "model", pin, None)
                except (ServiceError, OSError) as error:
                    log_failure(logger, logging.INFO, "Left a costume build for the next start", error)
                    result["left"].append(named)
                    continue
            if item["id"]:
                try:
                    self.update(item["id"], log, pin=pin, fraction=rebuilt)
                except (ServiceError, OSError) as error:
                    if isinstance(error, ServiceError) and error.code in LATER:
                        result["left"].append(named)
                        continue
                    log_failure(logger, logging.WARNING, "Kept an Optimize Physics copy as it is", error)
                    self._keep_costume(item["id"], COSTUME_RULE)
                    result["kept"].append(named)
                    continue
            if item["installed"]:
                try:
                    self.apply(item["target"], item["source"], log, kind="model", pin=pin, fraction=written)
                except (ServiceError, OSError) as error:
                    log_failure(logger, logging.INFO, "Left a costume build for the next start", error)
                    result["left"].append(named)
                    continue
            result["done"].append(named)
        if items:
            logger.info(
                "Costume builds of an older rule: %d built again, %d kept, %d left",
                len(result["done"]),
                len(result["kept"]),
                len(result["left"]),
            )
        return result

    def _entry_identity(self, pin: Pin, core: str, config: dict[str, Any] | None) -> dict[str, str]:
        """Who a toggle's entry is (``_progress_identity``): its core's variant and kind, and its configured source."""
        try:
            variant = self._core_map(pin).key(core) or ""
            kind = self._kind(core, pin) or ""
        except (OSError, ServiceError):
            variant, kind = "", ""
        folder = (((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        source = (folder.get(core.lower()) or {}).get("source")
        return {**self._progress_identity(variant, kind, source), "core": core}

    def _progress_identity(self, variant: str, kind: str, source: str | None) -> dict[str, str]:
        """Say who an item of a job over mods is, for its progress line (``mod_progress``).

        That is the variant and kind it writes, and the mod's own name (``_progress_name``) or the variant whose files
        it takes.
        """
        identity = {"variant": variant or "", "kind": kind or ""}
        if isinstance(source, str) and source.startswith("mod:"):
            record = self._records.get(source[4:])
            if record is not None:
                identity["name"] = self._progress_name(record)
        elif isinstance(source, str) and source.startswith("variant:"):
            identity["source"] = source[8:]
        return identity

    def _progress_name(self, record: dict[str, Any]) -> str:
        """Give a mod's name as its list shows it (``_label``).

        Nothing when that only repeats its target or one of its file names or cores.
        """
        try:
            label = self._label(record)
        except (KeyError, TypeError):
            return ""
        if not isinstance(label, str) or not label:
            return ""
        plain = {str(record.get("target") or "").lower()}
        for name in record.get("files") or []:
            plain.update({str(name).lower(), Path(str(name)).stem.lower(), core_of(str(name))})
        return "" if label.lower() in plain else label

    @_serialized
    def _keep_costume(self, mod_id: str, rule: int) -> None:
        """An Optimize Physics copy kept as it is under costume rule ``rule``: not built again under it."""
        record = self._records.get(mod_id)
        if record is not None and record.get("costume_kept") != rule:
            record["costume_kept"] = rule
            self._save()

    def _failed_writes(self, pin: Pin, config: dict) -> list[str]:
        """List the configured entries of ``pin`` whose last write failed (their saved error).

        Only those not tried again under this build of the App (``retry_failed``) count: an App update may have fixed
        the cause, and nothing else writes such an entry until the user switches it again. None while mods are off: the
        next On reaches every entry.
        """
        if not profiles_mod.enabled(config):
            return []
        folder = (config.get("folders") or {}).get(pin.key) or {}
        if folder.get("retried") == _app_version():
            return []
        with self._lock:
            return [
                core
                for core, choice in sorted((folder.get("configured") or {}).items())
                if isinstance(choice, dict) and choice.get("error") and self._kind(core, pin)
            ]

    def retry_failed(
        self, pin: Pin | None = None, log: LogFunc = no_log, progress: ProgressCallback | None = None
    ) -> ToggleResult | None:
        """Retry failed configured writes once per application build at startup.

        Entries whose last write failed (``_failed_writes``) are written again once
        under this build, as an On's Retry does for them alone. One that is written, or already holds its source,
        loses its error; one that fails keeps a fresh one. Refusals of the moment (the game running, an untested
        build to confirm) leave them all for the next start; otherwise they are not tried again under this build.
        None when there is nothing to try.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install.
            log: Receives user-facing operation messages.
            progress: Receives toggle progress; None disables reporting.
        """
        pin = self._current(pin)
        cores = self._failed_writes(pin, self.profiles.read() or {})
        if not cores:
            return None
        logger.info("Retrying %d entries whose last write failed", len(cores))
        result = self._run(True, True, [pin], log, progress, None, drift=None, retry=cores)
        version = _app_version()

        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key)
            if folder is None or folder.get("retried") == version:
                return False
            folder["retried"] = version
            return True

        with _errors("toggle"):
            self.profiles.update(change)
        logger.info(
            "Entries whose last write failed: %d written, %d failed again", len(result.done), len(result.failed)
        )
        return result

    def _impact_stamp(self, pin: Pin, version: str, records: list[dict[str, Any]], configured: set) -> str:
        """Give what an impact summary depends on.

        That is the resource version, each mod's files and stored baseline (by its modification time) and the configured
        sources. Names, sizes and times only.
        """
        parts = [pin.key, version, sorted(str(source) for source in configured if source)]
        for record in records:
            baseline = self.root / "mods" / record["id"] / fingerprint.FILE_NAME
            try:
                mark = baseline.stat().st_mtime_ns
            except OSError:
                mark = None
            parts.append([record["id"], sorted(record.get("files") or []), record.get("label"), mark])
        return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def update_due(self, pin: Pin | None = None) -> dict[str, Any]:
        """Determine whether startup should offer an impact check or retry background maintenance.

        A check is due when the install's resource version
        differs from the last one seen there and the library has mods. Reads only the version file and the
        configuration. With no version seen yet (a first start, a newly chosen folder) or no mod, the version is
        recorded as seen at once and nothing is asked. Never fails: a version that cannot be read asks nothing.

        ``outdated``: whatever the version, the App built something from a costume under an older costume rule
        (``_outdated_costumes``): the start builds it again first (``refresh_costumes``), without asking.
        ``retry``: entries whose last write failed, not tried again under this build of the App (``_failed_writes``):
        the start writes them again (``retry_failed``), without asking; after the check while ``due``.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.
        """
        pin = pin or self.current_pin()
        out: dict[str, Any] = {
            "due": False,
            "outdated": False,
            "retry": False,
            "install": None,
            "version": None,
            "previous": None,
        }
        if pin is None:
            return out
        version = self.resource_version(pin)
        config = self.profiles.read() or {}
        try:
            out["retry"] = bool(self._failed_writes(pin, config))
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.DEBUG, "Could not look for entries whose last write failed", error)
        seen = ((config.get("folders") or {}).get(pin.key) or {}).get("resource") or {}
        previous = seen.get("version") if isinstance(seen, dict) else None
        out.update(install=pin.key, version=version, previous=previous)
        try:
            out["outdated"] = bool(self._outdated_costumes(pin, config))
        except (OSError, ServiceError) as error:
            log_failure(logger, logging.DEBUG, "Could not look for costume builds of an older rule", error)
        if version is None or version == previous:
            return out
        with self._lock:
            has_mods = bool(self._records)
        if previous is None or not has_mods:
            try:
                self.impact_seen(pin.key, version)
            except ServiceError as error:
                log_failure(logger, logging.DEBUG, "Could not record the resource version as seen", error)
            return out
        out["due"] = True
        return out

    def impacts(self, ids: Sequence[str], pin: Pin | None = None) -> list[dict[str, str]]:
        """Classify update impacts for selected mods, omitting missing IDs.

        Args:
            ids: Stored mod IDs to classify in the supplied order.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            Mod IDs paired with fingerprint.impact categories.
        """
        pin = pin or self.current_pin()
        out = []
        for mod_id in ids:
            with self._lock:
                record = self._records.get(mod_id)
                if record is not None:
                    out.append({"id": mod_id, "impact": self._impact(record, pin)})
        return out

    def impact_seen(self, install: str, version: str) -> None:
        """Record a resource version as seen because its impact summary was shown or unnecessary.

        Args:
            install: Install key whose resource notification is acknowledged.
            version: Nonempty resource version to record with the acknowledgement time.
        """
        if not isinstance(install, str) or not isinstance(version, str) or not version:
            raise ServiceError("bad_params", key="version")
        if self._writes_refused():
            return

        def change(data: dict) -> bool:
            folder = data["folders"].setdefault(install, {})
            if (folder.get("resource") or {}).get("version") == version:
                return False
            folder["resource"] = {"version": version, "seen": now()}
            return True

        with _errors("impact_seen"):
            self.profiles.update(change)

    def untested_version(self, pin: Pin) -> str | None:
        """Return a pinned install's untested resource version, or None if tested, unknown, or unreadable."""
        tested = tested_build.for_server(pin.server)
        if tested is None:
            return None
        version = self.resource_version(pin)
        return None if version is None or version in tested.versions else version

    def _untested_cores(self, pin: Pin, targets: Iterable[Path], version: str | None) -> list[str]:
        """Find the cores of ``targets`` that the untested build of resource version ``version`` changed.

        ``version`` is the one ``untested_version`` gives. A core counts when the user has not confirmed writing it and
        it has a bundle whose name the tested build does not have (a part rebuilt under a new CRC, or a new part). Names
        only.
        """
        targets = list(targets)
        if version is None or not targets:
            return []
        tested = tested_build.for_server(pin.server)
        confirmed = self._untested_ok.get((pin.key, version), set())
        index = self._index(pin)
        changed = []
        for core_name in sorted({core_of(path.name) for path in targets}):
            names = [
                path.name
                for path in index.get(core_name) or [path for path in targets if core_of(path.name) == core_name]
            ]
            if core_name not in confirmed and any(not tested.has(name) for name in names):
                changed.append(core_name)
        return changed

    def _untested_entry(
        self, pin: Pin, core: str, targets: Sequence[Path], variant: str | None = None, kind: str | None = None
    ) -> dict[str, Any]:
        """Name an entry of an untested build's confirmation.

        It is {install, core, variant, kind}, with its file names for a generic part.
        """
        kind = kind or self._kind(core, pin) or "spr"
        entry = {
            "install": pin.key,
            "core": core,
            "variant": variant or self._core_map(pin).key(core) or core,
            "kind": kind,
        }
        if kind == "other":
            entry["files"] = [{"file": path.name} for path in targets]
        return entry

    def _check_untested(self, writes: Sequence[tuple[Pin, dict[str, Any], Sequence[Path]]]) -> None:
        """Refuse writes onto cores an untested build changed until the user confirms them.

        The refusal is ``untested_build``, a refusal of the moment: nothing is written or configured. ``writes`` holds,
        per entry to write, its install, the entry the refusal names (``_untested_entry``) and its target files. The
        refusal's ``confirm`` ({install: {version, cores}}) is what ``confirm_untested`` takes back.
        """
        entries: list[dict[str, Any]] = []
        confirm: dict[str, dict[str, Any]] = {}
        versions: dict[str, str | None] = {}  # read once per install
        for pin, entry, targets in writes:
            if pin.key not in versions:
                versions[pin.key] = self.untested_version(pin)
            version = versions[pin.key]
            cores = self._untested_cores(pin, targets, version)
            if not cores:
                continue
            entries.append(entry)
            item = confirm.setdefault(pin.key, {"version": version, "cores": []})
            item["cores"] = sorted({*item["cores"], *cores})
        if entries:
            logger.info("Untested game build: %d entries need a confirmation", len(entries))
            raise ServiceError("untested_build", entries=entries, confirm=confirm)

    def confirm_untested(self, confirm: Mapping[str, Mapping[str, Any]]) -> None:
        """Record confirmed writes to changed cores for this process and resource version.

        Args:
            confirm: Refusal payload mapping at most 64 install keys to a nonempty version
                and a list of at most 65536 nonempty core names per install.

        Raises:
            ServiceError: The confirmation payload is malformed (bad_params).
        """
        if not isinstance(confirm, Mapping) or len(confirm) > 64:
            raise ServiceError("bad_params", key="confirm")
        for install, item in confirm.items():
            cores = item.get("cores") if isinstance(item, Mapping) else None
            version = item.get("version") if isinstance(item, Mapping) else None
            if (
                not isinstance(install, str)
                or not isinstance(version, str)
                or not version
                or not isinstance(cores, list)
                or len(cores) > 65536
                or not all(isinstance(core, str) and core for core in cores)
            ):
                raise ServiceError("bad_params", key="confirm")
            self._untested_ok.setdefault((install, version), set()).update(cores)
            logger.info("Untested game build: %d cores confirmed in %s", len(cores), install)

    def _backfill_built_against(self, record: dict[str, Any], pin: Pin | None) -> dict | None:
        """Build a fingerprint for a mod stored without one, from evidence alone.

        The evidence is the official files of its core (the install's current parts and the kept originals) whose
        atlases, skeletons and textures the mod's own files carry unchanged. One file per part: the install's part
        before a kept original. It is stored, as incomplete (other parts of the core are not known). None, and nothing
        stored, when no file shows it.
        """
        try:
            paths = self._paths(record)
            files = {path.name: path for path in paths}
            miss = (
                record["id"],
                tuple(sorted(self._mod_hashes(record).items())),
                pin.key if pin else None,
                tuple(sorted(self._originals())),
            )
            if miss in self._backfill_misses:
                return None
            candidates: list[tuple[str, Path]] = []
            if pin is not None:
                for target in self._core_targets(files, pin):
                    try:
                        candidates.append((target.name, self._official_for(target, pin, game_first=True)))
                    except ServiceError as error:
                        if error.code not in ("original_unavailable", "original_invalid"):
                            raise
            generic = record["kind"] == "other"
            cores = {core_of(name) for name in files}
            keys = {integrity.replacement_key(name) for name in files}
            for name in sorted(self._originals()):
                if (integrity.replacement_key(name) in keys) if generic else (core_of(name) in cores):
                    kept = self.root / "originals" / name
                    if self._is_official(kept):
                        candidates.append((name, kept))
            inspection = integrity.Inspection()
            mod = fingerprint.carried(inspection, paths)
            parts, taken = [], set()
            for name, official in candidates:
                key = integrity.replacement_key(name)
                if key in taken:
                    continue
                item = fingerprint.part(inspection, official, name=name, sha1=self._hashes.get(official).sha1)
                if fingerprint.standing(item, mod) == fingerprint.SAME:
                    item["mod"] = fingerprint.SAME
                    parts.append(item)
                    taken.add(key)
            if not parts:
                if len(self._backfill_misses) >= 4096:
                    self._backfill_misses.clear()
                self._backfill_misses.add(miss)
                return None
            data = fingerprint.document("backfill", parts, complete=False)
            self._write_built_against(record, data)
            return self._read_built_against(record) or data
        except Exception as error:  # noqa: BLE001 - evidence that cannot be read is no evidence
            log_failure(logger, logging.DEBUG, "No fingerprint made for the mod", error)
            return None

    def _backfill_before_release(self, name: str) -> None:
        """Backfill fingerprints before a kept original goes.

        The mods of its core stored without a fingerprint get theirs while the file that shows it is still here
        (``_backfill_built_against``, kept originals only). Never raises.
        """
        try:
            key, core = integrity.replacement_key(name), core_of(name)
            for record in list(self._records.values()):
                names = record.get("files") or []
                if record["kind"] == "other":
                    if key not in {integrity.replacement_key(file) for file in names}:
                        continue
                elif core not in {core_of(file) for file in names}:
                    continue
                if self._read_built_against(record) is None:
                    self._backfill_built_against(record, None)
        except Exception as error:  # noqa: BLE001 - releasing the original does not wait for it
            log_failure(logger, logging.DEBUG, "No fingerprint made before the release", error)
