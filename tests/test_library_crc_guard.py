"""The last CRC check of a library write: bytes whose CRC is not the one in the target's name never reach an install
that needs the CRC fix."""
import pytest

from ba_modding_toolkit.naming import parse_filename
from ba_modding_toolkit.utils import CRCUtils
from bmpt.library.service import ServiceError
from ba_modding_toolkit.utils.common import no_log

from test_library import expect_error, game_bundle, make_mod, setup_library  # noqa: F401


def crc_ok(path):
    return CRCUtils.compute_crc32(path.read_bytes()) == int(parse_filename(path.name).crc)


def _needs_crc(env, monkeypatch, needed=True):
    monkeypatch.setattr(env.settings, "crc_needed", lambda target: needed)


def test_a_write_with_a_wrong_crc_is_refused_and_leaves_the_game_file(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch)
    before = env.original.read_bytes()
    prepared = make_mod(env.original, env.tmp / "prepared")  # not CRC fixed
    expect_error("crc_fix_failed", lambda: env.library._commit({env.original: prepared}, "mod:x", no_log))
    assert env.original.read_bytes() == before
    assert not list((env.root / "transactions").glob("*"))
    assert not env.library._applied


def test_the_refusal_names_the_target_file(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch)
    prepared = make_mod(env.original, env.tmp / "prepared")
    with pytest.raises(ServiceError) as caught:
        env.library._commit({env.original: prepared}, "mod:x", no_log)
    assert env.original.name in str(caught.value.to_json()["params"])


def test_an_install_without_the_crc_fix_takes_the_file_as_it_is(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch, needed=False)
    prepared = make_mod(env.original, env.tmp / "prepared")
    env.library._commit({env.original: prepared}, "mod:x", no_log)
    assert env.original.read_bytes() == prepared.read_bytes()


def test_a_fixed_mod_and_the_official_restore_pass(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch)
    official = env.original.read_bytes()
    result = env.library.add_and_apply([make_mod(env.original, env.tmp / "upload", fixed=True)])
    assert result.applied and not result.failed and crc_ok(env.original)
    assert env.original.read_bytes() != official
    env.library.restore("test")
    assert env.original.read_bytes() == official


def test_a_rollback_puts_back_what_was_there(setup_library, monkeypatch):
    env = setup_library
    prepared = make_mod(env.original, env.tmp / "prepared")
    env.library._commit({env.original: prepared}, "mod:x", no_log)  # written while the fix was not needed
    _needs_crc(env, monkeypatch)
    fixed = make_mod(env.original, env.tmp / "fixed", payload=b"other mod", fixed=True)
    env.library._commit({env.original: fixed}, "mod:y", no_log)
    env.library._commit({env.original: prepared}, "mod:x", no_log, untested=False)
    assert env.original.read_bytes() == prepared.read_bytes()


def _crc_only_update(env, v1):
    v1.unlink()  # the update removes the old file and ships one whose name differs only in its CRC
    v2 = game_bundle(env.game_dir, payload=b"official after update")
    assert v2.name != v1.name and crc_ok(v2)
    return v2


def test_a_mod_for_the_previous_crc_never_reaches_the_game_with_a_wrong_crc(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch)
    v1 = env.original
    result = env.library.add_and_apply([make_mod(v1, env.tmp / "upload", fixed=True)])
    assert result.applied and not result.failed
    mod_id = env.library.mods()[0].id
    env.library.restore("test")
    v2 = _crc_only_update(env, v1)
    try:
        env.library.apply("test", f"mod:{mod_id}")
    except ServiceError:
        pass  # refused here, or the rebuild with the new CRC failed on these header-only bundles
    assert crc_ok(v2)


def test_a_toggle_after_a_crc_only_update_never_writes_a_wrong_crc(setup_library, monkeypatch):
    env = setup_library
    _needs_crc(env, monkeypatch)
    v1 = env.original
    result = env.library.add_and_apply([make_mod(v1, env.tmp / "upload", fixed=True)])
    assert result.applied and not result.failed
    env.library.toggle(False, True)
    v2 = _crc_only_update(env, v1)
    env.library.toggle(True, False)
    assert crc_ok(v2)
