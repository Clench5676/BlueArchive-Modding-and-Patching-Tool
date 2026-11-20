"""The Enable Mods switch: its status, the toggle run, unfinished entries and their prompts."""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from bmpt.engine import (
    LogFunc,
    ProgressCallback,
    no_log,
)
from ...core.errors import ServiceError
from ...core.logging_utils import log_failure
from .. import profiles as profiles_mod
from .. import mod_progress, safety, toggle_timing
from ..catalog_switch import MODE_CATALOG, MODE_FILES, mode_of
from ..judge import (
    LIBRARY_SOURCES,
    sorted_resource_types,
    core_of,
    distinguishing_folders,
    resource_type_of_file,
    leaves,
    now,
)
from ..records import (
    Pin,
    ToggleResult,
)
from .base import (
    SAVE_EVERY,
    STANDING_STEP,
    STOPPING,
    _Entry,
    _Unread,
    _View,
    _background,
    _errors,
    _folder_size,
    _serialized,
)


logger = logging.getLogger(__package__)


def _method(pin: Pin, record: dict[str, Any] | None) -> dict[str, Any]:
    """How an install switches mods, as its catalog record says; an install without one replaces files."""
    if isinstance(record, dict) and record.get("mode") in (MODE_CATALOG, MODE_FILES):
        return {"method": record["mode"], "methodReason": record.get("reason"), "methodVersion": record.get("rv")}
    try:
        reason = mode_of(pin)[1]
    except OSError:
        reason = None
    return {"method": MODE_FILES, "methodReason": reason, "methodVersion": None}


class EnableMixin:
    """The Enable Mods switch: its status, the toggle run, unfinished entries and their prompts."""

    def status(self) -> dict[str, Any]:
        """Build the switch snapshot from names and profiles without locking or walking storage.

        Include known installs, the current install, intent, configured count, and
        reason (mod_configuration_unreadable or None). Each install's
        ``distinct`` is the shortest trailing part of its path that tells it from the other installs of its server
        (for the install label, "Global (BlueArchive)"), None when it is the only one of its server. ``method`` is
        how mods are switched there (``catalog`` or ``files``), ``methodReason`` why an install uses files (a
        refusal code of the catalog checks, or a layout reason), and ``methodVersion`` the resource version that
        answer was recorded for; they come from the catalog store, read without the library lock.
        """
        pins = self.installs()
        named = {pin.key for pin in self._named_pins()}
        current = self.current_pin()
        servers: dict[str, dict[str, Path]] = {}
        for pin in pins:
            servers.setdefault(pin.server, {})[pin.key] = pin.root
        distinct: dict[str, str] = {}
        for roots in servers.values():
            if len(roots) > 1:
                distinct.update(distinguishing_folders(roots))
        catalogs = self._catalog_records_for_read()
        out: dict[str, Any] = {
            "installs": [
                {
                    "id": pin.id,
                    "key": pin.key,
                    "server": pin.server,
                    "folder": str(pin.root),
                    "named": pin.key in named,
                    "distinct": distinct.get(pin.key),
                    **_method(pin, catalogs.get(pin.key)),
                }
                for pin in pins
            ],
            "current": current.key if current else None,
            "reason": None,
        }
        try:
            config = self.profiles.read()
            out.update(self.intent())
        except ServiceError as error:
            out.update(
                enabled=True,
                toggled=None,
                unfinished={why: 0 for why in profiles_mod.WHY},
                held=[],
                reason=error.code,
                file=error.params.get("file"),
            )
            config = None
        keys = {pin.key for pin in pins}
        out["configured"] = sum(
            len(folder.get("configured") or {})
            for key, folder in ((config or {}).get("folders") or {}).items()
            if key in keys
        )
        return out

    def sizes(self, kind: str) -> dict[str, int]:
        """Count stored bundle bytes for one help popup or Settings row.

        Args:
            kind: Storage category to count, built or officials.

        Returns:
            A builtSize or officialsSize field containing the bundle byte count.

        Raises:
            ServiceError: The storage category is invalid (bad_params).
        """
        if kind not in ("built", "officials"):
            raise ServiceError("bad_params", key="kind")
        folder = "originals" if kind == "officials" else kind
        return {f"{kind}Size": _folder_size(self.root / folder)}

    def _plan_toggle(
        self, enabled: bool, pins: list[Pin], config: dict | None, found: list[_Entry]
    ) -> list[tuple[Pin, str]]:
        """List the entries a toggle reaches, in every known install.

        For an Off with scan consent, that is every entry its own pass over the files found (``found``), and for any Off
        the configured entries and those with records. For an On, the configured entries, those with a record of a
        removed mod, and those the unfinished list holds.
        """
        plan: list[tuple[Pin, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(pin: Pin, core: str) -> None:
            core = self._core_map(pin).slot(core)
            if (pin.key, core) not in seen and self._resource_type(core, pin):
                seen.add((pin.key, core))
                plan.append((pin, core))

        folders = (config or {}).get("folders") or {}
        for entry in found:
            add(entry.pin, entry.core)
        for pin in pins:
            folder = folders.get(pin.key) or {}
            for core_name in sorted(folder.get("configured") or {}):
                add(pin, core_name)
            for name, record in sorted(self._applied.items()):
                path = Path(name)
                if path.parent in pin.dirs and (not enabled or record.get("removed")):
                    add(pin, core_of(path.name))
        if enabled:
            keys = {pin.key: pin for pin in pins}
            for item in (config or {}).get("unfinished", []):
                if item["install"] in keys:
                    add(keys[item["install"]], item["core"])
        return plan

    def _hidden_items(self, config: dict | None) -> list[dict[str, str]]:
        """List the entries of the installs this data directory removed from its list.

        They are listed as not switched for the data directory that names them (a toggle here leaves their files as they
        are).
        """
        items = []
        for key, folder in ((config or {}).get("folders") or {}).items():
            if self.data_id not in (folder.get("hidden") or []):
                continue
            cores = set(folder.get("configured") or {})
            root = Path(key)
            cores.update(
                core_of(Path(path).name)
                for path in self._applied
                if Path(path).is_relative_to(root) and resource_type_of_file(Path(path).name)
            )
            pin = self._recorded_pin(key, folder)
            if pin is not None:
                mapped = self._core_map(pin)
                cores = {mapped.slot(core) for core in cores}
            items.extend({"install": key, "core": core, "why": "pending"} for core in sorted(cores))
        return items

    def toggle(
        self,
        enabled: bool,
        expect: bool,
        pins: list[Pin] | None = None,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ToggleResult:
        """Switch known installs between configured sources and official files, preserving choices.

        An Off adopts the mods
        it finds first (with scan consent its own pass finds them; without, the changed files it meets), keeps the
        built copies, then switches every entry carrying a library source to official. An On switches every
        configured entry that does not carry its source to it, from the built copy when it still fits; a file
        changed outside the library is skipped (``why`` ``changed``) and left as it is. The
        configured source choices stay intact; the intent and the entries not switched yet are saved first.
        ``expect`` is the intent the caller saw (``state_changed`` otherwise); Retry is the same call with ``expect``
        equal to the current intent.

        On an install switched by its catalog, the swap alone switches every entry whose placed files and
        official-named files are as recorded (``_standing_on``, ``_standing_off``): they are not read again. Such an
        install's Off makes no pass over the game files; files that are neither official nor a library source are
        left to the scan that follows.

        Args:
            enabled: Desired master-switch intent; False restores official files and True applies choices.
            expect: Intent observed by the caller, checked before changing it; repeat enabled for Retry.
            pins: Game installs and servers the operation is fixed to; None uses every known install.
            log: Receives user-facing operation messages.
            progress: Receives per-entry build and write dictionaries; None disables reporting.
            cancel: Requests a stop between switch entries; None processes the full switch plan.

        Raises:
            ServiceError: Intent or installs changed, required consent or confirmation is absent,
                or a stopping write refusal prevents the job.
        """
        with toggle_timing.toggle(enabled):
            with self._lock:
                pins = list(pins) if pins is not None else self.installs()
                self._check_pins(pins)
                if profiles_mod.enabled(self.profiles.read()) != expect:
                    raise ServiceError("state_changed")
            files_only = set()
            if enabled:
                for pin in pins:
                    try:
                        with _errors("prepare catalog toggle"):
                            self._refresh_catalog_version(pin)
                            self._migrate_catalog(pin, progress)
                    except ServiceError as error:
                        if error.code in STOPPING:
                            raise
                        files_only.add(pin.key)
                        log_failure(logger, logging.INFO, "Catalog switch deferred", error)
            result = self._run(
                enabled, expect, pins, log, progress, cancel, drift=None, files_only=files_only, fallback=True
            )
            for pin in pins:
                if pin.key in files_only:
                    self._defer_catalog_migration(pin)
            if not enabled and not (cancel and cancel()):
                for pin in pins:
                    if pin.key in files_only:
                        continue
                    try:
                        self._migrate_catalog(pin, progress)
                    except ServiceError as error:
                        log_failure(logger, logging.INFO, "Catalog switch deferred", error)
            return result

    def restore_drift(
        self,
        entries: list[dict],
        pins: list[Pin] | None = None,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ToggleResult:
        """Reapply configured sources to selected reset, updated, or explicitly changed entries.

        Switch each reset or updated entry to its
        configured source again, the whole core, from its built copy when it still fits; its configuration stays.
        A changed entry is switched only when listed as changed (its outside change is kept as a mod first); one
        that turned changed since it was listed is skipped.

        Args:
            entries: Install/core/state descriptors from the scan; changed state authorizes
                adopting outside edits first.
            pins: Game installs and servers the operation is fixed to; None uses every known install.
            log: Receives user-facing operation messages.
            progress: Receives per-entry build and write dictionaries; None disables reporting.
            cancel: Returns True to stop at an entry boundary; None completes the job.
        """
        return self._run(None, None, pins, log, progress, cancel, drift=entries)

    def _run(
        self,
        enabled: bool | None,
        expect: bool | None,
        pins: list[Pin] | None,
        log: LogFunc,
        progress: ProgressCallback | None,
        cancel: Callable[[], bool] | None,
        drift: list[dict] | None,
        retry: Sequence[str] | None = None,
        *,
        catalog_reconcile: bool = False,
        files_only: set[str] | None = None,
        fallback: bool = False,
        staged_only: bool = False,
    ) -> ToggleResult:
        """Run a toggle, Retry, Apply Mod Configuration or the start's retry of failed writes.

        The kinds are a toggle, Retry (``drift`` None), Apply Mod Configuration (``drift``, the listed entries) and the
        start's retry of failed writes (``retry``, cores of the one install ``pins``: an On's Retry of those entries
        alone, the intent unchanged).

        Class 2 and the class 3 catalog reconcile take the library lock for planning and for one entry at a time,
        yielding between entries. Catalog preparation and publication share a transaction. Generation and swaps
        release the lock. Class 3 leaves pruning to maintenance.

        ``staged_only``, with ``drift`` and mods off: the listed entries of an install switched by its catalog are
        built and placed and the redirect set is published beside the live catalog, which stays the official one.
        """
        found: list[_Entry] = []
        files_only = set() if files_only is None else files_only
        full_toggle = drift is None and retry is None

        def check_pins() -> None:
            if catalog_reconcile:
                if pins[0] not in self.installs():
                    raise ServiceError("folder_changed")
            else:
                self._check_pins(pins)

        with toggle_timing.enter("library_wait", self._lock), _errors("toggle"), toggle_timing.stage("prepare"):
            pins = list(pins) if pins is not None else self.installs()
            self._check_start(pins, drift is None, expect, scoped=catalog_reconcile)
            config = self.profiles.read()
            current = profiles_mod.enabled(config)
            if drift is None and retry is None:
                from ..portrait_edit import refresh_missing
                from ..memorial_lobby_camera import refresh

                refresh_missing(self, pins, log)
                refresh(self, pins, log)
                config = self.profiles.read()
            allowed = drift is None and self._scan_allowed()  # Keep consent fixed for planning and execution.
            # an Off with scan consent plans from the files as they are now: its own pass, read outside the lock.
            # An install switched by its catalog is turned off by the swap alone: files that are neither official
            # nor a library source are left to the scan that follows the toggle
            swapped = self._catalog_records_for_read() if drift is None and not enabled and allowed and pins else {}
            scanned = [pin for pin in pins if swapped.get(pin.key, {}).get("mode") != MODE_CATALOG]
            view = (
                self._view(scanned, "scan", config, snapshot=True, official_only=True)
                if drift is None and not enabled and allowed and scanned
                else None
            )
        if view is not None:
            # previews go first: one loads while the pass runs (which pauses itself while a chibi preview is exported)
            with _errors("toggle"), toggle_timing.stage("off_scan"):
                found = view.entries(scanned, background=True)
        with toggle_timing.enter("library_wait", self._lock), _errors("toggle"), toggle_timing.stage("plan"):
            if view is not None:  # what the start checked may have changed during the pass
                self._check_start(pins, True, expect)
                config = self.profiles.read()
            if retry is not None:
                enabled = True
                plan = [(pins[0], self._core_map(pins[0]).slot(core)) for core in retry]
                listed = {}
            elif drift is not None:
                if not current and not staged_only:
                    # mods off: official files are the intent, there is no drift to apply
                    raise ServiceError("state_changed")
                enabled = True
                by_key = {pin.key: pin for pin in pins}
                plan = [
                    (by_key[item["install"]], item["core"].lower())
                    for item in drift
                    if isinstance(item, dict) and item.get("install") in by_key and isinstance(item.get("core"), str)
                ]
                listed = {
                    (item["install"], item["core"].lower()): item.get("state")
                    for item in drift
                    if isinstance(item, dict) and isinstance(item.get("core"), str)
                }
            else:
                plan = self._plan_toggle(enabled, pins, config, found)
                listed = {}
                if not enabled:
                    self._check_space(self._off_space(plan, found, config))
            hidden = self._hidden_items(config) if drift is None and retry is None else []
            installs = self._catalog_records_for_read() if pins else {}
            if enabled and fallback:
                for pin in pins:
                    record = installs.get(pin.key)
                    if not record or record["mode"] != MODE_CATALOG:
                        continue
                    try:
                        if (
                            record["rv"] != self.resource_version(pin)
                            or self._catalog_live(pin, record) is None
                            or not self._catalog_library_copy(pin, record["rv"]).is_file()
                            or self._catalog_check_pending(pin, record["rv"])
                        ):
                            files_only.add(pin.key)
                    except (OSError, ValueError, ServiceError):
                        files_only.add(pin.key)
            catalog_pins = {
                pin.key: pin
                for pin in pins
                if pin.key not in files_only
                and installs.get(pin.key, {}).get("mode") == MODE_CATALOG
                and (drift is None and retry is None or any(current.key == pin.key for current, _ in plan))
            }
            if not enabled:
                planned = {(pin.key, core) for pin, core in plan}
                for key, pin in catalog_pins.items():
                    record = installs[key]
                    if record["live"] == "mods":
                        mapped = self._core_map(pin)
                        redirected = {mapped.slot(core_of(name)) for name in record["redirects"]}
                        plan.extend((pin, core) for core in sorted(redirected) if (key, core) not in planned)
            toggle_timing.planned(len(plan))
            standing: dict[tuple[str, str], dict[str, dict]] = {}
            if enabled and full_toggle:
                with toggle_timing.stage("standing_check"):
                    for key, pin in catalog_pins.items():
                        cores = [core for current, core in plan if current.key == key]
                        for core, redirects in self._standing_on(pin, cores, config, installs[key]).items():
                            standing[(key, core)] = redirects
            if enabled:  # an On, its Retry or Apply Mod Configuration: confirmed before the intent changes
                self._untested_plan(plan, config, drift is not None)
            with toggle_timing.stage("progress_identity"):
                identities = (
                    {(pin.key, core): self._entry_identity(pin, core, config) for pin, core in plan} if progress else {}
                )
        stamp = now()
        if drift is None and retry is None:

            def intent(data: dict) -> None:
                data["enabled"], data["toggled"] = enabled, stamp
                listed_keys = {(item["install"], item["core"]) for item in data["unfinished"]}
                for pin, core in plan:
                    if (pin.key, core) not in listed_keys:
                        data["unfinished"].append({"install": pin.key, "core": core, "why": "pending"})
                        listed_keys.add((pin.key, core))
                for item in hidden:
                    if (item["install"], item["core"]) not in listed_keys:
                        data["unfinished"].append(item)
                        listed_keys.add((item["install"], item["core"]))

            with _errors("toggle"), toggle_timing.stage("outcomes"):
                self.profiles.update(intent)
            logger.info("Mods %s: %d entries to reach", "on" if enabled else "off", len(plan))
        result = {
            "done": [],
            "adopted": [],
            "failed": [],
            "stuck": [],
            "left": [],
            "skipped": [],
            "dropped": [],
            "held": [],
        }
        outcomes: dict[tuple[str, str], str | None] = {}
        cores: set[str] = set()
        resource_types: set[str] = set()
        saved_at, unsaved = time.monotonic(), 0
        stop: ServiceError | None = None
        was_paced = getattr(_background, "paced", False)
        _background.paced = True  # other callers of the library go first between its entries (_let_others_in)
        line = mod_progress.ItemProgress(progress, max(1, len(plan) - len(standing)), "apply" if enabled else "restore")
        batches: dict[str, dict[str, dict]] = {key: {} for key in catalog_pins}
        reached: dict[str, dict[str, dict]] = {key: {} for key in catalog_pins}
        off_redirects: dict[str, set[str]] = {}
        failed_off: set[tuple[str, str]] = set()
        unavailable: set[str] = set()
        catalog_done: dict[str, list[dict]] = {key: [] for key in catalog_pins}
        settled: dict[str, list[str]] = {}
        walked = 0
        try:
            with self._catalog_transaction() if catalog_pins else contextlib.nullcontext():
                if not enabled:
                    for key, pin in catalog_pins.items():
                        if cancel and cancel():
                            break
                        self._let_others_in()
                        with self._lock:
                            check_pins()
                            record = self._catalog_record(pin)
                            mapped = self._core_map(pin)
                            redirected = {mapped.slot(core_of(name)) for name in record["redirects"]}
                        try:
                            with _errors("toggle"):
                                self._timed_swap(pin, "official")
                        except ServiceError as error:
                            with self._lock:
                                for core in sorted(redirected):
                                    item = self._toggle_entry(pin, core)
                                    result["failed"].append(
                                        {"entry": item, "code": error.code, "params": error.to_json()["params"]}
                                    )
                                    outcomes[(key, core)] = "pending"
                                    failed_off.add((key, core))
                                files_only.add(key)
                        else:
                            if record["live"] == "mods":
                                off_redirects[key] = redirected
                                if full_toggle:
                                    with self._lock, toggle_timing.stage("standing_check"):
                                        for core in self._standing_off(
                                            pin, redirected & {c for p, c in plan if p.key == key}
                                        ):
                                            standing[(key, core)] = {}
                position = 0
                for pin, core in plan:
                    if cancel and cancel():
                        break
                    if (pin.key, core) in standing:
                        # nothing of it is read or written: the swap alone switches it
                        item = self._toggle_entry(pin, core, identities.get((pin.key, core)))
                        toggle_timing.standing()
                        if enabled:
                            batches[pin.key].update(standing[(pin.key, core)])
                            reached[pin.key][core] = item
                            catalog_done[pin.key].append(item)
                            outcomes[(pin.key, core)] = "pending"
                        else:
                            settled.setdefault(pin.key, []).append(core)
                            result["done"].append(item)
                            outcomes[(pin.key, core)] = None
                            cores.add(core)
                            resource_types.add(item["resource_type"])
                        continue
                    position += 1
                    walked += 1
                    i = position
                    with toggle_timing.stage("preview_yield"):
                        self._let_others_in()
                    with toggle_timing.enter("library_wait", self._lock), toggle_timing.entry():
                        batch = None
                        try:
                            with _errors("toggle"):
                                check_pins()
                                if progress:
                                    line.item(i, **identities[(pin.key, core)], step=mod_progress.STEP_CHECK)
                                if enabled and pin.key in batches and pin.key not in files_only:
                                    choice = ((config or {}).get("folders") or {}).get(pin.key) or {}
                                    source = ((choice.get("configured") or {}).get(core) or {}).get("source")
                                    if self._catalog_needs_mod_update(installs[pin.key], core, source):
                                        result["skipped"].append(
                                            {**self._toggle_entry(pin, core), "why": "entry_changed"}
                                        )
                                        outcomes[(pin.key, core)] = None
                                        line.done()
                                        continue
                                eligible = False
                                if enabled and pin.key in batches and pin.key not in files_only:
                                    try:
                                        eligible = self._catalog_targets(
                                            pin, self._spine_parts(core, pin), installs[pin.key]
                                        )
                                    except ServiceError as error:
                                        if fallback and error.code not in STOPPING:
                                            files_only.add(pin.key)
                                            unavailable.add(pin.key)
                                        else:
                                            if error.code == "catalog_changed":
                                                unavailable.add(pin.key)
                                            raise
                                if eligible:
                                    batch = batches[pin.key]
                                    reached[pin.key][core] = {
                                        "install": pin.key,
                                        "core": core,
                                        **identities.get((pin.key, core), {}),
                                    }
                                outcome, why, entry = self._reach(
                                    pin,
                                    core,
                                    enabled,
                                    allowed,
                                    listed.get((pin.key, core)),
                                    drift is not None,
                                    log,
                                    fraction=line.part(0.0, 1.0, build=0.9),
                                    **({"redirect_batch": batch} if batch is not None else {}),
                                    **(
                                        {"official_only": True}
                                        if pin.key in files_only or (not enabled and pin.key in catalog_pins)
                                        else {}
                                    ),
                                )
                        except ServiceError as error:
                            item = self._toggle_entry(pin, core)
                            if batch is not None:
                                reached[pin.key][core] = item
                            stops = error.code in STOPPING and not (
                                batch is not None and error.code == "untested_build"
                            )
                            if not stops:  # a refusal of the moment belongs to the switch, not the choice
                                self._selection_error(pin, [core], error.to_json())
                            result["failed"].append(
                                {"entry": item, "code": error.code, "params": error.to_json()["params"]}
                            )
                            outcomes[(pin.key, core)] = "pending"
                            if stops:
                                stop = error
                                break
                            continue
                    if batch is not None:
                        reached[pin.key][core] = entry
                        if "done" in outcome:
                            catalog_done[pin.key].append(outcome.pop("done"))
                            why = "pending"
                    if entry is not None and not outcome and core in off_redirects.get(pin.key, ()):
                        outcome["done"] = entry
                    outcomes[(pin.key, core)] = "pending" if (pin.key, core) in failed_off else why
                    if "done" in outcome:
                        self._selection_error(pin, [core], None)
                    for name, value in outcome.items():
                        result[name].append(value)
                    if entry is not None and ("done" in outcome or "adopted" in outcome):
                        cores.add(core)
                        resource_types.add(entry["resource_type"])
                    unsaved += 1
                    if unsaved >= SAVE_EVERY[0] or time.monotonic() - saved_at >= SAVE_EVERY[1]:
                        with _errors("toggle"):
                            self._save_outcomes(outcomes)
                        outcomes, unsaved, saved_at = {}, 0, time.monotonic()
                    line.done()
                if enabled and stop is None and not (cancel and cancel()):
                    for key, pin in catalog_pins.items():
                        if key in unavailable and not (fallback and reached[key]):
                            continue
                        self._let_others_in()
                        try:
                            with self._lock:
                                check_pins()
                            if key in unavailable:
                                raise ServiceError("catalog_changed")
                            self._regenerate(
                                pin,
                                batches[key],
                                drop=set(reached[key]),
                                replace_all=full_toggle,
                                **({"keep_official": True} if staged_only and not current else {}),
                            )
                        except ServiceError as error:
                            if error.code in STOPPING:
                                stop = error
                                break
                            if fallback:
                                files_only.add(key)
                                log_failure(logger, logging.INFO, "Catalog switch deferred", error)
                                for core, item in reached[key].items():
                                    self._let_others_in()
                                    try:
                                        with self._lock, _errors("toggle files"):
                                            check_pins()
                                            written, why, _ = self._reach(
                                                pin, core, True, allowed, None, False, log, official_only=True
                                            )
                                    except ServiceError as file_error:
                                        if file_error.code in STOPPING:
                                            stop = file_error
                                            break
                                        result["failed"].append({"entry": item, **file_error.to_json()})
                                        outcomes[(key, core)] = "pending"
                                        continue
                                    result["failed"] = [
                                        failure
                                        for failure in result["failed"]
                                        if (failure["entry"]["install"], failure["entry"]["core"]) != (key, core)
                                    ]
                                    for name, value in written.items():
                                        result[name].append(value)
                                    if "done" in written:
                                        self._selection_error(pin, [core], None)
                                        cores.add(core)
                                        resource_types.add(item["resource_type"])
                                    outcomes[(key, core)] = why
                                if stop is not None:
                                    break
                                continue
                            if not reached[key]:
                                raise
                            for core, item in reached[key].items():
                                if any(
                                    failure["entry"]["install"] == key and failure["entry"]["core"] == core
                                    for failure in result["failed"]
                                ):
                                    continue
                                self._selection_error(pin, [core], error.to_json())
                                result["failed"].append({"entry": item, **error.to_json()})
                                outcomes[(key, core)] = "pending"
                            continue
                        settled.setdefault(key, []).extend(item["core"] for item in catalog_done[key])
                        for item in catalog_done[key]:
                            core = item["core"]
                            result["done"].append(item)
                            outcomes[(key, core)] = None
                            cores.add(core)
                            resource_types.add(item["resource_type"])
        finally:
            _background.paced = was_paced
            self.checks.flush()
        by_key = {pin.key: pin for pin in pins}
        for key, done_cores in settled.items():
            with _errors("toggle"):
                self._clear_done(by_key[key], done_cores)
        # An interrupted run leaves its last saved checkpoint for Retry. A run that walked no entry wrote no game file
        # and kept no original: it leaves the prune to maintenance.
        # The start's retry runs behind the start cover under its entries' line: it leaves the prune to maintenance too.
        if retry is not None:
            self._prune_due = True
        elif not catalog_reconcile and (walked or not standing):
            with toggle_timing.enter("library_wait", self._lock), toggle_timing.stage("prune"):
                self._prune_quietly()
        with _errors("toggle"):
            self._save_outcomes(outcomes)
        if stop is not None:
            raise stop
        return ToggleResult(
            bool(enabled),
            result["done"],
            result["adopted"],
            result["failed"],
            result["stuck"],
            result["left"],
            result["skipped"],
            result["dropped"],
            sorted(cores),
            sorted_resource_types(resource_types),
            result["held"],
        )

    def _toggle_entry(self, pin: Pin, core: str, identity: dict | None = None) -> dict[str, str]:
        identity = identity or {}
        return {
            "install": pin.key,
            "core": core,
            "resource_type": identity.get("resource_type") or self._resource_type(core, pin) or "portrait",
            "alt": identity.get("alt") or self._core_map(pin).key(core) or core,
        }

    def _standing_on(self, pin: Pin, cores: list[str], config: dict | None, record: dict) -> dict[str, dict[str, dict]]:
        """Find the entries of an On that the catalog swap alone switches, with their recorded redirects.

        An entry stands when its configured library source still has every part's redirect recorded for the
        catalog's resource version, each placed file is there as recorded and still fits the source
        (``_reusable_redirects``), and every official-named part is official. Such an entry was admitted when its
        redirect was recorded; nothing of it is read again, no bundle is opened. Any doubt leaves the entry to
        ``_reach``. Class 2, under the library lock in steps of ``STANDING_STEP`` entries, stepping aside for a
        waiting preview or page read between steps.
        """
        configured = (((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        standing: dict[str, dict[str, dict]] = {}
        stamp_of = None
        for number, core in enumerate(cores):
            if number and number % STANDING_STEP == 0 and hasattr(self._lock, "step_aside"):
                self._lock.step_aside()
            source = (configured.get(core) or {}).get("source")
            if not isinstance(source, str) or not source.startswith(LIBRARY_SOURCES):
                continue
            try:
                if self._catalog_needs_mod_update(record, core, source):
                    continue
                targets = self._spine_parts(core, pin)
                if not targets or not self._catalog_targets(pin, targets, record):
                    continue
                if not all(self._untouched_official(path) for path in targets):
                    continue
                stamp_of = self._placed_stamps(pin) if stamp_of is None else stamp_of
                redirects = self._reusable_redirects(
                    pin,
                    core,
                    targets,
                    source,
                    self._resource_type(core, pin) or "portrait",
                    record=record,
                    stamp_of=stamp_of,
                )
            except (OSError, ValueError, ServiceError):
                continue
            if redirects:
                standing[core] = redirects
        return standing

    def _standing_off(self, pin: Pin, cores: set[str]) -> list[str]:
        """Find the redirected entries of an Off that the swap to the official catalog alone switched.

        Those are the entries of the redirect set that was live whose official-named parts are all official: nothing
        is left to restore. Class 2, under the library lock, in steps as ``_standing_on``.
        """
        standing = []
        for number, core in enumerate(sorted(cores)):
            if number and number % STANDING_STEP == 0 and hasattr(self._lock, "step_aside"):
                self._lock.step_aside()
            try:
                targets = self._spine_parts(core, pin)
                if targets and all(self._untouched_official(path) for path in targets):
                    standing.append(core)
            except (OSError, ValueError, ServiceError):
                continue
        return standing

    def _untouched_official(self, path: Path) -> bool:
        """Tell whether an official-named game file holds official bytes, by its record and date, else its hash."""
        return self._unchanged_official(path) or self._is_official(path)

    def _clear_done(self, pin: Pin, cores: list[str]) -> None:
        """Drop the update markers and write errors of entries a toggle finished, in one save."""

        def change(data: dict) -> bool:
            folder = data["folders"].get(pin.key) or {}
            choices, updated = folder.get("configured") or {}, folder.get("updated") or {}
            changed = False
            for core in cores:
                if updated.pop(core, None) is not None:
                    changed = True
                choice = choices.get(core)
                if choice is not None and choice.pop("error", None) is not None:
                    changed = True
            return changed

        if cores:
            self._update_profiles(change)

    def _check_start(self, pins: list[Pin], intent: bool, expect: bool | None, *, scoped: bool = False) -> None:
        """Check what a toggle, Retry or Apply Mod Configuration checks before it plans.

        With ``intent``, it also checks that the stored intent is still the one the caller saw.
        """
        if scoped:
            if pins[0] not in self.installs():
                raise ServiceError("folder_changed")
        else:
            self._check_pins(pins)
        if intent and profiles_mod.enabled(self.profiles.read()) != expect:
            raise ServiceError("state_changed")
        safety.check_game_not_running()
        self._check_idle(pins)

    def _untested_plan(self, plan: Sequence[tuple[Pin, str]], config: dict | None, drift: bool) -> None:
        """Give the confirmation of a toggle's writes on an untested build (``_check_untested``).

        It covers every planned entry configured with a source other than official, but those an On or its Retry holds
        for a changed rig (``_hold``).
        """
        writes = []
        untested = {key: self.untested_version(pin) for key, pin in {pin.key: pin for pin, _core in plan}.items()}
        for pin, core_name in plan:
            if untested[pin.key] is None:
                continue
            entry = ((((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}).get(
                core_name
            ) or {}
            source = entry.get("source")
            if (
                not isinstance(source, str)
                or not source
                or source == "official"
                or (not drift and self._hold(pin, source))
            ):
                continue
            targets = self._spine_parts(self._core_map(pin).slot(core_name), pin)
            if targets:
                writes.append(
                    (
                        pin,
                        self._untested_entry(pin, core_name, targets, entry.get("alt"), entry.get("resource_type")),
                        targets,
                    )
                )
        self._check_untested(writes)

    @toggle_timing.stage("outcomes")
    def _save_outcomes(self, outcomes: dict[tuple[str, str], str | None]) -> None:
        """Save the unfinished list after the reached entries.

        An entry that holds its intent leaves, another keeps (or gets) the why of what happened. Items of entries the
        job did not reach stay as they are.
        """
        if not outcomes:
            return

        def change(data: dict) -> bool:
            items = {(item["install"], item["core"]): item for item in data["unfinished"]}
            for key, why in outcomes.items():
                if why is None:
                    items.pop(key, None)
                else:
                    items[key] = {"install": key[0], "core": key[1], "why": why}
            data["unfinished"] = list(items.values())
            return True

        self.profiles.update(change)

    def _off_space(self, plan: list[tuple[Pin, str]], found: list[_Entry], config: dict | None) -> int:
        """Give what an Off copies.

        That is the mods it adopts and their official copies (from its pass), and the built copies it keeps (parts not
        in built/ yet and not files of their mod).
        """
        need = 0
        for entry in found:
            if entry.state in ("untracked", "changed"):
                need += sum(part.size for part in entry.parts if part.carried == "unknown")
                need += sum(
                    part.size
                    for part in entry.parts
                    if not part.official and entry.copies.get(str(part.path)) in ("backup", "elsewhere")
                )
        folders = (config or {}).get("folders") or {}
        for pin, core_name in plan:
            configured = ((folders.get(pin.key) or {}).get("configured") or {}).get(core_name)
            source = (configured or {}).get("source")
            if not source:
                continue
            mod_sha1s: set[str] = set()
            if source.startswith("mod:") and source[4:] in self._records:
                with contextlib.suppress(OSError):
                    mod_sha1s = {self._hashes.get(path).sha1 for path in self._paths(self._records[source[4:]])}
            for path in self._spine_parts(core_name, pin):
                record = self._applied.get(str(path.resolve()))
                if not record or record.get("source") != source or record.get("official") is True:
                    continue
                if record.get("sha1") in mod_sha1s or (self.root / "built" / f"{record.get('sha1')}.bundle").exists():
                    continue
                need += record.get("size") or 0
        return need

    def _reach(
        self,
        pin: Pin,
        core: str,
        enabled: bool,
        allowed: bool,
        listed_state: str | None,
        drift: bool,
        log: LogFunc,
        fraction: Callable[[float], None] | None = None,
        *,
        redirect_batch: dict[str, dict] | None = None,
        official_only: bool = False,
    ) -> tuple[dict[str, Any], str | None, dict | None]:
        """Reach one class 2 entry under the library lock, yielding at the caller's next entry boundary.

        Its configuration is read again and its files judged at write strength (a part the library can show unchanged by
        stat carries its record's source; every other part is read in full). Returns (what happened, by result list; the
        entry's why in the unfinished list, None when it leaves; the entry). ``fraction``: its progress line's share
        (``mod_progress.Part``), told the build and the write.
        """
        with toggle_timing.stage("judge"):
            pins = self._view_pins(pin)
            view = self._view(pins, "full", official_only=official_only)
            entry = view.entry(pin, core)
            self._note_judged(view, entry)
        item = entry.item()
        outcome: dict[str, Any] = {}
        if entry.state == "source_missing":
            self._configure(pin, entry.alt, {entry.resource_type: core}, None)
            logger.info("Dropped the configuration of %s: %s is gone from the library", core, entry.configured)
            outcome["dropped"] = item
            view = self._view(pins, "full", official_only=official_only)
            entry = view.entry(pin, core)
        if entry.takeover:
            self._configure(pin, entry.alt, {entry.resource_type: core}, entry.takeover)
            logger.info("Configured %s in %s with %s (taken over)", core, pin.key, entry.takeover)
            view = self._view(pins, "full", official_only=official_only)
            entry = view.entry(pin, core)
        state = entry.state
        if state == "damaged" or (state == "missing" and redirect_batch is None):
            outcome["skipped"] = {**item, "why": state}
            return outcome, None, item
        if enabled is False:
            if state in ("untracked", "changed"):
                try:
                    if allowed is False:
                        self._check_space(sum(part.size for part in entry.parts if part.carried == "unknown"))
                    saved = entry.configured
                    mod_id = self._adopt_one(pin, core)
                    if saved and view.source_exists(saved, pin.key, entry.resource_type):
                        self._configure(pin, entry.alt, {entry.resource_type: core}, saved)
                except ServiceError as error:
                    outcome["left"] = {**item, "code": error.code, "params": error.to_json()["params"]}
                    return outcome, "left", item
                outcome["adopted"] = {**item, "mod": mod_id}
                view = self._view(pins, "full", official_only=official_only)
                entry = view.entry(pin, core)
            if entry.carried == "official":
                return outcome, None, item
            if entry.overwritten:
                outcome["stuck"] = item
                return outcome, "stuck", item
            if entry.configured and entry.carried == entry.configured and not entry.drifted:
                self._make_built(pin, entry)
            try:
                self._switch(
                    pin,
                    core,
                    "official",
                    log,
                    fraction=fraction,
                    **({"files_only": True} if official_only else {}),
                )
            except ServiceError as error:
                if error.code == "official_unavailable":
                    outcome["stuck"] = item
                    return outcome, "stuck", item
                raise
            outcome["done"] = item
            return outcome, None, item
        # mods on: an On, its Retry, or Apply Mod Configuration. An outside change is never written over by an On or its
        # Retry (it leaves the unfinished list: the "Mods Not in the Library" prompt offers it); only Apply Mod
        # Configuration may, for an entry it was given as changed
        if state in ("untracked", "changed") and not (drift and state == "changed" and listed_state == "changed"):
            outcome["skipped"] = {**item, "why": state}
            return outcome, (None if leaves(state, True) else "pending"), item
        if drift and state not in ("reset", "updated", "changed") and redirect_batch is None:
            if state != "ok":
                outcome["skipped"] = {**item, "why": "entry_changed"}
            return outcome, (None if leaves(state, True) else "pending"), item
        target = entry.configured or "official"
        if state == "ok" and (redirect_batch is None or target == "official"):
            self._selection_error(pin, [core], None)
            return outcome, None, item
        if entry.overwritten and not entry.configured:
            outcome["stuck"] = item
            return outcome, "stuck", item
        if not drift and self._hold(pin, target):
            # A game update changed the official rig under the mod: no On or Retry writes it until it is remapped or
            # updated, or the user applies it as it is.
            outcome["held"] = {**item, "mod": target[4:]}
            return outcome, "held", item
        mod_count = len(self._records)
        try:
            self._switch(
                pin,
                core,
                target,
                log,
                fraction=fraction,
                **({"redirect_batch": redirect_batch} if redirect_batch is not None else {}),
                **({"files_only": True} if official_only else {}),
            )
        except ServiceError as error:
            if error.code == "official_unavailable":
                outcome["stuck"] = item
                return outcome, "stuck", item
            raise
        if state == "ok" and entry.carried == target:
            return outcome, None, item
        outcome["done"] = item
        if len(self._records) > mod_count:
            outcome["done"] = {**outcome["done"], "kept_current": True}
        if (
            target.startswith("mod:")
            and target[4:] in self._records
            and self._incomplete(self._records[target[4:]], pin)
        ):
            outcome["done"] = {**outcome["done"], "incomplete": True}
        return outcome, None, item

    def _note_judged(self, view: _View, entry: _Entry) -> None:
        """Keep in the records what a judgement found, so the cheap judgement agrees with it from then on.

        That is the new date of a file whose bytes are the record's (its sha1 at write strength, its last bytes
        cheaply), and, at write strength, a file the cheap rule calls official whose bytes were changed outside the
        library (``outside``, its size and date; dropped by the next write there).
        """
        if self._writes_refused():
            return  # a recovery that has failed refuses changes: a later judgement notes it
        changed = False
        for part in entry.parts:
            record = self._applied.get(str(part.path.resolve())) if part.record is not None else None
            if record is None:
                continue
            if part.restamp is not None:
                if record.get("mtime") != part.restamp or "outside" in record:
                    logger.info("Same bytes, new date: %s", part.path.name)
                    record["mtime"] = part.restamp
                    record.pop("outside", None)
                    changed = True
                continue
            if view.allow != "full" or part.matched or part.carried != "unknown" or part.trailing != 0:
                continue
            expected = view.official_size(part.path.name)
            if expected is not None and expected != part.size:
                continue  # the cheap rule finds it already: another size than the official file
            mark = [part.size, part.mtime]
            if record.get("outside") != mark:
                logger.info("Changed outside the library, though it looks official: %s", part.path.name)
                record["outside"] = mark
                changed = True
        if changed:
            try:
                self._save()
            except (OSError, ServiceError) as error:
                log_failure(logger, logging.WARNING, "Could not save what a judgement found", error)

    def judge_unfinished(self) -> int:
        """Drop finished entries from known installs' unfinished lists using recorded file evidence.

        On the job worker, use stats, headers, and recorded-write tails to apply the
        leaving rule without requiring scan consent. Inspect only this directory's
        known installs, and retain everything if intent changes during judgement.

        Returns:
            Number of dropped items; zero if writes are refused or switch intent changed during judgement.
        """
        with self._lock, _errors("judge_unfinished"):
            if self._writes_refused():
                return 0  # a recovery that has failed refuses changes of the list
            config = self.profiles.read()
            if not config or not config.get("unfinished"):
                return 0
            enabled, toggled = profiles_mod.enabled(config), config.get("toggled")
            pins = self.installs()
            by_key = {pin.key: pin for pin in pins}
            allowed = self._scan_allowed()
            view = self._view(pins, "scan" if allowed else "recorded", config)
            leaving: set[tuple[str, str]] = set()
            for item in config["unfinished"]:
                pin = by_key.get(item["install"])
                if pin is None:
                    continue
                files = view.indexes[pin.key].get(item["core"], [])
                if (
                    not allowed
                    and files
                    and item["core"] not in view.redirects.get(pin.key, {})
                    and not any(str(path.resolve()) in view.applied for path in files)
                ):
                    continue  # judged only by the scan, or when a toggle reaches it
                try:
                    entry = view.entry(pin, item["core"])
                    if item["why"] == "stuck" and item["core"] in view.redirects.get(pin.key, {}):
                        if any(
                            path.is_file()
                            and view.says_nonofficial(path, record, present=True)
                            and (part := view.part(path, pin, entry.resource_type)).matched
                            and not part.official
                            for path, record in view.records_of(pin, item["core"])
                        ):
                            continue
                except (OSError, _Unread):
                    continue
                self._note_judged(view, entry)
                if leaves(entry.state, enabled):
                    leaving.add((item["install"], item["core"]))
            if not leaving:
                return 0
            dropped = []

            def change(data: dict) -> bool:
                if profiles_mod.enabled(data) != enabled or data.get("toggled") != toggled:
                    return False
                kept = [item for item in data["unfinished"] if (item["install"], item["core"]) not in leaving]
                dropped.extend(range(len(data["unfinished"]) - len(kept)))
                data["unfinished"] = kept
                return bool(dropped)

            self.profiles.update(change)
            return len(dropped)

    @_serialized
    def dismiss(self, resource_type: str, entries: list[dict]) -> None:
        """Suppress entry prompts until filenames, sizes, or modification times change.

        Only file stats are read.

        Args:
            resource_type: Prompt group, drift or untracked.
            entries: Install/core descriptors whose current file signatures should be remembered.
                Unknown installs and malformed descriptors are skipped.
        """
        if resource_type not in ("drift", "untracked"):
            raise ServiceError("bad_params", resource_type=resource_type)
        pins = {pin.key: pin for pin in self.installs()}
        found = {}
        for item in entries:
            pin = pins.get(item.get("install")) if isinstance(item, dict) else None
            if pin is None or not isinstance(item.get("core"), str):
                continue
            core = self._core_map(pin).slot(item["core"])
            files = []
            for path in self._spine_parts(core, pin):
                with contextlib.suppress(OSError):
                    stat = path.stat()
                    files.append([path.name, stat.st_size, stat.st_mtime_ns])
            found[(pin.key, core)] = (pin, sorted(files))
        if not found:
            return

        def change(data: dict) -> None:
            for (key, core), (pin, files) in found.items():
                self._folder(data, pin)["dismissed"].setdefault(resource_type, {})[core] = files

        self._update_profiles(change)

    @_serialized
    def forget(self, install: str) -> None:
        """Hide or forget a recorded install absent from this settings directory's selected folders.

        With no other data directory naming it, its record goes (configuration, markers, dismissals, built copies and
        unfinished items); otherwise it is only hidden here. state.json's records and the kept originals stay, and
        no game file is touched.

        Args:
            install: Install key to hide here, or remove if no other data directory names it.

        Raises:
            ServiceError: The install is still named by this directory's settings (bad_params).
        """
        if any(pin.key == install for pin in self._named_pins()):
            raise ServiceError("bad_params", install=install)
        dropped: list[dict] = []
        data_id = self.data_id

        def change(data: dict) -> bool:
            folder = data["folders"].get(install)
            if folder is None:
                return False
            if any(other != data_id for other in folder["named_by"]):
                if data_id not in folder["hidden"]:
                    folder["hidden"].append(data_id)
                return True
            dropped.extend(folder["built"].values())
            del data["folders"][install]
            data["unfinished"] = [item for item in data["unfinished"] if item["install"] != install]
            return True

        self._update_profiles(change, after=lambda data: self._delete_built_files(dropped, data))
