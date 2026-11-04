"""Errors carry the parameters their text names, above all the file (no text ending at its colon)."""

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_modding_toolkit.bundle import Bundle
from bmpt.errors import ServiceError
from bmpt.library import integrity, manual_identity
from bmpt.webui import batch_import, export_service

from test_batch_import import analyze, env, finish, rpc, run
from test_library import game_bundle, make_mod, setup_library

PACKAGE = Path(__file__).parents[1] / "src" / "bmpt"
ENGINE_PACKAGE = PACKAGE.with_name("ba_modding_toolkit")
LOCALES = sorted((PACKAGE / "locales").glob("*.json"))


def placeholders() -> dict[str, set[str]]:
    """The placeholders of every ``webui.error.<code>`` text, over all locales."""
    found: dict[str, set[str]] = {}
    for locale in LOCALES:
        for code, text in json.loads(locale.read_text(encoding="utf-8"))["webui"]["error"].items():
            if isinstance(text, str):
                found.setdefault(code, set()).update(re.findall(r"\{(\w+)\}", text))
    return found


def test_every_error_raised_with_a_constant_code_passes_the_parameters_of_its_text():
    needed = {code: names for code, names in placeholders().items() if names}
    assert {"file", "name", "error"} <= set().union(*needed.values())
    missing = []
    for path in sorted([*PACKAGE.rglob("*.py"), *ENGINE_PACKAGE.rglob("*.py")]):
        relative = path.relative_to(PACKAGE if path.is_relative_to(PACKAGE) else ENGINE_PACKAGE).as_posix()
        if relative.startswith(("gui/", "cli/")):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            code = node.args[0].value
            if name not in ("ServiceError", "RemapError") or code not in needed:
                continue
            if any(keyword.arg is None for keyword in node.keywords):
                continue  # parameters forwarded as a whole
            lacking = needed[code] - {keyword.arg for keyword in node.keywords}
            if lacking:
                missing.append(f"{relative}:{node.lineno} {code} lacks {sorted(lacking)}")
    assert missing == []


def raised(action):
    with pytest.raises(ServiceError) as caught:
        action()
    json.dumps(caught.value.to_json())
    return caught.value


# --- library --------------------------------------------------------------------------------------------------

def test_manual_identity_names_the_refused_file(tmp_path, monkeypatch):
    error = raised(lambda: manual_identity.validate({"one.bundle": tmp_path / "one.bundle",
                                                     "notes.txt": tmp_path / "notes.txt"}))
    assert (error.code, error.params) == ("mod_unsupported", {"file": "notes.txt"})
    error = raised(lambda: manual_identity.validate({"../one.bundle": tmp_path / "one.bundle"}))
    assert error.params == {"file": "../one.bundle"}
    # Nothing to name: the code whose text has no placeholder
    error = raised(lambda: manual_identity.validate({}))
    assert (error.code, error.params) == ("source_unsupported", {})
    assert not placeholders()["source_unsupported"]

    paths = [tmp_path / "one.bundle", tmp_path / "two.bundle"]
    contents = {path: integrity.Contents(platforms={"Android"}) for path in paths}
    monkeypatch.setattr(integrity.Inspection, "inspect", lambda self, path, **kwargs: contents[path])
    for cores, code in (({"one_spr", "two_home"}, "mod_target_ambiguous"), (set(), "mod_unsupported")):
        monkeypatch.setattr(manual_identity, "content_cores", lambda path, cores=cores: cores)
        error = raised(lambda: manual_identity.validate({path.name: path for path in paths}))
        assert (error.code, error.params) == (code, {"file": "one.bundle"})


def test_add_files_names_the_first_file_of_an_unsupported_kind(setup_library):
    env = setup_library
    source = make_mod(env.original, env.tmp / "incoming")
    error = raised(lambda: env.library.add_files("nonsense", [source]))
    assert (error.code, error.params) == ("mod_unsupported", {"file": source, "kind": "nonsense"})
    assert error.to_json()["params"]["file"] == str(source)
    error = raised(lambda: env.library.add_files("nonsense", []))
    assert (error.code, error.params) == ("source_unsupported", {"kind": "nonsense"})


def test_store_names_the_file_it_refuses(setup_library):
    env = setup_library
    source = make_mod(env.original, env.tmp / "incoming")
    error = raised(lambda: env.library._store({source.name: source, "notes.txt": source}, "label", "test", "spr"))
    assert (error.code, error.params) == ("mod_unsupported", {"file": "notes.txt"})
    error = raised(lambda: env.library._store({}, "label", "test", "spr"))
    assert (error.code, error.params) == ("source_unsupported", {})
    assert not env.library.mods()


@pytest.mark.parametrize("failing", ["mod", "official"])
def test_compare_names_the_bundle_it_cannot_read(setup_library, monkeypatch, failing):
    env = setup_library
    mod, official = env.tmp / "mod.bundle", env.tmp / "official.bundle"
    kind = type("Kind", (), {"name": "Texture2D"})()

    def unreadable():
        raise EOFError("short read")

    def load(path, *args, **kwargs):
        broken = path == (mod if failing == "mod" else official)
        objects = [SimpleNamespace(type=kind, peek_name=lambda: "n", get_raw_data=lambda: b"d", read=unreadable)]
        return SimpleNamespace(env=SimpleNamespace(objects=objects if broken else []))

    monkeypatch.setattr(Bundle, "load", load)
    monkeypatch.setattr("bmpt.library.service.AssetType", SimpleNamespace(
        Texture2D=kind, TextAsset=kind, Mesh=kind, AnimationClip=kind))
    error = raised(lambda: env.library._compare_paths("id", [mod], [official]))
    assert (error.code, error.params) == ("bundle_invalid", {"file": mod if failing == "mod" else official})


# --- batch import ---------------------------------------------------------------------------------------------

def test_an_unreadable_source_names_its_own_file(env):
    good = make_mod(env.original, env.tmp / "incoming")
    broken = game_bundle(env.tmp / "incoming", "other_spr", payload=b"x")
    broken.write_bytes(b"broken")
    entries = {e["paths"][0]: e for e in analyze(env, [good, broken])["entries"]}
    entry = entries[str(broken)]
    assert (entry["state"], entry["reason"]) == ("blocked", "unreadable")
    assert entry["error"] == {"code": "bundle_invalid", "params": {"file": str(broken)}}
    assert entry["params"] == {"file": str(broken)}


def test_a_preflight_read_failure_names_the_entry_file(env, monkeypatch):
    source = make_mod(env.original, env.tmp / "incoming")

    def fail(*args, **kwargs):
        raise ValueError("Not a readable UnityFS directory")

    monkeypatch.setattr(env.library, "inspect_import", fail)
    entry, = analyze(env, [source])["entries"]
    assert entry["reason"] == "unreadable"
    assert entry["error"]["code"] == "bundle_invalid"
    assert entry["error"]["params"] == {"file": str(source), "error": "Not a readable UnityFS directory"}


@pytest.mark.parametrize("problem", ["duplicate", "count"])
def test_conflicting_game_files_name_the_entry_file(env, monkeypatch, problem):
    source = game_bundle(env.tmp / "old", date="2025-01-01", payload=b"old mod")
    inspect = env.library.inspect_import
    if problem == "duplicate":
        def doubled(*args, **kwargs):
            report = inspect(*args, **kwargs)
            report["targets"] = [*report["targets"], *report["targets"]]
            return report

        monkeypatch.setattr(env.library, "inspect_import", doubled)
    else:
        monkeypatch.setattr(env.library, "official_bundles", lambda *args, **kwargs: [])
    entry, = analyze(env, [source])["entries"]
    assert entry["reason"] == "game_file_conflict"
    assert entry["error"] == {"code": "game_file_conflict", "params": {"file": str(source)}}


def test_a_mod_stored_under_another_target_names_the_entry_file(env):
    source = make_mod(env.original, env.tmp / "incoming")
    entry, = analyze(env, [source])["entries"]
    stored, = env.library.add([source])
    candidate = env.services.batch_import._latest[entry["id"]]
    candidate.error = "mod_target_ambiguous"
    candidate.entry["assignment"] = "another-target"
    assert stored.target != "another-target"
    env.services.batch_import._judge(candidate, env.library, env.library.current_pin())
    assert candidate.entry["reason"] == "mod_target_ambiguous"
    assert candidate.entry["error"] == {"code": "mod_target_ambiguous", "params": {"file": str(source)}}


def test_assigning_a_blocked_entry_keeps_the_recorded_file(env, monkeypatch):
    monkeypatch.setattr(env.catalog, "assignment_variant", lambda key: SimpleNamespace(key=key), raising=False)
    source = make_mod(env.original, env.tmp / "incoming")
    other = game_bundle(env.tmp / "incoming", "other_spr", payload=b"x")
    other.write_bytes(b"broken")
    broken, = [e for e in analyze(env, [source, other])["entries"] if e["paths"] == [str(other)]]
    assert broken["reason"] == "unreadable"
    batch = env.services.batch_import
    candidate = batch._latest[broken["id"]]
    assert candidate.error == "unreadable" and candidate.failure["params"] == {"file": str(other)}
    error = raised(lambda: batch.assign_character(broken["id"], "future-student"))
    assert (error.code, error.params) == ("unreadable", {"file": str(other)})
    # Without recorded details, the first file of the entry is named
    candidate.failure, candidate.error = None, "library_io_error"
    error = batch_import.blocked_error(candidate)
    assert (error.code, error.params) == ("library_io_error", {"file": str(other)})


def test_a_write_failure_names_the_failing_file_else_the_entry_file(env, monkeypatch):
    source = make_mod(env.original, env.tmp / "incoming")
    entry, = analyze(env, [source])["entries"]
    failures = iter([OSError(28, "No space left on device", "/library/mods/x.bundle"), OSError("no name given")])

    def fail(*args, **kwargs):
        raise next(failures)

    monkeypatch.setattr(env.library, "add_files", fail)
    first, = run(env, [entry], enableAfterImport=False)
    assert first["reason"] == "library_io_error"
    assert first["params"] == {"file": "/library/mods/x.bundle", "error": "[Errno 28] No space left on device: "
                                                                          "'/library/mods/x.bundle'"}
    entry, = analyze(env, [source])["entries"]
    second, = run(env, [entry], enableAfterImport=False)
    assert second["params"]["file"] == str(source)


def test_an_import_that_stores_several_mods_names_the_entry_file(env, monkeypatch):
    source = make_mod(env.original, env.tmp / "incoming")
    entry, = analyze(env, [source])["entries"]

    class Added(list):
        duplicates = ()

    monkeypatch.setattr(env.library, "add_files", lambda *args, **kwargs: Added([object(), object()]))
    result, = run(env, [entry], enableAfterImport=False)
    assert (result["outcome"], result["reason"]) == ("failed", "mod_unsupported")
    assert result["params"] == {"file": str(source)}
    assert result["error"]["params"] == {"file": str(source)}


# --- export ---------------------------------------------------------------------------------------------------

def test_export_names_the_colliding_member(setup_library, monkeypatch):
    env = setup_library
    env.library.add([make_mod(env.original, env.tmp / "incoming")])
    names = ["Part-1.bundle", "part-1.bundle", "other.bundle"]
    paths = [env.tmp / name for name in names]
    monkeypatch.setattr(export_service, "_prepare", lambda *args, **kwargs: (paths, {}))
    monkeypatch.setattr(export_service, "_member_name", lambda path: path.name)
    error = raised(lambda: export_service.export_zip(
        SimpleNamespace(settings=env.settings), env.library, env.library.current_pin(), scope="library", target=None,
        folder=env.tmp / "exports", server="global", platform="windows", kind=None,
        log=lambda *_: None, progress=lambda *_: None))
    assert (error.code, error.params) == ("game_file_conflict", {"file": "Part-1.bundle"})
