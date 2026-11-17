"""The package manifest (ba-mod.json): what a reader accepts, when it is trusted, and the release marks it records."""

import json
import zipfile

import pytest

from bmpt.library import ba_mod
from bmpt.webui import batch_import, export_service

from test_batch_import import analyze, env  # noqa: F401 - fixtures
from test_library import make_mod, setup_library  # noqa: F401 - fixtures
from test_import_archive import write_zip


def manifest(**changes):
    data = {"format_version": "1.0", "created_by": {"tool": "Packer", "version": "1"}, "kind": "portrait",
            "core": "test_spr", "target": {"server": "global", "platform": "windows",
                                          "release": {"bundle_revision": 478339,
                                                      "catalog_hash": "01a02066e18c3b4540fbb2278c8d7b06"}},
            "files": [{"path": "a.bundle", "sha256": "0" * 64, "role": "mod"}]}
    data.update(changes)
    return json.dumps(data).encode()


def test_parse_takes_a_valid_manifest_and_drops_what_it_does_not_know():
    data = json.loads(manifest())
    data["unknown"] = {"x": 1}
    data["files"][0]["role"] = "something new"
    data["target"]["release"]["catalog_hash"] = "not hex"
    data["skeletons"] = [{"name": "test_spr", "atlas": "official", "skel": "mod", "textures": "?"}, "bad"]
    parsed = ba_mod.parse(json.dumps(data).encode())
    assert parsed["kind"] == "portrait" and parsed["core"] == "test_spr"
    assert parsed["files"] == [{"path": "a.bundle", "sha256": "0" * 64, "role": None}]
    assert parsed["target"]["release"] == {"bundle_revision": 478339}
    assert parsed["skeletons"] == [{"name": "test_spr", "atlas": "official", "skel": "mod"}]
    assert "unknown" not in parsed


@pytest.mark.parametrize("change", [
    {"format_version": "2.0"}, {"format_version": "1"}, {"kind": "sticker"}, {"core": "../x"},
    {"target": {"server": "korea", "platform": "windows"}}, {"files": []},
    {"files": [{"path": "../a.bundle", "sha256": "0" * 64, "role": "mod"}]},
    {"files": [{"path": "/a.bundle", "sha256": "0" * 64, "role": "mod"}]},
    {"files": [{"path": "a\\b.bundle", "sha256": "0" * 64, "role": "mod"}]},
    {"files": [{"path": "a.bundle", "sha256": "0" * 63, "role": "mod"}]},
    {"files": [{"path": "a.bundle", "sha256": "0" * 64}, {"path": "A.bundle", "sha256": "1" * 64}]},
])
def test_parse_refuses_what_no_reader_can_rely_on(change):
    assert ba_mod.parse(manifest(**change)) is None


def test_parse_refuses_non_json_and_large_files():
    assert ba_mod.parse(b"{") is None
    assert ba_mod.parse(b"[]") is None
    assert ba_mod.parse(b" " * (ba_mod.MAX_BYTES + 1)) is None


def test_a_core_is_optional_for_kinds_without_one():
    data = json.loads(manifest(kind="ui"))
    del data["core"]
    assert ba_mod.parse(json.dumps(data).encode())["core"] is None


def test_read_trusts_a_manifest_only_when_every_listed_file_matches(tmp_path):
    (tmp_path / "a.bundle").write_bytes(b"bundle")
    digest = ba_mod.sha256(tmp_path / "a.bundle")
    files = [{"path": "a.bundle", "sha256": digest, "role": "mod"}]
    (tmp_path / ba_mod.FILE_NAME).write_bytes(manifest(files=files))
    trusted = ba_mod.read(tmp_path)
    assert trusted is not None and trusted["files"][0]["sha256"] == digest
    assert ba_mod.covers(trusted, tmp_path, [tmp_path / "a.bundle"])
    assert not ba_mod.covers(trusted, tmp_path, [tmp_path / "b.bundle"])
    (tmp_path / "a.bundle").write_bytes(b"changed")
    assert ba_mod.read(tmp_path) is None
    (tmp_path / ba_mod.FILE_NAME).write_bytes(manifest(files=[*files, {"path": "gone.bundle", "sha256": digest}]))
    assert ba_mod.read(tmp_path) is None
    assert ba_mod.read(tmp_path / "nothing") is None


def test_release_marks_are_read_from_the_install_catalog(tmp_path):
    resources = tmp_path / "PUB" / "Resource" / "GameData" / "Windows"
    resources.mkdir(parents=True)
    catalog = tmp_path / "PUB" / "Resource" / "Catalog"
    (catalog / "Windows").mkdir(parents=True)
    (catalog / "BundleRevision").write_bytes("﻿478339".encode())
    (catalog / "Windows" / "catalog_Windows.hash").write_text("01A02066E18C3B4540FBB2278C8D7B06")
    assert ba_mod.release_of([resources], "global", "windows") == {
        "bundle_revision": 478339, "catalog_hash": "01a02066e18c3b4540fbb2278c8d7b06"}
    assert ba_mod.release_of([resources], "global", "android") == {"bundle_revision": 478339}
    assert ba_mod.release_of([resources], "japan", "windows") == {}
    (catalog / "BundleRevision").write_text("0")
    assert ba_mod.release_of([resources], "global", "windows") == {"catalog_hash": "01a02066e18c3b4540fbb2278c8d7b06"}


@pytest.mark.parametrize("name, kind", [
    ("assets-_mx-spinecharacters-akari_spr-_mxdependency-2024-11-18_002_assets_all_1.bundle", "portrait"),
    ("assets-_mx-spinelobbies-akari_home-_mxdependency-2024-11-18_000_assets_all_1.bundle", "lobby"),
    ("assets-_mx-spinebackground-spinebg-_mxdependency-2024-11-18_000_assets_all_1.bundle", "spine_scene"),
    ("ui-uilobbyelement-_mxload-prefabs-2025-07-02_assets_all_1.bundle", "lobby"),
    ("uis-03_scenario-02_character-_mxload-prefabs-2025-07-02_assets_all_1.bundle", "story_art"),
    ("uis-01_common-04_weapon-_mxload-2024-11-18_assets_all_1.bundle", "ui"),
    ("effect-_mxload-prefabs-2025-07-02_assets_all_1.bundle", "effect"),
    ("assets-_mx-characters-ch0058-_mxdependency-2024-11-18_000_assets_all_1.bundle", "model"),
    ("assets-_mx-shaders-_mxdependency-2021-10-29_assets_all_1.bundle", "other"),
])
def test_bundle_families_map_to_kinds(name, kind):
    assert ba_mod.family_kind(name) == kind


def test_library_kinds_map_to_package_kinds():
    background = "assets-_mx-spinebackground-spinebg-_mxdependency-2024-11-18_000_assets_all_1.bundle"
    assert ba_mod.kind_of("spr", []) == "portrait"
    assert ba_mod.kind_of("lobby", [background]) == "spine_scene"
    assert ba_mod.kind_of("lobby", ["assets-_mx-spinelobbies-x_home-_mxdependency-2024-11-18_000_assets_all_1.bundle"]) == "lobby"
    assert ba_mod.kind_of("other", ["effect-_mxload-prefabs-2025-07-02_assets_all_1.bundle",
                                    "uis-01_common-04_weapon-_mxload-2024-11-18_assets_all_1.bundle"]) == "other"


def test_a_claim_orders_the_guesses_but_never_adds_or_removes_one():
    choices = [{"server": s, "platform": p, "detected": True} for s in ("global", "japan") for p in ("android", "windows")]
    package = {"target": {"server": "japan", "platform": "windows"}}
    ordered = batch_import.claimed_first(choices, package)
    assert ordered[0] == {"server": "japan", "platform": "windows", "detected": True}
    assert sorted(map(str, ordered)) == sorted(map(str, choices))
    assert batch_import.claimed_first(choices, None) == choices
    assert batch_import.claimed_first(choices[:1], package) == choices[:1]


def test_an_exported_package_imports_with_its_manifest_trusted(env):
    mod, = env.library.add([make_mod(env.original, env.tmp / "upload", fixed=True)])
    result = export_service.export_zip(env.services, env.library, env.library.current_pin(), scope="single",
        target=f"mod:{mod.id}", folder=env.tmp / "exports", server="global", platform="windows", kind=None,
        log=lambda *_: None, progress=lambda *_: None)
    with zipfile.ZipFile(result["archive"]) as archive:
        names = set(archive.namelist())
    assert ba_mod.FILE_NAME in names and env.original.name in names
    analyzed = analyze(env, [result["archive"]])
    assert analyzed["skipped"] == 1  # the export's own manifest.json; ba-mod.json is never counted
    entry, = analyzed["entries"]
    candidate = env.services.batch_import._latest[entry["id"]]
    assert candidate.package is not None and candidate.package["kind"] == "portrait"


def test_a_package_whose_files_differ_is_a_plain_mod(env):
    source = make_mod(env.original, env.tmp / "made")
    archive = write_zip(env.tmp / "drop" / "mod.zip", {
        source.name: source.read_bytes(),
        ba_mod.FILE_NAME: manifest(files=[{"path": source.name, "sha256": "0" * 64, "role": "mod"}]),
    })
    analyzed = analyze(env, [archive])
    assert analyzed["skipped"] == 0
    entry, = analyzed["entries"]
    assert entry["state"] == "ready"
    assert env.services.batch_import._latest[entry["id"]].package is None
