"""The game folders: pins, groups packed one asset per bundle, the bundle index and the core map."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import logging
import os
from dataclasses import replace
from operator import attrgetter, methodcaller
from pathlib import Path
from typing import Any, Iterable

from bmpt.core import devmode
from bmpt.engine import (
    Bundle,
    get_search_dirs,
    parse_filename,
)
from ...catalog.models import CoreMap
from ...core.errors import ServiceError
from ...gameassets.discovery import cab_names
from ...core.logging_utils import log_failure
from .. import safety, toggle_timing
from .. import targets as resolver
from ..judge import (
    KINDS,
    core_of,
    install_key,
    install_root,
    kind_of,
    kind_of_file,
    learn_packed,
    other_target,
    packed_cab,
    part_of,
)
from ..models import (
    Kind,
    Pin,
)
from .base import _View, _pins_signature, _record_maps, _serialized


logger = logging.getLogger(__package__)


def _directory_revision(directory: Path) -> tuple[int, int, int, int]:
    info = directory.stat()
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


_REGULAR_FILE = methodcaller("is_file", follow_symlinks=False)
_ENTRY_NAME = attrgetter("name")
_BUNDLE_NAME = methodcaller("endswith", ".bundle")


class FoldersMixin:
    """The game folders: pins, groups packed one asset per bundle, the bundle index and the core map."""

    def _scan_allowed(self) -> bool:
        """Whether the scan consent is given, asked at each use (it can change while the library lives)."""
        try:
            return bool(self._scan_allowed_input())
        except Exception as error:
            log_failure(logger, logging.WARNING, "Cannot read the scan consent", error)
            return False

    def _pin(self, folder: Path, server: str) -> Pin | None:
        if not folder.is_dir():
            return None
        root = install_root(folder)
        dirs = tuple(directory.resolve() for directory in get_search_dirs(folder))
        if not dirs:
            return None
        return Pin(install_key(root), root, dirs, server)

    def _named_pins(self) -> list[Pin]:
        """List the Global and Japan folders the settings name, whatever the file source.

        Each is listed when it is set and exists. In dev mode (devmode) a folder outside the dev roots is not used, as
        the settings' own game folder.
        """
        out: list[Pin] = []
        if self.settings is None:
            return out
        for setting, server in (("game_resource_dir", "global"), ("game_resource_dir_japan", "japan")):
            try:
                value = self.settings.get(setting)
            except KeyError:
                continue
            if not value or not devmode.allows_read(value):
                continue
            pin = self._pin(Path(value), server)
            if pin is not None and all(other.key != pin.key for other in out):
                out.append(pin)
        return out

    def _recorded_pin(self, key: str, folder: dict) -> Pin | None:
        root = Path(key)
        if not devmode.allows_read(root) or not root.is_dir():
            return None
        dirs = tuple(directory.resolve() for directory in get_search_dirs(root))
        return Pin(key, root, dirs, folder.get("server") or "global") if dirs else None

    @_serialized
    def variant_cores(self, variant_key: str) -> set[str]:
        """Collect lower-case asset cores for scanning a variant across known installs.

        Args:
            variant_key: Variant key, or a generic other:/other-set: target whose stored file slots are scanned.
        """
        if variant_key.startswith(("other:", "other-set:")):
            return {
                core_of(name)
                for record in self._records.values()
                if record["kind"] == "other" and record["target"] == variant_key
                for name in record["files"]
            }
        cores = set()
        for pin in self.installs():
            for kind in KINDS:
                core = self._core_map(pin).core(variant_key, kind)
                if core:
                    cores.add(core.lower())
        return cores

    def installs(self) -> list[Pin]:
        """List switch-covered installs: settings folders, then existing recorded installs not hidden here."""
        pins = self._named_pins()
        try:
            config = self.profiles.read()
        except ServiceError:
            return pins
        for key, folder in ((config or {}).get("folders") or {}).items():
            if any(pin.key == key for pin in pins) or self.data_id in (folder.get("hidden") or []):
                continue
            pin = self._recorded_pin(key, folder)
            if pin is not None:
                pins.append(pin)
        return pins

    def _all_pins(self) -> list[Pin]:
        """List the known installs and the recorded ones this data directory removed from its list.

        The kept originals of the removed ones must stay just the same.
        """
        pins = self.installs()
        with contextlib.suppress(ServiceError):
            for key, folder in ((self.profiles.read() or {}).get("folders") or {}).items():
                if all(pin.key != key for pin in pins):
                    pin = self._recorded_pin(key, folder)
                    if pin is not None:
                        pins.append(pin)
        return pins

    def current_pin(self) -> Pin | None:
        """Return the selected file source's install, or None in ADB mode or without an available folder."""
        if self.settings is None or self.settings.is_adb_mode():
            return None
        folder = self.settings.game_dir()
        if folder is None:
            return None
        return self._pin(folder, self.settings.server_region())

    def _current(self, pin: Pin | None = None) -> Pin:
        pin = pin or self.current_pin()
        if pin is None:
            raise ServiceError("game_not_found")
        return pin

    def _check_pins(self, pins: list[Pin]) -> None:
        """folder_changed when the known installs now differ from the ones a job was submitted with."""
        if _pins_signature(pins) != _pins_signature(self.installs()):
            raise ServiceError("folder_changed")

    def _check_idle(self, pins: list[Pin]) -> None:
        idle = getattr(safety, "check_steam_idle", None)
        if idle is not None:
            for pin in pins:
                idle(pin.root)

    @staticmethod
    def _packed_groups(listing: Iterable[tuple[str, str]]) -> dict[tuple, list[Path]]:
        """Group the generic bundles whose part several files of one folder share, by part.

        A part is ``part_of``: prefix and part number, no date or CRC. Such bundles are groups packed one asset per
        bundle, and a part kept under several dates. Their names cannot tell them apart over game updates, so each gets
        a slot keyed by its CAB.
        """
        groups: dict[tuple, list[Path]] = {}
        for name, path in listing:
            if kind_of_file(name) == "other":
                groups.setdefault((str(Path(path).parent), part_of(name)), []).append(Path(path))
        packed: dict[tuple, list[Path]] = {}
        for (_folder, part), paths in groups.items():
            if len(paths) > 1:
                packed.setdefault(part, []).extend(paths)
        return packed

    def _wanted_packed(self) -> set[tuple]:
        """List the parts of the generic mods' files and of files being imported.

        Only bundles of those parts need their CAB learned.
        """
        return {
            part_of(name)
            for record in self._records.values()
            if record.get("kind") == "other"
            for name in record["files"]
        } | self._packed_wanted

    def _learn_packed(self, paths: Iterable[Path]) -> bool:
        """Learn each bundle's CAB from its header for ``judge.core_of``; True when a slot key changed."""
        changed = False
        for path in paths:
            if packed_cab(path.name) is not None:
                continue
            cab = self._first_cab(path)
            if cab and learn_packed(path.name, cab):
                self._cores.pop(path.name, None)
                changed = True
        return changed

    @staticmethod
    def _first_cab(path: Path) -> str | None:
        try:
            cabs = [name for name in cab_names(path) if not name.endswith((".ress", ".resource"))]
        except (OSError, ValueError):
            return None
        return min(cabs) if cabs else None

    def _learn_packed_sources(self, files: dict[str, Path], packed: dict[tuple, list[Path]], pin: Pin) -> bool:
        """Learn the slot of each mod file of a packed part.

        The slot is the CAB it shares with a bundle of that part in the install, else (an older release) the CAB of the
        one install file it updates, found by its assets.
        """
        changed = False
        for name, path in files.items():
            name = name.removesuffix(".backup")
            members = packed.get(part_of(name))
            if not members or packed_cab(name) is not None:
                continue
            cab = self._first_cab(path)
            held = {packed_cab(member.name) for member in members}
            if cab is None or cab not in held:
                try:
                    targets = self._other_targets({name: path}, pin)
                except ServiceError:
                    targets = []
                cab = packed_cab(targets[0].name) or self._first_cab(targets[0]) if len(targets) == 1 else None
            if cab and learn_packed(name, cab):
                self._cores.pop(name, None)
                changed = True
        return changed

    def _listing(self, pin: Pin) -> list[tuple[str, str]]:
        listing: list[tuple[str, str]] = []
        for directory in pin.dirs:
            try:
                with os.scandir(directory) as entries:
                    listing.extend(
                        (entry.name, entry.path)
                        for entry in entries
                        if entry.name.endswith(".bundle") and entry.is_file(follow_symlinks=False)
                    )
            except OSError:
                continue
        return listing

    @toggle_timing.stage("index_listing")
    def _bundle_names(self, directory: Path) -> dict[str, None]:
        with os.scandir(directory) as entries:
            names = map(_ENTRY_NAME, filter(_REGULAR_FILE, entries))
            # Keep listing order for a rebuild and compare names independently of that order.
            return dict.fromkeys(filter(_BUNDLE_NAME, names))

    @_serialized
    def note_packed(self, paths: Iterable[Path], pin: Pin | None = None) -> None:
        """Learn CAB-based slots for packed generic files before import identity is resolved.

        Each imported file uses the slot of the install file it replaces; every install
        bundle in the packed group gets its own CAB-keyed slot.

        Args:
            paths: Uploaded bundles whose generic filename groups need slot identities.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
        """
        self._note_packed(paths, pin)

    def _note_packed(self, paths: Iterable[Path], pin: Pin | None = None) -> None:
        pin = pin or self.current_pin()
        files = {Path(path).name.removesuffix(".backup"): Path(path) for path in paths}
        parts = {part_of(name) for name in files if kind_of_file(name) == "other"}
        if pin is None or not parts:
            return
        packed = self._packed_groups(self._listing(pin))
        hit = parts & packed.keys()
        if not hit:
            return
        self._packed_wanted |= hit
        changed = self._learn_packed([path for part in hit for path in packed[part]])
        changed |= self._learn_packed_sources(files, packed, pin)
        if changed:
            self._indexes.pop(pin.key, None)

    def _migrate_packed_slots(self) -> None:
        """Split shared packed-group slots into per-bundle slots in mod records and configuration.

        Each bundle needs its own identity to retain independent choices. No game bytes change.
        """
        records = [record for record in self._records.values() if record.get("kind") == "other"]
        if not records:
            return
        pins = self.installs()
        groups = {pin.key: self._packed_groups(self._listing(pin)) for pin in pins}
        for pin in pins:
            self._indexes.pop(pin.key, None)
            self._index(pin)  # learns the install's bundles of the parts the records use
        moved: dict[str, dict[str, set[str]]] = {}  # mod id -> old shared slot -> its files' own slots
        changed = False
        for record in records:
            names = [name for name in record["files"] if any(part_of(name) in packed for packed in groups.values())]
            if not names:
                continue
            try:
                paths = {path.name: path for path in self._paths(record)}
            except ServiceError:
                continue
            for pin in pins:
                self._learn_packed_sources({name: paths[name] for name in names if name in paths}, groups[pin.key], pin)
            for name in names:
                if packed_cab(name) is None:
                    continue
                shared = "other:" + hashlib.sha256(repr(part_of(name)).encode()).hexdigest()
                moved.setdefault(record["id"], {}).setdefault(shared, set()).add(core_of(name))
            target = other_target(record["files"])
            if target != record["target"]:
                record["target"] = target
                changed = True
        if changed:
            self._save()
        if not moved:
            return
        self._indexes.clear()

        def update(data: dict) -> bool:
            done = False
            for install, folder in data.get("folders", {}).items():
                configured = folder.get("configured", {})
                for old, entry in list(configured.items()):
                    source = entry.get("source", "")
                    new = moved.get(source[4:], {}).get(old) if source.startswith("mod:") else None
                    if not new:
                        continue
                    entry = configured.pop(old)
                    for key in sorted(new):
                        configured.setdefault(key, copy.deepcopy(entry))
                    for field_name in ("built", "updated"):
                        if old in folder.get(field_name, {}):
                            value = folder[field_name].pop(old)
                            for key in sorted(new):
                                folder[field_name].setdefault(key, copy.deepcopy(value))
                    for marker in folder.get("dismissed", {}).values():
                        if isinstance(marker, dict) and old in marker:
                            value = marker.pop(old)
                            for key in sorted(new):
                                marker.setdefault(key, copy.deepcopy(value))
                    items = [
                        item
                        for item in data.get("unfinished", [])
                        if item.get("install") == install and item.get("core") == old
                    ]
                    for item in items:
                        data["unfinished"].remove(item)
                        data["unfinished"].extend({**item, "core": key} for key in sorted(new))
                    done = True
            return done

        self.profiles.update(update)

    def _index(self, pin: Pin) -> dict[str, list[Path]]:
        """Index the install's bundle files by lower-case core (names only).

        Compare regular-file names only in directories with a new revision. Build paths, slot mappings and name
        lookups again only when membership changes.
        """
        cached = self._indexes.get(pin.key)
        try:
            signature_ = tuple((directory, _directory_revision(directory)) for directory in pin.dirs)
            if cached and cached[0] == signature_ and signature_:
                return cached[1]
            previous = dict(cached[0]) if cached else {}
            files = {
                directory: cached[2][directory]
                if cached and previous.get(directory) == revision
                else self._bundle_names(directory)
                for directory, revision in signature_
            }
        except OSError:
            signature_ = ()
            files = {directory: {} for directory in pin.dirs}
            for name, path in self._listing(pin):
                files[Path(path).parent][name] = None
        if cached and cached[2] == files:
            self._indexes[pin.key] = (signature_, cached[1], files, cached[3])
            return cached[1]
        listing = [(name, os.path.join(directory, name)) for directory in pin.dirs for name in files[directory]]
        packed = self._packed_groups(listing)
        wanted = self._wanted_packed()
        self._learn_packed([path for key, paths in packed.items() if key in wanted for path in paths])
        index: dict[str, list[Path]] = {}
        for name, path in listing:
            core = self._cores.get(name)
            if core is None:
                core = self._cores[name] = core_of(name)
            index.setdefault(core, []).append(Path(path))
        index = {key: sorted(paths) for key, paths in index.items()}
        self._generation += 1
        self._indexes[pin.key] = (signature_, index, files, self._generation)
        return index

    def _kind(self, core: str, pin: Pin) -> str | None:
        """Use the bundle category for special slots, including files removed by an update."""
        if core.startswith("other:"):
            return "other"
        paths = self._index(pin).get(core.lower(), [])
        if paths:
            return next((kind for path in paths if (kind := kind_of_file(path.name))), None)
        for name in self._applied:
            path = Path(name)
            if path.parent in pin.dirs and core_of(path.name) == core.lower():
                return kind_of_file(path.name)
        for record in self._records.values():
            if core_of(record["files"][0]) == core.lower():
                return record["kind"]
        mapped = self._maps.get(pin.key)
        if mapped and core.lower() in mapped[1].model.values():
            return "model"
        return kind_of(core)

    def _core_map(self, pin: Pin) -> CoreMap:
        """How the install's cores map to variant keys (Catalog.core_map on its file names)."""
        index = self._index(pin)
        built_for = self._indexes[pin.key][3]
        cached = self._maps.get(pin.key)
        if cached and cached[0] == built_for:
            return cached[1]
        slots = [
            parse_filename(path.name)
            for paths in index.values()
            if (path := next((part for part in paths if kind_of_file(part.name)), None)) is not None
        ]
        spr = [slot.core for slot in slots if kind_of(slot.core, slot.category) == "spr"]
        lobby = [slot.core for slot in slots if kind_of(slot.core, slot.category) == "lobby"]
        special = [
            slot.core
            for slot in slots
            if kind_of(slot.core, slot.category) == "lobby" and slot.category in ("spinebackground", "spinecharacters")
        ]
        build = getattr(self.catalog, "core_map", None)
        if build is not None:
            mapped = build(spr, lobby, special_cores=special) if special else build(spr, lobby)
        else:  # a catalog without core maps: the current catalog's own answers
            keys, spr_of, lobby_of = {}, {}, {}
            for core in [*spr, *lobby]:
                variant = self.catalog.variant_for_core(core.lower())
                if variant is not None:
                    keys[core.lower()] = variant.key
                    target = spr_of if core.lower().endswith("_spr") else lobby_of
                    target.setdefault(variant.key, core)
            mapped = CoreMap(keys, spr_of, lobby_of, {})
        models = [slot.core.lower() for slot in slots if kind_of(slot.core, slot.category) == "model"]
        model_map = getattr(self.catalog, "model_map", None)
        model_variant = getattr(self.catalog, "variant_for_model_core", self.catalog.variant_for_core)
        keys = (
            model_map(models)
            if model_map
            else {core.lower(): variant.key for core in models if (variant := model_variant(core.lower())) is not None}
        )
        model_of = {}
        preferred = {
            v.key: v.model.lower()
            for family in getattr(self.catalog, "families", lambda: [])()
            for v in family.variants
            if getattr(v, "model", None)
        }
        # Prefer this install's prefab over its dependency cores, even when a dependency sorts first.
        prefabs = {
            core
            for core in models
            if any(parse_filename(path.name).category == "character" for path in index[core.lower()])
        }
        for core in sorted(models):
            key = keys.get(core.lower(), core.lower())
            mapped.keys.setdefault(core.lower(), key)
            model_of.setdefault(key, core)
            if core in prefabs and model_of[key] not in prefabs:
                model_of[key] = core
        for key, core in preferred.items():
            if key in model_of:
                model_of[key] = core
                mapped.keys.setdefault(core, key)
        model_cores = {
            key: (core, *sorted(value for value in models if value != core and keys.get(value.lower()) == key))
            for key, core in model_of.items()
        }
        mapped = replace(mapped, model=model_of, model_cores=model_cores)
        self._maps[pin.key] = (built_for, mapped)
        return mapped

    def _names(self, pin: Pin) -> dict[str, Path]:
        """The install's bundle files by name, rebuilt with its index."""
        index = self._index(pin)
        built_for = self._indexes[pin.key][3]
        cached = self._names_cache.get(pin.key)
        if cached and cached[0] == built_for:
            return cached[1]
        names = {path.name: path for paths in index.values() for path in paths}
        self._names_cache[pin.key] = (built_for, names)
        return names

    def _originals(self) -> dict[str, int]:
        """The kept originals by name with their sizes, listed again when the folder changed."""
        directory = self.root / "originals"
        try:
            signature_: Any = directory.stat().st_mtime_ns
        except OSError:
            signature_ = None
        if self._originals_cache and self._originals_cache[0] == signature_ and signature_ is not None:
            return self._originals_cache[1]
        sizes: dict[str, int] = {}
        if signature_ is not None:
            for path in directory.glob("*.bundle"):
                with contextlib.suppress(OSError):
                    sizes[path.name] = path.stat().st_size
        self._originals_cache = (signature_, sizes)
        return sizes

    def _record_maps(self) -> tuple[dict, dict]:
        """The records by name and by folder, rebuilt when the records changed."""
        key = (self._version, id(self._applied), len(self._applied))
        if self._maps_cache and self._maps_cache[0] == key:
            return self._maps_cache[1]
        maps = _record_maps(self._applied)
        self._maps_cache = (key, maps)
        return maps

    def _game_files(self, key: str, kind: Kind | None = None, pin: Pin | None = None) -> list[Path]:
        if kind == "other":
            pin = self._current(pin)
            record = next(
                (record for record in self._records.values() if record["kind"] == "other" and record["target"] == key),
                None,
            )
            if record is None:
                return self._index(pin).get(key, []) if key.startswith("other:") else []
            index = self._index(pin)
            slots = {core_of(name) for name in record["files"]}
            if all(slot in index for slot in slots):
                return resolver.slot_files(index, slots)
            return self._other_targets({path.name: path for path in self._paths(record)}, pin)
        variant = self._variant(key)
        pin = self._current(pin)
        index = self._index(pin)
        return [
            path
            for current, value in (
                ("spr", variant.spr),
                ("lobby", variant.lobby),
                ("model", getattr(variant, "model", None)),
            )
            if value and (kind is None or current == kind)
            for core in (self._core_map(pin).slot_cores(value) if current == "model" else (value.lower(),))
            for path in index.get(core, [])
        ]

    @_serialized
    def model_cores(self, variant_key: str, pin: Pin | None = None) -> list[str]:
        """List a model slot's prefab core followed by its own installed dependency cores.

        Args:
            variant_key: Variant key whose catalog metadata supplies the model prefab core.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            Slot cores, only the prefab core without an install, or empty when the variant has no model.
        """
        variant = self._variant(variant_key)
        core = getattr(variant, "model", None)
        if not core:
            return []
        pin = pin or self.current_pin()
        return list(self._core_map(pin).slot_cores(core)) if pin else [core.lower()]

    def _other_targets(self, files: dict[str, Path], pin: Pin) -> list[Path]:
        """The official files generic mod files replace or update (``targets.generic``)."""
        return sorted({target for match in self._other_matches(files, pin).values() for target in match.targets})

    def _other_matches(self, files: dict[str, Path], pin: Pin) -> dict[str, resolver.Match]:
        available = [path for slot, paths in self._index(pin).items() if slot.startswith("other:") for path in paths]

        def container_keys(path: Path, source: bool) -> set:
            if source:
                bundle = Bundle.load(path)
                if bundle is None:
                    raise ServiceError("bundle_invalid", file=path)
            else:
                bundle = Bundle.load(self._official_for(path, pin))
            return bundle.get_asset_keys("cont_name_type") if bundle else set()

        return resolver.generic(
            files, available, self._readers(available, pin, keys=False), container_keys=container_keys
        )

    def _view(
        self, pins: list[Pin], allow: str, config: dict | None | type(...) = ..., snapshot: bool = False
    ) -> _View:
        self._refresh_state()
        if config is ...:
            config = self.profiles.read()
        return _View(self, pins, config, allow, snapshot)
