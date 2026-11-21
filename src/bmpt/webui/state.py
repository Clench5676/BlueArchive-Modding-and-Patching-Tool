"""Small UI state kept between launches, in webui.json.

It holds the asset consent, recently opened alts, seen mods, the user's own aliases of the alts and the web
UI's own preferences (voice volume, the mod library folder, scan and Spine consents, the mod switch's and the classic
hand-off's flags, the data directory's id). It is kept apart from config.toml, which the classic GUI rewrites with only
its own keys.

Several app instances may share one data folder: every change is applied to the file as it is at that moment (read
again under a lock the instances share), so one instance never erases what another wrote, and reads pick up the
other instances' writes, under the same lock: Windows cannot replace a file another process has open.

The file is written the way the library's profiles.json is: flushed to disk before the replace, and the last good file
kept as ``webui.json.prev`` before every write. An unreadable file is read from that copy instead, and kept as
``webui.json.bad`` at the next write. With neither readable, the state starts from the defaults (an error is logged)
and the bad bytes are kept as ``webui.json.bad`` before the first write.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import math
import os
import secrets
import shutil
import tempfile
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

# the lock and the replace shared with the mod library (bmpt.core.filelock)
from ..host.store_formats import legacy_document, managed_bytes, read_runtime, runtime_store
from ..core.filelock import file_lock as _file_lock, replace as _replace
from ..core.logging_utils import log_failure

logger = logging.getLogger(__name__)

RECENT_MAX = 50
# the user's own aliases of a student (the student list search matches them): at most this many, each this many
# characters
ALIAS_MAX, ALIAS_LENGTH = 8, 24


def alias_text(value: Any) -> str:
    """Clean one alias the way it is kept.

    Args:
        value: The alias as typed.

    Returns:
        NFC text without control or format characters, every run of whitespace one space, trimmed; an empty string
        for anything but text.
    """
    if not isinstance(value, str):
        return ""
    text = "".join(
        ch for ch in unicodedata.normalize("NFC", value) if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    return " ".join(text.split())


def alias_key(text: str) -> str:
    """Return what makes two aliases the same one, ignoring case and width.

    Args:
        text: An alias, cleaned by ``alias_text``.
    """
    return unicodedata.normalize("NFKC", text).casefold()


def custom_aliases(value: Any) -> list[str]:
    """Clean a student's own aliases as the page gets them.

    Args:
        value: The stored list; anything that is not a list gives no aliases.

    Returns:
        The aliases cleaned by ``alias_text``, in their order, without empty, longer than ``ALIAS_LENGTH`` and
        repeated ones (``alias_key``), at most ``ALIAS_MAX``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        text = alias_text(item)
        if text and len(text) <= ALIAS_LENGTH and alias_key(text) not in seen:
            seen.add(alias_key(text))
            out.append(text)
    return out[:ALIAS_MAX]


# The student list's view (web/js/student-list/student-list-order.js keeps the same rules): merge toggle, the two
# segment filters,
# the display settings' sort, order and filter groups.
STUDENT_LIST_SORTS = ("recent", "added", "name", "mods", "default")
STUDENT_LIST_NATURAL = {"recent": "desc", "added": "desc", "name": "asc", "mods": "desc", "default": "asc"}
STUDENT_LIST_FILTERS = {
    "favorite": ("yes", "no"),
    "portrait": ("official", "replaced"),
    "memorialLobby": ("yes", "no"),
    "chibi": ("yes", "no"),
}


def student_list_view(value: Any) -> dict[str, Any]:
    """Make a stored or sent student list view valid; unknown fields and options are dropped.

    Args:
        value: The view: ``merged``, ``mods``, ``recruit``, ``sort``, ``order`` and the ``filters`` per group.

    Returns:
        The view with every field set; a missing or invalid one takes its default, and the order defaults to the
        sort's natural direction.
    """
    src = value if isinstance(value, dict) else {}

    def pick(key: str, allowed: tuple[str, ...], fallback: str) -> str:
        v = src.get(key)
        return v if isinstance(v, str) and v in allowed else fallback

    sort = pick("sort", STUDENT_LIST_SORTS, "recent")
    given = src.get("filters") if isinstance(src.get("filters"), dict) else {}
    filters = {}
    for group, options in STUDENT_LIST_FILTERS.items():
        chosen = given.get(group)
        on = [o for o in options if isinstance(chosen, list) and o in chosen]
        if on:
            filters[group] = on
    return {
        "group_alts": src.get("groupAlts", src.get("group_alts")) is True,
        "mods": pick("mods", ("all", "mods", "none"), "all"),
        "releaseStatus": pick("releaseStatus", ("all", "yes", "no"), "all"),
        "sort": sort,
        "order": pick("order", ("asc", "desc"), STUDENT_LIST_NATURAL[sort]),
        "filters": filters,
    }


def _recent_of(data: dict[str, Any]) -> list[str]:
    value = data.get("recent")
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _seen_mods_of(data: dict[str, Any]) -> set[str] | None:
    value = data.get("seen_mods")
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        return None
    return set(value)


class UiState:
    """The web UI's own state file (webui.json), shared by every instance on the data directory."""

    def __init__(self, path: Path):
        """Read the state and migrate its preferences.

        Args:
            path: The state file, webui.json; its folder also holds the previous copy, the bad copy and the lock.
        """
        self.path = Path(path)
        self.prev = self.path.with_name(f"{self.path.name}.prev")
        self.bad = self.path.with_name(f"{self.path.name}.bad")
        self._lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._known = False  # whether _data came from a readable file (else it is the defaults)
        # the (file, previous copy) signatures, each (mtime, size) or None, when they were last read or written
        self._seen: tuple[tuple[int, int] | None, tuple[int, int] | None] | None = None
        initial = self._current()
        # Startup consents are saved before tours run. Only an answer from before this launch proves prior use.
        self.had_asset_consent = isinstance(initial.get("consent"), bool)
        prefs = initial.get("prefs")
        # A stored Spine "No" stays as it is: the start shows the licence notice again until it is acknowledged.
        # Drop the obsolete startup_checks flag; where it was off while scan consent was on, scan consent becomes
        # off, so nobody is opted in to automatic checks.
        if isinstance(prefs, dict) and any(key in prefs for key in ("startup_checks", "mod_switch_shown")):

            def migrate(data: dict[str, Any]) -> None:
                prefs = data.get("prefs", {})
                if prefs.pop("startup_checks", None) is False and prefs.get("scan_consent") is True:
                    prefs["scan_consent"] = False
                # Drop the obsolete mod_switch_shown flag: the main screens always keep the switch.
                prefs.pop("mod_switch_shown", None)

            self._update(migrate)

    def _signature(self) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        def stat(path: Path) -> tuple[int, int] | None:
            try:
                st = path.stat()
            except OSError:
                return None
            return st.st_mtime_ns, st.st_size

        return stat(self.path), stat(self.prev)

    @staticmethod
    def _parse(path: Path) -> dict[str, Any]:
        """A state file's content; OSError when it cannot be read, ValueError when it is not a JSON object."""
        data = read_runtime(path)
        if not isinstance(data, dict):
            raise ValueError("UI state must be an object")
        return data

    def _read(self) -> dict[str, Any]:
        """Return the file's content now, for the strict readers.

        It is {} while the file is missing, and OSError or ValueError while it is unreadable. The previous copy is
        not consulted: stale state must not stand in for the file.
        """
        try:
            return self._parse(self.path)
        except FileNotFoundError:
            return {}

    def _load(self) -> tuple[dict[str, Any] | None, str]:
        """Return (the content now, the file's state), with the file lock held.

        The state is "ok" (webui.json parsed, and it is the content), "bad" (it exists but cannot be read) or
        "missing"; unless "ok", the content is the previous copy's (logged) or None when that cannot be read either.
        """
        state = "missing"
        try:
            return self._parse(self.path), "ok"
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            log_failure(logger, logging.WARNING, "Cannot read UI state, reading previous copy instead", error)
            state = "bad"
        try:
            data = self._parse(self.prev)
        except FileNotFoundError:
            if state == "bad":
                logger.error("UI state unreadable and no previous copy, starting from defaults")
            return None, state
        except (OSError, ValueError) as error:
            if state == "bad":
                log_failure(
                    logger, logging.ERROR, "Cannot read previous UI state either, starting from defaults", error
                )
            return None, state
        if state == "missing":
            logger.warning("UI state missing, reading previous copy")
        return data, state

    def _current(self) -> dict[str, Any]:
        """Return the state as the file holds it now; another instance may have written it.

        While the file is missing or unreadable it is what was last known (else its previous copy, else the
        defaults). The file is read under the lock the instances share, so another instance's write never meets
        this one's open file (Windows refuses to replace it).
        """
        with self._lock:
            if self._signature() in ((None, None), self._seen):
                return self._data
            try:
                with _file_lock(self._lock_path):
                    signature = self._signature()
                    data, state = self._load()
            except OSError:  # the lock not had in time (Windows gives up after 10 s): what was last known
                logger.warning("cannot lock %s", self._lock_path, exc_info=True)
                return self._data
            self._seen = signature  # an unreadable file is looked at once, not at every call
            if data is not None and (state == "ok" or not self._known):
                self._data, self._known = data, True
            return self._data

    def _update(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Apply one change to the file's current content and write it back.

        The content is read again under the lock the instances share, so keys another instance wrote meanwhile are
        kept. When the file cannot be read the change applies to what this instance knew, else to the previous copy,
        else to the defaults, and the bad file is kept.
        """
        with self._lock, _file_lock(self._lock_path):
            data, state = self._load()
            if data is None or (state != "ok" and self._known):
                data = copy.deepcopy(self._data)
            change(data)
            self._write(data, bad=state == "bad")

    def _write(self, data: dict[str, Any], *, bad: bool = False) -> None:
        """Replace the state atomically while both state locks are held.

        The new content reaches the disk before it replaces the file. The file it replaces, when it parsed, becomes
        the previous copy; when it could not be read (``bad``) it is kept as webui.json.bad for the user.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                store = runtime_store(self.path)
                raw = (
                    managed_bytes(store, data)
                    if store
                    else json.dumps(legacy_document(self.path, data), ensure_ascii=False, indent=1).encode()
                )
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            if bad:
                with contextlib.suppress(OSError):
                    _replace(self.path, self.bad)
            elif self.path.exists():
                self._keep_previous()
            _replace(name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(name)
            raise
        self._data, self._known, self._seen = data, True, self._signature()

    def _keep_previous(self) -> None:
        """Copy the current file to webui.json.prev (flushed to disk, replacing the previous copy in one step)."""
        fd, name = tempfile.mkstemp(prefix=f".{self.prev.name}.", suffix=".tmp", dir=self.prev.parent)
        try:
            with os.fdopen(fd, "wb") as out, self.path.open("rb") as stream:
                shutil.copyfileobj(stream, out)
                out.flush()
                os.fsync(out.fileno())
            _replace(name, self.prev)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(name)
            raise

    def diagnostic_state(self) -> dict | None:
        """Read the last known diagnostic settings without a file lock or disk access.

        Returns:
            The ``consent`` answers (``uiAssets``, ``spine``, ``scan``) and the ``libraryDir`` preference; None
            while another thread holds the state's lock.
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            prefs = self._data.get("prefs", {})
            scan = prefs.get("scan_consent")
            if scan is True and prefs.get("scan_consent_scope") != SCAN_CONSENT_SCOPE:
                scan = None  # as scan_consent: an Allow given for a narrower scope is asked again
            return {
                "consent": {
                    "uiAssets": self._data.get("consent"),
                    "spine": prefs.get("spine_consent"),
                    "scan": scan if isinstance(scan, bool) else None,
                },
                "libraryDir": prefs.get("library_dir"),
            }
        finally:
            self._lock.release()

    @property
    def consent(self) -> bool | None:
        """None until the user answered the consent popup; then True (cache allowed) or False."""
        value = self._current().get("consent")
        return value if isinstance(value, bool) else None

    def set_consent(self, granted: bool) -> None:
        """Save the answer to the asset consent popup.

        Args:
            granted: Whether the web view may cache light assets.
        """
        self._update(lambda data: data.__setitem__("consent", bool(granted)))

    @property
    def preview_cache(self) -> bool:
        """Enable preview storage by default while preserving an explicit opt-out."""
        return self.get("preview_cache") is not False

    def recent(self) -> list[str]:
        """Return the keys of the recently opened alts, the latest first."""
        return _recent_of(self._current())

    def preview_sources(self, install: str, alt: str) -> dict[str, str]:
        """Return the saved view choice of one alt.

        Args:
            install: The install's id.
            alt: The alt key.

        Returns:
            Resource type mapped to the chosen source: ``official`` or ``mod:<id>``.
        """
        data = self._current().get("asset_previews", {})
        folder = data.get(install, {}) if isinstance(data, dict) else {}
        sources = folder.get(alt, {}) if isinstance(folder, dict) else {}
        return (
            {
                resource_type: source
                for resource_type, source in sources.items()
                if resource_type in ("portrait", "memorialLobby", "chibi") and isinstance(source, str)
            }
            if isinstance(sources, dict)
            else {}
        )

    def preview_snapshot(self, install: str) -> dict[str, dict[str, str]]:
        """Read all saved view choices of one install at once, detached from mutable state.

        Args:
            install: The install's id.

        Returns:
            alt key mapped to what ``preview_sources`` returns for it.
        """
        data = self._current().get("asset_previews", {})
        folder = data.get(install, {}) if isinstance(data, dict) else {}
        return (
            {
                alt: {
                    resource_type: source
                    for resource_type, source in sources.items()
                    if resource_type in ("portrait", "memorialLobby", "chibi") and isinstance(source, str)
                }
                for alt, sources in folder.items()
                if isinstance(alt, str) and isinstance(sources, dict)
            }
            if isinstance(folder, dict)
            else {}
        )

    def set_preview_source(self, install: str, alt: str, resource_type: str, source: str) -> None:
        """Remember a view choice independently of the library's game configuration.

        Args:
            install: The install's id.
            alt: The alt key.
            resource_type: The resource type.
            source: ``official`` or ``mod:<id>``.
        """

        def change(data: dict[str, Any]) -> None:
            data.setdefault("asset_previews", {}).setdefault(install, {}).setdefault(alt, {})[resource_type] = source

        self._update(change)

    def reconcile_deleted_source(self, source: str, fallback) -> None:
        """Clear a removed preview source everywhere; retain Student Hall placement when another source exists.

        View choices that name the source take the fallback or are dropped. Student Hall entries that use it are moved
        to
        the fallback, merged into an entry that has it already, or removed; the student hall's revision grows then.

        Args:
            source: The removed source, ``mod:<id>``.
            fallback: ``fallback(alt, resource_type)`` returns the source to use instead, or a falsy value for none.
        """

        def change(data):
            for folder in data.get("asset_previews", {}).values():
                for alt, choices in folder.items():
                    for resource_type, selected in list(choices.items()):
                        if selected == source:
                            replacement = fallback(alt, resource_type)
                            if replacement:
                                choices[resource_type] = replacement
                            else:
                                del choices[resource_type]
            student_hall = data.get("prefs", {}).get("student_hall")
            if student_hall:
                changed = False
                replacements = {}
                for group, resource_type in [("portraits", "portrait"), ("memorialLobbies", "memorialLobby")]:
                    kept = []
                    existing = {
                        (entry["alt"], entry["source"]): entry["id"]
                        for entry in student_hall.get(group, [])
                        if entry.get("source") != source
                    }
                    for entry in student_hall.get(group, []):
                        if entry.get("source") == source:
                            changed = True
                            replacement = fallback(entry["alt"], resource_type)
                            if not replacement:
                                continue
                            identity = (entry["alt"], replacement)
                            if identity in existing:
                                replacements[entry["id"]] = existing[identity]
                                continue
                            entry["source"] = replacement
                            existing[identity] = entry["id"]
                        kept.append(entry)
                    student_hall[group] = kept
                if changed:
                    student_hall["active"] = replacements.get(student_hall.get("active"), student_hall.get("active"))
                    if not any(
                        entry["id"] == student_hall.get("active") for entry in student_hall.get("memorialLobbies", [])
                    ):
                        student_hall["active"] = next(
                            (entry["id"] for entry in student_hall.get("memorialLobbies", [])), None
                        )
                    student_hall["revision"] = student_hall.get("revision", 0) + 1

        self._update(change)

    def visit(self, alt_id: str) -> None:
        """Move an alt to the front of the recent list, which keeps ``RECENT_MAX`` keys.

        Args:
            alt_id: The alt opened.
        """

        def change(data: dict[str, Any]) -> None:
            recent = [v for v in _recent_of(data) if v != alt_id]
            data["recent"] = [alt_id, *recent][:RECENT_MAX]

        self._update(change)

    def seen_mods(self, baseline: Callable[[], list[str]] | None = None) -> set[str] | None:
        """Read the ids of the mods the user has seen.

        The file is read strictly: stale or unreadable state must not turn every mod into a new one.

        Args:
            baseline: Returns the library's mod ids. Only a missing key is filled with them, never an existing
                list. None leaves a missing key missing.

        Returns:
            The ids; None when they are unknown (no key and no baseline, or the file is unreadable).
        """
        try:
            with self._lock, _file_lock(self._lock_path):
                data = self._read()
                if "seen_mods" in data or baseline is None:
                    return _seen_mods_of(data)
            # The library has its own lock: take its snapshot without holding either state lock.
            ids = baseline()
            with self._lock, _file_lock(self._lock_path):
                data = self._read()
                if "seen_mods" not in data:
                    data["seen_mods"] = sorted(set(ids))
                    self._write(data)
                return _seen_mods_of(data)
        except (OSError, ValueError):
            logger.warning("cannot read seen mods from %s", self.path, exc_info=True)
            return None

    def mark_mods_seen(self, ids: list[str]) -> None:
        """Remember opened mods without changing the shared mod library.

        Args:
            ids: The ids of the mods opened.
        """

        def change(data: dict[str, Any]) -> None:
            value = data.get("seen_mods")
            seen = {v for v in value if isinstance(v, str) and v} if isinstance(value, list) else set()
            data["seen_mods"] = sorted(seen.union(ids))

        self._update(change)

    # --- the user's own aliases, by alt key ---

    def aliases(self, legacy_targets: dict[str, str] | None = None) -> dict[str, list[str]]:
        """Return every alt's own aliases, cleaned as ``custom_aliases`` does.

        Ordinary student keys identify the original alt. Special-only students need a catalog-selected target;
        their entries are moved once under the shared lock, preserving any explicitly saved alt aliases.

        Args:
            legacy_targets: student key mapped to the alt that takes the aliases stored under the student key.

        Returns:
            alt key mapped to its aliases; alts without aliases are left out.
        """
        table = self._current().get("aliases")
        if isinstance(table, dict) and legacy_targets and any(key in table for key in legacy_targets):

            def migrate(data: dict[str, Any]) -> None:
                aliases = data.get("aliases")
                if not isinstance(aliases, dict):
                    return
                for student, alt in legacy_targets.items():
                    if student in aliases:
                        aliases.setdefault(alt, aliases.pop(student))

            self._update(migrate)
            table = self._current().get("aliases")
        if not isinstance(table, dict):
            return {}
        out = {}
        for alt, value in table.items():
            names = custom_aliases(value)
            if isinstance(alt, str) and names:
                out[alt] = names
        return out

    def set_aliases(self, alt: str, names: list[str]) -> list[str]:
        """Keep an alt's own aliases; only this alt's entry of the file changes.

        Args:
            alt: The alt key.
            names: The aliases as typed; an empty result forgets the alt's entry.

        Returns:
            The aliases as kept, cleaned by ``custom_aliases``.
        """
        kept = custom_aliases(names)

        def change(data: dict[str, Any]) -> None:
            table = data.get("aliases")
            if not isinstance(table, dict):
                table = data["aliases"] = {}
            if kept:
                table[alt] = kept
            else:
                table.pop(alt, None)

        self._update(change)
        return kept

    # --- preferences ---

    def update_tours(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Merge presentation history under the existing profile lock, apart from library and preview state.

        Args:
            change: Edits the stored ``tours`` dict in place.
        """

        def update(data: dict[str, Any]) -> None:
            if not isinstance(data.get("tours"), dict):
                data["tours"] = {}
            change(data["tours"])

        self._update(update)

    def tours(self) -> dict[str, Any]:
        """Return a copy of the presentation history ``update_tours`` edits; empty when there is none."""
        history = self._current().get("tours")
        return copy.deepcopy(history) if isinstance(history, dict) else {}

    def get(self, key: str, default: Any = None) -> Any:
        """Read a preference.

        Args:
            key: The preference's key.
            default: Returned when it is not set.
        """
        prefs = self._current().get("prefs")
        return prefs.get(key, default) if isinstance(prefs, dict) else default

    def set(self, key: str, value: Any) -> None:
        """Save a preference.

        Args:
            key: The preference's key.
            value: Its new value, JSON-serializable.
        """

        def change(data: dict[str, Any]) -> None:
            prefs = data.get("prefs")
            if not isinstance(prefs, dict):
                prefs = data["prefs"] = {}
            prefs[key] = value

        self._update(change)

    def flag(self, key: str) -> bool:
        """Read a boolean preference of the tray, the mod switch or the classic hand-off.

        Args:
            key: A key of ``FLAGS``.

        Returns:
            The saved value; the flag's default until it is set.
        """
        value = self.get(key)
        return value if isinstance(value, bool) else FLAGS[key]

    @property
    def scan_consent(self) -> bool | None:
        """The scan consent: True (the game folders may be checked and Steam's records read) or False.

        It is None until the user answered the scan consent popup, and again for an Allow given before the popup
        covered Steam's records.
        """
        value = self.get("scan_consent")
        if value is True and self.get("scan_consent_scope") != SCAN_CONSENT_SCOPE:
            return None
        return value if isinstance(value, bool) else None

    def set_scan_consent(self, granted: bool) -> None:
        """Save an answer to the scan consent popup, for what the popup covers now.

        Args:
            granted: Whether the game folders may be checked and Steam's records read.
        """

        def change(data: dict[str, Any]) -> None:
            prefs = data.get("prefs")
            if not isinstance(prefs, dict):
                prefs = data["prefs"] = {}
            prefs["scan_consent"] = bool(granted)
            prefs["scan_consent_scope"] = SCAN_CONSENT_SCOPE

        self._update(change)

    @property
    def spine_consent(self) -> bool | None:
        """The Spine licence notice's answer: None until it is answered, True once acknowledged.

        A stored False (a No) makes the start show the notice again.
        """
        value = self.get("spine_consent")
        return value if isinstance(value, bool) else None

    def data_id(self, data_dir: Path) -> str:
        """Return this data directory's id, made at the first call and kept with the directory's path.

        The id (``data_id``) is stored with the data directory's own path (``data_path``). When the recorded path is
        another one, the folder was copied or moved: a copy (the recorded folder still exists and holds the same id)
        gets a new id, so it never shares the original's; a moved folder keeps its id and records its new path. Only
        the services call it, when they first build the library, so the constructor never adds preferences.

        Args:
            data_dir: The data directory.
        """
        here = data_path(data_dir)
        made: list[str] = []

        def change(data: dict[str, Any]) -> None:
            prefs = data.get("prefs")
            if not isinstance(prefs, dict):
                prefs = data["prefs"] = {}
            old, path = prefs.get("data_id"), prefs.get("data_path")
            if _is_data_id(old) and path == here:
                made.append(old)
                return
            if _is_data_id(old) and not (isinstance(path, str) and path and _holds_id(Path(path), old)):
                new = old  # moved (or no path recorded yet): the same data directory
                logger.info("data directory now at %s, keeping its id", here)
            else:
                new = secrets.token_hex(DATA_ID_LENGTH // 2)
                if _is_data_id(old):
                    logger.info("data directory copied from %s: new id", path)
            prefs["data_id"], prefs["data_path"] = new, here
            made.append(new)

        current = self.get("data_id")
        if _is_data_id(current) and self.get("data_path") == here:
            return current
        self._update(change)
        return made[0]


# Boolean preferences and their defaults: the tray or menu bar icon,
# the hand-off prompt before the classic UI opens, and whether the first toggle's explanation was shown
# What the scan consent covers: 1 the game file checks; 2 also Steam's records (install location, update status, file
# lists), for finding the game folder, the Steam watch and the official files
SCAN_CONSENT_SCOPE = 2

FLAGS = {"tray": False, "classic_prompt": True, "mods_explained": False, "startup_update": True}
DATA_ID_LENGTH = 12  # hex digits


def data_path(data_dir: Path) -> str:
    """Return a data directory's path as ``data_path`` records it: resolved, and case-folded on Windows.

    Args:
        data_dir: The data directory.
    """
    return os.path.normcase(str(Path(data_dir).resolve()))


def _is_data_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) == DATA_ID_LENGTH and all(c in "0123456789abcdef" for c in value)


def _holds_id(folder: Path, data_id: str) -> bool:
    """Whether a data directory still exists and its webui.json holds this id (the folder was copied, not moved).

    An unreadable webui.json answers from its previous copy.
    """
    for name in ("webui.json", "webui.json.prev"):
        try:
            data = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        prefs = data.get("prefs") if isinstance(data, dict) else None
        return isinstance(prefs, dict) and prefs.get("data_id") == data_id
    return False


# The memorial lobby's fullscreen zoom-out limit (web/js/memorial-lobby/memorial-lobby.js zoomBounds): the game's
# view, the art every clip
# keeps painted (Full Art), the box of the scene's art over its poses (Unlocked), or a custom number of game views
# across.
MEMORIAL_LOBBY_ZOOM_MODES = ("gameView", "fullArt", "unlocked", "custom")
MEMORIAL_LOBBY_ZOOM_CUSTOM = (
    1.0,
    4.0,
)  # the custom limit's range; memorial-lobby.js never zooms out further (ZOOM_OUT_MAX)
MEMORIAL_LOBBY_ZOOM_CUSTOM_DEFAULT = 2.0


def is_number(value: Any) -> bool:
    """Return whether a value is a finite int or float; a bool is not."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def memorial_lobby_zoom(mode: Any, custom: Any) -> dict[str, Any]:
    """Make the memorial lobby's zoom-out setting valid, as it is stored.

    Args:
        mode: One of ``MEMORIAL_LOBBY_ZOOM_MODES``. An invalid mode uses Full Art.
        custom: The saved custom limit, in game views across; a number is rounded to 0.1 and kept within
            ``MEMORIAL_LOBBY_ZOOM_CUSTOM``. An invalid limit uses the default.

    Returns:
        ``mode`` and ``custom``.
    """
    lo, hi = MEMORIAL_LOBBY_ZOOM_CUSTOM
    return {
        "mode": mode if isinstance(mode, str) and mode in MEMORIAL_LOBBY_ZOOM_MODES else "fullArt",
        "custom": round(min(hi, max(lo, float(custom))), 1)
        if is_number(custom)
        else MEMORIAL_LOBBY_ZOOM_CUSTOM_DEFAULT,
    }
