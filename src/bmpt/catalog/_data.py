"""Character map ingestion shared by the runtime and offline generator."""

from __future__ import annotations

import csv
from pathlib import Path

from ._names import csv_bases, csv_names


def read_character_map(path: Path) -> list[dict[str, str]]:
    """Read nonempty file-id rows, including BOM-prefixed downloads.

    Args:
        path: Character-map CSV whose fields are stripped and missing cell values become empty strings.
    """
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [
            {key: (value or "").strip() for key, value in row.items() if key}
            for row in csv.DictReader(stream)
            if (row.get("file_id") or "").strip()
        ]


def csv_entry(row: dict[str, str]) -> dict:
    """Build conservative metadata for a file id absent from the shipped data.

    Args:
        row: Character-map CSV record with a file_id and optional localized names and student metadata.
    """
    bases = csv_bases(row)
    names, fallbacks = csv_names(row, bases)
    return {
        "file_id": row["file_id"],
        "student_id": row.get("student_id") or row["file_id"].lower(),
        "page_id": row.get("page_id"),
        "names": names,
        "fallbacks": fallbacks,
        "name_sources": {lang: "fallback" if lang in fallbacks else "derived" for lang in names},
        "playable": False,
        "order": 0,
        "background": None,
        "official_thumbnail": None,
        "school": row.get("school_name") or None,
        "kind": "npc",
        "spine_remark": row.get("spine_remark", ""),
    }


def base_rank(entry: dict) -> tuple:
    """Rank unskinned, playable alts ahead of story-only representations.

    Args:
        entry: Alt metadata with localized short labels, playability, and display order.

    Returns:
        A sorting key preferring base labels, then playable entries, then lower display order.
    """
    from ._names import BASE_SHORT

    original = all(name.get("short") == BASE_SHORT[lang] for lang, name in entry["names"].items())
    return (not original, not entry["playable"], entry["order"], entry["file_id"].lower())
