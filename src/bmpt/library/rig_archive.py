"""The official rigs of portraits, kept per game release (always on, no switch).

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
#: a kept release is taken when its masks overlap the mod's this much at least (intersection over union, the mean over
#: the atlas's pages) and this much more than the release installed now. Measured on the update of 2026-11-10: a
#: portrait's old texture against its own release 1.0, against the new release's of the 128 changed portraits median
#: 0.39 (0.15 to 1.0; 1.0 where the layout stayed)
MATCH_MIN = 0.5
MATCH_MARGIN = 0.1


def alpha_mask(image: Image.Image) -> np.ndarray:
    """Box-filter a page's alpha to MASK by MASK pixels and threshold it above ALPHA."""
    alpha = image.getchannel("A") if "A" in image.getbands() else image.convert("RGBA").getchannel("A")
    return np.asarray(alpha.resize((MASK, MASK), Image.Resampling.BOX)) > ALPHA


def pack(mask: np.ndarray) -> str:
    """Pack a boolean page mask into base64-encoded bits in row-major order."""
    return base64.b64encode(np.packbits(mask.astype(bool), axis=None).tobytes()).decode("ascii")


def unpack(data: str) -> np.ndarray | None:
    """Decode a base64 mask to a MASK by MASK boolean array, or None for invalid or short data."""
    try:
        bits = np.frombuffer(base64.b64decode(data, validate=True), dtype=np.uint8)
    except (ValueError, TypeError):
        return None
    if bits.size * 8 < MASK * MASK:
        return None
    return np.unpackbits(bits)[: MASK * MASK].reshape(MASK, MASK).astype(bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Compute two masks' intersection over union, treating two empty masks as a perfect match."""
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def overlap(mod: Mapping[str, np.ndarray], masks: Mapping[str, np.ndarray]) -> float | None:
    """Average page-mask IoU for texture keys shared by a mod and a release.

    Args:
        mod: Mod page masks indexed by normalized texture key.
        masks: Official release page masks indexed by the same keys.

    Returns:
        Mean overlap, or None when no page keys are shared.
    """
    keys = [key for key in masks if key in mod]
    return sum(iou(mod[key], masks[key]) for key in keys) / len(keys) if keys else None


@dataclass(frozen=True)
class Rig:
    """Keep a portrait release's official atlas, skeleton, layout hash, and page masks.

    Assets retain their names and SHA-1 digests; masks use normalized texture keys.
    """

    core: str
    atlas: str
    skeleton: str
    atlas_sha1: str
    skeleton_sha1: str
    layout: str
    masks: dict[str, str] = field(default_factory=dict)
    seen: str = ""

    def unpacked(self) -> dict[str, np.ndarray]:
        """Decode usable page masks by texture key, omitting invalid encodings."""
        out = {}
        for key, data in self.masks.items():
            mask = unpack(data)
            if mask is not None:
                out[key] = mask
        return out


def best(
    rigs: Iterable[Rig], mod: Mapping[str, np.ndarray], current: Mapping[str, np.ndarray]
) -> tuple[Rig, float] | None:
    """Choose a kept rig whose alpha masks clearly fit a mod better than the installed rig.

    Args:
        rigs: Archived official releases to rank by mean page-mask overlap.
        mod: Mod texture keys mapped to alpha masks.
        current: Installed release's texture keys mapped to alpha masks.

    Returns:
        Best rig and overlap if it reaches MATCH_MIN and beats current by MATCH_MARGIN;
        None when the current rig fits or the evidence is insufficient.
    """
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
        """Locate an archive without reading its index until needed.

        Args:
            folder: Library rigs folder containing index.json and content-addressed objects.
        """
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
        """Defer index writes until the outermost block exits, avoiding a large rewrite per portrait.

        Kept asset files are written immediately.
        """
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
            return Rig(
                core,
                str(item["atlas"]),
                str(item["skeleton"]),
                str(item["atlasSha1"]),
                str(item["skeletonSha1"]),
                str(item["layout"]),
                dict(masks),
                str(item.get("seen", "")),
            )
        except KeyError:
            return None

    def rigs(self, core: str) -> list[Rig]:
        """Return a portrait core's kept rigs, oldest first, ignoring core case."""
        with self._lock:
            items = self._load()["rigs"].get(core.lower()) or []
        return [rig for rig in (self._rig(core.lower(), item) for item in items) if rig is not None]

    def has(self, core: str, atlas_sha1: str, skeleton_sha1: str) -> bool:
        """Check whether a portrait's exact atlas and skeleton pair is already archived.

        Args:
            core: Portrait bundle core, matched case-insensitively.
            atlas_sha1: Hexadecimal digest of the official atlas bytes.
            skeleton_sha1: Hexadecimal digest of the official skeleton bytes.
        """
        return any(rig.atlas_sha1 == atlas_sha1 and rig.skeleton_sha1 == skeleton_sha1 for rig in self.rigs(core))

    def stamp(self, install: str) -> str | None:
        """Return an install key's last archived resource signature, or None if unrecorded."""
        with self._lock:
            value = self._load()["stamps"].get(install)
        return value if isinstance(value, str) else None

    def set_stamp(self, install: str, stamp: str) -> None:
        """Persist the resource signature covered by an install's archive pass.

        Args:
            install: Install key whose archive coverage is being recorded.
            stamp: Resource-list signature identifying the release inspected by the pass.
        """
        with self._lock:
            self._load()["stamps"][install] = stamp
            self._save()

    def add(
        self,
        core: str,
        atlas: str,
        atlas_data: bytes,
        skeleton: str,
        skeleton_data: bytes,
        layout: str,
        masks: Mapping[str, np.ndarray],
    ) -> bool:
        """Keep a distinct official portrait rig for repairing mods painted for that release.

        Every distinct atlas/skeleton pair is retained because deleting it would remove
        the repair baseline for mods drawn on that rig.

        Args:
            core: Portrait bundle core, stored case-insensitively.
            atlas: Atlas asset name associated with the kept bytes.
            atlas_data: Official atlas text bytes stored by SHA-1.
            skeleton: Skeleton asset name associated with the kept bytes.
            skeleton_data: Official skeleton bytes stored by SHA-1.
            layout: Atlas layout hash from fingerprint.atlas.
            masks: Texture keys mapped to page alpha masks for release matching.

        Returns:
            Whether a new pair was added; False when the exact pair is already kept.
        """
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
        item = {
            "atlas": atlas,
            "skeleton": skeleton,
            "atlasSha1": atlas_sha1,
            "skeletonSha1": skeleton_sha1,
            "layout": layout,
            "masks": {key: pack(mask) for key, mask in sorted(masks.items())},
            "seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            data = self._load()
            items = data["rigs"].setdefault(core.lower(), [])
            items.append(item)
            self._save()
            self._generation += 1
        return True

    def read(self, sha1: str) -> bytes | None:
        """Read an archived atlas or skeleton by lower-case SHA-1, or None if absent or mismatched."""
        if len(sha1) != 40 or not all(c in "0123456789abcdef" for c in sha1):
            return None
        try:
            data = (self.folder / "objects" / sha1).read_bytes()
        except OSError:
            return None
        return data if hashlib.sha1(data).hexdigest() == sha1 else None

    def generation(self) -> int:
        """Return the local addition counter used to invalidate derived archive results."""
        return self._generation
