"""Texture sizes are bounded before any decoder runs: the shared check, then every place the App decodes a texture."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from UnityPy.classes import Texture2D
from UnityPy.enums import BuildTarget, TextureFormat as TF
from UnityPy.export import Texture2DConverter
from UnityPy.helpers import ResourceReader, TextureSwizzler

from ba_modding_toolkit import atlas_remap as remap, refine_assembly, texture_guard as guard
from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.core import extraction
from ba_modding_toolkit.errors import ServiceError
from ba_modding_toolkit.gameassets import AssetCache, GameAssets, model, service
from ba_modding_toolkit.gameassets.spine import extract_spine
from ba_modding_toolkit.library import portrait_edit
from ba_modding_toolkit.models import AssetType
from ba_modding_toolkit.searching import get_search_dirs
from ba_modding_toolkit.utils import no_log

from test_atlas_remap import atlas_text, write_bundle

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
real_install = pytest.mark.skipif(not GAME_DIR.is_dir(), reason="No local game install")

CRUNCHED = {TF.DXT1Crunched, TF.DXT5Crunched, TF.ETC_RGB4Crunched, TF.ETC2_RGBA8Crunched}
# Formats whose decoders read past short input unless the size is checked first, and the ones that only raise
NATIVE = [TF.ETC_RGB4, TF.ETC_RGB4_3DS, TF.ETC_RGBA8_3DS, TF.ETC2_RGB, TF.ETC2_RGBA1, TF.ETC2_RGBA8,
          TF.EAC_R, TF.EAC_R_SIGNED, TF.EAC_RG, TF.EAC_RG_SIGNED, TF.PVRTC_RGB2, TF.PVRTC_RGBA2, TF.PVRTC_RGB4,
          TF.PVRTC_RGBA4, TF.ATC_RGB4, TF.ATC_RGBA8]
OTHER = [TF.DXT1, TF.DXT3, TF.DXT5, TF.BC4, TF.BC5, TF.BC7, TF.ASTC_RGB_4x4, TF.ASTC_RGBA_6x6, TF.ASTC_RGBA_12x12,
         TF.ASTC_HDR_8x8, TF.Alpha8, TF.ARGB4444, TF.RGB24, TF.RGBA32, TF.RGB565, TF.BGRA32, TF.RGBAHalf, TF.R8,
         TF.RGB48]
DECODABLE = sorted(set(Texture2DConverter.CONV_TABLE) - CRUNCHED, key=int)
CRASHING_SITE_FORMAT = int(TF.ETC2_RGBA8)


def texture(width, height, texture_format, data, name="page", **fields):
    """A real Texture2D object with the declared size and data."""
    result = Texture2D(image_data=bytes(data), m_CompleteImageSize=len(data), m_Height=height, m_ImageCount=1,
                       m_IsReadable=True, m_LightmapFormat=0, m_Name=name, m_TextureDimension=2,
                       m_TextureFormat=int(texture_format), m_TextureSettings=None, m_Width=width)
    for key, value in fields.items():
        setattr(result, key, value)
    return result


class Forbidden:
    """Stands in for a native decoder module: any use is a failure."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError(f"decoder used: {name}")


@pytest.fixture
def no_native(monkeypatch):
    stub = Forbidden()
    monkeypatch.setattr(Texture2DConverter, "texture2ddecoder", stub)
    monkeypatch.setattr(Texture2DConverter, "astc_encoder", stub)
    return stub


@pytest.fixture
def no_decoder(monkeypatch, no_native):
    def parse(*args, **kwargs):
        no_native.calls.append("parse_image_data")
        raise AssertionError("decoder used: parse_image_data")
    monkeypatch.setattr(Texture2DConverter, "parse_image_data", parse)
    return no_native


# --- the shared check ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("texture_format", NATIVE + OTHER, ids=lambda f: f.name)
def test_large_declared_size_with_short_data_is_refused_before_any_decoder(no_decoder, texture_format):
    declared = texture(8192, 8192, texture_format, bytes(64))
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(declared)
    with pytest.raises(guard.TextureRefused):
        guard.texture_image(declared)
    assert no_decoder.calls == []


def test_every_decodable_format_has_a_known_size_and_crunched_ones_are_refused(no_decoder):
    assert all(guard.supported(texture_format) for texture_format in DECODABLE)
    assert not any(guard.supported(texture_format) for texture_format in CRUNCHED)
    for texture_format in CRUNCHED:
        with pytest.raises(guard.TextureRefused):
            guard.check_texture(texture(4, 4, texture_format, bytes(1024)))
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(texture(4, 4, 999, bytes(1024)))
    assert no_decoder.calls == []


@pytest.mark.parametrize("texture_format", NATIVE + OTHER, ids=lambda f: f.name)
@pytest.mark.parametrize("size", [(16, 16), (64, 32), (7, 9), (1, 1)], ids=lambda s: "x".join(map(str, s)))
def test_exact_size_is_accepted_and_one_byte_less_is_refused(texture_format, size):
    need = guard.required_size(*size, texture_format)
    assert need >= 1
    guard.check_texture(texture(*size, texture_format, bytes(need)))
    guard.check_texture(texture(*size, texture_format, bytes(need + 5)))
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(texture(*size, texture_format, bytes(need - 1)))


DECODES = [TF.Alpha8, TF.ARGB4444, TF.RGB24, TF.RGBA32, TF.ARGB32, TF.RGB565, TF.BGR24, TF.RGBA4444, TF.BGRA32,
           TF.RGBAHalf, TF.R8, TF.RGB48, TF.DXT1, TF.DXT3, TF.DXT5, TF.BC4, TF.BC5, TF.BC7, TF.ETC_RGB4,
           TF.ETC_RGB4_3DS, TF.ETC_RGBA8_3DS, TF.ETC2_RGB, TF.ETC2_RGBA1, TF.ETC2_RGBA8, TF.EAC_R, TF.EAC_R_SIGNED,
           TF.EAC_RG, TF.EAC_RG_SIGNED, TF.ATC_RGB4, TF.ATC_RGBA8, TF.PVRTC_RGB2, TF.PVRTC_RGBA2, TF.PVRTC_RGB4,
           TF.PVRTC_RGBA4, TF.ASTC_RGB_4x4, TF.ASTC_RGBA_6x6, TF.ASTC_RGBA_10x10, TF.ASTC_HDR_8x8]


@pytest.mark.parametrize("texture_format", DECODES, ids=lambda f: f.name)
def test_valid_small_textures_of_every_family_decode(texture_format):
    # PVRTC decodes power-of-two sizes only
    sizes = [(16, 16), (64, 32)] if texture_format.name.startswith("PVRTC") else [(16, 16), (7, 9), (33, 5)]
    for size in sizes:
        declared = texture(*size, texture_format, bytes(guard.required_size(*size, texture_format)))
        assert guard.texture_image(declared).size == size


def test_a_texture_still_decodes_to_the_same_pixels(tmp_path):
    source = Image.new("RGBA", (13, 7))
    source.putdata([(x * 19 % 256, y * 37 % 256, (x + y) * 11 % 256, 255) for y in range(7) for x in range(13)])
    for texture_format in (TF.RGBA32, TF.DXT5, TF.BC7, TF.ETC2_RGBA8, TF.ASTC_RGBA_6x6):
        data, encoded = Texture2DConverter.image_to_texture2d(source, texture_format)
        declared = texture(13, 7, encoded, data)
        assert guard.texture_image(declared).tobytes() == declared.image.tobytes()


@pytest.mark.parametrize("texture_format", [TF.RGBA32, TF.RGB24, TF.Alpha8, TF.R8, TF.DXT1, TF.DXT5, TF.BC4, TF.BC5,
                                            TF.BC7, TF.ETC_RGB4, TF.ETC2_RGB, TF.ETC2_RGBA8, TF.ASTC_RGB_4x4,
                                            TF.ASTC_RGBA_6x6, TF.ASTC_RGBA_12x12], ids=lambda f: f.name)
@pytest.mark.parametrize("size", [(13, 7), (33, 65), (1, 1)], ids=lambda s: "x".join(map(str, s)))
def test_required_size_matches_what_unitypy_encodes(texture_format, size):
    data, encoded = Texture2DConverter.image_to_texture2d(Image.new("RGBA", size), texture_format)
    assert guard.required_size(*size, encoded) == len(data)


@pytest.mark.parametrize("size", [(0, 4), (4, 0), (0, 0), (-4, 4), (4, -1), (16385, 1), (1, 16385), (None, 4),
                                  (4.0, 4), (True, 4)])
def test_sizes_outside_the_range_are_refused_whatever_the_data(no_decoder, size):
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(texture(*size, TF.RGBA32, bytes(1 << 20)))
    assert no_decoder.calls == []


def test_the_games_font_placeholders_are_refused_without_touching_any_file(monkeypatch, no_decoder):
    # 0x0 with no data and an empty stream path: decoding one makes UnityPy open a path that resolves to a folder
    def resource(*args):
        raise AssertionError("resource read")
    monkeypatch.setattr(ResourceReader, "get_resource_data", resource)
    stream = SimpleNamespace(path="", offset=0, size=0)
    placeholder = texture(0, 0, TF.RGBA32, b"", name="Font Texture", m_StreamData=stream)
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(placeholder)
    with pytest.raises(ServiceError):
        guard.texture_image(placeholder, "font.bundle")
    assert no_decoder.calls == []


def test_the_largest_side_is_accepted():
    guard.check_texture(texture(16384, 1, TF.RGBA32, bytes(16384 * 4)))
    guard.check_texture(texture(1, 16384, TF.R8, bytes(16384)))
    assert guard.MAX_TEXTURE_SIDE == 16384


def test_missing_image_data_is_refused():
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(texture(4, 4, TF.RGBA32, b""))


def test_a_known_file_gives_bundle_invalid_and_none_gives_a_value_error():
    declared = texture(8192, 8192, TF.ETC2_RGB, bytes(64))
    with pytest.raises(ServiceError) as caught:
        guard.check_texture(declared, "mod.bundle")
    assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": "mod.bundle"}
    assert isinstance(caught.value.__cause__, guard.TextureRefused)
    with pytest.raises(ValueError) as caught:
        guard.check_texture(declared)
    assert isinstance(caught.value, guard.TextureRefused) and not isinstance(caught.value, ServiceError)


def test_stream_data_is_read_the_way_unitypy_reads_it(monkeypatch, no_decoder):
    reads = []
    payload = {"data": bytes(64)}

    def resource(path, assets_file, offset, size):
        reads.append((path, offset, size))
        return payload["data"]

    monkeypatch.setattr(ResourceReader, "get_resource_data", resource)
    stream = SimpleNamespace(path="archive:/CAB-test/CAB-test.resS", offset=16, size=64)
    streamed = texture(8192, 8192, TF.ETC2_RGBA8, b"", m_StreamData=stream)
    streamed.object_reader = SimpleNamespace(assets_file=None, version=(2021, 3, 0, 0), platform=BuildTarget.Android)
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(streamed)
    assert reads == [("archive:/CAB-test/CAB-test.resS", 16, 64)]
    # enough streamed data passes (nothing decodes it here)
    small = texture(16, 16, TF.ETC2_RGBA8, b"", m_StreamData=SimpleNamespace(path="CAB-test.resS", offset=0, size=256))
    small.object_reader = streamed.object_reader
    payload["data"] = bytes(256)
    guard.check_texture(small)
    payload["data"] = bytes(255)
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(small)
    assert no_decoder.calls == []


def test_switch_swizzled_sizes_use_the_padded_size_and_a_huge_shift_is_refused_at_once(no_decoder):
    def switch(blob, data):
        declared = texture(20, 20, TF.RGBA32, data, m_PlatformBlob=blob)
        declared.object_reader = SimpleNamespace(platform=BuildTarget.Switch, version=(2021, 3, 0, 0))
        return declared

    blob = [0] * 8 + [1, 0, 0, 0] + [0] * 4          # two GOBs per block
    block = TextureSwizzler.TEXTURE_FORMAT_BLOCK_SIZE_MAP[TF.RGBA32]
    width, height = TextureSwizzler.get_padded_texture_size(20, 20, *block, 2)
    need = width * height * 4
    assert need > 20 * 20 * 4
    guard.check_texture(switch(blob, bytes(need)))
    with pytest.raises(guard.TextureRefused):
        guard.check_texture(switch(blob, bytes(need - 1)))
    for huge in ([0] * 8 + [255, 255, 255, 127] + [0] * 4, [0] * 8 + [9, 0, 0, 0] + [0] * 4):
        with pytest.raises(guard.TextureRefused):
            guard.check_texture(switch(huge, bytes(1 << 20)))
    # no GOB shift: not swizzled, the plain size applies
    guard.check_texture(switch([0] * 16, bytes(20 * 20 * 4)))
    assert no_decoder.calls == []


# --- every place the App decodes a texture ----------------------------------------------------------------------------

BUNDLE = "assets-_mx-spinecharacters-test_spr-_mxdependency-2026-01-01_003_assets_all_123456.bundle"
PAGE_ATLAS = atlas_text([("page.png", (20, 18), [("body", -1, (4, 3), (6, 4), (6, 4), (0, 0), 0)])])


def crashing(tmp_path, name=BUNDLE, folder="mod"):
    """A real UnityFS bundle whose page declares 8192x8192 in a format whose native decoder reads past short data."""
    return write_bundle(tmp_path / folder / name, {"test.atlas": PAGE_ATLAS},
                        {"page": (8192, 8192, CRASHING_SITE_FORMAT, bytes(64))})


def test_spine_extraction_refuses_the_bundle(tmp_path, no_decoder):
    path = crashing(tmp_path)
    with pytest.raises(ServiceError) as caught:
        extract_spine([path])
    assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": path.name}
    assert no_decoder.calls == []


def test_extraction_skips_the_texture_and_reports_it(tmp_path, no_decoder):
    path = crashing(tmp_path)
    work, lines = tmp_path / "work", []
    work.mkdir()
    found = extraction.extract_assets_from_bundle([path], work, {"Texture2D", "TextAsset"}, lines.append)
    assert found[AssetType.Texture2D] == [] and not (work / "page.png").exists()
    assert sorted(file.name for file in found[AssetType.TextAsset]) == ["test.atlas", "test.skel"]
    assert any("page" in line and "bundle_invalid" in line for line in lines)
    ok, message = extraction.process_asset_extraction(path, tmp_path / "out", log=no_log)
    assert ok and not list((tmp_path / "out").glob("*.png"))
    assert no_decoder.calls == []


def test_update_patch_extraction_and_apply_refuse_the_texture(tmp_path, no_decoder):
    path = crashing(tmp_path)
    bundle = Bundle.load(path)
    with pytest.raises(ServiceError) as caught:
        bundle.extract_patch({"Texture2D"})
    assert caught.value.code == "required_asset_extract_failed"
    assert caught.value.params["file"] == path.name and caught.value.params["name"] == "page"
    assert "bounds check" in caught.value.params["error"]
    with pytest.raises(ServiceError) as caught:
        bundle.apply_patch({("page", "Texture2D"): Image.new("RGBA", (4, 4))}, "name_type")
    assert caught.value.code == "required_asset_apply_failed" and caught.value.params["name"] == "page"
    assert no_decoder.calls == []


def test_atlas_remap_inputs_refuse_the_texture_like_an_unreadable_one(tmp_path, no_decoder):
    path = crashing(tmp_path)
    with pytest.raises(remap.RemapError) as caught:
        remap.read_inputs([path])
    assert caught.value.code == "atlas_remap_invalid" and caught.value.params["file"] == f"{path.name} - page"
    assert "bounds check" in caught.value.params["reason"]
    assert no_decoder.calls == []


def collection_game(tmp_path):
    name = "uis-01_common-14_charactercollect-_mxload-2024-11-18_000_assets_all_1238120891.bundle"
    path = write_bundle(tmp_path / "game" / name, {}, {"Student_Portrait_CH0192_Collection":
                        (8192, 8192, CRASHING_SITE_FORMAT, bytes(64))}, skeleton=None)
    return path, GameAssets([path.parent], AssetCache(root=tmp_path / "cache"))


def test_collection_art_refuses_the_texture(tmp_path, no_decoder):
    path, game = collection_game(tmp_path)
    for read in (lambda: game.collection_portrait("Student_Portrait_CH0192_Collection"),
                 lambda: game.collection_portrait_from("Student_Portrait_CH0192_Collection", [path])):
        with pytest.raises(ServiceError) as caught:
            read()
        assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": path.name}
    assert no_decoder.calls == []


def fake_env(monkeypatch, tmp_path, declared, cab="CAB-test", path_id=42):
    @contextmanager
    def open_bundle(path):
        yield SimpleNamespace(objects=[SimpleNamespace(path_id=path_id, assets_file=SimpleNamespace(name=cab),
                                                       type=SimpleNamespace(name="Texture2D"), read=lambda: declared)])
    (tmp_path / "fake.bundle").write_bytes(b"fake")
    monkeypatch.setattr(service, "open_bundle", open_bundle)
    located = {"cab-test": str(tmp_path / "fake.bundle")}
    monkeypatch.setattr(GameAssets, "_resolve_cabs", lambda self, wanted, hint=None: located)


def test_selection_frame_and_lobby_effect_textures_refuse_the_texture(tmp_path, monkeypatch, no_decoder):
    game = GameAssets([tmp_path], AssetCache(root=tmp_path / "cache"))
    fake_env(monkeypatch, tmp_path, texture(8192, 8192, CRASHING_SITE_FORMAT, bytes(64)))
    slot = {"frameEffect": {"material": {"tex": {"_Tex_Main": {"id": "cab-test_42"}}}}}
    monkeypatch.setattr(game, "layout", lambda root, path: slot)
    monkeypatch.setattr(game, "lobby_info", lambda core: {"fx": {"textures": {"cab-test_42": {}}}})
    for read in (lambda: game.selection_frame_texture("cab-test_42"),
                 lambda: game.lobby_fx_texture("core", "cab-test_42")):
        with pytest.raises(ServiceError) as caught:
            read()
        assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": "fake.bundle"}
    assert no_decoder.calls == []


def test_atlas_sprite_pixels_refuse_the_texture(tmp_path, monkeypatch, no_decoder):
    game = GameAssets([tmp_path], AssetCache(root=tmp_path / "cache"))
    fake_env(monkeypatch, tmp_path, texture(8192, 8192, CRASHING_SITE_FORMAT, bytes(64)))
    record = {"bundle": "fake.bundle", "cab": "CAB-test", "pid": 42, "name": "page"}
    monkeypatch.setattr(game, "_bundle_path", lambda name: tmp_path / name)
    with pytest.raises(ServiceError) as caught:
        game._image(record)
    assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": "fake.bundle"}
    assert no_decoder.calls == []


def test_chibi_export_refuses_the_texture(no_decoder):
    owner = "/somewhere/character-ch0001-_mxload-2026-01-01.bundle"
    obj = SimpleNamespace(assets_file=SimpleNamespace(name="CAB-A"), path_id=7,
                          read=lambda: texture(8192, 8192, CRASHING_SITE_FORMAT, bytes(64)))
    exporter = model._Exporter(SimpleNamespace(locations={"cab-a": owner}), "ch0001")
    with pytest.raises(ServiceError) as caught:
        exporter.texture(obj)
    assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": Path(owner).name}
    unknown = model._Exporter(SimpleNamespace(locations={}), "ch0001")
    with pytest.raises(guard.TextureRefused):
        unknown.texture(obj)
    assert no_decoder.calls == []


def test_page_assembly_refuses_the_texture(tmp_path, no_decoder):
    source = crashing(tmp_path)
    with pytest.raises(ValueError, match="bounds check"):
        refine_assembly.assemble_mod([source], Image.new("RGBA", (20, 18)), tmp_path / "out", atlas_name="test.atlas",
                                     page_name="page.png")
    assert no_decoder.calls == []


def test_page_assembly_checks_the_page_it_decodes_itself(tmp_path, monkeypatch):
    # inputs read elsewhere (here from a valid bundle) do not excuse the page of the bundle that is assembled
    source = crashing(tmp_path)
    valid = write_bundle(tmp_path / "valid" / BUNDLE, {"test.atlas": PAGE_ATLAS}, {"page": Image.new("RGBA", (20, 18))})
    inputs = remap.read_inputs([valid])
    monkeypatch.setattr(remap, "read_inputs", lambda paths: inputs)
    decoded = []
    parse = Texture2DConverter.parse_image_data

    def spy(data, width, height, *args, **kwargs):
        decoded.append(width)
        return parse(data, width, height, *args, **kwargs)
    monkeypatch.setattr(Texture2DConverter, "parse_image_data", spy)
    with pytest.raises(ValueError, match="bounds check"):
        refine_assembly.assemble_mod([source], Image.new("RGBA", (20, 18)), tmp_path / "out", atlas_name="test.atlas",
                                     page_name="page.png")
    assert 8192 not in decoded


def test_portrait_edit_refuses_a_texture_it_cannot_check(tmp_path):
    # RGBA32 pages decode through Pillow alone, so only the size check applies: one pixel past the limit
    name = "Student_Portrait_CH0001_Collection"
    pixels = (16385, 1, int(TF.RGBA32), bytes(16385 * 4))
    base = write_bundle(tmp_path / "base" / BUNDLE, {}, {name: pixels}, skeleton=None)
    official = write_bundle(tmp_path / "official" / BUNDLE, {}, {name: pixels}, skeleton=None)
    png = tmp_path / "edit.png"
    Image.new("RGBA", (40, 40), (1, 2, 3, 255)).save(png)
    output = tmp_path / "out" / BUNDLE
    output.parent.mkdir()
    with pytest.raises(ServiceError) as caught:
        portrait_edit.patch_portraits(base, official, output, {name: png.read_bytes()})
    assert caught.value.code == "bundle_invalid" and caught.value.params == {"file": output.name}


# --- official files pass unchanged --------------------------------------------------------------------------------

def spaced(items, count):
    """``count`` items spread evenly over a sorted list, the same on every run."""
    items = sorted(items)
    if len(items) <= count:
        return items
    return [items[index * len(items) // count] for index in range(count)]


@real_install
def test_official_spine_cores_and_ui_bundles_pass_the_check(tmp_path):
    from ba_modding_toolkit.gameassets.discovery import group_bundles, is_atlas, is_texture
    from ba_modding_toolkit.gameassets.unity import open_bundle

    game = GameAssets(get_search_dirs(GAME_DIR), AssetCache(root=tmp_path / "cache"))
    portraits, lobbies = game.spine_cores("spr"), game.spine_cores("lobby")
    backgrounds = group_bundles(game._paths(), "spinebackground")
    # the first portraits include 4096 pages; the rest of each sample is spread over the sorted cores
    chosen = sorted(set(sorted(portraits)[:25]) | set(spaced(portraits, 15)))
    spine = ([portraits[core] for core in chosen] + [lobbies[core] for core in spaced(lobbies, 16)]
             + [backgrounds[core] for core in spaced(backgrounds, 13)])
    ui = [[path] for path in spaced([p for p in game._paths() if is_atlas(p) or is_texture(p)], 24)]
    assert len(spine) >= 40 and len(ui) >= 20
    checked, pages = 0, set()
    for kind, groups in (("spine", spine), ("ui", ui)):
        for group in groups:
            for path in group:
                with open_bundle(path) as env:
                    for obj in env.objects:
                        if obj.type.name == "Texture2D":
                            data = obj.read()
                            guard.check_texture(data, path.name)
                            checked += 1
                            if kind == "spine":
                                pages.add(max(data.m_Width, data.m_Height))
    assert checked >= 200 and 4096 in pages, (checked, sorted(pages))
