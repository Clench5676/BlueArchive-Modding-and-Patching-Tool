"""Names read from a mod must never choose where files land: asset names, atlas pages and atlas regions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from ba_modding_toolkit.core import extraction
from ba_modding_toolkit.models import AssetType
from ba_modding_toolkit.spine import SkelConverter, atlas_downgrade, unpack_atlas
from ba_modding_toolkit.utils import is_safe_file_name, is_safe_relative_name, no_log
from texture_helpers import fake_texture

ESCAPES = ["../escaped", "..\\escaped", "sub/../../escaped", "/escaped", "\\escaped", "C:escaped", "C:\\escaped",
           "..", ".", "", ".. ", "./..", "a/", "name\0.txt", "file.txt:stream"]


@pytest.mark.parametrize("name", ESCAPES)
def test_escaping_names_are_refused(name):
    assert not is_safe_relative_name(name)
    assert not is_safe_file_name(name)


@pytest.mark.parametrize("name", ["CH0808_home.atlas", "CH0808_home_2", ".hidden", "a..b", "name with space.skel"])
def test_ordinary_file_names_pass(name):
    assert is_safe_file_name(name)
    assert is_safe_relative_name(name)


def test_region_folders_are_relative_names_only():
    assert is_safe_relative_name("arm/left")
    assert not is_safe_file_name("arm/left")


def text_asset(name: str):
    data = SimpleNamespace(m_Name=name, m_Script="payload")
    return SimpleNamespace(type=AssetType.TextAsset, read=lambda: data)


def texture(name: str):
    data = fake_texture(Image.new("RGBA", (1, 1)), m_Name=name)
    return SimpleNamespace(type=AssetType.Texture2D, read=lambda: data)


def mesh(name: str):
    data = SimpleNamespace(m_Name=name)
    return SimpleNamespace(type=AssetType.Mesh, read=lambda: data, get_raw_data=lambda: b"payload")


def extract(tmp_path: Path, monkeypatch, objects: list) -> tuple[Path, dict]:
    work = tmp_path / "deep" / "work"
    work.mkdir(parents=True)
    monkeypatch.setattr(extraction.Bundle, "load",
                        lambda path, log: SimpleNamespace(env=SimpleNamespace(objects=objects)))
    return work, extraction.extract_assets_from_bundle([Path("mod.bundle")], work,
                                                       extraction.EXTRACTABLE_ASSET_TYPES, no_log)


@pytest.mark.parametrize("make", [text_asset, texture, mesh])
@pytest.mark.parametrize("name", ["../escaped", "../../escaped", "sub/../../escaped", "ABSOLUTE"])
def test_extraction_writes_nothing_outside_its_folder(tmp_path, monkeypatch, make, name):
    target = tmp_path / "absolute"
    name = str(target) if name == "ABSOLUTE" else name
    work, extracted = extract(tmp_path, monkeypatch, [make(name), make("good")])
    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(written) == 1 and written[0].parent == work and written[0].name.startswith("good")
    assert sum(map(len, extracted.values())) == 1


def test_skel_conversion_uses_its_own_file_name(monkeypatch):
    seen = []

    def run(input_path, output_path, converter_path, target_version, log):
        seen.append(input_path)
        return True

    monkeypatch.setattr(SkelConverter, "run", run)
    assert SkelConverter._convert_bytes(b"skel", Path("converter"), "3.8", no_log) == b"skel"
    assert seen[0].name == "input.skel"


def write_atlas(folder: Path, page: str = "page.png", region: str = "part") -> Path:
    folder.mkdir(parents=True)
    Image.new("RGBA", (4, 4)).save(folder / "page.png")
    atlas = folder / "mod.atlas"
    atlas.write_text(f"{page}\nsize: 4,4\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n{region}\n"
                     "  rotate: false\n  xy: 0, 0\n  size: 4, 4\n  orig: 4, 4\n  offset: 0, 0\n  index: -1\n",
                     encoding="utf-8")
    return atlas


def test_unpack_refuses_a_region_leaving_the_output_folder(tmp_path):
    atlas = write_atlas(tmp_path / "deep" / "atlas", region="../../../escaped")
    assert unpack_atlas(atlas, tmp_path / "deep" / "out", no_log) is False
    assert not list(tmp_path.rglob("escaped*"))


def test_unpack_keeps_regions_in_folders(tmp_path):
    atlas = write_atlas(tmp_path / "atlas", region="arm/left")
    assert unpack_atlas(atlas, tmp_path / "out", no_log) is True
    assert (tmp_path / "out" / "images" / "arm" / "left.png").is_file()


@pytest.mark.parametrize("scale", [False, True])
def test_downgrade_refuses_a_page_leaving_the_output_folder(tmp_path, scale):
    atlas = write_atlas(tmp_path / "deep" / "atlas", page="../../escaped.png")
    assert atlas_downgrade(atlas, tmp_path / "deep" / "out", scale, no_log) is False
    assert not list(tmp_path.rglob("escaped*"))
