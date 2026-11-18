"""Offline student list filtered by installed portraits and special Spine slots."""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Iterable, Protocol

from ..core.errors import ServiceError
from bmpt.engine import parse_filename
from ._data import base_rank, csv_entry, read_character_map
from ._names import (
    BASE_SHORT,
    LANGUAGES,
    apply_thai_names,
    cn_aliases,
    cn_base,
    cn_words,
    csv_bases,
    csv_names,
    name_head,
)
from .records import CoreMap, Student, Alt
from .special import SPECIAL_PREFIX, describe

__all__ = ["Catalog", "CoreMap", "Student", "Alt"]
DEFAULT_DATA = Path(__file__).parent / "data" / "catalog.json"
logger = logging.getLogger(__name__)
HOME_SUFFIXES = ("_home", "_home_gl", "_home_teen")
SERVERS = ("japan", "global")  # the keys of the shipped release flags, as Settings.server_region() names them


def _names_list(value: object) -> bool:
    """Whether a catalog field holds a list of non-empty names (the aliases)."""
    return isinstance(value, list) and all(isinstance(name, str) and name.strip() for name in value)


class GameSource(Protocol):
    """Supply installed Spine, collection, and optional chibi assets to the catalog."""

    def spine_cores(self, resource_type: str) -> dict[str, list[Path]]:
        """Return installed cores and their bundle parts.

        Args:
            resource_type: Portrait or Memorial Lobby resource type.
        """
        ...

    def collection_names(self) -> dict[str, list[str]]:
        """Return available background and portrait texture names."""
        ...

    def chibi_cores(self) -> set[str]:
        """Return the lower-case dev names of installed 3D chibi prefabs (optional for a game source)."""
        ...

    def field_chibi_cores(self) -> set[str]:
        """Return the lower-case dev names of field-mode player chibis (optional for a game source)."""
        ...

    def asset_chibi_cores(self, candidates: set[str]) -> set[str]:
        """Return candidates whose own bundles hold a chibi; optional for a game source.

        Args:
            candidates: Lower-case alt dev names lacking character or field prefabs.
        """
        ...


def chibi_core(key: str, aliases: list[str], cores: set[str]) -> str | None:
    """Find an alt's installed 3D chibi core.

    Try its key, the key + '_original' (base alts, e.g. hoshino_original), then the
    dev aliases, then the aliases + '_original'. Dependencies are never matched by name (the CAB index resolves
    them).

    Args:
        key: alt key.
        aliases: Alternative dev names, tried in order after the key and its original form.
        cores: Installed lower-case chibi cores eligible for a slot.

    Returns:
        The first matching core, or None when no candidate is installed.
    """
    aliases = [alias.lower() for alias in aliases]
    candidates = [key, key + "_original", *aliases, *(alias + "_original" for alias in aliases)]
    return next((name for name in candidates if name in cores), None)


class Catalog:
    """Build and cache a thread-safe student list; reload after the game scan changes.

    Returned records are independent copies so RPC callers cannot mutate the
    cached snapshot. Only the supplied game object performs game asset access.
    """

    def __init__(
        self, game: GameSource | None, character_map: Path | None, data: Path = DEFAULT_DATA, server: str = "global"
    ):
        """Configure lazy student list construction from shipped metadata and installed assets.

        Args:
            game: Installed asset source; None produces an empty installed student list.
            character_map: Optional CSV supplement; None or a missing file uses shipped data alone.
            data: Catalog JSON containing student, alt, and release metadata.
            server: Release-flag key, "global" or "japan", used when collection portraits are absent.
        """
        self.game = game
        self.character_map = Path(character_map) if character_map is not None else None
        self.data = Path(data)
        self.server = server  # whose release flags apply when the install has no collection portrait
        self._lock = threading.RLock()
        self._snapshot: tuple[dict[str, Student], dict[str, Alt], dict[str, Alt]] | None = None
        # the shipped data and the character map, parsed once and kept; every use works on a copy (_metadata)
        self._parsed: tuple[dict, dict, set[str]] | None = None

    def reload(self) -> None:
        """Rebuild the snapshot atomically, retaining it if rebuilding fails.

        Parse the shipped data and character map again, replacing the kept parse and
        snapshot only when both succeed.
        """
        with self._lock:
            parsed = self._parse() if self.game is not None or self._parsed is not None else None
            snapshot = self._build(parsed)
            if parsed is not None:
                self._parsed = parsed
            self._snapshot = snapshot

    def students(self) -> list[Student]:
        """Return students with installed portraits or special Spine slots, sorted by key."""
        with self._lock:
            return copy.deepcopy(list(self._get()[0].values()))

    def student(self, key: str) -> Student:
        """Return an independent copy of an installed student.

        Args:
            key: Base alt key, matched without case sensitivity.

        Raises:
            ServiceError: No installed student has this key (catalog_student_not_found).
        """
        with self._lock:
            result = self._get()[0].get(key.lower())
            if result is None:
                raise ServiceError("catalog_student_not_found", key=key)
            return copy.deepcopy(result)

    def assignment_students(self) -> list[Student]:
        """List all shipped and character-map students with only installed asset handles.

        Ownership selection must include unreleased students without fabricating a local install target.
        """
        with self._lock:
            entries, metadata, _ = self._metadata()
            students = copy.deepcopy(self._get()[0])
            installed = self._get()[1]
            for key, entry in entries.items():
                if key in installed:
                    continue
                student = metadata[str(entry["student_id"])]
                student_key = student["base"]
                student = students.setdefault(
                    student_key,
                    Student(student_key, student["names"], student.get("school"), [], list(student.get("aliases", []))),
                )
                student.alts.append(
                    Alt(
                        key,
                        entry["file_id"],
                        student_key,
                        entry["names"],
                        entry["playable"],
                        entry["order"],
                        None,
                        None,
                        None,
                        None,
                        None,
                        page_id=entry.get("page_id"),
                        schale_id=entry.get("schale_id"),
                        aliases=list(entry.get("aliases", [])),
                    )
                )
            for student in students.values():
                student.alts.sort(
                    key=lambda alt: (
                        alt.key != student.key,
                        not alt.released_on_server,
                        not alt.playable,
                        alt.order,
                        alt.key,
                    )
                )
            return [students[key] for key in sorted(students)]

    def assignment_alt(self, key: str) -> Alt:
        """Resolve an ownership label independently of local asset availability.

        Args:
            key: alt key.

        Raises:
            ServiceError: The shipped data and character map have no such alt.
        """
        if isinstance(key, str):
            for student in self.assignment_students():
                for alt in student.alts:
                    if alt.key == key.lower():
                        return alt
        raise ServiceError("catalog_alt_not_found", key=key)

    def alt(self, key: str) -> Alt:
        """Return an independent copy of an installed alt.

        Args:
            key: alt key.

        Raises:
            ServiceError: The alt is absent from the installed student list (catalog_alt_not_found).
        """
        with self._lock:
            result = self._get()[1].get(key.lower())
            if result is None:
                raise ServiceError("catalog_alt_not_found", key=key)
            return copy.deepcopy(result)

    def alt_for_core(self, core: str) -> Alt | None:
        """Resolve an installed portrait or memorial lobby core, ignoring letter case.

        Args:
            core: Bundle core to look up in the installed snapshot.

        Returns:
            An independent alt record, or None when the core is unknown.
        """
        with self._lock:
            return copy.deepcopy(self._get()[2].get(core.lower()))

    def alt_for_asset_core(self, core: str) -> Alt | None:
        """Resolve installed bundle cores and internal Spine names to alts.

        Assets can retain a dev name or character ID while their installed bundle uses
        the alt's file ID. Direct file IDs take precedence over shared dev aliases.

        Args:
            core: Bundle core or internal skeleton name ending in _spr or a memorial lobby suffix.

        Returns:
            The installed alt, or None for an unknown or ambiguous alias.
        """
        with self._lock:
            _, alts, cores = self._get()
            lower = core.lower()
            if lower in cores:
                return copy.deepcopy(cores[lower])
            suffix = next(
                (s for s in sorted(("_spr", *HOME_SUFFIXES), key=len, reverse=True) if lower.endswith(s)), None
            )
            if suffix is None:
                return None
            stem = lower[: -len(suffix)]
            if stem in cores and cores[stem].spine_category:
                return copy.deepcopy(cores[stem])
            entries, _, _ = self._metadata()
            if stem in entries:
                return copy.deepcopy(alts.get(stem))
            matches = [
                alts[key]
                for key, entry in entries.items()
                if key in alts and stem in (alias.lower() for alias in entry.get("core_aliases", []))
            ]
            return copy.deepcopy(matches[0]) if len(matches) == 1 else None

    def chibi_map(self, cores: Iterable[str]) -> dict[str, str]:
        """Map chibi dev names to alt keys, independently of the selected install's portraits.

        Args:
            cores: Chibi core names to match, ignoring case; direct file IDs take precedence over aliases.
        """
        available = {core.lower() for core in cores}
        with self._lock:
            entries, _, _ = self._metadata()
            for key in self._get()[1]:
                if not key.startswith(SPECIAL_PREFIX):
                    entries.setdefault(key, {})
            result = {}
            for key, entry in entries.items():
                aliases = [
                    alias.lower()
                    for alias in entry.get("core_aliases", [])
                    if alias.lower() not in entries or alias.lower() == key
                ]
                # Direct file IDs take precedence over shared dev aliases.
                for core in [key, key + "_original", *aliases, *(alias + "_original" for alias in aliases)]:
                    if core in available:
                        result.setdefault(core, key)
            return result

    def alt_for_chibi_core(self, core: str) -> Alt | None:
        """Resolve chibi filenames and internal dev names to an installed alt.

        Args:
            core: Parsed chibi core or internal dev name, matched without case sensitivity.

        Returns:
            The installed alt, or None when the name has no installed owner.
        """
        key = self.chibi_map([core]).get(core.lower())
        with self._lock:
            return copy.deepcopy(self._get()[1].get(key)) if key else None

    def core_map(
        self, portrait_cores: Iterable[str], memorial_lobby_cores: Iterable[str], special_cores: Iterable[str] = ()
    ) -> CoreMap:
        """Map any install's portrait and memorial lobby cores by the student list's rules.

        The snapshot maps only the selected install. A portrait core's key is the core less "_spr"; a memorial lobby
        core belongs to the installed alt whose key or core alias plus a memorial lobby suffix names it, a direct
        file-id
        match first. A memorial lobby core with no portrait of its alt in that install takes the alt the data names
        for it the same way, else the core less its suffix. Each call works on a copy of the kept parse, so one
        install's unknown portrait keys never enter another install's map. Backgrounds, nonstandard Spine cores
        and explicit special_cores use separate special:<core> keys.

        Args:
            portrait_cores: Portrait bundle cores present in the install being mapped.
            memorial_lobby_cores: Memorial lobby cores present in that install, including alternate suffixes.
            special_cores: Cores to assign separate special:<core> keys even when they resemble student slots.
        """
        portraits, memorial_lobbies = list(portrait_cores), list(memorial_lobby_cores)
        with self._lock:
            entries, students, added = self._metadata()
            shipped = set(entries)
            keys, homes, _aliases = self._map(entries, students, added, portraits, memorial_lobbies, set(special_cores))
        portrait = {keys[core.lower()]: core for core in sorted(portraits)}
        memorial_lobby = {key: cores[0] for key, cores in homes.items() if cores}
        names = {key: entries[key]["names"] for key in set(keys.values()) if key in shipped}
        names.update(
            {key: describe(core, entries, students)[2] for core, key in keys.items() if key.startswith(SPECIAL_PREFIX)}
        )
        return CoreMap(keys, portrait, memorial_lobby, names)

    def _get(self) -> tuple[dict[str, Student], dict[str, Alt], dict[str, Alt]]:
        if self._snapshot is None:
            self._snapshot = self._build()
        return self._snapshot

    def _metadata(self) -> tuple[dict, dict, set[str]]:
        """Return a copy of the cached parse of the shipped data and character map.

        The data is parsed on first use. A build adds the install's unknown portrait keys to its copy, which must
        not leak into another build or another install's map.
        """
        if self._parsed is None:
            self._parsed = self._parse()
        return copy.deepcopy(self._parsed)

    def _parse(self) -> tuple[dict, dict, set[str]]:
        try:
            data = json.loads(self.data.read_text(encoding="utf-8"))
            entries = {key.lower(): dict(entry) for key, entry in data["alts"].items()}
            shipped_keys = set(entries)
            students = {str(key): dict(student) for key, student in data["students"].items()}
            if any("th" not in record["names"] for record in (*entries.values(), *students.values())):
                apply_thai_names(students, entries)
            for key, entry in entries.items():
                student = students[str(entry["student_id"])]
                if student["base"] not in entries or key != entry["file_id"].lower():
                    raise ValueError("Invalid catalog key")
                if set(entry["names"]) != set(LANGUAGES) or set(student["names"]) != set(LANGUAGES):
                    raise ValueError("Invalid catalog languages")
                for name in entry["names"].values():
                    if not name["full"] or not name["short"]:
                        raise ValueError("Empty catalog name")
                if not isinstance(entry["order"], int) or not isinstance(entry["playable"], bool):
                    raise ValueError("Invalid catalog alt")
                released = entry.get("released_on_server")
                if released is not None and not (
                    isinstance(released, dict)
                    and all(server in SERVERS and isinstance(flag, bool) for server, flag in released.items())
                ):
                    raise ValueError("Invalid catalog release flags")
            for record in (*entries.values(), *students.values()):
                if not _names_list(record.get("aliases", [])):
                    raise ValueError("Invalid catalog aliases")
            shipped_students = set(students)
            if self.character_map is not None and self.character_map.exists():
                rows = read_character_map(self.character_map)
                words = cn_words(rows)
                for row in rows:
                    key = row["file_id"].lower()
                    if key in entries:
                        continue
                    entry = csv_entry(row)
                    sid = str(entry["student_id"])
                    student = students.setdefault(sid, {"names": csv_bases(row), "school": entry["school"]})
                    entry["names"], entry["fallbacks"] = csv_names(row, student["names"])
                    base, server = student["names"]["zh-CN"], cn_base(row)
                    # a new student takes the CN server's name of its rows named by the base alone
                    if (
                        sid not in shipped_students
                        and server
                        and server != base
                        and name_head(entry["names"]["zh-CN"]["full"]) == base
                    ):
                        student["aliases"] = sorted({*student.get("aliases", []), server})
                    entry["aliases"] = cn_aliases(
                        entry["names"]["zh-CN"]["full"], base, student.get("aliases", []), words, [server]
                    )
                    entry["order"] = 1 + max(
                        (e["order"] for e in entries.values() if str(e["student_id"]) == sid), default=-1
                    )
                    entries[key] = entry
                    if sid not in shipped_students and (
                        "base" not in student or base_rank(entry) < base_rank(entries[student["base"]])
                    ):
                        student["base"] = key
            return entries, students, set(entries) - shipped_keys
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.exception("Unable to read catalog metadata")
            raise ServiceError("catalog_data_invalid", path=str(self.data)) from exc

    @staticmethod
    def _map(
        entries: dict,
        students: dict,
        added: set[str],
        portraits: list[str],
        memorial_lobbies: list[str],
        special_cores: set[str] | None = None,
    ) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
        """Apply the student list's core rules to one install's core names.

        Return {lower-case core: alt key}, each key's memorial lobby cores in the install (the primary one first) and
        each portrait key's aliases (its key, then the core aliases the rule kept). Unknown portrait keys are added
        to ``entries``, ``students`` and ``added``, as the student list lists them, in the order the alias rule reads
        them.
        """
        special_cores = {core.lower() for core in special_cores or ()}
        memorial_lobby_names = {core.lower(): core for core in memorial_lobbies if core.lower() not in special_cores}
        keys: dict[str, str] = {}
        homes_of: dict[str, list[str]] = {}
        aliases_of: dict[str, list[str]] = {}
        for core in sorted(portraits):
            key = core.lower().removesuffix("_spr")
            if key not in entries:
                added.add(key)
                file_id = core[:-4] if core.lower().endswith("_spr") else core
                entries[key] = {
                    "file_id": file_id,
                    "student_id": f"unknown:{key}",
                    "names": {lang: {"full": file_id, "short": BASE_SHORT[lang]} for lang in LANGUAGES},
                    "playable": False,
                    "order": 0,
                    "school": None,
                }
                students[f"unknown:{key}"] = {
                    "base": key,
                    "names": {lang: file_id for lang in LANGUAGES},
                    "school": None,
                }
            entry = entries[key]
            aliases = [
                key,
                *(
                    alias
                    for alias in entry.get("core_aliases", [])
                    if alias.lower() not in entries or alias.lower() == key
                ),
            ]
            homes = [
                memorial_lobby_names[alias.lower() + suffix]
                for alias in aliases
                for suffix in HOME_SUFFIXES
                if alias.lower() + suffix in memorial_lobby_names
            ]
            aliases_of[key], homes_of[key] = aliases, homes
            keys[core.lower()] = key
            for home in homes:
                # A direct file-id match takes precedence over a shared dev alias.
                if home.lower() in (key + suffix for suffix in HOME_SUFFIXES):
                    keys[home.lower()] = key
                else:
                    keys.setdefault(home.lower(), key)
        # memorial lobby cores of alts without a portrait in this install: the alt the data names for them (its key,
        # else a core alias), else the core less its suffix, so a core gets one key in every install
        aliased: dict[str, str] = {}
        for key, entry in entries.items():
            for alias in entry.get("core_aliases", []):
                if alias.lower() not in entries or alias.lower() == key:
                    aliased.setdefault(alias.lower(), key)
        suffixes = sorted(HOME_SUFFIXES, key=len, reverse=True)
        memorial_lobby_only: dict[str, list[tuple[bool, int, str]]] = {}
        for lower, core in sorted(memorial_lobby_names.items()):
            suffix = next((s for s in suffixes if lower.endswith(s)), None)
            if lower in keys or suffix is None:
                continue
            stem = lower[: -len(suffix)]
            key = stem if stem in entries else aliased.get(stem, stem)
            keys[lower] = key
            memorial_lobby_only.setdefault(key, []).append((stem != key, HOME_SUFFIXES.index(suffix), core))
        for key, found in memorial_lobby_only.items():
            homes_of.setdefault(key, []).extend(core for *_, core in sorted(found))
        for core in memorial_lobbies:
            lower = core.lower()
            if lower in special_cores or not lower.endswith(HOME_SUFFIXES):
                key = SPECIAL_PREFIX + lower
                keys[lower], homes_of[key] = key, [core]
        return keys, homes_of, aliases_of

    def _build(
        self, parsed: tuple[dict, dict, set[str]] | None = None
    ) -> tuple[dict[str, Student], dict[str, Alt], dict[str, Alt]]:
        if self.game is None:
            return {}, {}, {}
        entries, metadata, added = copy.deepcopy(parsed) if parsed is not None else self._metadata()
        portraits = self.game.spine_cores("portrait")
        memorial_lobbies = self.game.spine_cores("memorialLobby")
        art = {kind: {name.lower(): name for name in names} for kind, names in self.game.collection_names().items()}
        collection = art.get("portrait", {})
        if portraits and not collection:
            logger.warning("No collection portraits found; recruitable follows the shipped release flags")
        chibis = getattr(self.game, "chibi_cores", set)()
        fields = getattr(self.game, "field_chibi_cores", set)()
        # the same mapping core_map gives any install, run on the core lists fetched once for this build
        special_cores = {
            core
            for core, paths in memorial_lobbies.items()
            if paths and parse_filename(paths[0].name).category != "spinelobbies"
        }
        keys, homes_of, aliases_of = self._map(
            entries, metadata, added, list(portraits), list(memorial_lobbies), special_cores
        )
        students, alts, cores = {}, {}, {}
        unmodelled: dict[str, list[str]] = {}  # alts with neither a character nor a field chibi: key -> aliases
        for core, paths in sorted(portraits.items()):
            key = keys[core.lower()]
            entry = entries[key]
            student = metadata[str(entry["student_id"])]
            if key in added:
                portrait = collection.get(f"student_portrait_{key}_collection")
                if portrait:
                    entry.update(playable=True, official_thumbnail=portrait)
                entry["background"] = entries[student["base"]].get("background")
            student_key = student["base"]
            student = students.setdefault(
                student_key,
                Student(student_key, student["names"], student.get("school"), [], list(student.get("aliases", []))),
            )
            aliases, homes = aliases_of[key], homes_of[key]
            dates = [parse_filename(path.name).date for path in paths]
            portrait = collection.get((entry.get("official_thumbnail") or "").lower())
            alt = Alt(
                key,
                entry["file_id"],
                student_key,
                entry["names"],
                entry["playable"],
                entry["order"],
                core,
                next(iter(homes), None),
                art.get("background", {}).get((entry.get("background") or "").lower()),
                portrait,
                max(filter(None, dates), default=None),
                entry.get("page_id") or None,
                released_on_server=self._released_on_server(entry, portrait is not None, bool(collection)),
                chibi=chibi_core(key, aliases[1:], chibis),
                schale_id=entry.get("schale_id"),
                aliases=list(entry.get("aliases", [])),
            )
            if alt.chibi is None:
                # an event or story alt with only a field-mode chibi: shown and offered as a costume, no slot
                alt.field_chibi = chibi_core(key, aliases[1:], fields)
                if alt.field_chibi is None:
                    unmodelled[key] = aliases[1:]
            student.alts.append(alt)
            alts[key] = alt
        if unmodelled and hasattr(self.game, "asset_chibi_cores"):
            # a story-only or UI-only character's chibi in its own bundles (a cutscene's NP0206, Arona), read for
            # these alts only. Those bundles are the alt's chibi slot: a mod of them applies there,
            # unlike a field-mode chibi, which shares its bundles with other students' and has no slot
            names = {
                name.lower()
                for key, aliases in unmodelled.items()
                for name in (key, key + "_original", *aliases, *(alias + "_original" for alias in aliases))
            }
            owned = self.game.asset_chibi_cores(names)
            for key, aliases in unmodelled.items():
                alts[key].chibi = chibi_core(key, aliases, owned)
        for core, paths in sorted(memorial_lobbies.items()):
            key = keys.get(core.lower(), "")
            if not key.startswith(SPECIAL_PREFIX):
                continue
            student_key, base, names = describe(core, entries, metadata)
            student = students.setdefault(student_key, Student(student_key, base, None, []))
            dates = [parse_filename(path.name).date for path in paths]
            category = parse_filename(paths[0].name).category if paths else "spinebackground"
            alt = Alt(
                key,
                core,
                student_key,
                names,
                False,
                len(student.alts),
                None,
                core,
                None,
                None,
                max(filter(None, dates), default=None),
                spine_category=category,
            )
            student.alts.append(alt)
            alts[key] = alt
        cores = {lower: alts[key] for lower, key in keys.items() if key in alts}
        for alt in alts.values():
            if alt.chibi:
                cores.setdefault(alt.chibi.lower(), alt)
        for student in students.values():
            # recruitable here first, then playable on another server, then story and NPC alts
            student.alts.sort(
                key=lambda alt: (
                    alt.key != student.key,
                    not alt.released_on_server,
                    not alt.playable,
                    alt.order,
                    alt.key,
                )
            )
        return dict(sorted(students.items())), alts, cores

    def _released_on_server(self, entry: dict, installed: bool, indexed: bool) -> bool:
        """Whether a playable alt can be recruited on the selected server.

        Each server builds its collection bundles with the students it has released, so an installed collection
        portrait decides. Without one, the shipped flag tells a portrait-name gap (True) from a student not on this
        server yet (False); with no flag either, the install decides unless it has no collection index at all.
        """
        if not entry["playable"]:
            return False
        if installed:
            return True
        flag = (entry.get("released_on_server") or {}).get(self.server)
        return flag if flag is not None else not indexed
