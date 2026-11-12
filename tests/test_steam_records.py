"""Steam's records on this device: the installed build's file list (size and SHA-1 per file) and what the library
judges official with it; read only with the Check Game Files consent."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from bmpt import steam_records
from bmpt.config import Settings
from bmpt.errors import ServiceError
from bmpt.library import ModLibrary
from ba_modding_toolkit.utils import CRCUtils

from test_library import FakeCatalog, FakeVariant, game_bundle, header_bundle

RESOURCE = "BlueArchive_Data/StreamingAssets/PUB/Resource"
GID = 7791673520712697115


def varint(value: int) -> bytes:
    out = b""
    while True:
        byte, value = value & 0x7F, value >> 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out


def field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def entry(path: str, data: bytes | None, flags: int = 0) -> bytes:
    size = len(data) if data else 0
    sha = hashlib.sha1(data).digest() if data else bytes(20)
    return field(1, field(1, path.encode()) + field(2, size) + field(3, flags) + field(5, sha))


def depot_manifest(files: dict[str, bytes], *, depot: int = 3557621, gid: int = GID, broken: bool = False) -> bytes:
    payload = b"".join([entry("BlueArchive_Data", None, 0x40), *(entry(path, data) for path, data in files.items())])
    crc = zlib.crc32(struct.pack("<I", len(payload)) + payload) ^ (1 if broken else 0)
    meta = field(1, depot) + field(2, gid) + field(9, crc)
    section = lambda magic, body: struct.pack("<II", magic, len(body)) + body
    return section(0x71F617D0, payload) + section(0x1F4812BE, meta) + struct.pack("<I", 0x32C415AB)


def app_manifest(gid: int = GID) -> str:
    return ('"AppState"\n{\n\t"appid"\t\t"3557620"\n\t"StateFlags"\t\t"4"\n\t"installdir"\t\t"BlueArchive"\n'
            '\t"InstalledDepots"\n\t{\n\t\t"3557621"\n\t\t{\n\t\t\t"manifest"\t\t"%d"\n\t\t\t"size"\t\t"1"\n\t\t}\n\t}\n}\n'
            % gid)


@pytest.fixture
def steam(tmp_path):
    """A Steam library holding the game, one official bundle in GameData, Steam's app manifest; no depot cache yet."""
    library = tmp_path / "Steam"
    install = library / "steamapps" / "common" / "BlueArchive"
    game_data = install / RESOURCE / "GameData" / "Windows"
    (install / RESOURCE / "Preload" / "Windows").mkdir(parents=True)
    official = game_bundle(game_data)
    (library / "steamapps" / "appmanifest_3557620.acf").write_text(app_manifest())

    def keep(files: dict[str, bytes], **options) -> Path:
        cache = library / "depotcache"
        cache.mkdir(exist_ok=True)
        path = cache / f"3557621_{options.pop('gid', GID)}.manifest"
        path.write_bytes(depot_manifest(files, **options))
        return path

    listed = {f"{RESOURCE}/GameData/Windows/{official.name}": official.read_bytes()}
    return library, install, game_data, official, keep, listed


def test_a_depot_manifest_gives_each_file_its_size_and_sha1():
    data = b"bundle bytes"
    depot, gid, files = steam_records.parse_depot_manifest(depot_manifest({"a/b.bundle": data, "a/empty": b""}))
    assert (depot, gid) == (3557621, GID)
    assert files == {"a/b.bundle": steam_records.OfficialFile(len(data), hashlib.sha1(data).hexdigest()),
                     "a/empty": steam_records.OfficialFile(0, hashlib.sha1(b"").hexdigest())}
    with pytest.raises(steam_records.ManifestError):
        steam_records.parse_depot_manifest(depot_manifest({"a": data}, broken=True))
    with pytest.raises(steam_records.ManifestError):
        steam_records.parse_depot_manifest(b"\x00" * 12)


def test_the_installed_build_is_read_from_steams_app_manifest():
    assert steam_records.installed_manifest(app_manifest(42)) == 42
    assert steam_records.installed_manifest(app_manifest(42), depot=1) is None
    assert steam_records.installed_manifest('"AppState" { "appid" "1" }') is None


def test_the_bundles_of_an_install_come_from_the_depot_cache_of_its_library(steam):
    library, install, game_data, official, keep, listed = steam
    files = steam_records.OfficialFiles()
    assert files.bundles(game_data) == {}  # no depot cache: no list
    keep(listed, gid=1)
    assert files.bundles(game_data) == {}  # a list of another build is not this install's
    keep({**listed, "BlueArchive.exe": b"exe", f"{RESOURCE}/GameData/Windows/sub/x.bundle": b"x"})
    expected = {official.name: steam_records.OfficialFile(len(listed[next(iter(listed))]),
                                                          hashlib.sha1(official.read_bytes()).hexdigest())}
    assert files.bundles(game_data) == expected == files.bundles(install)
    assert files.bundles(library.parent / "elsewhere") == {}


def make_library(tmp_path, game_data, allowed):
    settings = Settings(tmp_path / "config.toml")
    settings.set("game_resource_dir", str(game_data))
    catalog = FakeCatalog(FakeVariant("test", "test_spr", "test_home_gl"))
    return ModLibrary(tmp_path / "library", catalog, object(), settings, scan_allowed=lambda: allowed[0])


def test_with_steams_list_a_file_is_official_by_its_sha1_not_by_the_crc_in_its_name(tmp_path, steam):
    library_dir, install, game_data, official, keep, listed = steam
    keep(listed)
    allowed = [False]
    library = make_library(tmp_path, game_data, allowed)
    expected = int(official.name.rsplit("_", 1)[1].split(".")[0])
    # a file whose CRC32 matches its name, with nothing after the bundle, but not Steam's bytes
    other = header_bundle(b"modified")
    changed = CRCUtils.apply_crc_fix(other[:-4], expected)
    assert changed is not None and len(changed) == len(other)
    library._hashes = type(library._hashes)()
    official_bytes = official.read_bytes()
    official.write_bytes(changed)
    assert library._is_official_as(official, official.name)  # without the consent: the CRC rule
    allowed[0] = True
    library._hashes = type(library._hashes)()
    assert not library._is_official_as(official, official.name)
    official.write_bytes(official_bytes)
    library._hashes = type(library._hashes)()
    assert library._is_official_as(official, official.name)
    assert library._official_size(official.name) == len(official_bytes)


# --- how a kept original was verified (BL-194) ---

def crc_matching_mod(official: Path) -> bytes:
    """A mod of the official file's size whose CRC32 matches its name, with nothing after the bundle."""
    expected = int(official.name.rsplit("_", 1)[1].split(".")[0])
    other = header_bundle(b"modified")
    changed = CRCUtils.apply_crc_fix(other[:-4], expected)
    assert changed is not None and len(changed) == len(official.read_bytes())
    return changed


def checks_of(library: ModLibrary) -> dict:
    import json
    return json.loads((library.root / "originals.json").read_text())["originals"]


def test_an_original_kept_by_the_crc_rule_alone_is_marked_so(tmp_path, steam):
    library_dir, install, game_data, official, keep, listed = steam
    allowed = [False]
    library = make_library(tmp_path, game_data, allowed)
    pin = library.current_pin()
    official.write_bytes(crc_matching_mod(official))
    library._hashes = type(library._hashes)()
    kept = library._original_path(official)
    assert library._keep_original(official, pin) == official
    assert kept.is_file() and checks_of(library)[official.name]["by"] == "crc"
    # the copy the screens name: kept, verified by the CRC rule alone
    official.write_bytes(header_bundle(b"a larger mod"))
    files = [f for f in library.state("test").files if f.file.name == official.name]
    assert files and files[0].copy == "kept" and files[0].checked == "crc"


def test_a_kept_original_steams_list_rejects_is_flagged_and_never_written_back(tmp_path, steam):
    library_dir, install, game_data, official, keep, listed = steam
    keep(listed)
    allowed = [False]
    library = make_library(tmp_path, game_data, allowed)
    pin = library.current_pin()
    official_bytes = official.read_bytes()
    official.write_bytes(crc_matching_mod(official))
    library._hashes = type(library._hashes)()
    kept = library._original_path(official)
    library._keep_original(official, pin)  # no consent: the CRC rule keeps the mod as the official copy
    assert checks_of(library)[official.name]["by"] == "crc"
    allowed[0] = True  # Steam's list becomes available: the scan checks the kept originals
    assert library.check_originals() == {"failed": 1}
    assert checks_of(library)[official.name]["by"] == "failed"
    with pytest.raises(ServiceError) as raised:
        library._official_for(official, pin)
    assert raised.value.code == "original_unavailable"
    # without the list again, neither the kept copy nor the same bytes in the game folder pass as official
    allowed[0] = False
    library._steam_roots_cache = None
    with pytest.raises(ServiceError) as raised:
        library._official_for(official, pin)
    assert raised.value.code == "original_unavailable"
    assert kept.is_file()  # flagged, never deleted
    # a game file of other bytes: the screens name no official copy for it
    official.write_bytes(header_bundle(b"a larger mod"))
    entry = [f for f in library.state("test").files if f.file.name == official.name][0]
    assert not entry.official and entry.copy is None and not entry.restorable
    # once the game file is official again (Steam's verify), it replaces the rejected copy
    official.write_bytes(official_bytes)
    allowed[0] = True
    assert library._official_for(official, pin) == official
    library._keep_original(official, pin)
    assert kept.read_bytes() == official_bytes and checks_of(library)[official.name]["by"] == "steam"


def test_a_kept_original_steams_list_confirms_stays_verified_without_the_list(tmp_path, steam):
    library_dir, install, game_data, official, keep, listed = steam
    keep(listed)
    allowed = [False]
    library = make_library(tmp_path, game_data, allowed)
    pin = library.current_pin()
    library._keep_original(official, pin)
    assert checks_of(library)[official.name]["by"] == "crc"
    allowed[0] = True
    assert library.check_originals() == {"steam": 1}
    assert library.check_originals() == {}  # judged once
    allowed[0] = False
    library._steam_roots_cache = None
    official.write_bytes(header_bundle(b"a larger mod"))
    entry = [f for f in library.state("test").files if f.file.name == official.name][0]
    assert entry.copy == "kept" and entry.checked == "steam"
    assert library._official_for(official, pin) == library._original_path(official)
