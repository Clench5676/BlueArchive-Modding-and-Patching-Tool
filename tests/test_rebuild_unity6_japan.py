"""Exercise the App's update with official Japan bundles from both Unity releases."""

import shutil
from pathlib import Path

import pytest
from UnityPy.helpers.ResourceReader import get_resource_data

from bmpt.engine import Bundle, CRCUtils, SaveOptions
from bmpt.host.config import Settings
from bmpt.library import ModLibrary
from bmpt.library.integrity import replacement_key
from bmpt.library.rebuild import rebuild, unity_releases

from test_library import FakeCatalog

JP_CACHE = Path.home() / "Library/Caches/BAMT-Dev/jp"
BUNDLES = "BlueArchive_JP/BlueArchive_Data/StreamingAssets/AssetBundles"
SAVE = SaveOptions(perform_crc=False, compression="lz4")


def official_pair(part):
    old, new = (JP_CACHE / release / BUNDLES for release in ("1.74-r97-40", "1.75-r98-16"))
    pattern = f"assets-_mx-characters-hoshino_original-_mxdependency-{part}-*.bundle"
    for source in sorted(old.glob(pattern)):
        for target in new.glob(pattern):
            if replacement_key(source.name) == replacement_key(target.name):
                return source, target
    pytest.skip(f"Japan release pair for {part} is missing")


def objects(bundle, kind):
    assert bundle is not None
    key_of = Bundle._get_key_func("cont_name_type")
    result = {key_of(obj): obj for obj in bundle.env.objects if obj.type.name == kind}
    assert result
    return result


def save(bundle, path):
    ok, message = bundle.save(path, SAVE)
    assert ok, message


def assert_added_fields(tree, kind):
    if kind == "Mesh":
        assert tree.pop("m_CookingOptions") == 30
        assert tree.pop("m_MeshLodInfo") == {
            "m_LodSelectionCurve": {"m_LodSlope": 0.0, "m_LodBias": 0.0},
            "m_NumLevels": 1,
            "m_SubMeshes": [{"m_Levels": [{"m_IndexStart": 0, "m_IndexCount": 0}]} for _ in tree["m_SubMeshes"]],
        }
    else:
        for curve in tree["m_FloatCurves"] + tree["m_PPtrCurves"]:
            assert curve.pop("flags") == 0
        assert tree["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"].pop("discreteCurveCount") == 0
        for binding in tree["m_ClipBindingConstant"]["genericBindings"]:
            assert binding.pop("isIntCurve") == 0
            assert binding.pop("isSerializeReferenceCurve") == 0


@pytest.mark.parametrize("kind, part", [("Mesh", "meshes"), ("AnimationClip", "animationclips")])
def test_library_update_carries_one_edit_into_the_japan_release(tmp_path, kind, part):
    source, target = official_pair(part)
    upload, game = tmp_path / "upload", tmp_path / "game"
    upload.mkdir()
    game.mkdir()
    mod, old_official, official = upload / source.name, game / source.name, game / target.name
    shutil.copyfile(source, mod)
    shutil.copyfile(source, old_official)
    original = Bundle.load(mod)
    assert unity_releases(original) == {"2021.3"}
    original_objects = objects(original, kind)
    edited_key, edited = next(iter(original_objects.items()))
    tree = edited.read_typetree()
    if kind == "Mesh":
        positions = tree["m_CompressedMesh"]["m_Vertices"]["m_Data"]
        positions[0] ^= 1
    else:
        tree["m_SampleRate"] += 1
        assert tree["m_ClipBindingConstant"]["genericBindings"]
    edited.save_typetree(tree)
    save(original, mod)
    assert objects(Bundle.load(mod), kind)[edited_key].read_typetree() == tree

    settings = Settings(tmp_path / "config.toml")
    settings.set("file_source", "windows_japan")
    settings.set("game_resource_dir_japan", str(game))
    settings.set("enable_crc_correction", "true")
    settings.set("compression_method", "lz4")
    library = ModLibrary(tmp_path / "library", FakeCatalog(), object(), settings)
    (entry,) = library.add([mod])
    old_official.unlink()
    shutil.copyfile(target, official)
    result = library.update(entry.id)
    assert not result.unchanged
    (output,) = result.files
    assert output.name == target.name
    assert CRCUtils.compute_crc32(output) == CRCUtils.compute_crc32(official)
    assert [path.read_bytes() for path in result.previous] == [mod.read_bytes()]
    assert official.read_bytes() == target.read_bytes()

    saved = Bundle.load(output)
    assert unity_releases(saved) == {"6000.3"}
    upgraded = objects(saved, kind)
    stock = objects(Bundle.load(official), kind)
    assert upgraded.keys() == stock.keys() == original_objects.keys()
    assert len(upgraded) > 1
    for key, obj in upgraded.items():
        actual = obj.read_typetree()
        if key == edited_key:
            assert_added_fields(actual, kind)
            assert actual == tree
        else:
            assert actual == stock[key].read_typetree()
            assert obj.get_raw_data() == stock[key].get_raw_data()


@pytest.mark.parametrize("missing", [False, True])
def test_streamed_mesh_uses_its_mod_resource_and_protects_the_official_resource(tmp_path, missing):
    source, target = official_pair("meshes")
    target_bundle = Bundle.load(target)
    target_objects = objects(target_bundle, "Mesh")
    cab = next(iter(target_bundle.env.file.files))
    resource = f"{cab}.resS"
    archive_path = f"archive:/{cab}/{resource}"
    changed, retained = list(target_objects)[:2]
    stream = {"path": archive_path, "offset": 4, "size": 4}
    stock_bytes = b"skiporig"
    mod_bytes = b"skipedit"
    inputs = []
    for path, payload, bundle in ((source, mod_bytes, Bundle.load(source)), (target, stock_bytes, target_bundle)):
        staged = tmp_path / path.name
        inputs.append(staged)
        meshes = objects(bundle, "Mesh")
        mesh = meshes[changed]
        tree = mesh.read_typetree()
        tree["m_StreamData"] = stream
        mesh.save_typetree(tree)
        other = meshes[retained]
        tree = other.read_typetree()
        tree["m_StreamData"] = {**stream, "offset": 0}
        other.save_typetree(tree)
        if path == target or not missing:
            bundle.env.file.get_writeable_cab(resource).write(payload)
        save(bundle, staged)

    out = tmp_path / "out"
    out.mkdir()
    mod, official = inputs
    result = rebuild(
        [mod], [official], out, {"Mesh"}, SAVE, skip_unchanged=True, match_strategy="cont_name_type", read_back=True
    )
    if missing:
        assert not result.success and not list(out.iterdir())
        assert result.error.code == "mod_unity_asset_mismatch"
        return
    assert result.success, result.message
    (pair,) = result.file_pairs
    saved = Bundle.load(pair.output)
    meshes = objects(saved, "Mesh")
    upgraded = meshes[changed]
    tree = upgraded.read_typetree()
    new_stream = tree["m_StreamData"]
    prefix, name = new_stream["path"].rsplit("/", 1)
    assert prefix == f"archive:/{cab}"
    assert name != resource
    assert (new_stream["offset"], new_stream["size"]) == (4, 4)
    assert (
        get_resource_data(new_stream["path"], upgraded.assets_file, new_stream["offset"], new_stream["size"]) == b"edit"
    )
    assert meshes[retained].read_typetree()["m_StreamData"] == {**stream, "offset": 0}
    assert str(retained) in result.unchanged
    assert saved.env.file.files[resource].bytes == stock_bytes
    assert_added_fields(tree, "Mesh")
    tree["m_StreamData"] = stream
    assert tree == objects(Bundle.load(mod), "Mesh")[changed].read_typetree()
