"""Raw object bytes stay within a Unity release on update.

Official bundles of the verified view stand in for both sides: the "mod" is an official part with one asset changed,
and one side's serialized files are given another Unity release in memory. Needs BAMT_TEST_GAME_DIR.
"""

import os
from pathlib import Path

import pytest

from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.models import SaveOptions
from bmpt.library.rebuild import rebuild, unity_release, unity_releases

GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")
BUNDLES = GAME_DIR / "BlueArchive_Data/StreamingAssets/PUB/Resource/GameData/Windows"
NEXT_RELEASE = "6000.0.58f1"
pytestmark = pytest.mark.skipif(not BUNDLES.is_dir(), reason="Set BAMT_TEST_GAME_DIR to a verified official view")


def official(pattern: str, kind: str) -> Path:
    for path in sorted(BUNDLES.glob(pattern), key=lambda p: (p.stat().st_size, p.name)):
        bundle = Bundle.load(path)
        if bundle and any(obj.type.name == kind for obj in bundle.env.objects):
            return path
    pytest.skip(f"no official {kind} bundle matches {pattern}")


def edit_clip(obj):
    tree = obj.read_typetree()
    tree["m_SampleRate"] += 1
    obj.save_typetree(tree)


def edit_mesh(obj):
    tree = obj.read_typetree()
    tree["m_LocalAABB"]["m_Center"]["x"] += 0.5
    obj.save_typetree(tree)


def edit_text(obj):
    data = obj.read()
    if data.m_Name.lower().endswith(".atlas"):
        data.m_Script += "\n"
        data.save()


def edit_texture(obj):
    data = obj.read()
    image = data.image.convert("RGBA")
    pixel = image.getpixel((0, 0))
    image.putpixel((0, 0), tuple((value + 128) % 256 for value in pixel))
    data.image = image
    data.save()


CASES = {
    "AnimationClip": ("*-spinelobbies-*_home-*", edit_clip),
    "Mesh": ("assets-_mx-characters-*", edit_mesh),
    "TextAsset": ("*-spinecharacters-*_spr-*", edit_text),
    "Texture2D": ("*-spinecharacters-np0122_spr-*", edit_texture),
}


def make_mod(path: Path, out: Path, kind: str, edit) -> Path:
    bundle = Bundle.load(path)
    for obj in bundle.env.objects:
        if obj.type.name == kind:
            edit(obj)
    ok, message = bundle.save(out, SaveOptions(perform_crc=False, compression="lz4"))
    assert ok, message
    return out


@pytest.fixture
def next_release(monkeypatch):
    """Give the parts in the returned set another Unity release as they are loaded."""
    renamed: set[Path] = set()
    load = Bundle.load.__func__

    def loaded(cls, path, log=lambda *_: None):
        bundle = load(cls, path, log)
        if bundle is not None and Path(path) in renamed:

            def walk(file):
                if hasattr(file, "target_platform"):
                    file.set_version(NEXT_RELEASE)
                    return
                for child in getattr(file, "files", {}).values():
                    walk(child)

            walk(bundle.env)
        return bundle

    monkeypatch.setattr(Bundle, "load", classmethod(loaded))
    return renamed


def run(mod: Path, target: Path, out: Path, kinds: set[str]):
    out.mkdir(exist_ok=True)
    return rebuild(
        [mod],
        [target],
        out,
        kinds,
        SaveOptions(perform_crc=False, compression="lz4"),
        skip_unchanged=True,
        match_strategy="cont_name_type",
    )


def test_unity_release_takes_major_and_minor():
    assert unity_release("2021.3.56f2") == "2021.3"
    assert unity_release("6000.0.58f1") == "6000.0"
    assert unity_release("") is None


@pytest.mark.parametrize("kind", ["AnimationClip", "Mesh"])
def test_a_changed_raw_object_is_refused_when_updating_back_to_the_old_release(tmp_path, next_release, kind):
    pattern, edit = CASES[kind]
    target = official(pattern, kind)
    mod = make_mod(target, tmp_path / "mod.bundle", kind, edit)
    next_release.add(mod)
    result = run(mod, target, tmp_path / "out", {"Texture2D", "TextAsset", "Mesh", "AnimationClip"})
    assert not result.success and not result.file_pairs
    assert result.error.code == "mod_unity_asset_mismatch"
    assert result.error.params["type"] == kind
    assert result.error.params["name"]
    assert (result.error.params["version"], result.error.params["expected"]) == ("6000.0", "2021.3")
    assert not list((tmp_path / "out").iterdir())


@pytest.mark.parametrize("kind", ["AnimationClip", "Mesh"])
def test_a_changed_raw_object_still_moves_within_its_release(tmp_path, kind):
    pattern, edit = CASES[kind]
    target = official(pattern, kind)
    mod = make_mod(target, tmp_path / "mod.bundle", kind, edit)
    result = run(mod, target, tmp_path / "out", {"Mesh", "AnimationClip"})
    assert result.success, result.message
    assert result.file_pairs


def test_an_unchanged_raw_object_keeps_the_official_one_across_releases(tmp_path, next_release):
    # a lobby mod carries the part's camera clips as they were; they are no reason to refuse the mod
    target = official(CASES["AnimationClip"][0], "AnimationClip")
    mod = tmp_path / "mod.bundle"
    mod.write_bytes(target.read_bytes())
    next_release.add(target)
    result = run(mod, target, tmp_path / "out", {"AnimationClip"})
    assert result.success, result.message
    assert result.all_unchanged and not result.file_pairs and result.unchanged


@pytest.mark.parametrize("kind", ["TextAsset", "Texture2D"])
def test_type_tree_copies_still_update_across_releases(tmp_path, next_release, kind):
    pattern, edit = CASES[kind]
    target = official(pattern, kind)
    mod = make_mod(target, tmp_path / "mod.bundle", kind, edit)
    next_release.add(target)
    result = run(mod, target, tmp_path / "out", {"Texture2D", "TextAsset", "Mesh", "AnimationClip"})
    assert result.success, result.message
    (pair,) = result.file_pairs
    output = Bundle.load(pair.output)
    assert unity_releases(output) == {"6000.0"}  # the official part's release, written by the official part
    assert unity_releases(Bundle.load(mod)) == {"2021.3"}


def test_read_back_passes_a_good_part_and_fails_one_that_does_not_load(tmp_path, monkeypatch):
    target = official(CASES["TextAsset"][0], "TextAsset")
    mod = make_mod(target, tmp_path / "mod.bundle", "TextAsset", edit_text)
    kinds = {"TextAsset"}
    (tmp_path / "good").mkdir()
    good = rebuild([mod], [target], tmp_path / "good", kinds, SaveOptions(perform_crc=False), read_back=True)
    assert good.success and good.file_pairs, good.message

    def broken(self, output_path, *args):
        output_path.write_bytes(b"UnityFS\0" + bytes(64))
        return True, "saved"

    monkeypatch.setattr(Bundle, "save", broken)
    (tmp_path / "bad").mkdir()
    bad = rebuild([mod], [target], tmp_path / "bad", kinds, SaveOptions(perform_crc=False), read_back=True)
    assert not bad.success and not bad.file_pairs
    assert bad.error.code == "bundle_invalid"
    unchecked = rebuild([mod], [target], tmp_path / "bad", kinds, SaveOptions(perform_crc=False))
    assert unchecked.success  # without read_back the caller checks its outputs itself
