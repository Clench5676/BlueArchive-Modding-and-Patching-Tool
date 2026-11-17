"""Reading the library's mods and their photographs; renaming, favouring and removing mods."""

from __future__ import annotations

import contextlib
import hashlib
import unicodedata
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
from ...core.openfiles import remove_tree
from .. import profiles as profiles_mod
from ..judge import (
    core_of,
    date_of,
    says_official,
)
from ..models import (
    ApplyResult,
    DeleteResult,
    ModEntry,
    Pin,
)
from ..storage import (
    read_tail,
    trailing_bytes,
)
from .base import _Entry, _drop_source, _errors, _game_folder, _mod_writer, _serialized


class ModsMixin:
    """Reading the library's mods and their photographs; renaming, favouring and removing mods."""

    @_serialized
    def assigned_targets(self) -> set[str]:
        """Return explicit ownership targets represented by stored mods, including absent local students."""
        return {record["assignment"] for record in self._records.values() if record.get("assignment")}

    @_serialized
    def variant_metadata(self, key: str) -> Any:
        """Resolve a variant key's catalog or stored ownership metadata under the library lock."""
        return self._variant(key)

    def _variant(self, key: str) -> Any:
        try:
            return self.catalog.variant(key)
        except (KeyError, ServiceError) as error:
            if isinstance(error, ServiceError) and error.code != "catalog_alt_not_found":
                raise
            if callable(getattr(self.catalog, "assignment_variant", None)) and any(
                (record.get("assignment") or record.get("target")) == key for record in self._records.values()
            ):
                try:
                    return self.catalog.assignment_variant(key)
                except (KeyError, ServiceError) as lookup:
                    if isinstance(lookup, ServiceError) and lookup.code != "catalog_alt_not_found":
                        raise
            # Current NPC bundles may have no roster entry. Their stored filename owns the model slot.
            record = next(
                (
                    record
                    for record in self._records.values()
                    if record["kind"] == "model" and record["target"] == key and core_of(record["files"][0]) == key
                ),
                None,
            )
            if record:
                from ...catalog.models import Variant
                from ...catalog._names import LANGUAGES, BASE_SHORT

                return Variant(
                    key,
                    key,
                    key,
                    {lang: {"full": key, "short": BASE_SHORT[lang]} for lang in LANGUAGES},
                    False,
                    0,
                    None,
                    None,
                    None,
                    None,
                    None,
                    model=key,
                )
            if isinstance(error, KeyError):
                raise ServiceError("alt_not_found", alt=key) from error
            raise

    def _record(self, mod_id: str) -> dict[str, Any]:
        try:
            return self._records[mod_id]
        except KeyError as error:
            raise ServiceError("mod_not_found", id=mod_id) from error

    def _paths(self, record: dict[str, Any]) -> list[Path]:
        directory = self.root / "mods" / record["id"]
        paths = [directory / name for name in record["files"]]
        if not directory.resolve().is_relative_to(self.root) or any(
            not path.resolve().is_relative_to(directory.resolve()) for path in paths
        ):
            raise ServiceError("mod_unsupported", file=paths[0])
        return paths

    def _target_of(self, record: dict[str, Any]) -> str:
        """Give the variant a mod belongs to.

        That is the one the current catalog gives its core, else its stored target (fixed when it was added; a later
        catalog may name that core by an alias).
        """
        if record.get("assignment"):
            return record["assignment"]
        if record["kind"] == "other":
            return record["target"]
        variant = self.catalog.variant_for_core(core_of(record["files"][0]))
        return variant.key if variant is not None else record["target"]

    def _repaired_id(self, record: dict[str, Any]) -> str:
        """Give the repaired copy a mod links to, an empty string when the link points at no record.

        An old library that deleted the copy keeps its id, which counts as none. A held record is there (its files are
        missing for now, not gone): its link stays, and the mod stays not repairable while it is held.
        """
        mod_id = record.get("repaired_id")
        if not isinstance(mod_id, str) or not mod_id:
            return ""
        return mod_id if mod_id in self._records or mod_id in self._held_ids() else ""

    @staticmethod
    def _optimized_from(record: dict[str, Any]) -> str:
        """The source an Optimize Physics copy was built from ("alt:<key>"), "" for any other mod."""
        source = record.get("optimized_from")
        return source if isinstance(source, str) and source.startswith("alt:") else ""

    @classmethod
    def _costume_outdated(cls, record: dict[str, Any]) -> bool:
        """Tell whether the start builds an Optimize Physics copy again (``refresh_costumes``).

        It does when the copy was built under an older costume rule than the program's, or under one not recorded (every
        copy made before the rule was recorded), and was not kept as it is under the program's rule (``costume_kept``: a
        rebuild found no physics rule that improves it, or failed).
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        return (
            bool(cls._optimized_from(record))
            and record.get("costume_rule") != COSTUME_RULE
            and record.get("costume_kept") != COSTUME_RULE
        )

    def _entry(self, record: dict[str, Any]) -> ModEntry:
        from ..lobby_camera import meta_of

        paths = self._paths(record)
        parsed = parse_filename(paths[0].name)
        # a memorial lobby camera is one of its variant's lobby choices (lobby_camera)
        camera = meta_of(record)
        # The parts of one mod can carry different dates: the mod is dated by its newest part, not by the first one.
        return ModEntry(
            record["id"],
            paths[0],
            self._label(record),
            camera["alt"] if camera else self._target_of(record),
            "lobby" if camera else record["kind"],
            record["added"],
            not record["seen"],
            sum(p.stat().st_size for p in paths),
            max(date_of(path.name) for path in paths),
            parsed.crc,
            camera["core"] if camera else core_of(paths[0].name),
            repaired_from=record.get("repaired_from", ""),
            repaired_id=self._repaired_id(record),
            user_added=record.get("user_added") is True,
            alias=record.get("alias", ""),
            label_base=record["label"],
            optimized_from=self._optimized_from(record),
            camera={key: camera[key] for key in ("source", "number", "scale", "offset")} if camera else None,
        )

    def _label(self, record: dict[str, Any], seen: frozenset[str] = frozenset()) -> str:
        """Resolve internal aliases and repaired names without touching bundle names or bytes."""
        alias = record.get("alias")
        if isinstance(alias, str) and alias:
            return alias
        if record.get("roster_photography"):
            return t("webui.photo.modName")
        if isinstance(record.get("lobby_camera"), dict):
            return t("webui.lobby.cameraName", n=record["lobby_camera"].get("number", 1))
        if self._optimized_from(record):
            return t("webui.model.optimizedName", name=record["label"])
        source = record.get("repaired_from")
        if isinstance(source, str) and source and source not in seen:
            parent = self._records.get(source)
            name = self._label(parent, seen | {record["id"]}) if parent else record["label"]
            return t("webui.remap.name", name=name)
        return record["label"]

    @_serialized
    def mods(self) -> list[ModEntry]:
        """Return the mod entries in insertion order."""
        return [self._entry(record) for record in self._records.values()]

    @property
    def photographs(self):
        """Return the persistent family-aware photograph store, creating its accessor lazily."""
        from ..photographs import Photographs, student_family

        if not hasattr(self, "_photographs"):
            self._photographs = Photographs(self.root, lambda key, catalog=self.catalog: student_family(catalog, key))
        return self._photographs

    def _migrate_collection_slots(self) -> None:
        """Partition legacy shared Collection slots by batch without changing any game bytes."""
        if not any(
            name.lower().startswith("uis-01_common-14_charactercollect-")
            for record in self._records.values()
            for name in record["files"]
        ):
            return

        def update(data):
            changed = False
            for install, folder in data.get("folders", {}).items():
                configured = folder.get("configured", {})
                for old, entry in list(configured.items()):
                    source = entry.get("source", "")
                    record = self._records.get(source[4:], {}) if source.startswith("mod:") else {}
                    names = [
                        name
                        for name in record.get("files", [])
                        if name.lower().startswith("uis-01_common-14_charactercollect-")
                    ]
                    targets = {
                        core_of(name)
                        for name in names
                        if "other:"
                        + hashlib.sha256(
                            repr((parse_filename(name).prefix, parse_filename(name).res_type)).encode()
                        ).hexdigest()
                        == old
                    }
                    if len(targets) != 1:
                        continue
                    new = targets.pop()
                    if new == old or new in configured:
                        continue
                    configured[new] = configured.pop(old)
                    for field_name in ("built", "updated"):
                        if old in folder.get(field_name, {}):
                            folder[field_name][new] = folder[field_name].pop(old)
                    for marker in folder.get("dismissed", {}).values():
                        if isinstance(marker, dict) and old in marker:
                            marker[new] = marker.pop(old)
                    for item in data.get("unfinished", []):
                        if item.get("install") == install and item.get("core") == old:
                            item["core"] = new
                    changed = True
            return changed

        self.profiles.update(update)

    @_serialized
    def photograph_choice(self, descriptor: dict, pin: Pin | None = None) -> dict:
        """Describe the desired Collection portrait, including a choice deferred while mods are off.

        Args:
            descriptor: Collection target metadata containing the bundle and texture name.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        from .. import portrait_edit

        return portrait_edit.choice(self, descriptor, self._current(pin))

    @_serialized
    def photograph_removal_info(self, variant: str, key: str) -> dict:
        """Describe the skins whose managed recipes use a photograph before deletion.

        Args:
            variant: Variant key authorizing the photo's student family and identifying local use.
            key: Photograph ID, or official for the undeletable game-art choice.

        Returns:
            Official and usage flags plus the sorted target variant keys.
        """
        from ..portrait_edit import usage_targets

        if key == "official":
            return {"official": True, "used": False, "used_here": False, "used_elsewhere": False, "targets": []}
        self.photographs._entry(variant, key)
        uses = usage_targets(self)
        targets = sorted(uses.get(key, set()))
        return {
            "official": False,
            "used": key in uses,
            "used_here": variant in targets,
            "used_elsewhere": any(target != variant for target in targets),
            "targets": targets,
        }

    @_serialized
    @_mod_writer
    def delete_photograph(self, variant: str, key: str, confirm: bool = False, log: LogFunc = no_log) -> dict:
        """Delete a photograph transactionally, resetting its managed texture uses when confirmed.

        Args:
            variant: Variant key authorizing access to the photo's student family.
            key: Saved photograph ID; the official choice cannot be removed.
            confirm: Whether to reset recipes using the photo before deleting it.
            log: Receives user-facing operation messages.

        Returns:
            The remaining family photograph listing.

        Raises:
            ServiceError: The asset is official, confirmation is required, a held reference
                prevents deletion, or a protected write or photo validation fails.
        """
        from .. import portrait_edit

        if key in ("official", None):
            raise ServiceError("asset_official")
        self.photographs._entry(variant, key)
        if key in portrait_edit.in_use(self) and not confirm:
            raise ServiceError("asset_delete_confirm")
        from ..asset_delete import transaction

        with transaction(self) as deletion:
            if key in portrait_edit.in_use(self):
                portrait_edit.remove_photo(self, key, log)
            portrait_edit._prune_composites(self)
            deletion.remember_photo(variant, key)
            result = self.photographs.delete(variant, key, portrait_edit.in_use(self))
        return result

    @_serialized
    @_mod_writer
    def apply_photograph(
        self,
        variant: str,
        key: str | None,
        descriptor: dict,
        pin: Pin | None = None,
        log: LogFunc = no_log,
        progress: ProgressCallback | None = None,
    ) -> ApplyResult:
        """Apply a saved photograph to a variant's shared Collection texture.

        Args:
            variant: Variant key whose catalog portrait must match the descriptor's texture name.
            key: Photograph ID in the same student family, or None to restore official art.
            descriptor: Collection target metadata containing the bundle and exact texture name.
            pin: Game install and server the operation is fixed to; None uses the current install.
            log: Receives user-facing operation messages.
            progress: Receives game-write progress; None disables reporting.

        Returns:
            Applied or deferred result for the shared Collection core.
        """
        from .. import portrait_edit

        target = self._variant(variant)
        if not getattr(target, "portrait", None) or target.portrait != descriptor.get("name"):
            raise ServiceError("photo_target_unavailable")
        png = self.photographs.image(variant, key) if key is not None else None
        return portrait_edit.apply(self, variant, key, png, descriptor, self._current(pin), log, progress)

    def _recognize_installed(self, mod_id: str, identity: frozenset) -> bool:
        """Adopt exact outside content without writing any game files."""
        record = self._records[mod_id]
        matched = False
        for pin in self.installs():
            view = self._view(self._view_pins(pin), "full")
            cores = {view.maps[pin.key].slot(core_of(name)) for name in record["files"]}
            entries = [view.entry(pin, core) for core in cores]
            parts = [part for entry in entries for part in entry.parts]
            if not parts or not self._identity({p.path.name: p.path for p in parts if not p.official}) <= identity:
                continue
            for entry in entries:
                local = frozenset(item for item in identity if item[0] in view.maps[pin.key].slot_cores(entry.core))
                if entry.state not in ("untracked", "changed") or not self._matches_installed(local, entry):
                    continue
                try:
                    self._adopt_one(pin, entry.core, mod_id=mod_id)
                except ServiceError as error:
                    if error.code != "entry_changed":
                        raise
                else:
                    matched = True
        return matched

    def _matches_installed(self, identity: frozenset, entry: _Entry) -> bool:
        files = {part.path.name: part.path for part in entry.parts}
        modified = {part.path.name: part.path for part in entry.parts if not part.official}
        if entry.kind == "other":
            slots = {core_of(name) for name in files}
            identity = frozenset(item for item in identity if item[0] in slots)
        return bool(modified) and self._identity(modified) <= identity <= self._identity(files)

    @_serialized
    def other_mods(self, pin: Pin | None = None) -> list[dict]:
        """List generic sources for Settings with target files and configuration state.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            Generic mod rows excluding photograph recipes and cameras, with selected and active/mixed/missing states.
        """
        pin = pin or self.current_pin()
        view = self._view([pin], "full") if pin else None
        rows = []
        for record in self._records.values():
            if record["kind"] != "other" or record.get("roster_photography") or record.get("lobby_camera"):
                continue
            source = f"mod:{record['id']}"
            try:
                targets = self._game_files(record["target"], "other", pin) if pin else []
            except ServiceError:
                targets = []
            entries = [view.entry(pin, core_of(path.name)) for path in targets]
            selected = sum(entry.configured == source for entry in entries)
            active = sum(entry.carried == source for entry in entries)
            state = (
                "official"
                if all(not entry.configured and entry.carried == "official" for entry in entries)
                else "inactive"
            )
            if not entries:
                state = "missing"
            elif selected == len(entries):
                state = "active" if active == len(entries) and profiles_mod.enabled(self.profiles.read()) else "set"
            elif any(entry.state not in ("ok", "pending") for entry in entries):
                state = "changed"
            elif selected or active:
                state = "mixed"
            rows.append(
                {
                    "id": record["id"],
                    "label": self._label(record),
                    "target": record["target"],
                    "files": [path.name for path in targets] or list(record["files"]),
                    "selected": bool(entries) and selected == len(entries),
                    "state": state,
                    **({"overwritten": True} if any(entry.overwritten for entry in entries) else {}),
                }
            )
        return rows

    @_serialized
    def removal_info(self, mod_id: str) -> dict:
        """Return a stored mod ID's deletion label and whether configuration or game files use it."""
        return self._removal_info(mod_id)

    def _removal_info(self, mod_id: str) -> dict:
        source = f"mod:{mod_id}"
        used = any(
            entry.get("source") == source
            for folder in ((self.profiles.read() or {}).get("folders") or {}).values()
            for entry in folder["configured"].values()
        ) or bool(self._in_use(mod_id))
        return {"name": self._label(self._record(mod_id)), "used": used}

    @_serialized
    @_mod_writer
    def remove(
        self, mod_id: str, confirm: bool = False, log: LogFunc = no_log, reconcile: Callable[[], None] | None = None
    ) -> DeleteResult:
        """Reconcile every use through the protected writer before deleting its managed source.

        Args:
            mod_id: Stored mod ID to remove, including camera choices framed on its art.
            confirm: Whether to remove a source still named by configuration or carried in game files.
            log: Receives user-facing operation messages.
            reconcile: Optional callback inside the deletion transaction after source removal;
                None performs no additional reconciliation. Failure rolls back the transaction.

        Returns:
            Completed and dropped entries, affected cores/kinds, or a transactional failure report.

        Raises:
            ServiceError: The asset is official, its record or paths are invalid, or deletion needs confirmation.
        """
        if mod_id == "official":
            raise ServiceError("asset_official")
        record = self._record(mod_id)
        directory = self.root / "mods" / mod_id
        self._paths(record)  # refuse a stored directory or file that resolves outside the library
        source = f"mod:{mod_id}"
        pins = self._all_pins()
        for name, applied in self._applied.items():
            if applied.get("source") == source and not any(Path(name).parent in pin.dirs for pin in pins):
                folder = _game_folder(Path(name))
                if devmode.allows_read(folder):
                    pin = self._pin(folder, self.settings.server_region() if self.settings else "global")
                    if pin is not None:
                        pins.append(pin)
        by_key = {pin.key: pin for pin in pins}
        affected = set()
        for name, applied in self._applied.items():
            if applied.get("source") == source:
                path = Path(name)
                affected.update(
                    (pin.key, self._core_map(pin).slot(core_of(path.name))) for pin in pins if path.parent in pin.dirs
                )
        keys: list[dict] = []

        def change(data: dict) -> None:
            affected.update(_drop_source(data, source, keys))
            for folder in data["folders"].values():
                for entries in folder["dismissed"].values():
                    if isinstance(entries, dict):
                        entries.pop(core_of(record["files"][0]), None)
            for key, core in affected:
                folder = data["folders"].get(key)
                if folder:
                    folder["updated"].pop(core, None)
                    for entries in folder["dismissed"].values():
                        if isinstance(entries, dict):
                            entries.pop(core, None)
            data["unfinished"] = [
                item for item in data["unfinished"] if (item["install"], item["core"]) not in affected
            ]

        if self._removal_info(mod_id)["used"] and not confirm:
            raise ServiceError("asset_delete_confirm")
        # the lobby cameras framed on this mod's art go with it: each puts its prefab back first
        from ..lobby_camera import cameras, meta_of

        for camera in [r["id"] for r in cameras(self) if meta_of(r).get("source") == source]:
            gone = self.remove(camera, confirm=True, log=log)
            if gone.failed:
                return gone
        from ..asset_delete import transaction

        done = []
        try:
            with transaction(self) as deletion:
                deletion.remember_mods([mod_id])
                self._update_profiles(change)
                if profiles_mod.enabled(self.profiles.read()):
                    for key, core in sorted(affected):
                        pin = by_key.get(key)
                        if pin is None:
                            raise ServiceError("game_not_found")
                        config = (self.profiles.read() or {}).get("folders", {}).get(key, {})
                        target = config.get("configured", {}).get(core, {}).get("source", "official")
                        with _errors("restore"):
                            self._switch(pin, core, target, log)
                        done.append({"install": key, "core": core})
                for applied in self._applied.values():
                    if applied.get("source") == source:
                        applied.update(source="removed", removed=True)
                        applied.pop("build", None)
                del self._records[mod_id]
                for other in self._records.values():
                    if other.get("repaired_id") == mod_id:
                        del other["repaired_id"]  # the original is repairable again: one write with the copy's removal
                self._save()
                remove_tree(directory)
                self._delete_built_files(keys, self.profiles.read())
                if reconcile:
                    reconcile()
        except ServiceError as error:
            return DeleteResult([], [{"entry": {"id": mod_id}, **error.to_json()}], [], [], [])
        self._prune_quietly()
        return DeleteResult(
            done,
            [],
            [{"install": key, "core": core} for key, core in sorted(affected)],
            sorted({core for _, core in affected}),
            [record["kind"]] if affected else [],
        )

    def _in_use(self, mod_id: str) -> list[Path]:
        """Find the game files still carrying the mod: stats and tails first, then SHA-1 when their stamp changed.

        A part the mod left official carries nothing of it.
        """
        paths = []
        for name, record in self._applied.items():
            if record.get("source") != f"mod:{mod_id}" or record.get("removed"):
                continue
            path = Path(name)
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                matched = record.get("size") == stat.st_size and (
                    record.get("mtime") == stat.st_mtime_ns
                    or (bool(record.get("tail")) and read_tail(path) == record["tail"])
                )
                if not matched and self._hashes.get(path).sha1 != record.get("sha1"):
                    continue
                flag = record.get("official")
                if not isinstance(flag, bool):
                    expected = self._official_size(path.name)
                    flag = trailing_bytes(path) == 0 and (expected is None or expected == stat.st_size)
                if not flag:
                    paths.append(path)
            except OSError:
                continue
        return paths

    def _official_size(self, name: str) -> int | None:
        steam = self._steam_official(name)
        if steam is not None:
            return steam.size
        original = self._original_path(Path(name))
        with contextlib.suppress(OSError):
            return original.stat().st_size
        return next(
            (
                record.get("size")
                for path, record in self._applied.items()
                if Path(path).name == name and says_official(record) and isinstance(record.get("size"), int)
            ),
            None,
        )

    @_serialized
    def rename(self, mod_id: str, label: str) -> ModEntry:
        """Set or clear a metadata-only alias, retaining the default name.

        Args:
            mod_id: Stored mod ID whose displayed label changes.
            label: Alias normalized to NFC with control characters removed and whitespace collapsed;
                empty clears it, and the normalized alias must not exceed 120 characters.

        Returns:
            Updated mod entry with the resolved display label.

        Raises:
            ServiceError: The label is invalid, the mod is missing, or the state cannot be written.
        """
        if not isinstance(label, str):
            raise ServiceError("bad_params", key="label")
        alias = " ".join(
            "".join(
                ch
                for ch in unicodedata.normalize("NFC", label)
                if ch.isspace() or not unicodedata.category(ch).startswith("C")
            ).split()
        )
        if len(alias) > 120:
            raise ServiceError("bad_params", key="label")
        record = self._record(mod_id)
        old = record.get("alias")
        if alias:
            record["alias"] = alias
        else:
            record.pop("alias", None)
        try:
            self._save()
        except (OSError, ServiceError):
            if old is None:
                record.pop("alias", None)
            else:
                record["alias"] = old
            raise
        return self._entry(self._record(mod_id))

    @_serialized
    def mark_seen(self, mod_id: str) -> None:
        """Clear a stored mod ID's fresh flag and persist the change."""
        record = self._record(mod_id)
        old = record["seen"]
        record["seen"] = True
        try:
            self._save()
        except (OSError, ServiceError):
            record["seen"] = old
            raise

    @_serialized
    def favorites(self) -> set[str]:
        """Return a snapshot of the favorite variant keys."""
        return set(self._favorites)

    @_serialized
    def set_favorite(self, variant_key: str, on: bool) -> None:
        """Set a variant's favorite flag.

        Args:
            variant_key: Variant key validated against catalog or stored ownership metadata.
            on: Whether the variant belongs in the saved favorites set.
        """
        self._variant(variant_key)
        old = set(self._favorites)
        if on:
            self._favorites.add(variant_key)
        else:
            self._favorites.discard(variant_key)
        try:
            self._save()
        except (OSError, ServiceError):
            self._favorites = old
            raise
