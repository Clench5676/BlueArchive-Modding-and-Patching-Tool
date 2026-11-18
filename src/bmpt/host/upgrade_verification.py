"""Verify library files, references, and pending journals without replaying writes.

Recovery uses recorded digests to prove the next operation. An ambiguous outcome remains Recovery Pending with its
journal, payloads, and game files preserved.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import upgrade_files as files
from .store_formats import RESOURCE_TYPES, InvalidStore, load_document
from .upgrade_journals import RecoveryPending, bundle_name, verify_journals

_ID = re.compile(r"[0-9a-f]{32}")
_SHA1 = re.compile(r"[a-f0-9]{40}")


def _verify_native(store: str, path: Path, root: Path) -> None:
    document = load_document(store, path)
    if store == "A":
        if not isinstance(document.get("parts"), list) or not isinstance(document.get("files"), dict):
            raise InvalidStore(f"Invalid build fingerprint: {path}")
        for name, sha1 in document["files"].items():
            if not bundle_name(name) or not isinstance(sha1, str) or not _SHA1.fullmatch(sha1):
                raise InvalidStore(f"Invalid build fingerprint: {path}")
            payload = path.parent / name
            if files.present(payload) and files.digest(payload, "sha1") != sha1:
                # A stale fingerprint remains an unavailable historical baseline.
                continue
    elif store == "R":
        rigs = document.get("rigs")
        if not isinstance(rigs, dict) or not isinstance(document.get("stamps"), dict):
            raise InvalidStore(f"Invalid rig archive: {path}")
        for rows in rigs.values():
            if not isinstance(rows, list):
                raise InvalidStore(f"Invalid rig archive rows: {path}")
            for rig in rows:
                for field in ("atlasSha1", "skeletonSha1"):
                    sha1 = rig[field]
                    if (
                        not isinstance(sha1, str)
                        or not _SHA1.fullmatch(sha1)
                        or files.digest(root / "rigs" / "objects" / sha1, "sha1") != sha1
                    ):
                        raise InvalidStore(f"Invalid referenced rig object: {path}")


def verify_library(root: Path, *, check_game: bool) -> dict[str, int]:
    """Verify native payloads, state references, and the authority of any pending journals."""
    state_path = root / "state.json"
    state = load_document("S", state_path) if files.present(state_path) else {}
    records = {}
    held = 0
    for row in state.get("mods", ()):
        if not isinstance(row, dict):
            held += 1
            continue
        key, names = row.get("id"), row.get("files")
        if not isinstance(key, str) or not _ID.fullmatch(key) or not isinstance(names, list) or not names:
            held += 1
            continue
        if (
            any(not bundle_name(name) for name in names)
            or row.get("resource_type") not in RESOURCE_TYPES.values()
            or not all(isinstance(row.get(field), str) for field in ("name", "added"))
            or not isinstance(row.get("target_alt", row.get("generic_target")), str)
            or type(row.get("seen")) is not bool
        ):
            logging.getLogger(__name__).warning("Mod %s has an unusable record", key)
            held += 1
            continue
        if key in records:
            raise InvalidStore(f"Duplicate Mod record: {key}")
        records[key] = row
        if any(not files.present(root / "mods" / key / name) for name in names):
            logging.getLogger(__name__).warning("Mod %s has missing managed files", key)
            held += 1
    try:
        verify_journals(root, records, check_game=check_game)
    except RecoveryPending as error:
        if check_game:
            raise
        logging.getLogger(__name__).warning("Recovery remains pending: %s", error)
    thumbnails_path = root / "thumbnails" / "index.json"
    if files.present(thumbnails_path):
        from PIL import Image

        thumbnails = load_document("F", thumbnails_path)
        for key, entry in thumbnails["thumbnails"].items():
            if not isinstance(entry, dict) or not _ID.fullmatch(key) or entry.get("id") != key:
                continue
            png = root / "thumbnails" / "images" / f"{key}.png"
            if not files.present(png):
                continue
            if files.digest(png) != entry.get("sha256"):
                raise InvalidStore(f"Thumbnail digest differs: {png}")
            with Image.open(png) as image:
                if image.format != "PNG" or image.size != (entry.get("width"), entry.get("height")):
                    raise InvalidStore(f"Thumbnail dimensions differ: {png}")
                image.verify()
    official_path = root / "originals.json"
    if files.present(official_path):
        for name, entry in load_document("O", official_path)["officials"].items():
            if (
                not bundle_name(name)
                or not isinstance(entry, dict)
                or entry.get("by") not in ("steam", "crc", "failed")
            ):
                raise InvalidStore(f"Invalid kept official verification: {name}")
            original = root / "originals" / name
            if (
                files.present(original)
                and entry["by"] != "failed"
                and (
                    files.owned_stat(original).st_size != entry.get("size")
                    or files.digest(original, "sha1") != entry.get("sha1")
                )
            ):
                logging.getLogger(__name__).warning("Kept official verification no longer applies to %s", name)
    rig_index = root / "rigs" / "index.json"
    if files.present(rig_index):
        _verify_native("R", rig_index, root)
    for path in files.tree_files(root / "mods"):
        if path.name == "built-against.json":
            _verify_native("A", path, root)
    return {
        "liveMods": len(state.get("mods", ())) - held,
        "heldMods": held,
        "previousVersions": len(list((root / "mods").glob("*/previous/*"))),
    }
