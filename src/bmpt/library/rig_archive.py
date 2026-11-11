"""The official rigs of portraits, kept per game release (BL-166; owner 2026-11-11: always on, no switch).

A game update rewrites a portrait's .skel and .atlas files in place. A texture-only mod drawn for the release before
then has nothing left to be drawn on: the old files are gone from the install, and a mod imported without a record of
them (``fingerprint.painted``) cannot be repaired. So while a release is installed, the library keeps each portrait's
official atlas and skeleton here, with a small alpha mask of every page the atlas names: once per distinct pair, so a
release that leaves a portrait as it was adds nothing. After an update, ``best`` finds the release a mod's textures
were drawn for by their alpha alone: each page's mask against each kept release's, the closest one where it is
clearly closer than the release installed now.

The folder (``<library>/rigs``) holds ``index.json`` and ``objects/<sha1>``, the atlas and skeleton bytes named by
their SHA-1 (no name read from a file ever chooses a path). An index that cannot be read counts as empty: the archive
never fails a caller. The project's own code (GPLv3).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import numpy as np
from PIL import Image

from .storage import atomic_json

logger = logging.getLogger(__name__)

VERSION = 1
#: a mask's side, in texels: a page's alpha box-filtered down to this square
MASK = 128
#: alpha above this counts as drawn
ALPHA = 16
#: distinct rigs kept per portrait core; the oldest go first
KEEP = 8
#: a kept release is taken when its masks overlap the mod's this much at least (intersection over union, the mean over
#: the atlas's pages) and this much more than the release installed now. Measured on the update of 2026-11-10: a
#: portrait's old texture against its own release 1.0, against the new release's of the 128 changed portraits median
#: 0.39 (0.15 to 1.0; 1.0 where the layout stayed)
MATCH_MIN = 0.5
MATCH_MARGIN = 0.1


def alpha_mask(image: Image.Image) -> np.ndarray:
    """Where a page is drawn: its alpha box-filtered to MASK x MASK, above ALPHA."""
    alpha = image.getchannel("A") if "A" in image.getbands() else image.convert("RGBA").getchannel("A")
    return np.asarray(alpha.resize((MASK, MASK), Image.Resampling.BOX)) > ALPHA


def pack(mask: np.ndarray) -> str:
    return base64.b64encode(np.packbits(mask.astype(bool), axis=None).tobytes()).decode("ascii")


def unpack(data: str) -> np.ndarray | None:
    try:
        bits = np.frombuffer(base64.b64decode(data, validate=True), dtype=np.uint8)
    except (ValueError, TypeError):
        return None
    if bits.size * 8 < MASK * MASK:
        return None
    return np.unpackbits(bits)[:MASK * MASK].reshape(MASK, MASK).astype(bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def overlap(mod: Mapping[str, np.ndarray], masks: Mapping[str, np.ndarray]) -> float | None:
    """How closely a mod's pages follow a release's: the mean IoU over the pages both have (texture keys); None when
    they share none."""
    keys = [key for key in masks if key in mod]
    return sum(iou(mod[key], masks[key]) for key in keys) / len(keys) if keys else None


@dataclass(frozen=True)
class Rig:
    """One kept release of a portrait's rig: its official atlas and skeleton (asset names, SHA-1 of their bytes), the
    atlas's layout (``fingerprint.atlas``) and the alpha mask of each page it names (texture keys)."""
    core: str
    atlas: str
    skeleton: str
    atlas_sha1: str
    skeleton_sha1: str
    layout: str
    masks: dict[str, str] = field(default_factory=dict)
    seen: str = ""

    def unpacked(self) -> dict[str, np.ndarray]:
        out = {}
        for key, data in self.masks.items():
            mask = unpack(data)
            if mask is not None:
                out[key] = mask
        return out


def best(rigs: Iterable[Rig], mod: Mapping[str, np.ndarray], current: Mapping[str, np.ndarray]) -> tuple[Rig, float] | None:
    """Of kept releases, the one a mod's pages (``mod``, texture key to mask) were drawn on, with its overlap: the
    closest, where it reaches MATCH_MIN and beats the release installed now (``current``, its masks) by MATCH_MARGIN.
    None otherwise: the mod follows the release installed now, or nothing tells."""
    now = overlap(mod, current)
    found = None
    for rig in rigs:
        score = overlap(mod, rig.unpacked())
        if score is not None and (found is None or score > found[1]):
            found = (rig, score)
    if found is None or found[1] < MATCH_MIN or (now is not None and found[1] < now + MATCH_MARGIN):
        return None
    return found


class RigArchive:
    """The kept rigs of one library (``folder``: ``<library>/rigs``). Thread-safe; writes are atomic."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._generation = 0
        self._deferring = 0  # open deferred() blocks: the index is written when the last one ends
        self._dirty = False

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            data: Any = None
            try:
                data = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, RecursionError) as error:
                logger.warning("Rig archive index unreadable, starting empty: %s", error)
            if not isinstance(data, dict) or data.get("version") != VERSION:
                data = None
            self._data = data or {"version": VERSION, "rigs": {}, "stamps": {}}
        return self._data

    def _save(self) -> None:
        if self._deferring:
            self._dirty = True
            return
        atomic_json(self.folder / "index.json", self._load())
        self._dirty = False

    @contextmanager
    def deferred(self) -> Iterator[None]:
        """Write the index once, at the end of the block, instead of at every rig kept (BL-183: the startup archive
        wrote its whole index, megabytes by the end, once per portrait). The kept files are written as they come."""
        with self._lock:
            self._deferring += 1
        try:
            yield
        finally:
            with self._lock:
                self._deferring -= 1
                if not self._deferring and self._dirty:
                    self._save()

    @staticmethod
    def _rig(core: str, item: Any) -> Rig | None:
        if not isinstance(item, dict):
            return None
        try:
            masks = item.get("masks") or {}
            if not isinstance(masks, dict) or not all(isinstance(v, str) for v in masks.values()):
                return None
            return Rig(core, str(item["atlas"]), str(item["skeleton"]), str(item["atlasSha1"]),
                       str(item["skeletonSha1"]), str(item["layout"]), dict(masks), str(item.get("seen", "")))
        except KeyError:
            return None

    def rigs(self, core: str) -> list[Rig]:
        """The kept rigs of a portrait core, oldest first."""
        with self._lock:
            items = self._load()["rigs"].get(core.lower()) or []
        return [rig for rig in (self._rig(core.lower(), item) for item in items) if rig is not None]

    def has(self, core: str, atlas_sha1: str, skeleton_sha1: str) -> bool:
        return any(rig.atlas_sha1 == atlas_sha1 and rig.skeleton_sha1 == skeleton_sha1 for rig in self.rigs(core))

    def stamp(self, install: str) -> str | None:
        with self._lock:
            value = self._load()["stamps"].get(install)
        return value if isinstance(value, str) else None

    def set_stamp(self, install: str, stamp: str) -> None:
        with self._lock:
            self._load()["stamps"][install] = stamp
            self._save()

    def add(self, core: str, atlas: str, atlas_data: bytes, skeleton: str, skeleton_data: bytes, layout: str,
            masks: Mapping[str, np.ndarray]) -> bool:
        """Keep one official rig of a portrait core (its atlas and skeleton, named and as bytes, the atlas's layout and
        its pages' masks). False when it is kept already. The oldest go beyond KEEP per core."""
        atlas_sha1, skeleton_sha1 = hashlib.sha1(atlas_data).hexdigest(), hashlib.sha1(skeleton_data).hexdigest()
        if self.has(core, atlas_sha1, skeleton_sha1):
            return False
        for sha1, data in ((atlas_sha1, atlas_data), (skeleton_sha1, skeleton_data)):
            path = self.folder / "objects" / sha1
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(f".{sha1}.tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
        item = {"atlas": atlas, "skeleton": skeleton, "atlasSha1": atlas_sha1, "skeletonSha1": skeleton_sha1,
                "layout": layout, "masks": {key: pack(mask) for key, mask in sorted(masks.items())},
                "seen": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._lock:
            data = self._load()
            items = data["rigs"].setdefault(core.lower(), [])
            items.append(item)
            dropped = items[:-KEEP]
            del items[:-KEEP]
            self._save()
            self._generation += 1
            kept = {sha1 for entries in data["rigs"].values() for entry in entries
                    for sha1 in (entry.get("atlasSha1"), entry.get("skeletonSha1"))}
        for entry in dropped:
            for sha1 in (entry.get("atlasSha1"), entry.get("skeletonSha1")):
                if isinstance(sha1, str) and sha1 not in kept and len(sha1) == 40 and sha1.isalnum():
                    (self.folder / "objects" / sha1).unlink(missing_ok=True)
        return True

    def read(self, sha1: str) -> bytes | None:
        """A kept atlas or skeleton by its SHA-1; None when it is missing or no longer has those bytes."""
        if len(sha1) != 40 or not all(c in "0123456789abcdef" for c in sha1):
            return None
        try:
            data = (self.folder / "objects" / sha1).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha1(data).hexdigest() == sha1 else None

    def generation(self) -> int:
        """Changes whenever this archive keeps another rig (to key what was worked out from it)."""
        return self._generation
