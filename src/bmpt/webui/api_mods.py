"""The RPC methods of mods: batch import, the library, the Enable Mods switch, Troubleshoot Mods, the jobs."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from ..host import updater
from ..core import devmode
from ..core.errors import ServiceError
from bmpt.engine import get_version_info
from .rpc import RpcRegistry
from .services import Services


# the jobs a page may cancel when it drops what they check (``jobs.cancel``): read-only checks of what it shows
CANCELLABLE = ("costume-check",)


def register(rpc: RpcRegistry, s: Services) -> None:
    """Register the mods' RPC methods (see the RPC table in docs/webui/api.md).

    Args:
        rpc: The registry to add the methods to.
        s: The Services every method works on.
    """
    # --- batch import ---

    @rpc.method("import.pick")
    def import_pick(folder: bool = False, recursive: bool = False, directory: str | None = None) -> dict[str, Any]:
        return s.batch_import.pick(folder, recursive, directory)

    @rpc.method("import.analyze")
    def import_analyze(paths: list[str], recursive: bool = False) -> str:
        return s.batch_import.analyze(paths, recursive)

    @rpc.method("import.setSource")
    def import_set_source(id: str, server: str, platform: str) -> str:
        return s.batch_import.set_source(id, server, platform)

    @rpc.method("import.catalog")
    def import_catalog() -> dict[str, Any]:
        return s.batch_import.assignment_catalog()

    @rpc.method("import.assignCharacter")
    def import_assign_character(id: str, variant: str) -> str:
        return s.batch_import.assign_character(id, variant)

    @rpc.method("import.run")
    def import_run(ids: list[str], options: dict[str, Any], revision: int) -> str:
        return s.batch_import.run(ids, options, revision)

    @rpc.method("import.options")
    def import_options() -> dict[str, Any]:
        return s.batch_import.options()

    @rpc.method("import.setOptions")
    def import_set_options(
        strategy: str,
        types: list[str],
        skipUnchanged: bool,
        crc: str,
        compression: str,
        extraBytes: str,
        backup: bool,
        workers: int,
        output: str | None,
        enableAfterImport: bool,
        previewAfterImport: bool = False,
    ) -> dict[str, Any]:
        return s.batch_import.set_options(
            {
                "strategy": strategy,
                "types": types,
                "skipUnchanged": skipUnchanged,
                "crc": crc,
                "compression": compression,
                "extraBytes": extraBytes,
                "backup": backup,
                "workers": workers,
                "output": output,
                "enableAfterImport": enableAfterImport,
                "previewAfterImport": previewAfterImport,
            }
        )

    # --- mods ---

    @rpc.method("mods.add")
    def mods_add(paths: list[str], background: bool = False) -> Any:
        # dev mode: refused before the library is built (ModLibrary.add checks again)
        for p in paths:
            devmode.check_read(Path(p))
        if background:
            library = s.library
            pin = library.current_pin()

            def add(log: Any, progress: Any) -> Any:
                if pin:
                    s._check_pin(library, pin)
                return library.add_and_apply([Path(p) for p in paths], progress=progress, pin=pin, log=log)

            return s.submit("mods.add", add, writes="game", library=library)
        library = s.library
        added = library.add_and_apply([Path(p) for p in paths])
        changed = [*added.applied, *added.configured, *added.installed]
        s.events.publish(
            "library.changed",
            {"cores": sorted({m.core for m in changed}), "kinds": sorted({m.kind for m in changed}), "mods": True},
        )
        if added or added.installed:
            s.refresh_status(library)
            s.publish_state()
            s.request_scan()
        return added

    @rpc.method("mods.pick")
    def mods_pick(background: bool = False) -> Any:
        if s.window is None:
            raise ServiceError("native_unavailable")
        from .native import pick_files

        paths = pick_files(s.window, ("Bundle (*.bundle;*.backup)",))
        return mods_add([str(p) for p in paths], background) if paths else []

    @rpc.method("mods.removalInfo")
    def mods_removal_info(id: str) -> dict[str, Any]:
        return s.library.removal_info(id)

    @rpc.method("mods.other")
    def mods_other() -> list[dict[str, Any]]:
        return s.library.other_mods()

    @rpc.method("mods.remove")
    def mods_remove(id: str, confirm: bool = False) -> str:
        """Delete a library source as one writer job; game write refusals remain in its result and drift."""
        library = s.library
        if id == "official":
            raise ServiceError("asset_official")

        def fallback(variant, kind):
            try:
                value = variants[variant]
                if getattr(value, kind, None):
                    return "official"
            except (KeyError, ServiceError):
                pass
            return next(
                ("mod:" + mod.id for mod in remaining if mod.id != id and mod.target == variant and mod.kind == kind),
                None,
            )

        remaining = []
        variants = {}

        def remove(log, progress):
            remaining[:] = library.mods()
            variants.update((v.key, v) for family in s.catalog.families() for v in family.variants)
            result = library.remove(
                id,
                confirm=bool(confirm),
                log=log,
                reconcile=lambda: s.state.reconcile_deleted_source("mod:" + id, fallback),
            )
            if result.failed:
                error = result.failed[0]
                raise ServiceError(error["code"], **error.get("params", {}))
            return result

        return s.submit("mods.remove", remove, writes="library", library=library)

    @rpc.method("mods.rename")
    def mods_rename(id: str, label: str) -> dict[str, Any]:
        entry = s.library.rename(id, label)
        s.events.publish("library.changed", {"cores": [], "kinds": [], "mods": True})
        return entry.to_json()

    @rpc.method("mods.seen")
    def mods_seen(id: str | None = None, ids: list[str] | None = None) -> None:
        if (id is None) == (ids is None):
            raise ServiceError("bad_params", key="ids")
        chosen = [id] if id is not None else ids
        if not isinstance(chosen, list) or not all(isinstance(v, str) and v for v in chosen):
            raise ServiceError("bad_params", key="ids")
        if chosen:
            s.state.mark_mods_seen(chosen)
            s.events.publish("library.changed", {"cores": [], "kinds": [], "mods": True})

    # --- the Enable Mods switch, mods already in use, drift ---

    @rpc.method("mods.state")
    def mods_state() -> dict[str, Any]:
        """The switch's state for every place that shows it, from the snapshot (never through the library lock)."""
        return s.mods_state()

    @rpc.method("mods.toggle")
    def mods_toggle(enabled: bool, expect: bool) -> str:
        """Turn mods off or on in every known install (also Retry: ``expect`` equal to the current intent)."""
        return s.submit_toggle(enabled, expect)

    @rpc.method("mods.consent")
    def mods_consent(granted: bool) -> dict[str, Any]:
        if not isinstance(granted, bool):
            raise ServiceError("bad_params", key="granted")
        s.set_scan_consent(granted)
        return {}

    @rpc.method("mods.explained")
    def mods_explained() -> dict[str, Any]:
        s.state.set("mods_explained", True)
        s.publish_state()
        return {}

    @rpc.method("mods.scan")
    def mods_scan(dismissed: bool = False) -> dict[str, bool]:
        """Request a scan; the result comes as ``library.scanned``.

        ``dismissed`` is Settings' Check, which lists dismissed entries again. ``scan_off`` without the scan consent.
        """
        if not s.scan_allowed():
            raise ServiceError("scan_off")
        return {"requested": s.request_scan(bool(dismissed)) is not None}

    @rpc.method("mods.entries")
    def mods_entries() -> list[Any]:
        """The last scan's entries, as every writer job's result updated them; none without the scan consent."""
        return s.snapshot.entries() if s.scan_allowed() else []

    def entry_list(entries: Any) -> list[dict[str, Any]]:
        if not isinstance(entries, list) or not all(
            isinstance(item, dict) and isinstance(item.get("install"), str) and isinstance(item.get("core"), str)
            for item in entries
        ):
            raise ServiceError("bad_params", key="entries")
        return [
            {
                "install": item["install"],
                "core": item["core"],
                **({"state": item["state"]} if isinstance(item.get("state"), str) else {}),
            }
            for item in entries
        ]

    @rpc.method("mods.adopt")
    def mods_adopt(entries: list[dict[str, Any]]) -> str:
        """Add to Library: a library writer job, allowed while the game runs and while a classic window is open."""
        listed = entry_list(entries)
        library = s.library
        pins = library.installs()
        return s.submit(
            "library.adopt",
            lambda log, progress: library.adopt(listed, pins, log=log, progress=progress),
            writes="library",
            library=library,
        )

    @rpc.method("mods.restoreDrift")
    def mods_restore_drift(entries: list[dict[str, Any]]) -> str:
        """The drift prompt's Apply Mod Configuration."""
        return s.submit_restore_drift(entry_list(entries))

    @rpc.method("mods.dismiss")
    def mods_dismiss(kind: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """The drift prompt's Keep as Is, the untracked prompt's Don't Ask Again."""
        listed = entry_list(entries)
        s.library.dismiss(kind, listed)
        s.snapshot.mark_dismissed({(item["install"], item["core"].lower()) for item in listed})
        s.publish_state()
        return {}

    @rpc.method("mods.impact")
    def mods_impact(ids: list[str] | None = None, force: bool = False) -> str:
        """Start a reader job on what a game update did under the library's mods.

        Without ``ids``: the impact summary of the current install, classified only when its resource version
        differs from the last one seen, or always with ``force`` (Settings' View) (``update_impact``). With ``ids``:
        those mods' impacts now, ``{mods: [{id, impact}]}``.
        """
        if ids is not None and (
            not isinstance(ids, list) or len(ids) > 4096 or not all(isinstance(v, str) and v for v in ids)
        ):
            raise ServiceError("bad_params", key="ids")
        if not isinstance(force, bool):
            raise ServiceError("bad_params", key="force")
        library = s.library
        pin = s._current_pin(library)
        if ids is None:
            return s.submit(
                "mods.impact", lambda log, progress: library.update_impact(pin, force=force, progress=progress)
            )
        return s.submit("mods.impact", lambda log, progress: {"mods": library.impacts(ids, pin)})

    @rpc.method("mods.impactLast")
    def mods_impact_last() -> dict[str, Any]:
        """Return the outcome of the last impact check of the current install, without checking.

        See ``last_impact``: ``{install, version, checked, update, mods}``; Settings' View Mods to Update.
        """
        library = s.library
        return library.last_impact(library.current_pin())

    @rpc.method("mods.updateDue")
    def mods_update_due() -> dict[str, Any]:
        """Tell whether the start asks to check the mods against a game update (``update_due``).

        The answer is ``{due, outdated, retry, install, version, previous}``. ``outdated`` lists costume builds of an
        older costume rule to build again first (``mods.refreshCostumes``); ``retry`` lists entries whose last write
        failed, to write again (``mods.retryFailed``). Reads the version file, the configuration and cached
        fingerprints only.
        """
        library = s.library
        return library.update_due(library.current_pin())  # no game folder: nothing is asked

    @rpc.method("mods.refreshCostumes")
    def mods_refresh_costumes() -> str:
        """Build again, at the start and without asking, what the App built from a costume under an older rule.

        See ``refresh_costumes``; the result is ``{done, kept, left}``.
        """
        return s.submit_refresh_costumes()

    @rpc.method("mods.retryFailed")
    def mods_retry_failed() -> str:
        """Write again, at the start and without asking, the entries whose last write failed, once under this build.

        See ``retry_failed``; the result is a toggle result, or None with nothing to try.
        """
        return s.submit_retry_failed()

    @rpc.method("mods.impactSeen")
    def mods_impact_seen(install: str, version: str) -> dict[str, Any]:
        """Record that the impact summary of a game update was shown, so its resource version is not told again."""
        if not isinstance(install, str) or not isinstance(version, str) or not version:
            raise ServiceError("bad_params", key="version")
        s.library.impact_seen(install, version)
        return {}

    @rpc.method("mods.confirmUntested")
    def mods_confirm_untested(confirm: dict[str, Any]) -> dict[str, Any]:
        """Take the user's confirmation of the writes an ``untested_build`` refusal named.

        The refusal's ``confirm`` is given back, ``{install: {version, cores}}``; the refused call is then made
        again.
        """
        s.library.confirm_untested(confirm)
        return {}

    @rpc.method("mods.sizes")
    def mods_sizes(kind: str) -> dict[str, int]:
        return s.library.sizes(kind)

    @rpc.method("mods.clearBuilt")
    def mods_clear_built() -> str:
        library = s.library
        return s.submit(
            "library.clearBuilt",
            lambda log, progress: {"freed": library.clear_built()},
            writes="library",
            library=library,
        )

    @rpc.method("mods.forget")
    def mods_forget(install: str) -> str:
        """Remove an install from the list: the library records it, but this data directory's settings do not name it.

        ``bad_params`` for a named one.
        """
        library = s.library
        named = {item["key"] for item in library.status().get("installs", []) if item.get("named")}
        if not isinstance(install, str) or install in named:
            raise ServiceError("bad_params", install=install)
        return s.submit(
            "library.forget", lambda log, progress: library.forget(install), writes="library", library=library
        )

    # --- Troubleshoot Mods (pre-release builds only): every choice to official, then back ---

    def troubleshoot_gate() -> None:
        if not updater.prerelease_previews(get_version_info().get("version") or ""):
            raise ServiceError("unknown_method", method="mods.troubleshoot")

    @rpc.method("mods.troubleshootState")
    def mods_troubleshoot_state() -> dict[str, Any]:
        """{active, count, since}: whether a configuration is saved to restore."""
        troubleshoot_gate()
        from ..library import troubleshoot

        return troubleshoot.status(s.library)

    @rpc.method("mods.applyOfficial")
    def mods_apply_official() -> str:
        """Apply Official to All: a game writer job, {toggle, check}."""
        troubleshoot_gate()
        return s.submit_troubleshoot("apply")

    @rpc.method("mods.restoreConfig")
    def mods_restore_config() -> str:
        """Restore Configuration: a game writer job, {toggle, check}."""
        troubleshoot_gate()
        return s.submit_troubleshoot("restore")

    # --- actions (jobs) ---

    @rpc.method("actions.apply")
    def actions_apply(variant: str, source: str, kind: str = "spr") -> str:
        """Switch what the game shows: write a source into the variant's game files, and configure it.

        The files are the portrait ("spr") or memorial lobby ("lobby") ones of the current install, pinned at
        submit. The library keeps what it replaces (``ModLibrary._commit``). While mods are off it only configures
        the source (``deferred``).
        """
        return s.submit_apply(variant, source, kind)

    @rpc.method("actions.restore")
    def actions_restore(variant: str, kind: str = "spr") -> str:
        return s.submit_restore(variant, kind)

    @rpc.method("actions.update")
    def actions_update(mod: str) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit(
            "update",
            lambda log, progress: library.update(mod, log=log, progress=progress, pin=pin),
            writes="library",
            library=library,
        )

    @rpc.method("actions.extract")
    def actions_extract(target: str) -> str:
        out_dir = s.settings.output_subdir("extract")
        library = s.library
        pin = library.current_pin()
        return s.submit(
            "extract",
            lambda log, progress: {
                "folder": str(library.extract(target, out_dir, log=log, pin=pin, progress=progress))
            },
        )

    @rpc.method("actions.compare")
    def actions_compare(mod: str) -> Any:
        return s.library.compare(mod)

    @rpc.method("actions.remap")
    def actions_remap(mod: str, geometry: Any = None) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit(
            "remap", lambda log, progress: library.remap(mod, log=log, progress=progress, pin=pin, geometry=geometry)
        )

    @rpc.method("actions.oldRig")
    def actions_old_rig(mod: str) -> str:
        """Start the repair review of a texture-only portrait with the rig it was drawn for.

        See ``ModLibrary.old_rig``; it is accepted or discarded as a remap's.
        """
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.old_rig(mod, log=log, progress=progress, pin=pin))

    def spine_licensed() -> None:
        # the graft writes skeletons with code derived from the Spine runtime: only after the user's licence answer
        if s.state.spine_consent is not True:
            raise ServiceError("graft_spine_licence")

    @rpc.method("actions.graft")
    def actions_graft(mod: str) -> str:
        """Start the first step of a graft of the official rig's new animations into a mod's own rig.

        See ``ModLibrary.graft``; the result is the rigs for the page to pose.
        """
        spine_licensed()
        library = s.library
        pin = s._current_pin(library)
        return s.submit("remap", lambda log, progress: library.graft(mod, log=log, progress=progress, pin=pin))

    @rpc.method("actions.graftBuild")
    def actions_graft_build(token: str, geometry: Any = None) -> str:
        spine_licensed()
        library = s.library

        def build(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.extract"})
            return library.graft_build(token, geometry)

        return s.submit("remap", build)

    @rpc.method("actions.graftCheck")
    def actions_graft_check(token: str, geometry: Any = None) -> str:
        """The last step: the review of the grafted copy, accepted or discarded as a remap's."""
        spine_licensed()
        library = s.library
        pin = s._current_pin(library)
        return s.submit(
            "remap", lambda log, progress: library.graft_check(token, geometry, log=log, progress=progress, pin=pin)
        )

    @rpc.method("actions.discardGraft")
    def actions_discard_graft(token: str) -> None:
        s.library.discard_graft(token)

    # the converter route (bmpt.spine.converter): the optional helper for what the graft refuses
    @rpc.method("actions.converterStatus")
    def actions_converter_status() -> dict:
        """Whether the converter is in place, whether the App can fetch it here, and what its consent names."""
        return s.library.converter_status()

    @rpc.method("actions.converterDownload")
    def actions_converter_download(consent: bool = False) -> str:
        """Fetch the pinned converter release (Windows), only with the user's ``consent`` to its licence."""
        if consent is not True:
            raise ServiceError("converter_consent")
        library = s.library
        return s.submit("remap", lambda log, progress: library.converter_download(True, progress))

    @rpc.method("actions.graftConvert")
    def actions_graft_convert(token: str, geometry: Any = None) -> str:
        """Start the converter route after ``actions.graft``: the rigs rewritten through the converter.

        The result is the rigs before and after, to pose.
        """
        spine_licensed()
        library = s.library

        def convert(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.extract"})
            return library.graft_convert(token, geometry)

        return s.submit("remap", convert)

    @rpc.method("actions.graftConvertCheck")
    def actions_graft_convert_check(token: str, geometry: Any = None) -> str:
        """The converter route's last step: the pose check and the review, accepted or discarded as a remap's."""
        spine_licensed()
        library = s.library
        pin = s._current_pin(library)
        return s.submit(
            "remap",
            lambda log, progress: library.graft_convert_check(token, geometry, log=log, progress=progress, pin=pin),
        )

    @rpc.method("app.openConverterPage")
    def app_open_converter_page() -> None:
        """The converter's own page (its licence and source), in the browser."""
        from bmpt.spine import converter as spine_converter

        webbrowser.open(spine_converter.HOME)

    @rpc.method("actions.redrawn")
    def actions_redrawn(mod: str, geometry: Any) -> str:
        library = s.library
        pin = s._current_pin(library)

        def check(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
            return library.redrawn(mod, geometry, pin=pin)

        return s.submit("compatibility", check)

    @rpc.method("actions.acceptRemap")
    def actions_accept_remap(preview: str) -> str:
        library = s.library

        def accept(log: Any, progress: Any) -> Any:
            progress({"current": 1, "total": 1, "step": "import.step.add"})
            return library.accept_remap(preview)

        return s.submit("remap.accept", accept, writes="library", library=library)

    @rpc.method("actions.discardRemap")
    def actions_discard_remap(preview: str) -> None:
        s.library.discard_remap(preview)

    @rpc.method("actions.compatibilityStamp")
    def actions_compatibility_stamp(mod: str) -> str:
        library = s.library
        return library.compatibility_stamp(mod, pin=s._current_pin(library))

    @rpc.method("actions.compatibility")
    def actions_compatibility(mod: str) -> str:
        library = s.library
        pin = s._current_pin(library)
        return s.submit("compatibility", lambda log, progress: library.compatibility(mod, pin=pin, progress=progress))

    @rpc.method("jobs.get")
    def jobs_get(id: str) -> dict[str, Any]:
        job = s.jobs.get(id)
        return {**job.to_json(), "log": job.log[-500:]}

    @rpc.method("jobs.cancel")
    def jobs_cancel(id: str) -> str:
        """Stop a job the page has given up on, of the kinds that allow it (``CANCELLABLE``).

        The result is its state afterwards ("cancelled" at once for a queued one; a running one stops at its next
        step). ``bad_params`` for any other job.
        """
        if not isinstance(id, str) or not id or len(id) > 64:
            raise ServiceError("bad_params", key="id")
        return s.jobs.cancel(id, CANCELLABLE)
