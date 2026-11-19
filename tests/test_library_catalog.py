"""Exercise catalog storage and interrupted swaps on private synthetic installs."""

import hashlib
import os
import zlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from catalog_fixture import synthetic_catalog

from bmpt.core.errors import ServiceError
from bmpt.host.store_formats import CATALOG_JOURNALS, parse_json
from bmpt.library import ModLibrary, safety
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
