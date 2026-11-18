"""Complete ZIP publication and target preparation using isolated library fixtures."""

import json
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from dataclasses import replace

import pytest

from bmpt.core.errors import ServiceError
from ba_modding_toolkit.bundle import Bundle
from bmpt.library import ba_mod, integrity
from bmpt.library import targets as resolver
from bmpt.webui import batch_import
from bmpt.webui import export_service
from bmpt.webui.rpc import RpcRegistry
from bmpt.webui.services import Services

from test_library import game_bundle, make_mod, setup_library  # noqa: F401 - fixtures
from test_mod_origin import REAL_SHARING_KEYS, inspect_bundle, bundle_metadata, official_bundle, old_bundle


def services(env):
    stub = SimpleNamespace(
        settings=env.settings, library=env.library, state=SimpleNamespace(get=lambda *_: {}), upgrade=None
    )
    stub.check_output = lambda destination: Services.check_output(stub, destination)
    return stub


def run(env, scope="library", target=None, progress=lambda *_: None):
    return export_service.export_zip(
        services(env),
        env.library,
        env.library.current_pin(),
        scope=scope,
        target=target,
        folder=env.tmp / "exports",
        server="global",
        platform="windows",
        resource_type=None,
        log=lambda *_: None,
        progress=progress,
    )


def contents(result):
    with zipfile.ZipFile(result["archive"]) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    # ba-mod.json is the only metadata file an export writes
    assert not any(PurePosixPath(name).name == "manifest.json" for name in files)
    packages = {
        name.rpartition("/")[0]: json.loads(data)
        for name, data in files.items()
        if PurePosixPath(name).name == ba_mod.FILE_NAME
    }
    return packages, files


def test_library_export_prepares_existing_mods_without_mutating_library_or_game(setup_library):
    env = setup_library
    # A Windows export gives each file the CRC fix (auto follows the install); a fixed mod passes through unchanged.
    source = make_mod(env.original, env.tmp / "upload", fixed=True)
    (mod,) = env.library.add([source])
    state = (env.root / "state.json").read_bytes()
    game = env.original.read_bytes()
    result = run(env)
    packages, files = contents(result)
    assert result["count"] == 1
    directory = export_service._safe_name(f"mod:{mod.id}")
    assert set(packages) == {directory}
    assert packages[directory]["target"] == {"server": "global", "platform": "windows"}
    assert files[f"{directory}/{source.name}"] == source.read_bytes()
    assert (env.root / "state.json").read_bytes() == state
    assert env.original.read_bytes() == game
    assert sorted(path.suffix for path in (env.tmp / "exports").iterdir()) == [".zip"]


def test_an_export_names_each_mod_and_its_step_on_the_progress_line(setup_library):
    env = setup_library
    (mod,) = env.library.add([make_mod(env.original, env.tmp / "upload", fixed=True)])
    reported = []
    run(env, progress=lambda *args: reported.append(args))
    lines = [p for (p,) in reported]
    assert lines and all(
        p["current"] == 1
        and p["total"] == 1
        and p["action"] == "export"
        and p["alt"] == mod.target
        and p["resource_type"] == mod.resource_type
        and p["core"] == mod.core
        for p in lines
    )
    assert lines[0]["step"] == "import.step.export" and lines[-1]["fraction"] == 1.0
    assert [p["fraction"] for p in lines] == sorted(p["fraction"] for p in lines)


def test_applied_uses_desired_manifest_while_disabled(setup_library):
    env = setup_library
    source = make_mod(env.original, env.tmp / "upload", fixed=True)
    (mod,) = env.library.add([source])
    other = make_mod(env.original, env.tmp / "other", payload=b"other mod")
    env.library.add([other])
    env.library.profiles.update(lambda data: data.update(enabled=False))
    result = env.library.apply("test", f"mod:{mod.id}")
    assert result.deferred
    packages, files = contents(run(env, "applied"))
    directory = export_service._safe_name(f"mod:{mod.id}")
    assert set(packages) == {directory}
    assert files[f"{directory}/{source.name}"] == source.read_bytes()
    assert not env.library.profiles.read()["enabled"]


@pytest.mark.parametrize("stage", ["prepare", "write", "verify", "hash", "publish"])
def test_failed_export_never_publishes_partial_archive(setup_library, monkeypatch, stage):
    env = setup_library
    for i in range(2):
        env.library.add([make_mod(env.original, env.tmp / str(i), payload=f"mod {i}".encode())])
    before = env.tmp / "exports"
    before.mkdir()
    existing = before / "existing.zip"
    existing.write_bytes(b"previous complete archive")
    if stage == "prepare":
        original = export_service._prepare
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            return original(*args, **kwargs)

        monkeypatch.setattr(export_service, "_prepare", fail_second)
    elif stage == "verify":
        monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda _: "damaged.bundle")
    elif stage == "hash":
        monkeypatch.setattr(zipfile.ZipFile, "write", lambda archive, path, name: archive.writestr(name, b"changed"))
    else:

        def fail(*args, **kwargs):
            raise OSError("locked output")

        monkeypatch.setattr(
            zipfile.ZipFile if stage == "write" else export_service.os, "write" if stage == "write" else "replace", fail
        )
    with pytest.raises((OSError, ServiceError)):
        run(env)
    assert list(before.iterdir()) == [existing]
    assert existing.read_bytes() == b"previous complete archive"


def test_empty_applied_export_has_no_zip(setup_library):
    env = setup_library
    with pytest.raises(ServiceError, match="export_empty"):
        run(env, "applied")
    assert not list((env.tmp / "exports").iterdir())


def test_export_paths_are_portable_collision_resistant_and_stable():
    values = ["../escape", "..\\escape", "CON", "a/b", "a\\b", "A", "a", "", "a" * 200]
    paths = [export_service._safe_name(value) for value in values]
    assert len({value.casefold() for value in paths}) == len(paths)
    assert paths == [export_service._safe_name(value) for value in values]
    assert all("/" not in value and "\\" not in value and not value.startswith(".") for value in paths)
    for value in ("bad\\name.bundle", "bad:name.bundle", "NUL.bundle", "name. "):
        with pytest.raises(ServiceError):
            export_service._member_name(Path(value))


def test_unavailable_platform_is_rejected_instead_of_relabelled(setup_library):
    env = setup_library
    with pytest.raises(ServiceError, match="export_target_unavailable"):
        export_service._target_pin(env.library, env.settings, "global", "android")


@pytest.mark.parametrize("backup", [False, True])
def test_single_original_alt_archive_uses_current_bundle_names(setup_library, backup):
    env = setup_library
    expected = env.original.read_bytes()
    if backup:
        env.original.with_name(env.original.name + ".backup").write_bytes(expected)
        env.original.write_bytes(make_mod(env.original, env.tmp / "external").read_bytes())
    _, files = contents(run(env, "single", "alt:test"))
    assert files[env.original.name] == expected


@pytest.mark.parametrize("source_platform", [13, 9])
def test_foreign_platform_mod_is_rebuilt_with_real_unity_payload(setup_library, monkeypatch, source_platform):
    env = setup_library
    monkeypatch.setattr(integrity, "inspect", inspect_bundle)
    monkeypatch.setattr(integrity, "metadata", bundle_metadata)
    monkeypatch.setattr(resolver, "sharing_keys", REAL_SHARING_KEYS)
    official_bundle(env)
    source = old_bundle(
        env.tmp / "input" / env.original.name,
        source_platform,
        cab="CAB-source",
        payload=b"modded",
        references=["com.nexon.bluearchive"],
    )
    # A library may contain Mods imported for another installation than the selected one.
    record = env.library._store({source.name: source}, "foreign", "test", "spr", user_added=True)
    original_bytes = env.original.read_bytes()
    source_bytes = source.read_bytes()
    _, files = contents(run(env, "single", f"mod:{record['id']}"))
    output = env.tmp / "prepared.bundle"
    output.write_bytes(files[env.original.name])
    bundle = Bundle.load(output)
    assert bundle.platform_info[0] == "StandaloneWindows64"
    assert bundle.env.objects[0].read().m_Script == "modded"
    assert env.original.read_bytes() == original_bytes
    assert source.read_bytes() == source_bytes


def test_cross_server_mod_is_prepared_against_selected_target(setup_library, monkeypatch):
    env = setup_library
    monkeypatch.setattr(integrity, "inspect", inspect_bundle)
    monkeypatch.setattr(integrity, "metadata", bundle_metadata)
    monkeypatch.setattr(resolver, "sharing_keys", REAL_SHARING_KEYS)
    official_bundle(env)
    source = old_bundle(
        env.tmp / "input" / env.original.name,
        19,
        cab="CAB-global",
        payload=b"global mod",
        references=["com.nexon.bluearchive"],
    )
    record = env.library._store({source.name: source}, "global", "test", "spr", user_added=True)
    pin = replace(env.library.current_pin(), server="japan")
    result = export_service.export_zip(
        services(env),
        env.library,
        pin,
        scope="single",
        target=f"mod:{record['id']}",
        folder=env.tmp / "exports",
        server="japan",
        platform="windows",
        resource_type=None,
        log=lambda *_: None,
        progress=lambda *_: None,
    )
    _, files = contents(result)
    output = env.tmp / "japan.bundle"
    output.write_bytes(files[env.original.name])
    bundle = Bundle.load(output)
    assert bundle.env.objects[0].read().m_Script == "global mod"
    inspection = integrity.Inspection()
    assert inspection.metadata(output).files == inspection.metadata(env.original).files
    assert output.read_bytes() != source.read_bytes()


def test_options_use_serialized_platform_and_real_default_directory(setup_library, monkeypatch):
    env = setup_library
    monkeypatch.setattr(integrity, "metadata", bundle_metadata)
    official_bundle(env, platform=13)
    env.settings.set("output_dir", str(env.tmp / "custom output"))
    registry = RpcRegistry()
    export_service.register_api(registry, services(env))
    answer = registry.call("export.options", {})["result"]
    assert answer["folder"] == str(env.tmp / "custom output")
    assert answer["platform"] == "android"
    assert answer["targets"] == [{"server": "global", "platform": "android"}]
    assert not (env.tmp / "custom output").exists()


def test_source_change_during_archive_write_cannot_publish(setup_library, monkeypatch):
    env = setup_library
    (mod,) = env.library.add([make_mod(env.original, env.tmp / "upload")])
    write = zipfile.ZipFile.write

    def changed(archive, path, *args, **kwargs):
        write(archive, path, *args, **kwargs)
        Path(path).write_bytes(Path(path).read_bytes() + b"changed")

    monkeypatch.setattr(zipfile.ZipFile, "write", changed)
    with pytest.raises(ServiceError, match="entry_changed"):
        run(env)
    assert not list((env.tmp / "exports").iterdir())


ATLAS = b"test_spr.png\nsize:4,4\nfilter:Linear,Linear\nface\nbounds:0,0,2,2\n"


@pytest.mark.parametrize("scope", ["single", "library"])
def test_each_exported_mod_carries_only_its_package_manifest(setup_library, scope):
    env = setup_library
    catalog = env.game_dir / "Catalog"
    (catalog / "Windows").mkdir(parents=True)
    (catalog / "BundleRevision").write_bytes("﻿478339".encode())
    (catalog / "Windows" / "catalog_Windows.hash").write_text("01a02066e18c3b4540fbb2278c8d7b06")
    source = make_mod(env.original, env.tmp / "upload", fixed=True)
    (mod,) = env.library.add([source])
    source_id = f"mod:{mod.id}"
    directory = "" if scope == "single" else export_service._safe_name(source_id)
    packages, files = contents(run(env, scope, source_id if scope == "single" else None))
    assert set(files) == {export_service._member_path(directory, name) for name in (source.name, ba_mod.FILE_NAME)}
    data = packages[directory]
    assert data["format_version"] == "1.0" and data["created_by"]["tool"] == "BMPT"
    assert (data["kind"], data["core"]) == ("portrait", "test_spr")
    assert data["target"] == {
        "server": "global",
        "platform": "windows",
        "release": {"bundle_revision": 478339, "catalog_hash": "01a02066e18c3b4540fbb2278c8d7b06"},
    }
    (item,) = data["files"]
    assert item == {"path": source.name, "sha256": ba_mod.sha256(source), "role": "mod"}
    assert "skeletons" not in data  # nothing of the header-only test bundles reads as Spine


def test_an_official_alt_export_marks_its_files_official(setup_library):
    env = setup_library
    packages, _ = contents(run(env, "single", "alt:test"))
    data = packages[""]
    assert [item["role"] for item in data["files"]] == ["official"]
    assert "release" not in data["target"]


def test_a_portrait_export_carries_the_official_skel_and_atlas_it_lacks(setup_library, monkeypatch):
    env = setup_library
    rig = game_bundle(env.game_dir, part="002", payload=b"official rig")
    source = make_mod(env.original, env.tmp / "upload", fixed=True)
    (mod,) = env.library.add([source])

    def read(path):
        if "_002_" in path.name:
            return integrity.Contents(atlases={"test_spr.atlas": ATLAS}, skeletons={"test_spr.skel": b"skel"})
        return integrity.Contents(textures={"test_spr": (4, 4)})

    monkeypatch.setattr(export_service, "_contents", read)
    packages, files = contents(run(env, "single", f"mod:{mod.id}"))
    assert files[rig.name] == rig.read_bytes()
    data = packages[""]
    assert {item["path"]: item["role"] for item in data["files"]} == {source.name: "mod", rig.name: "official"}
    assert data["skeletons"] == [{"name": "test_spr", "atlas": "official", "skel": "official", "textures": "mod"}]


def test_a_mod_with_its_own_atlas_carries_no_official_one(setup_library, monkeypatch):
    env = setup_library
    rig = game_bundle(env.game_dir, part="002", payload=b"official rig")
    texture = make_mod(env.original, env.tmp / "upload", fixed=True)
    atlas = make_mod(rig, env.tmp / "upload", payload=b"own rig!", fixed=True)
    (mod,) = env.library.add([texture, atlas])

    def read(path):
        if "_002_" in path.name:
            own = path.read_bytes() == atlas.read_bytes()
            return integrity.Contents(
                atlases={"test_spr.atlas": ATLAS + (b"\n" if own else b"")}, skeletons={"test_spr.skel": b"skel"}
            )
        return integrity.Contents(textures={"test_spr": (4, 4)})

    monkeypatch.setattr(export_service, "_contents", read)
    packages, _ = contents(run(env, "single", f"mod:{mod.id}"))
    data = packages[""]
    assert sorted(item["path"] for item in data["files"]) == sorted([texture.name, rig.name])
    assert data["skeletons"] == [{"name": "test_spr", "atlas": "mod", "skel": "official", "textures": "mod"}]


def test_a_scene_export_carries_only_the_rigs_of_the_pages_it_paints(setup_library, monkeypatch):
    env = setup_library
    painted = game_bundle(env.game_dir, part="002", payload=b"rig one")
    other = game_bundle(env.game_dir, part="004", payload=b"rig two")
    (mod,) = env.library.add([make_mod(env.original, env.tmp / "upload", fixed=True)])

    def read(path):
        if "_002_" in path.name:
            return integrity.Contents(atlases={"test_spr.atlas": ATLAS}, skeletons={"test_spr.skel": b"skel"})
        if "_004_" in path.name:
            return integrity.Contents(
                atlases={"scene.atlas": ATLAS.replace(b"test_spr.png", b"scene.png")}, skeletons={"scene.skel": b"skel"}
            )
        return integrity.Contents(textures={"test_spr": (4, 4)})

    monkeypatch.setattr(export_service, "_contents", read)
    _, files = contents(run(env, "single", f"mod:{mod.id}"))
    assert painted.name in files and other.name not in files
