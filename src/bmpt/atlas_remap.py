"""Repack painted Spine regions onto current official pages, without changing the rig."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageMath

from . import devmode, spine_names
from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.models import ContNameTypeKey, Patch
from ba_modding_toolkit.spine import get_skel_version
from .texture_guard import texture_image


class RemapError(ValueError):
    """A remap failure with locale parameters, including the offending asset."""

    def __init__(self, code: str, **params):
        super().__init__(f"{code}: {params}")
        self.code, self.params = code, params


def texture_key(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".png")


@dataclass(frozen=True)
class Region:
    name: str
    index: int
    page: str
    xy: tuple[int, int]
    size: tuple[int, int]
    original: tuple[int, int]
    offset: tuple[int, int]
    degrees: int = 0

    @property
    def key(self) -> tuple[str, int]:
        return self.name, self.index

    @property
    def label(self) -> str:
        return self.name if self.index == -1 else f"{self.name} [{self.index}]"


@dataclass
class Page:
    name: str
    size: tuple[int, int] = (0, 0)
    pma: bool = False
    format: str = "RGBA8888"


@dataclass
class Atlas:
    pages: dict[str, Page] = field(default_factory=dict)
    regions: dict[tuple[str, str, int], Region] = field(default_factory=dict)
    file: str = field(default="—", compare=False)


def parse_atlas(text: str | bytes, file: str = "—") -> Atlas:
    """Read legacy xy/size/orig/offset and modern bounds/offsets syntax."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeError as error:
            raise RemapError("atlas_remap_property", file=file, page="—", region="—",
                             field="UTF-8", value="—") from error
    atlas = Atlas(file=file)
    page = None
    name = None
    props = {}
    page_start = True

    def invalid(key: str, value: str) -> RemapError:
        return RemapError("atlas_remap_property", file=file, page=page.name if page else "—",
                          region=name or "—", field=key, value=value)

    def numbers(key: str, default: tuple) -> tuple:
        if key not in props:
            return default
        try:
            values = tuple(int(v.strip()) for v in props[key].split(","))
        except ValueError as error:
            raise invalid(key, props[key]) from error
        if len(values) != len(default):
            raise invalid(key, props[key])
        return values

    def flush() -> None:
        if name is None:
            return
        bounds = numbers("bounds", numbers("xy", (0, 0)) + numbers("size", (0, 0)))
        offsets = numbers("offsets", numbers("offset", (0, 0)) + numbers("orig", bounds[2:]))
        rotation = props.get("rotate", "false").lower()
        degrees = {"true": 90, "false": 0}.get(rotation)
        if degrees is None:
            try:
                degrees = int(rotation) % 360
            except ValueError as error:
                raise invalid("rotate", rotation) from error
        if degrees not in (0, 90, 180, 270):
            raise invalid("rotate", rotation)
        index, = numbers("index", (-1,))
        region = Region(name, index, page.name, bounds[:2], bounds[2:],
                        offsets[2:], offsets[:2], degrees)
        if min(*region.size, *region.original) <= 0 or min(*region.xy, *region.offset) < 0:
            raise invalid("bounds/offsets", f"{bounds}/{offsets}")
        if any(o + s > original for o, s, original in zip(region.offset, region.size, region.original)):
            raise invalid("offsets/orig", f"{offsets}/{region.size}")
        key = (region.page, *region.key)
        if key in atlas.regions:
            raise RemapError("atlas_remap_region_ambiguous", file=file, region=region.label, pages=region.page)
        atlas.regions[key] = region

    for line_number, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line:
            flush()
            name, props, page_start = None, {}, True
        elif page_start:
            if ":" in line:
                raise RemapError("atlas_remap_structure", file=file, line=line_number, value=line)
            if line in atlas.pages:
                raise RemapError("atlas_remap_page_ambiguous", file=file, page=line)
            page = Page(line)
            atlas.pages[line] = page
            page_start = False
        elif ":" not in line:
            flush()
            name, props = line, {}
        else:
            key, value = (v.strip() for v in line.split(":", 1))
            if name is not None:
                props[key] = value
            elif key == "size":
                try:
                    page.size = tuple(int(v.strip()) for v in value.split(","))
                except ValueError as error:
                    raise invalid(key, value) from error
                if len(page.size) != 2 or min(page.size) <= 0:
                    raise invalid(key, value)
            elif key == "pma":
                page.pma = value.lower() == "true"
            elif key == "format":
                page.format = value
    flush()
    if not atlas.pages:
        raise RemapError("atlas_remap_structure", file=file, line=1, value="—")
    return atlas


def pair_atlases(old: dict, new: dict) -> dict[str, str]:
    """Pair by asset basename, with the preview's single remaining pair fallback."""
    result = {}
    remaining = set(old)
    for target in new:
        matches = [source for source in remaining if Path(texture_key(source)).stem == Path(texture_key(target)).stem]
        if len(matches) == 1:
            result[target] = matches[0]
            remaining.remove(matches[0])
    unmatched = set(new) - result.keys()
    if len(remaining) == len(unmatched) == 1:
        result[unmatched.pop()] = remaining.pop()
    return result


def region_layout(region: Region, atlas: Atlas) -> tuple:
    """Drawing coordinates, independent of page density and atlas spelling."""
    width, height = atlas.pages[region.page].size
    return (texture_key(region.page), region.degrees,
            tuple(v / (width or 1) for v in (region.xy[0], region.size[0])),
            tuple(v / (height or 1) for v in (region.xy[1], region.size[1])),
            tuple(v / size for v, size in zip(region.offset, region.original)),
            tuple(v / size for v, size in zip(region.size, region.original)))


def skeleton_regions(skeletons: dict[str, bytes]) -> set[str] | None:
    """Filter JSON atlases here; binary attachment paths are read by the web runtime."""
    if not skeletons:
        return None
    result = set()
    try:
        for data in skeletons.values():
            skins = json.loads(data).get("skins", [])
            attachments = skins.values() if isinstance(skins, dict) else (skin.get("attachments", {}) for skin in skins)
            for skin in attachments:
                for slot in skin.values():
                    for name, attachment in slot.items():
                        if attachment.get("type") in ("boundingbox", "path", "point", "clipping"):
                            continue
                        path = attachment.get("path", name)
                        sequence = attachment.get("sequence")
                        if sequence:
                            result.update(path + str(sequence.get("start", 1) + i).zfill(sequence.get("digits", 0))
                                          for i in range(sequence["count"]))
                        else:
                            result.add(path)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return result


def layout_changes(old: dict[str, bytes], new: dict[str, bytes], regions: set[str] | None = None) -> list[str]:
    pairs = pair_atlases(old, new)
    def layout(atlas: Atlas) -> dict:
        return {(texture_key(r.page), r.name, r.index): region_layout(r, atlas) for r in atlas.regions.values()
                if regions is None or r.name in regions}

    changes = [name for inputs, names in ((old, old.keys() - set(pairs.values())), (new, new.keys() - pairs.keys()))
               for name in names if regions is None or layout(parse_atlas(inputs[name], name))]
    for name, data in new.items():
        if name not in pairs:
            continue
        left, right = parse_atlas(old[pairs[name]], pairs[name]), parse_atlas(data, name)
        if layout(left) != layout(right):
            changes.append(name)
    return sorted(changes)


def page_size_mismatch(atlases: dict[str, bytes], textures: dict[str, tuple[int, int]]) -> bool:
    for name, data in atlases.items():
        for page in parse_atlas(data, name).pages.values():
            size = textures.get(texture_key(page.name))
            if size is None or not all(page.size) or size[0] * page.size[1] != size[1] * page.size[0]:
                return True
    return False


def _box(region: Region, page: Page, image: Image.Image, file: str = "—") -> tuple[int, int, int, int]:
    width, height = region.size[::-1] if region.degrees % 180 else region.size
    declared = page.size if all(page.size) else image.size
    sx, sy = image.width / declared[0], image.height / declared[1]
    x, y = region.xy
    box = round(x * sx), round(y * sy), round((x + width) * sx), round((y + height) * sy)
    if box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height:
        raise RemapError("atlas_remap_bounds", file=file, region=region.label, page=page.name,
                         width=image.width, height=image.height)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise RemapError("atlas_remap_property", file=file, page=page.name, region=region.label,
                         field="bounds", value=str(box))
    return box


def _rotate(image: Image.Image, degrees: int) -> Image.Image:
    operation = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
                 270: Image.Transpose.ROTATE_270}.get(degrees % 360)
    return image.transpose(operation) if operation else image


def _alpha(image: Image.Image, source_pma: bool, target_pma: bool) -> Image.Image:
    if source_pma == target_pma:
        return image
    if source_pma:
        return Image.frombytes("RGBa", image.size, image.tobytes()).convert("RGBA")
    return Image.frombytes("RGBA", image.size, image.convert("RGBa").tobytes())


def _resize(image: Image.Image, size: tuple[int, int], box: tuple, pma: bool) -> Image.Image:
    if all(value == round(value) for value in box) and (box[2] - box[0], box[3] - box[1]) == size:
        return image.crop(tuple(round(value) for value in box))
    if pma:
        return Image.frombytes("RGBA", size, Image.frombytes("RGBa", image.size, image.tobytes())
                               .resize(size, Image.Resampling.BICUBIC, box=box).tobytes())
    # Float bands avoid the 8-bit premultiply/unpremultiply loss in Pillow's RGBA resize.
    red, green, blue, alpha = (band.convert("F") for band in image.split())
    resized_alpha = alpha.resize(size, Image.Resampling.BICUBIC, box=box)
    channels = []
    for channel in (red, green, blue):
        weighted = ImageMath.lambda_eval(lambda v: v["c"] * v["a"], c=channel, a=alpha)
        weighted = weighted.resize(size, Image.Resampling.BICUBIC, box=box)
        straight = ImageMath.lambda_eval(lambda v: v["c"] / (v["a"] + (v["a"] == 0)) + 0.5,
                                        c=weighted, a=resized_alpha)
        channels.append(straight.convert("L"))
    return Image.merge("RGBA", (*channels, resized_alpha.convert("L")))


#: how far two regions' trimmed rectangles may differ, as fractions of their untrimmed images, and still be one frame
FRAME_TOLERANCE = 0.01
#: the share of a region's trimmed rectangle the old one must hold of the same canvas to be the same piece of it
FRAME_HELD = 0.9
#: the share two regions of one untrimmed size have in common of what either holds, from which on they are two
#: pictures of that size (expressions) and not parts of one image (a body's layers and pieces)
PICTURE_OVERLAP = 0.8
#: how far two trimmed rectangles' proportions may differ and still bound the same art
TRIM_TOLERANCE = 0.03
#: how far the art's share of its image may drift when the image is exported again with other margins
CANVAS_DRIFT = 0.15
#: how far the proportions of two untrimmed images may differ and still be one canvas at two scales
CANVAS_TOLERANCE = 0.005
#: pixels kept around a hull outline: the art's soft edge and the sampling of a mesh drawn a little differently
HULL_MARGIN = 3


def _hull_mask(region: Region, outlines: list[list[float]], size: tuple[int, int]) -> Image.Image:
    """The part of a region's packed rectangle (unrotated, ``size`` pixels) its meshes draw: their hull outlines
    (``spine_names.hulls``: fractions of the untrimmed image), widened by ``HULL_MARGIN``. Atlases pack regions into
    the empty corners of each other's rectangles, so a rectangle holds pixels of its neighbours too."""
    sx, sy = size[0] / region.size[0], size[1] / region.size[1]
    width, height = region.original[0] * sx, region.original[1] * sy
    left, top = region.offset[0] * sx, (region.original[1] - region.offset[1] - region.size[1]) * sy
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    for outline in outlines:
        points = [(outline[i] * width - left, outline[i + 1] * height - top) for i in range(0, len(outline) - 1, 2)]
        if len(points) >= 3:
            draw.polygon(points, fill=255)
    return mask.filter(ImageFilter.MaxFilter(2 * HULL_MARGIN + 1))


def _frame(region: Region) -> tuple[float, ...]:
    """A region's trimmed rectangle as fractions of its untrimmed image."""
    return (region.offset[0] / region.original[0], region.offset[1] / region.original[1],
            region.size[0] / region.original[0], region.size[1] / region.original[1])


def _placement(source: Region, target: Region) -> tuple[str, bool]:
    """How a region's pixels go from one layout to the other, and whether its art changed between them.

    ``frame``: both trim the same untrimmed image (the same canvas, scaled or not), so the pixels keep their place
    in it. That holds too when the game cut the canvas into other pieces: each piece takes its part.
    ``trim``: a game update may export the image again on another canvas (a margin added, a side cropped). The
    art then keeps, nearly, its share of the image while the image changes its proportions: the trimmed rectangles
    bound the same art and are laid on each other.

    The art changed when the canvas did and the trimmed rectangles differ in shape as well (nothing then says where
    the old pixels belong), or when the region is another piece of its canvas; the caller reports the region."""
    if max(abs(a - b) for a, b in zip(_frame(source), _frame(target))) < FRAME_TOLERANCE:
        return "frame", False
    (sw, sh), (tw, th) = source.size, target.size
    (ow, oh), (nw, nh) = source.original, target.original
    if abs(nw * oh - nh * ow) <= CANVAS_TOLERANCE * nw * oh:
        # One canvas. Where the game cut it into other pieces, the part of this piece the old one did not hold
        # has no pixels of the mod's: the art changed as far as this region goes.
        (sx, sy, sw_, sh_), (tx, ty, tw_, th_) = _frame(source), _frame(target)
        held = (max(0.0, min(sx + sw_, tx + tw_) - max(sx, tx)) * max(0.0, min(sy + sh_, ty + th_) - max(sy, ty)))
        return "frame", held < FRAME_HELD * tw_ * th_

    def close(value: float, wanted: float) -> bool:
        # a pixel of rounding, but for regions of a few pixels, where it is the shape
        return abs(value - wanted) <= max(TRIM_TOLERANCE * wanted, min(1.5, 0.15 * wanted))

    same_shape = close(sw * th / sh, tw) and close(sh * tw / sw, th)
    if all(1 - CANVAS_DRIFT <= trimmed / canvas <= 1 / (1 - CANVAS_DRIFT)
           for trimmed, canvas in ((tw / sw, nw / ow), (th / sh, nh / oh))):
        return "trim", not same_shape
    return "frame", True


def _rect(region: Region) -> tuple[float, float, float, float]:
    """A region's trimmed rectangle on its untrimmed image as fractions of it: left, top, right, bottom."""
    (w, h), (ow, oh), (ox, oy) = region.size, region.original, region.offset
    top = oh - oy - h
    return ox / ow, top / oh, (ox + w) / ow, (top + h) / oh


def _pictures(regions: list[Region]) -> bool:
    """Whether regions of one untrimmed size are pictures of that size (a face's expressions, which fill their
    images alike) rather than parts of one image (a body's layers and pieces, each its own part of it)."""
    rects = [_rect(region) for region in regions]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            shared = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            either = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - shared
            if either <= 0 or shared > PICTURE_OVERLAP * either:
                return True
    return False


def _held(target: Region, sources: list[Region], steps: int = 24) -> float:
    """The share of a region's trimmed rectangle that regions of the same image, cut another way, hold between
    them."""
    left, top, right, bottom = _rect(target)
    rects = [_rect(source) for source in sources]
    inside = 0
    for i in range(steps):
        for j in range(steps):
            x, y = left + (right - left) * (i + 0.5) / steps, top + (bottom - top) * (j + 0.5) / steps
            inside += any(a <= x <= c and b <= y <= d for a, b, c, d in rects)
    return inside / (steps * steps)


def _recut(old: Atlas, new: Atlas, pairs: dict) -> dict[tuple, list[Region]]:
    """The regions of the new atlas that are pieces of an image the old atlas cut another way, each with the old
    pieces of that image.

    A game update may cut a body into other parts (two where there was one, the seam elsewhere, a layer more or
    less, the names swapped), so a part's namesake holds another part of the picture. The image is told by its
    untrimmed size: the new parts share one, their namesakes in the old atlas share one of the same proportions,
    and on either side the regions of that size are parts of an image, not pictures of that size. The old parts
    laid on each other in their draw order are the image again."""
    by_size: dict[tuple, list] = {}
    for key, region in new.regions.items():
        by_size.setdefault((None, region.original), []).append((key, region))
    old_by_size: dict[tuple, list[Region]] = {}
    for region in old.regions.values():
        old_by_size.setdefault(region.original, []).append(region)
    result = {}
    for (_page, (nw, nh)), members in by_size.items():
        sizes = {pairs[key].original for key, _region in members if key in pairs}
        if len(sizes) != 1:
            continue
        (ow, oh), = sizes
        sources = old_by_size[(ow, oh)]
        if abs(nw * oh - nh * ow) > 2 * CANVAS_TOLERANCE * nw * oh:
            continue
        if _pictures([region for _key, region in members]) or _pictures(sources):
            continue
        # one piece that kept its name and its place is no other cut
        if len(members) == 1 and len(sources) == 1 and not _placement(sources[0], members[0][1])[1]:
            continue
        if len(members) == 1 and len(sources) == 1 and _placement(sources[0], members[0][1])[0] == "trim":
            continue
        for key, region in members:
            # a piece the old ones do not hold between them has no picture to be cut from
            if _held(region, sources) >= FRAME_HELD:
                result[key] = sources
    return result


def _outlines(hulls: dict | None, name: str) -> list | None:
    """The hull outlines of a region when its skeleton tells them; None for one moved as its whole rectangle."""
    value = (hulls or {}).get(name)
    # Outlines come from a mod's skeleton or a stored record: a number that is no place in an image is no outline.
    if not isinstance(value, list) or not all(isinstance(outline, list) and len(outline) >= 6 and all(
            isinstance(number, (int, float)) and -8 <= number <= 8 for number in outline) for outline in value):
        return None
    return value or None


def _pair_regions(old: Atlas, new: Atlas) -> dict[tuple[str, str, int], Region]:
    """Unique names can move pages; repeated names are identified by page order."""
    result = {}
    old_order = {page: i for i, page in enumerate(old.pages)}
    new_order = {page: i for i, page in enumerate(new.pages)}
    sources, targets = {}, {}
    for region in old.regions.values():
        sources.setdefault(region.key, []).append(region)
    for key, region in new.regions.items():
        targets.setdefault(region.key, []).append((key, region))
    for identity in targets:
        left, right = list(sources.get(identity, [])), list(targets[identity])
        if len(left) == len(right) == 1:
            result[right[0][0]] = left[0]
            continue
        for key, target in right[:]:
            source = next((r for r in left if old_order[r.page] == new_order[target.page]), None)
            if source is not None:
                result[key] = source
                left.remove(source)
                right.remove((key, target))
        if left and right:
            if len(left) == len(right) == 1:
                result[right[0][0]] = left[0]
            else:
                raise RemapError("atlas_remap_region_ambiguous", file=old.file, region=left[0].label,
                                 pages=", ".join(r.page for r in left))
    return result


def _page_image(atlas: Atlas, page: str, images: dict[str, Image.Image]) -> Image.Image:
    image = images.get(texture_key(page))
    if image is None:
        raise RemapError("atlas_remap_page_missing", file=atlas.file, page=page)
    return image if image.mode == "RGBA" else image.convert("RGBA")


def remap_pages(old: Atlas, new: Atlas, mod_pages: dict[str, Image.Image],
                official_pages: dict[str, Image.Image], required: set[str] | None = None,
                fill: bool = False, hulls: dict | None = None,
                official_hulls: dict | None = None) -> tuple[dict[str, Image.Image], dict]:
    """Move only painted pixels onto transparent pages; refuse unavailable required regions. With ``fill`` a
    required region the mod has no pixels for takes the official ones instead (``official`` in the summary), and
    only a mod with no region to move at all is refused.

    ``hulls`` and ``official_hulls`` (``spine_names.hulls`` of the skeleton the mod's texture and the official one
    are drawn by) keep a region to what its meshes draw: only those pixels move, and only those are written, so a
    neighbour packed into the same rectangle neither comes along nor is erased. A region they do not outline moves
    as its whole rectangle."""
    mod_pages, official_pages = dict(mod_pages), dict(official_pages)
    official_sources = dict(official_pages)
    upscaled = []
    for atlas, images in ((old, mod_pages), (new, official_pages)):
        keys = set()
        for page in atlas.pages:
            key = texture_key(page)
            if key in keys:
                raise RemapError("atlas_remap_page_ambiguous", file=atlas.file, page=page)
            keys.add(key)
            if atlas is old and key not in images:
                continue
            image = _page_image(atlas, page, images)
            declared = atlas.pages[page].size
            size = max(image.width, declared[0]), max(image.height, declared[1])
            if image.size != size:
                # Resize the whole page before cropping, including pixels around region edges.
                if atlas is new:
                    image = Image.new("RGBA", size)
                elif atlas.pages[page].pma:
                    image = Image.frombytes("RGBA", size, Image.frombytes("RGBa", image.size, image.tobytes())
                                            .resize(size, Image.Resampling.LANCZOS).tobytes())
                else:
                    image = image.resize(size, Image.Resampling.LANCZOS)
                images[key] = image
                upscaled.append({"page": page, "w": size[0], "h": size[1]})
            for region in atlas.regions.values():
                if region.page == page:
                    _box(region, atlas.pages[page], image, atlas.file)
    pairs = _pair_regions(old, new)
    # Pieces of an image the old atlas cut another way: every old piece of it has to be among the mod's pages.
    # (Straight alpha only, as the game's pages are: premultiplied pieces are moved one by one as before.)
    recut = {key: sources for key, sources in _recut(old, new, pairs).items()
             if all(texture_key(source.page) in mod_pages and not old.pages[source.page].pma for source in sources)}
    canvases: dict[tuple, Image.Image] = {}
    needed = required if required is not None else {r.name for r in new.regions.values()}
    available = {target.name for key, target in new.regions.items()
                 if key in recut or (key in pairs and texture_key(pairs[key].page) in mod_pages)}
    missing = needed - available
    if missing and (not fill or not available):
        raise RemapError("atlas_remap_regions_missing", names=", ".join(sorted(missing)))
    output = {name: Image.new("RGBA", _page_image(new, name, official_pages).size) for name in new.pages}
    moved, added, official = [], [], []
    def unpainted(item) -> bool:
        if item[0] in recut:
            return False
        source = pairs.get(item[0])
        return source is None or texture_key(source.page) not in mod_pages

    def written(target: Region) -> list | None:
        """The outline the skeleton that will draw the result draws of a region: nothing is written outside it."""
        return _outlines(official_hulls, target.name)

    def assembled(sources: list[Region]) -> Image.Image:
        """The old parts of one image put together on its untrimmed canvas, at the mod's resolution: each part
        inside the outline its skeleton draws of it, laid on each other in the skeleton's draw order (``hulls``
        names the regions in it). Without a skeleton to tell, the lower parts first, so that a part lies over the
        one below it where they share a seam."""
        identity = tuple(sorted((source.page, *source.key) for source in sources))
        if identity not in canvases:
            whole = None
            order = {name: place for place, name in enumerate(hulls or {})}
            drawn = all(source.name in order for source in sources)
            for source in sorted(sources, key=lambda region: order[region.name] if drawn else -_rect(region)[1]):
                page = old.pages[source.page]
                image = _page_image(old, source.page, mod_pages)
                cut = _rotate(image.crop(_box(source, page, image, old.file)), -source.degrees)
                sx, sy = cut.width / source.size[0], cut.height / source.size[1]
                if whole is None:
                    whole = Image.new("RGBA", (round(source.original[0] * sx), round(source.original[1] * sy)))
                outline = _outlines(hulls, source.name)
                if outline is not None:
                    cut.putalpha(ImageChops.multiply(cut.getchannel("A"), _hull_mask(source, outline, cut.size)))
                corner = (round(source.offset[0] * sx),
                          round((source.original[1] - source.offset[1] - source.size[1]) * sy))
                layer = Image.new("RGBA", whole.size)
                layer.paste(cut, corner)
                whole = Image.alpha_composite(whole, layer)
            canvases[identity] = whole
        return canvases[identity]

    changed = []

    def place(target: Region, destination: Image.Image, corner: tuple[int, int], picture: Image.Image,
              mask: Image.Image | None = None) -> None:
        """Write a region's picture (as the page stores it, rotated) at its rectangle: inside the outline the
        skeleton draws of it, and of that only where ``mask`` allows. The packer puts regions into the empty corners
        of each other's rectangles, so a rectangle written whole would erase its neighbours."""
        outline = written(target)
        if outline is not None:
            unrotated = picture.size[::-1] if target.degrees % 180 else picture.size
            inside = _rotate(_hull_mask(target, outline, unrotated), target.degrees)
            mask = inside if mask is None else ImageChops.multiply(mask, inside)
        destination.paste(picture, corner, mask)

    def official_picture(target: Region, size: tuple[int, int]) -> Image.Image:
        # The official page as it is stored, which may be smaller than the atlas declares.
        image = _page_image(new, target.page, official_sources)
        cut = image.crop(_box(target, new.pages[target.page], image, new.file))
        return cut if cut.size == size else cut.resize(size, Image.Resampling.LANCZOS)

    # Official pixels first, the mod's over them, and whole rectangles before outlined regions: where rectangles
    # overlap, the mod's pixels and the outlined ones are kept.
    for key, target in sorted(new.regions.items(), key=lambda item: (not unpainted(item), written(item[1]) is not None)):
        target_page = new.pages[target.page]
        destination = output[target.page]
        box = _box(target, target_page, destination, new.file)
        source = pairs.get(key)
        packed = box[2] - box[0], box[3] - box[1]  # as the page stores it
        size = packed[::-1] if target.degrees % 180 else packed  # upright
        if unpainted((key, target)):
            if target.name in missing:
                place(target, destination, box[:2], official_picture(target, packed))
                official.append(target.label)
            else:
                added.append(target.label)
            continue
        if key in recut:
            # A piece of an image cut another way: taken from the image as the old pieces give it.
            sources = recut[key]
            source_page = old.pages[sources[0].page]
            full = assembled(sources)
            left, top, right, bottom = _rect(target)
            crop = (left * full.width, top * full.height, right * full.width, bottom * full.height)
            cut = _alpha(_resize(full, size, crop, source_page.pma), source_page.pma, target_page.pma)
            place(target, destination, box[:2], _rotate(cut, target.degrees))
            moved.append(target.label)
            continue
        placement, redrawn = _placement(source, target)
        if redrawn:
            changed.append(target.label)
            if fill:
                # Nothing says where the old pixels belong on art the game drew anew: the region keeps the official
                # ones rather than the mod's in the wrong place.
                place(target, destination, box[:2], official_picture(target, packed))
                continue
        source_page = old.pages[source.page]
        image = _page_image(old, source.page, mod_pages)
        cut = _rotate(image.crop(_box(source, source_page, image, old.file)), -source.degrees)
        # What the region's meshes draw of its rectangle, as the source lays it out: only those pixels are the
        # region's own, and they move with it.
        outline = _outlines(hulls, source.name)
        mask = _hull_mask(source, outline, cut.size) if outline is not None else None
        if placement == "frame":
            # Work in the untrimmed coordinate space; offsets are measured from the bottom left.
            # Keep the source's physical resolution until the target crop is resampled.
            sx, sy = cut.width / source.size[0], cut.height / source.size[1]
            canvas = round(source.original[0] * sx), round(source.original[1] * sy)
            corner = (round(source.offset[0] * sx),
                      round((source.original[1] - source.offset[1] - source.size[1]) * sy))
            full = Image.new("RGBA", canvas)
            full.paste(cut, corner)
            tx, ty = full.width / target.original[0], full.height / target.original[1]
            crop = (target.offset[0] * tx, (target.original[1] - target.offset[1] - target.size[1]) * ty,
                    (target.offset[0] + target.size[0]) * tx, (target.original[1] - target.offset[1]) * ty)
            if mask is not None:
                whole = Image.new("L", canvas)
                whole.paste(mask, corner)
                mask = whole
        else:
            # The image was exported again on another canvas: the trimmed rectangles bound the same art.
            full, crop = cut, (0, 0, cut.width, cut.height)
        cut = _resize(full, size, crop, source_page.pma)
        cut = _alpha(cut, source_page.pma, target_page.pma)
        if mask is not None:
            mask = _rotate(mask.resize(size, Image.Resampling.BILINEAR, box=crop).point(lambda v: 255 if v else 0),
                           target.degrees)
        place(target, destination, box[:2], _rotate(cut, target.degrees), mask)
        if region_layout(source, old) != region_layout(target, new):
            moved.append(target.label)
    matched = {(region.page, *region.key) for region in pairs.values()}
    matched.update((source.page, *source.key) for sources in recut.values() for source in sources)
    return output, {"moved": sorted(moved), "added": sorted(added), "official": sorted(official),
                    "changed": sorted(changed), "upscaled": upscaled,
                    "dropped": sorted(region.label for key, region in old.regions.items() if key not in matched)}


@dataclass
class Inputs:
    atlases: dict[str, bytes] = field(default_factory=dict)
    skeletons: dict[str, bytes] = field(default_factory=dict)
    textures: dict[str, Image.Image] = field(default_factory=dict)
    keys: dict[str, ContNameTypeKey] = field(default_factory=dict)
    # per atlas name, the hull outlines of the skeleton its textures are drawn by (spine_names.hulls), where a
    # caller knows them and the inputs carry no such skeleton
    hulls: dict[str, dict] = field(default_factory=dict)


def read_inputs(paths: list[Path]) -> Inputs:
    result = Inputs()
    for path in paths:
        devmode.check_read(path)
        bundle = Bundle.load(path)
        if bundle is None:
            raise RemapError("bundle_invalid", file=path.name)
        for obj in bundle.env.objects:
            if obj.type.name not in ("TextAsset", "Texture2D"):
                continue
            data = obj.read()
            name = data.m_Name
            if obj.type.name == "Texture2D":
                key = texture_key(name)
                try:
                    result.textures[key] = texture_image(data).convert("RGBA")
                except (ValueError, EOFError, OSError) as error:
                    raise RemapError("atlas_remap_invalid", file=f"{path.name} - {name}",
                                     reason=str(error)) from error
                result.keys[key] = ContNameTypeKey(obj.container, name, "Texture2D")
            elif name.lower().endswith((".atlas", ".skel")):
                raw = data.m_Script
                raw = raw if isinstance(raw, bytes) else raw.encode("utf-8", "surrogateescape")
                collection = result.atlases if name.lower().endswith(".atlas") else result.skeletons
                collection[name] = raw
    return result


def skeleton_warnings(old: dict[str, bytes], new: dict[str, bytes]) -> list[dict]:
    warnings = []
    pairs = pair_atlases(old, new)
    for name, target in new.items():
        source = old.get(pairs.get(name, ""))
        if source is None:
            warnings.append({"key": "rig", "name": name})
            continue
        if get_skel_version(source) != get_skel_version(target):
            warnings.append({"key": "version", "name": name})
        if source == target:
            continue
        try:
            left, right = json.loads(source), json.loads(target)
            if not isinstance(left, dict) or not isinstance(right, dict):
                raise ValueError("Invalid skeleton object")
            for field in ("bones", "slots", "skins"):
                if left.get(field) != right.get(field):
                    warnings.append({"key": "rig", "name": name})
                    break
        except (ValueError, UnicodeError):
            # Binary geometry is inspected by the bundled web runtime. Opaque differences
            # must remain a partial result for engine callers as well.
            warnings.append({"key": "rig", "name": name})
    return warnings


def rig_gains(mod_atlases: dict[str, bytes], mod_skeletons: dict[str, bytes],
              official_atlases: dict[str, bytes], official_skeletons: dict[str, bytes]) -> dict:
    """What the official rig has that a mod carrying its own atlas or skeleton lacks, per official asset name:
    ``animations`` the mod's counterpart skeleton does not declare, ``regions`` its counterpart atlas does not hold.
    Assets without a counterpart in the mod, and ones that cannot be read, are left out."""
    result: dict[str, dict[str, list[str]]] = {"animations": {}, "regions": {}}
    for name, source in pair_atlases(mod_skeletons, official_skeletons).items():
        try:
            have = set(spine_names.read(mod_skeletons[source]).animations)
            missing = [animation for animation in spine_names.read(official_skeletons[name]).animations
                       if animation not in have]
        except ValueError:
            continue
        if missing:
            result["animations"][name] = missing
    for name, source in pair_atlases(mod_atlases, official_atlases).items():
        try:
            have = {region.label for region in parse_atlas(mod_atlases[source], source).regions.values()}
            missing = sorted({region.label for region in parse_atlas(official_atlases[name], name).regions.values()}
                             - have)
        except (ValueError, UnicodeError):
            continue
        if missing:
            result["regions"][name] = missing
    return result


def rerigged(gains: dict, mod_atlases: dict[str, bytes], mod_skeletons: dict[str, bytes],
             official_atlases: dict[str, bytes], official_skeletons: dict[str, bytes],
             textures: Iterable[str]) -> dict[str, str | None]:
    """The official atlases a mod's own atlas should give way to (``rig_gains``), each with its official skeleton:
    the mod's skeleton lacks animations the official one has, or the mod brings no skeleton and its atlas lacks
    regions the official skeleton draws. Only atlases the mod has a page texture for (``textures``: texture keys)."""
    textures = set(textures)
    skeleton_of = pair_atlases(official_skeletons, official_atlases)
    carried = set(pair_atlases(mod_skeletons, official_skeletons))
    result = {}
    for name, source in pair_atlases(mod_atlases, official_atlases).items():
        skeleton = skeleton_of.get(name)
        if skeleton in gains["animations"] or (skeleton not in carried and name in gains["regions"]):
            try:
                pages = parse_atlas(mod_atlases[source], source).pages
            except (ValueError, UnicodeError):
                continue
            if any(texture_key(page) in textures for page in pages):
                result[name] = skeleton
    return result


def same_rig(mod: bytes, official: bytes) -> bool:
    """Whether a mod's skeleton declares nothing the official one lacks (bones with their parents, slots with
    their bones, animations): an earlier official skeleton, as far as names tell."""
    try:
        left, right = spine_names.read(mod), spine_names.read(official)
    except ValueError:
        return False
    return (set(left.bones) <= set(right.bones) and set(left.slots) <= set(right.slots)
            and set(left.animations) <= set(right.animations))


def _hulls_of(skeleton: bytes | None) -> dict | None:
    if skeleton is None:
        return None
    try:
        return spine_names.hulls(skeleton)
    except ValueError:
        return None


def prepare_patch(mod: Inputs, official: Inputs) -> tuple[Patch, dict]:
    pairs = pair_atlases(mod.atlases, official.atlases)
    if not pairs:
        raise RemapError("atlas_remap_atlas_missing", file=", ".join(official.atlases) or "*.atlas")
    report = {"moved": [], "added": [], "official": [], "changed": [], "dropped": [], "upscaled": [],
              "warnings": []}
    current_pages = {name: Image.new("RGBA", image.size) for name, image in official.textures.items()}
    patch = {official.keys[name]: image for name, image in current_pages.items() if name in official.keys}
    skeleton_pairs = pair_atlases(official.skeletons, official.atlases)
    mod_skeletons = pair_atlases(mod.skeletons, mod.atlases)
    for name, text in official.atlases.items():
        target = parse_atlas(text, name)
        skeleton = official.skeletons.get(skeleton_pairs.get(name, ""))
        official_hulls = _hulls_of(skeleton)
        required = skeleton_regions({name: skeleton}) if skeleton is not None else None
        if name not in pairs:
            # An atlas the mod has no counterpart of keeps the official pixels of its regions.
            for region in target.regions.values():
                key = texture_key(region.page)
                if key not in official.keys or key not in official.textures:
                    raise RemapError("atlas_remap_page_missing", file=name, page=region.page)
                image, destination = official.textures[key], current_pages[key]
                cut = image.crop(_box(region, target.pages[region.page], image, target.file))
                box = _box(region, target.pages[region.page], destination, target.file)
                size = box[2] - box[0], box[3] - box[1]
                outline, mask = _outlines(official_hulls, region.name), None
                if outline is not None:
                    mask = _rotate(_hull_mask(region, outline, size[::-1] if region.degrees % 180 else size),
                                   region.degrees)
                destination.paste(cut if cut.size == size else cut.resize(size, Image.Resampling.LANCZOS), box[:2],
                                  mask)
                report["official"].append(f"{name} - {region.label}")
            continue
        source = parse_atlas(mod.atlases[pairs[name]], pairs[name])
        if skeleton is not None and required is None:
            # Without a binary attachment reader, require every source and target region.
            required = {r.name for atlas in (source, target) for r in atlas.regions.values()}
        hulls = mod.hulls.get(pairs[name])
        if hulls is None:
            hulls = _hulls_of(mod.skeletons.get(mod_skeletons.get(pairs[name], "")))
        pages, summary = remap_pages(source, target, mod.textures, official.textures, required, fill=True,
                                     hulls=hulls, official_hulls=official_hulls)
        report["upscaled"].extend({"atlas": name, **page} for page in summary["upscaled"])
        for page, image in pages.items():
            key = texture_key(page)
            # Several atlases can share a page. Copy only their rectangles, including transparent pixels.
            if key not in official.keys:
                raise RemapError("atlas_remap_page_missing", file=name, page=page)
            destination = current_pages[key]
            if destination.size != image.size:
                destination = current_pages[key] = destination.resize(image.size, Image.Resampling.LANCZOS)
            for region in target.regions.values():
                if region.page == page:
                    box = _box(region, target.pages[page], image, target.file)
                    destination.paste(image.crop(box), box[:2])
            patch[official.keys[key]] = destination
        for key in ("moved", "added", "official", "changed", "dropped"):
            report[key].extend(f"{name} - {region}" for region in summary[key])
    for name in mod.atlases.keys() - set(pairs.values()):
        report["dropped"].extend(f"{name} - {r.label}" for r in parse_atlas(mod.atlases[name], name).regions.values())
    # Art the game drew anew under the same name cannot be told from art exported on another canvas: the result
    # needs a look.
    report["partial"] = bool(report["warnings"] or report["changed"])
    report["unchanged"] = (
        set(current_pages) == set(mod.textures) and all(
            image.size == mod.textures[key].size and image.tobytes() == mod.textures[key].tobytes()
            for key, image in current_pages.items()))
    return patch, report
