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


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason='Set BAMT_TEST_GAME_DIR to a verified official view')
def test_official_model_slot_ownership(tmp_path, record_property):
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.library.judge import core_of
    from ba_modding_toolkit.searching import get_search_dirs

    directories = get_search_dirs(GAME_DIR)
    game = GameAssets(directories, AssetCache(allowed=False, root=tmp_path / 'cache'))
    catalog = Catalog(game, None, server='global')
    settings = Settings(tmp_path / 'config.toml')
    settings.set('game_resource_dir', str(GAME_DIR))
    library = ModLibrary(tmp_path / 'library', catalog, game, settings)
    cores = {core_of(path.name) for directory in directories for path in directory.glob('*.bundle')
             if kind_of_file(path.name) == 'model'}
    mapped = catalog.model_map(cores)
    all_variants = [v for f in catalog.families() for v in f.variants if v.model]
    variants = [v for v in all_variants if v.playable]
    expanded = {}
    for variant in variants:
        slot = [variant.model, *sorted(core for core in cores
                                      if core != variant.model and mapped.get(core) == variant.key)]
        assert all(mapped.get(core) == variant.key for core in slot), (variant.key, slot)
        assert not [(other.key, other.model) for other in all_variants
                    if other.key != variant.key and other.model in slot], (variant.key, slot)
        assert library.model_cores(variant.key) == slot
        assert {core_of(path.name) for path in library._game_files(variant.key, 'model')} == set(slot)
        if len(slot) > 1:
            expanded[variant.key] = slot
    assert expanded['ch0258'][:2] == ['ch0258_02', 'ch0258']
    record_property('expanded_model_slots', str(expanded))
    print(f'Model ownership: variants={len(variants)}; expanded={expanded}')


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason='Set BAMT_TEST_GAME_DIR to a verified official view')
@pytest.mark.parametrize('batch', [False, True], ids=['Add', 'Batch'])
@pytest.mark.parametrize('complete', [False, True], ids=['dependency-parts', 'complete-slot'])
def test_official_split_model_dependency_mod_on_clones(tmp_path, batch, complete, record_property):
    import hashlib
    import json
    import subprocess
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.gameassets.cabs import CabIndex
    from bmpt.library.integrity import Inspection
    from bmpt.library.judge import core_of
    from bmpt.library.storage import atomic_clone
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services
    from ba_modding_toolkit.searching import get_search_dirs
    from test_batch_import import analyze, run

    directories = get_search_dirs(GAME_DIR)
    cores = {'ch0258_02', 'ch0258'}
    official = [path for directory in directories for path in directory.glob('*.bundle')
                if kind_of_file(path.name) == 'model' and core_of(path.name) in cores]
    assert len(official) >= 12
    inspection = Inspection()
    references = set().union(*(inspection.inspect(path).references for path in official))
    supplied = CabIndex(directories).resolve(references, official[0])
    portraits = {path for directory in directories for path in directory.glob('*.bundle')
                 if core_of(path.name) == 'ch0258_spr'}
    assert portraits
    sources = set(official) | portraits | {Path(path) for path in supplied.values()}
    clone_dir = tmp_path / 'game'
    clone_dir.mkdir()
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    for path in sources:
        assert atomic_clone(path, clone_dir / path.name), 'The real-install write check requires native clones'
        (clone_dir / path.name).chmod(0o644)
    settings = Settings(tmp_path / 'config.toml')
    settings.set('game_resource_dir', str(clone_dir))
    settings.set('enable_crc_correction', 'false')
    settings.set('compression_method', 'none')
    game = GameAssets([clone_dir], AssetCache(allowed=False, root=tmp_path / 'cache'))
    catalog = Catalog(game, None, server='global')
    library = ModLibrary(tmp_path / 'library', catalog, game, settings)
    assert library.model_cores('ch0258') == ['ch0258_02', 'ch0258']
    official_bytes = {path: path.read_bytes() for path in clone_dir.glob('*.bundle')}
    slot_files = library._game_files('ch0258', 'model')
    targets = sorted(slot_files if complete else (
        path for path in slot_files if core_of(path.name) == 'ch0258' and '_mxdependency-' in path.name
        and any('_' + part + '_' in path.name for part in ('004', '006'))))
    assert len(targets) == (len(official) if complete else 2)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    uploads = []
    for path in targets:
        source = incoming / path.name
        assert atomic_clone(path, source)
        with source.open('ab') as stream:
            stream.write(b'BL-088 changed bytes')
        uploads.append(source)
    inspection.validate()
    del inspection
    services = Services(settings, tmp_path / 'app', cache=object(), game=game, catalog=catalog, library=library)
    services.game_found = lambda: False
    env = SimpleNamespace(services=services, rpc=build_api(services))
    try:
        row, = analyze(env, uploads)['entries']
        assert (row['kind'], row['core'], row['variant'], row['state']) == ('model', 'ch0258_02', 'ch0258', 'ready')
        assert row['paths'] == sorted(map(str, uploads))
        assert {file['target'] for file in row['files']} == {path.name for path in targets}
        if batch:
            result, = run(env, [row], enableAfterImport=complete, backup=False)
            assert result['outcome'] == 'imported'
            assert result['applied'] is complete
            mod, = library.mods()
        else:
            mod, = library.add_files('model', uploads)
        assert [entry.id for entry in library.mods()] == [mod.id]
        assert {path.name for path in library._paths(library._record(mod.id))} == {p.name for p in uploads}
        result = library.apply('ch0258', f'mod:{mod.id}', kind='model')
        assert not result.incomplete
        if batch and complete:
            assert result.unchanged
        else:
            assert set(result.files) == set(targets)
        assert {path for path, data in official_bytes.items() if path.read_bytes() != data} == set(targets)
        assert all(target.read_bytes() == upload.read_bytes() for target, upload in zip(targets, uploads))
        assert all((library.root / 'originals' / target.name).read_bytes() == official_bytes[target] for target in targets)
        family = env.rpc.call('family.get', {'key': 'hoshino'})['result']
        variant = next(v for v in family['variants'] if v['key'] == 'ch0258')
        assert variant['selection']['model']['installed']
        if shutil.which('node'):
            script = ('import {readFileSync} from "node:fs"; '
                      'import {candidates} from "./src/bmpt/web/js/switch-sources.js"; '
                      'const f=JSON.parse(readFileSync(0,"utf8")); const v=f.variants.find(v=>v.key==="ch0258"); '
                      'if(!candidates(f,v,"model").some(c=>c.id===' + json.dumps(mod.id) + ')) process.exit(1);')
            check = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps(family),
                                   cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=30)
            assert check.returncode == 0, check.stderr
        row, = analyze(env, uploads)['entries']
        assert row['state'] == ('done' if batch else 'library') and row['libraryId'] == mod.id
        library.restore('ch0258', kind='model')
        assert all(path.read_bytes() == data for path, data in official_bytes.items())
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
        record_property('cloned_model_files', len(official))
        record_property('cloned_shared_files', len(sources - set(official) - portraits))
        print(f'Hoshino clone workflow: slot_files={len(official)}; shared_files={len(sources - set(official) - portraits)}; changed={len(targets)}')
        print('Hoshino slot files: ' + ', '.join(sorted(path.name for path in official)))
    finally:
        services.close()


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


@pytest.fixture
def split_model_env(setup_library, request):
    env = setup_library
    env.variant_key, env.prefab_core, env.dependency_core, env.family_key = getattr(
        request, 'param', ('ch0258', 'ch0258_02', 'ch0258', 'hoshino'))
    game_bundle(env.game_dir, env.variant_key + '_spr')
    env.parts = [model_bundle(env.game_dir, env.prefab_core, prefab=True, part='000', payload=b'prefab'),
                 *[model_bundle(env.game_dir, env.dependency_core, part=part, payload=b'official ' + part.encode())
                   for part in ('004', '006', '007')]]
    env.excluded = [model_bundle(env.game_dir, 'droid_kaiserpmc_shield', part='000'),
                    model_bundle(env.game_dir, 'ch0258_01_enemy', part='000'),
                    model_bundle(env.game_dir, 'hoshino_original', prefab=True, part='000')]
    env.official = {path: path.read_bytes() for path in [*env.parts, *env.excluded]}
    game = SimpleNamespace(spine_cores=lambda kind: {'hoshino_spr': [], env.variant_key + '_spr': []} if kind == 'spr' else {},
                           collection_names=lambda: {}, model_cores=lambda: {env.prefab_core, 'hoshino_original'})
    env.catalog = Catalog(game, None)
    env.library = ModLibrary(env.root, env.catalog, object(), env.settings, scan_allowed=lambda: True)
    return env


@pytest.fixture
def split_model_app(split_model_env):
    from bmpt.webui.api import build_api
    from bmpt.webui.services import Services

    env = split_model_env
    env.previews = []
    game = SimpleNamespace(model_glb=lambda core, overrides=(), reads=None:
                           env.previews.append((core, list(overrides), reads)) or b'glTF')
    env.services = Services(env.settings, env.tmp / 'app', cache=object(), game=game,
                            catalog=env.catalog, library=env.library)
    env.services.game_found = lambda: False
    env.rpc = build_api(env.services)
    yield env
    env.services.close()


def test_split_model_slot_uses_only_its_own_install_cores(split_model_env):
    env = split_model_env
    library = env.library
    pin = library.current_pin()
    assert library.model_cores('ch0258') == ['ch0258_02', 'ch0258']
    assert library._core_map(pin).core('ch0258', 'model') == 'ch0258_02'
    assert library._game_files('ch0258', 'model') == env.parts
    assert {file.file for file in library.state('ch0258').files if file.kind == 'model'} == set(env.parts)
    assert set(library.official_bundles('ch0258')) == set(env.parts)
    assert set(library.official_bundles('ch0258_02')) == set(env.parts)
    assert library.model_cores('hoshino') == ['hoshino_original']
    # A second install never borrows dependencies present only in the current one.
    other = env.tmp / 'second-game'
    model_bundle(other, 'ch0258_02', prefab=True, part='000')
    other_pin = library._pin(other, 'global')
    assert library.model_cores('ch0258', other_pin) == ['ch0258_02']
    assert len(library._game_files('ch0258', 'model', other_pin)) == 1
    # Even if that install loses its prefab file, the remaining dependencies keep the prefab choice key.
    for path in other.glob('*.bundle'):
        path.unlink()
    dependency = model_bundle(other, 'ch0258', part='004')
    assert library.model_cores('ch0258', other_pin) == ['ch0258_02', 'ch0258']
    assert library._game_files('ch0258', 'model', other_pin) == [dependency]
    assert library._core_map(other_pin).slot('ch0258') == 'ch0258_02'


@pytest.mark.parametrize('batch', [False, True], ids=['Add', 'Batch'])
def test_split_model_dependency_add_apply_switch_and_restore(split_model_app, batch):
    from test_batch_import import analyze, run

    env = split_model_app
    library = env.library
    uploads = [make_mod(path, env.tmp / 'incoming', payload=b'mod ' + str(i).encode())
               for i, path in enumerate(env.parts[1:3])]
    assert all(library.identify_import(path, path.name) == (path.name, 'ch0258', 'model') for path in uploads)
    if batch:
        row, = analyze(env, uploads)['entries']
        assert (row['core'], row['variant'], row['state']) == ('ch0258_02', 'ch0258', 'ready')
        result, = run(env, [row], enableAfterImport=False)
        assert result['outcome'] == 'imported'
        mod, = library.mods()
    else:
        mod, = library.add_files('model', uploads)
    assert mod.core == 'ch0258' and mod.target == 'ch0258'
    assert {path.name for path in library.preview_bundles(mod.id)} == {path.name for path in env.parts}
    result = library.apply('ch0258', f'mod:{mod.id}', kind='model')
    assert set(result.files) == set(env.parts[1:3]) and not result.incomplete
    assert library.apply('ch0258', f'mod:{mod.id}', kind='model').unchanged
    assert all(path.read_bytes() == upload.read_bytes() for path, upload in zip(env.parts[1:3], uploads))
    assert all(path.read_bytes() == data for path, data in env.official.items() if path not in env.parts[1:3])
    assert library.configured('ch0258')['model'] == f'mod:{mod.id}'
    assert library.selection('ch0258')['model']['installed']
    choices = library.profiles.read()['folders'][library.current_pin().key]['configured']
    assert set(choices) == {'ch0258_02'}
    hidden = library.profiles.read()
    pin = library.current_pin()
    hidden['folders'][pin.key]['hidden'] = [library.data_id]
    assert library._hidden_items(hidden) == [{'install': pin.key, 'core': 'ch0258_02', 'why': 'pending'}]
    entries = [entry for entry in library.entries() if entry.kind == 'model']
    entry, = entries
    assert entry.core == 'ch0258_02' and len(entry.files) == len(env.parts)
    filtered, = library.scan(cores={'ch0258'}).entries
    assert filtered.core == 'ch0258_02' and len(filtered.files) == len(env.parts)
    state = [part for part in library.state('ch0258').files if part.kind == 'model']
    assert {part.applied for part in state} == {f'mod:{mod.id}'} and all(part.restorable for part in state)
    family = env.rpc.call('family.get', {'key': 'hoshino'})['result']
    shown = next(v for v in family['variants'] if v['key'] == 'ch0258')
    assert shown['modelCores'] == ['ch0258_02', 'ch0258']
    assert {part['file'] for part in shown['modelFiles']} == set(map(str, env.parts))
    assert shown['selection']['model']['installed']
    row, = analyze(env, uploads)['entries']
    assert row['libraryId'] == mod.id and row['state'] == ('done' if batch else 'library')
    # Another choice replaces its own part and returns the previous choice's parts to official.
    second, = library.add([make_mod(env.parts[0], env.tmp / 'second', payload=b'other prefab')])
    library.apply('ch0258', f'mod:{second.id}', kind='model')
    assert all(path.read_bytes() == env.official[path] for path in env.parts[1:])
    library.apply('ch0258', f'mod:{mod.id}', kind='model')
    assert not library.toggle(False, True).failed
    assert all(path.read_bytes() == data for path, data in env.official.items())
    assert all((env.root / 'originals' / path.name).read_bytes() == env.official[path] for path in env.parts[1:3])
    assert not library.toggle(True, False).failed
    assert library.selection('ch0258')['model']['installed']
    library.restore('ch0258', kind='model')
    assert library.configured('ch0258')['model'] is None
    assert all(path.read_bytes() == data for path, data in env.official.items())


@pytest.mark.parametrize('split_model_env', [
    ('ch0258', 'ch0258_02', 'ch0258', 'hoshino'),
    ('ch0175', 'ch0175', 'wakamo_swimsuit_original', 'wakamo'),
], indirect=True, ids=['Hoshino-Armed', 'Wakamo-Swimsuit'])
@pytest.mark.parametrize('batch', [False, True], ids=['Add', 'Batch'])
def test_complete_split_model_is_one_row_one_mod_and_one_choice(split_model_app, batch):
    from test_batch_import import analyze, run

    env = split_model_app
    library = env.library
    uploads = [make_mod(path, env.tmp / 'incoming', payload=b'complete mod ' + str(i).encode())
               for i, path in enumerate(env.parts)]
    row, = analyze(env, uploads)['entries']
    assert (row['core'], row['variant'], row['state']) == (env.prefab_core, env.variant_key, 'ready')
    assert row['paths'] == sorted(map(str, uploads))
    assert {file['target'] for file in row['files']} == {path.name for path in env.parts}
    if batch:
        result, = run(env, [row], enableAfterImport=True, backup=False)
        assert result['outcome'] == 'imported' and result['applied'] and result['configured']
        mod, = library.mods()
    else:
        mod, = library.add_files('model', uploads)
        applied = library.apply(env.variant_key, f'mod:{mod.id}', kind='model')
        assert set(applied.files) == set(env.parts)
    assert [entry.id for entry in library.mods()] == [mod.id]
    assert {path.name for path in library._paths(library._record(mod.id))} == {path.name for path in uploads}
    assert all(target.read_bytes() == source.read_bytes() for target, source in zip(env.parts, uploads))
    assert all(path.read_bytes() == env.official[path] for path in env.excluded)
    assert all((env.root / 'originals' / path.name).read_bytes() == env.official[path] for path in env.parts)
    choices = library.profiles.read()['folders'][library.current_pin().key]['configured']
    assert set(choices) == {env.prefab_core} and choices[env.prefab_core]['source'] == f'mod:{mod.id}'
    assert library.selection(env.variant_key)['model']['installed']
    assert library.add(list(reversed(uploads))).duplicates == [mod]
    row, = analyze(env, uploads)['entries']
    assert row['libraryId'] == mod.id and row['core'] == env.prefab_core
    restored = library.restore(env.variant_key, kind='model')
    assert set(restored.files) == set(env.parts)
    assert all(path.read_bytes() == data for path, data in env.official.items())
    assert library.configured(env.variant_key)['model'] is None


def test_complete_split_model_keeps_existing_partial_mods_separate(split_model_app):
    from test_batch_import import analyze

    env = split_model_app
    uploads = [make_mod(path, env.tmp / 'incoming', payload=b'complete mod ' + str(i).encode())
               for i, path in enumerate(env.parts)]
    prefab, = env.library.add(uploads[:1])
    dependency, = env.library.add(uploads[1:])
    existing = {mod.id: env.library._output_identity(env.library._paths(env.library._record(mod.id)))
                for mod in (prefab, dependency)}
    complete, = env.library.add(uploads)
    assert len(env.library.mods()) == 3 and complete.id not in existing
    assert all(env.library._output_identity(env.library._paths(env.library._record(mod_id))) == files
               for mod_id, files in existing.items())
    row, = analyze(env, uploads)['entries']
    assert row['libraryId'] == complete.id


def test_split_model_release_split_groups_dependencies_with_their_prefab_version(split_model_app):
    from test_batch_import import analyze

    env = split_model_app
    current = [make_mod(path, env.tmp / 'incoming', payload=b'current ' + str(i).encode())
               for i, path in enumerate(env.parts)]
    older = model_bundle(current[0].parent, env.prefab_core, prefab=True, part='000',
                         date='2025-01-01', payload=b'older prefab')
    expected = sorted([sorted(path.name for path in current), [older.name]])
    rows = analyze(env, [*current, older])['entries']
    assert sorted(sorted(Path(path).name for path in row['paths']) for row in rows) == expected
    assert {row['core'] for row in rows} == {env.prefab_core}
    mods = env.library.add([*current, older])
    assert sorted(sorted(path.name for path in env.library._paths(env.library._record(mod.id)))
                  for mod in mods) == expected


def test_split_model_rejects_foreign_and_mixed_core_mods(split_model_env):
    env = split_model_env
    foreign, = env.library.add([make_mod(env.excluded[-1], env.tmp / 'foreign')])
    with pytest.raises(ServiceError, match='mod_target_mismatch'):
        env.library.apply('ch0258', f'mod:{foreign.id}', kind='model')
    # Even a record targeted at the correct variant cannot smuggle a foreign core alongside its own.
    own = make_mod(env.parts[1], env.tmp / 'mixed')
    other = make_mod(env.excluded[-1], own.parent)
    record = env.library._store({own.name: own, other.name: other}, 'mixed', 'ch0258', 'model')
    with pytest.raises(ServiceError, match='mod_target_mismatch'):
        env.library.apply('ch0258', f'mod:{record["id"]}', kind='model')
    assert all(path.read_bytes() == data for path, data in env.official.items())


def test_split_model_existing_dependency_mod_is_adopted_under_one_choice(split_model_env):
    env = split_model_env
    target = env.parts[1]
    upload = make_mod(target, env.tmp / 'incoming')
    target.with_name(target.name + '.backup').write_bytes(env.official[target])
    target.write_bytes(upload.read_bytes())
    result = env.library.add([upload])
    mod, = result
    assert result.installed == [mod]
    assert env.library.configured('ch0258')['model'] == f'mod:{mod.id}'
    choice, = env.library.entries()
    assert choice.core == 'ch0258_02' and {file.file for file in choice.files} == set(env.parts)
    env.library.restore('ch0258', kind='model')
    assert all(path.read_bytes() == data for path, data in env.official.items())


@pytest.mark.parametrize('outside', ['prefab', 'dependency'])
def test_split_model_incomplete_and_unrestorable_cover_the_whole_slot(split_model_app, outside):
    env = split_model_app
    path = env.parts[0 if outside == 'prefab' else 3]
    path.write_bytes(header_bundle(b'outside bytes'))
    mod, = env.library.add([make_mod(env.parts[1], env.tmp / 'incoming')])
    assert env.library.preview_incomplete(mod.id)
    result = env.library.apply('ch0258', f'mod:{mod.id}', kind='model')
    assert result.incomplete and result.kept_current
    assert env.library.selection('ch0258')['model']['incomplete']
    family = env.rpc.call('family.get', {'key': 'hoshino'})['result']
    variant = next(v for v in family['variants'] if v['key'] == 'ch0258')
    assert next(m for m in variant['modList'] if m['id'] == mod.id)['incomplete']
    assert next(p for p in variant['modelFiles'] if p['file'] == str(path))['restorable'] is False
    with pytest.raises(ServiceError, match='original_unavailable'):
        env.library.restore('ch0258', kind='model')
    assert not (env.root / 'originals' / path.name).exists()


def test_split_model_drift_and_needs_fix_belong_to_prefab_entry(split_model_app):
    env = split_model_app
    target = env.parts[1]
    mod, = env.library.add_and_apply([make_mod(target, env.tmp / 'incoming', fixed=True)])
    # A record of a write can carry Needs Fix on any dependency, including the file table's copy.
    env.library._applied[str(target.resolve())]['crc'] = 'needs_fix'
    env.library._save()
    family = env.rpc.call('family.get', {'key': 'hoshino'})['result']
    variant = next(v for v in family['variants'] if v['key'] == 'ch0258')
    assert next(p for p in variant['modelFiles'] if p['file'] == str(target))['crc'] == 'needs_fix'
    target.write_bytes(env.official[target])
    entry, = [e for e in env.library.scan().entries if e.kind == 'model']
    assert entry.core == 'ch0258_02' and entry.state == 'reset'
    assert not env.library.selection('ch0258')['model']['installed']
    assert not env.library.restore_drift([{'install': entry.install, 'core': entry.core}]).failed
    assert target.read_bytes() == mod.file.read_bytes()


def test_split_model_batch_enable_after_import_and_deferred_choice(split_model_app):
    from test_batch_import import analyze, run

    env = split_model_app
    assert not env.library.toggle(False, True).failed
    upload = make_mod(env.parts[1], env.tmp / 'incoming')
    row, = analyze(env, [upload])['entries']
    result, = run(env, [row], enableAfterImport=True)
    assert result['configured'] and not result['applied']
    assert env.library.configured('ch0258')['model'] == 'mod:' + result['libraryId']
    assert all(path.read_bytes() == data for path, data in env.official.items())
    assert not env.library.toggle(True, False).failed
    assert env.parts[1].read_bytes() == upload.read_bytes()
    assert env.library.selection('ch0258')['model']['installed']
    assert not env.library.remove(result['libraryId'], confirm=True).failed
    assert all(path.read_bytes() == data for path, data in env.official.items())


def test_split_model_journal_rolls_back_prefab_and_dependency_together(split_model_env, monkeypatch):
    import json
    from bmpt.library import service

    env = split_model_env
    targets = env.parts[:2]
    uploads = [make_mod(path, env.tmp / 'incoming') for path in targets]
    mod, = env.library.add(uploads)
    copy = service.atomic_copy
    written = []

    def fail_second(source, target):
        if target in targets and source.parent == mod.file.parent:
            journal, = (env.root / 'transactions').glob('*/journal.json')
            data = json.loads(journal.read_text())
            assert data['phase'] == 'writing'
            assert {Path(item['target']) for item in data['files']} >= set(targets)
            written.append(target)
            if len(written) == 2:
                raise PermissionError('synthetic failed second slot part')
        return copy(source, target)

    monkeypatch.setattr(service, 'atomic_copy', fail_second)
    with pytest.raises(ServiceError, match='game_file_locked'):
        env.library.apply('ch0258', f'mod:{mod.id}', kind='model')
    assert len(written) == 2
    assert all(path.read_bytes() == data for path, data in env.official.items())
    assert not list((env.root / 'transactions').glob('*/journal.json'))
    assert env.library.configured('ch0258')['model'] == f'mod:{mod.id}'
    assert env.library.selection('ch0258')['model']['error']['code'] == 'game_file_locked'


def test_split_model_preview_sources_use_prefab_and_every_own_core(split_model_app):
    from test_batch_import import analyze

    env = split_model_app
    upload = make_mod(env.parts[1], env.tmp / 'incoming')
    row, = analyze(env, [upload])['entries']
    assert env.services.asset('model/ch0258.glb', {'source': 'import:' + row['preview']['key']}).body == b'glTF'
    assert env.previews[-1][0] == 'ch0258_02'
    assert set(env.previews[-1][1]) == {upload, env.parts[0], *env.parts[2:]}
    mod, = env.library.add_and_apply([upload])
    assert env.services.asset('model/ch0258.glb', {'source': f'mod:{mod.id}'}).body == b'glTF'
    assert env.previews[-1][0] == 'ch0258_02' and mod.file in env.previews[-1][1]
    env.services.asset('model/ch0258.glb', {'source': 'official'})
    assert {p.name: p.read_bytes() for p in env.previews[-1][1]} == {p.name: env.official[p] for p in env.parts}
    # No official bytes remain for this omitted part: only the preview may use its current bytes.
    env.parts[3].write_bytes(header_bundle(b'outside dependency'))
    env.services.asset('model/ch0258.glb', {'source': 'official'})
    assert env.parts[3] in env.previews[-1][1]
    with pytest.raises(ServiceError, match='original_unavailable'):
        env.library.official_bundles('ch0258_02')
    for bundles in ([env.excluded[0]], [upload, env.excluded[-1]]):
        env.services.batch_import = SimpleNamespace(preview=lambda key: ('ch0258', bundles))
        with pytest.raises(KeyError):
            env.services.asset('model/ch0258.glb', {'source': 'import:wrong'})
    foreign, = env.library.add([make_mod(env.excluded[-1], env.tmp / 'foreign')])
    with pytest.raises(KeyError):
        env.services.asset('model/ch0258.glb', {'source': f'mod:{foreign.id}'})


def test_model_cards_read_other_cores_by_their_source_whatever_the_mods_switch(split_model_app):
    """A 3D look split across cores (the owner's case: a mesh mod for ch0263 and a texture mod for the NPC en0002,
    whose bundle holds ch0263's textures): the Original card reads the textures' official bytes and a mod card the
    configured texture mod, with mods on and off; a bundle no mod touches is read where it is."""
    env = split_model_app
    texture = game_bundle(env.game_dir, 'en0002', category='npcs', part='005', payload=b'official texture')
    official = texture.read_bytes()
    untouched = env.excluded[1]
    mesh, = env.library.add_and_apply([make_mod(env.parts[1], env.tmp / 'mesh')])
    skin, = env.library.add_and_apply([make_mod(texture, env.tmp / 'skin', payload=b'modded texture')])
    modded = header_bundle(b'modded texture')
    assert texture.read_bytes() == modded

    def read(source, path):
        assert env.services.asset('model/ch0258.glb', {'source': source}).body == b'glTF'
        return env.previews[-1][2].resolve(path)

    for enabled in (True, False):
        if not enabled:
            env.library.toggle(False, True)
            assert texture.read_bytes() == official and env.parts[1].read_bytes() == env.official[env.parts[1]]
        assert read('official', texture).read_bytes() == official
        assert read(f'mod:{mesh.id}', texture).read_bytes() == modded
        assert read('official', untouched) == read(f'mod:{mesh.id}', untouched) == untouched
    assert env.library.configured('en0002')['model'] == f'mod:{skin.id}'


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
    expected_core = 'hoshino' if name == 'Hoshino_Body' else 'hoshino_original'
    assert (parse_filename(filename).core, key, kind) == (expected_core, 'hoshino', 'model')


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


def test_model_costume_of_itself_is_refused(model_env):
    # another variant's costume is BL-117's (test_model_adapt.py); a variant's own model is no costume source
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
    upload = upload.rename(upload.with_name(upload.name.replace('hoshino_original', 'renamed')))
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
    previews = []
    game = SimpleNamespace(model_glb=lambda core, **kwargs: previews.append(kwargs['overrides']) or b'preview')
    services = Services(env.settings, env.tmp / 'app', cache=object(), game=game,
                        catalog=env.catalog, library=env.library)
    try:
        assert services.asset('model/hoshino.glb', {'source': 'official'}).body == b'preview'
        assert set(previews[-1]) == set(env.parts)
        with pytest.raises(ServiceError, match='original_unavailable'):
            env.library.official_bundles('hoshino_original')
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


@pytest.mark.skipif(not GAME_DIR.is_dir(), reason='Set BAMT_TEST_GAME_DIR to a verified official view')
def test_official_roster_partial_and_complete_mod_preflight(tmp_path, monkeypatch, record_property):
    """Every official part is a valid partial Mod, with or without recoverable originals.

    One inspection per core is shared by all parts, states and checks. Clones live only
    for that core; the read-only development view is never linked or modified.
    BAMT_ROSTER_FULL=1 extends both missing-original states to the entire roster.
    """
    import time
    from functools import cache
    from bmpt.config import Settings
    from bmpt.gameassets import AssetCache, GameAssets
    from bmpt.library import integrity
    from bmpt.library.storage import atomic_clone
    from bmpt.openfiles import temporary_directory
    from ba_modding_toolkit.searching import get_search_dirs

    started = time.perf_counter()
    directories = get_search_dirs(GAME_DIR)
    game = GameAssets(directories, AssetCache(allowed=False, root=tmp_path / 'cache'))
    catalog = Catalog(game, None, server='global')
    # This roster and read-only install stay fixed throughout the sweep. Resolve each
    # core once instead of repeatedly copying all catalog metadata for every part.
    for method in ('variant_for_model_core', 'variant_for_asset_core', 'variant_for_core'):
        monkeypatch.setattr(catalog, method, cache(getattr(catalog, method)))
    settings = Settings(tmp_path / 'config.toml')
    settings.set('game_resource_dir', str(GAME_DIR))
    library = ModLibrary(tmp_path / 'library', catalog, game, settings)
    pin = library.current_pin()
    variants = [catalog.variant(variant.key) for family in catalog.families() for variant in family.variants
                if variant.playable]
    required = {'ch0258', 'airi', 'cherino', 'hoshino', 'ch0156', 'hare', 'chinatsu', 'akari', 'aru', 'ch0164', 'hihumi'}
    fixed = required | {'shiroko', 'serika', 'ayane', 'iori', 'hina', 'ako', 'yuuka', 'noa', 'asuna', 'karin',
                        'neru', 'natsu', 'koharu', 'hanako', 'azusa', 'mika', 'nagisa', 'shun', 'haruna',
                        'mutsuki', 'saya', 'tsubaki', 'tsurugi', 'izumi', 'fuuka', 'juri'}
    sample = {variant.key for variant in variants if variant.key in fixed}
    assert required <= sample and len(sample) >= 30
    sample_cores = {getattr(variant, kind) for variant in variants if variant.key in sample
                    for kind in ('spr', 'lobby', 'model')}
    full = os.environ.get('BAMT_ROSTER_FULL') == '1'
    official_for = library._official_for
    counters = {'official': 0, 'replaced': 0, 'all': 0, 'complete': 0, 'identified': 0}
    failures, seen = [], set()

    def check(label, action):
        try:
            action()
        except ServiceError as error:
            failures.append((label, error.code, error.params))

    for variant in variants:
        for kind in ('spr', 'lobby', 'model'):
            core = getattr(variant, kind)
            if not core or core in seen:
                continue
            seen.add(core)
            if len(seen) % 100 == 0:
                print(f'Roster progress: cores={len(seen)}; parts={counters}; seconds={time.perf_counter() - started:.3f}', flush=True)
            targets = library._spine_parts(core, pin)
            assert targets, (variant.key, kind, core)
            with temporary_directory(prefix='roster-mod-', dir=tmp_path) as temporary:
                folder = Path(temporary)
                for target in targets:
                    assert atomic_clone(target, folder / target.name), 'The roster check requires native file clones'
                files = {target.name: folder / target.name for target in targets}
                inspection = integrity.Inspection()
                # Inputs are fixed clones of a read-only view. Cache the derived snapshot
                # per path; validate all revisions after the core, and during direct import.
                inspection.inspect = cache(inspection.inspect)
                inspection.metadata = cache(inspection.metadata)
                states = ('official', 'replaced', 'all') if full or core in sample_cores else ('official',)
                for state in states:
                    replaced = set()

                    def baseline(path, *args, **kwargs):
                        if state == 'all' or state == 'replaced' and path in replaced:
                            raise ServiceError('original_unavailable', file=path.name)
                        return official_for(path, *args, **kwargs)

                    monkeypatch.setattr(library, '_official_for', baseline)
                    for target in targets:
                        source = files[target.name]
                        replaced = {target}
                        label = f'{variant.key}/{kind}/{state}/{target.name}'
                        check(label + '/Add', lambda: library._check_group(
                            {target.name: source}, pin, compatibility=False, inspection=inspection))
                        check(label + '/Import', lambda: library.inspect_import(
                            {target.name: source}, pin, conversion=True, inspection=inspection))
                        counters[state] += 1
                        if state == 'official':
                            identity = library.identify_import(source, source.name, pin)
                            assert identity[0] == source.name and identity[2] == kind
                            counters['identified'] += 1
                    if full or core in sample_cores:
                        replaced = set(targets)
                        check(f'{variant.key}/{kind}/{state}/complete', lambda: library._check_group(
                            files, pin, compatibility=False, inspection=inspection))
                        # Exercise Add's identification, grouping and storage as well as its check.
                        # Reuse the per-core snapshot, and remove the stored clone before the next slot.
                        original_check = library._check_group
                        with monkeypatch.context() as patch:
                            patch.setattr(library, '_check_group', lambda *args, **kwargs: original_check(
                                *args, **{**kwargs, 'inspection': inspection}))
                            try:
                                added = library.add(list(files.values()), pin=pin)
                                assert len(added) + len(added.duplicates) == 1, (variant.key, kind, state)
                                for mod in [*added, *added.duplicates]:
                                    library.remove(mod.id)
                            except ServiceError as error:
                                failures.append((f'{variant.key}/{kind}/{state}/Add-complete', error.code, error.params))
                        counters['complete'] += 1
                monkeypatch.setattr(library, '_official_for', official_for)
                inspection.validate()
                # Release environments before Windows removes the core's temporary clones.
                del inspection
    elapsed = time.perf_counter() - started
    record_property('roster_counts', str(counters))
    record_property('roster_seconds', round(elapsed, 3))
    print(f'Roster preflight: {counters}; variants={len(variants)}; sample={len(sample)}; seconds={elapsed:.3f}; full={full}')
    assert not failures, failures[:30]


def test_costume_verdicts_are_kept_per_build_signature(model_env, monkeypatch):
    """BL-122: a pair's verdict holds for the program and the official bytes it was judged on; the rule's refusals are
    saved by the preview itself."""
    from bmpt.gameassets import model_adapt
    from bmpt.library import service

    env = model_env
    model_bundle(env.game_dir, core='hoshino_swimsuit', prefab=True, part='000', payload=b'swim prefab')
    game = SimpleNamespace(spine_cores=lambda kind: {'hoshino_spr': [], 'hoshino_swimsuit_spr': []} if kind == 'spr' else {},
                           collection_names=lambda: {}, model_cores=lambda: {'hoshino_original', 'hoshino_swimsuit'})
    library = ModLibrary(env.root, Catalog(game, None), object(), env.settings, scan_allowed=lambda: True)
    assert library.costume_verdict('hoshino', 'hoshino_swimsuit') is None

    def refused(self, other, targets, directory, log, pin, *, preview=False, report=None):
        raise model_adapt.refuse('stretch', adapted=8.84, own=5.85)

    monkeypatch.setattr(ModLibrary, '_stage_costume', refused)
    with pytest.raises(ServiceError, match='source_unsupported'):
        library.costume_preview('hoshino', 'hoshino_swimsuit')
    verdict = library.costume_verdict('hoshino', 'hoshino_swimsuit')
    assert (verdict['ok'], verdict['reason'], verdict['detail']) == (
        False, 'stretch', {'source': 'stretch', 'adapted': '8.84', 'own': '5.85'})
    # another library over the same root reads it; a new program build judges again
    assert ModLibrary(env.root, Catalog(game, None), object(), env.settings).costume_verdict(
        'hoshino', 'hoshino_swimsuit')['ok'] is False
    monkeypatch.setattr(service, '_build_version', lambda: 'next:build')
    assert library.costume_verdict('hoshino', 'hoshino_swimsuit') is None
    assert library.record_costume_verdict('hoshino', 'hoshino_swimsuit', True)['ok'] is True
    assert library.costume_verdict('hoshino', 'hoshino_swimsuit')['ok'] is True
    # bounded, newest kept; an unreadable file reads as none
    monkeypatch.setattr(service, 'COSTUME_VERDICTS', 2)
    for i in range(3):
        library._save_verdict(f'signature-{i}', False, 'stretch')
    assert sorted(library._verdicts()) == ['signature-1', 'signature-2']
    # a pair judged unfit or refused is never saved as a choice or written, mods on or off
    library._save_verdict(library._costume_signature('hoshino', 'hoshino_swimsuit', library.current_pin())[0], False,
                          'stretch', preview=True)
    assert library.costume_verdict('hoshino', 'hoshino_swimsuit')['preview'] is True
    for enabled in (True, False):
        if not enabled:
            library.toggle(False, True)
        with pytest.raises(ServiceError, match='source_unsupported'):
            library.apply('hoshino', 'variant:hoshino_swimsuit', kind='model')
        assert library.configured('hoshino').get('model') is None
    (env.root / 'costume-verdicts.json').write_text('{not json')
    assert library.costume_verdict('hoshino', 'hoshino_swimsuit') is None
    with pytest.raises(ServiceError, match='source_unsupported'):
        library.costume_verdict('hoshino', 'hoshino')
