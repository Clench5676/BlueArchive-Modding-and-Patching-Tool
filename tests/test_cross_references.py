"""Cross-bundle pointers: a mod's pointers into other bundles must reach objects the game still holds."""

import logging

import pytest

from ba_modding_toolkit.bundle import Bundle
from bmpt.errors import ServiceError
from bmpt.library import integrity, pointers
from bmpt.library.integrity import inspect as inspect_bundle
from test_batch_direct import rename_crc, restore_metadata
from test_batch_import import batch_model_env, env  # noqa: F401 - fixtures
from test_library import game_bundle, setup_library  # noqa: F401 - fixture
from test_library_model import model_bundle, model_env  # noqa: F401 - fixture
from test_mod_origin import unity_bundle

SHARED = "archive:/CAB-shared/CAB-shared"
SHADER, TEXTURE = 48, 28


def material(name, shader):
    return {
        "m_Name": name,
        "m_Shader": {"m_FileID": 1 if shader else 0, "m_PathID": shader},
        "m_ValidKeywords": [],
        "m_InvalidKeywords": [],
        "m_LightmapFlags": 0,
        "m_EnableInstancingVariants": False,
        "m_DoubleSidedGI": False,
        "m_CustomRenderQueue": -1,
        "stringTagMap": [],
        "disabledShaderPasses": [],
        "m_BuildTextureStacks": [],
        "m_SavedProperties": {"m_TexEnvs": [], "m_Ints": [], "m_Floats": [], "m_Colors": []},
    }


def body(path, shader, *, name="Body", payload=None):
    objects = [(1, 21, material(name, shader))]
    if payload is not None:
        objects.append((2, 49, {"m_Name": "Note", "m_Script": payload}))
    return unity_bundle(path, cab="CAB-body", references=[SHARED], objects=objects)


def shared(path, objects):
    """A bundle other bundles point into; only its object table is read."""
    return unity_bundle(path, cab="CAB-shared", objects=[(path_id, kind, bytes(64)) for path_id, kind in objects])


def test_pointers_are_read_with_their_declared_type(tmp_path):
    path = body(tmp_path / "body.bundle", 5)
    found = pointers.external_pointers(Bundle.load(path).env)
    assert found == {pointers.Pointer("cab-shared", 5, "Shader")}
    # a pointer into the file itself, or an empty one, is no cross-bundle pointer
    assert not pointers.external_pointers(Bundle.load(body(tmp_path / "none.bundle", 0)).env)


def test_dangling_pointers_are_missing_or_of_another_type():
    pointer = pointers.Pointer("cab-shared", 5, "Shader")
    assert not pointers.dangling([pointer], lambda cab: {5: SHADER})
    assert pointers.dangling([pointer], lambda cab: {6: SHADER}) == {pointer}
    assert pointers.dangling([pointer], lambda cab: {5: TEXTURE}) == {pointer}
    assert not pointers.dangling([pointer], lambda cab: None)  # a CAB found nowhere: the missing-part checks


@pytest.mark.parametrize(
    "declared,class_id,expected",
    [
        ("Texture", TEXTURE, True),
        ("Object", SHADER, True),
        ("Component", 4, True),
        ("Material", TEXTURE, False),
        ("SkeletonDataAsset", 114, True),
        ("SkeletonDataAsset", TEXTURE, False),
        ("Shader", 999999, True),
    ],
)
def test_declared_types_accept_their_subclasses(declared, class_id, expected):
    assert pointers.compatible(declared, class_id) is expected


def generated(env, monkeypatch):
    """Generated bundles for any model parts; the mesh part left out (part 004 takes its place)."""
    restore_metadata(monkeypatch)
    monkeypatch.setattr(integrity, "inspect", inspect_bundle)
    for index, target in enumerate(env.parts):
        env.parts[index] = rename_crc(unity_bundle(target, cab=f"CAB-part-{index}"))
    if env.parts:
        env.parts[1].unlink()
    return env


@pytest.mark.parametrize("change", ["kept", "moved", "retyped", "broken_in_game"])
def test_whole_model_file_must_point_at_objects_the_game_holds(batch_model_env, monkeypatch, change):
    env = generated(batch_model_env, monkeypatch)
    # the game's shared bundle and its body, as the current release has them
    held = {
        "kept": [(5, SHADER)],
        "moved": [(6, SHADER)],
        "retyped": [(5, TEXTURE), (6, SHADER)],
        "broken_in_game": [(6, SHADER)],
    }[change]
    env.original = rename_crc(shared(env.original, held))
    official_shader = 5 if change in ("kept", "broken_in_game") else 6
    target = rename_crc(body(model_bundle(env.game_dir, part="004"), official_shader))
    # a mod made on the release before: its material still points at object 5
    source = body(env.tmp / "input" / target.name, 5, name="Modded")
    if change in ("moved", "retyped"):
        with pytest.raises(ServiceError, match="mod_release_mismatch") as refused:
            env.library.add_files("model", [source], direct=True)
        assert refused.value.params["file"] == source.name
        assert not env.library.mods()
    else:
        # the game's own file points there too: not held against the mod
        env.library.add_files("model", [source], direct=True)
        (stored,) = env.library.mods()
        assert stored.file.read_bytes() == source.read_bytes()
        env.library.apply("hoshino", f"mod:{stored.id}", kind="model")
        assert target.read_bytes() == source.read_bytes()


def mod_with_pointers(env, monkeypatch, kind, *, same_name=False):
    """A whole-file mod with a material and a transferable TextAsset, before the shared shader is removed."""
    if kind != "model":
        env.parts = []
    generated(env, monkeypatch)
    # With both shaders present, the official body may already use 6 while the mod still uses 5. Removing 5 then
    # leaves the body's name and bytes unchanged, including its build signature and any kept copy of the mod.
    held = [(5, SHADER), (6, SHADER)] if same_name else [(5, SHADER)]
    env.original = rename_crc(shared(env.original, held))
    if kind == "model":
        target = model_bundle(env.game_dir, part="004")
    else:
        core, category = {
            "spr": ("test_spr", "spinecharacters"),
            "lobby": ("test_home_gl", "spinelobbies"),
            "other": ("common", "ui"),
        }[kind]
        target = game_bundle(env.game_dir, core, category=category, part="004")
    target = rename_crc(body(target, 6 if same_name else 5, payload="official"))
    source = body(env.tmp / "input" / target.name, 5, payload="modded")
    (mod,) = env.library.add_files(kind, [source], direct=True)
    return target, mod


def move_shader(env, target, *, same_name=False):
    """The game's shared CAB loses shader 5; its body uses 6 in the current release."""
    env.original.unlink()
    env.original = rename_crc(shared(env.original, [(6, SHADER)]))
    if not same_name:
        target = rename_crc(body(target, 6, payload="official"))
    return target


@pytest.mark.parametrize("kind", ["spr", "lobby", "model", "other"])
@pytest.mark.parametrize("entry_point", ["apply", "toggle"])
@pytest.mark.parametrize("same_name", [False, True])
def test_apply_updates_a_file_whose_pointers_a_game_update_left_dangling(
    request, monkeypatch, caplog, kind, entry_point, same_name
):
    env = request.getfixturevalue("batch_model_env" if kind == "model" else "env")
    target, mod = mod_with_pointers(env, monkeypatch, kind, same_name=same_name)
    if entry_point == "toggle":
        assert not env.library.toggle(False, True).failed
        assert env.library.apply(mod.target, f"mod:{mod.id}", kind=kind).deferred
    target = move_shader(env, target, same_name=same_name)
    with caplog.at_level(logging.INFO, logger="bmpt.library.service"):
        if entry_point == "apply":
            result = env.library.apply(mod.target, f"mod:{mod.id}", kind=kind)
            assert target in result.files
        else:
            assert not env.library.toggle(True, False).failed
    material_object, text = Bundle.load(target).env.objects
    assert material_object.read_typetree()["m_Shader"]["m_PathID"] == 6
    assert text.read().m_Script == "modded"
    demotions = [
        record.message for record in caplog.records if "updating it as a file of an older release" in record.message
    ]
    assert len(demotions) == 1 and mod.file.name in demotions[0]


@pytest.mark.parametrize("kind", ["spr", "lobby", "model"])
@pytest.mark.parametrize("entry_point", ["apply", "toggle"])
@pytest.mark.parametrize("needs_crc_fix", [False, True])
def test_apply_checks_references_after_restoring_omitted_official_parts(
    request, monkeypatch, caplog, kind, entry_point, needs_crc_fix
):
    env = request.getfixturevalue("batch_model_env" if kind == "model" else "env")
    target, mod = mod_with_pointers(env, monkeypatch, kind, same_name=True)
    move_shader(env, target, same_name=True)
    # The omitted shared part belongs to this entry, so the write restores its official copy. The install still
    # has an older, non-official copy that masks the mod's stale pointer when resolved through the CAB index.
    env.original = rename_crc(env.original.rename(env.original.with_name(target.name.replace("_004_", "_000_"))))
    official = env.original.read_bytes()
    if entry_point == "toggle":
        assert not env.library.toggle(False, True).failed
    previous = shared(env.tmp / "previous" / env.original.name, [(5, SHADER), (6, SHADER)])
    (previous_mod,) = env.library.add_files(kind, [previous])
    # Reproduce a partial write with its library record intact. An unrecorded outside
    # change is deliberately skipped by Mods on before it ever plans or writes this entry.
    env.library._commit(
        {env.original: previous_mod.file},
        f"mod:{previous_mod.id}",
        lambda message: None,
        pin=env.library.current_pin(),
        prune=False,
    )
    if entry_point == "toggle":
        assert env.library.apply(mod.target, f"mod:{mod.id}", kind=kind).deferred
    assert not env.library._is_official(env.original)
    monkeypatch.setattr(env.settings, "crc_needed", lambda path: needs_crc_fix)
    assert env.library._needs_crc_fix(mod.file, target) is needs_crc_fix
    with caplog.at_level(logging.DEBUG, logger="bmpt.library.service"):
        if entry_point == "apply":
            result = env.library.apply(mod.target, f"mod:{mod.id}", kind=kind)
            assert target in result.files
        else:
            result = env.library.toggle(True, False)
            assert not result.failed, result.failed
    assert env.original.read_bytes() == official
    material_object, text = Bundle.load(target).env.objects
    assert material_object.read_typetree()["m_Shader"]["m_PathID"] == 6
    assert text.read().m_Script == "modded"
    demotions = [
        record.message
        for record in caplog.records
        if "points at objects the game no longer holds, updating it as a file of an older release" in record.message
    ]
    assert len(demotions) == 1 and mod.file.name in demotions[0]


@pytest.mark.parametrize("entry_point", ["cached_build", "built_replacements"])
def test_a_stale_kept_copy_rebuilds_by_updating_its_source(env, monkeypatch, caplog, entry_point):
    target, mod = mod_with_pointers(env, monkeypatch, "lobby", same_name=True)
    official = target.read_bytes()
    env.library.apply(mod.target, f"mod:{mod.id}", kind="lobby")
    assert not env.library.toggle(False, True).failed
    if entry_point == "cached_build":
        assert not env.library.toggle(True, False).failed
        # A game update restores the body too, to the same official bytes; the retained build still has its key.
        target.write_bytes(official)
    move_shader(env, target, same_name=True)
    with caplog.at_level(logging.INFO, logger="bmpt.library.service"):
        if entry_point == "cached_build":
            env.library.apply(mod.target, f"mod:{mod.id}", kind="lobby")
        else:
            assert not env.library.toggle(True, False).failed
    assert "points at objects the game no longer holds, rebuilding" in caplog.text
    material_object, text = Bundle.load(target).env.objects
    assert material_object.read_typetree()["m_Shader"]["m_PathID"] == 6
    assert text.read().m_Script == "modded"


def test_apply_still_refuses_a_dangling_pointer_transferred_by_replace_all(batch_model_env, monkeypatch):
    env = batch_model_env
    target, mod = mod_with_pointers(env, monkeypatch, "model")
    target = move_shader(env, target)
    official = target.read_bytes()
    env.settings.set("replace_all", True)
    with pytest.raises(ServiceError, match="mod_release_mismatch"):
        env.library.apply(mod.target, f"mod:{mod.id}", kind="model")
    assert target.read_bytes() == official


def test_a_demoted_file_with_no_update_target_keeps_the_existing_error(batch_model_env, monkeypatch):
    env = generated(batch_model_env, monkeypatch)
    env.original = rename_crc(shared(env.original, [(5, SHADER), (6, SHADER)]))
    target = rename_crc(body(model_bundle(env.game_dir, part="004"), 6))
    source = body(env.tmp / "input" / target.name, 5, name="Modded", payload="modded")
    (mod,) = env.library.add_files("model", [source], direct=True)
    move_shader(env, target, same_name=True)
    official = target.read_bytes()
    with pytest.raises(ServiceError, match="game_file_not_found"):
        env.library.apply(mod.target, f"mod:{mod.id}", kind="model")
    assert target.read_bytes() == official


@pytest.mark.parametrize("crc_name", [False, True])
def test_a_whole_file_uses_objects_supplied_by_the_same_write(batch_model_env, monkeypatch, caplog, crc_name):
    env = generated(batch_model_env, monkeypatch)
    env.original = rename_crc(unity_bundle(env.original, cab="CAB-unrelated"))
    dependency = rename_crc(shared(env.parts[2], [(6, SHADER)]))
    target = rename_crc(body(model_bundle(env.game_dir, part="004"), 6, payload="official"))
    sources = [
        body(env.tmp / "input" / target.name, 5, payload="modded"),
        shared(env.tmp / "input" / dependency.name, [(5, SHADER)]),
    ]
    if crc_name:
        sources[0] = rename_crc(sources[0])
    (mod,) = env.library.add_files("model", sources, direct=True)
    with caplog.at_level(logging.INFO, logger="bmpt.library.service"):
        env.library.apply(mod.target, f"mod:{mod.id}", kind="model")
    assert target.read_bytes() == sources[0].read_bytes()
    assert dependency.read_bytes() == sources[1].read_bytes()
    assert "updating it as a file of an older release" not in caplog.text
