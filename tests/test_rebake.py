"""The world-space rebake of a portrait's pages, on synthetic geometry and images (no game files)."""

import math

import numpy as np
import pytest
from PIL import Image

from bmpt import rebake
from bmpt.atlas_remap import RemapError, parse_atlas

RED, GREEN, BLUE, WHITE = (220, 30, 30, 255), (30, 200, 40, 255), (40, 50, 210, 255), (250, 250, 250, 255)


def atlas(regions: dict[str, tuple[int, int, int, int]], size: int = 64):
    text = f"page.png\nsize: {size},{size}\npma: false\n" + "".join(
        f"{name}\n bounds: {x},{y},{w},{h}\n" for name, (x, y, w, h) in regions.items())
    return parse_atlas(text, "test.atlas")


def quad(slot, region, world, box, size=64, blend=0, alpha=1.0, turn=0):
    """An attachment drawing the page rectangle ``box`` (x, y, w, h in texels) on the world rectangle ``world``
    (x0, y0, x1, y1, y up); ``turn`` rotates the picture on the page by quarter turns."""
    x0, y0, x1, y1 = world
    bx, by, bw, bh = box
    corners = [(bx, by + bh), (bx + bw, by + bh), (bx + bw, by), (bx, by)]  # bottom left, bottom right, top right, top left
    corners = corners[turn:] + corners[:turn]
    return {"slot": slot, "region": region, "page": "page.png", "blend": blend, "alpha": alpha,
            "vertices": [x0, y0, x1, y0, x1, y1, x0, y1],
            "uvs": [v / size for corner in corners for v in corner], "triangles": [0, 1, 2, 2, 3, 0]}


def geometry(slots, poses):
    return {"slots": slots, "poses": poses}


def world_of(item, x, y, size=64):
    """The world point a page texel centre of a quad attachment is drawn at."""
    uv, v = np.array(item["uvs"]).reshape(4, 2) * size, np.array(item["vertices"]).reshape(4, 2)
    p = np.array([x + 0.5, y + 0.5])
    a, b = np.linalg.solve(np.column_stack([uv[1] - uv[0], uv[3] - uv[0]]), p - uv[0])
    return v[0] + a * (v[1] - v[0]) + b * (v[3] - v[0])


def paint(items, picture, size=64, base=(0, 0, 0, 0)):
    """A page whose quads show ``picture(wx, wy)`` (an RGBA tuple) at the world points they are drawn at."""
    page = np.zeros((size, size, 4), dtype=np.uint8)
    page[:] = base
    for item in items:
        uv = np.array(item["uvs"]).reshape(4, 2) * size
        for y in range(int(uv[:, 1].min()), int(math.ceil(uv[:, 1].max()))):
            for x in range(int(uv[:, 0].min()), int(math.ceil(uv[:, 0].max()))):
                page[y, x] = picture(*world_of(item, x, y, size))
    return Image.fromarray(page, "RGBA")


def quadrants(wx, wy):
    return (RED if wy >= 4 else BLUE) if wx < 4 else (GREEN if wy >= 4 else WHITE)


def texels(page: Image.Image, item, size=64):
    """The texels of a quad on a page, each with its world point, away from the picture's edges and seams."""
    uv = np.array(item["uvs"]).reshape(4, 2) * size
    array = np.asarray(page)
    for y in range(int(uv[:, 1].min()), int(math.ceil(uv[:, 1].max()))):
        for x in range(int(uv[:, 0].min()), int(math.ceil(uv[:, 0].max()))):
            yield world_of(item, x, y, size), tuple(int(v) for v in array[y, x])


def clear_of(point, lines, margin=0.75):
    return all(abs(point[axis] - value) > margin for axis, value in lines)


def make(value, regions, size=64):
    return rebake.rig(value, atlas(regions, size))


def test_the_same_rig_on_both_sides_gives_the_official_page_back_at_any_source_size():
    regions = {"body": (2, 2, 16, 16), "face": (30, 4, 8, 8)}
    items = [quad("body", "body", (0, 0, 8, 8), regions["body"]), quad("face", "face", (2, 4, 6, 8), regions["face"])]
    rig = make(geometry(["body", "face"], {"": items}), regions)
    official = paint(items[:1], quadrants, base=(9, 9, 9, 0))
    official = Image.fromarray(np.maximum(np.asarray(official), np.asarray(paint(items[1:], lambda x, y: GREEN))))
    pages, report = rebake.bake(rig, rig, {"page": official}, {"page": official})
    assert np.array_equal(np.asarray(pages["page"]), np.asarray(official))
    assert report["moved"] == ["body", "face"] and report["official"] == [] and not report["partial"]
    larger = official.resize((128, 128), Image.Resampling.NEAREST)
    pages, _ = rebake.bake(rig, rig, {"page": larger}, {"page": official})
    assert np.array_equal(np.asarray(pages["page"]), np.asarray(official))
    assert rebake.overlap(rig, rig, {"page": official}, {"page": official}) == 1.0


def test_a_region_moved_rotated_and_scaled_takes_its_own_pixels():
    old = {"body": (0, 0, 8, 8)}
    new = {"body": (20, 30, 16, 16)}
    source_item = quad("body", "body", (0, 0, 8, 8), old["body"])
    target_item = quad("body", "body", (0, 0, 8, 8), new["body"], turn=1)
    source = make(geometry(["body"], {"": [source_item]}), old)
    target = make(geometry(["body"], {"": [target_item]}), new)
    mod = paint([source_item], quadrants)
    official = paint([target_item], lambda x, y: (128, 128, 128, 255))
    pages, report = rebake.bake(source, target, {"page": mod}, {"page": official})
    checked = 0
    for point, colour in texels(pages["page"], target_item):
        if clear_of(point, [(0, 4), (1, 4)]) and 0.75 < point[0] < 7.25 and 0.75 < point[1] < 7.25:
            assert colour == quadrants(*point), (point, colour)
            checked += 1
    assert checked > 50 and report["moved"] == ["body"]
    # what no region draws keeps the official pixels
    assert np.array_equal(np.asarray(pages["page"])[:16, :16], np.asarray(official)[:16, :16])


def test_a_body_cut_into_other_layers_under_other_names_is_found_by_its_place():
    old = {"body": (0, 0, 16, 8)}
    new = {"upper_new": (40, 2, 16, 4), "lower_new": (4, 40, 16, 4)}
    source_item = quad("body", "body", (0, 0, 8, 8), old["body"])
    upper = quad("upper", "upper_new", (0, 4, 8, 8), new["upper_new"])
    lower = quad("lower", "lower_new", (0, 0, 8, 4), new["lower_new"])
    source = make(geometry(["body"], {"": [source_item]}), old)
    target = make(geometry(["lower", "upper"], {"": [lower, upper]}), new)
    pages, report = rebake.bake(source, target, {"page": paint([source_item], quadrants)},
                                {"page": paint([upper, lower], lambda x, y: (128, 128, 128, 255))})
    for item in (upper, lower):
        for point, colour in texels(pages["page"], item):
            if clear_of(point, [(0, 4), (1, 4)]) and 0.75 < point[0] < 7.25 and 0.75 < point[1] < 7.25:
                assert colour == quadrants(*point)
    assert report["moved"] == ["lower_new", "upper_new"]


def expression_rigs(target_extra=()):
    old = {"body": (0, 0, 16, 16), "face_a": (20, 0, 8, 8), "face_b": (30, 0, 8, 8)}
    new = {"torso": (0, 30, 16, 16), "eyes_00": (40, 40, 8, 8), "eyes_01": (50, 40, 8, 8), "eyes_02": (40, 50, 8, 8)}
    body, face_a, face_b = (quad("body", "body", (0, 0, 8, 8), old["body"]),
                            quad("face", "face_a", (2, 2, 6, 6), old["face_a"]),
                            quad("face", "face_b", (2, 2, 6, 6), old["face_b"]))
    torso, eyes = quad("body", "torso", (0, 0, 8, 8), new["torso"]), {
        name: quad("face", f"eyes_{name}", (2, 2, 6, 6), new[f"eyes_{name}"]) for name in ("00", "01", "02")}
    source = make(geometry(["body", "face"], {"": [body, face_a], "01": [body, face_b]}), old)
    poses = {"": [torso, eyes["00"]], "01": [torso, eyes["01"]]}
    for name in target_extra:
        poses[name] = [torso, eyes[name]]
    target = make(geometry(["body", "face"], poses), new)
    mod = Image.fromarray(np.maximum.reduce([np.asarray(paint([body], lambda x, y: RED)),
                                             np.asarray(paint([face_a], lambda x, y: GREEN)),
                                             np.asarray(paint([face_b], lambda x, y: BLUE))]))
    official = Image.fromarray(np.full((64, 64, 4), (128, 128, 128, 255), dtype=np.uint8))
    return source, target, mod, official, torso, eyes


def test_an_expression_takes_the_pose_of_its_name_and_the_body_keeps_its_own_paint():
    source, target, mod, official, torso, eyes = expression_rigs()
    pages, report = rebake.bake(source, target, {"page": mod}, {"page": official})
    assert {colour for _point, colour in texels(pages["page"], eyes["00"])} == {GREEN}
    assert {colour for _point, colour in texels(pages["page"], eyes["01"])} == {BLUE}
    # the body under the face is the body's own paint, not the expression's
    assert {colour for _point, colour in texels(pages["page"], torso)} == {RED}
    assert report["moved"] == ["eyes_00", "eyes_01", "torso"]


def test_an_expression_only_the_official_rig_has_keeps_the_official_pixels_and_is_reported():
    source, target, mod, official, _torso, eyes = expression_rigs(target_extra=("02",))
    pages, report = rebake.bake(source, target, {"page": mod}, {"page": official})
    assert {colour for _point, colour in texels(pages["page"], eyes["02"])} == {(128, 128, 128, 255)}
    assert report["official"] == ["eyes_02"] and report["partial"]


def test_paint_hidden_under_another_layer_comes_from_the_layer_of_the_same_depth():
    old = {"back": (0, 0, 8, 8), "front": (10, 0, 8, 8)}
    new = {"back_new": (30, 30, 8, 8), "front_new": (40, 30, 8, 8)}
    back, front = quad("back", "back", (0, 0, 8, 8), old["back"]), quad("front", "front", (0, 0, 8, 8), old["front"])
    back2, front2 = (quad("back", "back_new", (0, 0, 8, 8), new["back_new"]),
                     quad("front", "front_new", (0, 0, 8, 8), new["front_new"]))
    source = make(geometry(["back", "front"], {"": [back, front]}), old)
    target = make(geometry(["back", "front"], {"": [back2, front2]}), new)
    mod = Image.fromarray(np.maximum(np.asarray(paint([back], lambda x, y: BLUE)),
                                     np.asarray(paint([front], lambda x, y: RED))))
    official = Image.fromarray(np.full((64, 64, 4), (128, 128, 128, 255), dtype=np.uint8))
    pages, _report = rebake.bake(source, target, {"page": mod}, {"page": official})
    assert {colour for _point, colour in texels(pages["page"], back2)} == {BLUE}
    assert {colour for _point, colour in texels(pages["page"], front2)} == {RED}


def test_an_additive_layer_takes_the_additive_layer_of_the_source():
    old = {"body": (0, 0, 8, 8), "glow": (10, 0, 8, 8)}
    new = {"body_new": (30, 30, 8, 8), "glow_new": (40, 30, 8, 8)}
    body, glow = quad("body", "body", (0, 0, 8, 8), old["body"]), quad("glow", "glow", (0, 0, 8, 8), old["glow"], blend=1)
    body2, glow2 = (quad("body", "body_new", (0, 0, 8, 8), new["body_new"]),
                    quad("glow", "glow_new", (0, 0, 8, 8), new["glow_new"], blend=1))
    source = make(geometry(["body", "glow"], {"": [body, glow]}), old)
    target = make(geometry(["body", "glow"], {"": [body2, glow2]}), new)
    mod = Image.fromarray(np.maximum(np.asarray(paint([body], lambda x, y: RED)),
                                     np.asarray(paint([glow], lambda x, y: WHITE))))
    official = Image.fromarray(np.full((64, 64, 4), (128, 128, 128, 255), dtype=np.uint8))
    pages, _report = rebake.bake(source, target, {"page": mod}, {"page": official})
    assert {colour for _point, colour in texels(pages["page"], glow2)} == {WHITE}
    assert {colour for _point, colour in texels(pages["page"], body2)} == {RED}


def test_a_portrait_drawn_anew_elsewhere_is_refused_before_anything_is_baked():
    regions = {"body": (0, 0, 16, 16)}
    left, right = quad("body", "body", (0, 0, 40, 40), regions["body"]), quad("body", "body", (35, 0, 75, 40), regions["body"])
    source = make(geometry(["body"], {"": [left]}), regions)
    target = make(geometry(["body"], {"": [right]}), regions)
    page = paint([left], lambda x, y: RED)
    assert rebake.overlap(source, target, {"page": page}, {"page": page}) < rebake.REDRAWN_BELOW
    with pytest.raises(RemapError) as error:
        rebake.bake(source, target, {"page": page}, {"page": page})
    assert error.value.code == "atlas_remap_redrawn"


def valid():
    return geometry(["body"], {"": [quad("body", "body", (0, 0, 8, 8), (0, 0, 8, 8))]})


def broken(change):
    value = valid()
    change(value, value["poses"][""][0])
    return value


@pytest.mark.parametrize("change", [
    lambda g, item: g.pop("slots"),
    lambda g, item: g["poses"].pop(""),
    lambda g, item: g["slots"].append(7),
    lambda g, item: item.update(slot="other"),
    lambda g, item: item.update(region="elsewhere"),
    lambda g, item: item.update(page="other.png"),
    lambda g, item: item.update(blend=9),
    lambda g, item: item.update(blend=True),
    lambda g, item: item.update(alpha=2),
    lambda g, item: item["vertices"].__setitem__(0, float("nan")),
    lambda g, item: item["vertices"].__setitem__(0, float("inf")),
    lambda g, item: item["vertices"].__setitem__(0, "1"),
    lambda g, item: item["vertices"].__setitem__(0, 1e9),
    lambda g, item: item["vertices"].pop(),
    lambda g, item: item["uvs"].__setitem__(0, 1.5),
    lambda g, item: item["uvs"].__setitem__(0, -0.2),
    lambda g, item: item["triangles"].__setitem__(0, 4),
    lambda g, item: item["triangles"].__setitem__(0, -1),
    lambda g, item: item["triangles"].__setitem__(0, 1.0),
    lambda g, item: item["triangles"].pop(),
    lambda g, item: g["poses"].update(extra="pose"),
], ids=lambda change: str(change.__code__.co_firstlineno))
def test_untrusted_geometry_is_refused(change):
    with pytest.raises(RemapError) as error:
        rebake.rig(broken(change), atlas({"body": (0, 0, 8, 8)}))
    assert (error.value.code, error.value.params) == ("bad_params", {"key": "geometry"})


def test_counts_are_capped(monkeypatch):
    regions = atlas({"body": (0, 0, 8, 8)})
    assert rebake.rig(valid(), regions).poses[""][0].region == "body"
    for cap, grow in (("MAX_POSES", lambda g: g["poses"].update({"01": g["poses"][""]})),
                      ("MAX_DRAWN", lambda g: g["poses"][""].append(dict(g["poses"][""][0]))),
                      ("MAX_SLOTS", lambda g: g["slots"].append("face"))):
        value = valid()
        grow(value)
        monkeypatch.setattr(rebake, cap, 1)
        with pytest.raises(RemapError):
            rebake.rig(value, regions)
        monkeypatch.undo()
    monkeypatch.setattr(rebake, "MAX_NUMBERS", 10)
    with pytest.raises(RemapError):
        rebake.rig(valid(), regions)
