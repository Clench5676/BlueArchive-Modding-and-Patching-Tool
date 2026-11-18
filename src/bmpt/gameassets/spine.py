"""In-memory extraction of Spine files and atlas page names."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from typing import Iterable, Mapping

from PIL import Image
from bmpt.engine import spine_kind

from ..unity.texture_guard import texture_image
from .errors import AssetNotFound
from .unity import open_bundle


def spine_resource_type(core: str, category: str | None = None) -> str | None:
    """Use the engine's bundle classification to identify an App resource type."""
    return {"spr": "portrait", "lobby": "memorialLobby"}.get(spine_kind(core, category))


def spine_stamps(bundles: list[Path]) -> list[tuple[str, int, int, int]]:
    """Read ordered file versions, detecting replacements that preserve size and mtime.

    Args:
        bundles: Bundle paths resolved and deduplicated in merge order.

    Returns:
        Resolved path, byte size, mtime, and ctime in nanoseconds for each bundle.

    Raises:
        AssetNotFound: The list is empty or a bundle is missing.
    """
    stamps = []
    for path in dict.fromkeys(Path(path).resolve() for path in bundles):
        try:
            stat = path.stat()
        except FileNotFoundError:
            raise AssetNotFound(str(path)) from None
        stamps.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    if not stamps:
        raise AssetNotFound("spine")
    return stamps


#: the asset types a Spine preview reads of its bundles
PREVIEW_TYPES = frozenset({"TextAsset", "Texture2D"})


class Composition(list):
    """Carry bundles in merge order with asset types to omit from selected bundles.

    An asset-transfer import leaves types outside its options official, so its preview does too.
    """

    def __init__(self, bundles: Iterable[Path] = (), skip: Mapping[Path, Iterable[str]] | None = None):
        """Record a merge order and normalize per-bundle exclusions.

        Args:
            bundles: Source bundles in merge order, with later assets overriding earlier ones.
            skip: Asset type names to omit by bundle path; None reads all preview types.
        """
        super().__init__(bundles)
        self.skip = {Path(path).resolve(): frozenset(types) for path, types in (skip or {}).items() if types}


def skipped(bundles: list[Path]) -> dict[Path, frozenset[str]]:
    """Return a composition's per-bundle type exclusions, or an empty map for a plain list."""
    return dict(getattr(bundles, "skip", None) or {})


def spine_revision(bundles: list[Path]) -> str:
    """Hash ordered bundle revisions and type exclusions into a URL segment.

    Args:
        bundles: Merge-ordered paths, optionally a Composition carrying per-bundle type exclusions.
    """
    skip = sorted([str(path), sorted(types)] for path, types in skipped(bundles).items())
    return hashlib.sha256(
        json.dumps([spine_stamps(bundles), skip] if skip else spine_stamps(bundles)).encode()
    ).hexdigest()


def png_bytes(image: Image.Image, level: int | None = None) -> bytes:
    """Encode an image as PNG bytes without creating a file.

    Args:
        image: Pillow image to encode in its current mode.
        level: zlib compression level from 0 to 9; None uses Pillow's default of 6.
    """
    buffer = BytesIO()
    image.save(buffer, format="PNG", **({} if level is None else {"compress_level": level}))
    return buffer.getvalue()


def atlas_pages(data: bytes) -> list[str]:
    """Read page headers in both legacy and current Spine atlas syntax.

    A page starts after a blank line (or at the start); header properties contain
    colons. Region names may also end in .png and must not be treated as pages.

    Args:
        data: UTF-8 atlas text, optionally prefixed with a BOM.
    """
    pages = []
    page_start = True
    for line in data.decode("utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            page_start = True
        elif page_start and ":" not in text:
            pages.append(text)
            page_start = False
        else:
            page_start = False
    return pages


def extract_spine(bundles: list[Path], skip: Mapping[Path, frozenset[str]] | None = None) -> dict[str, bytes]:
    """Extract skeletons, atlases, and PNG textures while retaining atlas page filenames.

    Args:
        bundles: Bundles in merge order; later assets with the same name take precedence.
        skip: Asset types to omit by resolved bundle path; None reads all relevant types.

    Returns:
        File bytes by exported filename, including aliases matching atlas page spelling.

    Raises:
        AssetNotFound: An atlas references a page absent from the extracted textures.
    """
    result, textures = {}, {}
    for path in bundles:
        left = (skip or {}).get(Path(path).resolve(), frozenset())
        with open_bundle(path) as env:
            for obj in env.objects:
                if obj.type.name in left:
                    continue
                if obj.type.name == "TextAsset":
                    data = obj.read()
                    if data.m_Name.endswith((".skel", ".atlas")):
                        result[data.m_Name] = data.m_Script.encode("utf-8", "surrogateescape")
                elif obj.type.name == "Texture2D":
                    data = obj.read()
                    # served from the same machine and held in memory only: level 1 encodes a memorial lobby's large
                    # pages
                    # several times faster than the default and the larger bytes cost nothing on loopback
                    textures[data.m_Name] = png_bytes(texture_image(data, path.name).convert("RGBA"), level=1)
    for name, data in textures.items():
        result[name if name.lower().endswith(".png") else name + ".png"] = data
    # Some bundles use a different case, a .png suffix, or a directory prefix
    # in the atlas. Serve the original page spelling as an alias.
    aliases = {Path(name).stem.lower(): data for name, data in textures.items()}
    aliases.update({name.lower(): data for name, data in textures.items()})
    for name, data in list(result.items()):
        if not name.endswith(".atlas"):
            continue
        for page in atlas_pages(data):
            if page in result:
                continue
            content = aliases.get(Path(page).stem.lower())
            if content is None:
                raise AssetNotFound(page)
            result[page] = content
    return result
