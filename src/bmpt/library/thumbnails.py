"""Persistent, lossless student thumbnails, independent of disposable preview caches."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from ..core import devmode
from ..host.store_formats import epoch_root, read_runtime
from ..core.errors import ServiceError
from ..core.filelock import file_lock
from .storage import atomic_json, sync_directory

logger = logging.getLogger(__name__)

_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_ENCODED = 48 * 1024 * 1024
MAX_DIMENSION = 4096


def decode_capture(value: str) -> tuple[bytes, int, int]:
    """Validate a bounded PNG without changing its original encoded bytes.

    Args:
        value: PNG base64 data URL, limited to MAX_ENCODED characters, RGB/RGBA mode,
            and dimensions from 64 through MAX_DIMENSION pixels on each side.

    Returns:
        Original PNG bytes, width, and height in pixels.

    Raises:
        ServiceError: The encoding, dimensions, mode, or PNG payload is invalid (thumbnail_invalid).
    """
    if not isinstance(value, str) or len(value) > MAX_ENCODED or not value.startswith("data:image/png;base64,"):
        raise ServiceError("thumbnail_invalid")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if (
                image.format != "PNG"
                or image.mode not in ("RGB", "RGBA")
                or not (64 <= image.width <= MAX_DIMENSION and 64 <= image.height <= MAX_DIMENSION)
            ):
                raise ValueError("Invalid thumbnail dimensions")
            width, height = image.size
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
        raise ServiceError("thumbnail_invalid") from None
    return raw, width, height


def student_of(catalog, key: str) -> str:
    """Resolve an alt's student, including manually assigned alts.

    Args:
        catalog: Character catalog providing standard and assignment-alt lookups.
        key: alt key whose costumes share thumbnails with the same student.
    """
    try:
        return catalog.alt(key).student
    except (KeyError, ServiceError):
        return catalog.assignment_alt(key).student


class _Manifest(dict):
    """The usable part of the manifest.

    ``thumbnails`` and ``selected`` hold only the rows this code can use. The rows it cannot (``held_thumbnails``,
    ``held_selected``, raw JSON) stay out of every use and come back unchanged in every write, at their place
    (``order_thumbnails``, ``order_selected``).
    """

    held_thumbnails: dict
    held_selected: dict
    order_thumbnails: list
    order_selected: list

    @classmethod
    def of(
        cls, data: dict, thumbnails: dict, selected: dict, held_thumbnails: dict, held_selected: dict
    ) -> "_Manifest":
        manifest = cls({**data, "thumbnails": thumbnails, "selected": selected})
        manifest.held_thumbnails, manifest.held_selected = held_thumbnails, held_selected
        manifest.order_thumbnails, manifest.order_selected = list(data["thumbnails"]), list(data["selected"])
        return manifest

    def document(self) -> dict:
        """What the file holds after a write: the usable rows as they are now, the held rows as they were."""

        def merged(order: list, held: dict, rows: dict) -> dict:
            out = {key: rows[key] if key in rows else held[key] for key in order if key in rows or key in held}
            out.update((key, value) for key, value in rows.items() if key not in out)
            return out

        return {
            **self,
            "thumbnails": merged(self.order_thumbnails, self.held_thumbnails, self["thumbnails"]),
            "selected": merged(self.order_selected, self.held_selected, self["selected"]),
        }

    def named(self) -> set[str]:
        """The thumbnail files the held rows name (their keys, ids and the thumbnails held selections point at)."""
        names = set(self.held_thumbnails)
        names.update(
            entry["id"]
            for entry in self.held_thumbnails.values()
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        )
        names.update(key for key in self.held_selected.values() if isinstance(key, str))
        return names


class Thumbnails:
    """Manage a shared thumbnail manifest with validated IDs, digests, and student ownership.

    An unusable record or selection is logged once, excluded from use, and written
    back unchanged. An unreadable manifest blocks all reads and writes and remains untouched.
    """

    def __init__(self, library_root: Path, student_of=None):
        """Locate persistent thumbnail storage and prepare manifest caching and locking.

        Args:
            library_root: Library folder containing the thumbnails directory.
            student_of: Resolve an alt to its student. None uses the alt's own key.
        """
        self.student_of = student_of or (lambda alt: alt)
        self.root = Path(library_root) / ("thumbnails" if epoch_root(Path(library_root), "library") else "photographs")
        devmode.check_write(self.root)
        self.index = self.root / "index.json"
        self._lock = threading.RLock()
        self._cached: _Manifest | None = None
        self._stamp: tuple | None = None
        self._warned: set[tuple] = set()  # held rows and unusable manifests already logged

    def _warn(self, resource_type: str, reason: str, name: str) -> None:
        if (resource_type, reason, name) not in self._warned:  # once per entry, not at every read of the file
            self._warned.add((resource_type, reason, name))
            logger.warning("Thumbnail manifest: %s cannot be used (%s): %.80s", resource_type, reason, name)

    def student(self, alt: str) -> str:
        """Resolve an alt's student, falling back to its own key when unknown."""
        try:
            return self.student_of(alt) or alt
        except (KeyError, ServiceError):
            return alt

    def selection(self, alt: str):
        """Return an alt's thumbnail ID, official choice, or None for its default or stale selection."""
        with self._lock:
            return self._chosen(self._read(), alt)

    def _chosen(self, data: _Manifest, alt: str):
        """Read the alt's selection.

        Returns:
            The selection, or None when the alt has none or it is stale: the thumbnail's student differs from the
            alt's (the catalog changed). A stale selection reads as the default thumbnail and leaves the row as
            it is.
        """
        key = data["selected"].get(alt)
        if key in (None, "official"):
            return key
        if self.student(data["thumbnails"][key]["alt"]) != self.student(alt):
            self._warn("selection", "thumbnail of another student", alt)
            return None
        return key

    def _signature(self) -> tuple | None:
        try:
            stat = self.index.stat()
            return stat.st_mtime_ns, stat.st_size, stat.st_ino
        except FileNotFoundError:
            return None

    def _read(self) -> _Manifest:
        try:
            stamp = self._signature()
            if self._cached is not None and stamp == self._stamp:
                return self._cached
            if self.index.is_symlink():
                raise ValueError("Symlinked thumbnail manifest")
            data = read_runtime(self.index) if stamp else {"version": 2, "thumbnails": {}, "selected": {}, "next": 1}
            # The file as a whole: anything wrong here leaves it untouched and unusable, never repaired or replaced.
            if (
                not isinstance(data, dict)
                or data.get("version") != 2
                or not isinstance(data.get("thumbnails"), dict)
                or not isinstance(data.get("selected"), dict)
                or type(data.get("next")) is not int
                or data["next"] < 1
            ):
                raise ValueError("Invalid thumbnail manifest")
            counters = data.get("next_by_alt", {})
            if not isinstance(counters, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 1 for key, value in counters.items()
            ):
                raise ValueError("Invalid thumbnail counters")
        except (OSError, ValueError, TypeError, RecursionError) as error:
            self._warn("manifest", str(error) if isinstance(error, ValueError) else type(error).__name__, "")
            raise ServiceError("thumbnail_library_invalid") from error
        thumbnails, held_thumbnails = {}, {}
        for key, entry in data["thumbnails"].items():
            reason = self._unusable_thumbnail(key, entry)
            if reason is None:
                thumbnails[key] = entry
            else:
                self._warn("thumbnail record", reason, key if _ID.fullmatch(key) else "no usable id")
                held_thumbnails[key] = entry
        selected, held_selected = {}, {}
        for alt, key in data["selected"].items():
            if key is None or key == "official" or (isinstance(key, str) and key in thumbnails):
                selected[alt] = key
            else:
                self._warn("selection", "no such thumbnail", alt)
                held_selected[alt] = key
        manifest = _Manifest.of(data, thumbnails, selected, held_thumbnails, held_selected)
        self._cached, self._stamp = manifest, stamp
        return manifest

    @staticmethod
    def _unusable_thumbnail(key: str, entry) -> str | None:
        """Why a thumbnail record cannot be used (a word, never a path), None when it can."""
        if not _ID.fullmatch(key):
            return "bad key"
        if not isinstance(entry, dict):
            return "not an object"
        if entry.get("id") != key:
            return "id differs from its key"
        if not isinstance(entry.get("alt"), str):
            return "no alt"
        if not isinstance(entry.get("sha256"), str) or not _DIGEST.fullmatch(entry["sha256"]):
            return "bad digest"
        return None

    def _write(self, data: _Manifest) -> None:
        atomic_json(self.index, data.document() if isinstance(data, _Manifest) else data)
        self._cached = self._stamp = None  # read back: held rows and the file's order come from what is on disk

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not _ID.fullmatch(key):
            raise ServiceError("thumbnail_missing")
        path = self.root / "images" / f"{key}.png"
        if path.is_symlink() or path.parent.is_symlink() or self.root.is_symlink():
            raise ServiceError("thumbnail_invalid")
        return path

    def _entry(self, alt: str, key: str) -> dict:
        self._path(key)
        entry = self._read()["thumbnails"].get(key)
        if entry is None or self.student(entry["alt"]) != self.student(alt):
            raise ServiceError("thumbnail_missing")
        return entry

    @staticmethod
    def url(alt: str, key: str) -> str:
        """Build a thumbnail asset URL scoped to an alt.

        Args:
            alt: alt key escaped into the asset route for student validation.
            key: thumbnail ID identifying the PNG within the library.
        """
        return f"/asset/thumbnails/{quote(alt, safe='')}/{key}.png"

    def list(self, alt: str) -> dict:
        """List an alt's student thumbnails with URLs, provenance, and its app selection."""
        with self._lock:
            data = self._read()
            thumbnails = [
                {
                    **entry,
                    "url": self.url(alt, key),
                    "managed": True,
                    "provenance": "generated" if entry.get("builtin") else "capture",
                }
                for key, entry in data["thumbnails"].items()
                if self.student(entry["alt"]) == self.student(alt)
            ]
            return {"alt": alt, "thumbnails": thumbnails, "app": self._chosen(data, alt)}

    def app_url(self, alt: str) -> str | None:
        """Return an alt's selected thumbnail URL, or None for official, default, or stale choices."""
        with self._lock:
            key = self._chosen(self._read(), alt)
            return self.url(alt, key) if key and key != "official" else None

    def has_selection(self, alt: str) -> bool:
        """Check whether an alt has an explicit usable thumbnail, official, or default selection."""
        with self._lock:
            data = self._read()
            return alt in data["selected"] and (data["selected"][alt] is None or self._chosen(data, alt) is not None)

    def save(
        self,
        alt: str,
        png: str,
        mode: str,
        expression: str = "",
        action: str = "",
        source: str = "",
        *,
        builtin: bool = False,
    ) -> dict:
        """Store a lossless capture and its provenance without selecting it automatically.

        Args:
            alt: alt key owning the capture and its sequence number for the alt.
            png: PNG base64 data URL accepted by decode_capture.
            mode: Capture scene, portrait or memorial lobby.
            expression: Captured expression label, empty if unavailable; at most 500 characters.
            action: Captured animation label, empty if unavailable; at most 500 characters.
            source: Captured source label, empty if unavailable; at most 500 characters.
            builtin: Whether this is a generated default; reuses an existing verified default for the alt.

        Returns:
            The student thumbnail listing with saved set to the stored or reused thumbnail ID.

        Raises:
            ServiceError: The capture or manifest is invalid, or a reused default image is missing.
        """
        if mode not in ("portrait", "memorialLobby") or not all(
            isinstance(value, str) and len(value) <= 500 for value in (alt, expression, action, source)
        ):
            raise ServiceError("thumbnail_invalid")
        raw, width, height = decode_capture(png)
        key = uuid.uuid4().hex
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            before = copy.deepcopy(data)
            counters = data.setdefault("next_by_alt", {})
            number = max(
                counters.get(alt, 1),
                max(
                    (
                        int(item.get("number", 0)) + 1
                        for item in data["thumbnails"].values()
                        if item["alt"] == alt and not item.get("builtin")
                    ),
                    default=1,
                ),
            )
            if builtin:
                existing = next(
                    (item["id"] for item in data["thumbnails"].values() if item["alt"] == alt and item.get("builtin")),
                    None,
                )
                if existing:
                    self.image(alt, existing)
                    return {**self.list(alt), "saved": existing}
            entry = {
                "id": key,
                "number": number if not builtin else 0,
                "alt": alt,
                "mode": mode,
                "expression": expression,
                "action": action,
                "source": source,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "created": datetime.now(timezone.utc).isoformat(),
            }
            if builtin:
                entry["builtin"] = "generated"
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix(".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                sync_directory(path.parent)
                data["thumbnails"][key] = entry
                if alt not in data.held_selected:  # a row kept as it is stays the alt's row
                    data["selected"].setdefault(alt, None)
                if not builtin:
                    counters[alt] = number + 1
                data["next"] += 1
                self._write(data)
            except BaseException:
                temporary.unlink(missing_ok=True)
                # A manifest rename can succeed before its directory flush fails.
                # Restore it before removing the image; retain valid bytes if rollback fails.
                self._cached = None
                if self._read()["thumbnails"].get(key):
                    self._write(before)
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
                raise
        return {**self.list(alt), "saved": key}

    def select(self, alt: str, key: str | None) -> dict:
        """Persist an alt's app-thumbnail choice after verifying any selected thumbnail.

        Args:
            alt: alt key whose thumbnail selection changes.
            key: Thumbnail ID belonging to the same student, official for game art,
                or None for the default thumbnail.

        Returns:
            The student thumbnail listing with the updated selection.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            if key not in (None, "official"):
                self.image(alt, key)
            data["selected"][alt] = key
            self._write(data)
        return self.list(alt)

    def delete(self, alt: str, key: str, protected: set[str] = frozenset()) -> dict:
        """Delete a thumbnail and clear usable selections, preserving protected references.

        Args:
            alt: alt key authorizing access to the thumbnail's student.
            key: Thumbnail ID to remove; official cannot be deleted.
            protected: Thumbnail IDs referenced by managed library assets and therefore retained.

        Returns:
            The remaining student thumbnail listing.

        Raises:
            ServiceError: The thumbnail is official, missing, invalid, or named by a protected or held reference.
        """
        if key == "official":
            raise ServiceError("asset_official")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            self._entry(alt, key)
            # a kept row that names the file keeps it: a held record or selection, or a selection that is stale now
            stale = {
                chosen
                for chosen_for, chosen in data["selected"].items()
                if chosen not in (None, "official") and self._chosen(data, chosen_for) is None
            }
            if key in protected or key in data.named() or key in stale:
                raise ServiceError("thumbnail_in_use")
            before = copy.deepcopy(data)
            raw = self.image(alt, key)
            del data["thumbnails"][key]
            for selected_alt, selected in list(data["selected"].items()):
                if selected == key:
                    data["selected"][selected_alt] = None
            try:
                self._write(data)
                self._path(key).unlink()
                sync_directory(self.root / "images")
            except BaseException:
                path = self._path(key)
                if not path.exists():
                    with path.open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    sync_directory(path.parent)
                self._write(before)
                raise
        return self.list(alt)

    def image(self, alt: str, key: str) -> bytes:
        """Read a thumbnail only after checking its student and SHA-256 digest.

        Args:
            alt: alt key authorizing access to the thumbnail's student.
            key: Thumbnail ID naming the image and its manifest record.

        Raises:
            ServiceError: The thumbnail is missing, belongs to another student, or fails integrity validation.
        """
        with self._lock:
            entry = self._entry(alt, key)
            try:
                raw = self._path(key).read_bytes()
            except FileNotFoundError:
                raise ServiceError("thumbnail_missing") from None
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ServiceError("thumbnail_invalid")
            return raw
