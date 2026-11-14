"""Cross-bundle pointers: a mod's pointers into other bundles must reach objects the game still holds."""

import pytest

from ba_modding_toolkit.bundle import Bundle
from bmpt.errors import ServiceError
from bmpt.library import integrity, pointers
from bmpt.library.integrity import inspect as inspect_bundle
from test_batch_direct import rename_crc, restore_metadata
from test_batch_import import batch_model_env, env  # noqa: F401 - fixtures
from test_library import setup_library  # noqa: F401 - fixture
from test_library_model import model_bundle, model_env  # noqa: F401 - fixture
from test_mod_origin import unity_bundle

SHARED = 'archive:/CAB-shared/CAB-shared'
SHADER, TEXTURE = 48, 28


def material(name, shader):
    return {'m_Name': name, 'm_Shader': {'m_FileID': 1 if shader else 0, 'm_PathID': shader},
            'm_ValidKeywords': [], 'm_InvalidKeywords': [], 'm_LightmapFlags': 0,
            'm_EnableInstancingVariants': False, 'm_DoubleSidedGI': False, 'm_CustomRenderQueue': -1,
            'stringTagMap': [], 'disabledShaderPasses': [], 'm_BuildTextureStacks': [],
            'm_SavedProperties': {'m_TexEnvs': [], 'm_Ints': [], 'm_Floats': [], 'm_Colors': []}}


def body(path, shader, *, name='Body'):
    return unity_bundle(path, cab='CAB-body', references=[SHARED], objects=[(1, 21, material(name, shader))])


def shared(path, objects):
    """A bundle other bundles point into; only its object table is read."""
    return unity_bundle(path, cab='CAB-shared', objects=[(path_id, kind, bytes(64)) for path_id, kind in objects])


def test_pointers_are_read_with_their_declared_type(tmp_path):
    path = body(tmp_path / 'body.bundle', 5)
    found = pointers.external_pointers(Bundle.load(path).env)
    assert found == {pointers.Pointer('cab-shared', 5, 'Shader')}
    # a pointer into the file itself, or an empty one, is no cross-bundle pointer
    assert not pointers.external_pointers(Bundle.load(body(tmp_path / 'none.bundle', 0)).env)


def test_dangling_pointers_are_missing_or_of_another_type():
    pointer = pointers.Pointer('cab-shared', 5, 'Shader')
    assert not pointers.dangling([pointer], lambda cab: {5: SHADER})
    assert pointers.dangling([pointer], lambda cab: {6: SHADER}) == {pointer}
    assert pointers.dangling([pointer], lambda cab: {5: TEXTURE}) == {pointer}
    assert not pointers.dangling([pointer], lambda cab: None)  # a CAB found nowhere: the missing-part checks


@pytest.mark.parametrize('declared,class_id,expected', [
    ('Texture', TEXTURE, True), ('Object', SHADER, True), ('Component', 4, True), ('Material', TEXTURE, False),
    ('SkeletonDataAsset', 114, True), ('SkeletonDataAsset', TEXTURE, False), ('Shader', 999999, True)])
def test_declared_types_accept_their_subclasses(declared, class_id, expected):
    assert pointers.compatible(declared, class_id) is expected


def generated(env, monkeypatch):
    """Every installed bundle a generated fixture; the mesh part left out (part 004 takes its place)."""
    restore_metadata(monkeypatch)
    monkeypatch.setattr(integrity, 'inspect', inspect_bundle)
    for index, target in enumerate(env.parts):
        env.parts[index] = rename_crc(unity_bundle(target, cab=f'CAB-part-{index}'))
    env.parts[1].unlink()
    return env


@pytest.mark.parametrize('change', ['kept', 'moved', 'retyped', 'broken_in_game'])
def test_whole_model_file_must_point_at_objects_the_game_holds(batch_model_env, monkeypatch, change):
    env = generated(batch_model_env, monkeypatch)
    # the game's shared bundle and its body, as the current release has them
    held = {'kept': [(5, SHADER)], 'moved': [(6, SHADER)], 'retyped': [(5, TEXTURE), (6, SHADER)],
            'broken_in_game': [(6, SHADER)]}[change]
    env.original = rename_crc(shared(env.original, held))
    official_shader = 5 if change in ('kept', 'broken_in_game') else 6
    target = rename_crc(body(model_bundle(env.game_dir, part='004'), official_shader))
    # a mod made on the release before: its material still points at object 5
    source = body(env.tmp / 'input' / target.name, 5, name='Modded')
    if change in ('moved', 'retyped'):
        with pytest.raises(ServiceError, match='mod_release_mismatch') as refused:
            env.library.add_files('model', [source], direct=True)
        assert refused.value.params['file'] == source.name
        assert not env.library.mods()
    else:
        # the game's own file points there too: not held against the mod
        env.library.add_files('model', [source], direct=True)
        stored, = env.library.mods()
        assert stored.file.read_bytes() == source.read_bytes()


def test_apply_refuses_a_file_whose_pointers_a_game_update_left_dangling(batch_model_env, monkeypatch):
    env = generated(batch_model_env, monkeypatch)
    env.original = rename_crc(shared(env.original, [(5, SHADER)]))
    target = rename_crc(body(model_bundle(env.game_dir, part='004'), 5))
    source = body(env.tmp / 'input' / target.name, 5, name='Modded')
    env.library.add_files('model', [source], direct=True)
    mod, = env.library.mods()
    # the update rebuilds the shared bundle under the same CAB, its shader now object 6, and the body with it
    env.original.unlink()
    rename_crc(shared(env.original, [(6, SHADER)]))
    body(target, 6)
    with pytest.raises(ServiceError, match='mod_release_mismatch'):
        env.library.apply('hoshino', f'mod:{mod.id}', kind='model')
    assert Bundle.load(target).env.objects[0].read_typetree()['m_Shader']['m_PathID'] == 6
