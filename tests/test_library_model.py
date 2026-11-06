"""Character model mods use the same library transactions and intent as Spine mods."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from bmpt.catalog import Catalog
from bmpt.errors import ServiceError
from bmpt.library import ModLibrary, supports_kind
from bmpt.library.identity import content_cores, identify
from bmpt.library.integrity import Contents
from bmpt.library.judge import kind_of_file
from ba_modding_toolkit.naming import parse_filename

from test_library import game_bundle, header_bundle, make_mod, setup_library
from test_mod_identity import objects

# the real-install tests run only on an official game folder named by BAMT_TEST_GAME_DIR
GAME_DIR = Path(os.environ.get("BAMT_TEST_GAME_DIR") or "/nonexistent/bamt-real-game")


def model_bundle(folder, core='hoshino_original', *, prefab=False, **kwargs):
    path = game_bundle(folder, core, category='character' if prefab else 'characters', **kwargs)
    if prefab:
        target = path.with_name(path.name.replace('assets-_mx-character-', 'character-').replace('_mxdependency-', '_mxload-'))
        path.rename(target)
        return target
    return path


@pytest.fixture
def model_env(setup_library):
    env = setup_library
    prefab = model_bundle(env.game_dir, prefab=True, part='000', payload=b'prefab official')
    mesh = model_bundle(env.game_dir, part='meshes', payload=b'mesh official')
    texture = model_bundle(env.game_dir, part='textures', payload=b'texture official')
    game = SimpleNamespace(spine_cores=lambda kind: {'hoshino_spr': []} if kind == 'spr' else {},
                           collection_names=lambda: {}, model_cores=lambda: {'hoshino_original'})
    env.catalog = Catalog(game, None)
    env.library = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    env.parts = [prefab, mesh, texture]
    env.official = {p: p.read_bytes() for p in env.parts}
    return env


@pytest.mark.parametrize('category', ['character', 'characters', 'npcs'])
def test_supported_model_bundle_categories(category):
    assert kind_of_file(f'assets-_mx-{category}-hoshino_original-_mxload-2026-01-01_assets_all_1.bundle') == 'model'
    assert supports_kind('model') and ModLibrary.supports_kind('model')
    assert not supports_kind('unknown')


@pytest.mark.parametrize('name', ['Echelon_Hoshino_Original', 'Hoshino_Body',
                                  'Assets/_MX/Characters/Hoshino_Original/Mesh/Body.asset'])
def test_model_content_identity_overrules_renamed_file(model_env, monkeypatch, name):
    env = model_env
    source = model_bundle(env.tmp / 'upload', core='ch0246')
    with monkeypatch.context() as patch:
        objects(patch, [name], [name] if '/' in name else [])
        patch.setattr('bmpt.library.identity.content_cores', content_cores)
        filename, key, kind = identify(source, source.name, env.catalog)
    assert (parse_filename(filename).core, key, kind) == ('hoshino_original', 'hoshino', 'model')


def test_model_content_conflict_and_unknown_are_refused(model_env, monkeypatch):
    env = model_env
    source = model_bundle(env.tmp / 'upload')
    for names, code in [(['Echelon_Unknown'], 'mod_target_unknown'),
                        (['Echelon_Hoshino_Original', 'hoshino_spr.skel'], 'mod_target_ambiguous')]:
        with monkeypatch.context() as patch:
            objects(patch, names)
            patch.setattr('bmpt.library.identity.content_cores', content_cores)
            with pytest.raises(ServiceError, match=code):
                identify(source, source.name, env.catalog)


@pytest.mark.parametrize('anchor', ['container', 'prefab', 'filename'])
@pytest.mark.parametrize('role', ['Body', 'CutIn_Body', 'Cut_In_Body', 'EyeBrow', 'Formation_Idle',
                                  'Cafe_Reaction', 'Victory_End', 'Normal_Idle', 'Stand_Idle', 'Idle'])
def test_model_roles_resolve_against_the_owning_core(model_env, monkeypatch, anchor, role):
    env = model_env
    variant = SimpleNamespace(key='shigure', model='ch0123')
    catalog = SimpleNamespace(variant_for_core=lambda core: None,
                              variant_for_model_core=lambda core: variant if core == 'ch0123' else None)
    source = model_bundle(env.tmp / 'upload', core='ch0123' if anchor == 'filename' else 'renamed')
    names = [f'CH0123_{role}']
    containers = ['Assets/_MX/Characters/CH0123/Mesh/Body.asset'] if anchor == 'container' else []
    if anchor == 'prefab':
        names.append('Echelon_CH0123')
    objects(monkeypatch, names, containers)
    monkeypatch.setattr('bmpt.library.identity.content_cores', content_cores)
    assert content_cores(source) == {'model:ch0123'}
    filename, key, kind = identify(source, source.name, catalog)
    assert (parse_filename(filename).core, key, kind) == ('ch0123', 'shigure', 'model')


def test_model_core_with_role_word_keeps_the_prefab_identity(model_env, monkeypatch):
    source = model_bundle(model_env.tmp / 'upload', core='renamed')
    objects(monkeypatch, ['Echelon_CH0123_CutIn', 'CH0123_CutIn_Body'])
    assert content_cores(source) == {'model:ch0123_cutin'}


@pytest.mark.parametrize('names, code', [
    (['Echelon_Hoshino_Original', 'Unknown_CutIn_Body'], 'mod_target_unknown'),
    (['Echelon_Unknown', 'Hoshino_CutIn_Body'], 'mod_target_unknown'),
    (['Echelon_Hoshino_Original', 'Echelon_Shigure', 'Shigure_CutIn_Body'], 'mod_target_ambiguous'),
])
def test_model_role_resolution_preserves_unknown_and_conflicting_targets(model_env, monkeypatch, names, code):
    source = model_bundle(model_env.tmp / 'upload')
    hoshino = SimpleNamespace(key='hoshino', model='hoshino_original')
    shigure = SimpleNamespace(key='shigure', model='shigure')
    variants = {'hoshino': hoshino, 'hoshino_original': hoshino, 'shigure': shigure}
    catalog = SimpleNamespace(variant_for_core=lambda core: None,
                              variant_for_model_core=variants.get)
    objects(monkeypatch, names)
    monkeypatch.setattr('bmpt.library.identity.content_cores', content_cores)
    with pytest.raises(ServiceError) as caught:
        identify(source, source.name, catalog)
    assert caught.value.code == code


# Object names from official model parts: bones, an outline mesh, the game's own misspellings, the shield prop,
# animation clips, a controller, a model's own Spine effects (Kotama's skill bear, CH0333's emoji), and a role name of
# another student.
STRAY_OBJECT_NAMES = ['Bip001_Weapon', 'Bone_Hair_F_L_00', 'OL_Hoshino_Original_Body', 'Hosino_Origninal_Weapon',
                      'Hoshino_Origina_Halo', 'Hoshino_Original_Shield_Weapon', 'Hoshino_Original_Move_End_Normal',
                      'Hoshino_Original_Rearrange_Idle', 'Hoshino_Motion_Victory_Voc',
                      'Echelon_Hoshino_Original_Controller', 'Kotama_skill_bear.skel', 'CH0333_Emoji.atlas',
                      'Shigure_Body']


@pytest.mark.parametrize('container, prefab', [
    ('Assets/_MX/Characters/Hoshino_Original/Mesh/Body.asset', False),
    ('Assets/_MX/AddressableAsset/Character/Hoshino_Original/Echelon/Echelon_Hoshino_Original.prefab', True),
    ('Assets/_MX/AddressableAsset/Character/Hoshino_Original/Hoshino_Original_Controller.overrideController', True),
])
def test_model_asset_paths_identify_the_model_whatever_the_object_names(model_env, monkeypatch, container, prefab):
    source = model_bundle(model_env.tmp / 'upload', core='renamed', prefab=prefab)
    hoshino = SimpleNamespace(key='hoshino', model='hoshino_original')
    shigure = SimpleNamespace(key='shigure', model='shigure')
    catalog = SimpleNamespace(variant_for_core=lambda core: None,
                              variant_for_model_core={'hoshino_original': hoshino, 'shigure': shigure}.get)
    objects(monkeypatch, STRAY_OBJECT_NAMES, [container])
    monkeypatch.setattr('bmpt.library.identity.content_cores', content_cores)
    assert content_cores(source) == {'model:hoshino_original'}
    filename, key, kind = identify(source, source.name, catalog)
    assert (parse_filename(filename).core, key, kind) == ('hoshino_original', 'hoshino', 'model')


def test_model_asset_paths_of_two_models_stay_ambiguous(model_env, monkeypatch):
    source = model_bundle(model_env.tmp / 'upload')
    variants = {'hoshino_original': SimpleNamespace(key='hoshino', model='hoshino_original'),
                'shigure': SimpleNamespace(key='shigure', model='shigure')}
    catalog = SimpleNamespace(variant_for_core=lambda core: None, variant_for_model_core=variants.get)
    objects(monkeypatch, [], ['Assets/_MX/Characters/Hoshino_Original/Mesh/Body.asset',
                              'Assets/_MX/Characters/Shigure/Mesh/Body.asset'])
    monkeypatch.setattr('bmpt.library.identity.content_cores', content_cores)
    with pytest.raises(ServiceError, match='mod_target_ambiguous'):
        identify(source, source.name, catalog)


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason="Blue Archive install unavailable")
@pytest.mark.parametrize('core, key', [('hoshino_original', 'hoshino'), ('hoshino_swimsuit', 'hoshino_swimsuit')])
def test_installed_model_parts_identify_by_their_content(tmp_path, core, key):
    # Every official part of a model, read by content as for a file with no installed namesake.
    from bmpt.gameassets import AssetCache, GameAssets
    from ba_modding_toolkit.searching import get_search_dirs

    dirs = get_search_dirs(GAME_DIR)
    catalog = Catalog(GameAssets(dirs, AssetCache(allowed=False, root=tmp_path)), None, server='global')
    parts = [path for directory in dirs for path in directory.glob('*.bundle')
             if kind_of_file(path.name) == 'model' and parse_filename(path.name).core.lower() == core]
    assert len(parts) >= 3
    for path in parts:
        assert identify(path, path.name, catalog)[1:] == (key, 'model'), path.name


def test_model_add_apply_restore_keeps_every_slot_and_official_backup(model_env):
    env = model_env
    uploads = [make_mod(p, env.tmp / 'upload', payload=f'mod {i}'.encode()) for i, p in enumerate(env.parts[:2])]
    added = env.library.add_files('model', uploads, apply=True)
    entry, = added
    assert entry.kind == 'model' and entry.target == 'hoshino' and added.applied == [entry]
    assert [p.read_bytes() for p in env.parts[:2]] == [p.read_bytes() for p in uploads]
    assert env.parts[2].read_bytes() == env.official[env.parts[2]]
    files = [f for f in env.library.state('hoshino').files if f.kind == 'model']
    assert len(files) == 3 and all(f.restorable for f in files)
    assert {f.applied for f in files} == {f'mod:{entry.id}'}
    assert env.library.configured('hoshino')['model'] == f'mod:{entry.id}'
    assert (env.root / 'originals' / env.parts[0].name).read_bytes() == env.official[env.parts[0]]
    reloaded = ModLibrary(env.root, env.catalog, object(), env.settings)
    assert reloaded.mods()[0].id == entry.id
    assert reloaded.apply('hoshino', f'mod:{entry.id}', kind='model').unchanged
    reloaded.restore('hoshino', kind='model')
    assert all(p.read_bytes() == data for p, data in env.official.items())
    assert reloaded.configured('hoshino')['model'] is None


def test_model_master_switch_and_remove(model_env):
    env = model_env
    off = env.library.toggle(False, True)
    assert not off.failed
    upload = make_mod(env.parts[1], env.tmp / 'upload')
    result = env.library.add_and_apply([upload])
    entry, = result
    assert result.configured == [entry] and result.applied == []
    assert env.parts[1].read_bytes() == env.official[env.parts[1]]
    assert not env.library.toggle(True, False).failed
    assert env.parts[1].read_bytes() == upload.read_bytes()
    assert not env.library.toggle(False, True).failed
    assert env.parts[1].read_bytes() == env.official[env.parts[1]]
    assert not env.library.toggle(True, False).failed
    assert env.library.remove(entry.id, confirm=True).failed == []
    assert env.library.configured('hoshino')['model'] is None
    assert all(p.read_bytes() == data for p, data in env.official.items())


def test_model_drift_and_untracked_adoption(model_env):
    env = model_env
    target = env.parts[1]
    entry, = env.library.add_and_apply([make_mod(target, env.tmp / 'upload')])
    target.write_bytes(env.official[target])
    state = next(e for e in env.library.scan().entries if e.core == 'hoshino_original')
    assert state.kind == 'model' and state.state == 'reset'
    assert not env.library.restore_drift([{'install': state.install, 'core': state.core}]).failed
    assert target.read_bytes() == entry.file.read_bytes()
    env.library.restore('hoshino', kind='model')
    Path(str(target) + '.backup').write_bytes(env.official[target])
    target.write_bytes(header_bundle(b'outside model bytes'))
    state = next(e for e in env.library.scan().entries if e.core == 'hoshino_original')
    assert state.state == 'untracked' and state.kind == 'model'
    adopted = env.library.adopt([{'install': state.install, 'core': state.core}])
    assert adopted.added and not adopted.failed
    assert adopted.added[0].kind == 'model'
    env.library.restore('hoshino', kind='model')
    assert target.read_bytes() == env.official[target]


def test_model_library_relocation_keeps_restore_and_remove(model_env):
    env = model_env
    entry, = env.library.add_and_apply([make_mod(env.parts[0], env.tmp / 'upload')])
    relocated = env.tmp / 'relocated'
    shutil.move(env.root, relocated)
    library = ModLibrary(relocated, env.catalog, object(), env.settings)
    assert library.mods()[0].file.is_relative_to(relocated)
    assert library.remove(entry.id, confirm=True).failed == []
    assert all(p.read_bytes() == data for p, data in env.official.items())


def test_model_missing_mesh_stream_and_platform_refuse_the_whole_upload(model_env, monkeypatch):
    env = model_env
    upload = make_mod(env.parts[1], env.tmp / 'upload')
    monkeypatch.setattr('bmpt.library.integrity.inspect',
                        lambda path: Contents(platforms={'StandaloneWindows64'}, streams={'missing.ress'})
                        if path == upload else Contents(platforms={'StandaloneWindows64'}))
    with pytest.raises(ServiceError, match='mod_parts_missing'):
        env.library.add([upload])
    assert env.library.mods() == []
    monkeypatch.setattr('bmpt.library.integrity.inspect',
                        lambda path: Contents(platforms={'Android' if path == upload else 'StandaloneWindows64'}))
    with pytest.raises(ServiceError, match='mod_platform_mismatch'):
        env.library.add([upload])
    assert env.library.mods() == []


def test_model_variant_transplant_is_refused(model_env):
    env = model_env
    with pytest.raises(ServiceError, match='source_unsupported'):
        env.library.apply('hoshino', 'variant:hoshino', kind='model')


def test_model_jobs_publish_the_model_kind(model_env):
    from bmpt.webui.services import _changed_of
    env = model_env
    entry, = env.library.add([make_mod(env.parts[1], env.tmp / 'upload')])
    result = env.library.apply('hoshino', f'mod:{entry.id}', kind='model')
    assert _changed_of(result) == {'cores': ['hoshino_original'], 'kinds': ['model']}


def test_add_files_checks_the_content_kind_before_storing_anything(model_env, monkeypatch):
    env = model_env
    upload = make_mod(env.parts[1], env.tmp / 'upload')
    monkeypatch.setattr('bmpt.library.identity.content_cores', lambda path: {'hoshino_spr'})
    with pytest.raises(ServiceError, match='mod_unsupported'):
        env.library.add_files('model', [upload])
    assert env.library.mods() == []


@pytest.mark.parametrize('partial', [False, True])
def test_model_missing_official_preserves_outside_parts_and_marks_only_partial(model_env, partial):
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services

    env = model_env
    for path in env.parts:
        path.write_bytes(header_bundle(b'outside ' + path.name[-20:].encode()))
    outside = {path: path.read_bytes() for path in env.parts}
    uploads = [make_mod(path, env.tmp / 'incoming', payload=b'incoming ' + str(i).encode())
               for i, path in enumerate(env.parts[:1] if partial else env.parts)]
    result = env.library.add_and_apply(uploads)
    mod, = result
    assert result.applied == [mod] and not result.failed and result.kept_current
    assert bool(result.incomplete) is partial
    assert env.library.preview_incomplete(mod.id) is partial
    kept, = [entry for entry in env.library.mods() if entry.id != mod.id]
    assert {path.name: path.read_bytes() for path in kept.file.parent.glob('*.bundle')} == {
        path.name: data for path, data in outside.items()}
    assert not list((env.root / 'originals').glob('*.bundle'))
    with pytest.raises(ServiceError, match='original_unavailable'):
        env.library.restore('hoshino', kind='model')
    services = Services(env.settings, env.tmp / 'app', cache=object(), game=object(),
                        catalog=env.catalog, library=env.library)
    services.game_found = lambda: False
    try:
        family = build_api(services).call('family.get', {'key': 'hoshino'})
        assert 'error' not in family, family
        shown = next(m for v in family['result']['variants'] for m in v['modList'] if m['id'] == mod.id)
        assert shown.get('incomplete', False) is partial
    finally:
        services.close()


def test_model_overwritten_add_button_rpc_works_with_mods_off_without_scan(model_env):
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services

    env = model_env
    env.library._scan_allowed_input = lambda: False
    env.library.toggle(False, True)
    for path in env.parts:
        path.write_bytes(header_bundle(b'outside ' + path.name[-20:].encode()))
    outside = {path: path.read_bytes() for path in env.parts}
    game = SimpleNamespace(model_glb=lambda *args, **kwargs: pytest.fail('No official preview is available'))
    services = Services(env.settings, env.tmp / 'app', cache=object(), game=game,
                        catalog=env.catalog, library=env.library)
    try:
        with pytest.raises(ServiceError, match='original_unavailable'):
            services.asset('model/hoshino.glb', {'source': 'official'})
        api = build_api(services)
        pin = env.library.current_pin()
        response = api.call('mods.adopt', {'entries': [{'install': pin.key, 'core': 'hoshino_original'}]})
        assert 'error' not in response
        assert services.jobs.wait(response['result'], 10)
        job = services.jobs.get(response['result'])
        assert job.state == 'done' and not job.result['failed'] and not job.result['skipped']
        mod, = env.library.mods()
        assert mod.kind == 'model' and mod.target == 'hoshino'
        assert {path: path.read_bytes() for path in env.parts} == outside
        assert not list((env.root / 'originals').glob('*.bundle'))
    finally:
        services.close()
