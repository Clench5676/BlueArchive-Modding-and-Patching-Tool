"""Synthetic geometry for remap tests of portraits: every region drawn on a world cell of its own name, as two rigs of
one picture draw it after a repack under the same names. In the App, the page posts the real geometry
(``rig-geometry.js`` ``forRemap``)."""

from __future__ import annotations

import zlib

from bmpt.atlas_remap import parse_atlas
from bmpt.library.judge import core_of


def by_name(text: str | bytes) -> dict:
    atlas = parse_atlas(text)
    regions = list(atlas.regions.values())
    slots, drawn = [], []
    for region in regions:
        width, height = atlas.pages[region.page].size
        w, h = region.size[::-1] if region.degrees % 180 else region.size
        x0, y0 = region.xy
        x1, y1 = x0 + w, y0 + h
        corners = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]  # the upright picture's bottom left, bottom right, ...
        turn = region.degrees // 90
        corners = corners[turn:] + corners[:turn]
        (ow, oh), (ox, oy), (rw, rh) = region.original, region.offset, region.size
        left = zlib.crc32(region.name.encode()) % 4999 * 200  # the same place for the same name in any atlas
        wx0, wx1 = left + ox / ow * 100, left + (ox + rw) / ow * 100
        wy0, wy1 = oy / oh * 100, (oy + rh) / oh * 100
        slots.append(region.label)
        drawn.append({"slot": region.label, "region": region.name, "page": region.page, "blend": 0, "alpha": 1.0,
                      "vertices": [wx0, wy0, wx1, wy0, wx1, wy1, wx0, wy1],
                      "uvs": [v / size for corner in corners for v, size in zip(corner, (width, height))],
                      "triangles": [0, 1, 2, 2, 3, 0]})
    return {"slots": slots, "poses": {"": drawn}}


def geometry(library, mod_id: str) -> dict:
    """What the page would post for a library mod's remap, from the atlases the library chooses."""
    record = library._record(mod_id)
    paths = library._paths(record)
    pin = library.current_pin()
    targets = library._spine_parts(core_of(paths[0].name), pin)
    originals = {target: library._official_for(target, pin) for target in targets}
    inputs = library._remap_view(record, paths, targets, originals, pin)
    return {name: {"source": by_name(source), "target": by_name(inputs["official"].atlases[name])}
            for name, source in library._rebake_plan(inputs).items()}
