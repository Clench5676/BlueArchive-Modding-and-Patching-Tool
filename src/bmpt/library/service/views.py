"""What the game shows: the state, the student list, the selection, the bundles and the previews' reads."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from bmpt.engine import (
    LogFunc,
    no_log,
    parse_filename,
)
from ...core.errors import ServiceError
from ...gameassets.preview_reads import PreviewReads
from .. import profiles as profiles_mod
from .. import targets as resolver
from ..judge import (
    core_of,
    resource_type_of_file,
)
from ..records import (
    GameFileState,
    Pin,
    AltState,
)
from ..safety import check_resource_path
from .base import _Entry, _Part, _Unread, _View, _serialized


class ViewsMixin:
    """What the game shows: the state, the student list, the selection, the bundles and the previews' reads."""

    def _file_state(self, view: _View, entry: _Entry, part: _Part) -> GameFileState:
        name = part.official_name or part.path.name
        parsed = parse_filename(name)
        copy_ = entry.copies.get(str(part.path))
        applied = "unknown" if part.carried == "damaged" else part.carried
        checked = (
            None if part.official else self.original_check(name, copy_, roots=[pin.root for pin in view.pins.values()])
        )
        return GameFileState(
            part.path,
            entry.core if entry.resource_type == "other" else parsed.core,
            entry.resource_type,
            applied,
            part.crc,
            name in view.originals,
            parsed.date,
            part.size,
            copy_,
            copy_ is not None,
            part.official,
            checked,
        )

    @_serialized
    def state(self, alt_id: str, pin: Pin | None = None) -> AltState:
        """Inspect an alt's game parts using size, date, and headers without hashing whole files.

        Needs Fix comes from the recorded write while the file remains unchanged.

        Args:
            alt_id: alt key, or generic other:/other-set: target whose parts are inspected.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        pin = self._current(pin)
        view = self._view(self._view_pins(pin), "student")
        files = []
        if alt_id.startswith(("other:", "other-set:")):
            values = {core_of(path.name) for path in self._game_files(alt_id, "other", pin)}
        else:
            alt = self._alt(alt_id)
            values = (alt.portrait, alt.memorial_lobby, getattr(alt, "chibi", None))
        for value in values:
            if not value:
                continue
            core = view.maps[pin.key].slot(value)
            entry = view.entry(pin, core)
            files.extend(self._file_state(view, entry, part) for part in entry.parts)
        sources = {file.applied for file in files}
        crc = (
            "needs_fix"
            if any(f.crc == "needs_fix" for f in files)
            else ("fixed" if any(f.crc == "fixed" for f in files) else "ok")
        )
        return AltState(
            alt_id,
            files,
            sources.pop() if len(sources) == 1 else "unknown",
            crc,
            any(f.has_kept_official for f in files),
        )

    def _view_pins(self, pin: Pin) -> list[Pin]:
        pins = [pin]
        pins.extend(other for other in self.installs() if other.key != pin.key)
        return pins

    @_serialized
    def configured(self, alt_id: str, pin: Pin | None = None) -> dict[str, str | None]:
        """Read configured sources by asset kind, combining camera art and prefab choices.

        Args:
            alt_id: alt key whose portrait, memorial lobby, and chibi choices are requested.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Sources by kind, with None for official or unconfigured choices.
        """
        alt = self._alt(alt_id)
        pin = self._current(pin)
        folder = (((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        from .. import memorial_lobby_camera

        return memorial_lobby_camera.overlay(
            memorial_lobby_camera.cameras(self, alt_id), folder, self._configured_sources(alt, folder)
        )

    @staticmethod
    def _configured_sources(alt: Any, folder: dict) -> dict[str, str | None]:
        return {
            resource_type: (folder.get(value.lower()) or {}).get("source") if value else None
            for resource_type, value in (
                ("portrait", alt.portrait),
                ("memorialLobby", alt.memorial_lobby),
                ("chibi", getattr(alt, "chibi", None)),
            )
            if resource_type != "chibi" or hasattr(alt, "chibi")
        }

    @_serialized
    def student_list_snapshot(self, alt_ids: list[str]) -> dict[str, Any]:
        """Read one install and configuration for the whole student list under the library lock.

        The student list needs manifest intent and cheap replacement checks, not physical
        installation verification. Detailed selection errors and byte verification
        remain in ``selection`` for the detail screen and write operations.

        Args:
            alt_ids: student list alt keys to include; stored explicit ownership targets are added too.

        Returns:
            Current install, mods, favorites, configured sources, cheap modified-source flags,
            and explicit assignments from one locked snapshot.
        """
        pin = self.current_pin()
        config = self.profiles.read() if pin is not None else None
        folder = ((config or {}).get("folders") or {}).get(pin.key) if pin else None
        choices = (folder or {}).get("configured") or {}
        assigned = {record["assigned_alt"] for record in self._records.values() if record.get("assigned_alt")}
        configured = {}
        from .. import memorial_lobby_camera

        made = {}
        for record in memorial_lobby_camera.cameras(self):
            made.setdefault(memorial_lobby_camera.meta_of(record)["alt"], []).append(record)
        for key in dict.fromkeys([*alt_ids, *sorted(assigned)]):
            try:
                configured[key] = (
                    memorial_lobby_camera.overlay(
                        made.get(key, []), choices, self._configured_sources(self._alt(key), choices)
                    )
                    if pin
                    else {"portrait": None, "memorialLobby": None}
                )
            except ServiceError:
                configured[key] = {"portrait": None, "memorialLobby": None}
        modified = {}
        if pin is not None:
            allow = "scan" if self._scan_allowed() else "recorded"
            modified = self._modified(pin, _View(self, [pin], config, allow))
        return {
            "install": pin.key if pin else None,
            "mods": [self._entry(record) for record in self._records.values()],
            "favorites": set(self._favorites),
            "configured": configured,
            "modified": modified,
            "assigned": sorted(assigned),
        }

    @_serialized
    def selection(self, alt_id: str, pin: Pin | None = None) -> dict[str, dict]:
        """Read saved choices and verified installation status using the master switch as intent.

        Every current part must match the recorded write of this source. Fingerprints are
        cached by size, mtime and ctime; an unchanged tail is not proof of installation.

        Args:
            alt_id: alt key whose configured choices need byte-verified installation status.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Configured kinds mapped to source, installed status, error, and any incomplete-preview flag.
        """
        pin = self._current(pin)
        alt = self._alt(alt_id)
        config = self.profiles.read()
        folder = (((config or {}).get("folders") or {}).get(pin.key) or {}).get("configured") or {}
        result = {}
        for resource_type, core_name in (
            ("portrait", alt.portrait),
            ("memorialLobby", alt.memorial_lobby),
            ("chibi", getattr(alt, "chibi", None)),
        ):
            choice = folder.get(core_name.lower()) if core_name else None
            if not choice:
                continue
            result[resource_type] = self._selection_of(pin, config, core_name, choice)
        from .. import memorial_lobby_camera

        return memorial_lobby_camera.overlay_selection(self, alt_id, pin, config, folder, result)

    def _selection_of(self, pin: Pin, config: dict | None, core: str, choice: dict) -> dict:
        """One configured entry's saved choice and whether it is verified installed (``selection``)."""
        paths = self._spine_parts(core, pin)
        installed = bool(paths) and profiles_mod.enabled(config) and not choice.get("error")
        try:
            for path in paths:
                record = self._applied.get(str(path.resolve())) or {}
                if (
                    record.get("source") != choice["source"]
                    or record.get("removed")
                    or self._hashes.get(path).sha1 != record.get("sha1")
                ):
                    installed = False
                    break
            recorded = {
                Path(name)
                for name, record in self._applied.items()
                if Path(name).parent in pin.dirs
                and self._core_map(pin).slot(core_of(Path(name).name)) == core.lower()
                and record.get("source") == choice["source"]
                and not record.get("removed")
            }
            installed = installed and recorded == set(paths)
        except (OSError, ServiceError):
            installed = False
        entry = {
            "source": choice["source"],
            "installed": installed,
            "error": None if installed else choice.get("error"),
        }
        source = choice["source"]
        if (
            source.startswith("mod:")
            and source[4:] in self._records
            and self._incomplete(self._records[source[4:]], pin)
        ):
            entry["incomplete"] = True
        return entry

    def _swap_outdated(self, paths: list[Path], choice: dict) -> bool:
        """Tell whether the game files of a chibi slot hold another alt's costume built under an outdated rule.

        ``choice`` is its configured entry. A costume is outdated when it was built under an older costume rule than the
        program's, or under one not recorded (every swap applied before the rule was recorded): applying it again builds
        it under the current rule.
        """
        from ...gameassets.costume_rule import COSTUME_RULE

        if choice.get("resource_type") != "chibi" or not str(choice.get("source") or "").startswith("alt:"):
            return False
        return any((self._applied.get(str(path.resolve())) or {}).get("costume_rule") != COSTUME_RULE for path in paths)

    def _selection_error(self, pin: Pin, cores, error: dict | None) -> None:
        def change(data):
            choices = (data["folders"].get(pin.key) or {}).get("configured", {})
            changed = False
            for core_name in cores:
                choice = choices.get(core_name)
                if choice is not None and choice.get("error") != error:
                    if error is None:
                        choice.pop("error", None)
                    else:
                        choice["error"] = error
                    changed = True
            return changed

        self._update_profiles(change)

    @_serialized
    def modified(self, pin: Pin | None = None) -> dict[str, str]:
        """Find alts with non-official portrait or special-slot files using cheap checks.

        Trust unchanged write records, excluding parts recorded as official. With
        scan consent, check other slot headers for trailing bytes or a size that
        differs from the known official size.

        Args:
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            alt keys mapped to mod:<id>, alt:<key>, or unknown; empty without an install.
        """
        pin = self.current_pin() if pin is None else pin
        if pin is None:
            return {}
        allowed = self._scan_allowed()
        view = self._view([pin], "scan" if allowed else "recorded")
        return self._modified(pin, view)

    def _modified(self, pin: Pin, view: _View) -> dict[str, str]:
        out: dict[str, str] = {}
        for core_name, paths in view.indexes[pin.key].items():
            alt = self.catalog.alt_for_core(core_name)
            if alt is None or (not core_name.endswith("_spr") and not getattr(alt, "spine_category", None)):
                continue
            resource_type = (
                next((resource_type for path in paths if (resource_type := resource_type_of_file(path.name))), None)
                or "portrait"
            )
            sources = set()
            for path in paths:
                try:
                    part = view.part(path, pin, resource_type)
                except (_Unread, OSError):
                    continue
                if not part.official and part.carried != "damaged":
                    sources.add(part.carried)
            if sources:
                out[alt.key] = sources.pop() if len(sources) == 1 else "unknown"
        return out

    @_serialized
    def official_bundles(self, core: str, pin: Pin | None = None, *, current_fallback: bool = False) -> list[Path]:
        """Resolve official parts of a core, including kept originals of overwritten game files.

        Resolve the whole set or fail. Chibi previews can explicitly request current files where
        no official copy remains; callers rebuilding mods always require verified official bytes.

        Args:
            core: Bundle core; chibi slots include their own installed dependency cores.
            pin: Game install and server the operation is fixed to; None uses the current install.
            current_fallback: Whether a missing official copy may use current bytes for a chibi preview.

        Raises:
            ServiceError: No target parts exist or a required verified official copy is unavailable.
        """
        pin = self._current(pin)
        paths = self._spine_parts(core, pin)
        if not paths:
            raise ServiceError("game_file_not_found", alt=core)
        resolve = self._official_or_current if current_fallback else self._official_for
        return [resolve(path, pin) for path in paths]

    def _spine_parts(self, core: str, pin: Pin) -> list[Path]:
        """The supported character bundle parts of a core, in game folder order.

        Chibi parts include the prefab and its own meshes, materials, textures and clips; shared dependencies
        remain supplied by the install's CAB graph.
        """
        core = core.lower()
        index = self._index(pin)
        paths = [path for value in self._core_map(pin).slot_cores(core) for path in index.get(value, [])]
        return [path for path in paths if resource_type_of_file(path.name)]

    @_serialized
    def live_bundles(self, core: str, pin: Pin | None = None) -> list[Path]:
        """Order a core's live game files with non-official parts last so mod assets win preview merges.

        Files merge by name, the last one winning (``GameAssets.spine_files_from``). A mod preview merges the mod's
        parts after the official ones; the game folder lists parts by file name, so a part left official could come
        after an applied mod part and win a texture it shares by name: the mod's atlas drawn over the official
        texture. Putting every part that is not official last gives the game files the preview's order. Judged by
        the cheap rule (headers, never whole files).

        Args:
            core: Bundle core whose live parts are merged for a game-state preview.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        pin = self._current(pin)
        paths = self._spine_parts(core, pin)
        if not paths:
            raise ServiceError("game_file_not_found", alt=core)
        view = self._view([pin], "student")
        resource_type = self._resource_type(core, pin) or "portrait"
        parts = [view.part(path, pin, resource_type) for path in paths]
        return [part.path for part in sorted(parts, key=lambda part: not part.official)]

    @_serialized
    def preview_bundles(self, mod_id: str, pin: Pin | None = None) -> list[Path]:
        """Return omitted parts followed by the mod's overriding parts.

        Parts use the import/apply replacement mapping. Omitted parts prefer verified official
        copies of the mod's own core in this install, falling back to its current files. Pass this order to
        ``GameAssets.spine_files_from`` so mod assets win.

        Args:
            mod_id: Stored mod ID to preview; a camera resolves its source's memorial lobby art.
            pin: Game install and server the operation is fixed to; None uses the current install.
        """
        from ..memorial_lobby_camera import meta_of

        pin = self._current(pin)
        record = self._record(mod_id)
        camera = meta_of(record)
        if camera is not None:  # a memorial lobby camera shows its source's art
            source = camera["source"]
            return (
                self._preview(self._record(source[4:]), pin)
                if source.startswith("mod:")
                else self.official_bundles(camera["core"], pin=pin)
            )
        return self._preview(record, pin)

    @_serialized
    def save_camera(
        self,
        alt_id: str,
        source: str,
        scale: float,
        offset: list[float],
        prefab: str,
        root: str,
        pin: Pin | None = None,
        log: LogFunc = no_log,
    ) -> dict[str, Any]:
        """Store a memorial-lobby camera as a new memorial lobby choice for an alt.

        Args:
            alt_id: alt key whose memorial lobby is framed.
            source: official, memorial lobby mod:<id>, or camera mod:<id> to compose with the new framing.
            scale: Additional zoom multiplier validated by memorial_lobby_camera.check_params.
            offset: Horizontal and vertical NGUI-unit translation for the new framing.
            prefab: Current ui-uilobbyelement bundle filename used as the camera's base.
            root: Name of the memorial lobby's root GameObject to frame inside that prefab.
            pin: Game install and server the operation is fixed to; None uses the current install.
            log: Receives user-facing operation messages.

        Returns:
            The stored generic camera record with source-art and framing metadata.
        """
        from .. import memorial_lobby_camera

        return memorial_lobby_camera.save(self, alt_id, source, scale, offset, prefab, root, self._current(pin), log)

    @_serialized
    def camera_prefab(self, mod_id: str, pin: Pin | None = None) -> tuple[dict, Path, Path | None, Path | None] | None:
        """Resolve a camera's metadata and prefab inputs for previewing.

        Args:
            mod_id: Stored mod ID to inspect for camera metadata.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Copied metadata, stored prefab, optional updated official prefab, and optional
            intro timeline; None for a noncamera mod.
        """
        from .. import memorial_lobby_camera

        record = self._record(mod_id)
        meta = memorial_lobby_camera.meta_of(record)
        return (
            None
            if meta is None
            else (copy.deepcopy(meta), *memorial_lobby_camera.preview_files(self, record, self._current(pin)))
        )

    @_serialized
    def memorial_lobby_prefab_base(self, core: str, pin: Pin | None = None) -> Path | None:
        """Find an unframed official prefab for other memorial lobby previews when a camera is installed.

        Args:
            core: Memorial Lobby bundle core whose preview must avoid the installed camera's framing.
            pin: Game install and server the operation is fixed to; None uses the current install if available.

        Returns:
            Official prefab path, or None without an install or an installed camera for this core.
        """
        from .. import memorial_lobby_camera

        pin = pin or self.current_pin()
        return (
            memorial_lobby_camera.live_base(self, core, pin)
            if pin is not None and memorial_lobby_camera.cameras(self)
            else None
        )

    def _preview(self, record: dict[str, Any], pin: Pin) -> list[Path]:
        paths = self._paths(record)
        targets = self._spine_parts(core_of(paths[0].name), pin)
        replaced = self._replaced_parts({path.name: path for path in paths}, targets, pin)
        others = [self._official_or_current(path, pin) for path in targets if path not in replaced]
        return others + paths

    @_serialized
    def preview_complements(
        self, core: str, names: list[str], pin: Pin | None = None, *, files: dict[str, Path] | None = None
    ) -> list[Path]:
        """Resolve incoming-preview complements with the same replacement rule as stored mods.

        Args:
            core: Bundle core whose official or current parts can supply omitted assets.
            names: Incoming bundle filenames used for a names-only resolution when files is None.
            pin: Game install and server the operation is fixed to; None uses the current install.
            files: Incoming filenames mapped to readable source paths for full evidence;
                None resolves names alone.
        """
        pin = self._current(pin)
        targets = self._spine_parts(core, pin)
        if files is not None:
            replaced = self._replaced_parts(files, targets, pin)
        else:
            replaced = resolver.resolve({name: Path(name) for name in names}, targets).replaced
        return [self._official_or_current(path, pin) for path in targets if path not in replaced]

    def preview_reads(self, policy: str, pin: Pin | None = None) -> PreviewReads:
        """Choose dependency bytes for a 3D preview independently of the mods switch and live state.

        A chibi export also loads shared bundles and textures stored under other cores (ch0263's materials take
        theirs from the NPC en0002), which other mods replace; a preview never reads them as the game folder holds
        them now, so it
        follows neither the mods switch nor the game's state:

        - ``official`` (the Original card): verified official bytes (``_official_or_current``); the current bytes only
          where no official copy is left at all, as for the slot's own parts.
        - ``configured`` (a mod or an import): the bytes the configured source of each file's slot writes (its kept
          built copy, else the mod's own file of that name, else the game file while it carries that source), else
          as ``official``.

        Not serialized, like ``official_bundles``: the export calls ``resolve`` under the chibi lock.

        Args:
            policy: official for verified baselines with a current-file fallback, or configured
                for selected sources' kept builds, own files, or verified applied bytes before that fallback.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Dependency resolver, configuration cache key, and trusted-library-path predicate.

        Raises:
            ValueError: The policy is neither official nor configured.
        """
        if policy not in ("official", "configured"):
            raise ValueError(f"Unknown preview policy {policy!r}")
        pin = self._current(pin)
        folder: dict[str, Any] = {}
        if policy == "configured":
            with contextlib.suppress(ServiceError):
                folder = ((self.profiles.read() or {}).get("folders") or {}).get(pin.key) or {}
        configured, built = folder.get("configured") or {}, folder.get("built") or {}
        key = hashlib.sha256(json.dumps([policy, configured, built], sort_keys=True, default=str).encode()).hexdigest()
        core_map = self._core_map(pin)
        dirs = list(pin.dirs)
        chosen: dict[Path, Path] = {}
        lock = threading.Lock()

        def official(path: Path) -> Path:
            try:
                check_resource_path(path, dirs)
            except ServiceError:
                return path  # outside this install's resource folders: the library holds nothing for it
            return self._official_or_current(path, pin)

        def configured_file(path: Path) -> Path | None:
            resource_type = resource_type_of_file(path.name)
            if not resource_type:
                return None
            core = core_of(path.name)
            slot = (
                core_map.slot(core) if resource_type == "chibi" else core
            )  # the key _configure and the built copies use
            source = (configured.get(slot) or {}).get("source") or ""
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            if record is None:
                return None
            own = {file.name: file for file in self._paths(record)}
            kept = built.get(slot) or {}
            part = (kept.get("parts") or {}).get(path.name) if kept.get("source") == source else None
            if isinstance(part, dict):
                sha1 = part.get("sha1")
                candidate = (
                    own.get(part.get("mod_file"))
                    if part.get("mod_file")
                    else (
                        self.root / "built" / f"{sha1}.bundle"
                        if isinstance(sha1, str) and re.fullmatch(r"[0-9a-f]{40}", sha1)
                        else None
                    )
                )
                if candidate is not None and candidate.is_file() and self._hashes.get(candidate).sha1 == sha1:
                    return candidate
            if path.name in own and own[path.name].is_file():
                return own[path.name]
            applied = self._applied.get(str(path.resolve()))
            if applied and applied.get("source") == source and self._hashes.get(path).sha1 == applied.get("sha1"):
                return path  # the game file is the configured write (an adapted mod without a kept copy)
            return None

        def resolve(path: Path) -> Path:
            path = Path(path)
            with lock:
                if path in chosen:
                    return chosen[path]
            result = (configured_file(path) if policy == "configured" else None) or official(path)
            with lock:
                chosen[path] = result
            return result

        def trusted(path: Path) -> bool:
            try:
                return Path(path).resolve().is_relative_to(self.root)
            except (OSError, RuntimeError):
                return False

        return PreviewReads(resolve, key, trusted)

    def _incomplete(self, record: dict[str, Any], pin: Pin) -> bool:
        targets = self._spine_parts(core_of(record["files"][0]), pin)
        replaced = self._replaced_parts({path.name: path for path in self._paths(record)}, targets, pin)
        return any(
            self._official_or_current(path, pin) == path and not self._is_official(path)
            for path in targets
            if path not in replaced
        )

    @_serialized
    def preview_incomplete(self, mod_id: str, pin: Pin | None = None) -> bool:
        """Check whether a partial mod's preview needs non-official live parts.

        Args:
            mod_id: Stored mod ID; a camera inherits its art source's completeness.
            pin: Game install and server the operation is fixed to; None uses the current install if available.
        """
        from ..memorial_lobby_camera import meta_of

        pin = pin or self.current_pin()
        record = self._record(mod_id)
        camera = meta_of(record)
        if camera is not None:  # a memorial lobby camera is as complete as its source's art
            source = camera["source"]
            record = self._records.get(source[4:]) if source.startswith("mod:") else None
            if record is None:
                return False
        return bool(pin and self._incomplete(record, pin))
