"""Open and seed generation-2 libraries with whichever release's code the running interpreter imports.

Both drivers use it: ``legacy_driver.py`` with a pinned release's code, ``crash_driver.py`` with the current code.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import runpy
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

CATALOG_GAME = "catalog-game"


def fake_alt(fixture: dict, key: str, portrait: str, memorial_lobby: str | None = None, student: str = "test"):
    """Build the catalog fixture's alt under the name the release's tests use."""
    if "FakeAlt" in fixture:
        return fixture["FakeAlt"](key, portrait, memorial_lobby, student=student)
    return fixture["FakeVariant"](key, portrait, memorial_lobby, family=student)


def open_data(data: Path, action: str = "retry"):
    """Run the release's data upgrade on a data folder the way its start-up does.

    Returns:
        The coordinator, still open, and its state. The caller closes the coordinator.
    """
    from bmpt.host.data_upgrade import DataUpgrade

    upgrade = DataUpgrade(data)
    state = upgrade.run()
    if action in ("keep", "import") and state["status"] == "changed":
        state = upgrade.run(action)
    if state["reason"] == "olderVersion":
        state = upgrade.run()
    return upgrade, state


@contextlib.contextmanager
def epoch_library(upgrade, tests: Path) -> Iterator:
    """Open the release's library at the active epoch with the gates its web UI passes."""
    from pytest import MonkeyPatch

    from bmpt.host.config import Settings
    from bmpt.library import ModLibrary, safety

    paths = upgrade.paths
    sys.path.insert(0, str(tests))
    fixture = runpy.run_path(str(tests / "test_library.py"))
    helpers = runpy.run_path(str(tests / "conftest.py"))
    catalog = fixture["FakeCatalog"](
        fake_alt(fixture, "test", "test_spr", "test_home_gl"), fake_alt(fixture, "other", "other_spr", student="other")
    )
    gates = {"game_write_gate": upgrade.game_write}
    if "catalog_write_gate" in inspect.signature(ModLibrary).parameters:
        gates["catalog_write_gate"] = upgrade.catalog_write
    with MonkeyPatch.context() as patch:
        helpers["header_only_bundles"].__wrapped__(patch)
        patch.setattr(safety, "check_game_not_running", lambda *_args, **_kwargs: None)
        if hasattr(safety, "check_steam_idle"):
            patch.setattr(safety, "check_steam_idle", lambda *_args, **_kwargs: None)
        yield ModLibrary(paths.library_epoch, catalog, object(), Settings(paths.config), **gates)


def pending(epoch: Path) -> list[str]:
    """List the journals left in a library epoch, relative to it."""
    return sorted(str(path.relative_to(epoch)) for path in epoch.glob("*/*/journal.json"))


def catalog_summary(epoch: Path) -> dict:
    """Return each install's catalog mode and live kind from the epoch's catalog store, empty without one."""
    path = epoch / "catalogs.json"
    if not path.is_file():
        return {}
    installs = json.loads(path.read_bytes()).get("installs", {})
    return {key: {"mode": record.get("mode"), "live": record.get("live")} for key, record in installs.items()}


def seed_catalog(library, case: Path, tests: Path, stop: Callable[[Path], None] | None = None) -> dict:
    """Put a synthetic install into catalog mode, and with ``stop`` interrupt its first swap to the Mods catalog.

    The install's official catalog is kept, which fences the library to the catalog-capable reader, and a Mods
    catalog is generated for one placed bundle. ``stop`` receives the swap journal once its swapping phase is durable
    and must not return.
    """
    from bmpt.library.catalog_switch import MODS_DIR, catalog_paths
    from bmpt.library.service import catalog as catalog_service
    from bmpt.unity import ccdb

    sys.path.insert(0, str(tests))
    from catalog_fixture import synthetic_catalog

    game = case / "work" / CATALOG_GAME
    resource = game / "BlueArchive_Data" / "StreamingAssets" / "PUB" / "Resource"
    bundles = resource / "GameData" / "Windows"
    live = resource / "Catalog" / "Windows" / "catalog_Windows.bytes"
    bundles.mkdir(parents=True)
    live.parent.mkdir(parents=True)
    live.write_bytes(ccdb.write(synthetic_catalog()))
    live.with_suffix(".hash").write_bytes(b"upgrade-matrix catalog stamp")
    pin = library._pin(game, "global")
    if pin is None or catalog_paths(pin).live != live:
        raise RuntimeError("The synthetic install does not resolve to its catalog")
    placed = b"upgrade-matrix mod bundle"
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
    library._keep_official_catalog(pin)
    library._generate_catalog(pin, redirects)
    library._save_catalog_record(pin, {**library._catalog_record(pin), "mode": "catalog", "reason": None})
    if stop is not None:
        write = catalog_service.atomic_json

        def swapping(target: Path, data: dict) -> None:
            write(target, data)
            if target.name == "journal.json" and data.get("phase") == "swapping":
                stop(target)

        catalog_service.atomic_json = swapping
        try:
            library._swap_catalog(pin, "mods")
        finally:
            catalog_service.atomic_json = write
        raise RuntimeError("The catalog swap did not reach its swapping phase")
    return {"catalog": catalog_summary(library.root), "pending": pending(library.root)}
