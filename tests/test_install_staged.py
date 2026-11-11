"""The library builds a planned install into staged copies of the parts (bmpt.library.install), also in isolated
development runs."""

import binascii
from dataclasses import replace

import pytest

from ba_modding_toolkit.bundle import Bundle
from ba_modding_toolkit.models import FilePair, SaveOptions
from ba_modding_toolkit.naming import parse_filename
from bmpt.library import install
from bmpt.library.rebuild import RebuildResult


def make_group(tmp_path, core, action=install.Action.DIRECT_REPLACE, need_crc_fix=False):
    payload = f"mod {core}".encode()
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    name = f"assets-_mx-spinecharacters-{core}-2026-10-20_003_{crc}.bundle"
    source, target = tmp_path / "mods" / name, tmp_path / "game" / name
    source.parent.mkdir(exist_ok=True)
    target.parent.mkdir(exist_ok=True)
    source.write_bytes(payload)
    target.write_bytes(b"original")
    parsed = parse_filename(name)
    file = install.PlannedFile(source, parsed, action,
                               existing_path=target if action == install.Action.DIRECT_REPLACE else None,
                               need_crc_fix=need_crc_fix)
    return install.PlannedGroup(parsed.prefix, parsed.category, core, [file],
                                target_paths=[target] if action == install.Action.UPDATE else [])


@pytest.mark.parametrize("crc", [None, "mismatch", "missing"])
def test_bundle_crc_checks_bytes_without_loading_unity(tmp_path, monkeypatch, crc):
    payload = b"synthetic bytes, not a Unity bundle"
    expected = binascii.crc32(payload) & 0xFFFFFFFF
    suffix = str(expected if crc is None else expected ^ 1) if crc != "missing" else "unknown"
    path = tmp_path / f"bundle_{suffix}.bundle"
    path.write_bytes(payload)
    monkeypatch.setattr(Bundle, "load", lambda *args: pytest.fail("CRC checking must not load Unity"))
    assert Bundle.check_crc_match(path) is (crc is None)


def test_crc_matched_direct_install_copies_without_resaving(tmp_path, monkeypatch):
    group = make_group(tmp_path, "matched", need_crc_fix=True)
    file = group.files[0]
    monkeypatch.setattr(Bundle, "load", lambda *args: pytest.fail("matching CRC must use a direct copy"))

    assert install.install_staged([group], tmp_path / "output", {"Texture2D"}, SaveOptions()) == []

    assert file.existing_path.read_bytes() == file.mod_path.read_bytes()
    assert not file.existing_path.with_suffix(".bundle.backup").exists()  # staged copies keep no backup


def test_crc_mismatch_resaves_in_temp_and_preserves_caller_options(tmp_path, monkeypatch):
    group = make_group(tmp_path, "mismatch", need_crc_fix=True)
    file = group.files[0]
    file.mod_path.write_bytes(b"changed CRC")
    options = SaveOptions(perform_crc=False, extra_bytes=b"tail", compression="lz4")
    saved = []

    class RebuiltBundle:
        def save(self, path, save_options):
            saved.append(path)
            assert save_options == replace(options, perform_crc=True)
            assert path.parent != file.existing_path.parent
            path.write_bytes(b"rebuilt")
            return True, ""

    monkeypatch.setattr(Bundle, "load", lambda *args: RebuiltBundle())
    assert install.install_staged([group], tmp_path / "output", {"Texture2D"}, options) == []

    assert file.existing_path.read_bytes() == b"rebuilt"
    assert file.mod_path.read_bytes() == b"changed CRC"
    assert options.perform_crc is False
    assert len(saved) == 1 and not saved[0].exists()


def test_groups_build_in_order_and_report_each_failure(tmp_path, monkeypatch):
    groups = [make_group(tmp_path, core, install.Action.UPDATE) for core in ("first", "unchanged", "failed", "raised")]
    groups.append(make_group(tmp_path, "direct"))
    progress = []

    def update(source_paths, target_paths, output_dir, *args, **kwargs):
        core = parse_filename(source_paths[0].name).core
        assert kwargs["skip_unchanged"] is True and kwargs["match_strategy"] == "cont_name_type"
        if core == "raised":
            raise OSError("unreadable bundle")
        if core == "failed":
            return RebuildResult(False, "incompatible skeleton")
        if core == "unchanged":
            return RebuildResult(True, "", all_unchanged=True)
        output = output_dir / target_paths[0].name
        output.write_bytes(f"updated {core}".encode())
        return RebuildResult(True, "updated", [FilePair(output, target_paths[0])])

    monkeypatch.setattr(install, "rebuild", update)
    failures = install.install_staged(groups, tmp_path / "output", {"Texture2D"}, SaveOptions(),
                                      progress=lambda *args: progress.append(args))

    assert set(failures) == {("failed", "incompatible skeleton"), ("raised", "unreadable bundle")}
    assert progress == [(i, len(groups), group.core) for i, group in enumerate(groups, 1)]
    assert groups[0].target_paths[0].read_bytes() == b"updated first"
    assert groups[1].target_paths[0].read_bytes() == b"original"
    direct = groups[-1].files[0]
    assert direct.existing_path.read_bytes() == direct.mod_path.read_bytes()


def test_mixed_group_copies_the_direct_part_when_the_update_is_unchanged(tmp_path, monkeypatch):
    direct = make_group(tmp_path, "direct")
    update = make_group(tmp_path, "unchanged", install.Action.UPDATE)
    direct.files += update.files
    direct.target_paths = update.target_paths
    monkeypatch.setattr(install, "rebuild", lambda *args, **kwargs: RebuildResult(True, "", all_unchanged=True))

    assert install.install_staged([direct], tmp_path / "output", {"Texture2D"}, SaveOptions()) == []
    assert direct.files[0].existing_path.read_bytes() == direct.files[0].mod_path.read_bytes()
    assert update.target_paths[0].read_bytes() == b"original"


def test_build_keeps_the_dev_write_guard(tmp_path, monkeypatch):
    groups = [make_group(tmp_path, core, need_crc_fix=True) for core in ("one", "two")]
    monkeypatch.setenv("BAMT_DEV_ROOTS", str(tmp_path))
    monkeypatch.setenv("BAMT_DEV_WRITABLE", "")

    failures = install.install_staged(groups, tmp_path / "output", {"Texture2D"}, SaveOptions())

    assert {core for core, _ in failures} == {"one", "two"}
    for group in groups:
        assert group.files[0].existing_path.read_bytes() == b"original"


@pytest.mark.parametrize("create", [True, False])
def test_backup_file_keeps_the_classic_adjacent_backup_when_asked(tmp_path, create):
    target = tmp_path / "game.bundle"
    target.write_bytes(b"official")
    install.backup_file(target, create)
    assert (tmp_path / "game.bundle.backup").exists() is create
