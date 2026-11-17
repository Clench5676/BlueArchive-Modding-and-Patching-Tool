"""Mods chosen as ZIP archives: bundles unpacked once into staging, nested archives left packed."""

from __future__ import annotations

import io
import os
import time
import zipfile
from types import SimpleNamespace

import pytest

from bmpt.library import import_archive, staging
from bmpt.webui import export_service

from test_batch_import import analyze, batch_model_env, env, rpc, run  # noqa: F401 - fixtures
from test_library import game_bundle, make_mod, setup_library  # noqa: F401 - fixtures


def write_zip(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def nested_zip(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return stream.getvalue()


def unpacked(env):
    return sorted((env.library.root / "staging").glob(import_archive.PREFIX + "*"))


def test_zip_bundles_import_like_files_and_nested_archives_stay_packed(env):
    source = make_mod(env.original, env.tmp / "made")
    archive = write_zip(
        env.tmp / "drop" / "mod.zip",
        {
            f"Mod folder/{source.name}": source.read_bytes(),
            "Mod folder/readme.txt": b"notes",
            "Mod folder/more.zip": nested_zip({"other/" + source.name: b"another mod"}),
            f"__MACOSX/Mod folder/._{source.name}": b"resource fork",
        },
    )
    result = analyze(env, [archive])
    (entry,) = result["entries"]
    assert result["skipped"] == 3
    assert (entry["variant"], entry["core"], entry["kind"], entry["state"]) == ("test", "test_spr", "spr", "ready")
    (folder,) = unpacked(env)
    assert entry["paths"] == [str(folder / "Mod folder" / source.name)]
    # nothing of the nested archive was unpacked
    assert [path.name for path in folder.rglob("*") if path.is_file()] == [source.name]
    # the same archive again reuses its unpacked copy, so the row keeps its id
    again = analyze(env, [archive])
    assert [item["id"] for item in again["entries"]] == [entry["id"]]
    assert unpacked(env) == [folder]
    # rechecking the rows reads the unpacked files themselves
    assert analyze(env, entry["paths"])["entries"][0]["id"] == entry["id"]
    (outcome,) = run(env, result["entries"], enableAfterImport=False)
    assert outcome["outcome"] == "imported"
    (stored,) = env.library.mods()
    assert stored.id == outcome["libraryId"]


def test_zip_and_loose_files_are_taken_together(env):
    loose = make_mod(env.original, env.tmp / "loose")
    other_target = game_bundle(env.game_dir, "test_home_gl")
    lobby = make_mod(other_target, env.tmp / "made", payload=b"lobby mod")
    archive = write_zip(env.tmp / "drop" / "lobby.zip", {lobby.name: lobby.read_bytes()})
    result = analyze(env, [loose, archive])
    assert result["skipped"] == 0
    assert sorted(entry["kind"] for entry in result["entries"]) == ["lobby", "spr"]


def test_member_names_never_leave_the_unpacked_folder(env):
    source = make_mod(env.original, env.tmp / "made")
    archive = env.tmp / "drop" / "unsafe.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as stream:
        for name in (
            f"../{source.name}",
            f"/abs/{source.name}",
            f"a/../../{source.name}",
            f"C:/x/{source.name}",
            f"ok/{source.name}",
        ):
            info = zipfile.ZipInfo("placeholder")
            info.filename = name  # ZipInfo would clean the name; an archive from elsewhere need not
            stream.writestr(info, source.read_bytes())
        # the same name in another case cannot live beside it on a case-insensitive disk
        stream.writestr(f"OK/{source.name}", b"other")
    before = {path for path in env.tmp.rglob("*")}
    result = analyze(env, [archive])
    (entry,) = result["entries"]
    assert result["skipped"] == 5
    (folder,) = unpacked(env)
    assert entry["paths"] == [str(folder / "ok" / source.name)]
    created = {path for path in env.tmp.rglob("*")} - before
    assert [path for path in created if path.name == source.name] == [folder / "ok" / source.name]


@pytest.mark.parametrize("damage", ["not a zip", "truncated", "bad crc", "bad data", "encrypted"])
def test_damaged_archive_fails_the_analysis_and_leaves_nothing(env, damage):
    source = make_mod(env.original, env.tmp / "made")
    archive = write_zip(env.tmp / "drop" / "mod.zip", {source.name: source.read_bytes()})
    data = archive.read_bytes()
    if damage == "not a zip":
        archive.write_bytes(b"plain text")
    elif damage == "truncated":
        archive.write_bytes(data[: len(data) // 2])
    elif damage == "bad crc":
        central = data.rindex(b"PK\x01\x02")  # the CRC the reader checks is the central directory's
        archive.write_bytes(data[: central + 16] + b"\0\0\0\0" + data[central + 20 :])
    elif damage == "bad data":
        with zipfile.ZipFile(archive) as stream:
            info = stream.infolist()[0]
        start = info.header_offset + 30 + len(info.filename.encode())
        changed = bytearray(data)
        for i in range(start, start + info.compress_size, 7):
            changed[i] ^= 0xFF
        archive.write_bytes(bytes(changed))
    else:
        offset = 6  # the general purpose flags of the first local header
        changed = bytearray(data)
        changed[offset] |= 1
        central = data.rindex(b"PK\x01\x02")
        changed[central + 8] |= 1
        archive.write_bytes(bytes(changed))
    job = rpc(env, "import.analyze", paths=[str(archive)], recursive=False)
    assert env.services.jobs.wait(job, 10)
    answer = rpc(env, "jobs.get", id=job)
    assert answer["state"] == "failed"
    assert answer["error"]["code"] == "archive_invalid"
    assert not unpacked(env)
    assert not any((env.library.root / "staging").glob("*part*"))


def test_an_archive_without_bundles_adds_nothing(env):
    archive = write_zip(env.tmp / "drop" / "empty.zip", {"readme.txt": b"notes", "inner.zip": nested_zip({})})
    result = analyze(env, [archive])
    assert result == {**result, "entries": [], "skipped": 2}
    assert not unpacked(env)


def test_archives_inside_a_chosen_folder_stay_packed(env):
    source = make_mod(env.original, env.tmp / "made")
    write_zip(env.tmp / "drop" / "mod.zip", {source.name: source.read_bytes()})
    result = analyze(env, [env.tmp / "drop"], recursive=True)
    assert result["entries"] == [] and result["skipped"] == 1
    assert not unpacked(env)


def test_an_exported_archive_imports_back(env):
    source = make_mod(env.original, env.tmp / "made")
    (mod,) = env.library.add([source])
    services = SimpleNamespace(settings=env.settings, library=env.library, state=SimpleNamespace(get=lambda *_: {}))
    exported = export_service.export_zip(
        services,
        env.library,
        env.library.current_pin(),
        scope="library",
        target=None,
        folder=env.tmp / "exports",
        server="global",
        platform="windows",
        kind=None,
        log=lambda *_: None,
        progress=lambda *_: None,
    )
    env.library.remove(mod.id, confirm=True)
    assert not env.library.mods()
    result = analyze(env, [exported["archive"]])
    (entry,) = result["entries"]
    assert result["skipped"] == 1  # manifest.json
    assert (entry["core"], entry["kind"], entry["state"]) == ("test_spr", "spr", "ready")
    (outcome,) = run(env, result["entries"], enableAfterImport=False)
    assert outcome["outcome"] == "imported"


def test_startup_cleanup_removes_only_unpacked_archives_unused_for_a_day(tmp_path):
    root = tmp_path / "library"
    old = root / "staging" / f"{import_archive.PREFIX}0123456789abcdef"
    fresh = root / "staging" / f"{import_archive.PREFIX}fedcba9876543210"
    for folder in (old, fresh):
        (folder / "a").mkdir(parents=True)
        (folder / "a" / "x.bundle").write_bytes(b"x")
    past = time.time() - staging.MIN_IMPORT_AGE - 60
    for path in (old / "a" / "x.bundle", old / "a", old):
        os.utime(path, (past, past))
    staging.cleanup(root)
    assert not old.exists() and fresh.is_dir()


def test_the_file_dialog_offers_zip_archives(env, monkeypatch):
    from webview.util import parse_file_type
    from bmpt.webui import native

    seen = {}
    env.services.window = object()
    monkeypatch.setattr(native, "pick_files", lambda window, types, **_: seen.setdefault("types", types) and [])
    assert rpc(env, "import.pick", folder=False) == {"paths": []}
    (kinds,) = seen["types"]
    description, patterns = parse_file_type(kinds)
    assert set(patterns.split(";")) == {"*.bundle", "*.bundle.backup", "*.zip"}
