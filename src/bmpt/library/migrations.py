"""Metadata upgrades for older mod libraries."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from .judge import core_of, kind_of_file, seconds
from .storage import atomic_json

LEGACY_REMAP = "legacy_remapped_v020"
# Legacy generated labels must be recognized independently of the current UI language.
_REMAP_SUFFIXES = (
    " (remapped)",
    "\uff08\u5df2\u91cd\u6392\uff09",
    "\uff08\u518d\u914d\u7f6e\u6e08\u307f\uff09",
    " (\uc7ac\ubc30\uce58 \uc644\ub8cc)",
)


def legacy_remapped(records: dict[str, dict]) -> dict[str, dict]:
    """Recognize legacy generated labels and link only an unambiguous source.

    Records without a source ID use current official bundle names for their output,
    so dates, CRCs, part counts and bytes can all differ from the source. The stored
    label, target, kind and bundle core are the remaining provenance. An unknown
    source gets a distinct dangling repair reference, keeping its own count and tag.

    Args:
        records: Mod records indexed by ID, including legacy generated copies.

    Returns:
        A deep copy with repair provenance linked and generated label suffixes removed.
    """
    records = copy.deepcopy(records)
    groups: dict[tuple, list[dict]] = {}
    keys = {}
    for mod_id, record in records.items():
        cores = {core_of(name) for name in record["files"]}
        if len(cores) != 1 or not all(kind_of_file(name) == record["kind"] for name in record["files"]):
            continue
        key = (record["target"], record["kind"], cores.pop())
        keys[mod_id] = key
        groups.setdefault((record["label"], *key), []).append(record)

    repairs = {}
    for mod_id, record in records.items():
        if mod_id not in keys or "user_added" in record or record.get("repaired_from"):
            continue
        name = next(
            (record["label"][: -len(suffix)] for suffix in _REMAP_SUFFIXES if record["label"].endswith(suffix)), ""
        )
        if not name:
            continue
        added = seconds(record["added"])
        sources = [
            source
            for source in groups.get((name, *keys[mod_id]), [])
            if source["id"] != mod_id
            and added is not None
            and (at := seconds(source["added"])) is not None
            and at <= added
        ]
        repairs[mod_id] = (name, sources[0]["id"] if len(sources) == 1 else f"legacy-remap:{mod_id}")

    for mod_id, (name, source_id) in repairs.items():
        # Chained repairs share the first source's count.
        seen = {mod_id}
        while source_id in repairs and source_id not in seen:
            seen.add(source_id)
            name, source_id = repairs[source_id]
        record = records[mod_id]
        record["label"], record["repaired_from"] = name, source_id
        if source_id in records:
            source = records[source_id]
            alias = source.get("alias")
            record["label"] = alias if isinstance(alias, str) and alias else source["label"]
            source.setdefault("repaired_id", mod_id)
    return records


logger = logging.getLogger(__name__)

API_NOUNS = "api_nouns"
_API_FIELDS = {
    "variant": "alt",
    "variant_key": "alt_id",
    "variantKey": "altId",
    "variant_keys": "alt_ids",
    "variants": "alts",
    "family": "student",
    "families": "students",
    "expression": "face",
    "expressions": "faces",
    "next_by_variant": "next_by_alt",
}


def _values(value):
    return value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()


def _source(value):
    return "alt:" + value[8:] if isinstance(value, str) and value.startswith("variant:") else value


def _fields(row):
    if not isinstance(row, dict):
        return
    for old, new in _API_FIELDS.items():
        if old in row:
            if old == "next_by_variant" and isinstance(row[old], dict) and isinstance(row.get(new), dict):
                counters = {**row[new]}
                for key, value in row[old].items():
                    current = counters.get(key, 1)
                    if type(current) is not int or type(value) is not int or min(current, value) < 1:
                        raise ValueError("Invalid photograph counters")
                    counters[key] = max(current, value)
                row[new] = counters
                del row[old]
            else:
                # Both names appear when an older build rewrote a migrated file of a shared library: the old field is
                # what that build wrote last, so it wins.
                row[new] = row.pop(old)
    for key in ("source", "target", "optimized_from", "base", "carried", "applied", "configured"):
        if key in row:
            row[key] = _source(row[key])
    error = row.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            for old, new in (("variant", "alt"), ("family", "student"), ("expression", "face")):
                code = code.replace(old, new)
            error["code"] = code
        _fields(error.get("params"))


def _record(record):
    if not isinstance(record, dict):
        return
    _fields(record)
    _fields(record.get("lobby_camera"))
    photography = record.get("roster_photography")
    if isinstance(photography, dict):
        for choice in _values(photography.get("choices")):
            _fields(choice)


def migrate_state_names(data: dict) -> bool:
    """Update stored library records and applied sources when API noun fields are present.

    Args:
        data: Mutable library state document; unrelated fields and identity-map keys are preserved.

    Returns:
        Whether any stored field or source needed migration, independently of migration markers.
    """
    before = copy.deepcopy(data)
    for record in _values(data.get("mods")):
        _record(record)
    for entry in _values(data.get("applied")):
        _fields(entry)
    return data != before


def migrate_profile_names(data: dict) -> bool:
    """Update configured choices, built sources and troubleshooting snapshots in place.

    Args:
        data: Mutable profiles document, including any saved troubleshooting configuration.

    Returns:
        Whether fields or source prefixes changed.
    """
    before = copy.deepcopy(data)
    for folder in _values(data.get("folders")):
        if not isinstance(folder, dict):
            continue
        for table in ("configured", "built"):
            for entry in _values(folder.get(table)):
                _fields(entry)
    for entry in _values(data.get("unfinished")):
        _fields(entry)
    saved = data.get("troubleshoot")
    if isinstance(saved, dict):
        for folder in _values(saved.get("folders")):
            for entry in _values(folder):
                _fields(entry)
    return data != before


def migrate_photo_names(data: dict) -> bool:
    """Update photograph provenance and per-alt counters in place.

    Args:
        data: Mutable photograph manifest, with selections and photo IDs kept intact.

    Returns:
        Whether fields or source prefixes changed.
    """
    before = copy.deepcopy(data)
    _fields(data)
    for photo in _values(data.get("photos")):
        _fields(photo)
    return data != before


def migrate_ui_names(data: dict) -> bool:
    """Update hall entries and saved preview sources without changing alias identity maps.

    Args:
        data: Mutable web UI state document.

    Returns:
        Whether fields or source prefixes changed.
    """
    before = copy.deepcopy(data)
    for folder in _values(data.get("asset_previews")):
        for choices in _values(folder):
            if isinstance(choices, dict):
                for kind in ("spr", "lobby", "model"):
                    if kind in choices:
                        choices[kind] = _source(choices[kind])
    prefs = data.get("prefs")
    hall = prefs.get("student_hall") if isinstance(prefs, dict) else None
    if isinstance(hall, dict):
        for group in ("portraits", "memorials"):
            for entry in _values(hall.get(group)):
                _fields(entry)
    return data != before


def migrate_journal_names(data: dict, path: Path | None = None) -> bool:
    """Update recovery snapshots before they can restore stored library choices.

    Args:
        data: Mutable import, game-write or asset-deletion journal document.
        path: Journal path to save through the journal's atomic writer, or None to change only the document.

    Returns:
        Whether any snapshot field or source prefix changed.
    """
    before = copy.deepcopy(data)
    _record(data.get("record"))
    for record in _values(data.get("records")):
        _record(record)
    for table in ("applied", "old_applied"):
        for entry in _values(data.get(table)):
            _fields(entry)
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        migrate_profile_names(profiles)
    photo = data.get("photo")
    if isinstance(photo, dict):
        _fields(photo)
        _fields(photo.get("entry"))
    changed = data != before
    if changed and path is not None:
        atomic_json(path, data)
        logger.info("Migrated API names in %s", path)
    return changed
