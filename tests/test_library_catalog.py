"""Exercise catalog storage and interrupted swaps on private synthetic installs."""

import contextlib
import hashlib
import os
import shutil
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.naming import parse_filename
from ba_modding_toolkit.utils import CRCUtils
from catalog_fixture import LOAD, PRELOAD, bundle_id, synthetic_catalog
from test_library import FakeAlt, FakeCatalog, game_bundle, make_mod

from bmpt.core.errors import ServiceError
from bmpt.host.config import Settings
from bmpt.host.data_upgrade import DataUpgrade
from bmpt.host.steam_records import OfficialFile
from bmpt.host.store_formats import CATALOG_JOURNALS, parse_json, read_runtime
from bmpt.library import ModLibrary, builders, fingerprint, safety, tested_build
from bmpt.library.catalog_switch import MODS_DIR, catalog_paths
from bmpt.library.service import catalog as catalog_service, updating
from bmpt.library.service.base import _View
from bmpt.unity import ccdb
from bmpt.webui.api import build_api
from bmpt.webui.jobs import START
from bmpt.webui.services import Services
from bmpt.webui.services_jobs import JobsMixin


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "check_game_not_running", lambda: None)
    monkeypatch.setattr(safety, "check_steam_idle", lambda _: None)
    root = tmp_path / "game"
    resource = root / "BlueArchive_Data/StreamingAssets/PUB/Resource"
    bundles = resource / "GameData/Windows"
    bundles.mkdir(parents=True)
    live = resource / "Catalog/Windows/catalog_Windows.bytes"
    live.parent.mkdir(parents=True)
    official = ccdb.write(synthetic_catalog())
    live.write_bytes(official)
    live.with_suffix(".hash").write_bytes(b"official build stamp")
    library = ModLibrary(tmp_path / "library", None)
    pin = library._pin(root, "global")
    assert catalog_paths(pin).live == live
    placed = b"synthetic mod bundle"
    path = bundles / MODS_DIR / f"{hashlib.sha1(placed).hexdigest()}.bundle"
    path.parent.mkdir()
    path.write_bytes(placed)
    redirects = {
        "a.bundle": {
            "placed": path.name,
            "dir": "GameData/Windows",
            "source": "mod:fixture",
            "build": "fixture",
            "size": len(placed),
            "mtime": path.stat().st_mtime_ns,
        }
    }
    return SimpleNamespace(library=library, pin=pin, live=live, official=official, redirects=redirects)


@pytest.fixture
def prepared(install):
    library, pin, live = install.library, install.pin, install.live
    record = library._keep_official_catalog(pin)
    rv = record["rv"]
    install.library_copy = library.root / "catalogs" / pin.id / f"{rv}.bytes"
    install.official_copy = live.parent / MODS_DIR / f"official-{rv[:12]}.bytes"
    install.mods_copy = live.parent / MODS_DIR / f"mods-{rv[:12]}.bytes"
    library._generate_catalog(pin, install.redirects)
    install.mods = install.mods_copy.read_bytes()
    return install


def test_missing_store_defaults_to_files_mode(install):
    library = install.library
    path = library.root / "catalogs.json"
    orphan = install.live.with_name(f"{install.live.name}.{'1' * 32}.tmp")
    orphan.touch()
    reopened = ModLibrary(library.root, None)
    assert reopened._catalog_record(install.pin) == {"mode": "files"}
    assert not path.exists()
    assert not (library.root / "catalogs").exists()
    assert orphan.exists()


def test_status_names_each_install_switch_method_from_the_store(prepared, monkeypatch):
    library, pin = prepared.library, prepared.pin
    monkeypatch.setattr(library, "installs", lambda: [pin])
    record = library._catalog_record(pin)
    library._save_catalog_record(pin, {**record, "mode": "catalog", "reason": None})
    item = library.status()["installs"][0]
    assert (item["method"], item["methodReason"], item["methodVersion"]) == ("catalog", None, record["rv"])
    library._save_catalog_record(pin, {**record, "mode": "files", "reason": "catalog_invalid"})
    item = library.status()["installs"][0]
    assert (item["method"], item["methodReason"], item["methodVersion"]) == ("files", "catalog_invalid", record["rv"])
    # before any record: file replacement, with the layout's reason when the install cannot use the catalog
    (library.root / "catalogs.json").unlink()
    assert library.status()["installs"][0]["method"] == "files"
    assert library.status()["installs"][0]["methodReason"] is None
    japan = library._pin(pin.root, "japan")
    monkeypatch.setattr(library, "installs", lambda: [japan])
    item = library.status()["installs"][0]
    assert (item["method"], item["methodReason"], item["methodVersion"]) == ("files", "unsupported_server", None)


def test_keep_and_generate_retain_verified_copies(prepared):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    record = library._catalog_record(pin)
    assert live.read_bytes() == prepared.official
    assert prepared.library_copy.read_bytes() == prepared.official == prepared.official_copy.read_bytes()
    assert record["official"] == {
        "sha1": hashlib.sha1(prepared.official).hexdigest(),
        "size": len(prepared.official),
        "crc32": zlib.crc32(prepared.official),
        "mtime": live.stat().st_mtime_ns,
    }
    placed = prepared.redirects["a.bundle"]["placed"]
    assert prepared.mods == ccdb.redirect(prepared.official, {"a.bundle": placed}, MODS_DIR)
    assert record["mods"]["sha1"] == hashlib.sha1(prepared.mods).hexdigest()
    assert record["mods"]["size"] == len(prepared.mods)
    assert record["mods"]["mtime"] == prepared.mods_copy.stat().st_mtime_ns
    assert record["redirects"] == prepared.redirects
    assert record["live"] == "official"


@pytest.mark.parametrize(("invalid", "code"), [(b"CCDB\x02", "catalog_unsupported"), (b"CCDB\x01", "catalog_invalid")])
def test_keep_refuses_unsupported_and_invalid_official_catalogs(install, invalid, code):
    install.live.write_bytes(invalid)
    with pytest.raises(ServiceError) as refusal:
        install.library._keep_official_catalog(install.pin)
    assert refusal.value.code == code
    assert not (install.library.root / "catalogs").exists()
    assert install.library._catalog_record(install.pin) == {"mode": "files"}


def test_keep_replaces_the_previous_library_copy_only_after_verifying_the_new_version(prepared):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    live.with_suffix(".hash").write_bytes(b"another build stamp")
    live.write_bytes(b"broken")
    with pytest.raises(ServiceError):
        library._keep_official_catalog(pin)
    assert prepared.library_copy.read_bytes() == prepared.official
    new_official = ccdb.write(replace(synthetic_catalog(), keys=b"new build"))
    live.write_bytes(new_official)
    record = library._keep_official_catalog(pin)
    assert record["mods"] is None
    assert (prepared.library_copy.parent / f"{record['rv']}.bytes").read_bytes() == new_official
    assert not prepared.library_copy.exists()


def test_generator_refusal_records_files_mode(prepared):
    library, pin = prepared.library, prepared.pin
    with pytest.raises(ServiceError) as refusal:
        library._generate_catalog(pin, {"preload.bundle": prepared.redirects["a.bundle"]})
    assert refusal.value.code == "catalog_invalid"
    assert refusal.value.params["guard"] == "preload"
    assert prepared.mods_copy.read_bytes() == prepared.mods
    assert library._catalog_record(pin)["reason"] == "catalog_invalid"


def test_generation_verifies_library_copy(prepared):
    prepared.library_copy.write_bytes(ccdb.write(replace(synthetic_catalog(), keys=b"other")))
    with pytest.raises(ServiceError) as refusal:
        prepared.library._generate_catalog(prepared.pin, prepared.redirects)
    assert refusal.value.code == "official_invalid"
    assert prepared.mods_copy.read_bytes() == prepared.mods


def test_swap_both_ways_uses_actual_bytes_after_stat_changes(prepared):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    for kind, expected, source in (
        ("mods", prepared.mods, prepared.mods_copy),
        ("official", prepared.official, prepared.official_copy),
    ):
        stat = live.stat()
        os.utime(live, ns=(stat.st_atime_ns, stat.st_mtime_ns - 10_000_000_000))
        library._swap_catalog(pin, kind)
        assert live.read_bytes() == expected
        assert not live.samefile(source)
        record = library._catalog_record(pin)
        assert record["live"] == kind
        assert record[kind]["mtime"] == live.stat().st_mtime_ns
        assert not library._journals()


def test_swap_refuses_foreign_live_catalog(prepared):
    foreign = ccdb.write(replace(synthetic_catalog(), keys=b"another catalog"))
    prepared.live.write_bytes(foreign)
    with pytest.raises(ServiceError) as refusal:
        prepared.library._swap_catalog(prepared.pin, "mods")
    assert refusal.value.code == "catalog_changed"
    assert prepared.live.read_bytes() == foreign
    assert not prepared.library._journals()


@pytest.mark.parametrize("bad_copy", ["missing", "wrong digest"])
def test_official_swap_falls_back_to_library_copy(prepared, bad_copy):
    library, pin = prepared.library, prepared.pin
    library._swap_catalog(pin, "mods")
    if bad_copy == "missing":
        prepared.official_copy.unlink()
    else:
        prepared.official_copy.write_bytes(prepared.mods)
    library._swap_catalog(pin, "official")
    assert prepared.live.read_bytes() == prepared.official
    assert library._catalog_record(pin)["live"] == "official"


def test_unverified_temporary_copy_never_reaches_live(prepared):
    library, pin = prepared.library, prepared.pin
    prepared.mods_copy.write_bytes(b"corrupt prepared catalog")
    with pytest.raises(ServiceError) as refusal:
        library._swap_catalog(pin, "mods")
    assert refusal.value.code == "catalog_invalid"
    assert prepared.live.read_bytes() == prepared.official
    assert library._journals()
    with library._transaction_lock():
        pass
    assert not library._journals()
    assert library._catalog_record(pin)["live"] == "official"


def test_generation_preserves_fingerprint_of_live_mods(prepared):
    library, pin = prepared.library, prepared.pin
    library._swap_catalog(pin, "mods")
    other_redirects = {"b.bundle": prepared.redirects["a.bundle"]}
    with pytest.raises(ServiceError) as refusal:
        library._generate_catalog(pin, other_redirects)
    assert refusal.value.code == "catalog_changed"
    assert prepared.mods_copy.read_bytes() == prepared.mods
    library._swap_catalog(pin, "official")
    library._generate_catalog(pin, other_redirects)
    assert prepared.mods_copy.read_bytes() != prepared.mods
    assert library._catalog_record(pin)["redirects"] == other_redirects


class _Interrupted(BaseException):
    pass


def _interrupt_after(prepared, monkeypatch, checkpoint):
    library, live = prepared.library, prepared.live
    if checkpoint in ("preparing", "swapping"):
        write = catalog_service.atomic_json

        def step(path, data):
            write(path, data)
            if path.name == "journal.json" and data["phase"] == checkpoint:
                raise _Interrupted

        monkeypatch.setattr(catalog_service, "atomic_json", step)
    else:
        owner, name = {
            "verified": (library, "_prepare_catalog_swap"),
            "replaced": (catalog_service, "replace_file"),
            "synced": (catalog_service, "sync_directory"),
            "saved": (library, "_save_catalog_live"),
        }[checkpoint]
        original = getattr(owner, name)

        def step(*args):
            original(*args)
            if checkpoint != "synced" or args[0] == live.parent:
                raise _Interrupted

        monkeypatch.setattr(owner, name, step)


@pytest.mark.parametrize(
    ("checkpoint", "phase", "kind"),
    [
        ("preparing", "preparing", "official"),
        ("verified", "preparing", "official"),
        ("swapping", "swapping", "official"),
        ("replaced", "swapping", "mods"),
        ("synced", "swapping", "mods"),
        ("saved", "swapping", "mods"),
    ],
)
@pytest.mark.parametrize("startup", [False, True])
def test_recovers_at_every_swap_checkpoint(prepared, monkeypatch, checkpoint, phase, kind, startup):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    with monkeypatch.context() as patch:
        _interrupt_after(prepared, patch, checkpoint)
        with pytest.raises(_Interrupted):
            library._swap_catalog(pin, "mods")
    (journal,) = (library.root / CATALOG_JOURNALS).glob("*/journal.json")
    assert parse_json(journal.read_bytes())["phase"] == phase
    orphan = live.with_name(f"{live.name}.{'1' * 32}.tmp")
    orphan.touch()
    if startup:
        library = ModLibrary(library.root, None)
    else:
        with library._transaction_lock():
            pass
    assert live.read_bytes() == getattr(prepared, kind)
    assert library._catalog_record(pin)["live"] == kind
    assert not library._journals()
    assert not list(live.parent.glob(f"{live.name}.*.tmp"))


@pytest.mark.parametrize("foreign_kind", ["official", "corrupt", "missing"])
def test_recovery_of_live_bytes_outside_journal(prepared, monkeypatch, caplog, foreign_kind):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    with monkeypatch.context() as patch:
        _interrupt_after(prepared, patch, "swapping")
        with pytest.raises(_Interrupted):
            library._swap_catalog(pin, "mods")
    foreign = ccdb.write(replace(synthetic_catalog(), keys=b"another catalog"))
    if foreign_kind == "missing":
        live.unlink()
    else:
        live.write_bytes(foreign if foreign_kind == "official" else b"broken")
    prepared.official_copy.unlink()
    with caplog.at_level("INFO"), library._transaction_lock():
        pass
    assert live.read_bytes() == (foreign if foreign_kind == "official" else prepared.official)
    assert library._catalog_record(pin)["live"] == "official"
    assert ("catalog_restored" in caplog.text) == (foreign_kind != "official")
    assert not library._journals()


def test_swap_cleans_only_its_orphan_temps(prepared):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    orphan = live.with_name(f"{live.name}.{'1' * 32}.tmp")
    unrelated = live.with_name(f"{live.name}.backup.tmp")
    orphan.touch()
    unrelated.touch()
    library._swap_catalog(pin, "official")
    assert not orphan.exists()
    assert unrelated.exists()


def test_swap_passes_the_pin_to_the_existing_write_gate(prepared):
    library, pin = prepared.library, prepared.pin

    def classic_open(current):
        assert current == pin
        raise ServiceError("classic_open")

    library._write_check = classic_open
    with pytest.raises(ServiceError) as refusal:
        library._swap_catalog(pin, "mods")
    assert refusal.value.code == "classic_open"
    assert prepared.live.read_bytes() == prepared.official
    assert not library._journals()


@pytest.mark.parametrize("parseable", [True, False])
def test_recovery_preserves_new_official_catalog_with_new_resource_version(prepared, monkeypatch, parseable):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    library._swap_catalog(pin, "mods")
    with monkeypatch.context() as patch:
        _interrupt_after(prepared, patch, "swapping")
        with pytest.raises(_Interrupted):
            library._swap_catalog(pin, "official")
    new_official = ccdb.write(replace(synthetic_catalog(), keys=b"updated catalog")) if parseable else b"new bytes"
    live.write_bytes(new_official)
    live.with_suffix(".hash").write_bytes(b"updated build stamp")
    with library._transaction_lock():
        pass
    assert live.read_bytes() == new_official
    assert not library._journals()


@pytest.fixture
def switch_install(tmp_path, monkeypatch, header_only_bundles):
    monkeypatch.setattr(safety, "check_game_not_running", lambda: None)
    monkeypatch.setattr(safety, "check_steam_idle", lambda _: None)
    root = tmp_path / "game"
    resource = root / "BlueArchive_Data/StreamingAssets/PUB/Resource"
    load = resource / "GameData/Windows"
    preload = resource / "Preload/Windows"
    files = {
        key: [game_bundle(load, f"{key}_spr", payload=key.encode())]
        for key in ("test", "second", "held", "untested", "unlisted", "failed")
    }
    files["test"].append(game_bundle(load, "test_spr", part="004", payload=b"extra part"))
    files["unlisted"].append(game_bundle(load, "unlisted_spr", part="004", payload=b"unlisted part"))
    files["preload"] = [
        game_bundle(load, "preload_spr", payload=b"load part"),
        game_bundle(preload, "preload_spr", part="004", payload=b"preload part"),
    ]
    live = resource / "Catalog/Windows/catalog_Windows.bytes"
    live.parent.mkdir(parents=True)
    ids = [
        bundle_id(PRELOAD if path.parent == preload else LOAD, path.name)
        for paths in files.values()
        for path in paths
        if path != files["unlisted"][1]
    ]
    official = ccdb.write(replace(synthetic_catalog(), ids=ids))
    live.write_bytes(official)
    live.with_suffix(".hash").write_bytes(b"official build stamp")
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(root))
    settings.set("enable_crc_correction", "false")
    settings.set("compression_method", "none")
    catalog = FakeCatalog(*(FakeAlt(key, f"{key}_spr") for key in files))
    library = ModLibrary(tmp_path / "library", catalog, object(), settings, scan_allowed=lambda: True)
    monkeypatch.setattr(library, "untested_version", lambda pin: None)
    pin = library.current_pin()
    assert pin is not None and catalog_paths(pin).live == live

    def add(key, *, fixed=False):
        upload = make_mod(files[key][0], tmp_path / "uploads" / key, payload=f"{key} mod".encode(), fixed=fixed)
        return f"mod:{library.add([upload])[0].id}", upload

    return SimpleNamespace(
        library=library,
        pin=pin,
        live=live,
        official=official,
        files=files,
        originals={path: path.read_bytes() for paths in files.values() for path in paths},
        add=add,
    )


@pytest.fixture
def catalog_install(switch_install):
    switch_install.library._migrate_catalog(switch_install.pin)
    return switch_install


def test_toggle_swaps_once_each_way_and_reuses_generated_catalog(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, upload = env.add("test", fixed=True)
    lib.toggle(False, True)
    assert lib.apply("test", source).deferred
    events = []
    replace_file = catalog_service.replace_file
    generate = lib._generate_catalog

    def renamed(src, dst):
        assert dst == env.live
        assert not lib._lock._lock._is_owned()
        events.append("swap")
        return replace_file(src, dst)

    def generated(*args):
        assert not lib._lock._lock._is_owned()
        events.append("generate")
        return generate(*args)

    monkeypatch.setattr(catalog_service, "replace_file", renamed)
    monkeypatch.setattr(lib, "_generate_catalog", generated)
    assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
    assert events == ["generate", "swap"]
    record = lib._catalog_record(pin)
    placed = lib._placed_path(pin, record["redirects"][upload.name])
    assert placed.name == hashlib.sha1(upload.read_bytes()).hexdigest() + ".bundle"
    assert placed.read_bytes() == upload.read_bytes()
    assert lib.toggle(False, True).done[0]["core"] == "test_spr"
    assert env.live.read_bytes() == env.official
    assert placed.is_file()
    lib.toggle(True, False)
    assert events == ["generate", "swap", "swap", "swap"]
    assert all(path.read_bytes() == data for path, data in env.originals.items())
    assert lib._applied == {}


def test_apply_and_restore_change_only_the_chosen_redirect(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    first, upload = env.add("test")
    second, _ = env.add("second")
    monkeypatch.setattr(lib.settings, "crc_needed", lambda path: True)
    assert lib.apply("test", first).files
    record = lib._catalog_record(pin)
    assert lib._placed_path(pin, record["redirects"][upload.name]).read_bytes() == upload.read_bytes()
    assert lib._applied == {}
    assert lib.crc_repair_due()["due"] is False
    assert lib.apply("second", second).files
    assert lib.would_write("test", "official")
    assert lib.restore("test").files
    assert set(lib._catalog_record(pin)["redirects"]) == {env.files["second"][0].name}
    assert all(path.read_bytes() == data for path, data in env.originals.items())


def test_deferred_choice_changes_catalog_with_one_rename_on_next_on(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    first, _ = env.add("test")
    second, _ = env.add("second")
    lib.apply("test", first)
    lib.toggle(False, True)
    assert lib.apply("second", second).deferred
    swaps = []
    swap = lib._swap_catalog

    def observed(pin, want):
        swaps.append(want)
        return swap(pin, want)

    monkeypatch.setattr(lib, "_swap_catalog", observed)
    assert not lib.toggle(True, False).failed
    assert swaps == ["mods"]
    assert len(lib._catalog_record(env.pin)["redirects"]) == 3


def test_off_continues_restoring_files_when_one_catalog_swap_fails(catalog_install, tmp_path, monkeypatch):
    env = catalog_install
    lib = env.library
    second_root = tmp_path / "files-game"
    second_file = game_bundle(second_root, "test_spr", payload=b"test")
    baseline = second_file.read_bytes()
    files_pin = lib._pin(second_root, "global")
    source, upload = env.add("test")
    fallback, _ = env.add("unlisted")
    lib.apply("test", source)
    lib.apply("unlisted", fallback)
    lib.apply("test", source, pin=files_pin)
    assert second_file.read_bytes() == upload.read_bytes()
    env.live.write_bytes(b"corrupt catalog")
    prune_calls = []
    prune = lib._prune_quietly

    def observed_prune():
        prune_calls.append(True)
        prune()

    monkeypatch.setattr(lib, "_prune_quietly", observed_prune)
    result = lib.toggle(False, True)
    assert [(item["entry"]["install"], item["code"]) for item in result.failed] == [(env.pin.key, "catalog_changed")]
    assert {(item["install"], item["core"]) for item in result.done} == {
        (files_pin.key, "test_spr"),
        (env.pin.key, "unlisted_spr"),
    }
    assert env.live.read_bytes() == b"corrupt catalog"
    assert second_file.read_bytes() == baseline
    assert env.files["unlisted"][0].read_bytes() == env.originals[env.files["unlisted"][0]]
    assert lib.profiles.read()["unfinished"] == [{"install": env.pin.key, "core": "test_spr", "why": "pending"}]
    assert prune_calls


def test_off_accepts_newer_official_catalog_in_place(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    old_stamp = lib._catalog_record(env.pin)["official"]
    newer = ccdb.write(replace(synthetic_catalog(), keys=b"updated catalog"))
    env.live.write_bytes(newer)
    env.live.with_suffix(".hash").write_bytes(b"updated build stamp")
    monkeypatch.setattr(catalog_service, "replace_file", lambda *_: pytest.fail("Unexpected catalog rename"))

    result = lib.toggle(False, True)
    assert not result.failed
    assert [item["core"] for item in result.done] == ["test_spr"]
    assert env.live.read_bytes() == newer
    assert lib.profiles.read()["unfinished"] == []
    record = lib._catalog_record(env.pin)
    assert record["live"] == "official"
    assert record["official"] == old_stamp


def test_toggle_confirms_untested_redirect_before_saving_intent(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    lib.toggle(False, True)
    source, _ = env.add("test")
    assert lib.apply("test", source).deferred
    monkeypatch.delattr(lib, "untested_version")
    tested = tested_build.Tested(env.pin.server, "1", frozenset({"older resource version"}), frozenset())
    monkeypatch.setattr(tested_build, "for_server", lambda server: tested if server == env.pin.server else None)

    with pytest.raises(ServiceError, match="untested_build") as refusal:
        lib.toggle(True, False)
    confirm = refusal.value.params["confirm"]
    assert confirm == {env.pin.key: {"version": lib.resource_version(env.pin), "cores": ["test_spr"]}}
    assert lib.intent()["enabled"] is False
    assert not lib._catalog_record(env.pin)["redirects"]
    assert env.live.read_bytes() == env.official
    lib.confirm_untested(confirm)
    assert not lib.toggle(True, False).failed
    assert lib._catalog_record(env.pin)["live"] == "mods"


def test_toggle_rebuilds_placements_when_the_build_key_changes(catalog_install):
    env = catalog_install
    lib = env.library
    source, upload = env.add("test")
    lib.apply("test", source)
    saved = lib._catalog_record(env.pin)["redirects"][upload.name]
    lib.toggle(False, True)
    path = lib._paths(lib._record(source[4:]))[0]
    make_mod(path, path.parent, payload=b"mod with different bytes")
    assert not lib.toggle(True, False).failed
    rebuilt = lib._catalog_record(env.pin)["redirects"][upload.name]
    assert rebuilt["build"] != saved["build"]
    assert lib._placed_path(env.pin, rebuilt).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("keep_side_copy", [True, False])
def test_missing_official_library_copy_falls_back_to_file_replacement(catalog_install, keep_side_copy):
    env = catalog_install
    lib = env.library
    lib.toggle(False, True)
    for key in ("test", "unlisted", "preload"):
        source, _ = env.add(key)
        assert lib.apply(key, source).deferred
    record = lib._catalog_record(env.pin)
    lib._catalog_library_copy(env.pin, record["rv"]).unlink()
    if not keep_side_copy:
        (env.live.parent / MODS_DIR / f"official-{record['rv'][:12]}.bytes").unlink()
    result = lib.toggle(True, False)
    assert not result.failed
    assert {item["core"] for item in result.done} == {"test_spr", "preload_spr", "unlisted_spr"}
    assert env.live.read_bytes() == env.official


def test_partial_target_apply_uses_crc_for_file_replacement(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    target, omitted = env.files["test"]
    source, upload = env.add("test")
    saved = []

    def load(path, *args, **kwargs):
        def save(output, options):
            saved.append(options.perform_crc)
            output.write_bytes(CRCUtils.apply_crc_fix(path.read_bytes(), int(parse_filename(output.name).crc)))
            return True, "saved"

        return SimpleNamespace(save=save)

    game_files = lib._game_files

    def partial(alt, kind, pin):
        return [target] if alt == "test" and kind == "portrait" else game_files(alt, kind, pin)

    monkeypatch.setattr(lib, "_game_files", partial)
    monkeypatch.setattr(lib.settings, "crc_needed", lambda _: True)
    monkeypatch.setattr(Bundle, "load", load)
    assert lib.apply("test", source).files == [target]
    assert saved == [True]
    assert target.read_bytes() != upload.read_bytes()
    assert zlib.crc32(target.read_bytes()) == int(parse_filename(target.name).crc)
    assert omitted.read_bytes() == env.originals[omitted]
    assert not lib._catalog_record(env.pin)["redirects"]


def test_catalog_toggle_excludes_held_and_failed_entries(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    lib.toggle(False, True)
    for key in ("test", "held", "failed"):
        source, _ = env.add(key)
        lib.apply(key, source)
        if key == "held":
            monkeypatch.setattr(lib, "_hold", lambda pin, target, held=source: target == held)

    place = lib._place

    def sometimes_fails(pin, name, prepared):
        if name == env.files["failed"][0].name:
            raise ServiceError("bundle_invalid", file=prepared)
        return place(pin, name, prepared)

    monkeypatch.setattr(lib, "_place", sometimes_fails)
    result = lib.toggle(True, False)
    assert [item["core"] for item in result.held] == ["held_spr"]
    assert {item["entry"]["core"]: item["code"] for item in result.failed} == {
        "failed_spr": "bundle_invalid",
    }
    assert set(lib._catalog_record(env.pin)["redirects"]) == {path.name for path in env.files["test"]}


def test_catalog_classic_window_refuses_apply_and_toggle(catalog_install):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    other, _ = env.add("second")
    lib.apply("test", source)
    generated = env.live.read_bytes()

    def classic_open(pin):
        assert pin == env.pin
        raise ServiceError("classic_open")

    lib._write_check = classic_open
    with pytest.raises(ServiceError, match="classic_open"):
        lib.apply("second", other)
    result = lib.toggle(False, True)
    assert [(item["entry"]["core"], item["code"]) for item in result.failed] == [("test_spr", "classic_open")]
    assert env.live.read_bytes() == generated


def test_only_load_cores_fully_listed_in_catalog_redirect(catalog_install):
    env = catalog_install
    lib = env.library
    lib.toggle(False, True)
    uploads = {}
    for key in ("test", "preload", "unlisted"):
        source, uploads[key] = env.add(key)
        lib.apply(key, source)
    assert not lib.toggle(True, False).failed
    assert set(lib._catalog_record(env.pin)["redirects"]) == {path.name for path in env.files["test"]}
    for key in ("preload", "unlisted"):
        assert env.files[key][0].read_bytes() == uploads[key].read_bytes()
    assert all(path.read_bytes() == env.originals[path] for path in env.files["test"])
    lib.toggle(False, True)
    assert all(path.read_bytes() == data for path, data in env.originals.items())
    assert env.live.read_bytes() == env.official


def test_catalog_prune_keeps_recorded_placements_and_version(catalog_install):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    lib.toggle(False, True)
    record = lib._catalog_record(pin)
    placed = {lib._placed_path(pin, item) for item in record["redirects"].values()}
    unused = next(iter(placed)).with_name("0" * 40 + ".bundle")
    unrelated = unused.with_name("sample.bundle")
    side = env.live.parent / MODS_DIR
    old = side / ("mods-" + "0" * 12 + ".bytes")
    backup = side / "mods-backup.bytes"
    for path in (unused, unrelated, old, backup):
        path.touch()
    lib.prune()
    assert all(path.is_file() for path in placed | {unrelated, backup, side / f"official-{record['rv'][:12]}.bytes"})
    assert not unused.exists() and not old.exists()


@pytest.mark.parametrize("mode", ["catalog", "files"])
def test_prune_keeps_foreign_catalog_placements(catalog_install, mode):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    record = lib._catalog_record(pin)
    folder = catalog_paths(pin).mods_dir
    foreign = folder / ("1" * 40 + ".bundle")
    unused = folder / ("2" * 40 + ".bundle")
    foreign.touch()
    unused.touch()
    env.live.write_bytes(ccdb.redirect(env.official, {env.files["second"][0].name: foreign.name}, MODS_DIR))
    lib._save_catalog_record(pin, {**record, "mode": mode})
    lib.prune()
    assert foreign.is_file()
    assert not unused.exists()
    assert all(lib._placed_path(pin, item).is_file() for item in record["redirects"].values())


def test_prune_waits_without_holding_the_lock_and_rejects_stale_state(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    generating, resume, waiting = threading.Event(), threading.Event(), threading.Event()
    generate = lib._generate_catalog
    wait = lib._catalog_idle.wait

    def paused_generate(*args):
        generating.set()
        assert resume.wait(timeout=3)
        return generate(*args)

    def observed_wait(*args, **kwargs):
        waiting.set()
        return wait(*args, **kwargs)

    monkeypatch.setattr(lib, "_generate_catalog", paused_generate)
    monkeypatch.setattr(lib._catalog_idle, "wait", observed_wait)
    with ThreadPoolExecutor(max_workers=2) as jobs:
        apply = jobs.submit(lib.apply, "test", source)
        try:
            assert generating.wait(timeout=3)
            prune = jobs.submit(lib.prune)
            assert waiting.wait(timeout=3)
            assert lib._lock.acquire(timeout=1)
            lib._lock.release()
        finally:
            resume.set()
        assert all(path.is_file() for path in apply.result().files)
        with pytest.raises(ServiceError, match="state_changed"):
            prune.result()
    lib.prune()
    record = lib._catalog_record(env.pin)
    assert all(lib._placed_path(env.pin, item).is_file() for item in record["redirects"].values())


@pytest.mark.parametrize("state", ["ok", "reset", "updated", "source_missing", "missing"])
def test_scan_redirected_entry(catalog_install, monkeypatch, state):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, upload = env.add("test")
    lib.apply("test", source)
    record = lib._catalog_record(pin)
    placed = lib._placed_path(pin, record["redirects"][upload.name])
    if state == "missing":
        placed.unlink()
    elif state == "source_missing":
        monkeypatch.setattr(lib, "_records", {})
    elif state in ("reset", "updated"):
        env.live.write_bytes(env.official)
        if state == "updated":
            env.live.with_suffix(".hash").write_bytes(b"updated build stamp")
    elif state == "ok":
        monkeypatch.setattr(catalog_service, "digest", lambda *args: pytest.fail("Unexpected catalog hash"))
    (entry,) = lib.scan(cores={"test_spr"}).entries
    assert entry.state == state
    if state == "ok":
        assert entry.source == source
        assert {item.file for item in entry.files} == {
            lib._placed_path(pin, item) for item in record["redirects"].values()
        }


def test_views_share_the_cached_catalog_identification(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    stat = env.live.stat()
    os.utime(env.live, ns=(stat.st_atime_ns, stat.st_mtime_ns - 1_000_000_000))
    calls = []
    digest = catalog_service.digest

    def observed(path, algorithm):
        calls.append(path)
        return digest(path, algorithm)

    monkeypatch.setattr(catalog_service, "digest", observed)
    with lib._lock:
        for _ in range(2):
            assert lib._view([pin], "full").entry(pin, "test_spr").carried == source
    assert calls == [env.live]


def test_leftover_redirect_with_official_choice_is_ok(catalog_install):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    env.live.write_bytes(env.official)
    lib._configure(env.pin, "test", {"portrait": "test_spr"}, "official")
    (entry,) = lib.scan(cores={"test_spr"}).entries
    assert entry.state == "ok"


def test_restore_drift_swaps_back_to_recorded_redirects(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    expected = env.live.read_bytes()
    env.live.write_bytes(env.official)
    (entry,) = lib.scan(cores={"test_spr"}).entries
    assert entry.state == "reset"
    monkeypatch.setattr(lib, "_generate_catalog", lambda *args: pytest.fail("Unexpected regeneration"))
    swaps = []
    rename = catalog_service.replace_file

    def swapped(source, destination):
        swaps.append(destination)
        return rename(source, destination)

    monkeypatch.setattr(catalog_service, "replace_file", swapped)
    result = lib.restore_drift([{"install": entry.install, "core": entry.core, "state": entry.state}])
    assert [item["core"] for item in result.done] == ["test_spr"]
    assert env.live.read_bytes() == expected
    assert swaps == [env.live]


@pytest.mark.parametrize("catalog_mode", [False, True])
def test_prune_releases_configured_original_only_in_catalog_mode(switch_install, catalog_mode, monkeypatch):
    env = switch_install
    lib, pin = env.library, env.pin
    path = env.files["test"][0]
    source, upload = env.add("test")
    if catalog_mode:
        lib._migrate_catalog(pin)
    else:
        monkeypatch.setattr(catalog_service, "mode_of", lambda _: ("files", "missing_catalog"))
    lib.apply("test", source)
    lib.toggle(False, True)
    kept = lib.root / "originals" / path.name
    kept.parent.mkdir(exist_ok=True)
    kept.write_bytes(env.originals[path])
    lib.prune()
    assert kept.exists() is not catalog_mode
    if not catalog_mode:
        lib.toggle(True, False)
        assert path.read_bytes() == upload.read_bytes()
        assert env.live.read_bytes() == env.official
        assert not (lib.root / "catalogs.json").exists()
        assert not (path.parent / MODS_DIR).exists()


def _steam_update(env, *, renamed=False, removed=False, library=None):
    old = env.files["test"][0]
    gone = env.files["held"][0]
    ids = ccdb.parse(env.official).ids
    if renamed:
        new = game_bundle(old.parent, "test_spr", date="2026-02-01", payload=b"renamed official")
        old.unlink()
        ids = [bundle_id(LOAD, new.name) if item == bundle_id(LOAD, old.name) else item for item in ids]
    if removed:
        gone.unlink()
        ids.remove(bundle_id(LOAD, gone.name))
    newer = ccdb.write(replace(synthetic_catalog(), ids=ids, keys=b"new official catalog"))
    env.live.write_bytes(newer)
    env.live.with_suffix(".hash").write_bytes(b"new resource version")
    (library or env.library)._steam_files.catalog = lambda _: OfficialFile(len(newer), hashlib.sha1(newer).hexdigest())
    return newer


def test_catalog_update_drops_retired_targets_and_reapplies_after_check(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    for core in ("test", "held", "second"):
        source, _ = env.add(core)
        lib.apply(core, source)
    old = lib._catalog_record(pin)
    old_side = list((env.live.parent / MODS_DIR).iterdir())
    retired = {env.files["test"][0].name, env.files["held"][0].name}
    placed = {name: lib._placed_path(pin, item) for name, item in old["redirects"].items()}
    newer = _steam_update(env, renamed=True, removed=True)
    events = []
    rename = catalog_service.replace_file

    def swapped(source, destination):
        events.append(destination)
        return rename(source, destination)

    monkeypatch.setattr(catalog_service, "replace_file", swapped)
    assert lib.update_due()["due"]
    assert lib.update_due()["outdated"]
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert set(record["redirects"]) == old["redirects"].keys() - retired
    assert record["live"] == "official" and record["mods"] is None
    assert env.live.read_bytes() == newer and not events
    assert all(path.is_file() for path in old_side)
    states = {entry.core: entry.state for entry in lib.scan().entries}
    assert {key: states[key] for key in ("test_spr", "held_spr", "second_spr")} == {
        "test_spr": "updated",
        "held_spr": "missing",
        "second_spr": "updated",
    }
    with pytest.raises(ServiceError, match="catalog_changed"):
        lib._regenerate(pin)
    assert not events
    monkeypatch.setattr(lib, "_scan_allowed", lambda: False)
    impact = lib.update_impact(pin)
    assert {row["core"] for row in impact["mods"] if row.get("target_retired")} == {"test_spr", "held_spr"}
    assert {row["core"] for row in lib.last_impact(pin)["mods"]} == {"test_spr", "held_spr"}
    assert env.live.read_bytes() == newer
    lib._reapply_catalog_update(pin, impact["version"])
    record = lib._catalog_record(pin)
    unchanged = env.files["second"][0].name
    assert record["redirects"] == {unchanged: old["redirects"][unchanged]}
    assert env.live.read_bytes() == ccdb.redirect(newer, {unchanged: placed[unchanged].name}, MODS_DIR)
    assert events == [env.live]
    assert lib._view([pin], "recorded").entry(pin, "second_spr").state == "ok"
    assert "test_spr" in lib.profiles.read()["folders"][pin.key]["updated"]
    lib.prune()
    assert not any(path.exists() for path in old_side)
    assert [name for name, path in placed.items() if path.is_file()] == [unchanged]


def test_catalog_update_while_mods_are_off_never_swaps(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    lib.toggle(False, True)
    old = lib._catalog_record(pin)
    newer = _steam_update(env)
    monkeypatch.setattr(catalog_service, "replace_file", lambda *_: pytest.fail("Unexpected catalog swap"))
    wrong = OfficialFile(len(newer), old["official"]["sha1"])
    for evidence in (None, wrong):
        monkeypatch.setattr(lib._steam_files, "catalog", lambda _, evidence=evidence: evidence)
        lib.refresh_catalogs([pin])
        assert lib._catalog_record(pin) == old
    right = OfficialFile(len(newer), hashlib.sha1(newer).hexdigest())
    monkeypatch.setattr(lib._steam_files, "catalog", lambda _: right)
    lib.prepare_catalogs()
    lib.update_impact(pin)
    record = lib._catalog_record(pin)
    assert record["rv"] != old["rv"]
    assert record["redirects"] == old["redirects"]
    assert record["live"] == "official" and record["mods"] is None
    assert env.live.read_bytes() == newer
    lib.prune()
    assert not (env.live.parent / MODS_DIR / f"mods-{old['rv'][:12]}.bytes").exists()


def test_start_resumes_catalog_reapply_after_check_is_recorded(catalog_install):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    newer = _steam_update(env)
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    with pytest.raises(ServiceError, match="catalog_changed"):
        lib.impact_seen(pin.key, record["rv"], checked=True)
    lib.update_impact(pin)
    assert lib.update_due()["outdated"]
    lib.prepare_catalogs()
    assert env.live.read_bytes() == newer
    lib._reapply_catalog_update(pin, record["rv"])
    record = lib._catalog_record(pin)
    assert not record["reapply"]
    assert env.live.read_bytes() == ccdb.redirect(
        newer, {name: item["placed"] for name, item in record["redirects"].items()}, MODS_DIR
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_migration_places_recorded_bytes_with_mods_on_or_off(switch_install, monkeypatch, enabled):
    env = switch_install
    lib, pin = env.library, env.pin
    source, upload = env.add("test", fixed=True)
    lib.apply("test", source)
    if not enabled:
        with monkeypatch.context() as patch:
            patch.setattr(catalog_service, "mode_of", lambda _: ("files", "missing_catalog"))
            lib.toggle(False, True)
        monkeypatch.setattr(catalog_service, "replace_file", lambda *_: pytest.fail("Unexpected catalog swap"))
    progress = []
    assert lib.update_due()["outdated"]
    lib.prepare_catalogs(progress=progress.append)
    record = lib._catalog_record(pin)
    assert record["migrated"] and record["mode"] == "catalog"
    assert record["live"] == ("mods" if enabled else "official")
    assert record["redirects"].keys() == {path.name for path in env.files["test"]}
    assert lib._placed_path(pin, record["redirects"][upload.name]).read_bytes() == upload.read_bytes()
    assert env.live.read_bytes() == (
        ccdb.redirect(env.official, {name: item["placed"] for name, item in record["redirects"].items()}, MODS_DIR)
        if enabled
        else env.official
    )
    assert all(path.read_bytes() == env.originals[path] for path in env.files["test"])
    assert progress[-1]["fraction"] == 1
    assert not lib.update_due()["outdated"]


def test_start_cover_job_forwards_catalog_progress(switch_install):
    env = switch_install
    lib = env.library
    source, _ = env.add("second")
    lib.apply("second", source)
    assert lib.update_due()["outdated"]
    submitted = []
    services = SimpleNamespace(
        library=lib,
        classic=SimpleNamespace(starting=contextlib.nullcontext),
        classic_open=lambda: False,
        _check_pin=lambda *_: None,
        submit_catalog_reapply=lambda *_: None,
        submit=lambda kind, run, **options: submitted.append((kind, run, options)),
    )
    JobsMixin.submit_refresh_costumes(services)
    ((kind, run, options),) = submitted
    assert kind == "mods.refreshCostumes"
    assert options == {"writes": "game", "library": lib, "rank": START}
    progress = []
    run(lambda _: None, progress.append)
    assert len(progress) >= 2 and progress[-1]["fraction"] == 1
    assert lib._catalog_record(env.pin)["migrated"]


def test_migration_waits_to_publish_when_a_game_update_needs_checking(switch_install):
    env = switch_install
    lib, pin = env.library, env.pin
    lib.update_due()
    source, upload = env.add("second")
    lib.apply("second", source)
    newer = _steam_update(env)
    assert lib.update_due()["due"]
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert record["redirects"] and not record["migrated"]
    assert env.live.read_bytes() == newer
    target = env.files["second"][0]
    assert target.read_bytes() == upload.read_bytes()
    lib.update_impact(pin)
    assert env.live.read_bytes() == newer
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert record["live"] == "mods" and record["migrated"]
    assert target.read_bytes() == env.originals[target]


@pytest.mark.parametrize(
    "checkpoint",
    [
        "_view",
        "_place_entry",
        "_generate_catalog",
        "_timed_swap",
        "_restore_migrated_entry",
        "_save_catalog_record",
        "published_catalog",
        "published_bundle",
    ],
)
def test_migration_resumes_after_every_checkpoint(switch_install, monkeypatch, checkpoint):
    env = switch_install
    lib, pin = env.library, env.pin
    for core in ("test", "second"):
        source, _ = env.add(core)
        lib.apply(core, source)
    lib.update_due()
    monkeypatch.setattr(lib, "_let_others_in", lambda: 0)
    owner, name = {
        "published_catalog": (catalog_service, "replace_file"),
        "published_bundle": (lib, "_save"),
    }.get(checkpoint, (lib, checkpoint))
    original = getattr(owner, name)

    def crash(*args, **kwargs):
        if checkpoint == "published_bundle":
            assert env.files["second"][0].read_bytes() == env.originals[env.files["second"][0]]
            raise _Interrupted()
        result = original(*args, **kwargs)
        if checkpoint != "_save_catalog_record" or args[1].get("migrated"):
            raise _Interrupted()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(owner, name, crash)
        with pytest.raises(_Interrupted):
            lib._migrate_catalog(pin)
    reopened = ModLibrary(lib.root, lib.catalog, lib.game, lib.settings, scan_allowed=lambda: True)
    monkeypatch.setattr(reopened, "_let_others_in", lambda: 0)
    reopened.prepare_catalogs()
    record = reopened._catalog_record(pin)
    assert record["migrated"] and record["live"] == "mods"
    assert record["redirects"].keys() == {path.name for core in ("test", "second") for path in env.files[core]}
    assert env.live.read_bytes() == ccdb.redirect(
        env.official, {name: item["placed"] for name, item in record["redirects"].items()}, MODS_DIR
    )
    assert all(path.read_bytes() == data for path, data in env.originals.items())
    assert not reopened._journals()
    state = read_runtime(reopened.root / "state.json")
    assert all(state["applied"][str(path)]["official"] for core in ("test", "second") for path in env.files[core])


def test_migration_keeps_the_redirect_and_written_file_when_official_is_missing(switch_install):
    env = switch_install
    lib, pin = env.library, env.pin
    source, upload = env.add("second")
    (target,) = env.files["second"]
    lib.apply("second", source)
    (lib.root / "originals" / target.name).unlink()
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert record["migrated"] and record["live"] == "mods"
    assert target.read_bytes() == lib._placed_path(pin, record["redirects"][target.name]).read_bytes()
    assert target.read_bytes() == upload.read_bytes()
    lib.judge_unfinished()
    assert lib.profiles.read()["unfinished"] == [{"install": pin.key, "core": "second_spr", "why": "stuck"}]


def test_running_game_defers_migration_to_the_next_toggle(switch_install, monkeypatch):
    env = switch_install
    lib, pin = env.library, env.pin
    source, upload = env.add("second")
    lib.apply("second", source)

    def running():
        raise ServiceError("game_running")

    with monkeypatch.context() as patch:
        patch.setattr(safety, "_sandboxed", lambda paths: False)
        patch.setattr(safety, "check_game_not_running", running)
        lib.prepare_catalogs()
        assert lib._catalog_record(pin) == {"mode": "files"}
        assert env.files["second"][0].read_bytes() == upload.read_bytes()
    lib.toggle(True, True)
    assert lib._catalog_record(pin)["migrated"]
    assert env.files["second"][0].read_bytes() == env.originals[env.files["second"][0]]


@pytest.mark.parametrize(("invalid", "code"), [(b"CCDB\x02", "catalog_unsupported"), (b"CCDB\x01", "catalog_invalid")])
def test_migration_retries_guard_refusal_on_a_new_resource_version(switch_install, monkeypatch, invalid, code):
    env = switch_install
    lib, pin = env.library, env.pin
    env.live.write_bytes(invalid)
    lib.prepare_catalogs()
    old = lib._catalog_record(pin)
    assert (old["mode"], old["reason"], old["migrated"]) == ("files", code, None)
    with monkeypatch.context() as patch:
        patch.setattr(lib, "_keep_official_catalog", lambda _: pytest.fail("Repeated refusal"))
        lib.prepare_catalogs()
    env.live.write_bytes(env.official)
    env.live.with_suffix(".hash").write_bytes(b"new version")
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert record["rv"] != old["rv"] and record["migrated"] and record["mode"] == "catalog"


@pytest.mark.parametrize("foreign", [False, True])
def test_start_safety_net_recovers_only_the_damaged_managed_catalog(catalog_install, monkeypatch, foreign):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    monkeypatch.setattr(lib, "_scan_allowed", lambda: False)
    assert not lib.update_due()["outdated"]
    replacement = ccdb.write(replace(synthetic_catalog(), keys=b"foreign catalog")) if foreign else b"damaged"
    env.live.write_bytes(replacement)
    assert lib.update_due()["outdated"]
    notices = lib.prepare_catalogs()
    assert env.live.read_bytes() == (replacement if foreign else env.official)
    assert [notice["code"] for notice in notices] == ([] if foreign else ["catalog_restored"])
    assert lib._catalog_record(pin)["live"] == ("mods" if foreign else "official")
    assert not lib._journals()


def test_japan_install_is_never_migrated(switch_install, monkeypatch):
    env = switch_install
    lib = env.library
    japan = replace(env.pin, server="japan")
    monkeypatch.setattr(lib, "installs", lambda: [japan])
    lib.prepare_catalogs()
    assert lib._catalog_record(japan) == {"mode": "files"}
    assert not (lib.root / "catalogs.json").exists()
    assert env.live.read_bytes() == env.official


@pytest.mark.parametrize("problem", ["missing", "changed", "before_swap", "verify"])
def test_migration_rechecks_live_and_placed_bytes_before_restoring(switch_install, monkeypatch, problem):
    env = switch_install
    lib, pin = env.library, env.pin
    for core in ("second", "test"):
        source, _ = env.add(core)
        lib.apply(core, source)
    target = env.files["test"][0]
    applied = target.read_bytes()
    with monkeypatch.context() as patch:
        generate = lib._generate_catalog

        def interrupted(*args):
            generate(*args)
            raise _Interrupted()

        patch.setattr(lib, "_generate_catalog", interrupted)
        with pytest.raises(_Interrupted):
            lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    placed = lib._placed_path(pin, record["redirects"][target.name])
    if problem == "missing":
        placed.unlink()
    elif problem == "changed":
        placed.write_bytes(b"modified placement")
    elif problem == "verify":
        restore = lib._restore_migrated_entry

        def steam_verify(pin, core, redirects):
            if core == "test_spr":
                env.live.write_bytes(env.official)
            return restore(pin, core, redirects)

        with monkeypatch.context() as patch:
            patch.setattr(lib, "_restore_migrated_entry", steam_verify)
            lib.prepare_catalogs()
        assert env.live.read_bytes() == env.official
        assert target.read_bytes() == applied
        assert not lib._catalog_record(pin)["migrated"]
    reopened = ModLibrary(lib.root, lib.catalog, lib.game, lib.settings, scan_allowed=lambda: True)
    if problem == "before_swap":
        prepare = reopened._prepare_catalog_swap
        publish = catalog_service.replace_file

        def remove_placement(*args):
            prepare(*args)
            placed.unlink()

        def publish_only_with_placement(source, destination):
            assert placed.is_file()
            publish(source, destination)

        monkeypatch.setattr(reopened, "_prepare_catalog_swap", remove_placement)
        monkeypatch.setattr(catalog_service, "replace_file", publish_only_with_placement)
    reopened.prepare_catalogs()
    record = reopened._catalog_record(pin)
    assert record["migrated"] and record["live"] == "mods"
    assert placed.read_bytes() == applied
    assert target.read_bytes() == env.originals[target]
    assert read_runtime(lib.root / "state.json")["applied"][str(target)]["official"]
    assert env.live.read_bytes() == ccdb.redirect(
        env.official, {name: item["placed"] for name, item in record["redirects"].items()}, MODS_DIR
    )


@pytest.mark.parametrize("operation", ["_refresh_catalog_version", "_migrate_catalog"])
def test_catalog_error_during_toggle_keeps_file_replacement(switch_install, monkeypatch, operation):
    env = switch_install
    lib, pin = env.library, env.pin
    source, upload = env.add("second")
    lib.apply("second", source)
    target = env.files["second"][0]

    def changed(*args):
        if operation == "_migrate_catalog" and not lib.profiles.read()["enabled"]:
            assert target.read_bytes() == env.originals[target]
        raise ServiceError("catalog_changed")

    monkeypatch.setattr(lib, operation, changed)
    assert not lib.toggle(False, True).failed
    assert target.read_bytes() == env.originals[target]
    assert not lib.toggle(True, False).failed
    assert target.read_bytes() == upload.read_bytes()
    assert env.live.read_bytes() == env.official
    record = lib._catalog_record(pin)
    assert "reason" not in record or record["reason"] is None
    assert record.get("live", "official") == "official"


@pytest.mark.parametrize("change", ["missing", "crc_tail", "same_mods", "steam_verify"])
def test_start_checks_missing_and_stale_catalog_stamps(catalog_install, change):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    mods = env.live.read_bytes()
    if change == "missing":
        env.live.unlink()
    else:
        env.live.write_bytes({"crc_tail": mods[:-4], "same_mods": mods, "steam_verify": env.official}[change])
    assert lib.update_due()["outdated"]
    notices = lib.prepare_catalogs()
    assert [notice["code"] for notice in notices] == (["catalog_restored"] if change in ("missing", "crc_tail") else [])
    current = lib._catalog_record(pin)
    kind = "mods" if change == "same_mods" else "official"
    assert env.live.read_bytes() == (mods if kind == "mods" else env.official)
    assert current["live"] == kind and current[kind]["mtime"] == env.live.stat().st_mtime_ns
    assert not lib.update_due()["outdated"]
    if change == "steam_verify":
        entries = lib.scan().entries
        assert [entry.state for entry in entries if entry.core == "second_spr"] == ["reset"]
        result = lib.restore_drift([{"install": pin.key, "core": "second_spr", "state": "reset"}])
        assert not result.failed and env.live.read_bytes() == mods


def test_resource_stamp_arrives_before_the_catalog(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    old = lib._catalog_record(pin)
    monkeypatch.setattr(lib, "_scan_allowed", lambda: False)
    env.live.write_bytes(env.official)
    env.live.with_suffix(".hash").write_bytes(b"new resource version")
    version = lib.resource_version(pin)
    for data in (
        env.official,
        b"CCDB\x01",
        ccdb.redirect(env.official, {env.files["second"][0].name: "a" * 40 + ".bundle"}, MODS_DIR)[:-4],
    ):
        env.live.write_bytes(data)
        lib.prepare_catalogs()
        assert lib._catalog_record(pin) == old and env.live.read_bytes() == data
        with pytest.raises(ServiceError, match="catalog_changed"):
            lib.update_impact(pin)
        with pytest.raises(ServiceError, match="catalog_changed"):
            lib.impact_seen(pin.key, version, checked=True)
        folder = lib.profiles.read()["folders"][pin.key]
        assert (folder.get("impact") or {}).get("version") != version
    newer = _steam_update(env)
    lib.prepare_catalogs()
    assert lib.update_impact(pin)["version"] == version
    assert lib.profiles.read()["folders"][pin.key]["impact"]["catalog_sha1"] == hashlib.sha1(newer).hexdigest()
    assert env.live.read_bytes() == newer


def test_confirmed_guard_refusal_marks_fallback_and_prunes_without_consent(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, upload = env.add("second")
    lib.apply("second", source)
    old = lib._catalog_record(pin)
    placed = lib._placed_path(pin, next(iter(old["redirects"].values())))
    side = list((env.live.parent / MODS_DIR).iterdir())
    library_copy = lib._catalog_library_copy(pin, old["rv"])
    refused = b"CCDB\x02"
    env.live.write_bytes(refused)
    env.live.with_suffix(".hash").write_bytes(b"new resource version")
    monkeypatch.setattr(
        lib._steam_files, "catalog", lambda _: OfficialFile(len(refused), hashlib.sha1(refused).hexdigest())
    )
    lib.prepare_catalogs()
    record = lib._catalog_record(pin)
    assert record["mode"] == "files" and record["reason"] == "catalog_unsupported"
    assert lib._view([pin], "full").entry(pin, "second_spr").state == "updated"
    target = env.files["second"][0]
    assert env.live.read_bytes() == refused and target.read_bytes() == env.originals[target]
    monkeypatch.setattr(lib, "_scan_allowed", lambda: False)
    jobs = []

    def submit(kind, run, **options):
        assert kind == "library.prune" and options["writes"] == "game"
        jobs.append(run)
        return "prune"

    services = SimpleNamespace(
        timings=SimpleNamespace(measure=lambda _: contextlib.nullcontext()), scan_allowed=lambda: False, submit=submit
    )
    Services._start_prune(services, lib)
    (prune,) = jobs
    with monkeypatch.context() as patch:
        patch.setattr(lib, "_prune_originals", lambda *_: pytest.fail("Catalog-only job pruned originals"))
        lib._write_check = lambda _: (_ for _ in ()).throw(ServiceError("classic_open"))
        prune(None, None)
        assert placed.exists() and all(path.exists() for path in side)
        lib._write_check = None
        prune(None, None)
    assert not placed.exists() and not library_copy.exists() and not any(path.exists() for path in side)
    applied = lib.restore_drift([{"install": pin.key, "core": "second_spr", "state": "updated"}])
    assert not applied.failed and target.read_bytes() == upload.read_bytes()
    assert env.live.read_bytes() == refused


def test_partial_catalog_reapply_retains_pending_entries_after_reopening(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    sources = {}
    for core in ("second", "failed"):
        source, _ = env.add(core)
        lib.apply(core, source)
        sources[core] = source
    _steam_update(env)
    lib.prepare_catalogs()
    version = lib.update_impact(pin)["version"]
    with monkeypatch.context() as patch:
        reuse = lib._reusable_redirects

        def fail_one(pin, core, *args):
            if core == "failed_spr":
                raise ServiceError("bundle_invalid")
            return reuse(pin, core, *args)

        patch.setattr(lib, "_reusable_redirects", fail_one)
        lib._reapply_catalog_update(pin, version)
    record = lib._catalog_record(pin)
    assert record["pending"] == {"failed_spr": sources["failed"]}
    assert list(record["redirects"]) == [env.files["second"][0].name]
    reopened = ModLibrary(lib.root, lib.catalog, lib.game, lib.settings, scan_allowed=lambda: True)
    reopened._reapply_catalog_update(pin, version)
    record = reopened._catalog_record(pin)
    assert record["pending"] == {} and not record["reapply"]
    assert set(record["redirects"]) == {env.files[core][0].name for core in sources}


def test_app_info_without_a_game_does_not_wait_for_migration(tmp_path, monkeypatch):
    monkeypatch.setattr("bmpt.webui.api.register_students", lambda *_: None)
    services = SimpleNamespace(
        game_found=lambda: False,
        safe_mode=False,
        _startup_ready=False,
        library=SimpleNamespace(_catalog_start_due=lambda: True),
        jobs=SimpleNamespace(wait=lambda *_: pytest.fail("app.info waited for a catalog job")),
        submit_refresh_costumes=lambda: pytest.fail("app.info started a catalog job"),
        state=SimpleNamespace(consent=None, spine_consent=None, preview_cache=True, flag=lambda _: False),
        preview_cache=SimpleNamespace(generation=0),
        cache_dir=lambda: tmp_path,
        data_dir=tmp_path,
        window=None,
        mute=False,
        debug=False,
    )
    result = build_api(services).call("app.info", None)
    assert result["result"]["gameFound"] is False


def test_impact_queues_a_separate_per_install_game_writer(catalog_install):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    newer = _steam_update(env)
    lib.prepare_catalogs()
    jobs = []
    services = SimpleNamespace(
        _startup_ready=False,
        classic=SimpleNamespace(starting=contextlib.nullcontext),
        submit=lambda kind, run, **options: jobs.append((kind, run, options)),
    )
    services.submit_catalog_reapply = lambda *args: JobsMixin.submit_catalog_reapply(services, *args)
    JobsMixin.submit_update_impact(services, lib, pin, False)
    assert jobs[0][0] == "mods.impact" and jobs[0][2] == {}
    jobs[0][1](lambda _: None, None)
    assert env.live.read_bytes() == newer
    assert jobs[1][0] == "mods.catalogReapply" and jobs[1][2] == {"writes": "game", "rank": START, "library": lib}
    jobs[1][1](lambda _: None, None)
    assert lib._catalog_record(pin)["live"] == "mods"


def test_catalog_migration_uses_the_managed_legacy_fence(switch_install, tmp_path, monkeypatch):
    env = switch_install
    original_lib, pin = env.library, env.pin
    data = tmp_path / "data"
    data.mkdir()
    (data / "webui.json").write_text(f'{{"prefs": {{"data_id": "0123456789ab", "data_path": "{data}"}}}}')
    (data / "library").mkdir()
    settings = Settings(data / "config.toml")
    settings.set("game_resource_dir", str(pin.root))
    settings.save()
    upgrade = DataUpgrade(data, qualify=lambda _: "apfs")
    try:
        ready = upgrade.run()
        assert ready["status"] == "ready", ready
        fences = []
        lock = upgrade._lock

        def observed(stack, path, reason):
            fences.append((path, reason))
            return lock(stack, path, reason)

        monkeypatch.setattr(upgrade, "_lock", observed)
        # a file the older build never wrote or configured needs no restore path of its own
        with upgrade.game_write([env.live], pin.root, lambda *_: False):
            pass
        tracked = upgrade._legacy_tracked
        monkeypatch.setattr(upgrade, "_legacy_tracked", lambda: ({env.live.resolve()}, set()))
        with (
            pytest.raises(ServiceError, match="official_unavailable"),
            upgrade.game_write([env.live], pin.root, lambda *_: False),
        ):
            pytest.fail("Legacy bundle write accepted a catalog without a legacy original")
        monkeypatch.setattr(upgrade, "_legacy_tracked", tracked)
        lib = ModLibrary(
            upgrade.paths.library_epoch,
            original_lib.catalog,
            original_lib.game,
            original_lib.settings,
            game_write_gate=upgrade.game_write,
            catalog_write_gate=upgrade.catalog_write,
        )
        lib.prepare_catalogs()
        record = lib._catalog_record(pin)
        assert record["mode"] == "catalog" and record["migrated"]
        assert (upgrade.paths.legacy_library / "transactions.lock", "olderVersion") in fences
        assert env.live.read_bytes() == env.official
        backup = lib._catalog_library_copy(pin, record["rv"])
        with upgrade.catalog_write(backup, record["official"]["sha1"]):
            assert backup.read_bytes() == env.official
        with pytest.raises(ServiceError, match="official_invalid"), upgrade.catalog_write(backup, "0" * 40):
            pytest.fail("Catalog gate accepted a mismatched SHA-1")
    finally:
        upgrade.close()


@pytest.mark.parametrize("code", ["steam_busy", "classic_open"])
def test_mid_job_catalog_refusals_leave_other_install_available(switch_install, tmp_path, monkeypatch, code):
    env = switch_install
    lib, pin = env.library, env.pin
    other_root = tmp_path / "other"
    shutil.copytree(pin.root, other_root)
    other = lib._pin(other_root, "global")
    other_live = catalog_paths(other).live
    source, upload = env.add("second")
    lib.apply("second", source, pin=pin)
    lib.apply("second", source, pin=other)
    blocked = False
    place = lib._place_entry

    def placed(current, *args, **kwargs):
        nonlocal blocked
        result = place(current, *args, **kwargs)
        if current == pin:
            blocked = True
        return result

    def refuse(root):
        if blocked and root == pin.root:
            raise ServiceError(code)

    with monkeypatch.context() as patch:
        patch.setattr(lib, "_place_entry", placed)
        if code == "steam_busy":
            patch.setattr(safety, "check_steam_idle", refuse)
        else:
            patch.setattr(lib, "_write_check", lambda current: refuse(current.root))
        lib.prepare_catalogs()
    first = lib._catalog_record(pin)
    second = lib._catalog_record(other)
    assert not first["migrated"] and not first["redirects"]
    assert second["migrated"] and second["live"] == "mods"
    assert env.live.read_bytes() == env.official and other_live.read_bytes() != env.official
    target = env.files["second"][0]
    assert target.read_bytes() == upload.read_bytes()
    reopened = ModLibrary(lib.root, lib.catalog, lib.game, lib.settings, scan_allowed=lambda: True)
    reopened.prepare_catalogs()
    assert reopened._catalog_record(pin)["migrated"]
    assert reopened._catalog_record(other) == second
    assert target.read_bytes() == env.originals[target]
    for current in (pin, other):
        assert read_runtime(lib.root / "catalogs.json")["installs"][current.key]["live"] == "mods"
    _steam_update(env, library=reopened)
    reopened.prepare_catalogs()
    version = reopened.update_impact(pin)["version"]

    def busy_elsewhere(root):
        if root == other_root:
            raise ServiceError("steam_busy")

    monkeypatch.setattr(safety, "check_steam_idle", busy_elsewhere)
    reopened._reapply_catalog_update(pin, version)
    assert reopened._catalog_record(pin)["live"] == "mods"
    assert not reopened._catalog_record(pin)["pending"]


def test_placement_checks_expected_bytes_after_copy(switch_install, monkeypatch):
    env = switch_install
    lib, pin = env.library, env.pin
    source, upload = env.add("second")
    lib.apply("second", source)
    target = env.files["second"][0]
    sha1 = hashlib.sha1(upload.read_bytes()).hexdigest()
    copy = catalog_service.atomic_copy

    def change_after_selection(source, destination):
        if ".bundle." in destination.name and destination.suffix == ".tmp":
            assert not lib._lock._lock._is_owned()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"changed during copy")
        else:
            copy(source, destination)

    monkeypatch.setattr(catalog_service, "atomic_clone", lambda *_: False)
    monkeypatch.setattr(catalog_service, "atomic_copy", change_after_selection)
    lib.prepare_catalogs()
    assert env.live.read_bytes() == env.official
    assert target.read_bytes() == upload.read_bytes()
    assert not lib._catalog_record(pin)["redirects"]
    assert not (target.parent / MODS_DIR / f"{sha1}.bundle").exists()


def test_updating_renamed_target_publishes_new_redirect(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("second")
    lib.apply("second", source)
    old = env.files["second"][0]
    new = game_bundle(old.parent, "second_spr", date="2026-02-01", payload=b"new official")
    old.unlink()
    ids = [
        bundle_id(LOAD, new.name) if item == bundle_id(LOAD, old.name) else item
        for item in ccdb.parse(env.official).ids
    ]
    official = ccdb.write(replace(synthetic_catalog(), ids=ids))
    env.live.write_bytes(official)
    env.live.with_suffix(".hash").write_bytes(b"new version")
    monkeypatch.setattr(
        lib._steam_files, "catalog", lambda _: OfficialFile(len(official), hashlib.sha1(official).hexdigest())
    )
    lib.prepare_catalogs()
    monkeypatch.setattr(lib, "_impact_of", lambda *args, **kwargs: (fingerprint.REBUILT, {}))
    result = lib.update_impact(pin)
    assert result["mods"][0]["impact"] == "rebuilt" and result["mods"][0]["target_retired"]
    lib.impact_seen(pin.key, result["version"], checked=True)
    monkeypatch.setattr(lib, "_plan_targets", lambda *_: [new])
    monkeypatch.setattr(lib, "_plan", lambda *_: [])
    rebuilt = []

    def rebuild(paths, targets, output, *args, **kwargs):
        rebuilt.append(make_mod(new, output, payload=b"rebuilt mod"))
        return SimpleNamespace(
            success=True, no_match=False, all_unchanged=False, file_pairs=[SimpleNamespace(output=rebuilt[-1])]
        )

    monkeypatch.setattr(updating, "rebuild", rebuild)
    assert not lib.update(source[4:], pin=pin).unchanged
    assert not lib.last_impact(pin)["mods"]
    lib.apply("second", source)
    record = lib._catalog_record(pin)
    assert set(record["redirects"]) == {new.name}
    placed = lib._placed_path(pin, record["redirects"][new.name])
    assert env.live.read_bytes() == ccdb.redirect(official, {new.name: placed.name}, MODS_DIR)
    assert placed.read_bytes() == lib._paths(lib._record(source[4:]))[0].read_bytes()


def test_impact_rows_carry_a_retired_target_in_the_wire_spelling():
    from bmpt.webui.contract import wire_value

    row = {"id": "1", "resource_type": "portrait", "impact": "rebuilt", "target_retired": True}
    assert wire_value({"mods": [row]}) == {
        "mods": [{"id": "1", "resourceType": "portrait", "impact": "rebuilt", "targetRetired": True}]
    }


def _switch_state(env):
    lib = env.library
    config = lib.profiles.read()
    return {
        "live": env.live.read_bytes() == env.official,
        "redirects": lib._catalog_record(env.pin)["redirects"],
        "applied": lib._applied,
        "unfinished": config["unfinished"],
        "enabled": config["enabled"],
        "game": {path.name: path.read_bytes() for paths in env.files.values() for path in paths},
    }


def _outcome(result):
    return {
        name: sorted(item["core"] for item in getattr(result, name))
        for name in ("done", "adopted", "stuck", "left", "skipped", "dropped", "held")
    } | {"failed": sorted(item["entry"]["core"] for item in result.failed), "cores": result.cores}


@pytest.mark.parametrize("standing", [True, False])
def test_unchanged_entries_switch_by_the_swap_alone(catalog_install, monkeypatch, standing):
    env = catalog_install
    lib = env.library
    for key in ("test", "second"):
        source, _ = env.add(key, fixed=key == "test")
        lib.apply(key, source)
    if not standing:
        monkeypatch.setattr(lib, "_standing_on", lambda *args: {})
        monkeypatch.setattr(lib, "_standing_off", lambda *args: [])
    walked, saves = [], []
    reach, update = lib._reach, lib.profiles.update
    monkeypatch.setattr(
        lib, "_reach", lambda pin, core, *args, **kwargs: walked.append(core) or reach(pin, core, *args, **kwargs)
    )
    monkeypatch.setattr(lib.profiles, "update", lambda *args, **kwargs: saves.append(1) or update(*args, **kwargs))
    states = []
    for enabled in (False, True, False, True):
        result = lib.toggle(enabled, not enabled)
        states.append((_outcome(result), _switch_state(env)))
    assert all(outcome["done"] == ["second_spr", "test_spr"] and not outcome["failed"] for outcome, _ in states)
    assert [state["live"] for _, state in states] == [True, False, True, False]
    assert all(state["unfinished"] == [] and state["applied"] == {} for _, state in states)
    assert states[1][1]["redirects"] == states[3][1]["redirects"]
    if standing:
        assert walked == []
        assert len(saves) <= 4 * 3  # the intent and the finished list of each toggle, never one per entry
    else:
        assert len(walked) == 8


def test_standing_and_walked_toggles_agree(catalog_install, monkeypatch, tmp_path_factory):
    env = catalog_install
    lib = env.library
    for key in ("test", "second"):
        source, _ = env.add(key)
        lib.apply(key, source)
    lib.toggle(False, True)
    fast = (_outcome(lib.toggle(True, False)), _switch_state(env))
    lib.toggle(False, True)
    monkeypatch.setattr(lib, "_standing_on", lambda *args: {})
    slow = (_outcome(lib.toggle(True, False)), _switch_state(env))
    assert fast == slow


def test_placed_mod_file_stands_under_another_build_key(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, upload = env.add("test")
    lib.apply("test", source)
    lib.toggle(False, True)
    record = lib._catalog_record(pin)
    redirects = {
        name: {**item, "build": "written by another program build"} for name, item in record["redirects"].items()
    }
    lib._save_catalog_record(pin, {**record, "redirects": redirects})
    monkeypatch.setattr(lib, "_reach", lambda *args, **kwargs: pytest.fail("a placed mod file was walked again"))
    assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
    assert env.live.read_bytes() != env.official
    # bytes that are not the mod's own file need a current build key
    lib.toggle(False, True)
    monkeypatch.setattr(lib, "_placed_as_is", lambda *args: False)
    walked = []
    monkeypatch.setattr(lib, "_reach", lambda pin, core, *args, **kwargs: walked.append(core) or ({}, None, None))
    lib.toggle(True, False)
    assert walked == ["test_spr"]


@pytest.mark.parametrize("change", ["placed_gone", "placed_resized", "mod_replaced", "source_changed", "part_changed"])
def test_changed_entry_is_walked_as_before(catalog_install, monkeypatch, change):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, upload = env.add("test")
    other, _ = env.add("second")
    lib.apply("test", source)
    lib.apply("second", other)
    lib.toggle(False, True)
    placed = lib._placed_path(pin, lib._catalog_record(pin)["redirects"][upload.name])
    if change == "placed_gone":
        placed.unlink()
    elif change == "placed_resized":
        placed.write_bytes(placed.read_bytes() + b"more")
    elif change == "mod_replaced":
        path = lib._paths(lib._record(source[4:]))[0]
        make_mod(path, path.parent, payload=b"other bytes of the same mod")
    elif change == "source_changed":
        second_upload = make_mod(env.files["test"][0], upload.parent.parent / "again", payload=b"another mod")
        lib.apply("test", f"mod:{lib.add([second_upload])[0].id}")
    else:
        env.files["test"][0].write_bytes(b"written by another tool")
    walked = []
    reach = lib._reach
    monkeypatch.setattr(
        lib, "_reach", lambda pin, core, *args, **kwargs: walked.append(core) or reach(pin, core, *args, **kwargs)
    )
    result = lib.toggle(True, False)
    assert walked == ["test_spr"]
    assert "second_spr" in [item["core"] for item in result.done]


def test_interrupted_publication_leaves_standing_entries_pending(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    lib.toggle(False, True)

    def stopped(*args, **kwargs):
        raise _Interrupted

    monkeypatch.setattr(lib, "_regenerate", stopped)
    with pytest.raises(_Interrupted):
        lib.toggle(True, False)
    assert lib.profiles.read()["unfinished"] == [{"install": env.pin.key, "core": "test_spr", "why": "pending"}]
    assert env.live.read_bytes() == env.official


def test_off_of_a_catalog_install_makes_no_pass_over_the_game_files(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    stray = env.files["second"][0]
    stray.write_bytes(b"put there by another tool")
    monkeypatch.setattr(_View, "entries", lambda *args, **kwargs: pytest.fail("the Off scanned the game files"))
    result = lib.toggle(False, True)
    assert [item["core"] for item in result.done] == ["test_spr"]
    assert stray.read_bytes() == b"put there by another tool"


def test_a_prune_pass_asks_each_mod_for_its_fingerprint_once(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    env.add("test")
    asked = []
    monkeypatch.setattr(lib, "_read_built_against", lambda record: None)
    monkeypatch.setattr(lib, "_backfill_built_against", lambda record, pin: asked.append(record["id"]))
    tried: set[str] = set()
    for path in env.files["test"]:
        lib._backfill_before_release(path.name, tried)
    assert len(asked) == 1
    lib._backfill_before_release(env.files["test"][0].name)
    assert len(asked) == 2  # a later pass asks again


def test_start_retry_leaves_the_prune_to_maintenance(catalog_install, monkeypatch):
    env = catalog_install
    lib = env.library
    source, _ = env.add("test")
    lib.apply("test", source)
    monkeypatch.setattr(lib, "_prune_quietly", lambda: pytest.fail("the start's retry pruned"))
    lib._run(True, True, [env.pin], lambda *args: None, None, None, drift=None, retry=["test_spr"])
    assert lib.take_prune_due() and not lib.take_prune_due()


def test_output_of_another_builder_stands_in_a_toggle_and_is_built_again_at_start(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    record = lib._catalog_record(pin)
    current = builders.fingerprint()
    assert all(item["build"].endswith(f".{current}") for item in record["redirects"].values())
    older = {
        name: {**item, "build": item["build"].split(".")[0] + ".build-older"}
        for name, item in record["redirects"].items()
    }
    lib._save_catalog_record(pin, {**record, "redirects": older})
    as_is = lib._placed_as_is
    monkeypatch.setattr(lib, "_placed_as_is", lambda *args: False)  # as a rebuilt output would be
    with monkeypatch.context() as patch:
        patch.setattr(lib, "_reach", lambda *args, **kwargs: pytest.fail("a toggle built an output again"))
        lib.toggle(False, True)
        assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
    assert lib._stale_builds(pin, lib.profiles.read()) == ["test_spr"]
    assert lib.update_due()["outdated"]
    assert lib.refresh_builds() == ["test_spr"]
    rebuilt = lib._catalog_record(pin)
    assert all(item["build"].endswith(f".{current}") for item in rebuilt["redirects"].values())
    assert rebuilt["live"] == "mods" and env.live.read_bytes() != env.official
    assert lib._stale_builds(pin, lib.profiles.read()) == []
    assert not lib._fresh_builds_only
    # a placed file that is the mod's own file is never stale, whatever its key says
    monkeypatch.setattr(lib, "_placed_as_is", as_is)
    lib._save_catalog_record(pin, {**rebuilt, "redirects": older})
    assert lib._stale_builds(pin, lib.profiles.read()) == []


def test_check_answers_are_kept_across_runs(tmp_path):
    from bmpt.library.check_cache import SCHEMA, CheckCache

    path = tmp_path / "cache" / "checks.json"
    cache = CheckCache(path)
    assert cache.get("pointers", "a" * 40) is None
    cache.put("pointers", "a" * 40, [["cab-1", 7, "Material"]])
    cache.flush()
    again = CheckCache(path)
    assert again.get("pointers", "a" * 40) == [["cab-1", 7, "Material"]]
    assert (again.hits, again.misses) == (1, 0)
    # another schema number, or a file that does not read, starts empty and is written again
    path.write_text(path.read_text().replace(f'"schema":{SCHEMA}', '"schema":0'))
    assert CheckCache(path).get("pointers", "a" * 40) is None
    path.write_text("not json")
    broken = CheckCache(path)
    assert broken.get("layouts", "b" * 40) is None
    broken.put("layouts", "b" * 40, {"cab": {"1": 2}})
    broken.flush()
    assert CheckCache(path).get("layouts", "b" * 40) == {"cab": {"1": 2}}
    broken.clear()
    assert not path.exists() and broken.get("layouts", "b" * 40) is None


def test_kept_answers_are_bounded(tmp_path, monkeypatch):
    from bmpt.library import check_cache

    monkeypatch.setattr(check_cache, "LIMIT", 3)
    cache = check_cache.CheckCache(tmp_path / "checks.json")
    for number in range(5):
        cache.put("references", str(number), {})
    assert [cache.get("references", str(number)) is not None for number in range(5)] == [False, False, True, True, True]


def test_redirected_parts_name_the_official_copy_behind_them(catalog_install):
    env = catalog_install
    lib = env.library
    target = env.files["test"][0]
    source, _ = env.add("test")
    lib.apply("test", source)
    files = lib.state("test").files
    assert files and all(not item.official and item.restorable for item in files)
    assert {item.copy for item in files} == {"game"}
    (entry,) = lib.scan(cores={"test_spr"}).entries
    assert entry.state == "ok" and not entry.overwritten
    # the game's own file no longer holds official bytes and nothing else does: that part has no copy
    target.write_bytes(b"not official" + b"\0" * 8)
    lost = [item for item in lib.state("test").files if not item.restorable]
    assert [item.copy for item in lost] == [None] and len(lost) < len(files) or len(files) == 1
    # a kept original stands in for it
    kept = lib.root / "originals" / target.name
    kept.parent.mkdir(exist_ok=True)
    kept.write_bytes(env.originals[target])
    reopened = ModLibrary(lib.root, lib.catalog, object(), lib.settings, scan_allowed=lambda: True)
    files = reopened.state("test").files
    assert all(item.restorable for item in files) and "kept" in {item.copy for item in files}
    assert any(item.has_kept_official for item in files)


def test_output_of_another_builder_is_built_again_at_start_with_mods_off(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    record = lib._catalog_record(pin)
    current = builders.fingerprint()
    older = {name: {**item, "build": item["build"].split(".")[0]} for name, item in record["redirects"].items()}
    lib._save_catalog_record(pin, {**record, "redirects": older})
    monkeypatch.setattr(lib, "_placed_as_is", lambda *args: False)  # as a rebuilt output would be
    lib.toggle(False, True)
    assert lib._stale_builds(pin, lib.profiles.read()) == ["test_spr"]
    assert lib.refresh_builds() == ["test_spr"]
    rebuilt = lib._catalog_record(pin)
    assert all(item["build"].endswith(f".{current}") for item in rebuilt["redirects"].values())
    # mods stay off: the official catalog is still the live one, the redirect set waits beside it
    assert rebuilt["live"] == "official" and env.live.read_bytes() == env.official
    assert not lib.status()["enabled"]
    assert lib._stale_builds(pin, lib.profiles.read()) == []
    assert lib.refresh_builds() == []
    monkeypatch.setattr(lib, "_reach", lambda *args, **kwargs: pytest.fail("the On walked a refreshed entry"))
    assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
    assert env.live.read_bytes() != env.official


def test_output_under_a_key_of_an_older_program_is_built_again_once(catalog_install, monkeypatch):
    env = catalog_install
    lib, pin = env.library, env.pin
    source, _ = env.add("test")
    lib.apply("test", source)
    record = lib._catalog_record(pin)
    current = builders.fingerprint()
    # what an older program left: one-part build keys, and a kept built copy under such a key
    older = {name: {**item, "build": item["build"].split(".")[0]} for name, item in record["redirects"].items()}
    lib._save_catalog_record(pin, {**record, "redirects": older})
    parts = {}
    for name, item in older.items():
        sha1 = item["placed"].removesuffix(".bundle")
        kept = lib.root / "built" / f"{sha1}.bundle"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(lib._placed_path(pin, item).read_bytes())
        parts[name] = {"sha1": sha1, "mod_file": None}
    key = {"source": source, "inputs": [], "parts": parts, "build": next(iter(older.values()))["build"]}

    def keep(data):
        data["folders"][pin.key]["built"]["test_spr"] = key
        return True

    lib._update_profiles(keep)
    monkeypatch.setattr(lib, "_placed_as_is", lambda *args: False)  # as a rebuilt output would be
    # a toggle walks the entry and places the kept copy again, under the key it was kept with
    lib.toggle(False, True)
    assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
    assert lib._stale_builds(pin, lib.profiles.read()) == ["test_spr"]
    assert lib.refresh_builds() == ["test_spr"]
    rebuilt = lib._catalog_record(pin)
    assert all(item["build"].endswith(f".{current}") for item in rebuilt["redirects"].values())
    assert "test_spr" not in lib.profiles.read()["folders"][pin.key]["built"]
    assert lib._stale_builds(pin, lib.profiles.read()) == []
    assert lib.refresh_builds() == []
    with monkeypatch.context() as patch:
        patch.setattr(lib, "_reach", lambda *args, **kwargs: pytest.fail("a toggle built an output again"))
        lib.toggle(False, True)
        assert [item["core"] for item in lib.toggle(True, False).done] == ["test_spr"]
