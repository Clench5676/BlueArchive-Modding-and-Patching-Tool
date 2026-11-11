"""Move a portrait mod's pixels onto the official rig's pages through world space.

The page posts what both rigs draw, pose by pose (``web/js/rig-geometry.js``, the runtime's public API): per drawn
slot its region, page, blend, alpha, world vertices, page UVs and triangles. For every texel a region of the official
rig draws, its world point is where the official rig puts it; the mod's pixels at that point are the ones the rig the
mod's textures were drawn for shows there. Both rigs draw the same picture at the same place, so this holds however
the update repacked the atlas, cut the art into other layers or renamed regions. Only pixels are mapped here; nothing
in this module poses a skeleton.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .atlas_remap import Atlas, RemapError, texture_key

#: below this overlap of the two rest pictures' silhouettes the game drew the portrait anew: nothing can be moved
REDRAWN_BELOW = 0.75
#: pixels per world unit of the silhouettes the overlap compares
GATE_SCALE = 0.4
#: world units around the two rest pictures in the silhouettes
GATE_MARGIN = 20
#: a region fewer of whose texels than this share the source rig holds keeps the official pixels whole
HELD_BELOW = 0.5

# Caps of the posted geometry, about ten times the largest official portrait (92 animations, 152 slots, 6036 mesh
# vertices and 7326 triangles in one skeleton).
MAX_POSES = 1000
MAX_SLOTS = 1600
MAX_DRAWN = 1600
MAX_VERTICES = 120_000  # numbers per attachment
MAX_TRIANGLES = 220_000  # indices per attachment
MAX_NUMBERS = 12_000_000  # per rig
MAX_COORDINATE = 1e6
MAX_GATE_PIXELS = 16_000_000
#: how far UVs may lie off the page: an official portrait has a mesh 0.03 off it (sampling clamps at the edges)
UV_SLACK = 0.1


@dataclass(frozen=True)
class Drawn:
    slot: str
    region: str
    page: str  # texture key of the page
    blend: int
    alpha: float
    vertices: np.ndarray  # (n, 2) world
    uvs: np.ndarray  # (n, 2) page-normalised
    triangles: np.ndarray  # (m, 3)


@dataclass(frozen=True)
class Rig:
    slots: tuple[str, ...]
    poses: dict[str, tuple[Drawn, ...]]


def _bad() -> RemapError:
    return RemapError("bad_params", key="geometry")


def _numbers(value, limit: int, *, integer: bool = False) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise _bad()
    allowed = (int,) if integer else (int, float)
    for number in value:
        if type(number) not in allowed or (not integer and not math.isfinite(number)):
            raise _bad()
    return value


def rig(value, atlas: Atlas, max_poses: int | None = None) -> Rig:
    """The posted geometry of one rig, checked against the atlas the back end read itself (untrusted input: counts
    capped, numbers finite, UVs on the page, triangle indices in range, regions and pages of that atlas). Refused
    with ``bad_params``. ``max_poses``: the most poses taken (a graft's test rig asks for one per candidate)."""
    if not isinstance(value, dict) or not isinstance(value.get("slots"), list) or not isinstance(value.get("poses"), dict):
        raise _bad()
    slots = value["slots"]
    if len(slots) > MAX_SLOTS or not all(isinstance(slot, str) for slot in slots):
        raise _bad()
    known = set(slots)
    pages = {texture_key(page) for page in atlas.pages}
    regions = {(texture_key(page), name) for page, name, _index in atlas.regions}
    poses, total = {}, 0
    if len(value["poses"]) > (MAX_POSES if max_poses is None else max_poses) or "" not in value["poses"]:
        raise _bad()
    for name, drawn in value["poses"].items():
        if not isinstance(name, str) or not isinstance(drawn, list) or len(drawn) > MAX_DRAWN:
            raise _bad()
        items = []
        for item in drawn:
            if not isinstance(item, dict):
                raise _bad()
            slot, region, page = item.get("slot"), item.get("region"), item.get("page")
            if not (isinstance(slot, str) and slot in known and isinstance(region, str) and isinstance(page, str)):
                raise _bad()
            page = texture_key(page)
            if page not in pages or (page, region) not in regions:
                raise _bad()
            blend, alpha = item.get("blend"), item.get("alpha")
            if type(blend) is not int or not 0 <= blend <= 3 or type(alpha) not in (int, float) \
                    or not 0 <= alpha <= 1:
                raise _bad()
            vertices = _numbers(item.get("vertices"), MAX_VERTICES)
            uvs = _numbers(item.get("uvs"), MAX_VERTICES)
            triangles = _numbers(item.get("triangles"), MAX_TRIANGLES, integer=True)
            count = len(vertices) // 2
            if len(vertices) % 2 or len(uvs) != len(vertices) or count < 3 or len(triangles) % 3:
                raise _bad()
            total += len(vertices) + len(uvs) + len(triangles)
            if total > MAX_NUMBERS:
                raise _bad()
            v = np.asarray(vertices, dtype=np.float64).reshape(-1, 2)
            uv = np.asarray(uvs, dtype=np.float64).reshape(-1, 2)
            t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
            if np.abs(v).max() > MAX_COORDINATE or uv.min() < -UV_SLACK or uv.max() > 1 + UV_SLACK \
                    or (t.size and (t.min() < 0 or t.max() >= count)):
                raise _bad()
            items.append(Drawn(slot, region, page, blend, float(alpha), v, uv, t))
        poses[name] = tuple(items)
    return Rig(tuple(slots), poses)


def _premultiplied(image: Image.Image) -> np.ndarray:
    page = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    page[..., :3] *= page[..., 3:4]
    return page


def _sample(page: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear, at page-normalised coordinates, clamped at the edges."""
    h, w = page.shape[:2]
    x, y = u * w - 0.5, v * h - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0).astype(np.float32), (y - y0).astype(np.float32)
    if page.ndim == 3:
        fx, fy = fx[:, None], fy[:, None]

    def at(ix, iy):
        return page[np.clip(iy, 0, h - 1), np.clip(ix, 0, w - 1)]

    return (at(x0, y0) * (1 - fx) * (1 - fy) + at(x0 + 1, y0) * fx * (1 - fy)
            + at(x0, y0 + 1) * (1 - fx) * fy + at(x0 + 1, y0 + 1) * fx * fy)


def _bary(px, py, a, b, c):
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(d) < 1e-12:
        return None
    l1 = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
    l2 = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
    return l1, l2, 1 - l1 - l2


def _switching(rig: Rig) -> set[str]:
    """The slots that do not draw the same region in every pose: another region, or none."""
    seen: dict[str, set] = {slot: set() for slot in rig.slots}
    for drawn in rig.poses.values():
        regions = {item.slot: item.region for item in drawn}
        for slot, found in seen.items():
            found.add(regions.get(slot))
    return {slot for slot, found in seen.items() if len(found) > 1}


def _class(item: Drawn, switching: set[str]) -> str:
    if item.slot in switching:
        return "switch"
    return "static" if item.blend == 0 else f"blend{item.blend}"


def _texels(item: Drawn, width: int, height: int):
    """The page texels inside an attachment's triangles (their centres), each with the world point it is drawn at."""
    texels_x, texels_y, world_x, world_y = [], [], [], []
    for triangle in item.triangles:
        p = item.uvs[triangle] * (width, height)
        x0, x1 = max(0, int(np.floor(p[:, 0].min()))), min(width, int(np.ceil(p[:, 0].max())))
        y0, y1 = max(0, int(np.floor(p[:, 1].min()))), min(height, int(np.ceil(p[:, 1].max())))
        if x1 <= x0 or y1 <= y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
        weights = _bary(gx + 0.5, gy + 0.5, p[0], p[1], p[2])
        if weights is None:
            continue
        inside = (weights[0] >= -1e-3) & (weights[1] >= -1e-3) & (weights[2] >= -1e-3)
        if not inside.any():
            continue
        l0, l1, l2 = (w[inside] for w in weights)
        w = item.vertices[triangle]
        texels_x.append(gx[inside])
        texels_y.append(gy[inside])
        world_x.append(l0 * w[0, 0] + l1 * w[1, 0] + l2 * w[2, 0])
        world_y.append(l0 * w[0, 1] + l1 * w[1, 1] + l2 * w[2, 1])
    if not texels_x:
        return None
    px, py, wx, wy = (np.concatenate(values) for values in (texels_x, texels_y, world_x, world_y))
    # a texel two triangles share is taken once
    _unique, first = np.unique(py * width + px, return_index=True)
    return px[first], py[first], wx[first], wy[first]


def _pick(item: Drawn, kind: str, statics: list[Drawn], pose: str, source: Rig, switching: set[str]) -> list[Drawn]:
    """The source attachments a target attachment takes its pixels from, first first: ``switch`` from the source
    pose of the same name, top of the draw order first (none without such a pose); the others from the source's
    rest pose, of the same class, static layers in order of stratum distance (rank among the rig's static
    attachments, as a fraction), so a layer takes its own depth first and keeps paint hidden under others."""
    if kind == "switch":
        drawn = source.poses.get(pose)
        return [d for d in reversed(drawn) if _class(d, switching) == "switch"] if drawn else []
    candidates = [d for d in source.poses[""] if _class(d, switching) == kind]
    if kind == "static" and len(statics) > 1 and len(candidates) > 1:
        rank = statics.index(item) / (len(statics) - 1)
        order = sorted(range(len(candidates)), key=lambda i: abs(i / (len(candidates) - 1) - rank))
        return [candidates[i] for i in order]
    return list(reversed(candidates))


def _gather(candidates: list[Drawn], pages: dict[str, np.ndarray], wx: np.ndarray, wy: np.ndarray):
    """The candidates' pixels at the world points, composited under each other until opaque (premultiplied), and
    which points any of them holds."""
    acc = np.zeros((len(wx), 4), dtype=np.float32)
    held = np.zeros(len(wx), dtype=bool)
    for candidate in candidates:
        page = pages.get(candidate.page)
        if page is None:
            continue
        todo = acc[:, 3] < 0.999
        if not todo.any():
            break
        got = np.zeros(len(wx), dtype=bool)
        colour = np.zeros((len(wx), 4), dtype=np.float32)
        for triangle in candidate.triangles:
            w = candidate.vertices[triangle]
            near = todo & ~got & (wx >= w[:, 0].min()) & (wx <= w[:, 0].max()) \
                & (wy >= w[:, 1].min()) & (wy <= w[:, 1].max())
            if not near.any():
                continue
            weights = _bary(wx[near], wy[near], w[0], w[1], w[2])
            if weights is None:
                continue
            inside = (weights[0] >= -1e-4) & (weights[1] >= -1e-4) & (weights[2] >= -1e-4)
            if not inside.any():
                continue
            index = np.flatnonzero(near)[inside]
            l0, l1, l2 = (x[inside] for x in weights)
            q = candidate.uvs[triangle]
            colour[index] = _sample(page, l0 * q[0, 0] + l1 * q[1, 0] + l2 * q[2, 0],
                                    l0 * q[0, 1] + l1 * q[1, 1] + l2 * q[2, 1])
            got[index] = True
        acc[got] += (1 - acc[got, 3:4]) * colour[got]
        held |= got
    return acc, held


def _frame(*drawn: tuple[Drawn, ...]) -> tuple[float, float, int, int]:
    points = [item.vertices for items in drawn for item in items]
    if not points:
        return 0.0, 0.0, 1, 1
    every = np.concatenate(points)
    x0, y0 = every.min(axis=0) - GATE_MARGIN
    x1, y1 = every.max(axis=0) + GATE_MARGIN
    width, height = int(np.ceil((x1 - x0) * GATE_SCALE)), int(np.ceil((y1 - y0) * GATE_SCALE))
    if width * height > MAX_GATE_PIXELS:
        raise _bad()
    return float(x0), float(y1), width, height


def _silhouette(drawn: tuple[Drawn, ...], pages: dict[str, np.ndarray], frame) -> np.ndarray:
    """What a pose covers, as alpha at ``GATE_SCALE``: its normal-blend attachments of alpha above 0.5."""
    left, top, width, height = frame
    out = np.zeros((height, width), dtype=np.float32)
    for item in drawn:
        page = pages.get(item.page)
        if item.blend != 0 or item.alpha <= 0.5 or page is None:
            continue
        sx, sy = (item.vertices[:, 0] - left) * GATE_SCALE, (top - item.vertices[:, 1]) * GATE_SCALE
        for triangle in item.triangles:
            px, py = sx[triangle], sy[triangle]
            a0, a1 = max(0, int(np.floor(px.min()))), min(width, int(np.ceil(px.max())))
            b0, b1 = max(0, int(np.floor(py.min()))), min(height, int(np.ceil(py.max())))
            if a1 <= a0 or b1 <= b0:
                continue
            gx, gy = np.meshgrid(np.arange(a0, a1), np.arange(b0, b1))
            weights = _bary(gx + 0.5, gy + 0.5, (px[0], py[0]), (px[1], py[1]), (px[2], py[2]))
            if weights is None:
                continue
            inside = (weights[0] >= 0) & (weights[1] >= 0) & (weights[2] >= 0)
            if not inside.any():
                continue
            l0, l1, l2 = (w[inside] for w in weights)
            q = item.uvs[triangle]
            alpha = _sample(page, l0 * q[0, 0] + l1 * q[1, 0] + l2 * q[2, 0],
                            l0 * q[0, 1] + l1 * q[1, 1] + l2 * q[2, 1]) * item.alpha
            yy, xx = gy[inside], gx[inside]
            out[yy, xx] = alpha + (1 - alpha) * out[yy, xx]
    return out


def _alpha_pages(pages: dict[str, Image.Image]) -> dict[str, np.ndarray]:
    return {key: np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
            for key, image in pages.items()}


def overlap(source: Rig, target: Rig, source_pages: dict[str, Image.Image],
            official_pages: dict[str, Image.Image]) -> float:
    """How far the two rest pictures cover the same place: the IoU of their alpha silhouettes (``_silhouette``).
    Pages are keyed by texture key."""
    frame = _frame(source.poses[""], target.poses[""])
    left = _silhouette(source.poses[""], _alpha_pages(source_pages), frame) > 0.5
    right = _silhouette(target.poses[""], _alpha_pages(official_pages), frame) > 0.5
    union = int((left | right).sum())
    return float((left & right).sum() / union) if union else 1.0


def bake(source: Rig, target: Rig, source_pages: dict[str, Image.Image],
         official_pages: dict[str, Image.Image]) -> tuple[dict[str, Image.Image], dict]:
    """The official pages with every region the target rig draws filled from the source pages through world space
    (pages keyed by texture key, straight alpha as the game's). A texel no source attachment holds keeps the
    official pixel; a region less than half of which the source holds keeps the official pixels whole (``official``
    in the report); page area no attachment draws keeps the official pixels. Refuses a portrait the game drew anew
    (``atlas_remap_redrawn``) before anything is baked."""
    for drawn in target.poses.values():
        for item in drawn:
            if item.page not in official_pages:
                raise RemapError("atlas_remap_page_missing", file="—", page=item.page)
    if overlap(source, target, source_pages, official_pages) < REDRAWN_BELOW:
        raise RemapError("atlas_remap_redrawn")
    sources = {key: _premultiplied(image) for key, image in source_pages.items()}
    officials = {key: np.asarray(image.convert("RGBA"), dtype=np.uint8) for key, image in official_pages.items()}
    output = {key: page.copy() for key, page in officials.items()}
    source_switching, target_switching = _switching(source), _switching(target)
    done: dict[tuple[str, str], str] = {}
    for name in ["", *(name for name in target.poses if name)]:  # the rest pose first
        drawn = target.poses[name]
        statics = [item for item in drawn if _class(item, target_switching) == "static"]
        for item in drawn:
            identity = (item.page, item.region)
            if identity in done:
                continue
            official = officials[item.page]
            height, width = official.shape[:2]
            found = _texels(item, width, height)
            if found is None:
                done[identity] = "empty"
                continue
            px, py, wx, wy = found
            kind = _class(item, target_switching)
            candidates = _pick(item, kind, statics, name, source, source_switching)
            acc, held = _gather(candidates, sources, wx, wy)
            page = output[item.page]
            if held.mean() < HELD_BELOW:
                page[py, px] = official[py, px]
                done[identity] = "official"
                continue
            done[identity] = "baked"
            alpha = acc[held, 3:4]
            colour = np.where(alpha > 0, acc[held, :3] / np.maximum(alpha, 1e-6), 0)
            pixels = (np.clip(np.concatenate([colour, alpha], axis=1), 0, 1) * 255 + 0.5).astype(np.uint8)
            hx, hy = px[held], py[held]
            clear = pixels[:, 3] == 0
            # a texel the mod leaves clear keeps the official colour under it, and no alpha
            pixels[clear, :3] = official[hy[clear], hx[clear], :3]
            page[hy, hx] = pixels
            page[py[~held], px[~held]] = official[py[~held], px[~held]]
    report = {"moved": sorted({region for (_page, region), result in done.items() if result == "baked"}),
              "official": sorted({region for (_page, region), result in done.items() if result == "official"}),
              "added": [], "dropped": [], "upscaled": [], "warnings": [], "unchanged": False}
    report["partial"] = bool(report["official"])
    return {key: Image.fromarray(page, "RGBA") for key, page in output.items()}, report
