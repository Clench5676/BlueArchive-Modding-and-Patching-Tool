"""Exercise catalog storage and interrupted swaps on private synthetic installs."""

import hashlib
import os
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
from bmpt.host.store_formats import CATALOG_JOURNALS, parse_json
from bmpt.library import ModLibrary, safety, tested_build
from bmpt.library.catalog_switch import MODS_DIR, catalog_paths
from bmpt.library.service import catalog as catalog_service
from bmpt.unity import ccdb


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


def test_recovery_preserves_new_official_catalog_with_new_resource_version(prepared, monkeypatch):
    library, pin, live = prepared.library, prepared.pin, prepared.live
    library._swap_catalog(pin, "mods")
    with monkeypatch.context() as patch:
        _interrupt_after(prepared, patch, "swapping")
        with pytest.raises(_Interrupted):
            library._swap_catalog(pin, "official")
    new_official = ccdb.write(replace(synthetic_catalog(), keys=b"updated catalog"))
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
    switch_install.library._enter_catalog_mode(switch_install.pin)
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
def test_missing_official_library_copy_only_blocks_redirects(catalog_install, keep_side_copy):
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
    assert [(item["entry"]["core"], item["code"]) for item in result.failed] == [("test_spr", "catalog_changed")]
    assert {item["core"] for item in result.done} == {"preload_spr", "unlisted_spr"}
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
    monkeypatch.setattr(lib, "_generate_catalog", lambda *args: pytest.fail("Unexpected regeneration"))
    result = lib.restore_drift([{"install": entry.install, "core": entry.core, "state": entry.state}])
    assert [item["core"] for item in result.done] == ["test_spr"]
    assert env.live.read_bytes() == expected


@pytest.mark.parametrize("catalog_mode", [False, True])
def test_prune_releases_configured_original_only_in_catalog_mode(switch_install, catalog_mode):
    env = switch_install
    lib, pin = env.library, env.pin
    path = env.files["test"][0]
    source, upload = env.add("test")
    if catalog_mode:
        lib._enter_catalog_mode(pin)
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
