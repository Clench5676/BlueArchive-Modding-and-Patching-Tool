"""Persistent, lossless student photographs, independent of disposable preview caches."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from .. import devmode
from ..errors import ServiceError
from ..filelock import file_lock
from .storage import atomic_json, sync_directory

logger = logging.getLogger(__name__)

_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_ENCODED = 48 * 1024 * 1024
MAX_DIMENSION = 4096


def decode_capture(value: str) -> tuple[bytes, int, int]:
    """Validate a bounded PNG without changing its original encoded bytes."""
    if not isinstance(value, str) or len(value) > MAX_ENCODED or not value.startswith("data:image/png;base64,"):
        raise ServiceError("photo_invalid")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.mode not in ("RGB", "RGBA") or not (
                    64 <= image.width <= MAX_DIMENSION and 64 <= image.height <= MAX_DIMENSION):
                raise ValueError("Invalid photograph dimensions")
            width, height = image.size
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
        raise ServiceError("photo_invalid") from None
    return raw, width, height


def student_family(catalog, key: str) -> str:
    try:
        return catalog.variant(key).family
    except (KeyError, ServiceError):
        return catalog.assignment_variant(key).family


class _Manifest(dict):
    """The usable part of the manifest: ``photos`` and ``selected`` hold only the rows this code can use. The rows it
    cannot (``held_photos``, ``held_selected``, raw JSON) stay out of every use and come back unchanged in every write,
    at their place (``order_photos``, ``order_selected``)."""

    held_photos: dict
    held_selected: dict
    order_photos: list
    order_selected: list

    @classmethod
    def of(cls, data: dict, photos: dict, selected: dict, held_photos: dict, held_selected: dict) -> "_Manifest":
        manifest = cls({**data, "photos": photos, "selected": selected})
        manifest.held_photos, manifest.held_selected = held_photos, held_selected
        manifest.order_photos, manifest.order_selected = list(data["photos"]), list(data["selected"])
        return manifest

    def document(self) -> dict:
        """What the file holds after a write: the usable rows as they are now, the held rows as they were."""
        def merged(order: list, held: dict, rows: dict) -> dict:
            out = {key: rows[key] if key in rows else held[key] for key in order if key in rows or key in held}
            out.update((key, value) for key, value in rows.items() if key not in out)
            return out
        return {**self, "photos": merged(self.order_photos, self.held_photos, self["photos"]),
                "selected": merged(self.order_selected, self.held_selected, self["selected"])}

    def named(self) -> set[str]:
        """The photo files the held rows name (their keys, their ids and the photos held selections point at)."""
        names = set(self.held_photos)
        names.update(entry["id"] for entry in self.held_photos.values()
                     if isinstance(entry, dict) and isinstance(entry.get("id"), str))
        names.update(key for key in self.held_selected.values() if isinstance(key, str))
        return names


class Photographs:
    """One shared library manifest; IDs, digests and per-character ownership are validated on every read. A record
    or a selection that cannot be used costs that entry only (it is logged once, left out of every use and written
    back unchanged); a manifest that cannot be read as a whole makes every read and write refuse and is never touched."""

    def __init__(self, library_root: Path, family_of=None):
        self.family_of = family_of or (lambda variant: variant)
        self.root = Path(library_root) / "photographs"
        devmode.check_write(self.root)
        self.index = self.root / "index.json"
        self._lock = threading.RLock()
        self._cached: _Manifest | None = None
        self._stamp: tuple | None = None
        self._warned: set[tuple] = set()  # held rows and unusable manifests already logged

    def _warn(self, kind: str, reason: str, name: str) -> None:
        if (kind, reason, name) not in self._warned:  # once per entry, not at every read of the file
            self._warned.add((kind, reason, name))
            logger.warning("Photograph manifest: %s cannot be used (%s): %.80s", kind, reason, name)

    def family(self, variant: str) -> str:
        try:
            return self.family_of(variant) or variant
        except (KeyError, ServiceError):
            return variant

    def selection(self, variant: str):
        with self._lock:
            return self._chosen(self._read(), variant)

    def _chosen(self, data: _Manifest, variant: str):
        """The variant's selection, None when it has none or it is stale: the photo's family is no longer the
        variant's (the catalog changed), which reads as the default thumbnail and leaves the row as it is."""
        key = data["selected"].get(variant)
        if key in (None, "official"):
            return key
        if self.family(data["photos"][key]["variant"]) != self.family(variant):
            self._warn("selection", "photo of another family", variant)
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
                raise ValueError("Symlinked photograph manifest")
            data = json.loads(self.index.read_text(encoding="utf-8")) if stamp else {
                "version": 1, "photos": {}, "selected": {}, "next": 1}
            # The file as a whole: anything wrong here leaves it untouched and unusable, never repaired or replaced.
            if (not isinstance(data, dict) or data.get("version") != 1 or
                    not isinstance(data.get("photos"), dict) or not isinstance(data.get("selected"), dict) or
                    type(data.get("next")) is not int or data["next"] < 1):
                raise ValueError("Invalid photograph manifest")
            counters = data.get("next_by_variant", {})
            if not isinstance(counters, dict) or any(not isinstance(key, str) or type(value) is not int or value < 1
                                                     for key, value in counters.items()):
                raise ValueError("Invalid photograph counters")
        except (OSError, ValueError, TypeError, RecursionError) as error:
            self._warn("manifest", str(error) if isinstance(error, ValueError) else type(error).__name__, "")
            raise ServiceError("photo_library_invalid") from error
        photos, held_photos = {}, {}
        for key, entry in data["photos"].items():
            reason = self._unusable_photo(key, entry)
            if reason is None:
                photos[key] = entry
            else:
                self._warn("photo record", reason, key if _ID.fullmatch(key) else "no usable id")
                held_photos[key] = entry
        selected, held_selected = {}, {}
        for variant, key in data["selected"].items():
            if key is None or key == "official" or (isinstance(key, str) and key in photos):
                selected[variant] = key
            else:
                self._warn("selection", "no such photo", variant)
                held_selected[variant] = key
        manifest = _Manifest.of(data, photos, selected, held_photos, held_selected)
        self._cached, self._stamp = manifest, stamp
        return manifest

    @staticmethod
    def _unusable_photo(key: str, entry) -> str | None:
        """Why a photo record cannot be used (a word, never a path), None when it can."""
        if not _ID.fullmatch(key):
            return "bad key"
        if not isinstance(entry, dict):
            return "not an object"
        if entry.get("id") != key:
            return "id differs from its key"
        if not isinstance(entry.get("variant"), str):
            return "no variant"
        if not isinstance(entry.get("sha256"), str) or not _DIGEST.fullmatch(entry["sha256"]):
            return "bad digest"
        return None

    def _write(self, data: _Manifest) -> None:
        atomic_json(self.index, data.document() if isinstance(data, _Manifest) else data)
        self._cached = self._stamp = None  # read back: held rows and the file's order come from what is on disk

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not _ID.fullmatch(key):
            raise ServiceError("photo_missing")
        path = self.root / "images" / f"{key}.png"
        if path.is_symlink() or path.parent.is_symlink() or self.root.is_symlink():
            raise ServiceError("photo_invalid")
        return path

    def _entry(self, variant: str, key: str) -> dict:
        self._path(key)
        entry = self._read()["photos"].get(key)
        if entry is None or self.family(entry["variant"]) != self.family(variant):
            raise ServiceError("photo_missing")
        return entry

    @staticmethod
    def url(variant: str, key: str) -> str:
        return f"/asset/photography/{quote(variant, safe='')}/{key}.png"

    def list(self, variant: str) -> dict:
        with self._lock:
            data = self._read()
            photos = [{**entry, "url": self.url(variant, key), "managed": True,
                       "provenance": "generated" if entry.get("builtin") else "capture"} for key, entry in data["photos"].items()
                      if self.family(entry["variant"]) == self.family(variant)]
            return {"variant": variant, "photos": photos, "app": self._chosen(data, variant)}

    def app_url(self, variant: str) -> str | None:
        with self._lock:
            key = self._chosen(self._read(), variant)
            return self.url(variant, key) if key and key != "official" else None

    def has_selection(self, variant: str) -> bool:
        with self._lock:
            data = self._read()
            return variant in data["selected"] and (data["selected"][variant] is None or
                                                    self._chosen(data, variant) is not None)

    def save(self, variant: str, png: str, mode: str, expression: str = "", action: str = "", source: str = "", *, builtin: bool = False) -> dict:
        if mode not in ("portrait", "lobby") or not all(isinstance(value, str) and len(value) <= 500
                                                      for value in (variant, expression, action, source)):
            raise ServiceError("photo_invalid")
        raw, width, height = decode_capture(png)
        key = uuid.uuid4().hex
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            before = copy.deepcopy(data)
            counters = data.setdefault("next_by_variant", {})
            number = max(counters.get(variant, 1), max((int(item.get("number", 0)) + 1
                         for item in data["photos"].values() if item["variant"] == variant and not item.get("builtin")), default=1))
            if builtin:
                existing = next((item["id"] for item in data["photos"].values()
                                 if item["variant"] == variant and item.get("builtin")), None)
                if existing:
                    self.image(variant, existing)
                    return {**self.list(variant), "saved": existing}
            entry = {"id": key, "number": number if not builtin else 0, "variant": variant, "mode": mode,
                     "expression": expression, "action": action, "source": source, "width": width, "height": height,
                     "sha256": hashlib.sha256(raw).hexdigest(), "created": datetime.now(timezone.utc).isoformat()}
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
                data["photos"][key] = entry
                if variant not in data.held_selected:  # a row kept as it is stays the variant's row
                    data["selected"].setdefault(variant, None)
                if not builtin:
                    counters[variant] = number + 1
                data["next"] += 1
                self._write(data)
            except BaseException:
                temporary.unlink(missing_ok=True)
                # A manifest rename can succeed before its directory flush fails.
                # Restore it before removing the image; retain valid bytes if rollback fails.
                self._cached = None
                if self._read()["photos"].get(key):
                    self._write(before)
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
                raise
        return {**self.list(variant), "saved": key}

    def select(self, variant: str, key: str | None) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            if key not in (None, "official"):
                self.image(variant, key)
            data["selected"][variant] = key
            self._write(data)
        return self.list(variant)

    def delete(self, variant: str, key: str, protected: set[str] = frozenset()) -> dict:
        if key == "official":
            raise ServiceError("asset_official")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock, file_lock(self.root / "index.lock"):
            self._cached = None
            data = copy.deepcopy(self._read())
            self._entry(variant, key)
            # a kept row that names the file keeps it: a held record or selection, or a selection that is stale now
            stale = {chosen for chosen_for, chosen in data["selected"].items()
                     if chosen not in (None, "official") and self._chosen(data, chosen_for) is None}
            if key in protected or key in data.named() or key in stale:
                raise ServiceError("photo_in_use")
            before = copy.deepcopy(data)
            raw = self.image(variant, key)
            del data["photos"][key]
            for selected_variant, selected in list(data["selected"].items()):
                if selected == key:
                    data["selected"][selected_variant] = None
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
        return self.list(variant)

    def image(self, variant: str, key: str) -> bytes:
        with self._lock:
            entry = self._entry(variant, key)
            try:
                raw = self._path(key).read_bytes()
            except FileNotFoundError:
                raise ServiceError("photo_missing") from None
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ServiceError("photo_invalid")
            return raw
