"""Write and inspect release fixtures using the serializers in an extracted local tag."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import inspect
import io
import json
import os
import runpy
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID


def _reject_network(event: str, args: tuple) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto", "webbrowser.open"}:
        raise RuntimeError("Network and desktop launches are disabled for release fixtures")
    if event in {"os.startfile", "os.system"}:
        raise RuntimeError("Desktop launches are disabled for release fixtures")
    if event in {"winreg.ConnectRegistry", "winreg.OpenKey", "winreg.CreateKey"}:
        raise RuntimeError("Registry access is disabled for release fixtures")


def _confine(root: Path) -> None:
    roots = (root, Path.cwd().resolve())

    def check(path: object) -> None:
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        target = Path(os.fsdecode(path)).resolve()
        if not any(target.is_relative_to(owned) for owned in roots):
            raise RuntimeError(f"Fixture write escapes its scratch roots: {target}")

    def audit(event: str, args: tuple) -> None:
        _reject_network(event, args)
        if event == "open":
            path, mode, flags = args
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                check(path)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            check(args[0])
            check(args[1])
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            check(args[0])

    sys.addaudithook(audit)
    socket.getaddrinfo = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("DNS is disabled"))


def _module(package: str, name: str):
    return importlib.import_module(f"{package}.{name}")


def _available(package: str, name: str):
    try:
        return _module(package, name)
    except ModuleNotFoundError as error:
        if error.name != f"{package}.{name}":
            raise
        return None


def _settings(env, data: Path) -> None:
    env.settings.path = data / "config.toml"
    (env.game_dir / "GameData" / "Windows").mkdir(parents=True, exist_ok=True)
    if hasattr(env.library, "confirm_untested"):
        catalog_hash = env.game_dir / "Catalog" / "Windows" / "catalog_Windows.hash"
        catalog_hash.parent.mkdir(parents=True, exist_ok=True)
        catalog_hash.write_bytes(b"upgrade-matrix-fixture")
    env.settings.set("file_source", "windows_global")
    env.settings.set("game_resource_dir", str(env.game_dir))
    env.settings.set("enable_crc_correction", "false")
    env.settings.set("compression_method", "none")
    env.settings.save()


def _confirm_fixture_build(library, game: Path) -> None:
    confirm = getattr(library, "confirm_untested", None)
    if confirm is None:
        return
    game = game.resolve()
    pin = library.current_pin()
    if pin is None or pin.root != game or any(not path.is_relative_to(game) for path in pin.dirs):
        raise RuntimeError("The build confirmation must name only the fabricated game install")
    version = library.untested_version(pin)
    cores = sorted(library._index(pin)) if version else []
    if cores:
        confirm({pin.key: {"version": version, "cores": cores}})


def _setup(root: Path):
    from pytest import MonkeyPatch

    test_dir = Path.cwd() / "tests"
    sys.path.insert(0, str(test_dir))
    fixture = runpy.run_path(str(test_dir / "test_library.py"))
    setup = fixture["setup_library"].__wrapped__
    patch = MonkeyPatch()
    args = [root / "work", patch]
    if "header_only_bundles" in inspect.signature(setup).parameters:
        helpers = runpy.run_path(str(test_dir / "conftest.py"))
        helpers["header_only_bundles"].__wrapped__(patch)
        args.append(None)
    return fixture, setup(*args), patch


def _fixture_png() -> str:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (64, 64), (32, 64, 128, 255)).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _seed_camera(package: str, env, fixture: dict, patch, observed: list[str]) -> None:
    camera = _available(package, "library.lobby_camera")
    if camera is None:
        return
    helpers = runpy.run_path(str(Path.cwd() / "tests" / "test_lobby_camera.py"))
    prefab = helpers["official_like"]()
    data = fixture["header_bundle"](b"synthetic prefab")
    name = f"ui-uilobbyelement-test_2026-01-01_assets_all_{fixture['CRCUtils'].compute_crc32(data)}.bundle"
    path = env.game_dir / name
    path.write_bytes(data)

    def patch_prefab(base, output, root, scale, offset, **options):
        helpers["patched"](prefab, scale, offset)
        output.write_bytes(fixture["header_bundle"](f"{root}: {scale} {offset}".encode()))
        return False

    with patch.context() as local:
        local.setattr(camera, "patch_prefab", patch_prefab)
        local.setattr(camera, "_slot", lambda *_: "fixture-cab")
        if hasattr(camera, "timeline_file"):
            local.setattr(camera, "timeline_file", lambda *_: None)
        camera.save(
            env.library,
            "test",
            "official",
            1.25,
            (0.0, -120.0),
            name,
            "LobbyTest",
            env.library.current_pin(),
            lambda *_: None,
        )
    observed.append("S.memorialCamera")


def _seed_collection(package: str, env, fixture: dict, patch, observed: list[str]) -> None:
    portrait_edit = _available(package, "library.portrait_edit")
    if portrait_edit is None:
        return
    photographs = _module(package, "library.photographs").Photographs(env.root)
    photo = photographs.selection("test")
    if not photo:
        raise RuntimeError("The capture fixture is absent")
    data = fixture["header_bundle"](b"synthetic Collection")
    crc = fixture["CRCUtils"].compute_crc32(data)
    target = env.game_dir / f"uis-01_common-14_charactercollect-fixture_assets_all_{crc}.bundle"
    target.write_bytes(data)
    _confirm_fixture_build(env.library, env.game_dir)

    def patch_portraits(base, official, output, edits, **options):
        output.write_bytes(fixture["header_bundle"](b"composite:" + b"".join(edits.values())))

    with patch.context() as local:
        local.setattr(portrait_edit, "patch_portraits", patch_portraits)
        portrait_edit.apply(
            env.library,
            "test",
            photo,
            photographs.image("test", photo),
            {"bundle": target.name, "name": "FixtureThumbnail"},
            env.library.current_pin(),
            lambda *_: None,
            None,
            configure_only=True,
        )
    observed.append("S.rosterThumbnails")


def _seed_optional(package: str, state, env, release: str, observed: list[str]) -> None:
    if hasattr(state, "set_aliases"):
        state.set_aliases("test", ["literal variant:favorite", "test_spr"])
        state.set("roster", {"merged": True, "recruit": "yes"})
        state.set("lobby_zoom_out", release != "0.1.0-alpha.2")
        if release != "0.1.0-alpha.2":
            state.set("lobby_zoom", "game")
        state.set("lobby_post", False)
        observed.append("U.aliases")
    if hasattr(state, "set_preview_source"):
        state.set_preview_source(str(env.game_dir.resolve()), "test", "spr", "variant:other")
        observed.append("U.preview")
    if _available(package, "webui.batch_import"):
        state.set("batch_import", {"strategy": "cont_name_type", "types": ["Texture2D"], "workers": 2})
        observed.append("J")
    hall = _available(package, "webui.student_hall")
    if hall is not None:
        value = hall.empty_hall()
        portrait, memorial = str(UUID(int=1)), str(UUID(int=2))
        value["portraits"] = [{"id": portrait, "variant": "test", "source": "official", "mirror": True}]
        value["memorials"] = [{"id": memorial, "variant": "test", "source": "official"}]
        value["active"] = memorial
        value["mode"] = "memorial"
        saved = hall.save_hall(state, value, value["revision"])
        hall.save_hall(state, {**saved, "mode": "portraits"}, saved["revision"])
        state.set("hall_viewed", ["test:official"])
        observed.append("H")
    captures = _available(package, "library.photographs")
    if captures is not None:
        photographs = captures.Photographs(env.root)
        png = _fixture_png()
        saved = photographs.save("test", png, "portrait", "expression", source="variant:other")
        photographs.save("test", png, "lobby", action="Idle", source="official")
        photographs.save("other", png, "portrait", source="official")
        photographs.select("test", saved["saved"])
        photographs.select("other", None)
        observed.append("F")
    archive = _available(package, "library.rig_archive")
    if archive is not None:
        rigs = archive.RigArchive(env.root / "rigs")
        rigs.add("test_spr", "test.atlas", b"page.png\n", "test.skel", b"rig", "sample layout", {})
        rigs.set_stamp(str(env.game_dir.resolve()), "fixture-resource")
        observed.append("R")
    verdicts = _available(package, "library.original_checks")
    kept = env.root / "originals" / env.original.name
    if verdicts is not None and kept.is_file():
        store = verdicts.OriginalChecks(env.root)
        store.save(kept, "crc", env.library._hashes.get(kept))
        observed.append("O")


def _seed_update(fixture: dict, env, patch, observed: list[str]) -> None:
    writer = fixture["test_update_uses_official_baseline_preserves_previous_and_reports_unchanged"]
    optional = {"legacy": False} if "legacy" in inspect.signature(writer).parameters else {}
    with patch.context() as local_patch:
        writer(env, local_patch, **optional)
    observed.append("B.previous")


def _seed_package(package: str, root: Path, env, mod, observed: list[str]) -> None:
    sidecars = _available(package, "library.fingerprint")
    if sidecars is not None:
        parts = env.library._paths(env.library._record(mod.id))
        document = sidecars.document("import", [], complete=False)
        sidecars.write(parts[0].parent, document, {path.name: env.library._hashes.get(path).sha1 for path in parts})
        observed.append("A")
    exports = _available(package, "webui.export_service")
    if exports is not None:
        services = SimpleNamespace(settings=env.settings, state=None, catalog=env.catalog)
        pin = env.library.current_pin()
        target = exports._pin_target(env.library, env.settings, pin)
        if target != {"server": "global", "platform": "windows"}:
            raise RuntimeError(f"The fixture did not resolve to a Global Windows export target: {target}")
        parameters = dict(
            scope="single",
            target=f"mod:{mod.id}",
            folder=root / "output",
            kind=None,
            **target,
        )
        if "log" in inspect.signature(exports.export_zip).parameters:
            parameters.update(log=lambda *_: None, progress=lambda *_: None)
        exports.export_zip(services, env.library, pin, **parameters)
        observed.append("Y.package")


JOURNALS = {"T": "transactions", "I": "import-transactions", "D": "asset-deletions"}


def _fault_journals(package: str, boundary: str) -> None:
    kind, expected = boundary.split(":", 1)
    modules = (
        "library.storage",
        "library.service",
        "library.service.library",
        "library.service.switch_plan",
        "library.service.imports",
        "library.service.updating",
        "library.asset_delete",
    )
    active = {}

    def stop(path) -> None:
        print(json.dumps({"legacyBoundary": boundary, "journal": str(path)}), flush=True)
        os._exit(91)

    def wrapped_write(write):
        def fault(path, data):
            write(path, data)
            if path.name == "state.json" and expected == "published" and active:
                key = active["document"]["id"]
                records = {row["id"]: row for row in data.get("mods", ()) if isinstance(row, dict)}
                if key in records:
                    stop(active["path"])
            if path.name != "journal.json" or JOURNALS[kind] not in path.parts:
                return
            active.update(path=path, document=json.loads(json.dumps(data)))
            phase = data.get("phase")
            if expected == phase:
                stop(path)
            if kind == "I" and expected == "rollback" and phase == "publishing":
                raise OSError("Fixture interrupted while publishing the import")
            if kind == "D" and expected == "gameBackedUp" and phase == "active" and data.get("games"):
                stop(path)
            if kind == "D" and expected == "photoBackedUp" and phase == "active" and data.get("photo"):
                stop(path)

        return fault

    def wrapped_verify(verify):
        def fault(path, digest):
            verify(path, digest)
            if kind != "T" or expected != "afterFirstWrite" or not active:
                return
            document = active["document"]
            if document["phase"] == "writing" and any(
                Path(item["target"]) == path and item["after"] == digest for item in document["files"]
            ):
                stop(active["path"])

        return fault

    for name in modules:
        module = _available(package, name)
        if module is None:
            continue
        if hasattr(module, "atomic_json"):
            module.atomic_json = wrapped_write(module.atomic_json)
        if hasattr(module, "verify_file"):
            module.verify_file = wrapped_verify(module.verify_file)


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.bundle"))
    }


def _journal_receipts(library: Path) -> list[dict]:
    receipts = []
    for kind, name in JOURNALS.items():
        for path in sorted((library / name).glob("*/journal.json")):
            doc = json.loads(path.read_bytes())
            receipts.append({"store": kind, "journal": path.parent.name, "phase": doc["phase"]})
    return receipts


def _stable_state(raw: dict) -> dict:
    return {
        "mods": sorted(
            (key, row.get("files", []), row.get("label"), row.get("alias"), row.get("target"))
            for row in raw.get("mods", ())
            if isinstance(row, dict) and (key := row.get("id"))
        ),
        "favorites": sorted(raw.get("favorites", ())),
        "applied": {
            key: {field: value for field, value in info.items() if field not in ("at", "mtime")}
            for key, info in raw.get("applied", {}).items()
        },
    }


def _profiles_view(path: Path) -> dict | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_bytes())
    return {
        "enabled": raw.get("enabled", True),
        "unfinished": raw.get("unfinished", []),
        "configured": {
            install: {
                core: {key: item for key, item in entry.items() if key != "since"}
                for core, entry in folder.get("configured", {}).items()
            }
            for install, folder in raw.get("folders", {}).items()
        },
    }


def _inspect(data: Path, library) -> dict:
    state_path = library.root / "state.json"
    raw = json.loads(state_path.read_bytes()) if state_path.is_file() else {}
    return {
        **_stable_state(raw),
        "payloads": _hashes(library.root / "mods"),
        "kept": _hashes(library.root / "originals"),
        "previous": sorted(
            str(path.relative_to(library.root / "mods")) for path in (library.root / "mods").glob("*/previous/*")
        ),
        "pending": _journal_receipts(library.root),
        "configuration": _profiles_view(library.root / "profiles.json"),
        "preferences": json.loads((data / "webui.json").read_bytes()) if (data / "webui.json").is_file() else None,
        "state": str(state_path),
        "data": str(data),
        "library": str(library.root),
    }


def _seed(root: Path, package: str, release: str, interrupt: str | None) -> dict:
    fixture, env, patch = _setup(root)
    data = root / "data"
    data.mkdir(exist_ok=True)
    _settings(env, data)
    _confirm_fixture_build(env.library, env.game_dir)
    state = _module(package, "webui.state").UiState(data / "webui.json")
    state.set("library_dir", str(env.root))
    state.set_consent(True)
    state.visit("test")
    observed = ["C", "U", "S", "B"]
    if interrupt and interrupt.startswith("I:"):
        _fault_journals(package, interrupt)
    if interrupt is None or interrupt in ("I:copying", "I:replacing"):
        _seed_update(fixture, env, patch, observed)
    source = fixture["make_mod"](env.original, root / "input", b"literal variant:payload")
    second = fixture["game_bundle"](env.game_dir, "test_spr", part="002", payload=b"official second")
    second_mod = fixture["make_mod"](second, root / "input", b"mod part two")
    (mod,) = env.library.add([source, second_mod])
    env.library.rename(mod.id, "fixture variant:alias")
    env.library.set_favorite("test", True)
    _confirm_fixture_build(env.library, env.game_dir)
    if interrupt and interrupt.startswith("T:"):
        _fault_journals(package, interrupt)
    env.library.apply("test", f"mod:{mod.id}")
    if getattr(env.library, "profiles", None) is not None:
        observed.append("P")
        env.library.toggle(False, True)
        env.library.toggle(True, False)
    _seed_optional(package, state, env, release, observed)
    if not interrupt:
        _seed_camera(package, env, fixture, patch, observed)
        _seed_collection(package, env, fixture, patch, observed)
        _seed_package(package, root, env, mod, observed)
    if interrupt and interrupt.startswith("D:"):
        _fault_journals(package, interrupt)
        if interrupt == "D:photoBackedUp":
            photographs = _module(package, "library.photographs").Photographs(env.root)
            env.library.delete_photograph("test", photographs.selection("test"), confirm=True)
        else:
            parameters = {"confirm": True} if "confirm" in inspect.signature(env.library.remove).parameters else {}
            env.library.remove(mod.id, **parameters)
    if interrupt:
        raise RuntimeError(f"The tagged writer did not reach {interrupt}")
    patch.undo()
    result = _inspect(data, env.library)
    return {**result, "game": str(env.game_dir), "writers": observed}


def _older(root: Path, package: str, operation: str) -> dict:
    work = root / "work"
    data = root / "data"
    # Read existing data only in a scratch fixture. Settings and ModLibrary receive their original explicit paths.
    from pytest import MonkeyPatch

    fixture = runpy.run_path(str(Path.cwd() / "tests" / "test_library.py"))
    library_cls = fixture["ModLibrary"]
    settings = fixture["Settings"](data / "config.toml")
    variant = fixture["FakeVariant"]("test", "test_spr", "test_home_gl")
    other = fixture["FakeVariant"]("other", "other_spr", family="other")
    catalog = fixture["FakeCatalog"](variant, other)
    with MonkeyPatch.context() as patch:
        patch.setattr(fixture["safety"], "check_game_not_running", lambda *_: None)
        patch.setattr(fixture["Bundle"], "check_need_crc", lambda *_args, **_kw: False)
        helpers = Path.cwd() / "tests" / "conftest.py"
        if "header_only_bundles" in inspect.signature(fixture["setup_library"].__wrapped__).parameters:
            runpy.run_path(str(helpers))["header_only_bundles"].__wrapped__(patch)
        try:
            library = library_cls(work / "library", catalog, object(), settings)
            _confirm_fixture_build(library, work / "game")
            if operation == "recover":
                if pending := getattr(library, "_recover_pending", None):
                    pending()
            if operation == "downgrade":
                library.restore("test")
                library.set_favorite("test", False)
                if callable(prune := getattr(library, "prune", None)):
                    prune()
                _module(package, "webui.state").UiState(data / "webui.json").visit("other")
        except (fixture["ServiceError"], OSError) as error:
            if operation != "recover":
                raise
            path = work / "library"
            return {
                "recovery": "blocked",
                "code": getattr(error, "code", type(error).__name__),
                "pending": _journal_receipts(path),
            }
        result = _inspect(data, library)
        if operation == "recover":
            result["recovery"] = "deferred" if result["pending"] else "settled"
        return result


def main() -> None:
    """Run one fixture operation in the tag's installed dependency environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("seed", "inspect", "downgrade", "recover"))
    parser.add_argument("release")
    parser.add_argument("case", type=Path)
    parser.add_argument("--interrupt")
    args = parser.parse_args()
    root = args.case.resolve()
    if not root.is_dir() or root == Path.cwd().resolve() or Path.cwd().resolve().is_relative_to(root):
        parser.error("Use an existing scratch case separate from the extracted release")
    os.environ["BAMT_DATA_DIR"] = str(root / "data")
    os.environ["BAMT_GAME_DIRS_GLOBAL"] = ""
    os.environ["BAMT_GAME_DIRS_JAPAN"] = ""
    _confine(root)
    package = "bmpt" if (Path.cwd() / "src" / "bmpt").is_dir() else "ba_modding_toolkit"
    if args.operation == "seed":
        result = _seed(root, package, args.release, args.interrupt)
    else:
        sys.path.insert(0, str(Path.cwd() / "tests"))
        result = _older(root, package, args.operation)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
