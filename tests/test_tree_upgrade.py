"""Typetree upgrades use the target schema and an explicit list of new fields."""

from types import SimpleNamespace

import pytest

from bmpt.engine import AssetType, Bundle, SaveOptions
from bmpt.library.rebuild import rebuild
from bmpt.library.tree_upgrade import TreeUpgradeError, upgrade_tree

from test_rebuild_failures import asset, bundle


def field(name, kind="int", *children):
    return SimpleNamespace(m_Name=name, m_Type=kind, m_Children=children)


def array(name, kind, *children):
    return field(name, "vector", field("Array", "Array", field("size"), field("data", kind, *children)))


def test_clip_curves_and_bindings_get_defaults_for_each_source_element():
    node = field(
        "Base",
        "AnimationClip",
        array("m_FloatCurves", "FloatCurve", field("curve"), field("flags")),
        array("m_PPtrCurves", "PPtrCurve", field("curve"), field("flags")),
        field(
            "m_MuscleClip",
            "ClipMuscleConstant",
            field(
                "m_Clip",
                "Clip",
                field(
                    "data",
                    "ClipData",
                    field(
                        "m_StreamedClip",
                        "StreamedClip",
                        field("curveCount", "unsigned int"),
                        field("discreteCurveCount"),
                    ),
                ),
            ),
        ),
        field(
            "m_ClipBindingConstant",
            "AnimationClipBindingConstant",
            array(
                "genericBindings",
                "GenericBinding",
                field("isPPtrCurve"),
                field("isIntCurve"),
                field("isSerializeReferenceCurve"),
            ),
        ),
    )
    source = {
        "m_FloatCurves": [{"curve": 12}, {"curve": 34, "flags": 16}],
        "m_PPtrCurves": [{"curve": 56}],
        "m_MuscleClip": {"m_Clip": {"data": {"m_StreamedClip": {"curveCount": 37}}}},
        "m_ClipBindingConstant": {"genericBindings": [{"isPPtrCurve": 0}, {"isPPtrCurve": 1}]},
    }
    assert upgrade_tree(source, node) == {
        "m_FloatCurves": [{"curve": 12, "flags": 0}, {"curve": 34, "flags": 16}],
        "m_PPtrCurves": [{"curve": 56, "flags": 0}],
        "m_MuscleClip": {"m_Clip": {"data": {"m_StreamedClip": {"curveCount": 37, "discreteCurveCount": 0}}}},
        "m_ClipBindingConstant": {
            "genericBindings": [
                {"isPPtrCurve": 0, "isIntCurve": 0, "isSerializeReferenceCurve": 0},
                {"isPPtrCurve": 1, "isIntCurve": 0, "isSerializeReferenceCurve": 0},
            ]
        },
    }
    assert "flags" not in source["m_FloatCurves"][0]


@pytest.mark.parametrize("count", [0, 3])
def test_mesh_lod_defaults_follow_the_source_submesh_count(count):
    node = field(
        "Base",
        "Mesh",
        field("m_CookingOptions"),
        array("m_SubMeshes", "SubMesh", field("count")),
        field(
            "m_MeshLodInfo",
            "MeshLodInfo",
            field(
                "m_LodSelectionCurve", "LodSelectionCurve", field("m_LodSlope", "float"), field("m_LodBias", "float")
            ),
            field("m_NumLevels"),
            array(
                "m_SubMeshes",
                "MeshLodSubMesh",
                array("m_Levels", "MeshLodLevel", field("m_IndexStart"), field("m_IndexCount")),
            ),
        ),
    )
    submeshes = [{"count": i} for i in range(count)]
    expected_levels = [{"m_Levels": [{"m_IndexStart": 0, "m_IndexCount": 0}]}] * count
    assert upgrade_tree({"m_SubMeshes": submeshes}, node) == {
        "m_CookingOptions": 30,
        "m_SubMeshes": submeshes,
        "m_MeshLodInfo": {
            "m_LodSelectionCurve": {"m_LodSlope": 0.0, "m_LodBias": 0.0},
            "m_NumLevels": 1,
            "m_SubMeshes": expected_levels,
        },
    }


@pytest.mark.parametrize(
    "source, item, message",
    [
        ({"curve": 1}, field("newField"), r"No default for AnimationClip.m_FloatCurves.\[\].newField"),
        ({"curve": 1, "oldField": 2}, field("flags"), r"No target field for AnimationClip.m_FloatCurves.\[\].oldField"),
    ],
)
def test_unknown_nested_fields_are_refused(source, item, message):
    node = field("Base", "AnimationClip", array("m_FloatCurves", "FloatCurve", field("curve"), item))
    with pytest.raises(TreeUpgradeError, match=message):
        upgrade_tree({"m_FloatCurves": [source]}, node)


def test_byte_arrays_and_pairs_keep_their_values():
    node = field(
        "Base",
        "Mesh",
        array("m_Data", "UInt8"),
        array("m_Map", "pair", field("first", "string"), field("second", "TypelessData")),
    )
    values = {"m_Data": b"\x00\xff", "m_Map": [("a", b"\x80")]}
    assert upgrade_tree(values, node) == values


def clip(path, release, rate, **target_fields):
    obj = asset("clip", AssetType.AnimationClip, payload=f"{rate}".encode())
    obj.assets_file = SimpleNamespace(unity_version=release)
    values = {"m_Name": "clip", "m_SampleRate": rate, **target_fields}
    obj.serialized_type = SimpleNamespace(
        node=field(
            "Base",
            "AnimationClip",
            field("m_Name", "string"),
            field("m_SampleRate", "float"),
            *(field(name) for name in target_fields),
        )
    )
    obj.read_typetree = values.copy
    obj.save_typetree = lambda tree: pytest.fail("the target object should not be rewritten")
    obj.set_raw_data = lambda data: pytest.fail("raw bytes should not cross releases")
    return bundle(path, [obj])


@pytest.mark.parametrize("rate, unchanged", [(30.0, True), (60.0, False)])
def test_rebuild_keeps_the_equal_official_clip_but_refuses_to_guess_an_unknown_default(
    tmp_path, monkeypatch, rate, unchanged
):
    source = clip(tmp_path / "mod.bundle", "2021.3.56f2", rate)
    target = clip(tmp_path / "target.bundle", "6000.3.27f2", 30.0, m_UnknownField=15)
    bundles = {item.path: item for item in (source, target)}
    monkeypatch.setattr(Bundle, "load", lambda path, *args: bundles[path])
    monkeypatch.setattr(Bundle, "save", lambda *args: pytest.fail("there should be no output"))
    result = rebuild(
        [source.path],
        [target.path],
        tmp_path,
        {"AnimationClip"},
        SaveOptions(),
        match_strategy="name_type",
        skip_unchanged=True,
    )
    assert result.success is result.all_unchanged is unchanged
    assert not result.file_pairs
    if unchanged:
        assert result.unchanged == result.matched == ["[AnimationClip] clip"]
    else:
        assert result.error.code == "mod_unity_asset_mismatch"
        assert result.error.params["name"] == "clip"


def test_rebuild_uses_each_counterparts_release(tmp_path, monkeypatch):
    source = clip(tmp_path / "mod.bundle", "2021.3.56f2", 60.0)
    current = clip(tmp_path / "current.bundle", "6000.3.27f2", 30.0)
    same_release = clip(tmp_path / "same-release.bundle", "2021.3.56f2", 30.0)
    bundles = {item.path: item for item in (source, current, same_release)}
    upgraded, raw = [], []
    current.env.objects[0].save_typetree = upgraded.append
    same_release.env.objects[0].set_raw_data = raw.append
    monkeypatch.setattr(Bundle, "load", lambda path, *args: bundles[path])
    monkeypatch.setattr(Bundle, "save", lambda *args: (True, "saved"))
    result = rebuild(
        [source.path],
        [current.path, same_release.path],
        tmp_path,
        {"AnimationClip"},
        SaveOptions(),
        match_strategy="name_type",
        skip_unchanged=True,
    )
    assert result.success, result.message
    assert {pair.source for pair in result.file_pairs} == {current.path, same_release.path}
    assert upgraded == [{"m_Name": "clip", "m_SampleRate": 60.0}]
    assert raw == [source.env.objects[0].get_raw_data()]
    assert not result.unchanged
